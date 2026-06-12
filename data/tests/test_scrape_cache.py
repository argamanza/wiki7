"""Tests for the HEAD-based no-op scrape cache (Pattern A.4)."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest
import requests

from data_pipeline.scrape_cache import (
    ScrapeHashCache,
    _hash,
    _normalise_html,
    squad_page_unchanged,
)


def _mock_response(html: str = "", status: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.text = html
    resp.status_code = status
    resp.raise_for_status = MagicMock()
    if status >= 400:
        resp.raise_for_status.side_effect = requests.HTTPError(f"HTTP {status}")
    return resp


def _session_returning(html: str) -> MagicMock:
    session = MagicMock(spec=requests.Session)
    session.get.return_value = _mock_response(html)
    return session


class TestHashAndNormalise:
    def test_identical_input_gives_identical_hash(self):
        assert _hash("foo") == _hash("foo")

    def test_different_input_gives_different_hash(self):
        assert _hash("foo") != _hash("foo ")

    def test_normalise_strips_csrf_meta(self):
        html_a = '<meta name="csrf-token" content="abc123"><body>X</body>'
        html_b = '<meta name="csrf-token" content="xyz789"><body>X</body>'
        # After normalise, both should produce same hash because the only
        # difference is the volatile csrf token
        assert _normalise_html(html_a).replace(" ", "") == _normalise_html(html_b).replace(" ", "")
        assert _hash(_normalise_html(html_a)) == _hash(_normalise_html(html_b))

    def test_normalise_strips_cache_bust_query(self):
        html_a = '<link href="/style.css?abc12345">'
        html_b = '<link href="/style.css?def67890">'
        assert _hash(_normalise_html(html_a)) == _hash(_normalise_html(html_b))

    def test_real_content_change_changes_hash(self):
        """The whole point: a player added/removed should change the hash."""
        html_a = '<tr><td class="hauptlink">Niv Eliasi</td></tr>'
        html_b = (
            '<tr><td class="hauptlink">Niv Eliasi</td></tr>'
            '<tr><td class="hauptlink">Ben Gordin</td></tr>'
        )
        assert _hash(_normalise_html(html_a)) != _hash(_normalise_html(html_b))


class TestScrapeHashCacheBasic:
    def test_missing_file_starts_empty(self, tmp_path: Path):
        cache = ScrapeHashCache(tmp_path / "x.yaml").load()
        assert cache.get_stored_hash("2024") is None

    def test_save_then_load(self, tmp_path: Path):
        path = tmp_path / "x.yaml"
        cache = ScrapeHashCache(path)
        cache.update("2024", "abcdef", changed=True)
        cache.save()
        reloaded = ScrapeHashCache(path).load()
        assert reloaded.get_stored_hash("2024") == "abcdef"
        assert reloaded.get_stored_hash(2024) == "abcdef"  # int key works too

    def test_idempotent_save(self, tmp_path: Path):
        path = tmp_path / "x.yaml"
        cache = ScrapeHashCache(path)
        cache.update("2024", "abcdef", changed=True)
        cache.save()
        mtime = path.stat().st_mtime
        ScrapeHashCache(path).load().save()  # No upserts
        assert path.stat().st_mtime == mtime

    def test_human_friendly_unicode(self, tmp_path: Path):
        # No Hebrew expected in the hash cache, but the YAML setting matters
        path = tmp_path / "x.yaml"
        cache = ScrapeHashCache(path)
        cache.update("2024", "abcdef", changed=True)
        cache.save()
        raw = path.read_text(encoding="utf-8")
        assert "\\u" not in raw


class TestSquadPageUnchanged:
    def test_first_observation_returns_false_and_caches(self, tmp_path: Path):
        """First run for a season: no cache → save the hash, return False
        (caller should proceed with full scrape)."""
        cache = ScrapeHashCache(tmp_path / "x.yaml")
        session = _session_returning("<body>squad content here</body>")
        result = squad_page_unchanged(2025, cache=cache, session=session)
        assert result is False
        assert cache.get_stored_hash(2025) is not None  # Hash was saved

    def test_unchanged_returns_true(self, tmp_path: Path):
        """Same hash on second observation → unchanged → return True."""
        cache = ScrapeHashCache(tmp_path / "x.yaml")
        session = _session_returning("<body>squad content here</body>")
        squad_page_unchanged(2025, cache=cache, session=session)  # First obs
        result = squad_page_unchanged(2025, cache=cache, session=session)  # Second
        assert result is True

    def test_changed_returns_false_and_updates_cache(self, tmp_path: Path):
        cache = ScrapeHashCache(tmp_path / "x.yaml")
        # First observation
        squad_page_unchanged(
            2025, cache=cache,
            session=_session_returning("<body>old content</body>"),
        )
        old_hash = cache.get_stored_hash(2025)
        # Second observation with different content
        result = squad_page_unchanged(
            2025, cache=cache,
            session=_session_returning("<body>NEW content</body>"),
        )
        assert result is False
        assert cache.get_stored_hash(2025) != old_hash

    def test_network_failure_returns_false(self, tmp_path: Path):
        """Defensive: if probe HTTP-fails, treat as 'might have changed'
        and proceed with full scrape rather than skip."""
        cache = ScrapeHashCache(tmp_path / "x.yaml")
        session = MagicMock(spec=requests.Session)
        session.get.side_effect = requests.ConnectionError("transient")
        result = squad_page_unchanged(2025, cache=cache, session=session)
        assert result is False

    def test_volatile_html_doesnt_falsely_invalidate(self, tmp_path: Path):
        """Real-world: TM serves the same squad with a different csrf token
        on each request. The cache must not flag the page as 'changed' just
        because the token rotated."""
        cache = ScrapeHashCache(tmp_path / "x.yaml")
        html_v1 = '<meta name="csrf-token" content="abc"><body>same squad</body>'
        html_v2 = '<meta name="csrf-token" content="xyz"><body>same squad</body>'
        squad_page_unchanged(
            2025, cache=cache, session=_session_returning(html_v1),
        )
        result = squad_page_unchanged(
            2025, cache=cache, session=_session_returning(html_v2),
        )
        assert result is True  # Despite the token change

    def test_real_squad_change_triggers_scrape(self, tmp_path: Path):
        cache = ScrapeHashCache(tmp_path / "x.yaml")
        # Squad with 1 player
        squad_page_unchanged(
            2025, cache=cache,
            session=_session_returning(
                '<tr><td class="hauptlink">Niv Eliasi</td></tr>',
            ),
        )
        # Squad with 2 players (Ben Gordin added)
        result = squad_page_unchanged(
            2025, cache=cache,
            session=_session_returning(
                '<tr><td class="hauptlink">Niv Eliasi</td></tr>'
                '<tr><td class="hauptlink">Ben Gordin</td></tr>',
            ),
        )
        assert result is False


class TestScrapeCacheSaveOnlyAfterSuccess:
    """§6 ⑥ fix from the 2026-06-12 review: the cache must NOT be persisted
    until the pipeline has succeeded end-to-end. Pre-fix, run_pipeline.py
    saved the new hash immediately after the probe, BEFORE the scrape ran.
    A failed scrape then permanently recorded "unchanged" — next invocation
    saw the same hash, skipped scrape, never recovered without
    `--force-rescrape`. These tests cover the cache-layer behavior; the
    save-deferral happens in run_pipeline.py and is tested separately by
    convention.
    """

    def test_update_mutates_in_memory_without_writing(self, tmp_path: Path):
        """The probe mutates the cache in memory (so subsequent probes
        in the same run see the new hash) but only `save()` writes to
        disk. Caller can choose to skip save() if the run failed."""
        path = tmp_path / "x.yaml"
        cache = ScrapeHashCache(path)
        cache.update("2024", "newhash", changed=True)
        # In-memory state has the new hash
        assert cache.get_stored_hash("2024") == "newhash"
        # But nothing on disk yet
        assert not path.exists()

    def test_skipping_save_after_failure_preserves_prior_state(self, tmp_path: Path):
        """The §6 ⑥ wedge regression: simulate a failed scrape by
        updating the in-memory cache then NOT calling save(). The on-disk
        cache must still reflect the previous successful run."""
        path = tmp_path / "x.yaml"
        # Successful prior run
        cache_run1 = ScrapeHashCache(path)
        cache_run1.update("2024", "good_hash", changed=True)
        cache_run1.save()
        # Pipeline run 2: probe detects "changed", updates in-memory cache,
        # but scrape fails → operator (or run_pipeline) does NOT call save()
        cache_run2 = ScrapeHashCache(path).load()
        cache_run2.update("2024", "would_be_new_hash", changed=True)
        # save() NOT called
        # Run 3: cache reload sees the PRE-failure hash, NOT the wedge value
        cache_run3 = ScrapeHashCache(path).load()
        assert cache_run3.get_stored_hash("2024") == "good_hash"
        assert cache_run3.get_stored_hash("2024") != "would_be_new_hash"
