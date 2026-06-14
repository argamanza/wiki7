"""B.2 contract test (reviewer-pass constraint #2, 2026-06-13).

Section ids are a PERMANENT CONTRACT. A rename orphans the old section
across every existing page AND appends a duplicate at the new id. The
fix has to be a schema-migration-style cleanup, not a silent template
update.

This test pins the section ids each bot-managed template renders:
  - Renders the template with stub data that exercises every
    conditional branch.
  - Extracts the section ids from the rendered output.
  - Asserts the set is a subset of `KNOWN_TEMPLATE_SECTIONS[template]`.

Any drift (template adds a new id without updating
`KNOWN_TEMPLATE_SECTIONS`, or emits a typo'd id that bypassed
`validate_section_id`) surfaces here at CI time, not in production.
"""

from wiki_import.wikitext_merger import (
    KNOWN_TEMPLATE_SECTIONS,
    extract_managed_sections,
)


def _stub_player_with_all_sections() -> dict:
    """Stub player whose rendered page exercises every conditional
    section (infobox, youth-career, career, stats, market-values,
    categories)."""
    return {
        "id": "TEST_ID",
        "name_english": "Test Player",
        "name_hebrew": "טסט פלייר",
        "birth_date": "2000-01-01",
        "birth_place": "Tel Aviv",
        "nationality": ["Israel"],
        "main_position": "Forward",
        "other_positions": [],
        "current_jersey_number": 9,
        "current_squad": True,
        "homegrown": False,
        "retired": False,
        "is_captain": False,
        "height_cm": 180,
        "preferred_foot": "right",
        "contract_expires": "2026-06-30",
        "current_market_value": "€500k",
    }


def _stub_transfers(player_id: str, include_youth: bool) -> list[dict]:
    """A senior transfer always; a youth transfer optionally."""
    out = [
        {
            "player_id": player_id,
            "season": "2024",
            "transfer_date": "2024-08-15",
            "from_club": "Old Club",
            "to_club": "Hapoel Beer Sheva",
            "fee": "€500k",
            "loan": False,
            "from_club_tm_id": "10",
            "to_club_tm_id": "2976",
        },
    ]
    if include_youth:
        out.append({
            "player_id": player_id,
            "season": "2020",
            "transfer_date": "2020-09-01",
            "from_club": "Some Academy",
            "to_club": "Hapoel Beer Sheva U19",
            "fee": "Free",
            "loan": False,
        })
    return out


def _stub_market_values(player_id: str) -> list[dict]:
    return [
        {
            "player_id": player_id,
            "value_date": "2024-01-01",
            "value": "€500k",
            "team": "Hapoel Beer Sheva",
        },
    ]


def _stub_stats(player_id: str) -> list[dict]:
    return [
        {
            "player_id": player_id,
            "season": "2024",
            "appearances": 20,
            "goals": 5,
            "assists": 3,
            "yellow_cards": 2,
            "second_yellow_cards": 0,
            "red_cards": 0,
            "minutes_played": 1800,
            "clean_sheets": None,
            "goals_conceded": None,
            "own_goals": 1,
            "subs_on": 2,
            "subs_off": 3,
            "ppg": 1.75,
        },
    ]


def _stub_competition_stats(player_id: str) -> list[dict]:
    return [
        {
            "player_id": player_id, "season": "2024", "competition": "ליגת העל",
            "appearances": 18, "goals": 5, "assists": 3, "yellow_cards": 2,
            "second_yellow_cards": 0, "red_cards": 0, "own_goals": 1,
            "clean_sheets": None, "goals_conceded": None,
        },
        {
            "player_id": player_id, "season": "2024", "competition": "גביע המדינה בכדורגל",
            "appearances": 2, "goals": 0, "assists": 0, "yellow_cards": 0,
            "second_yellow_cards": 0, "red_cards": 0, "own_goals": 0,
            "clean_sheets": None, "goals_conceded": None,
        },
    ]


class TestPlayerPageSectionContract:
    """player_page.j2 contract — every section id it renders must appear
    in KNOWN_TEMPLATE_SECTIONS['player_page.j2']. The reviewer review
    flagged that drift here is a schema migration, not an oops."""

    def test_full_render_emits_known_ids_only(self):
        """All sections present (player has youth + senior + stats + MVs)."""
        from wiki_import.import_players import _build_player_page

        player = _stub_player_with_all_sections()
        transfers = _stub_transfers(player["id"], include_youth=True)
        market_values = _stub_market_values(player["id"])
        stats = _stub_stats(player["id"])
        competition_stats = _stub_competition_stats(player["id"])

        page = _build_player_page(player, transfers, market_values, stats, competition_stats)
        rendered_ids = set(extract_managed_sections(page).keys())

        known = KNOWN_TEMPLATE_SECTIONS["player_page.j2"]
        # Every rendered id MUST be a known id (subset). Drift = test fails.
        unknown = rendered_ids - known
        assert not unknown, (
            f"player_page.j2 rendered unknown section id(s): {unknown}. "
            "Either fix the template, or update "
            "KNOWN_TEMPLATE_SECTIONS['player_page.j2'] AND ship a "
            "MovePage-style cleanup for the rename "
            "(section ids are a permanent contract)."
        )
        # Sanity: at least the always-rendered sections are present.
        assert "infobox" in rendered_ids
        assert "categories" in rendered_ids
        # keeper-stats branch: the per-competition section renders when present.
        assert "competition-stats" in rendered_ids

    def test_minimal_render_subset_of_known(self):
        """Player with NO youth career, NO stats, NO MVs. The remaining
        sections still all have to be in the known set."""
        from wiki_import.import_players import _build_player_page

        player = _stub_player_with_all_sections()
        # Only senior transfer; no youth, no stats, no MVs.
        transfers = _stub_transfers(player["id"], include_youth=False)
        market_values = []
        stats = []

        page = _build_player_page(player, transfers, market_values, stats)
        rendered_ids = set(extract_managed_sections(page).keys())

        known = KNOWN_TEMPLATE_SECTIONS["player_page.j2"]
        unknown = rendered_ids - known
        assert not unknown, (
            f"Minimal render produced unknown ids: {unknown}"
        )

    def test_full_render_does_not_log_marker_warnings(self, caplog):
        """A clean template render must NOT trigger ANY wikitext_merger
        warnings — those signal orphan/inverted/duplicate markers. The
        test that surfaced this during development: a stats-conditional
        end marker placed OUTSIDE its `{% endif %}` block (bug now
        fixed), which logged 'END marker for section stats with no
        matching START' on every minimal render."""
        from wiki_import.import_players import _build_player_page

        player = _stub_player_with_all_sections()
        transfers = _stub_transfers(player["id"], include_youth=True)
        market_values = _stub_market_values(player["id"])
        stats = _stub_stats(player["id"])

        with caplog.at_level("WARNING", logger="wiki_import.wikitext_merger"):
            page = _build_player_page(player, transfers, market_values, stats)
            extract_managed_sections(page)  # trigger any warnings
        merger_warnings = [
            r for r in caplog.records if r.name == "wiki_import.wikitext_merger"
        ]
        assert not merger_warnings, (
            f"player_page.j2 render produced merger warnings: "
            f"{[r.message for r in merger_warnings]}"
        )

    def test_minimal_render_does_not_log_marker_warnings(self, caplog):
        """Same warning-free invariant on the minimal-data path. This
        is the regression test for the specific bug surfaced during
        B.2 development: a `{% if stats %}` conditional with the end
        marker placed AFTER `{% endif %}` left an orphan end on
        every page that had no stats."""
        from wiki_import.import_players import _build_player_page

        player = _stub_player_with_all_sections()
        transfers = _stub_transfers(player["id"], include_youth=False)

        with caplog.at_level("WARNING", logger="wiki_import.wikitext_merger"):
            page = _build_player_page(player, transfers, [], [])
            extract_managed_sections(page)
        merger_warnings = [
            r for r in caplog.records if r.name == "wiki_import.wikitext_merger"
        ]
        assert not merger_warnings, (
            f"minimal player_page.j2 render produced merger warnings: "
            f"{[r.message for r in merger_warnings]}"
        )

    def test_all_known_ids_are_valid_under_validate_section_id(self):
        """Every id we register MUST pass the syntax validator. Belt-
        and-suspenders: if someone adds a typo'd id to
        KNOWN_TEMPLATE_SECTIONS, it surfaces here, not at first render."""
        from wiki_import.wikitext_merger import validate_section_id

        for template_name, ids in KNOWN_TEMPLATE_SECTIONS.items():
            for section_id in ids:
                # Raises ValueError on invalid; passes through on valid.
                assert validate_section_id(section_id) == section_id, (
                    f"{template_name} declares invalid id {section_id!r}"
                )
