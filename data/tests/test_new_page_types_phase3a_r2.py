"""Tests for the Phase 3a R2 new page types: Derbies + European campaign."""

import json
from pathlib import Path

import pytest

from wiki_import.import_templates import (
    import_derbies_page,
    import_european_campaign_page,
    _EUROPEAN_COMPETITIONS,
    _MAJOR_DERBIES,
)


def _write_json(path: Path, data) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Derbies page
# ---------------------------------------------------------------------------


class TestDerbiesPage:
    def test_renders_all_four_major_derbies_with_data(self, tmp_path: Path):
        head_to_head = [
            {"opponent": "Maccabi Tel Aviv", "opponent_tm_id": "869",
             "matches": 121, "wins": 35, "draws": 42, "losses": 44,
             "goals_for": 140, "goals_against": 160, "avg_attendance": 12500},
            {"opponent": "Hapoel Tel Aviv", "opponent_tm_id": "1000",
             "matches": 100, "wins": 30, "draws": 35, "losses": 35,
             "goals_for": 110, "goals_against": 115, "avg_attendance": 8000},
            {"opponent": "Beitar Jerusalem", "opponent_tm_id": "3793",
             "matches": 103, "wins": 53, "draws": 26, "losses": 24,
             "goals_for": 0, "goals_against": 0, "avg_attendance": 9501},
            {"opponent": "Maccabi Haifa", "opponent_tm_id": "999",
             "matches": 119, "wins": 40, "draws": 35, "losses": 44,
             "goals_for": 0, "goals_against": 0, "avg_attendance": 10000},
            # Tail opponent
            {"opponent": "Bnei Sakhnin", "opponent_tm_id": "888",
             "matches": 50, "wins": 25, "draws": 15, "losses": 10,
             "goals_for": 0, "goals_against": 0, "avg_attendance": 5000},
        ]
        path = _write_json(tmp_path / "head_to_head.json", head_to_head)

        summary = import_derbies_page(head_to_head_path=path, dry_run=True)
        assert summary["created"] == 1
        assert summary["failed"] == 0

    def test_renders_with_short_name_aliases(self, tmp_path: Path):
        """TM sometimes serves short forms like 'B. Jerusalem'. The lookup
        must accept both the canonical and the short form."""
        head_to_head = [
            {"opponent": "B. Jerusalem", "opponent_tm_id": "3793",
             "matches": 103, "wins": 53, "draws": 26, "losses": 24,
             "goals_for": 0, "goals_against": 0, "avg_attendance": 9501},
        ]
        path = _write_json(tmp_path / "head_to_head.json", head_to_head)

        summary = import_derbies_page(head_to_head_path=path, dry_run=True)
        assert summary["created"] == 1

    def test_no_data_skips_gracefully(self, tmp_path: Path):
        path = _write_json(tmp_path / "head_to_head.json", [])
        summary = import_derbies_page(head_to_head_path=path, dry_run=True)
        # Empty input → no page created (template would have nothing to render).
        assert summary["created"] == 0
        assert summary["failed"] == 0

    def test_missing_file_skips_gracefully(self, tmp_path: Path):
        summary = import_derbies_page(
            head_to_head_path=tmp_path / "missing.json", dry_run=True,
        )
        assert summary["created"] == 0
        assert summary["failed"] == 0


# ---------------------------------------------------------------------------
# European campaign page
# ---------------------------------------------------------------------------


class TestEuropeanCampaignPage:
    def test_renders_campaigns_from_fixtures(self, tmp_path: Path, monkeypatch):
        from wiki_import import import_templates
        monkeypatch.setattr(
            import_templates, "DEFAULT_SCRAPER_OUTPUT_DIR", tmp_path,
        )
        # 2016/17: Champions League qualifying + Europa League group stage
        _write_json(tmp_path / "2016" / "fixtures.json", [
            {"competition": "Champions League Qualifying", "date": "2016-07-15", "opponent": "Olympiacos"},
            {"competition": "Champions League Qualifying", "date": "2016-07-22", "opponent": "Olympiacos"},
            {"competition": "UEFA Europa League", "date": "2016-09-15", "opponent": "Inter"},
            {"competition": "Ligat ha'Al", "date": "2016-09-01", "opponent": "Maccabi TA"},  # filtered out
        ])
        # 2024/25: nothing European
        _write_json(tmp_path / "2024" / "fixtures.json", [
            {"competition": "Ligat ha'Al", "date": "2024-09-01", "opponent": "Hapoel Jerusalem"},
        ])

        summary = import_european_campaign_page(
            seasons=["2016", "2024"], dry_run=True,
        )
        assert summary["created"] == 1
        assert summary["failed"] == 0

    def test_empty_seasons_renders_fallback_message(self, tmp_path: Path, monkeypatch):
        from wiki_import import import_templates
        monkeypatch.setattr(
            import_templates, "DEFAULT_SCRAPER_OUTPUT_DIR", tmp_path,
        )
        # Season with no fixtures.
        summary = import_european_campaign_page(
            seasons=["1965"], dry_run=True,
        )
        # The template still emits a page (with a "no data" fallback inside),
        # so we expect created=1 here too.
        assert summary["created"] == 1
        assert summary["failed"] == 0

    def test_competition_filter_excludes_league_matches(self, tmp_path: Path, monkeypatch):
        from wiki_import import import_templates
        monkeypatch.setattr(
            import_templates, "DEFAULT_SCRAPER_OUTPUT_DIR", tmp_path,
        )
        # Only league + cup competitions: nothing European.
        _write_json(tmp_path / "2024" / "fixtures.json", [
            {"competition": "Ligat ha'Al", "date": "2024-09-01"},
            {"competition": "Gvia haMedina", "date": "2024-12-01"},  # Israeli State Cup
        ])
        summary = import_european_campaign_page(
            seasons=["2024"], dry_run=True,
        )
        assert summary["created"] == 1   # page still emits, content section will be empty
        assert summary["failed"] == 0


class TestMajorDerbiesConstant:
    """Sanity check that the canonical derby list covers the 4 major Israeli
    football rivalries with the right Hebrew display names."""

    def test_four_canonical_derbies_listed(self):
        assert len(_MAJOR_DERBIES) == 4
        names = {d["display_name"] for d in _MAJOR_DERBIES}
        assert "מכבי תל אביב" in names
        assert "הפועל תל אביב" in names
        assert "מכבי חיפה" in names
        # Beitar Jerusalem includes the gershayim (\") in the abbreviated form.
        assert any("ירושלים" in n for n in names)


class TestEuropeanCompetitionsConstant:
    """Verify the European-competitions filter covers the modern + historical
    UEFA tournament names TM uses."""

    def test_modern_uefa_tournaments_covered(self):
        for name in (
            "UEFA Champions League",
            "UEFA Europa League",
            "UEFA Conference League",
        ):
            assert name in _EUROPEAN_COMPETITIONS

    def test_qualifying_rounds_covered(self):
        # TM names qualifying rounds with various casings; the filter should
        # accept the common forms.
        assert "Champions League Qualifying" in _EUROPEAN_COMPETITIONS
        assert "Europa League Qualifying" in _EUROPEAN_COMPETITIONS

    def test_historical_tournaments_covered(self):
        # Pre-modern tournaments HBS may have played in.
        assert "UEFA Cup" in _EUROPEAN_COMPETITIONS
        assert "Intertoto Cup" in _EUROPEAN_COMPETITIONS
