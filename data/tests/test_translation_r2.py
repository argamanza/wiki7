"""Tests for Phase 3a R2 translation shape changes.

Covers:
- Backward-compat reader (`_value` / `_lookup`) handles both flat and nested
  mapping entries.
- One-shot legacy-to-nested migration is idempotent and preserves human-
  curated entries with `src: manual`, `confidence: high`.
- Auto-fill respects the manual-entry preservation rule (never overwrites
  a `src: manual` entry, even if the LLM produced a different value).
- The `--review-flagged-only` report surfaces every `confidence: low` row.

Anthropic API calls themselves are NOT exercised by these tests — the API
client is mocked via `monkeypatch` so the tests stay offline and fast. PR
B step 10 (local multi-season test) is where the live Claude path gets
real coverage.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from data_pipeline import auto_translate_hebrew as att
from data_pipeline.apply_hebrew_mapping import _lookup, _value, apply_hebrew, load_mapping


# ---------------------------------------------------------------------------
# Backward-compat reader
# ---------------------------------------------------------------------------


class TestValueResolver:
    def test_none_returns_empty(self):
        assert _value(None) == ""

    def test_empty_string_returns_empty(self):
        assert _value("") == ""

    def test_flat_string_passes_through(self):
        assert _value("שוער") == "שוער"

    def test_nested_returns_he(self):
        assert _value({"he": "שוער", "src": "manual", "confidence": "high"}) == "שוער"

    def test_nested_with_missing_he_returns_empty(self):
        assert _value({"src": "manual", "confidence": "high"}) == ""

    def test_nested_with_empty_he_returns_empty(self):
        assert _value({"he": "", "src": "auto-llm", "confidence": "low"}) == ""


class TestLookup:
    def test_lookup_in_flat_section(self):
        section = {"Goalkeeper": "שוער", "Centre-Back": "בלם"}
        assert _lookup(section, "Goalkeeper") == "שוער"
        assert _lookup(section, "Centre-Back") == "בלם"
        assert _lookup(section, "Unknown") == ""

    def test_lookup_in_nested_section(self):
        section = {
            "Goalkeeper": {"he": "שוער", "src": "manual", "confidence": "high"},
            "Centre-Back": {"he": "בלם", "src": "auto-llm", "confidence": "high"},
        }
        assert _lookup(section, "Goalkeeper") == "שוער"
        assert _lookup(section, "Centre-Back") == "בלם"

    def test_lookup_mixed_section(self):
        """During the transition some entries may already be nested while
        others are still flat. Both shapes must coexist in the same file."""
        section = {
            "Goalkeeper": "שוער",
            "Centre-Back": {"he": "בלם", "src": "manual", "confidence": "high"},
        }
        assert _lookup(section, "Goalkeeper") == "שוער"
        assert _lookup(section, "Centre-Back") == "בלם"


# ---------------------------------------------------------------------------
# Flat → nested migration
# ---------------------------------------------------------------------------


class TestMigration:
    def test_migrate_entry_with_value(self):
        result = att._migrate_entry("שוער")
        assert result == {"he": "שוער", "src": "manual", "confidence": "high", "note": ""}

    def test_migrate_entry_empty(self):
        result = att._migrate_entry("")
        assert result == {"he": "", "src": "", "confidence": "", "note": ""}

    def test_migrate_section_with_mixed_entries(self):
        section = {
            "Goalkeeper": "שוער",
            "Centre-Back": "",   # empty flat entry
            "Already Nested": {"he": "כבר", "src": "auto-llm", "confidence": "low"},
        }
        new_section, migrated = att._migrate_section(section)
        assert migrated == 1   # only "Goalkeeper" was a non-empty flat entry
        # The nested entry passes through unchanged.
        assert new_section["Already Nested"]["src"] == "auto-llm"
        # The flat manual entry is now nested.
        assert new_section["Goalkeeper"] == {
            "he": "שוער", "src": "manual", "confidence": "high", "note": ""
        }
        # The flat empty entry is now an empty nested slot.
        assert new_section["Centre-Back"]["src"] == ""

    def test_migrate_is_idempotent(self):
        """Re-running migration on an already-nested file changes nothing."""
        section = {
            "Goalkeeper": {"he": "שוער", "src": "manual", "confidence": "high", "note": ""}
        }
        once, n1 = att._migrate_section(section)
        twice, n2 = att._migrate_section(once)
        assert n1 == 0
        assert n2 == 0
        assert twice == once


# ---------------------------------------------------------------------------
# auto_translate orchestration with mocked Anthropic backend
# ---------------------------------------------------------------------------


def _write_mapping(tmp_path: Path, mapping: dict) -> Path:
    out = tmp_path / "mappings.he.yaml"
    out.write_text(yaml.dump(mapping, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return out


def _make_fake_claude_response(translations: list[dict]) -> MagicMock:
    """Mock the Anthropic client's `.messages.create()` return shape."""
    block = MagicMock()
    block.text = json.dumps({"translations": translations}, ensure_ascii=False)
    response = MagicMock()
    response.content = [block]
    return response


class TestAutoTranslateOrchestration:
    def test_legacy_flat_yaml_gets_migrated_on_first_run(self, tmp_path, monkeypatch):
        """A pre-R2 flat YAML loads, migrates to nested, fills empties via
        Claude (mocked), and writes back the nested shape."""
        legacy = {
            "positions": {"Goalkeeper": "שוער", "Centre-Back": ""},
            "nationalities": {},
            "clubs": {},
            "competitions": {},
            "names": {},
        }
        path = _write_mapping(tmp_path, legacy)

        # Mock the Anthropic client so the test stays offline.
        mock_client = MagicMock()
        mock_client.messages.create.return_value = _make_fake_claude_response([
            {"en": "Centre-Back", "he": "בלם", "confidence": "high"},
        ])
        monkeypatch.setattr(att, "anthropic", MagicMock(Anthropic=lambda api_key=None: mock_client))
        monkeypatch.setenv("WIKI7_ANTHROPIC_API_KEY", "test-key")

        summary = att.auto_translate(mapping_path=path)
        assert summary["positions"] == 1

        # Reload the file and confirm both entries are in nested shape.
        reloaded = load_mapping(path)
        gk = reloaded["positions"]["Goalkeeper"]
        cb = reloaded["positions"]["Centre-Back"]
        assert gk["src"] == "manual"  # migrated from the flat entry
        assert gk["he"] == "שוער"
        assert cb["src"] == "auto-llm"  # filled by Claude
        assert cb["he"] == "בלם"
        assert cb["confidence"] == "high"

    def test_manual_entries_are_not_overwritten(self, tmp_path, monkeypatch):
        """Even if Claude returns a different value for a manual entry, the
        manual one must survive."""
        mapping = {
            "positions": {
                "Goalkeeper": {
                    "he": "שוער",
                    "src": "manual",
                    "confidence": "high",
                    "note": "Verified by reviewer 2026-06-01",
                },
            },
            "nationalities": {},
            "clubs": {},
            "competitions": {},
            "names": {},
        }
        path = _write_mapping(tmp_path, mapping)

        # Even if a buggy Claude call would have returned a different value,
        # we should never see `messages.create()` called at all because all
        # entries are already filled.
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = AssertionError(
            "auto-translate should not contact Claude when all entries are filled"
        )
        monkeypatch.setattr(att, "anthropic", MagicMock(Anthropic=lambda api_key=None: mock_client))
        monkeypatch.setenv("WIKI7_ANTHROPIC_API_KEY", "test-key")

        summary = att.auto_translate(mapping_path=path)
        assert summary["positions"] == 0

        reloaded = load_mapping(path)
        assert reloaded["positions"]["Goalkeeper"]["src"] == "manual"
        assert reloaded["positions"]["Goalkeeper"]["he"] == "שוער"
        assert reloaded["positions"]["Goalkeeper"]["note"] == "Verified by reviewer 2026-06-01"

    def test_claude_low_confidence_is_recorded(self, tmp_path, monkeypatch):
        """Claude self-rates confidence; low ratings propagate into the
        nested shape so the reviewer can find flagged entries fast."""
        mapping = {
            "positions": {},
            "nationalities": {},
            "clubs": {},
            "competitions": {},
            "names": {"Obscure Russian Player": ""},
        }
        path = _write_mapping(tmp_path, mapping)

        mock_client = MagicMock()
        mock_client.messages.create.return_value = _make_fake_claude_response([
            {"en": "Obscure Russian Player", "he": "פלוני אלמוני", "confidence": "low"},
        ])
        monkeypatch.setattr(att, "anthropic", MagicMock(Anthropic=lambda api_key=None: mock_client))
        monkeypatch.setenv("WIKI7_ANTHROPIC_API_KEY", "test-key")

        att.auto_translate(mapping_path=path)
        reloaded = load_mapping(path)
        entry = reloaded["names"]["Obscure Russian Player"]
        assert entry["confidence"] == "low"
        assert entry["src"] == "auto-llm"

    def test_wikipedia_first_then_claude_for_names(self, tmp_path, monkeypatch):
        """Phase 3a R2: the `names` category gets a Wikipedia first-pass.
        Anything Wikipedia resolves is stamped `src: wikipedia, confidence:
        high`. The remaining unresolved names fall through to Claude."""
        mapping = {
            "positions": {},
            "nationalities": {},
            "clubs": {},
            "competitions": {},
            "names": {
                "Lior Refaelov": "",
                "Sagiv Jehezkel": "",
                "Obscure Player": "",
            },
        }
        path = _write_mapping(tmp_path, mapping)

        from data_pipeline import wikipedia_lookup
        monkeypatch.setattr(
            wikipedia_lookup, "lookup_batch",
            lambda names: {
                "Lior Refaelov": "ליאור רפאלוב",
                "Sagiv Jehezkel": "שגיב יחזקאל",
                "Obscure Player": None,
            },
        )

        mock_client = MagicMock()
        mock_client.messages.create.return_value = _make_fake_claude_response([
            {"en": "Obscure Player", "he": "פלוני אלמוני", "confidence": "low"},
        ])
        monkeypatch.setattr(att, "anthropic", MagicMock(Anthropic=lambda api_key=None: mock_client))
        monkeypatch.setenv("WIKI7_ANTHROPIC_API_KEY", "test-key")

        att.auto_translate(mapping_path=path)
        reloaded = load_mapping(path)

        refaelov = reloaded["names"]["Lior Refaelov"]
        assert refaelov["src"] == "wikipedia"
        assert refaelov["confidence"] == "high"
        assert refaelov["he"] == "ליאור רפאלוב"

        jehezkel = reloaded["names"]["Sagiv Jehezkel"]
        assert jehezkel["src"] == "wikipedia"
        assert jehezkel["he"] == "שגיב יחזקאל"

        obscure = reloaded["names"]["Obscure Player"]
        assert obscure["src"] == "auto-llm"
        assert obscure["he"] == "פלוני אלמוני"
        assert obscure["confidence"] == "low"

    def test_wikipedia_skipped_for_non_name_categories(self, tmp_path, monkeypatch):
        """Positions / nationalities / clubs don't get Wikipedia lookups in v1.
        They go straight to Claude."""
        mapping = {
            "positions": {"Centre-Back": ""},
            "nationalities": {},
            "clubs": {},
            "competitions": {},
            "names": {},
        }
        path = _write_mapping(tmp_path, mapping)

        lookup_calls: list[list[str]] = []
        from data_pipeline import wikipedia_lookup
        monkeypatch.setattr(
            wikipedia_lookup, "lookup_batch",
            lambda names: (lookup_calls.append(list(names)), {n: None for n in names})[1],
        )

        mock_client = MagicMock()
        mock_client.messages.create.return_value = _make_fake_claude_response([
            {"en": "Centre-Back", "he": "בלם", "confidence": "high"},
        ])
        monkeypatch.setattr(att, "anthropic", MagicMock(Anthropic=lambda api_key=None: mock_client))
        monkeypatch.setenv("WIKI7_ANTHROPIC_API_KEY", "test-key")

        att.auto_translate(mapping_path=path)
        assert lookup_calls == [], "Wikipedia should not be called for non-name categories"

    def test_falls_back_to_google_when_no_api_key(self, tmp_path, monkeypatch):
        """When neither WIKI7_ANTHROPIC_API_KEY nor ANTHROPIC_API_KEY is set,
        the helper picks the Google backend and stamps `src: auto-google` on
        every fill."""
        monkeypatch.delenv("WIKI7_ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        # Mock the Google translation path so the test stays offline.
        monkeypatch.setattr(
            att, "_translate_batch_via_google",
            lambda texts, src="en", dest="iw": ["FAKE_GOOGLE"] * len(texts),
        )

        mapping = {
            "positions": {"Centre-Back": ""},
            "nationalities": {},
            "clubs": {},
            "competitions": {},
            "names": {},
        }
        path = _write_mapping(tmp_path, mapping)

        att.auto_translate(mapping_path=path)
        reloaded = load_mapping(path)
        entry = reloaded["positions"]["Centre-Back"]
        assert entry["src"] == "auto-google"
        # Google-backed entries are always low confidence (no self-rating).
        assert entry["confidence"] == "low"


# ---------------------------------------------------------------------------
# apply_hebrew round-trip through both shapes
# ---------------------------------------------------------------------------


class TestApplyAcrossShapes:
    def test_apply_hebrew_with_nested_mapping(self):
        mapping = {
            "positions": {
                "Goalkeeper": {"he": "שוער", "src": "manual", "confidence": "high"}
            },
            "nationalities": {
                "Israel": {"he": "ישראל", "src": "manual", "confidence": "high"}
            },
            "names": {
                "Sagiv Jehezkel": {"he": "שגיב יחזקאל", "src": "manual", "confidence": "high"}
            },
        }
        player = {
            "name_english": "Sagiv Jehezkel",
            "name_hebrew": None,
            "main_position": "Goalkeeper",
            "nationality": ["Israel"],
        }
        out = apply_hebrew(player, mapping)
        assert out["main_position"] == "שוער"
        assert out["nationality"] == ["ישראל"]
        assert out["name_hebrew"] == "שגיב יחזקאל"

    def test_apply_hebrew_with_flat_mapping(self):
        """A pre-R2 mapping file that hasn't been migrated yet must still
        produce identical translations."""
        mapping = {
            "positions": {"Goalkeeper": "שוער"},
            "nationalities": {"Israel": "ישראל"},
            "names": {"Sagiv Jehezkel": "שגיב יחזקאל"},
        }
        player = {
            "name_english": "Sagiv Jehezkel",
            "name_hebrew": None,
            "main_position": "Goalkeeper",
            "nationality": ["Israel"],
        }
        out = apply_hebrew(player, mapping)
        assert out["main_position"] == "שוער"
        assert out["nationality"] == ["ישראל"]
        assert out["name_hebrew"] == "שגיב יחזקאל"

    def test_apply_hebrew_with_mixed_shapes(self):
        """Some entries flat, some nested — both must resolve correctly."""
        mapping = {
            "positions": {
                "Goalkeeper": "שוער",
                "Centre-Back": {"he": "בלם", "src": "auto-llm", "confidence": "high"},
            },
            "nationalities": {},
            "names": {},
        }
        player_gk = {"name_english": "X", "name_hebrew": None, "main_position": "Goalkeeper", "nationality": None}
        player_cb = {"name_english": "Y", "name_hebrew": None, "main_position": "Centre-Back", "nationality": None}
        assert apply_hebrew(player_gk, mapping)["main_position"] == "שוער"
        assert apply_hebrew(player_cb, mapping)["main_position"] == "בלם"
