"""Tests for data_pipeline.merge_seasons module."""

import json
import tempfile
from pathlib import Path

import pytest

from data_pipeline.merge_seasons import merge_players, merge_appendable, merge_seasons


def _write_jsonl(path: Path, records: list):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


class TestMergePlayers:
    def test_dedup_by_id(self, tmp_path):
        s1 = tmp_path / "2023"
        s2 = tmp_path / "2024"
        _write_jsonl(s1 / "players.jsonl", [
            {"id": "100", "name_english": "Player A", "current_squad": True,
             "current_jersey_number": 7, "birth_date": None, "birth_place": None,
             "nationality": None, "main_position": "Forward", "name_hebrew": None,
             "homegrown": False, "retired": False},
        ])
        _write_jsonl(s2 / "players.jsonl", [
            {"id": "100", "name_english": "Player A", "current_squad": False,
             "current_jersey_number": 9, "birth_date": "2000-01-01", "birth_place": "Tel Aviv",
             "nationality": ["Israel"], "main_position": "Forward", "name_hebrew": "שחקן א",
             "homegrown": True, "retired": False},
        ])

        merged = merge_players([s1, s2])
        assert len(merged) == 1
        p = merged[0]
        # Latest season wins for mutable fields
        assert p["current_squad"] is False
        assert p["current_jersey_number"] == 9
        # Non-None values from latest win
        assert p["birth_date"] == "2000-01-01"
        assert p["name_hebrew"] == "שחקן א"
        # Homegrown uses OR logic
        assert p["homegrown"] is True

    def test_multiple_players(self, tmp_path):
        s1 = tmp_path / "2023"
        _write_jsonl(s1 / "players.jsonl", [
            {"id": "100", "name_english": "A", "current_squad": True,
             "current_jersey_number": 7, "birth_date": None, "birth_place": None,
             "nationality": None, "main_position": None, "name_hebrew": None,
             "homegrown": False, "retired": False},
            {"id": "200", "name_english": "B", "current_squad": True,
             "current_jersey_number": 10, "birth_date": None, "birth_place": None,
             "nationality": None, "main_position": None, "name_hebrew": None,
             "homegrown": False, "retired": False},
        ])

        merged = merge_players([s1])
        assert len(merged) == 2

    def test_seasons_active_tracks_every_appearance(self, tmp_path):
        """§6 high #8 fix (2026-06-12 review): merge_players must stamp
        `seasons_active: [<year>, ...]` on each merged record so the
        per-season squad page renderer can filter to that season's squad.
        Pre-fix the field didn't exist and the squad page rendered the
        entire all-time roster."""
        s_2002 = tmp_path / "2002"
        s_2010 = tmp_path / "2010"
        s_2024 = tmp_path / "2024"
        # Player A in 2002 + 2010 (but not 2024)
        _write_jsonl(s_2002 / "players.jsonl", [
            {"id": "A", "name_english": "A", "current_squad": True,
             "current_jersey_number": 7, "birth_date": None, "birth_place": None,
             "nationality": None, "main_position": None, "name_hebrew": None,
             "homegrown": False, "retired": False},
        ])
        _write_jsonl(s_2010 / "players.jsonl", [
            {"id": "A", "name_english": "A", "current_squad": True,
             "current_jersey_number": 9, "birth_date": None, "birth_place": None,
             "nationality": None, "main_position": None, "name_hebrew": None,
             "homegrown": False, "retired": False},
        ])
        # Player B in 2024 only
        _write_jsonl(s_2024 / "players.jsonl", [
            {"id": "B", "name_english": "B", "current_squad": True,
             "current_jersey_number": 10, "birth_date": None, "birth_place": None,
             "nationality": None, "main_position": None, "name_hebrew": None,
             "homegrown": False, "retired": False},
        ])

        merged = merge_players([s_2002, s_2010, s_2024])
        by_id = {p["id"]: p for p in merged}
        assert by_id["A"]["seasons_active"] == ["2002", "2010"]
        assert by_id["B"]["seasons_active"] == ["2024"]

    def test_seasons_active_single_season_input(self, tmp_path):
        """When the merge sees only one season, every player still gets a
        single-element `seasons_active` — keeps the field shape stable so
        downstream filters don't need a None check."""
        s = tmp_path / "2024"
        _write_jsonl(s / "players.jsonl", [
            {"id": "X", "name_english": "X", "current_squad": True,
             "current_jersey_number": 1, "birth_date": None, "birth_place": None,
             "nationality": None, "main_position": None, "name_hebrew": None,
             "homegrown": False, "retired": False},
        ])
        merged = merge_players([s])
        assert merged[0]["seasons_active"] == ["2024"]


class TestMergeAppendable:
    def test_concatenate_and_dedup(self, tmp_path):
        s1 = tmp_path / "2023"
        s2 = tmp_path / "2024"
        _write_jsonl(s1 / "transfers.jsonl", [
            {"player_id": "100", "season": "2023", "from_club": "A", "to_club": "B", "fee": "Free", "loan": False, "transfer_date": "Aug 1"},
        ])
        _write_jsonl(s2 / "transfers.jsonl", [
            # Same record (should be deduped)
            {"player_id": "100", "season": "2023", "from_club": "A", "to_club": "B", "fee": "Free", "loan": False, "transfer_date": "Aug 1"},
            # New record
            {"player_id": "100", "season": "2024", "from_club": "B", "to_club": "C", "fee": "€1m", "loan": False, "transfer_date": "Jan 1"},
        ])

        merged = merge_appendable([s1, s2], "transfers.jsonl")
        assert len(merged) == 2  # deduped

    def test_missing_file(self, tmp_path):
        s1 = tmp_path / "2023"
        s1.mkdir()
        # No stats.jsonl exists
        merged = merge_appendable([s1], "stats.jsonl")
        assert merged == []


class TestMergeSeasons:
    def test_full_merge(self, tmp_path):
        base = tmp_path / "seasons"
        out = tmp_path / "merged"

        s1 = base / "2023"
        s2 = base / "2024"
        _write_jsonl(s1 / "players.jsonl", [
            {"id": "100", "name_english": "Player A", "current_squad": True,
             "current_jersey_number": 7, "birth_date": None, "birth_place": None,
             "nationality": None, "main_position": None, "name_hebrew": None,
             "homegrown": False, "retired": False},
        ])
        _write_jsonl(s2 / "players.jsonl", [
            {"id": "100", "name_english": "Player A", "current_squad": False,
             "current_jersey_number": 9, "birth_date": None, "birth_place": None,
             "nationality": None, "main_position": None, "name_hebrew": None,
             "homegrown": False, "retired": True},
        ])
        _write_jsonl(s1 / "transfers.jsonl", [
            {"player_id": "100", "season": "2023", "info": "transfer1"},
        ])
        _write_jsonl(s2 / "transfers.jsonl", [
            {"player_id": "100", "season": "2024", "info": "transfer2"},
        ])

        merge_seasons(base_dir=base, seasons=["2023", "2024"], output_dir=out)

        assert (out / "players.jsonl").exists()
        assert (out / "transfers.jsonl").exists()

        with open(out / "players.jsonl") as f:
            players = [json.loads(line) for line in f if line.strip()]
        assert len(players) == 1
        assert players[0]["retired"] is True

        with open(out / "transfers.jsonl") as f:
            transfers = [json.loads(line) for line in f if line.strip()]
        assert len(transfers) == 2

    def test_empty_seasons_still_creates_files(self, tmp_path):
        """Verify merge writes empty files instead of skipping them."""
        base = tmp_path / "seasons"
        s1 = base / "2023"
        _write_jsonl(s1 / "players.jsonl", [])
        _write_jsonl(s1 / "transfers.jsonl", [])
        _write_jsonl(s1 / "market_values.jsonl", [])
        _write_jsonl(s1 / "stats.jsonl", [])

        out = tmp_path / "merged"
        merge_seasons(base_dir=base, seasons=["2023"], output_dir=out)

        # All files must exist even when empty
        for f in ("players.jsonl", "transfers.jsonl", "market_values.jsonl", "stats.jsonl"):
            assert (out / f).exists()

    def test_missing_all_seasons(self, tmp_path):
        base = tmp_path / "empty"
        base.mkdir()
        with pytest.raises(FileNotFoundError):
            merge_seasons(base_dir=base, seasons=["2023"], output_dir=tmp_path / "out")
