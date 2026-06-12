"""Reviewer-pass blocker (2026-06-13): seed_merged_mapping_from_iter_cycles
must propagate every reviewer correction from per-season iter-cycle
mappings.he.yaml files into the merged file the all-time run reads.

Pre-fix wedge: iter-cycles write `output/<year>/mappings.he.yaml`, the
all-time run reads `output/merged/mappings.he.yaml`. Every reviewer
correction from current cycles was silently absent from the prod push:
the all-time run re-translated from scratch, picked bad Wikidata
titles, and MovePaged everything during reviewer cleanup."""

from pathlib import Path

import yaml

from data_pipeline.apply_hebrew_mapping import seed_merged_mapping_from_iter_cycles


def _write_mapping(path: Path, mapping: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(mapping, f, allow_unicode=True)


def _read_mapping(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


class TestSeedMergedMapping:
    def test_manual_entries_from_iter_cycle_seed_merged(self, tmp_path: Path):
        """The core regression: a reviewer's manual correction lives
        in the per-season file; without seeding, the all-time run
        re-translates from scratch and the correction evaporates."""
        cycle = tmp_path / "2024"
        merged = tmp_path / "merged" / "mappings.he.yaml"

        # Reviewer corrected "Ben Gordin" to common form in the 2024 cycle.
        _write_mapping(cycle / "mappings.he.yaml", {
            "names": {
                "Ben Gordin": {
                    "he": "בן גורדין",
                    "src": "manual",
                    "confidence": "high",
                    "wikidata_qid": "",
                    "note": "common-form correction (reviewer)",
                },
            },
        })

        added = seed_merged_mapping_from_iter_cycles([cycle], merged)
        assert added == 1

        result = _read_mapping(merged)
        assert result["names"]["Ben Gordin"]["he"] == "בן גורדין"
        assert result["names"]["Ben Gordin"]["src"] == "manual"

    def test_manual_beats_wikidata_when_both_present(self, tmp_path: Path):
        """Precedence: src=manual must win over src=wikidata even if
        the merged file already has a wikidata entry. Reviewer's word is
        authoritative."""
        cycle = tmp_path / "2024"
        merged = tmp_path / "merged" / "mappings.he.yaml"

        # Pre-existing wikidata entry in the merged file (an older auto-
        # translation that the reviewer has since corrected).
        _write_mapping(merged, {
            "names": {
                "Ben Gordin": {
                    "he": "בן אנריקה גורדין ענברי",
                    "src": "wikidata",
                    "confidence": "high",
                    "wikidata_qid": "Q123",
                },
            },
        })
        _write_mapping(cycle / "mappings.he.yaml", {
            "names": {
                "Ben Gordin": {
                    "he": "בן גורדין",
                    "src": "manual",
                    "confidence": "high",
                    "wikidata_qid": "",
                    "note": "common-form (reviewer override)",
                },
            },
        })

        seed_merged_mapping_from_iter_cycles([cycle], merged)
        result = _read_mapping(merged)
        # Manual wins.
        assert result["names"]["Ben Gordin"]["he"] == "בן גורדין"
        assert result["names"]["Ben Gordin"]["src"] == "manual"

    def test_existing_merged_manual_NOT_overwritten_by_iter_wikidata(self, tmp_path: Path):
        """Inverse precedence: if the merged file already has a manual
        entry (set directly against the merged dir during an earlier
        all-time review), an iter-cycle's auto-translated wikidata entry
        must NOT overwrite it."""
        cycle = tmp_path / "2024"
        merged = tmp_path / "merged" / "mappings.he.yaml"

        _write_mapping(merged, {
            "names": {
                "Lior Refaelov": {"he": "ליאור רפאלוב (custom)", "src": "manual"},
            },
        })
        _write_mapping(cycle / "mappings.he.yaml", {
            "names": {
                "Lior Refaelov": {"he": "ליאור רפאלוב", "src": "wikidata"},
            },
        })

        seed_merged_mapping_from_iter_cycles([cycle], merged)
        result = _read_mapping(merged)
        # Original manual entry preserved.
        assert result["names"]["Lior Refaelov"]["he"] == "ליאור רפאלוב (custom)"

    def test_multiple_iter_cycles_aggregate(self, tmp_path: Path):
        """Multiple per-season dirs should ALL contribute. A correction
        made in 2023's cycle and a correction made in 2024's cycle should
        both reach the merged file."""
        c2023 = tmp_path / "2023"
        c2024 = tmp_path / "2024"
        merged = tmp_path / "merged" / "mappings.he.yaml"

        _write_mapping(c2023 / "mappings.he.yaml", {
            "names": {"Player A": {"he": "שחקן א", "src": "manual"}},
        })
        _write_mapping(c2024 / "mappings.he.yaml", {
            "names": {"Player B": {"he": "שחקן ב", "src": "manual"}},
        })

        added = seed_merged_mapping_from_iter_cycles([c2023, c2024], merged)
        assert added == 2

        result = _read_mapping(merged)
        assert result["names"]["Player A"]["he"] == "שחקן א"
        assert result["names"]["Player B"]["he"] == "שחקן ב"

    def test_no_iter_cycles_is_noop(self, tmp_path: Path):
        merged = tmp_path / "merged" / "mappings.he.yaml"
        added = seed_merged_mapping_from_iter_cycles([], merged)
        assert added == 0
        # File created (empty mapping written) — not load-bearing but
        # documents the behavior.
        assert not merged.exists() or _read_mapping(merged) in ({}, None)

    def test_missing_iter_cycle_file_is_skipped(self, tmp_path: Path):
        """A dir without a mappings.he.yaml shouldn't crash; just skip."""
        cycle = tmp_path / "2024"
        cycle.mkdir()  # dir exists but no mappings file
        merged = tmp_path / "merged" / "mappings.he.yaml"
        added = seed_merged_mapping_from_iter_cycles([cycle], merged)
        assert added == 0

    def test_all_sections_seeded_not_just_names(self, tmp_path: Path):
        """clubs, competitions, positions, nationalities all flow through
        the same precedence logic."""
        cycle = tmp_path / "2024"
        merged = tmp_path / "merged" / "mappings.he.yaml"
        _write_mapping(cycle / "mappings.he.yaml", {
            "clubs": {"Lazio": {"he": "לאציו", "src": "manual"}},
            "competitions": {"Serie A": {"he": "סריה א", "src": "manual"}},
            "positions": {"Forward": {"he": "חלוץ", "src": "manual"}},
            "nationalities": {"Israel": {"he": "ישראל", "src": "manual"}},
        })

        seed_merged_mapping_from_iter_cycles([cycle], merged)
        result = _read_mapping(merged)
        assert result["clubs"]["Lazio"]["he"] == "לאציו"
        assert result["competitions"]["Serie A"]["he"] == "סריה א"
        assert result["positions"]["Forward"]["he"] == "חלוץ"
        assert result["nationalities"]["Israel"]["he"] == "ישראל"
