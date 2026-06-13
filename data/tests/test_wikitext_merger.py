"""Pattern B.1 tests — surgical wikitext merger.

These pin the merge invariants that Pattern B exists to deliver:

  1. Bot-managed sections inside markers are REPLACED with the new
     bot-rendered content on every run.
  2. Reviewer content OUTSIDE markers is PRESERVED across re-imports.
  3. When the page's last edit was ours (Auto-import: prefix), the
     whole page can be safely clean-rewritten.
  4. When the page's last edit was a reviewer, we do a surgical merge.
  5. Bot template no longer renders a section the existing page has →
     leave the section alone (reviewer may have hand-added it).
  6. New section in template that the existing page lacks → append.
  7. Empty / nonexistent existing page → clean rewrite of new_text.
  8. Marker-detection is robust to whitespace + case variation.
"""

from wiki_import.wikitext_merger import (
    BOT_EDIT_SUMMARY_PREFIX,
    MARKER_END,
    MARKER_START,
    extract_managed_sections,
    is_bot_authored,
    merge,
    surgical_merge,
)


def _wrap(section_id: str, body: str) -> str:
    """Helper — wrap `body` in start/end markers for `section_id`."""
    return f"{MARKER_START.format(id=section_id)}\n{body}\n{MARKER_END.format(id=section_id)}"


# ---------------------------------------------------------------------------
# is_bot_authored — the "was this ours" gate
# ---------------------------------------------------------------------------


class TestIsBotAuthored:
    def test_auto_import_prefix(self):
        assert is_bot_authored("Auto-import: Created player page for X") is True

    def test_auto_import_with_just_colon(self):
        assert is_bot_authored("Auto-import:") is True

    def test_leading_whitespace_tolerated(self):
        """A reviewer's manual edit summary that happened to copy-paste
        an `Auto-import:` line shouldn't be classified as ours just
        because of leading whitespace — but the convention is that the
        prefix is always at the START. Tolerate a leading newline/space
        which can sneak in from manual entry."""
        assert is_bot_authored("  Auto-import: X") is True

    def test_non_matching_prefix(self):
        assert is_bot_authored("Manual edit: fix typo") is False

    def test_reviewer_summary(self):
        assert is_bot_authored("Cleaned up the lead") is False

    def test_empty_or_none(self):
        assert is_bot_authored(None) is False
        assert is_bot_authored("") is False
        assert is_bot_authored("   ") is False

    def test_auto_import_substring_not_at_start_is_NOT_ours(self):
        """A reviewer's "Note: Auto-import: changed by ..." should NOT
        match — the prefix must be at the START."""
        assert is_bot_authored("Note: Auto-import: changed something") is False


# ---------------------------------------------------------------------------
# extract_managed_sections — section discovery
# ---------------------------------------------------------------------------


class TestExtractManagedSections:
    def test_finds_single_section(self):
        text = _wrap("infobox", "{{Player infobox|name=X}}")
        sections = extract_managed_sections(text)
        assert "infobox" in sections
        assert "{{Player infobox|name=X}}" in sections["infobox"]

    def test_finds_multiple_sections_preserves_ids(self):
        text = (
            _wrap("infobox", "INFOBOX_BODY")
            + "\n\n== Manual section ==\n\n"
            + _wrap("career", "CAREER_BODY")
            + "\n\n"
            + _wrap("stats", "STATS_BODY")
        )
        sections = extract_managed_sections(text)
        assert set(sections.keys()) == {"infobox", "career", "stats"}

    def test_handles_no_sections(self):
        assert extract_managed_sections("Just plain wikitext, no markers") == {}

    def test_section_text_includes_markers(self):
        """The returned span INCLUDES both start and end markers so the
        caller can splice the whole thing in one shot."""
        text = _wrap("x", "body")
        section = extract_managed_sections(text)["x"]
        assert "wiki7-bot-managed-section start: x" in section
        assert "wiki7-bot-managed-section end: x" in section
        assert "body" in section

    def test_whitespace_variation_in_marker(self):
        """The marker regex tolerates extra whitespace around colon and
        inside the comment delimiters. A reviewer who reformats the
        wikitext doesn't break the merger."""
        text = "<!--   wiki7-bot-managed-section   start:   foo   -->BODY<!--wiki7-bot-managed-section end:foo-->"
        sections = extract_managed_sections(text)
        assert "foo" in sections

    def test_case_insensitive_marker(self):
        text = "<!-- WIKI7-BOT-MANAGED-SECTION start: bar -->BODY<!-- wiki7-bot-managed-section END: bar -->"
        sections = extract_managed_sections(text)
        assert "bar" in sections

    def test_unmatched_start_marker_skipped(self):
        """Defensive: a start marker without an end is logged + skipped,
        not crashed. The page might be mid-edit; better to do nothing
        than corrupt it."""
        text = "<!-- wiki7-bot-managed-section start: orphan -->no end here"
        assert "orphan" not in extract_managed_sections(text)

    def test_unmatched_end_marker_skipped(self):
        text = "stuff<!-- wiki7-bot-managed-section end: orphan -->"
        assert "orphan" not in extract_managed_sections(text)


# ---------------------------------------------------------------------------
# surgical_merge — the load-bearing invariant tests
# ---------------------------------------------------------------------------


class TestSurgicalMergePreservesReviewerEdits:
    """The whole reason Pattern B exists: reviewer additions outside
    markers must survive re-imports."""

    def test_reviewer_paragraph_outside_markers_preserved(self):
        """The walk-1 reviewer added a paragraph between the infobox and
        the career section. The next bot import rebuilds infobox + career
        from data. Their paragraph stays."""
        existing = (
            _wrap("infobox", "OLD_INFOBOX")
            + "\n\nThe reviewer added this analysis paragraph that the "
            "template doesn't render.\n\n"
            + _wrap("career", "OLD_CAREER")
        )
        new = (
            _wrap("infobox", "NEW_INFOBOX_FROM_TEMPLATE")
            + "\n"
            + _wrap("career", "NEW_CAREER_FROM_TEMPLATE")
        )
        merged = surgical_merge(existing, new)
        assert "The reviewer added this analysis paragraph" in merged
        assert "NEW_INFOBOX_FROM_TEMPLATE" in merged
        assert "NEW_CAREER_FROM_TEMPLATE" in merged
        # Old bot content fully replaced inside the managed section.
        assert "OLD_INFOBOX" not in merged
        assert "OLD_CAREER" not in merged

    def test_reviewer_edit_at_top_of_page_preserved(self):
        """Reviewer added a maintenance template or hatnote ABOVE the
        infobox. Must survive."""
        existing = (
            "{{Maintenance|reason=foo}}\n\n"
            + _wrap("infobox", "OLD")
        )
        new = _wrap("infobox", "NEW")
        merged = surgical_merge(existing, new)
        assert merged.startswith("{{Maintenance|reason=foo}}")
        assert "NEW" in merged

    def test_reviewer_edit_at_bottom_of_page_preserved(self):
        """Reviewer added categories below all the bot sections. Must
        survive."""
        existing = (
            _wrap("infobox", "OLD")
            + "\n[[Category:Hand-added by reviewer]]"
        )
        new = _wrap("infobox", "NEW")
        merged = surgical_merge(existing, new)
        assert "[[Category:Hand-added by reviewer]]" in merged
        assert "NEW" in merged

    def test_reviewer_edits_BOTH_sides_preserved(self):
        existing = (
            "PRE_TEXT\n"
            + _wrap("a", "OLD_A")
            + "\nMID_TEXT\n"
            + _wrap("b", "OLD_B")
            + "\nPOST_TEXT"
        )
        new = _wrap("a", "NEW_A") + "\n" + _wrap("b", "NEW_B")
        merged = surgical_merge(existing, new)
        assert "PRE_TEXT" in merged
        assert "MID_TEXT" in merged
        assert "POST_TEXT" in merged
        assert "NEW_A" in merged and "OLD_A" not in merged
        assert "NEW_B" in merged and "OLD_B" not in merged


class TestSurgicalMergeManagedSections:
    """Tests for the section-content-replacement invariant."""

    def test_managed_section_content_fully_replaced(self):
        existing = _wrap("infobox", "OLD\nMULTIPLE\nLINES\nHERE")
        new = _wrap("infobox", "BRAND_NEW")
        merged = surgical_merge(existing, new)
        assert "BRAND_NEW" in merged
        for line in ("OLD", "MULTIPLE", "LINES", "HERE"):
            assert line not in merged

    def test_section_present_only_in_existing_left_alone(self):
        """The bot template no longer renders the `obsolete` section,
        but the existing page (from a prior template version) still has
        it AND a reviewer may want it kept. Leave it alone — don't strip
        it."""
        existing = _wrap("infobox", "OLD_INFOBOX") + "\n" + _wrap("obsolete", "OBSOLETE_CONTENT")
        new = _wrap("infobox", "NEW_INFOBOX")
        merged = surgical_merge(existing, new)
        assert "OBSOLETE_CONTENT" in merged
        assert "NEW_INFOBOX" in merged

    def test_new_section_in_template_appended(self):
        """The template introduced a `youth-career` section that the
        existing page lacks. Append it at the end so the reviewer can
        move it if they want."""
        existing = _wrap("infobox", "OLD_INFOBOX")
        new = _wrap("infobox", "NEW_INFOBOX") + "\n" + _wrap("youth-career", "YOUTH")
        merged = surgical_merge(existing, new)
        assert "NEW_INFOBOX" in merged
        assert "YOUTH" in merged
        # New section appears AFTER the existing ones.
        assert merged.index("YOUTH") > merged.index("NEW_INFOBOX")


# ---------------------------------------------------------------------------
# merge — top-level decision routing
# ---------------------------------------------------------------------------


class TestMergeTopLevel:
    def test_last_edit_was_bot_returns_clean_rewrite(self):
        existing = _wrap("infobox", "STALE_FROM_LAST_BOT_RUN")
        new = _wrap("infobox", "FRESH_FROM_THIS_RUN") + "\n[[Category:X]]"
        merged, action = merge(existing, new, last_edit_summary="Auto-import: Updated player page")
        assert action == "clean_rewrite"
        assert merged == new

    def test_last_edit_was_reviewer_returns_surgical_merge(self):
        existing = (
            "{{Hatnote|by reviewer}}\n"
            + _wrap("infobox", "OLD")
        )
        new = _wrap("infobox", "NEW")
        merged, action = merge(existing, new, last_edit_summary="Fixed typo in lead")
        assert action == "surgical_merge"
        assert "{{Hatnote|by reviewer}}" in merged
        assert "NEW" in merged

    def test_empty_existing_returns_clean_rewrite(self):
        new = _wrap("infobox", "FIRST_RUN")
        merged, action = merge("", new, last_edit_summary=None)
        assert action == "clean_rewrite"
        assert merged == new

    def test_none_existing_returns_clean_rewrite(self):
        new = _wrap("infobox", "FIRST_RUN")
        merged, action = merge(None, new, last_edit_summary=None)
        assert action == "clean_rewrite"
        assert merged == new

    def test_whitespace_only_existing_returns_clean_rewrite(self):
        new = _wrap("infobox", "FIRST_RUN")
        merged, action = merge("   \n  \n", new, last_edit_summary=None)
        assert action == "clean_rewrite"
        assert merged == new

    def test_no_change_when_surgical_merge_yields_identical_result(self):
        """A re-run with no template changes and no reviewer edits to
        managed sections should yield merged == existing — caller can
        skip the save."""
        existing = (
            "PRE\n"
            + _wrap("a", "BODY_A")
            + "\nMID\n"
            + _wrap("b", "BODY_B")
            + "\nPOST"
        )
        new = _wrap("a", "BODY_A") + "\n" + _wrap("b", "BODY_B")
        merged, action = merge(existing, new, last_edit_summary="Reviewer touched something")
        assert action == "no_change"
        assert merged == existing


# ---------------------------------------------------------------------------
# Integration shape — end-to-end smoke
# ---------------------------------------------------------------------------


class TestEndToEndShape:
    """Mimic the actual save flow shape: existing wikitext from a prior
    bot run + reviewer touched it, new wikitext from this run's
    re-render with template changes."""

    def test_realistic_reviewer_workflow(self):
        existing = (
            "{{Maintenance|stale=2024}}\n"
            + _wrap("infobox", "{{Player infobox|name_hebrew=ניב אליאסי|tmk_id=912586}}")
            + "\n\nReviewer-added prose: Played his first season in 2022.\n\n"
            + _wrap("career", "{{wikitable|...}}")
            + "\n\n[[Category:Goalkeepers]]\n"
            + "[[Category:Hand-curated]]\n"
        )
        # New template added a youth-career section + updated the
        # career table with this season's matches.
        new = (
            _wrap("infobox", "{{Player infobox|name_hebrew=ניב אליאסי|tmk_id=912586|preferred_foot=left}}")
            + "\n"
            + _wrap("youth-career", "{{Youth wikitable|...}}")
            + "\n"
            + _wrap("career", "{{wikitable|...|NEW_SEASON_ROW}}")
        )
        merged, action = merge(
            existing, new,
            last_edit_summary="Added prose summary + categories",
        )
        assert action == "surgical_merge"
        # Reviewer's prose preserved.
        assert "Reviewer-added prose" in merged
        # Categories preserved.
        assert "[[Category:Hand-curated]]" in merged
        assert "[[Category:Goalkeepers]]" in merged
        # Maintenance template preserved.
        assert "{{Maintenance|stale=2024}}" in merged
        # New infobox content (with preferred_foot) replaced the old.
        assert "preferred_foot=left" in merged
        # New section appended.
        assert "Youth wikitable" in merged
        # Career row updated.
        assert "NEW_SEASON_ROW" in merged


# ---------------------------------------------------------------------------
# Constant smoke — pin the prefix string PHP and Python share
# ---------------------------------------------------------------------------


class TestSharedConvention:
    def test_bot_edit_summary_prefix_value(self):
        """Pin the literal prefix string. PHP-side
        (`docker/import-pages.php`) and Python-side agree on
        `Auto-import:` — if this ever changes, BOTH sides need updating.
        This test is the regression alarm."""
        assert BOT_EDIT_SUMMARY_PREFIX == "Auto-import:"
