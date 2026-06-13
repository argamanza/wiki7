"""Tests for data_pipeline.helpers module."""

from datetime import date
from data_pipeline.helpers import (
    hbs_match_outcome,
    is_all_hebrew,
    is_youth_club_name,
    parse_birth_date,
    parse_countries,
    is_homegrown,
    is_retired,
    pivot_two_digit_year,
    to_il_date,
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


class TestIsYouthClubName:
    """Iter-cycle 1 walk (2026-06-12): TM emits youth/academy team labels with
    distinct suffixes. The player_page template buckets transfers into "Youth
    career" and senior "Career" based on the destination club's classification."""

    def test_u_age_groups_classified_youth(self):
        for suffix in ("U15", "U17", "U19", "U20", "U21", "U23"):
            assert is_youth_club_name(f"Some Club {suffix}") is True, suffix

    def test_yth_marker_with_trailing_dot(self):
        # Real corpus has both "Sporting Yth" and "Sporting Yth." — the
        # trailing dot must not break the match.
        assert is_youth_club_name("Sporting Yth") is True
        assert is_youth_club_name("Sporting Yth.") is True

    def test_youth_word_form(self):
        assert is_youth_club_name("Real Madrid Youth") is True

    def test_spanish_portuguese_youth_words(self):
        assert is_youth_club_name("Real Madrid Juvenil") is True
        assert is_youth_club_name("Real Madrid Cadete") is True
        assert is_youth_club_name("Some Club Junior") is True

    def test_sub_pattern_classified_youth(self):
        # Real corpus has "Sporting Sub-15"; the dash is part of the marker.
        assert is_youth_club_name("Sporting Sub-15") is True
        assert is_youth_club_name("Sporting Sub15") is True

    def test_b_team_NOT_youth(self):
        """B-teams / II / Reserves are senior reserve tiers — the player on
        them is already professional. Wikipedia infobox convention places
        them in senior career."""
        assert is_youth_club_name("Benfica B") is False
        assert is_youth_club_name("Chaves B") is False
        assert is_youth_club_name("1. FC Nürnberg II") is False

    def test_senior_clubs_NOT_youth(self):
        assert is_youth_club_name("Hapoel Beer Sheva") is False
        assert is_youth_club_name("1.FC Nuremberg") is False
        assert is_youth_club_name("FC Rapid 1923") is False  # year suffix isn't age group
        assert is_youth_club_name("Real Madrid") is False

    def test_marker_only_matches_as_suffix(self):
        """'U19' embedded mid-name (rare) isn't a youth classification.
        '#U19 Cup' style hashtag-form etc — must be at the end only."""
        assert is_youth_club_name("Sub-15 Club Madrid") is False
        assert is_youth_club_name("U17 Olympics") is False

    def test_empty_and_none(self):
        assert is_youth_club_name("") is False
        assert is_youth_club_name(None) is False

    def test_case_insensitive(self):
        assert is_youth_club_name("Some Club u19") is True
        assert is_youth_club_name("Some Club YTH") is True

    def test_hebrew_under_n_marker(self):
        """Iter-cycle 1 walk: apply_hebrew_mapping rewrites "Benfica U17"
        to "בנפיקה תחת 17". The classifier runs on Hebrew-mapped data in
        the import_players path — must recognise the Hebrew form too,
        otherwise 53 transfers in the 2024/25 corpus would be silently
        misbucketed as senior."""
        assert is_youth_club_name("בנפיקה תחת 17") is True
        assert is_youth_club_name("הפועל באר שבע תחת 19") is True
        assert is_youth_club_name("קרוזיירו תחת 20") is True

    def test_hebrew_noar_marker(self):
        """'נוער' = 'youth' in Hebrew. Used for academy / Yth labels post-
        translation ('Sporting Yth' → 'ספורטינג נוער')."""
        assert is_youth_club_name("ספורטינג נוער") is True
        assert is_youth_club_name("בנפיקה נוער") is True

    def test_hebrew_senior_clubs_NOT_youth(self):
        assert is_youth_club_name("הפועל באר שבע") is False
        assert is_youth_club_name("מכבי תל אביב") is False
        assert is_youth_club_name("בנפיקה") is False

    def test_hebrew_marker_only_as_suffix(self):
        """If 'תחת' or 'נוער' appears mid-name (rare), it doesn't classify
        the club as youth — must be at the end."""
        # Synthetic edge case: 'נוער' not at the end
        assert is_youth_club_name("נוער מועדון") is False


class TestPivotTwoDigitYear:
    """§6 ③ fix from the 2026-06-12 review: the project's standard 2-digit
    year pivot. Default cutoff = 30. Used by `to_il_date` for the YY date
    case AND mirrored by the transfers + platzierungen spiders for the
    season-label pivot."""

    def test_modern_years_pivot_to_2000s(self):
        assert pivot_two_digit_year(0) == 2000
        assert pivot_two_digit_year(24) == 2024
        assert pivot_two_digit_year(29) == 2029  # last 2000s year at default cutoff

    def test_pre_2000_years_pivot_to_1900s(self):
        """The corruption case: founding-era years must bin into 19XX, not
        20XX. HBS founded ~1949/50, so 49 is the operative boundary."""
        assert pivot_two_digit_year(30) == 1930  # first 1900s year at default cutoff
        assert pivot_two_digit_year(49) == 1949  # the §6 ③ regression case
        assert pivot_two_digit_year(50) == 1950
        assert pivot_two_digit_year(87) == 1987  # the to_il_date "25/07/87" regression case
        assert pivot_two_digit_year(99) == 1999

    def test_already_4digit_passes_through(self):
        """Idempotency — callers may sometimes have a 4-digit value already."""
        assert pivot_two_digit_year(2024) == 2024
        assert pivot_two_digit_year(1949) == 1949

    def test_custom_cutoff(self):
        """Cutoff can be overridden — by 2030 we'll bump the default to 25."""
        assert pivot_two_digit_year(29, cutoff=25) == 1929
        assert pivot_two_digit_year(24, cutoff=25) == 2024


class TestToIlDateYearPivot:
    """The historical-corruption regression test for `to_il_date`. The pre-
    fix version always prepended '20' to a 2-digit year, so a 1987 birth
    date came out as 2087 — invisible until anyone tried to render a player
    born before 2000. Iter-cycle 1 walk would have caught it on the first
    pre-2000 player imported."""

    def test_pre_2000_yy_pivots_to_1900s(self):
        assert to_il_date("25/07/87") == "25/07/1987"
        assert to_il_date("01/01/49") == "01/01/1949"

    def test_modern_yy_pivots_to_2000s(self):
        assert to_il_date("25/07/24") == "25/07/2024"
        assert to_il_date("01/01/29") == "01/01/2029"

    def test_yy_pivot_works_under_day_prefix(self):
        """TM emits "Thu 25/07/87" with a day-of-week prefix. The strip-
        prefix path feeds the YY pivot — exercise the chain."""
        assert to_il_date("Thu 25/07/87") == "25/07/1987"
        assert to_il_date("Sun 25/07/24") == "25/07/2024"

    def test_dd_mm_yyyy_still_passes_through(self):
        """Regression: the 4-digit-year path is unaffected by the pivot."""
        assert to_il_date("25/07/1987") == "25/07/1987"
        assert to_il_date("25/07/2024") == "25/07/2024"


class TestHbsMatchOutcome:
    """§6 ③ fix from the 2026-06-12 review: the win/loss/draw categorisation
    must consult `venue` (H/A) — TM's result string is always
    'home_goals:away_goals' so for an away match the home/away need to be
    swapped to get HBS's perspective. Pre-fix code compared the raw goals
    directly, miscategorising ~half of all matches (every away match)."""

    def test_home_win(self):
        # HBS at home, scored 2, opponent scored 1.
        assert hbs_match_outcome("2:1", "H") == "win"

    def test_home_loss(self):
        # HBS at home, scored 0, opponent scored 1.
        assert hbs_match_outcome("0:1", "H") == "loss"

    def test_home_draw(self):
        assert hbs_match_outcome("1:1", "H") == "draw"

    def test_away_win(self):
        """The regression-class case: HBS played away, result string "0:2"
        means home=0, away=2 → HBS scored 2, opponent scored 0 → WIN.
        Pre-fix code compared 0 vs 2 directly and called this a LOSS."""
        assert hbs_match_outcome("0:2", "A") == "win"
        # Real corpus sample: row 2 of 2024 matches was venue=A, result=0:4
        # (HBS won 4-0 at SC Dimona).
        assert hbs_match_outcome("0:4", "A") == "win"

    def test_away_loss(self):
        assert hbs_match_outcome("2:0", "A") == "loss"

    def test_away_draw(self):
        assert hbs_match_outcome("1:1", "A") == "draw"

    def test_unknown_venue_returns_empty(self):
        """Defensive: missing or weird venue → empty string, NOT a guess.
        Caller should fall through to "uncategorised" rather than display
        a wrong category."""
        assert hbs_match_outcome("2:1", None) == ""
        assert hbs_match_outcome("2:1", "") == ""
        assert hbs_match_outcome("2:1", "X") == ""

    def test_missing_or_unparseable_result_returns_empty(self):
        assert hbs_match_outcome(None, "H") == ""
        assert hbs_match_outcome("", "H") == ""
        assert hbs_match_outcome("postponed", "H") == ""
        assert hbs_match_outcome("2-1", "H") == ""  # dash not colon
        assert hbs_match_outcome("a:b", "H") == ""

    def test_venue_case_insensitive(self):
        assert hbs_match_outcome("2:1", "h") == "win"
        assert hbs_match_outcome("2:1", "a") == "loss"
