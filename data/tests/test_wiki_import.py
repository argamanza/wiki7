"""Tests for wiki_import module — template rendering and dry-run behavior."""

from pathlib import Path

import pytest

from data_pipeline.normalize_enrich_players import main as normalize_main

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def normalized_data(tmp_path):
    """Run normalization on fixtures and return output directory."""
    normalize_main(
        raw_path=str(FIXTURES_DIR / "players_sample.json"),
        out_dir=str(tmp_path),
    )
    return tmp_path


class TestPlayerPageRendering:
    def test_render_player_page(self, normalized_data):
        from wiki_import.import_players import _build_player_page, _load_jsonl

        players = _load_jsonl(normalized_data / "players.jsonl")
        transfers = _load_jsonl(normalized_data / "transfers.jsonl")
        market_values = _load_jsonl(normalized_data / "market_values.jsonl")

        content = _build_player_page(players[0], transfers, market_values)
        assert "Player infobox" in content
        assert "Sagiv Jehezkel" in content
        assert "[[קטגוריה:שחקנים]]" in content
        assert "[[קטגוריה:סגל נוכחי]]" in content

    def test_render_player_page_with_stats(self, normalized_data):
        from wiki_import.import_players import _build_player_page, _load_jsonl

        players = _load_jsonl(normalized_data / "players.jsonl")
        transfers = _load_jsonl(normalized_data / "transfers.jsonl")
        market_values = _load_jsonl(normalized_data / "market_values.jsonl")
        stats = [
            {"player_id": players[0]["id"], "season": "2024", "appearances": 30,
             "goals": 8, "assists": 5, "yellow_cards": 3, "red_cards": 0, "minutes_played": 2450},
        ]

        content = _build_player_page(players[0], transfers, market_values, stats)
        assert "סטטיסטיקה עונתית" in content
        assert "2024" in content
        assert "30" in content  # appearances

    def test_dry_run_import(self, normalized_data):
        from wiki_import.import_players import import_players

        summary = import_players(
            players_path=normalized_data / "players.jsonl",
            transfers_path=normalized_data / "transfers.jsonl",
            market_values_path=normalized_data / "market_values.jsonl",
            dry_run=True,
        )
        assert summary["created"] == 3
        assert summary["failed"] == 0


class TestKeeperAndCompetitionStatsRendering:
    """keeper-stats branch: conditional keeper columns + the per-competition
    section render only for the right players, and Cargo stores carry the new
    fields (NULL for outfielders)."""

    KEEPER = {
        "id": "GK1", "name_english": "Test Keeper", "name_hebrew": "שוער בדיקה",
        "main_position": "שוער", "other_positions": [], "nationality": ["Israel"],
        "current_squad": True, "homegrown": False, "retired": False, "is_captain": False,
    }
    OUTFIELD = dict(KEEPER, id="OF1", name_english="Test Forward",
                    name_hebrew="חלוץ בדיקה", main_position="חלוץ מרכזי")

    def _keeper_stats(self, pid):
        return [{"player_id": pid, "season": "2024", "appearances": 30, "goals": 0,
                 "assists": 0, "yellow_cards": 1, "second_yellow_cards": 0, "red_cards": 0,
                 "minutes_played": 2700, "clean_sheets": 12, "goals_conceded": 25,
                 "own_goals": 0, "subs_on": 0, "subs_off": 0, "ppg": 1.8}]

    def _keeper_comp(self, pid):
        return [{"player_id": pid, "season": "2024", "competition": "ליגת העל",
                 "appearances": 28, "goals": 0, "assists": 0, "yellow_cards": 1,
                 "second_yellow_cards": 0, "red_cards": 0, "own_goals": 0,
                 "clean_sheets": 11, "goals_conceded": 23}]

    def test_keeper_page_has_keeper_columns_and_competition_section(self):
        from wiki_import.import_players import _build_player_page

        page = _build_player_page(self.KEEPER, [], [], self._keeper_stats("GK1"),
                                  self._keeper_comp("GK1"))
        assert "רשתות נקיות" in page          # clean sheets header (keeper only)
        assert "שערים שספג" in page           # goals conceded header
        assert "סטטיסטיקה לפי מפעל" in page    # per-competition section
        assert "ליגת העל" in page             # merged league row
        # Cargo stores carry the keeper values + per-competition table.
        assert "| clean_sheets = 12" in page
        assert "Cargo/PlayerCompetitionStats" in page
        assert "| goals_conceded = 23" in page

    def test_outfielder_page_omits_keeper_columns(self):
        from wiki_import.import_players import _build_player_page

        stats = [{"player_id": "OF1", "season": "2024", "appearances": 25, "goals": 7,
                  "assists": 4, "yellow_cards": 2, "second_yellow_cards": 0, "red_cards": 0,
                  "minutes_played": 2100, "clean_sheets": None, "goals_conceded": None,
                  "own_goals": 0, "subs_on": 1, "subs_off": 5, "ppg": 2.0}]
        comp = [{"player_id": "OF1", "season": "2024", "competition": "ליגת העל",
                 "appearances": 25, "goals": 7, "assists": 4, "yellow_cards": 2,
                 "second_yellow_cards": 0, "red_cards": 0, "own_goals": 0,
                 "clean_sheets": None, "goals_conceded": None}]
        page = _build_player_page(self.OUTFIELD, [], [], stats, comp)
        assert "רשתות נקיות" not in page       # no keeper header
        assert "שערים שספג" not in page
        # outfielder still gets subs/ppg + the per-competition section
        assert "סטטיסטיקה לפי מפעל" in page
        assert "נקודות למשחק" in page
        # Cargo store NULLs the keeper fields (empty value after '=').
        assert "| clean_sheets = \n" in page or "| clean_sheets =\n" in page

    def test_own_goal_column_only_shown_when_present(self):
        from wiki_import.import_players import _build_player_page

        # No own goals anywhere → the column is suppressed.
        stats = [{"player_id": "OF1", "season": "2024", "appearances": 10, "goals": 1,
                  "assists": 0, "yellow_cards": 0, "second_yellow_cards": 0, "red_cards": 0,
                  "minutes_played": 900, "clean_sheets": None, "goals_conceded": None,
                  "own_goals": 0, "subs_on": 0, "subs_off": 0, "ppg": 1.0}]
        page = _build_player_page(self.OUTFIELD, [], [], stats, [])
        assert "שערים עצמיים" not in page
        # With an own goal, the column appears.
        stats[0]["own_goals"] = 1
        page2 = _build_player_page(self.OUTFIELD, [], [], stats, [])
        assert "שערים עצמיים" in page2

    @staticmethod
    def _wikitables(page):
        """Yield the lines of each {| ... |} wikitable block in the page."""
        tables, current = [], None
        for line in page.splitlines():
            s = line.strip()
            if s.startswith("{|"):
                current = []
            elif current is not None and s == "|}":
                tables.append(current)
                current = None
            elif current is not None:
                current.append(line)
        return tables

    @staticmethod
    def _cell_count(line):
        """Cell count for a wikitable header (!!) or data (||) row, else None."""
        s = line.strip()
        if s.startswith("!"):
            return s.count("!!") + 1
        if s.startswith("|") and not s.startswith(("|-", "|+", "|}")):
            return s.count("||") + 1
        return None

    def test_tables_well_formed_across_conditional_combinations(self):
        """Regression guard for the {% endif +%} whitespace control: row
        separators must never weld onto a content line, and every header/data/
        totals row in a table must have the same cell count — for keeper and
        outfielder, with and without own goals (all four column layouts)."""
        from wiki_import.import_players import _build_player_page

        keeper_comp = self._keeper_comp("GK1")
        of_stats = [{"player_id": "OF1", "season": "2024", "appearances": 25, "goals": 7,
                     "assists": 4, "yellow_cards": 2, "second_yellow_cards": 0, "red_cards": 0,
                     "minutes_played": 2100, "clean_sheets": None, "goals_conceded": None,
                     "own_goals": 0, "subs_on": 1, "subs_off": 5, "ppg": 2.0}]
        of_comp = [{"player_id": "OF1", "season": "2024", "competition": "ליגת העל",
                    "appearances": 25, "goals": 7, "assists": 4, "yellow_cards": 2,
                    "second_yellow_cards": 0, "red_cards": 0, "own_goals": 0,
                    "clean_sheets": None, "goals_conceded": None}]
        of_stats_og = [dict(of_stats[0], own_goals=2)]
        of_comp_og = [dict(of_comp[0], own_goals=2)]

        cases = {
            "keeper-with-og": (self.KEEPER, self._keeper_stats("GK1"), keeper_comp),
            "outfield-no-og": (self.OUTFIELD, of_stats, of_comp),
            "outfield-with-og": (self.OUTFIELD, of_stats_og, of_comp_og),
        }
        for label, (player, stats, comp) in cases.items():
            page = _build_player_page(player, [], [], stats, comp)
            # No welded separators/terminators anywhere in the tables.
            for line in page.splitlines():
                stripped = line.strip()
                if "|-" in stripped and stripped != "|-":
                    raise AssertionError(f"[{label}] welded row separator: {line!r}")
            # Each wikitable: all rows share the header's cell count.
            for table in self._wikitables(page):
                counts = [c for c in (self._cell_count(ln) for ln in table) if c is not None]
                assert counts, f"[{label}] empty table"
                assert len(set(counts)) == 1, (
                    f"[{label}] ragged table — cell counts {counts}"
                )


class TestMatchReportRendering:
    def test_render_match_report(self):
        from wiki_import.import_matches import import_matches

        summary = import_matches(
            matches_path=FIXTURES_DIR / "matches_sample.json",
            dry_run=True,
        )
        assert summary["created"] == 2
        assert summary["failed"] == 0

    def test_match_page_title(self):
        from wiki_import.import_matches import _match_page_title

        match = {
            "date": "2024-08-24",
            "opponent": "Maccabi Tel Aviv",
            "competition": "Israeli Premier League",
        }
        title = _match_page_title(match)
        # Yellow-triage fix (2026-06-13): `_match_page_title` now routes the
        # date through `to_il_date` so titles render as `DD/MM/YYYY` not
        # raw ISO. Pre-fix this test asserted the raw "2024-08-24" string.
        assert "24/08/2024" in title
        assert "Maccabi Tel Aviv" in title
        assert "Israeli Premier League" in title


class TestCargoTemplates:
    def test_dry_run_cargo_templates(self):
        from wiki_import.import_templates import import_cargo_templates

        summary = import_cargo_templates(dry_run=True)
        # Phase 3a R2: Player, Transfer, MarketValue, Match, PlayerStats, Coach,
        # Honour, SeasonStanding, HeadToHead.
        # keeper-stats branch: + PlayerCompetitionStats (10th table).
        assert summary["created"] == 10
        assert summary["failed"] == 0

    def test_cargo_template_content(self):
        from wiki_import.import_templates import _build_cargo_template

        content = _build_cargo_template("players", {"name": "String", "age": "Integer"})
        assert "#cargo_declare" in content
        assert "#cargo_store" in content
        assert "_table=players" in content
        assert "name=String" in content


class TestSquadPage:
    def test_dry_run_squad_page(self, normalized_data):
        from wiki_import.import_templates import import_squad_page

        summary = import_squad_page(
            season="2024",
            players_path=normalized_data / "players.jsonl",
            dry_run=True,
        )
        assert summary["created"] == 1
        assert summary["failed"] == 0


class TestTransferPage:
    def test_dry_run_transfer_page(self, normalized_data):
        from wiki_import.import_templates import import_transfer_page

        summary = import_transfer_page(
            season="2024",
            players_path=normalized_data / "players.jsonl",
            transfers_path=normalized_data / "transfers.jsonl",
            dry_run=True,
        )
        # May be 0 or 1 depending on whether fixture transfers match HBS keywords
        assert summary["failed"] == 0


class TestSeasonDisplayIntegration:
    """Phase 3a R2: page titles + h1 headings + categories should all render
    in slash form (YYYY/YY), even though the data files / Cargo column / spider
    CLI all keep the bare integer start-year as the join key.

    These tests pin the human-visible surface so a future inline format swap
    can't silently revert one site without the others noticing.
    """

    def test_squad_page_renders_slash_format(self, tmp_path):
        from wiki_import.import_templates import _render_template
        # Minimal Jinja inputs: empty players list per group.
        content = _render_template(
            "squad_table.j2",
            season="2024",
            season_display="2024/25",
            players=[],
            players_by_position={"GK": [], "DF": [], "MF": [], "FW": [], "OTHER": []},
        )
        assert "== סגל 2024/25 ==" in content
        assert "[[קטגוריה:עונת 2024/25]]" in content
        # The bare integer must NOT appear as a season label anywhere.
        assert "== סגל 2024 ==" not in content
        assert "[[קטגוריה:עונת 2024]]" not in content

    def test_transfer_page_renders_slash_format(self):
        from wiki_import.import_templates import _render_template
        content = _render_template(
            "transfer_table.j2",
            season="2024",
            season_display="2024/25",
            incoming=[],
            outgoing=[],
        )
        assert "== העברות 2024/25 ==" in content
        assert "[[קטגוריה:עונת 2024/25]]" in content

    def test_season_overview_cross_links_use_slash_format(self):
        from wiki_import.import_templates import _render_template
        content = _render_template(
            "season_overview.j2",
            season="2024",
            season_display="2024/25",
            stats=[{"season": "2024", "appearances": 1, "goals": 0,
                    "assists": 0, "yellow_cards": 0, "red_cards": 0,
                    "minutes_played": 90, "player_name": "Test"}],
            total_appearances=1, total_goals=0, total_assists=0,
            total_yellows=0, total_reds=0,
            top_scorers=[], top_appearances=[], top_assists=[],
            fixtures_by_competition={},
            # Phase 3a R2: presence flags so the template renders cross-links
            # instead of the placeholder banner.
            standings=None,
            has_squad_page=True,
            has_transfers_page=True,
            missing_notes=[],
        )
        # The cross-links to the squad + transfers + competition pages must
        # also be in slash form so they actually resolve to the renamed
        # page titles.
        assert "[[סגל 2024/25]]" in content
        assert "[[העברות 2024/25]]" in content
        assert "[[קטגוריה:עונת 2024/25]]" in content

    def test_season_overview_placeholder_when_no_data(self):
        """Phase 3a R2: sparse historical seasons (1949 → ~1974) render an
        explicit placeholder banner + a hand-curate prompt. The banner
        signals "no info available" without leaving the page empty."""
        from wiki_import.import_templates import _render_template
        content = _render_template(
            "season_overview.j2",
            season="1965",
            season_display="1965/66",
            stats=[],
            total_appearances=0, total_goals=0, total_assists=0,
            total_yellows=0, total_reds=0,
            top_scorers=[], top_appearances=[], top_assists=[],
            fixtures_by_competition={},
            standings=None,
            has_squad_page=False,
            has_transfers_page=False,
            missing_notes=[],  # not used when has_any is False
        )
        assert "אין מידע זמין על עונה זו" in content
        assert "Transfermarkt לא מספק מידע על עונה 1965/66" in content
        assert "[[קטגוריה:עונות ללא מידע]]" in content
        # Cross-link sub-pages should announce missing data, not stale links.
        assert "''אין מידע על הסגל לעונה זו''" in content
        assert "''אין מידע על העברות לעונה זו''" in content

    def test_season_overview_partial_with_missing_notes(self):
        """Phase 3a R2: a season with some data + some missing renders the
        "what's missing" footer so reviewers know sparseness is by design."""
        from wiki_import.import_templates import _render_template
        standings = {
            "season": "1990", "competition": "Liga Leumit",
            "tier": 1, "final_position": 6,
            "wins": 11, "draws": 11, "losses": 8,
            "goals_for": 20, "goals_against": 19, "points": 33,
            "manager_name": "Vicky Peretz", "manager_id": "12345",
        }
        content = _render_template(
            "season_overview.j2",
            season="1990",
            season_display="1990/91",
            stats=[], total_appearances=0, total_goals=0, total_assists=0,
            total_yellows=0, total_reds=0,
            top_scorers=[], top_appearances=[], top_assists=[],
            fixtures_by_competition={},
            standings=standings,
            has_squad_page=True,
            has_transfers_page=False,
            missing_notes=[
                "Transfermarkt לא מספק סטטיסטיקות שחקנים לעונה זו (`leistungsdaten` מתחיל בעיקר משנת 1985/86).",
            ],
        )
        # Has data (standings) + missing notes → renders standings + footer.
        assert "מקום בטבלה" in content
        assert "Liga Leumit" in content
        assert "[[Vicky Peretz]]" in content
        # The empty-placeholder banner must NOT appear when we have ANY data.
        assert "אין מידע זמין על עונה זו" not in content
        # The "what's missing" footer appears + lists the note.
        assert "מה חסר" in content
        assert "סטטיסטיקות שחקנים" in content
        # Squad page link exists; transfers announces missing.
        assert "[[סגל 1990/91]]" in content
        assert "''אין מידע על העברות לעונה זו''" in content
        # Partial-data category, not no-data category.
        assert "[[קטגוריה:עונות חלקיות]]" in content
        assert "[[קטגוריה:עונות ללא מידע]]" not in content

    def test_season_overview_full_data_has_no_banners(self):
        """A modern season with full data shows no missing-data banners
        anywhere — pure content + standard cross-links."""
        from wiki_import.import_templates import _render_template
        standings = {
            "season": "2024", "competition": "Ligat ha'Al",
            "tier": 1, "final_position": 1,
            "wins": 18, "draws": 6, "losses": 2,
            "goals_for": 52, "goals_against": 18, "points": 58,
            "manager_name": "Ben Binyamin", "manager_id": "140166",
        }
        content = _render_template(
            "season_overview.j2",
            season="2024",
            season_display="2024/25",
            stats=[{"season": "2024", "appearances": 30, "goals": 8,
                    "assists": 3, "yellow_cards": 2, "red_cards": 0,
                    "minutes_played": 2700, "player_name": "Test"}],
            total_appearances=30, total_goals=8, total_assists=3,
            total_yellows=2, total_reds=0,
            top_scorers=[], top_appearances=[], top_assists=[],
            fixtures_by_competition={},
            standings=standings,
            has_squad_page=True,
            has_transfers_page=True,
            missing_notes=[],
        )
        assert "אין מידע זמין על עונה זו" not in content
        assert "מה חסר" not in content
        assert "אין מידע על הסגל" not in content
        assert "אין מידע על העברות" not in content
        assert "[[קטגוריה:עונות ללא מידע]]" not in content
        assert "[[קטגוריה:עונות חלקיות]]" not in content

    def test_player_page_stats_table_uses_slash_format(self):
        """The per-row season label on the player page's stats table goes
        through the `season_display` Jinja filter so multi-season tables
        render every row in slash form.
        """
        from wiki_import.import_players import _render_template
        content = _render_template(
            "player_page.j2",
            player={
                "name_english": "Test", "name_hebrew": None,
                "id": "1", "birth_date": None, "birth_place": None,
                "nationality": None, "main_position": None,
                "current_jersey_number": None, "current_squad": False,
                "homegrown": False, "retired": False,
            },
            transfers=[],
            market_values=[],
            stats=[
                {"season": "2015", "appearances": 30, "goals": 8, "assists": 3,
                 "yellow_cards": 2, "second_yellow_cards": 0, "red_cards": 0,
                 "minutes_played": 2700},
                {"season": "2024", "appearances": 25, "goals": 5, "assists": 4,
                 "yellow_cards": 4, "second_yellow_cards": 1, "red_cards": 0,
                 "minutes_played": 2100},
            ],
        )
        # Both seasons get slash-form rendering in the row label.
        assert "[[עונת 2015/16|2015/16]]" in content
        assert "[[עונת 2024/25|2024/25]]" in content


class TestMediaWikiTemplates:
    def test_all_template_files_exist(self):
        from wiki_import.import_templates import MEDIAWIKI_TEMPLATES, MEDIAWIKI_TEMPLATE_DIR

        for title, filename in MEDIAWIKI_TEMPLATES.items():
            filepath = MEDIAWIKI_TEMPLATE_DIR / filename
            assert filepath.exists(), f"Template file missing for {title}: {filepath}"

    def test_template_file_contents(self):
        from wiki_import.import_templates import MEDIAWIKI_TEMPLATE_DIR

        tooltip = (MEDIAWIKI_TEMPLATE_DIR / "Tooltip.wikitext").read_text(encoding="utf-8")
        assert "<includeonly>" in tooltip
        assert "border-bottom" in tooltip

        player_infobox = (MEDIAWIKI_TEMPLATE_DIR / "Player_infobox.wikitext").read_text(encoding="utf-8")
        assert "infobox" in player_infobox
        assert "#cc0000" in player_infobox
        assert "עמדה" in player_infobox

        match_infobox = (MEDIAWIKI_TEMPLATE_DIR / "Match_infobox.wikitext").read_text(encoding="utf-8")
        assert "infobox" in match_infobox
        assert "מסגרת" in match_infobox

        stadium_infobox = (MEDIAWIKI_TEMPLATE_DIR / "Stadium_infobox.wikitext").read_text(encoding="utf-8")
        assert "infobox" in stadium_infobox
        assert "קיבולת" in stadium_infobox

    def test_dry_run_mediawiki_templates(self):
        from wiki_import.import_templates import import_mediawiki_templates

        summary = import_mediawiki_templates(dry_run=True)
        assert summary["created"] == 4  # Tooltip, Player, Match, Stadium
        assert summary["failed"] == 0

    def test_mediawiki_templates_dict_has_correct_keys(self):
        from wiki_import.import_templates import MEDIAWIKI_TEMPLATES

        expected_titles = {
            "Template:Tooltip",
            "Template:Player infobox",
            "Template:Match infobox",
            "Template:Stadium infobox",
        }
        assert set(MEDIAWIKI_TEMPLATES.keys()) == expected_titles


class TestPositionGrouping:
    def test_group_players_by_position(self):
        from wiki_import.import_templates import _group_players_by_position

        players = [
            {"main_position": "Goalkeeper", "name_english": "GK1"},
            {"main_position": "Centre-Back", "name_english": "CB1"},
            {"main_position": "Left-Back", "name_english": "LB1"},
            {"main_position": "Central Midfield", "name_english": "CM1"},
            {"main_position": "Centre-Forward", "name_english": "CF1"},
            {"main_position": "", "name_english": "Unknown1"},
        ]
        groups = _group_players_by_position(players)
        assert len(groups["GK"]) == 1
        assert len(groups["DF"]) == 2
        assert len(groups["MF"]) == 1
        assert len(groups["FW"]) == 1
        assert len(groups["OTHER"]) == 1
