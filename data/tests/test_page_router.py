"""Tests for the page-router (auto-MovePage on title drift) — Pattern A.2."""

from pathlib import Path
from unittest.mock import MagicMock

import mwclient
import pytest

from data_pipeline.pipeline_state import PageIndexState
from wiki_import.page_router import format_title, resolve_target_title


def _make_site(pages: dict[str, bool]) -> MagicMock:
    """Build a mock mwclient.Site where `pages` is {title: exists}."""
    site = MagicMock(spec=mwclient.Site)

    class _PagesAccessor:
        def __getitem__(self, title):
            page = MagicMock()
            page.exists = pages.get(title, False)
            page.move = MagicMock()
            page._title = title
            # Track moves on the parent so tests can assert
            site._moves_done.append((title, page))
            return page

    site.pages = _PagesAccessor()
    site._moves_done = []
    return site


class TestFormatTitle:
    def test_mainspace(self):
        assert format_title("ניב אליאסי", 0) == "ניב אליאסי"

    def test_draft_namespace(self):
        assert format_title("ניב אליאסי", 3000) == "Draft:ניב אליאסי"

    def test_template_namespace(self):
        assert format_title("Player infobox", 10) == "Template:Player infobox"

    def test_unknown_namespace_raises(self):
        with pytest.raises(ValueError):
            format_title("X", 9999)


class TestResolveTargetTitle:
    def test_first_time_player_no_existing_page(self, tmp_path: Path):
        state = PageIndexState(tmp_path / "state.yaml")
        site = _make_site({})
        title, action = resolve_target_title(site, state, "912586", "ניב אליאסי", 3000)
        assert title == "Draft:ניב אליאסי"
        assert action == "create"

    def test_first_time_player_target_already_exists_is_update(self, tmp_path: Path):
        """If state has no record but the wiki already has the page (e.g.
        a previous run crashed before saving state), treat as update."""
        state = PageIndexState(tmp_path / "state.yaml")
        site = _make_site({"Draft:ניב אליאסי": True})
        title, action = resolve_target_title(site, state, "912586", "ניב אליאסי", 3000)
        assert title == "Draft:ניב אליאסי"
        assert action == "update"

    def test_no_drift_returns_update(self, tmp_path: Path):
        state = PageIndexState(tmp_path / "state.yaml")
        state.upsert("912586", "ניב אליאסי", 3000)
        site = _make_site({"Draft:ניב אליאסי": True})
        title, action = resolve_target_title(site, state, "912586", "ניב אליאסי", 3000)
        assert title == "Draft:ניב אליאסי"
        assert action == "update"

    def test_drift_in_title_triggers_movepage(self, tmp_path: Path):
        """The Hélder Lopes / Ben Gordin case: pipeline previously emitted
        a stale title, mapping was overridden, now emits a new title."""
        state = PageIndexState(tmp_path / "state.yaml")
        state.upsert("171068", "הלדר לפופסיק", 3000)  # Old (stale Wikidata) title
        # Wiki currently has the old title; new target is free
        site = _make_site({"Draft:הלדר לפופסיק": True, "Draft:הלדר לופש": False})

        title, action = resolve_target_title(
            site, state, "171068", "הלדר לופש", 3000,
        )
        assert title == "Draft:הלדר לופש"
        assert action == "moved"
        # Verify move was actually called on the old page
        moved_calls = [
            (t, p) for t, p in site._moves_done if t == "Draft:הלדר לפופסיק"
        ]
        assert moved_calls
        moved_page = moved_calls[0][1]
        moved_page.move.assert_called_once()
        args, kwargs = moved_page.move.call_args
        assert args[0] == "Draft:הלדר לופש"
        assert kwargs.get("no_redirect") is True

    def test_drift_in_namespace_handles_promotion(self, tmp_path: Path):
        """Reviewer MovePaged Draft:X → mainspace X. Next bot run sees the
        state file says it's now in mainspace, writes there directly."""
        state = PageIndexState(tmp_path / "state.yaml")
        state.upsert("912586", "ניב אליאסי", 0)  # Already promoted

        site = _make_site({"ניב אליאסי": True})
        title, action = resolve_target_title(
            site, state, "912586", "ניב אליאסי", 0,
        )
        # Want NS=0; state has NS=0; no drift
        assert title == "ניב אליאסי"
        assert action == "update"

    def test_stranded_when_stored_page_no_longer_exists(self, tmp_path: Path):
        """Reviewer deleted the page entirely. State file is stale; treat
        as fresh-create at the new title rather than crashing."""
        state = PageIndexState(tmp_path / "state.yaml")
        state.upsert("999", "Old Player", 3000)
        site = _make_site({"Draft:Old Player": False, "Draft:New Title": False})
        title, action = resolve_target_title(
            site, state, "999", "New Title", 3000,
        )
        assert title == "Draft:New Title"
        assert action == "stranded"

    def test_both_exist_skips_move_but_continues(self, tmp_path: Path):
        """Defensive: somehow both old and new title exist. Log + treat as
        update on the target; orphan stays for reviewer to clean up. Don't
        crash the pipeline mid-batch."""
        state = PageIndexState(tmp_path / "state.yaml")
        state.upsert("1", "OldName", 3000)
        site = _make_site({"Draft:OldName": True, "Draft:NewName": True})
        title, action = resolve_target_title(
            site, state, "1", "NewName", 3000,
        )
        assert title == "Draft:NewName"
        assert action == "update"

    def test_movepage_api_failure_falls_back_to_stranded(self, tmp_path: Path):
        """If MediaWiki rejects the move (rate limit, target locked, etc),
        log + fall back to writing the new title without the move. The
        old page becomes an orphan to clean up later, but the pipeline
        keeps going."""
        state = PageIndexState(tmp_path / "state.yaml")
        state.upsert("1", "OldName", 3000)
        # Custom site where the OLD page's .move() raises APIError
        site = MagicMock(spec=mwclient.Site)
        existence = {"Draft:OldName": True, "Draft:NewName": False}

        class _FailingAccessor:
            def __getitem__(self, title):
                page = MagicMock()
                page.exists = existence.get(title, False)
                if title == "Draft:OldName":
                    page.move = MagicMock(side_effect=mwclient.errors.APIError(
                        "ratelimited", "info", "doc",
                    ))
                else:
                    page.move = MagicMock()
                return page

        site.pages = _FailingAccessor()

        title, action = resolve_target_title(
            site, state, "1", "NewName", 3000,
        )
        assert title == "Draft:NewName"
        assert action == "stranded"
