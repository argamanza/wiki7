"""Yellow-triage fix tests (reviewer-pass, 2026-06-13).

The `_format_match_title` + `_wikitext_sanitize_title` helpers are the
single source of truth for match-page titles AND for the link text in
`competition_season.j2`. These tests pin:
  - Date is run through `to_il_date` (raw TM English date → DD/MM/YYYY)
  - Wikitext-illegal chars are stripped/replaced consistently
  - Empty / missing fields produce stable fallback strings
"""


from wiki_import.import_matches import (
    _format_match_title,
    _wikitext_sanitize_title,
    _match_page_title,
)


class TestFormatMatchTitle:
    def test_modern_iso_date_renders_il(self):
        """ISO `YYYY-MM-DD` → `DD/MM/YYYY` via the il_date filter."""
        assert _format_match_title("2024-08-25", "Maccabi Netanya", "Ligat ha'Al") == (
            "25/08/2024 נגד Maccabi Netanya (Ligat ha'Al)"
        )

    def test_tm_english_date_renders_il(self):
        """TM-style `Sun 25/08/24` → `25/08/2024`. The pre-fix behavior
        was leaving the English prefix in the title, producing inconsistent
        per-page titles vs the per-row link in `competition_season.j2`."""
        assert _format_match_title("Sun 25/08/24", "M. Netanya", "Ligat ha'Al") == (
            "25/08/2024 נגד M. Netanya (Ligat ha'Al)"
        )

    def test_no_competition_omits_trailing_parens(self):
        assert _format_match_title("2024-08-25", "Opponent", "") == (
            "25/08/2024 נגד Opponent"
        )

    def test_missing_date_uses_hebrew_fallback(self):
        result = _format_match_title("", "Opponent", "Ligat ha'Al")
        assert result.startswith("תאריך לא ידוע")

    def test_missing_opponent_uses_hebrew_fallback(self):
        result = _format_match_title("2024-08-25", "", "Cup")
        assert "לא ידוע" in result


class TestWikitextSanitizeTitle:
    def test_strips_wikitext_brackets(self):
        assert _wikitext_sanitize_title("[X] {Y}") == "(X) (Y)"

    def test_strips_pipe_and_hash(self):
        """`|` and `#` are wikitext-illegal in titles. The pre-fix
        `competition_season.j2` link text skipped this sanitization, so
        an opponent or competition with these chars produced a link to
        a title that doesn't match the rendered match-report page."""
        assert _wikitext_sanitize_title("A|B #C") == "A-B C"

    def test_clean_title_unchanged(self):
        assert _wikitext_sanitize_title("25/08/2024 נגד Opponent") == (
            "25/08/2024 נגד Opponent"
        )


class TestMatchTitleParityWithLinkBuilder:
    """The renderer side (`_match_page_title`) and the link-builder side
    (the `match_title` Jinja filter — both call into
    `_format_match_title`) MUST produce identical strings. Pre-fix
    they diverged. Pin parity."""

    def test_renderer_matches_link_builder(self):
        match = {
            "date": "2024-08-25",
            "opponent": "M. Netanya",
            "competition": "Ligat ha'Al",
        }
        title_from_renderer = _match_page_title(match)
        title_from_link_builder = _format_match_title(
            match["date"], match["opponent"], match["competition"],
        )
        assert title_from_renderer == title_from_link_builder
