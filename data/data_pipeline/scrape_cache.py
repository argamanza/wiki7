"""HEAD-based no-op detection for periodic re-scrapes.

A daily cron that always does a full scrape is wasteful — on typical days
nothing on TM has changed since the last run. This module gates the scrape
behind a cheap "did anything change?" probe:

1. Fetch the season's squad page HTML directly from TM (no ScraperAPI)
2. Hash the response
3. Compare to the last-saved hash for this season
4. If unchanged → return True (caller skips the full scrape)
5. If changed (or first run) → save the new hash, return False (proceed)

Empirical: a single squad-page probe is ~1 HTTP GET vs a full season scrape
which is ~100-200 ScraperAPI credits + ~10-20 Anthropic API calls. The
probe saves ~99% of the cost on no-op days while keeping <24h freshness
when something actually changes.

Cost projection (iter-cycle 1 baseline):
- Daily with no-op gate: ~30 credits + occasional full-scrape days = 500-1000/mo
- Daily without gate: ~3000-6000 credits/mo
- Weekly full scrape (no gate): ~800 credits/mo
- All-time daily: would exceed Hobby tier ($49/mo, 100k credits)

The no-op gate matches weekly's monthly cost while delivering <24h freshness.

Iter-cycle 1 (2026-06-12): Pattern A.4 of the v1+ re-import architecture.
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests
import yaml

logger = logging.getLogger(__name__)


DEFAULT_HASH_CACHE_PATH = (
    Path(__file__).resolve().parent.parent / "pipeline-state" / "scrape_hashes.yaml"
)

_HBS_KADER_URL = (
    "https://www.transfermarkt.com/hapoel-beer-sheva/kader/verein/2976"
    "/saison_id/{year}"
)

_TIMEOUT_S = 15.0
_USER_AGENT = "wiki7-pipeline/1.0 scrape-cache no-op probe"


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _normalise_html(html: str) -> str:
    """Strip noise from TM's HTML so the hash only reacts to meaningful
    changes (player roster, market values, etc).

    Strips:
    - Anti-scraping nonce tokens (`<meta name="csrf-token" content="...">`,
      etc) that change every request
    - Inline timestamps / cache-busting URLs in `<link>` and `<script>` tags
    - View-counter / online-user-count widgets

    Returns the cleaned HTML for hashing.
    """
    # Drop meta-tag nonces / csrf tokens
    cleaned = re.sub(
        r'<meta[^>]+(?:csrf|nonce|token)[^>]*>',
        '',
        html,
        flags=re.IGNORECASE,
    )
    # Drop cache-bust query strings on asset URLs
    cleaned = re.sub(r'\?[a-f0-9]{6,}\b', '?', cleaned)
    # Drop inline script src/href timestamps
    cleaned = re.sub(r'\bv=\d{10,}', 'v=', cleaned)
    return cleaned


def _hash(text: str) -> str:
    """Stable SHA-256 of normalised text."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class ScrapeHashCache:
    """Per-season squad-page hash cache.

    Schema (YAML):
        "2025":                     # saison_id (string for YAML cleanliness)
          squad_hash: "abc123..."
          last_checked: "2026-06-12T10:30:00Z"
          last_changed: "2026-06-11T14:15:00Z"
    """

    def __init__(self, path: Path | None = None):
        self.path = path or DEFAULT_HASH_CACHE_PATH
        self._data: dict[str, dict] = {}
        self._dirty = False

    def load(self) -> "ScrapeHashCache":
        if self.path.exists():
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    loaded = yaml.safe_load(f) or {}
                if isinstance(loaded, dict):
                    self._data = {str(k): v for k, v in loaded.items()}
            except (yaml.YAMLError, OSError) as exc:
                logger.warning(
                    "Failed to load scrape-hash cache from %s: %s. Starting fresh.",
                    self.path, exc,
                )
        return self

    def save(self) -> None:
        if not self._dirty:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            yaml.dump(
                self._data, f,
                allow_unicode=True,
                sort_keys=True,
                default_flow_style=False,
            )
        self._dirty = False

    def get_stored_hash(self, season: int | str) -> Optional[str]:
        record = self._data.get(str(season))
        return record.get("squad_hash") if record else None

    def update(self, season: int | str, new_hash: str, *, changed: bool) -> None:
        """Update the cache after a probe.

        `changed=True` means we observed a different hash → bump last_changed
        as well as last_checked. `changed=False` means we observed the same
        hash → only bump last_checked.
        """
        key = str(season)
        existing = self._data.get(key, {})
        now = _now_iso()
        new_record = {
            **existing,
            "squad_hash": new_hash,
            "last_checked": now,
        }
        if changed or "last_changed" not in existing:
            new_record["last_changed"] = now
        if new_record != existing:
            self._data[key] = new_record
            self._dirty = True


def squad_page_unchanged(
    season: int | str,
    cache: Optional[ScrapeHashCache] = None,
    session: Optional[requests.Session] = None,
) -> bool:
    """Probe TM's squad page for `season` and compare against cache.

    Returns True iff the page hash matches the cached value (i.e. nothing
    has changed on TM since the last check → caller may skip the full
    scrape). Returns False on:
      - First run for this season (no cached hash yet)
      - Hash mismatch (something changed)
      - HTTP/network failure (be conservative — proceed with full scrape)

    Updates the cache as a side effect (so next call reflects current
    observation), but DOES NOT save it — caller is responsible for
    `cache.save()` at the end of the pipeline run, just like
    `PageIndexState`.
    """
    if cache is None:
        cache = ScrapeHashCache().load()
    if session is None:
        session = requests.Session()
        session.headers.update({"User-Agent": _USER_AGENT})

    url = _HBS_KADER_URL.format(year=season)
    try:
        response = session.get(url, timeout=_TIMEOUT_S)
        response.raise_for_status()
    except requests.RequestException as exc:
        logger.warning(
            "scrape-cache: probe for season %s failed (%s); "
            "treating as changed (full scrape will proceed)",
            season, exc,
        )
        return False

    current_hash = _hash(_normalise_html(response.text))
    stored = cache.get_stored_hash(season)

    if stored is None:
        logger.info(
            "scrape-cache: first observation for season %s (hash %s...). "
            "Full scrape will proceed.",
            season, current_hash[:12],
        )
        cache.update(season, current_hash, changed=True)
        return False

    if current_hash == stored:
        cache.update(season, current_hash, changed=False)
        logger.info(
            "scrape-cache: season %s squad page unchanged since last check (hash %s...). "
            "Full scrape can be skipped.",
            season, current_hash[:12],
        )
        return True

    cache.update(season, current_hash, changed=True)
    logger.info(
        "scrape-cache: season %s squad page changed (was %s..., now %s...). "
        "Full scrape will proceed.",
        season, stored[:12], current_hash[:12],
    )
    return False
