"""Pattern B.3 wire-in tests — `_edit_page` integrates the surgical merger.

The reviewer's load-bearing concern (2026-06-13): the entire wedge
defense depends on `_edit_page` reading the REAL last-revision edit
summary from the live page via mwclient. Reading a stale/cached/wrong
revision's comment silently reopens the Auto-import: vs Auto-merge:
wedge — the next bot run takes the wrong path and loses reviewer
content.

These tests pin:
  1. `_fetch_last_revision_summary` calls `page.revisions(limit=1)` —
     i.e. it asks the API for the top revision's comment, not for the
     page's cached text() / state.
  2. `_edit_page` end-to-end with a page whose last-revision summary
     is `Auto-merge:` → surgical-merge path taken, save uses
     `Auto-merge:` prefix, outside-marker content preserved.
  3. `_edit_page` with `Auto-import:` last-revision → clean-rewrite
     path → save uses `Auto-import:` prefix.
  4. `_edit_page` with reviewer last-revision → surgical-merge path.
  5. `_edit_page` with no-change merger result → no save call at all
     (the operator's cost-saving optimisation must survive the
     refactor).
"""

from unittest.mock import MagicMock

import mwclient

from wiki_import.import_players import (
    _edit_page,
    _fetch_last_revision_summary,
    _save_with_merger,
)
from wiki_import.wikitext_merger import (
    BOT_EDIT_SUMMARY_PREFIX,
    BOT_MERGE_SUMMARY_PREFIX,
    make_end_marker,
    make_start_marker,
)


def _wrap(section_id: str, body: str) -> str:
    return f"{make_start_marker(section_id)}\n{body}\n{make_end_marker(section_id)}"


def _make_page(
    *,
    exists: bool,
    name: str = "Draft:Test Player",
    text: str = "",
    last_summary: str | None = None,
    redirects_to: str | None = None,
    revisions_raise: Exception | None = None,
) -> MagicMock:
    """Build a mock mwclient Page with a controllable last-revision summary.

    `last_summary`: the comment the API will report for the page's top
    revision. None = no revisions (treat as not bot-authored).
    `revisions_raise`: simulate an APIError on the revisions() call.
    """
    page = MagicMock()
    page.exists = exists
    page.name = name
    page.text = MagicMock(return_value=text)
    page.save = MagicMock()
    # Redirect target (for the resolve_redirect path).
    if redirects_to is not None:
        target = MagicMock()
        target.name = redirects_to
        page.redirects_to = MagicMock(return_value=target)
    else:
        page.redirects_to = MagicMock(return_value=None)
    # Revision lookup — the load-bearing piece tested here.
    if revisions_raise is not None:
        page.revisions = MagicMock(side_effect=revisions_raise)
    elif last_summary is None:
        page.revisions = MagicMock(return_value=iter([]))
    else:
        page.revisions = MagicMock(
            return_value=iter([{"comment": last_summary}])
        )
    return page


def _make_site(pages: dict[str, MagicMock]) -> MagicMock:
    """Build a mock mwclient Site with controllable pages dict.
    Unknown titles return a non-existent page."""
    site = MagicMock(spec=mwclient.Site)

    class _Accessor:
        def __getitem__(self, title):
            if title in pages:
                return pages[title]
            return _make_page(exists=False, name=title)

    site.pages = _Accessor()
    return site


# ---------------------------------------------------------------------------
# _fetch_last_revision_summary — pin the API call shape
# ---------------------------------------------------------------------------


class TestFetchLastRevisionSummary:
    """The load-bearing piece: the entire wedge defense depends on
    reading the REAL last-revision summary from the live API."""

    def test_returns_top_revision_comment(self):
        page = _make_page(exists=True, last_summary="Auto-merge: Wiki7Bot run 2")
        assert _fetch_last_revision_summary(page) == "Auto-merge: Wiki7Bot run 2"

    def test_calls_revisions_with_limit_1(self):
        """Pin the API call shape — the test reviewer asked for. Reading
        more revisions than needed is wasted bandwidth; reading fewer
        than 1 means we'd never get the top revision's comment.
        `prop='comment'` ensures we ACTUALLY get the comment field back
        (mwclient may default to ids only)."""
        page = _make_page(exists=True, last_summary="X")
        _fetch_last_revision_summary(page)
        assert page.revisions.called
        # Verify the call carries limit=1 and prop='comment'.
        _args, kwargs = page.revisions.call_args
        assert kwargs.get("limit") == 1, (
            f"page.revisions must be called with limit=1; got kwargs={kwargs!r}"
        )
        assert "comment" in str(kwargs.get("prop", "")), (
            f"page.revisions must request the comment prop; got kwargs={kwargs!r}"
        )

    def test_returns_none_when_no_revisions(self):
        page = _make_page(exists=True, last_summary=None)
        assert _fetch_last_revision_summary(page) is None

    def test_returns_none_on_api_error(self):
        """Defensive: API errors don't crash the import. The caller
        treats None as 'not bot-authored', which routes to the
        surgical-merge path — the SAFE default (won't accidentally
        clean-rewrite a reviewer-edited page just because the API
        hiccupped)."""
        page = _make_page(
            exists=True,
            revisions_raise=mwclient.errors.APIError("hiccup", "info", "doc"),
        )
        assert _fetch_last_revision_summary(page) is None


# ---------------------------------------------------------------------------
# _save_with_merger — end-to-end with mock pages
# ---------------------------------------------------------------------------


class TestSaveWithMergerWedgeRegression:
    """Reviewer-pass blocker for B.3 (2026-06-13). The test reviewer
    explicitly asked for: stub a page whose last-revision summary is
    `Auto-merge:`, assert _edit_page takes the SURGICAL path, and
    assert outside-marker content is preserved end-to-end."""

    def test_auto_merge_last_revision_triggers_surgical_merge(self):
        """The exact reviewer-asked scenario."""
        REVIEWER_PROSE = "Reviewer-added paragraph that must survive."
        existing_text = (
            _wrap("infobox", "OLD_INFOBOX")
            + f"\n\n{REVIEWER_PROSE}\n\n"
            + _wrap("career", "OLD_CAREER")
        )
        new_content = (
            _wrap("infobox", "NEW_INFOBOX_FROM_TEMPLATE")
            + "\n"
            + _wrap("career", "NEW_CAREER_FROM_TEMPLATE")
        )
        # The wedge case: last revision was a previous surgical-merge
        # save (Auto-merge:). Pre-B.3-fix would have either lost the
        # reviewer paragraph OR not surfaced the issue. The fix: read
        # the real Auto-merge: summary, recognise it's NOT clean-rewrite-
        # eligible, take the surgical path.
        page = _make_page(
            exists=True,
            text=existing_text,
            last_summary="Auto-merge: Wiki7Bot prior section update",
        )

        result = _save_with_merger(
            page, new_content, summary_detail="Niv Eliasi update",
        )

        # 1. Took the surgical path.
        assert result == "merged"
        assert page.save.called, "save MUST be called — content did change"
        # 2. Saved with Auto-merge: prefix (NOT Auto-import:).
        saved_content, save_kwargs = page.save.call_args.args[0], page.save.call_args.kwargs
        saved_summary = save_kwargs.get("summary", "")
        assert saved_summary.startswith(BOT_MERGE_SUMMARY_PREFIX), (
            f"Surgical-merge save MUST use {BOT_MERGE_SUMMARY_PREFIX!r}; "
            f"got summary={saved_summary!r}"
        )
        # 3. Reviewer paragraph preserved in the saved content.
        assert REVIEWER_PROSE in saved_content, (
            "Reviewer paragraph LOST after surgical merge — wedge "
            "defense broken. Saved content:\n" + saved_content[:500]
        )
        # 4. Bot-managed sections updated.
        assert "NEW_INFOBOX_FROM_TEMPLATE" in saved_content
        assert "OLD_INFOBOX" not in saved_content

    def test_auto_import_last_revision_triggers_clean_rewrite(self):
        """The page's last edit was a bot-OWNED clean rewrite — safe to
        replace the whole thing. Save uses Auto-import: again."""
        existing_text = (
            _wrap("infobox", "OLD")
            + "\n"
            + _wrap("career", "OLD_CAREER")
        )
        new_content = (
            _wrap("infobox", "NEW")
            + "\n"
            + _wrap("career", "NEW_CAREER")
        )
        page = _make_page(
            exists=True,
            text=existing_text,
            last_summary="Auto-import: prior bot creation",
        )

        result = _save_with_merger(page, new_content, "player page")

        assert result == "updated"
        saved_summary = page.save.call_args.kwargs.get("summary", "")
        assert saved_summary.startswith(BOT_EDIT_SUMMARY_PREFIX), (
            f"Clean-rewrite save MUST use {BOT_EDIT_SUMMARY_PREFIX!r}; "
            f"got summary={saved_summary!r}"
        )
        # Clean rewrite: saved content equals new_content verbatim.
        assert page.save.call_args.args[0] == new_content

    def test_reviewer_last_revision_triggers_surgical_merge(self):
        """Last edit was a reviewer (no bot prefix). Reviewer content
        outside markers must be preserved."""
        REVIEWER_PROSE = "Reviewer prose"
        existing_text = (
            _wrap("infobox", "OLD_INFOBOX")
            + f"\n\n{REVIEWER_PROSE}\n\n"
        )
        new_content = _wrap("infobox", "NEW_INFOBOX")

        page = _make_page(
            exists=True,
            text=existing_text,
            last_summary="Cleaned up the lead paragraph",
        )
        result = _save_with_merger(page, new_content, "test")
        assert result == "merged"
        saved_summary = page.save.call_args.kwargs.get("summary", "")
        assert saved_summary.startswith(BOT_MERGE_SUMMARY_PREFIX)
        assert REVIEWER_PROSE in page.save.call_args.args[0]

    def test_no_change_path_does_not_save(self):
        """Surgical merge yielded identical content → no save at all.
        This is the operator's cost-saving optimisation (skip wiki
        edits when nothing actually changed) and the refactor must
        preserve it."""
        sections = _wrap("infobox", "BODY") + "\n" + _wrap("career", "CAREER")
        # Reviewer touched something before but no template change since.
        page = _make_page(
            exists=True,
            text=sections,
            last_summary="Reviewer copyedit",
        )
        result = _save_with_merger(page, sections, "test")
        assert result == "skipped"
        assert not page.save.called, (
            "no_change merge result MUST NOT trigger a save"
        )

    def test_new_page_clean_creates_with_auto_import(self):
        """Page doesn't exist → first save uses Auto-import: (the
        first-run baseline that future surgical merges build on)."""
        page = _make_page(exists=False, name="Draft:Brand New Player")
        new_content = _wrap("infobox", "FIRST_RUN")
        result = _save_with_merger(page, new_content, "player page for X")
        assert result == "created"
        saved_summary = page.save.call_args.kwargs.get("summary", "")
        assert saved_summary.startswith(BOT_EDIT_SUMMARY_PREFIX)


# ---------------------------------------------------------------------------
# _edit_page wrapper — resolves redirect before reading content
# ---------------------------------------------------------------------------


class TestEditPageResolvesRedirectFirst:
    """Pattern B carried-forward constraint (a): surgical-merging onto
    a redirect is nonsense. `_edit_page` MUST resolve redirects before
    reading existing content."""

    def test_edit_page_follows_redirect_then_merges_into_target(self):
        # Reviewer renamed Draft:Old → Draft:New, leaving Draft:Old as
        # a redirect. The bot calls _edit_page(site, 'Draft:Old', ...).
        # Must end up writing to Draft:New (the content target), not
        # overwriting the redirect.
        REVIEWER_PROSE = "On Draft:New only"
        target_text = (
            _wrap("infobox", "NEW_TARGET_INFOBOX")
            + f"\n\n{REVIEWER_PROSE}\n\n"
        )
        redirect_page = _make_page(
            exists=True,
            name="Draft:Old",
            text="#REDIRECT [[Draft:New]]",
            redirects_to="Draft:New",
            last_summary="Auto-import: prior",  # irrelevant
        )
        target_page = _make_page(
            exists=True,
            name="Draft:New",
            text=target_text,
            last_summary="Auto-merge: prior",
        )

        site = _make_site({
            "Draft:Old": redirect_page,
            "Draft:New": target_page,
        })

        new_content = _wrap("infobox", "BOT_RUN_3_INFOBOX")
        _edit_page(site, "Draft:Old", new_content, "test")

        # The TARGET page (Draft:New) is what got saved, not the
        # redirect.
        assert target_page.save.called
        assert not redirect_page.save.called
        # And the reviewer prose on Draft:New survived.
        saved_target_content = target_page.save.call_args.args[0]
        assert REVIEWER_PROSE in saved_target_content