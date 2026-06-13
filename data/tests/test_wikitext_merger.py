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

import pytest

from wiki_import.wikitext_merger import (
    BOT_EDIT_SUMMARY_PREFIX,
    BOT_MERGE_SUMMARY_PREFIX,
    extract_managed_sections,
    is_bot_authored,
    is_clean_rewrite_eligible,
    make_end_marker,
    make_start_marker,
    merge,
    surgical_merge,
    validate_section_id,
)


def _wrap(section_id: str, body: str) -> str:
    """Helper — wrap `body` in start/end markers for `section_id`."""
    return f"{make_start_marker(section_id)}\n{body}\n{make_end_marker(section_id)}"


def _save_summary_for(action: str) -> str:
    """The summary the caller (`_edit_page`) is contracted to stamp
    based on the merge action. Encodes the load-bearing convention so
    test scenarios mimic the production save shape exactly."""
    if action == "clean_rewrite":
        return f"{BOT_EDIT_SUMMARY_PREFIX} Wiki7Bot full rewrite"
    if action == "surgical_merge":
        return f"{BOT_MERGE_SUMMARY_PREFIX} Wiki7Bot section update; reviewer content preserved"
    raise AssertionError(f"no save expected for action={action!r}")


# ---------------------------------------------------------------------------
# is_bot_authored — the "was this ours" gate
# ---------------------------------------------------------------------------


class TestIsBotAuthored:
    """`is_bot_authored` is the OWNERSHIP check — matches either
    `Auto-import:` or `Auto-merge:` because both prefixes mean "bot
    saved this revision". Different from `is_clean_rewrite_eligible`
    which only matches `Auto-import:` — see `TestIsCleanRewriteEligible`."""

    def test_auto_import_prefix(self):
        assert is_bot_authored("Auto-import: Created player page for X") is True

    def test_auto_merge_prefix_is_also_ours(self):
        """Reviewer-pass fix (2026-06-13): a previous surgical-merge
        save (which uses `Auto-merge:`) IS still bot-authored for
        ownership purposes — only the CLEAN-REWRITE decision is
        stricter."""
        assert is_bot_authored("Auto-merge: Wiki7Bot section update") is True

    def test_auto_import_with_just_colon(self):
        assert is_bot_authored("Auto-import:") is True

    def test_auto_merge_with_just_colon(self):
        assert is_bot_authored("Auto-merge:") is True

    def test_leading_whitespace_tolerated(self):
        assert is_bot_authored("  Auto-import: X") is True
        assert is_bot_authored("\n\tAuto-merge: X") is True

    def test_non_matching_prefix(self):
        assert is_bot_authored("Manual edit: fix typo") is False

    def test_reviewer_summary(self):
        assert is_bot_authored("Cleaned up the lead") is False

    def test_empty_or_none(self):
        assert is_bot_authored(None) is False
        assert is_bot_authored("") is False
        assert is_bot_authored("   ") is False

    def test_auto_import_substring_not_at_start_is_NOT_ours(self):
        assert is_bot_authored("Note: Auto-import: changed something") is False
        assert is_bot_authored("Note: Auto-merge: changed something") is False


class TestIsCleanRewriteEligible:
    """Reviewer-pass fix (2026-06-13): the STRICTER discrimination —
    only `Auto-import:` qualifies. `Auto-merge:` does NOT (would
    create a two-run wedge that clobbers reviewer content). See
    `TestTwoRunWedge` below for the regression test."""

    def test_auto_import_prefix_eligible(self):
        assert is_clean_rewrite_eligible("Auto-import: anything") is True

    def test_auto_merge_prefix_NOT_eligible(self):
        """The load-bearing distinction. A page whose last edit was a
        surgical-merge save MUST NOT be clean-rewritten on the next run;
        the surgical-merge result encodes reviewer content that would
        be lost."""
        assert is_clean_rewrite_eligible("Auto-merge: anything") is False

    def test_reviewer_summary_NOT_eligible(self):
        assert is_clean_rewrite_eligible("Manual edit") is False

    def test_empty_or_none_NOT_eligible(self):
        assert is_clean_rewrite_eligible(None) is False
        assert is_clean_rewrite_eligible("") is False


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
        (`docker/import-pages.php` lines 222 + 270) and Python-side
        agree on `Auto-import:` — if this ever changes, BOTH sides need
        updating. This test is the regression alarm."""
        assert BOT_EDIT_SUMMARY_PREFIX == "Auto-import:"

    def test_bot_merge_summary_prefix_value(self):
        """Pin the literal merge prefix string. Reviewer-pass taxonomy
        fix (2026-06-13). PHP-side currently only knows about
        `Auto-import:` — its preserve-edit guard treats `Auto-merge:`
        as reviewer-touched (correct semantic — a merged page DID have
        reviewer content), so no PHP change needed. But if either side
        ever renames either prefix, both sides must update in lockstep
        and the parity assumption needs revisiting."""
        assert BOT_MERGE_SUMMARY_PREFIX == "Auto-merge:"


class TestTwoRunWedgeRegression:
    """Reviewer-pass fix (2026-06-13): the CRITICAL B.3 design trap. The
    original single-prefix design would have lost reviewer edits on the
    third bot run:

      Run 1: bot CREATES page → save with Auto-import:
      Run 2: reviewer COPYEDITS outside markers → save (no prefix)
      Run 3: bot re-renders → surgical_merge → save with Auto-import:
             (the buggy original design)
      Run 4: bot re-renders → sees Auto-import: → clean_rewrite →
             reviewer paragraph GONE.

    The two-prefix taxonomy (`Auto-import:` vs `Auto-merge:`) fixes it.
    These tests run the exact sequence and assert the reviewer's edit
    survives across multiple bot runs."""

    def test_two_consecutive_bot_runs_preserve_reviewer_paragraph(self):
        # Run 1: bot creates the page.
        new_v1 = (
            _wrap("infobox", "INFOBOX_V1")
            + "\n"
            + _wrap("career", "CAREER_V1")
        )
        existing_text = None
        last_summary = None
        merged_1, action_1 = merge(existing_text, new_v1, last_summary)
        assert action_1 == "clean_rewrite"
        assert merged_1 == new_v1
        # Save uses the action-appropriate prefix.
        last_summary = _save_summary_for(action_1)
        existing_text = merged_1

        # Reviewer copyedits between bot runs. Adds a paragraph
        # OUTSIDE the markers and updates the summary.
        REVIEWER_PROSE = "Reviewer-added analysis paragraph."
        existing_text = (
            _wrap("infobox", "INFOBOX_V1")
            + f"\n\n{REVIEWER_PROSE}\n\n"
            + _wrap("career", "CAREER_V1")
        )
        last_summary = "Added prose summary"

        # Run 2: bot re-renders with template tweaks. Reviewer-prose
        # is outside markers; must survive.
        new_v2 = (
            _wrap("infobox", "INFOBOX_V2")
            + "\n"
            + _wrap("career", "CAREER_V2")
        )
        merged_2, action_2 = merge(existing_text, new_v2, last_summary)
        assert action_2 == "surgical_merge", (
            "Last edit was reviewer → must surgical-merge, not clean-rewrite"
        )
        assert REVIEWER_PROSE in merged_2
        assert "INFOBOX_V2" in merged_2 and "CAREER_V2" in merged_2
        # Save with the merge prefix.
        last_summary = _save_summary_for(action_2)
        existing_text = merged_2

        # Run 3 — the wedge case. NO reviewer changes since run 2. Old
        # single-prefix design would see Auto-import: as last summary,
        # clean-rewrite, and CLOBBER the reviewer paragraph. The
        # taxonomy fix means the save from run 2 used `Auto-merge:`,
        # which is NOT clean-rewrite-eligible — the merger does a
        # surgical_merge that yields identical text → no_change.
        new_v3 = (
            _wrap("infobox", "INFOBOX_V2")  # no template changes since v2
            + "\n"
            + _wrap("career", "CAREER_V2")
        )
        merged_3, action_3 = merge(existing_text, new_v3, last_summary)
        assert action_3 == "no_change", (
            f"Last edit was Auto-merge: → must NOT be clean-rewrite-"
            f"eligible. Got action={action_3!r}; merged_3 differs from "
            f"existing_text — reviewer paragraph would be lost on save."
        )
        # The reviewer paragraph survives the third run.
        assert REVIEWER_PROSE in merged_3

    def test_run_4_after_real_template_change_still_preserves_reviewer(self):
        """Same wedge defense, but with a REAL template change on run
        4 so it MUST surgical_merge (not no_change). Verifies the
        Auto-merge: → surgical_merge path also preserves prose."""
        REVIEWER_PROSE = "Reviewer-added analysis."

        existing_after_review_and_merge = (
            _wrap("infobox", "INFOBOX_V2")
            + f"\n\n{REVIEWER_PROSE}\n\n"
            + _wrap("career", "CAREER_V2")
        )
        # Save from a prior surgical_merge — i.e. the wedge scenario.
        last_summary = _save_summary_for("surgical_merge")

        # Template legitimately changed (new field rendered into infobox).
        new_v3 = (
            _wrap("infobox", "INFOBOX_V3_with_new_field")
            + "\n"
            + _wrap("career", "CAREER_V2")
        )
        merged_3, action_3 = merge(
            existing_after_review_and_merge, new_v3, last_summary,
        )
        assert action_3 == "surgical_merge"
        # Real template change applied.
        assert "INFOBOX_V3_with_new_field" in merged_3
        # AND reviewer prose preserved.
        assert REVIEWER_PROSE in merged_3


class TestValidateSectionId:
    """Reviewer-pass constraint #4 (B.2 review, 2026-06-13): section
    ids are a permanent contract — must be space-free + kebab/snake-
    case + alpha-leading. `validate_section_id` fails fast at template-
    render time so a typo surfaces immediately, not in production."""

    def test_valid_simple_id(self):
        assert validate_section_id("infobox") == "infobox"

    def test_valid_kebab_case(self):
        assert validate_section_id("youth-career") == "youth-career"

    def test_valid_snake_case(self):
        assert validate_section_id("match_categories") == "match_categories"

    def test_valid_mixed_kebab_snake_with_digit(self):
        assert validate_section_id("section-2") == "section-2"

    def test_invalid_contains_space(self):
        with pytest.raises(ValueError, match="invalid section_id"):
            validate_section_id("youth career")

    def test_invalid_leading_digit(self):
        with pytest.raises(ValueError):
            validate_section_id("1section")

    def test_invalid_leading_hyphen(self):
        with pytest.raises(ValueError):
            validate_section_id("-section")

    def test_invalid_empty(self):
        with pytest.raises(ValueError):
            validate_section_id("")

    def test_make_start_marker_validates(self):
        with pytest.raises(ValueError):
            make_start_marker("invalid id with spaces")

    def test_make_end_marker_validates(self):
        with pytest.raises(ValueError):
            make_end_marker("invalid id with spaces")


class TestMarkerWarningText:
    """Reviewer-pass constraint #1 (B.2 review, 2026-06-13): markers
    must bake in a human-readable warning. Edits INSIDE managed sections
    are silently lost on re-import; the markers are invisible in read
    view; the warning is the only in-place signal an editor gets when
    they go to source view."""

    def test_start_marker_contains_do_not_edit_warning(self):
        marker = make_start_marker("infobox")
        assert "DO NOT EDIT" in marker
        assert "Wiki7Bot overwrites" in marker

    def test_start_marker_id_still_extractable(self):
        """The warning text must not interfere with id extraction —
        the regex still finds it cleanly."""
        marker = make_start_marker("youth-career")
        sections = extract_managed_sections(marker + "\nBODY\n" + make_end_marker("youth-career"))
        assert "youth-career" in sections


class TestEndBeforeStartDefensive:
    """🟢 Reviewer-pass minor: _find_section_spans previously silently
    corrupted on inverted markers. Now it skips + warns."""

    def test_end_before_start_skipped(self):
        text = (
            f"{make_end_marker('weird')}\n"
            "stuff in middle\n"
            f"{make_start_marker('weird')}\n"
            "body\n"
        )
        sections = extract_managed_sections(text)
        # Either skipped (preferred — defensive) or empty. Must NOT
        # produce a negative-length / nonsense span.
        assert "weird" not in sections

    def test_end_before_start_does_not_crash_merger(self):
        """Even with malformed markers, surgical_merge should not raise."""
        bad_existing = (
            f"{make_end_marker('a')}\n"
            "middle\n"
            f"{make_start_marker('a')}\n"
            "body\n"
        )
        good_new = _wrap("a", "NEW_A_CONTENT")
        # Should not raise. Output preserves the malformed existing
        # (we don't try to splice into spans we can't identify) and
        # appends the new section.
        result = surgical_merge(bad_existing, good_new)
        assert "NEW_A_CONTENT" in result
