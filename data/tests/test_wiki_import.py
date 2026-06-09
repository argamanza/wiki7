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
        assert "2024-08-24" in title
        assert "Maccabi Tel Aviv" in title
        assert "Israeli Premier League" in title


class TestCargoTemplates:
    def test_dry_run_cargo_templates(self):
        from wiki_import.import_templates import import_cargo_templates

        summary = import_cargo_templates(dry_run=True)
        # Phase 3a R2: Player, Transfer, MarketValue, Match, PlayerStats, Coach,
        # Honour, SeasonStanding, HeadToHead.
        assert summary["created"] == 9
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
        from wiki_import.import_templates import MEDIAWIKI_TEMPLATES, MEDIAWIKI_TEMPLATE_DIR

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
