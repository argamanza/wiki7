"""Tests for the TM-ID page-index state file (Pattern A.1)."""

import re
from pathlib import Path

import pytest

from data_pipeline.pipeline_state import PageIndexState, _now_iso


class TestPageIndexStateBasic:
    def test_missing_file_starts_empty(self, tmp_path: Path):
        state = PageIndexState(tmp_path / "nonexistent.yaml").load()
        assert len(state) == 0

    def test_save_then_load_roundtrip(self, tmp_path: Path):
        path = tmp_path / "page_index.yaml"
        state = PageIndexState(path)
        state.upsert("912586", "ניב אליאסי", 3000)
        state.upsert("171068", "הלדר לופש", 3000)
        state.save()
        # Reload
        reloaded = PageIndexState(path).load()
        assert len(reloaded) == 2
        assert reloaded.get("912586")["he_title"] == "ניב אליאסי"
        assert reloaded.get("171068")["namespace"] == 3000

    def test_save_skipped_when_clean(self, tmp_path: Path):
        path = tmp_path / "page_index.yaml"
        state = PageIndexState(path)
        state.upsert("1", "X", 3000)
        state.save()
        original_mtime = path.stat().st_mtime
        # Reload + immediate save = no-op
        reloaded = PageIndexState(path).load()
        reloaded.save()
        # File mtime unchanged → save() was a no-op
        assert path.stat().st_mtime == original_mtime

    def test_upsert_returns_previous_record(self, tmp_path: Path):
        state = PageIndexState(tmp_path / "x.yaml")
        first = state.upsert("1", "A", 3000)
        assert first is None  # No previous
        second = state.upsert("1", "B", 3000)
        assert second is not None
        assert second["he_title"] == "A"
        assert second["namespace"] == 3000

    def test_upsert_no_op_when_unchanged_doesnt_dirty_state(self, tmp_path: Path):
        """Pipeline runs touch every player on every run. If nothing drifted
        we must NOT rewrite the file every time (large repos, scary mtime
        churn). The dirty flag should only flip when title or namespace
        actually changed."""
        path = tmp_path / "x.yaml"
        state = PageIndexState(path)
        state.upsert("1", "X", 3000)
        state.save()
        mtime_after_first = path.stat().st_mtime
        # Reload and re-upsert with same values
        state2 = PageIndexState(path).load()
        state2.upsert("1", "X", 3000)
        state2.save()
        assert path.stat().st_mtime == mtime_after_first

    def test_remove(self, tmp_path: Path):
        state = PageIndexState(tmp_path / "x.yaml")
        state.upsert("1", "X", 3000)
        state.upsert("2", "Y", 3000)
        state.remove("1")
        assert "1" not in state
        assert "2" in state

    def test_contains_handles_int_keys(self, tmp_path: Path):
        """TM IDs come from URLs as strings, but Python code passing ints
        should also work — defensive typing."""
        state = PageIndexState(tmp_path / "x.yaml")
        state.upsert(912586, "X", 3000)
        assert 912586 in state
        assert "912586" in state
        assert state.get(912586) is not None
        assert state.get("912586") is not None


class TestPageIndexStateTitleNormalization:
    def test_strips_draft_namespace_prefix(self, tmp_path: Path):
        """Callers may pass either bare title or namespace-prefixed title
        (e.g. when reading directly off page.name from mwclient). The state
        stores the bare title + namespace number so reconstructions are
        unambiguous."""
        state = PageIndexState(tmp_path / "x.yaml")
        state.upsert("1", "Draft:ניב אליאסי", 3000)
        record = state.get("1")
        assert record["he_title"] == "ניב אליאסי"  # Draft: stripped
        assert record["namespace"] == 3000

    def test_does_not_strip_real_colon_in_hebrew_title(self, tmp_path: Path):
        """A Hebrew title might (rarely) contain a colon for stylistic
        reasons. Only strip recognised namespace prefixes."""
        state = PageIndexState(tmp_path / "x.yaml")
        state.upsert("1", "פלוני: אלמוני", 0)  # Bizarre but valid title
        assert state.get("1")["he_title"] == "פלוני: אלמוני"


class TestPageIndexStateTimestamp:
    def test_now_iso_format(self):
        ts = _now_iso()
        # ISO 8601, UTC, ends with Z, no microseconds
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", ts), ts

    def test_last_seen_is_iso(self, tmp_path: Path):
        state = PageIndexState(tmp_path / "x.yaml")
        state.upsert("1", "X", 3000)
        ts = state.get("1")["last_seen"]
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", ts), ts


class TestPageIndexStateFailureModes:
    def test_empty_tm_id_rejected(self, tmp_path: Path):
        state = PageIndexState(tmp_path / "x.yaml")
        with pytest.raises(ValueError):
            state.upsert("", "X", 3000)

    def test_corrupt_yaml_starts_fresh_without_crashing(self, tmp_path: Path):
        path = tmp_path / "x.yaml"
        path.write_text("!!!not valid yaml: {{{", encoding="utf-8")
        state = PageIndexState(path).load()
        # Should not raise; should fall back to empty
        assert len(state) == 0

    def test_non_dict_root_falls_back_to_empty(self, tmp_path: Path):
        path = tmp_path / "x.yaml"
        path.write_text("- just a list, not a dict\n", encoding="utf-8")
        state = PageIndexState(path).load()
        assert len(state) == 0

    def test_yaml_is_human_friendly_unicode(self, tmp_path: Path):
        """Reviewer might inspect the file when troubleshooting. The Hebrew
        should NOT be escape-encoded (\\u05E0...) — should be readable."""
        path = tmp_path / "x.yaml"
        state = PageIndexState(path)
        state.upsert("1", "ניב אליאסי", 3000)
        state.save()
        raw = path.read_text(encoding="utf-8")
        assert "ניב אליאסי" in raw
        assert "\\u05" not in raw  # No unicode escapes
