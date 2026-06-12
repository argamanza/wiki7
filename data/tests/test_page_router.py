"""Tests for the page-router (auto-MovePage on title drift) — Pattern A.2."""

from pathlib import Path
from unittest.mock import MagicMock

import mwclient
import pytest

from data_pipeline.pipeline_state import PageIndexState
from wiki_import.page_router import format_title, resolve_redirect, resolve_target_title


def _make_site(pages: dict[str, bool], redirects: dict[str, str] | None = None) -> MagicMock:
    """Build a mock mwclient.Site.

    `pages` is `{title: exists}`. `redirects` is an optional `{from_title:
    to_title}` map: when present, `pages[from_title]` reports a
    `redirects_to()` that returns a stand-in Page for `to_title`.

    Reviewer-pass orange #7 (2026-06-13): redirects are now first-class
    in the mock so the router's `resolve_redirect` calls in production-
    shape paths have something to walk.
    """
    redirects = redirects or {}
    site = MagicMock(spec=mwclient.Site)

    class _PagesAccessor:
        def __getitem__(self, title):
            page = MagicMock()
            page.exists = pages.get(title, False)
            page.move = MagicMock()
            page.name = title
            page._title = title
            # If this title is a redirect source, redirects_to() returns
            # a Page-shaped mock whose `.name` is the target.
            if title in redirects:
                target_mock = MagicMock()
                target_mock.name = redirects[title]
                page.redirects_to = MagicMock(return_value=target_mock)
            else:
                page.redirects_to = MagicMock(return_value=None)
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
    """All tests pass `want_namespace=3000` — that's the shape the production
    pipeline actually uses (`import_players.py`). Tests that called with
    `want_namespace=0` were removed/rewritten in the 2026-06-12 §6 ① fix
    because they didn't exercise the bug they purported to cover:
    production never asks for ns=0, so the latent
    "stored_ns=0 + want_ns=3000 → move mainspace→Draft" landmine was
    invisible to that shape of test. See `TestPromoteSyncProdShape` below
    for the new tests that catch it."""

    def test_first_time_player_no_existing_page(self, tmp_path: Path):
        state = PageIndexState(tmp_path / "state.yaml")
        site = _make_site({})
        title, action, ns = resolve_target_title(site, state, "912586", "ניב אליאסי", 3000)
        assert (title, action, ns) == ("Draft:ניב אליאסי", "create", 3000)

    def test_first_time_player_target_already_exists_is_update(self, tmp_path: Path):
        """If state has no record but the wiki already has the page (e.g.
        a previous run crashed before saving state), treat as update."""
        state = PageIndexState(tmp_path / "state.yaml")
        site = _make_site({"Draft:ניב אליאסי": True})
        title, action, ns = resolve_target_title(site, state, "912586", "ניב אליאסי", 3000)
        assert (title, action, ns) == ("Draft:ניב אליאסי", "update", 3000)

    def test_no_drift_returns_update(self, tmp_path: Path):
        state = PageIndexState(tmp_path / "state.yaml")
        state.upsert("912586", "ניב אליאסי", 3000)
        site = _make_site({"Draft:ניב אליאסי": True})
        title, action, ns = resolve_target_title(site, state, "912586", "ניב אליאסי", 3000)
        assert (title, action, ns) == ("Draft:ניב אליאסי", "update", 3000)

    def test_drift_in_title_triggers_movepage(self, tmp_path: Path):
        """The Hélder Lopes / Ben Gordin case: pipeline previously emitted
        a stale title, mapping was overridden, now emits a new title."""
        state = PageIndexState(tmp_path / "state.yaml")
        state.upsert("171068", "הלדר לפופסיק", 3000)  # Old (stale Wikidata) title
        # Wiki currently has the old title; new target is free
        site = _make_site({"Draft:הלדר לפופסיק": True, "Draft:הלדר לופש": False})

        title, action, ns = resolve_target_title(
            site, state, "171068", "הלדר לופש", 3000,
        )
        assert (title, action, ns) == ("Draft:הלדר לופש", "moved", 3000)
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

    def test_stranded_when_stored_page_no_longer_exists(self, tmp_path: Path):
        """Reviewer deleted the page entirely. State file is stale; treat
        as fresh-create at the new title rather than crashing."""
        state = PageIndexState(tmp_path / "state.yaml")
        state.upsert("999", "Old Player", 3000)
        site = _make_site({"Draft:Old Player": False, "Draft:New Title": False})
        title, action, ns = resolve_target_title(
            site, state, "999", "New Title", 3000,
        )
        assert (title, action, ns) == ("Draft:New Title", "stranded", 3000)

    def test_both_exist_skips_move_but_continues(self, tmp_path: Path):
        """Defensive: somehow both old and new title exist. Log + treat as
        update on the target; orphan stays for reviewer to clean up. Don't
        crash the pipeline mid-batch."""
        state = PageIndexState(tmp_path / "state.yaml")
        state.upsert("1", "OldName", 3000)
        site = _make_site({"Draft:OldName": True, "Draft:NewName": True})
        title, action, ns = resolve_target_title(
            site, state, "1", "NewName", 3000,
        )
        assert (title, action, ns) == ("Draft:NewName", "update", 3000)

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

        title, action, ns = resolve_target_title(
            site, state, "1", "NewName", 3000,
        )
        assert (title, action, ns) == ("Draft:NewName", "stranded", 3000)


class TestPromoteSyncProdShape:
    """The promote/sync flow tested with PRODUCTION call shape
    (`want_namespace=3000`). These are the tests the 2026-06-12 full-project
    review §6 ① called for — the original `test_drift_in_namespace_handles_promotion`
    passed `want_namespace=0`, a shape production NEVER uses, so it never
    exercised the real promote path.

    Production flow (the path that exists in the live bot):
      1. Reviewer manually MovePages `Draft:X → X` (promotion).
      2. Next bot run calls `resolve_target_title(..., want_namespace=3000)`.
      3. The router must (a) detect mainspace page exists, (b) treat it
         as authoritative, (c) sync state to ns=0, (d) NEVER move it back
         to Draft.

    The catastrophic latent path is `stored_ns=0 + want_ns=3000 → move
    mainspace page into Draft`. That makes the public page invisible
    overnight. These tests prevent it from ever shipping again."""

    def test_mainspace_probe_catches_reviewer_promote_without_state(self, tmp_path: Path):
        """No state record yet (e.g. brand new TM ID), and the reviewer
        already created the mainspace page manually OR promoted a stale
        Draft we never recorded. Bot calls with want_namespace=3000; the
        mainspace-first probe must catch the mainspace page and treat it
        as authoritative — return (mainspace_title, update, 0)."""
        state = PageIndexState(tmp_path / "state.yaml")
        site = _make_site({"ניב אליאסי": True})  # mainspace page exists
        title, action, ns = resolve_target_title(
            site, state, "912586", "ניב אליאסי", 3000,  # prod shape
        )
        assert (title, action, ns) == ("ניב אליאסי", "update", 0)

    def test_mainspace_probe_catches_post_save_promote(self, tmp_path: Path):
        """State says the page is at Draft:X (from the last bot save), but
        the reviewer has since promoted it to mainspace X. Bot calls with
        want_namespace=3000 expecting Draft. The mainspace-first probe must
        catch the mainspace page, treat it as authoritative, and return
        (mainspace_title, update, 0) — and NOT try to MovePage the live
        public page back into Draft."""
        state = PageIndexState(tmp_path / "state.yaml")
        state.upsert("912586", "ניב אליאסי", 3000)  # state still says Draft
        # Reviewer has since promoted; both pages exist briefly (mainspace
        # is the canonical, Draft may still exist as a redirect or orphan).
        site = _make_site({"ניב אליאסי": True, "Draft:ניב אליאסי": True})
        title, action, ns = resolve_target_title(
            site, state, "912586", "ניב אליאסי", 3000,
        )
        assert (title, action, ns) == ("ניב אליאסי", "update", 0)
        # Critical: NO move was attempted on the mainspace page.
        assert site._moves_done == [] or all(
            not p.move.called for _, p in site._moves_done
        )

    def test_stored_ns0_never_demotes_to_draft(self, tmp_path: Path):
        """The catastrophic landmine from §6 ①. State already has ns=0
        (we previously synced a reviewer-promote into state). Pipeline
        calls with want_namespace=3000 — the prod shape. Old code:
        comparison stored_ns=0 vs want_ns=3000 fires the "drift" branch,
        tries to MovePage the mainspace page into Draft. New code:
        mainspace-first probe catches it, returns mainspace + update + 0
        with ZERO move attempts."""
        state = PageIndexState(tmp_path / "state.yaml")
        state.upsert("912586", "ניב אליאסי", 0)
        site = _make_site({"ניב אליאסי": True})  # only mainspace exists
        title, action, ns = resolve_target_title(
            site, state, "912586", "ניב אליאסי", 3000,  # prod shape
        )
        assert (title, action, ns) == ("ניב אליאסי", "update", 0)
        # The catastrophic-case assertion: NO MovePage call was made at all.
        for _, page in site._moves_done:
            assert not page.move.called, (
                "MovePage was called — would have moved the public mainspace "
                "page back into Draft (catastrophic). Mainspace-first probe "
                "must short-circuit before any move."
            )

    def test_stored_ns0_title_drift_stays_in_mainspace(self, tmp_path: Path):
        """A page is in mainspace and the pipeline now emits a slightly
        different Hebrew title (reviewer-corrected mapping override).
        Even with want_namespace=3000, the router must MovePage WITHIN
        mainspace, not cross into Draft. The state file's stored_ns=0 is
        authoritative for namespace; want_namespace just communicates the
        bot's default."""
        state = PageIndexState(tmp_path / "state.yaml")
        state.upsert("171068", "הלדר לפופסיק", 0)  # stored in mainspace
        # Wiki currently has the old mainspace title (reviewer-promoted);
        # NEITHER the new mainspace title nor any Draft variant exists.
        site = _make_site({
            "הלדר לפופסיק": True,
            "הלדר לופש": False,
            "Draft:הלדר לופש": False,
            "Draft:הלדר לפופסיק": False,
        })
        title, action, ns = resolve_target_title(
            site, state, "171068", "הלדר לופש", 3000,  # prod shape
        )
        # Must MovePage WITHIN mainspace, never to Draft.
        assert (title, action, ns) == ("הלדר לופש", "moved", 0)
        # Verify the move target was the MAINSPACE title, not a Draft title
        moved_calls = [
            (t, p) for t, p in site._moves_done if t == "הלדר לפופסיק"
        ]
        assert moved_calls, "Expected a move on the stored mainspace page"
        moved_page = moved_calls[0][1]
        args, kwargs = moved_page.move.call_args
        assert args[0] == "הלדר לופש", (
            f"MovePage target was {args[0]!r}; must be the mainspace title, "
            "NOT a Draft: variant"
        )
        assert kwargs.get("no_redirect") is True


class TestResolveRedirectHelper:
    """Reviewer-pass orange #7 (2026-06-13): the resolve_redirect helper
    is shared between the router NOW and Pattern B's surgical merger
    LATER. Pin its contract."""

    def test_nonexistent_page_returns_input_unchanged(self):
        site = _make_site({})
        assert resolve_redirect(site, "Draft:X") == ("Draft:X", False)

    def test_non_redirect_existing_page_returns_input(self):
        site = _make_site({"Draft:X": True})
        assert resolve_redirect(site, "Draft:X") == ("Draft:X", False)

    def test_redirect_returns_target_with_was_redirect_True(self):
        site = _make_site({"X": True, "Y": True}, redirects={"X": "Y"})
        assert resolve_redirect(site, "X") == ("Y", True)


class TestRedirectAwareProbe:
    """Reviewer-pass orange #7 (2026-06-13): a reviewer mainspace rename
    X → Y leaves redirect-X. Pre-fix the router's bare `.exists` check
    treated the redirect as authoritative content and the bot overwrote
    it, producing duplicate public pages. These tests pin that the router
    resolves through redirects to the actual content target.

    This is ALSO a Pattern B prerequisite — surgical-merging onto a
    redirect is nonsense. The shared `resolve_redirect` helper means
    Pattern B inherits the behavior automatically."""

    def test_mainspace_probe_follows_redirect(self, tmp_path: Path):
        """No state record; mainspace X is a redirect to mainspace Y.
        The router must return Y (the content page), not X (the redirect).
        Otherwise the bot would overwrite the reviewer's redirect."""
        state = PageIndexState(tmp_path / "state.yaml")
        # X exists as a redirect to Y; Y exists as content.
        site = _make_site(
            {"ניב אליאסי": True, "ניב אליאסי הצעיר": True},
            redirects={"ניב אליאסי": "ניב אליאסי הצעיר"},
        )
        title, action, ns = resolve_target_title(
            site, state, "912586", "ניב אליאסי", 3000,  # prod shape
        )
        # Must point at the redirect target Y, not the redirect source X.
        assert (title, action, ns) == ("ניב אליאסי הצעיר", "update", 0)

    def test_stored_title_follows_intra_namespace_redirect(self, tmp_path: Path):
        """Reviewer renamed Draft:Old → Draft:New, leaving Draft:Old as
        a redirect. State still says Draft:Old. The router must resolve
        through the redirect and update Draft:New, not blast the redirect."""
        state = PageIndexState(tmp_path / "state.yaml")
        state.upsert("171068", "OldTitle", 3000)
        site = _make_site(
            {"Draft:OldTitle": True, "Draft:NewTitle": True},
            redirects={"Draft:OldTitle": "Draft:NewTitle"},
        )
        title, action, ns = resolve_target_title(
            site, state, "171068", "OldTitle", 3000,
        )
        assert (title, action, ns) == ("Draft:NewTitle", "update", 3000)

    def test_no_redirect_preserves_old_behavior(self, tmp_path: Path):
        """Regression guard: when there's no redirect, the router must
        still produce the same result as before."""
        state = PageIndexState(tmp_path / "state.yaml")
        state.upsert("999", "Stable Title", 3000)
        site = _make_site({"Draft:Stable Title": True})  # no redirects
        title, action, ns = resolve_target_title(
            site, state, "999", "Stable Title", 3000,
        )
        assert (title, action, ns) == ("Draft:Stable Title", "update", 3000)
