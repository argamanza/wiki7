"""Tests for the coach trophies-won derivation (Phase 3a R2)."""

import json
from pathlib import Path

from data_pipeline.derive_coach_trophies import (
    _expand_honours_to_per_season,
    _season_label_to_yyyy,
    derive,
    write_enriched,
)


class TestSeasonLabelConversion:
    def test_two_digit_modern(self):
        assert _season_label_to_yyyy("24/25") == "2024"
        assert _season_label_to_yyyy("00/01") == "2000"

    def test_two_digit_historical(self):
        assert _season_label_to_yyyy("75/76") == "1975"
        assert _season_label_to_yyyy("99/00") == "1999"

    def test_four_digit_passthrough(self):
        assert _season_label_to_yyyy("1996/97") == "1996"

    def test_malformed_returns_none(self):
        assert _season_label_to_yyyy("") is None
        assert _season_label_to_yyyy("nonsense") is None
        assert _season_label_to_yyyy("abc/def") is None


class TestHonoursPivot:
    def test_pivot_to_per_season_keyed(self):
        honours = [
            {"competition": "Israeli Champion", "achievement": "Winner",
             "seasons": ["24/25", "17/18"]},
            {"competition": "Israeli Cup", "achievement": "Winner",
             "seasons": ["19/20"]},
        ]
        out = _expand_honours_to_per_season(honours)
        assert out["2024"] == ["Israeli Champion 2024/25"]
        assert out["2017"] == ["Israeli Champion 2017/18"]
        assert out["2019"] == ["Israeli Cup 2019/20"]


class TestDeriveEndToEnd:
    """End-to-end: write 3 input fixtures, run derive(), assert the
    enriched coaches list groups by coach_id and includes trophies-won.
    """

    def test_barak_bakhar_wins_three_titles_in_a_row(self, tmp_path: Path):
        # Honours: HBS won 3 league titles in a row (real history: 15/16, 16/17, 17/18).
        honours = [
            {"competition": "Israeli Champion", "achievement": "Winner",
             "seasons": ["24/25", "17/18", "16/17", "15/16", "75/76", "74/75"]},
            {"competition": "Israeli Super Cup", "achievement": "Winner",
             "seasons": ["25/26", "22/23", "17/18", "16/17", "75/76"]},
        ]
        # Season standings: Bakhar was manager 15/16, 16/17, 17/18.
        # Then Yossi Abukasis took over for 19/20.
        standings = [
            {"season": "2015", "manager_id": "26280", "manager_name": "Barak Bakhar",
             "wins": 20, "draws": 4, "losses": 2},
            {"season": "2016", "manager_id": "26280", "manager_name": "Barak Bakhar",
             "wins": 18, "draws": 5, "losses": 3},
            {"season": "2017", "manager_id": "26280", "manager_name": "Barak Bakhar",
             "wins": 17, "draws": 6, "losses": 3},
            {"season": "2019", "manager_id": "19614", "manager_name": "Yossi Abukasis",
             "wins": 13, "draws": 5, "losses": 8},
        ]
        coaches_current = [
            # Yossi Abukasis is no longer current — only Bakhar's successor
            # of-the-moment lives here. For this test, leave it empty.
        ]

        honours_path = tmp_path / "honours.json"
        standings_path = tmp_path / "season_standings.json"
        coaches_path = tmp_path / "coaches.json"
        honours_path.write_text(json.dumps(honours, ensure_ascii=False))
        standings_path.write_text(json.dumps(standings, ensure_ascii=False))
        coaches_path.write_text(json.dumps(coaches_current, ensure_ascii=False))

        result = derive(honours_path, standings_path, coaches_path)
        # Result indexed by name for easy assertion.
        by_name = {c["name"]: c for c in result}

        bakhar = by_name["Barak Bakhar"]
        assert bakhar["id"] == "26280"
        assert bakhar["tenure_seasons"] == ["2015", "2016", "2017"]
        # 3 league titles + 2 super cups (16/17, 17/18) during his tenure.
        assert "Israeli Champion 2015/16" in bakhar["hbs_trophies_won"]
        assert "Israeli Champion 2016/17" in bakhar["hbs_trophies_won"]
        assert "Israeli Champion 2017/18" in bakhar["hbs_trophies_won"]
        assert "Israeli Super Cup 2016/17" in bakhar["hbs_trophies_won"]
        assert "Israeli Super Cup 2017/18" in bakhar["hbs_trophies_won"]
        # Not in his tenure: 2018/19 super cup (he wasn't manager).
        assert "Israeli Super Cup 2025/26" not in bakhar["hbs_trophies_won"]
        # Match aggregation: should be 55W + 15D + 8L = 78 matches across 3 seasons.
        assert bakhar["played"] == 78
        assert bakhar["wins"] == 55

        # Abukasis: manager 2019/20 only, no honours that season.
        abukasis = by_name["Yossi Abukasis"]
        assert abukasis["tenure_seasons"] == ["2019"]
        assert abukasis["hbs_trophies_won"] == []

    def test_current_staff_layered_on_top(self, tmp_path: Path):
        honours = []
        standings = [
            {"season": "2024", "manager_id": "96723", "manager_name": "Ran Kozuch",
             "wins": 18, "draws": 6, "losses": 2},
        ]
        coaches_current = [
            {"id": "96723", "name": "Ran Kozuch", "role": "Manager",
             "tenure_start": "01/07/2024", "tenure_end": "30.06.2028",
             "matches": 0, "wins": 0, "draws": 0, "losses": 0, "ppm": ""},
            {"id": "10000", "name": "Ben Binyamin", "role": "Assistant Manager",
             "tenure_start": "01/07/2024", "tenure_end": "",
             "matches": 0, "wins": 0, "draws": 0, "losses": 0, "ppm": ""},
        ]

        honours_path = tmp_path / "honours.json"
        standings_path = tmp_path / "season_standings.json"
        coaches_path = tmp_path / "coaches.json"
        honours_path.write_text(json.dumps(honours, ensure_ascii=False))
        standings_path.write_text(json.dumps(standings, ensure_ascii=False))
        coaches_path.write_text(json.dumps(coaches_current, ensure_ascii=False))

        result = derive(honours_path, standings_path, coaches_path)
        by_name = {c["name"]: c for c in result}

        kozuch = by_name["Ran Kozuch"]
        # The current-manager appointment dates from coaches.json are layered on
        # top of the platzierungen-derived row.
        assert kozuch["tenure_start"] == "01/07/2024"
        assert kozuch["tenure_end"] == "30.06.2028"
        # And the historical tenure_seasons stays from platzierungen.
        assert "2024" in kozuch["tenure_seasons"]

        # Non-manager current staff carry through.
        binyamin = by_name["Ben Binyamin"]
        assert binyamin["role"] == "Assistant Manager"
        assert binyamin["tenure_seasons"] == []
        assert binyamin["hbs_trophies_won"] == []

    def test_write_enriched_produces_loadable_json(self, tmp_path: Path):
        (tmp_path / "honours.json").write_text("[]")
        (tmp_path / "season_standings.json").write_text("[]")
        (tmp_path / "coaches.json").write_text("[]")

        out = write_enriched(tmp_path)
        assert out.exists()
        # Round-trips as valid JSON.
        data = json.loads(out.read_text())
        assert data == []
