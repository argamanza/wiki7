"""Tests for data_pipeline.helpers module."""

from datetime import date
from data_pipeline.helpers import (
    is_all_hebrew,
    parse_birth_date,
    parse_countries,
    is_homegrown,
    is_retired,
    to_season_display,
)


class TestIsAllHebrew:
    def test_hebrew_text(self):
        assert is_all_hebrew("שגיב יחזקאל") is True

    def test_english_text(self):
        assert is_all_hebrew("Sagiv Jehezkel") is False

    def test_mixed_text(self):
        assert is_all_hebrew("שגיב Sagiv") is False

    def test_empty_string(self):
        assert is_all_hebrew("") is False

    def test_hebrew_with_spaces(self):
        assert is_all_hebrew("תומר חמד") is True

    def test_numbers(self):
        assert is_all_hebrew("123") is False


class TestParseBirthDate:
    def test_standard_date(self):
        result = parse_birth_date("Jan 14, 2000")
        assert result == date(2000, 1, 14)

    def test_date_with_age(self):
        result = parse_birth_date("Jan 14, 2000 (24)")
        assert result == date(2000, 1, 14)

    def test_empty_string(self):
        assert parse_birth_date("") is None

    def test_none(self):
        assert parse_birth_date(None) is None

    def test_invalid_date(self):
        assert parse_birth_date("not a date") is None

    def test_different_format(self):
        result = parse_birth_date("May 2, 1987")
        assert result == date(1987, 5, 2)


class TestParseCountries:
    def test_single_country(self):
        result = parse_countries("Israel")
        assert "Israel" in result

    def test_empty_string(self):
        assert parse_countries("") == []

    def test_none(self):
        assert parse_countries(None) == []

    def test_whitespace_only(self):
        assert parse_countries("   ") == []

    def test_known_country(self):
        result = parse_countries("Germany")
        assert len(result) >= 1
        assert "Germany" in result


class TestIsHomegrown:
    def test_homegrown_player(self):
        player = {
            "transfers": [
                {"from": "Hapoel Beer Sheva U19", "to": "Hapoel Beer Sheva", "fee": "-"}
            ]
        }
        assert is_homegrown(player) is True

    def test_not_homegrown(self):
        player = {
            "transfers": [
                {"from": "Maccabi Tel Aviv", "to": "Hapoel Beer Sheva", "fee": "€500k"}
            ]
        }
        assert is_homegrown(player) is False

    def test_no_transfers(self):
        player = {"transfers": []}
        assert is_homegrown(player) is False

    def test_missing_transfers(self):
        player = {}
        assert is_homegrown(player) is False

    def test_hbs_u19_variant(self):
        player = {
            "transfers": [
                {"from": "H. B. Sheva U19", "to": "Hapoel Beer Sheva", "fee": "-"}
            ]
        }
        assert is_homegrown(player) is True


class TestIsRetired:
    def test_retired_player(self):
        player = {
            "transfers": [
                {"from": "Hapoel Beer Sheva", "to": "Retired", "fee": "-"}
            ]
        }
        assert is_retired(player) is True

    def test_active_player(self):
        player = {
            "transfers": [
                {"from": "Maccabi Haifa", "to": "Hapoel Beer Sheva", "fee": "€1m"}
            ]
        }
        assert is_retired(player) is False

    def test_no_transfers(self):
        player = {"transfers": []}
        assert is_retired(player) is False

    def test_missing_transfers(self):
        player = {}
        assert is_retired(player) is False


class TestToSeasonDisplay:
    """Phase 3a R2: single helper that owns the bare-integer → slash format
    conversion. Internal join-key stays bare; human-visible surfaces (page
    titles, h1 headings, category names) go through this.
    """

    def test_modern_season(self):
        assert to_season_display("2024") == "2024/25"
        assert to_season_display(2024) == "2024/25"

    def test_century_boundary_century(self):
        assert to_season_display("1999") == "1999/00"
        assert to_season_display("2000") == "2000/01"

    def test_historical_season(self):
        assert to_season_display("1975") == "1975/76"
        assert to_season_display("1949") == "1949/50"

    def test_passthrough_when_already_slash(self):
        # Defensive: if a caller has already converted, don't double-convert.
        assert to_season_display("2024/25") == "2024/25"

    def test_empty_and_none(self):
        assert to_season_display("") == ""
        assert to_season_display(None) == ""

    def test_garbage_input_passes_through(self):
        """Malformed input passes through unchanged rather than crashing —
        keeps the helper safe to use at every render site without try/except.
        """
        assert to_season_display("not-a-year") == "not-a-year"
