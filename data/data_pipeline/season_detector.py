"""Detect TM's latest populated season for HBS.

The pipeline is parameterized by `--season YYYY` (start year of the season,
matching TM's `saison_id` URL parameter). For periodic re-scrapes,
operator-specified seasons are brittle:
- Mid-summer the bot's `latest known season` may have been completed but TM
  hasn't moved the default to the new one yet.
- Around transfer-window opens, a new (sparse) saison_id page exists on TM
  but doesn't yet have a real squad — scraping it returns empty.
- Operators may forget to update the parameter when a new season starts.

This module probes TM directly to find the highest saison_id with a real
(populated) squad page, defined as ">= _MIN_SQUAD_ROWS player entries".

Wired into `run_pipeline.py` via `--season=latest`. Empirical baseline
from iter-cycle 1 (probed 2026-06-12):
- saison_id=2024 → 29 squad rows (last completed season, 24/25)
- saison_id=2025 → 29 squad rows (most recent populated, 25/26)
- saison_id=2026 →  4 squad rows (sparse — early transfers for 26/27)
- saison_id=2027 →  4 squad rows (sparse)
So `latest=2025` is the right answer in mid-June 2026.

Iter-cycle 1 (2026-06-12): Pattern A.3 of the v1+ re-import architecture.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

import requests

logger = logging.getLogger(__name__)


_HBS_KADER_URL = (
    "https://www.transfermarkt.com/hapoel-beer-sheva/kader/verein/2976"
    "/saison_id/{year}"
)

# Empirical threshold: real Israeli Premier League squad has ~25-30 players;
# an unpopulated future-season page has ~3-5 stub entries (early transfers
# announced before the season's squad is finalised). 15 is a safe middle
# ground that distinguishes populated from stub.
_MIN_SQUAD_ROWS = 15

# Conservative probe range. Don't go before football-data-exists; don't go
# absurdly far into the future. The detector returns the highest year in
# the range whose page passes the populated check.
_PROBE_START_DEFAULT = 2020  # well-covered TM era
_PROBE_END_DEFAULT = 2035    # arbitrary forward cap; reviewer can bump

_TIMEOUT_S = 15.0
_USER_AGENT = "wiki7-pipeline/1.0 season-detector (no scraping; lightweight probe)"


def _count_squad_rows(html: str) -> int:
    """Count squad-row entries on a TM kader page. TM puts every player
    row's main link in a `class="hauptlink"` cell; counting these is a
    fast proxy for `is this season's squad real or stub`."""
    return len(re.findall(r'class="hauptlink"', html))


def detect_latest_populated_season(
    session: Optional[requests.Session] = None,
    start_year: int = _PROBE_START_DEFAULT,
    end_year: int = _PROBE_END_DEFAULT,
    min_squad_rows: int = _MIN_SQUAD_ROWS,
) -> int:
    """Probe TM saison_id pages from `start_year` upward. Return the highest
    year whose squad page is populated.

    Stops early on the first sparse year (sparseness propagates forward —
    if 2027 is sparse, 2028 will also be). On HTTP/network failure for a
    probe, treats that year as unprobed and stops; returns the last known-
    populated year.

    Iter-cycle 1 baseline: 6 HTTP calls expected at the boundary (probe
    2020, 2021, ..., 2027 stopping when 2026 returns sparse). ScraperAPI
    not needed — these are lightweight HTML GETs against the public page.
    """
    if session is None:
        session = requests.Session()
        session.headers.update({"User-Agent": _USER_AGENT})

    last_populated = start_year
    for year in range(start_year, end_year + 1):
        url = _HBS_KADER_URL.format(year=year)
        try:
            response = session.get(url, timeout=_TIMEOUT_S)
            response.raise_for_status()
        except requests.RequestException as exc:
            logger.warning(
                "season detector: probe for %d failed (%s); stopping search",
                year, exc,
            )
            break
        rows = _count_squad_rows(response.text)
        logger.debug("season detector: saison_id=%d → %d squad rows", year, rows)
        if rows >= min_squad_rows:
            last_populated = year
            continue
        # First sparse year — assume everything after is also sparse, stop.
        logger.info(
            "season detector: saison_id=%d is sparse (%d rows < %d threshold); "
            "latest populated = %d",
            year, rows, min_squad_rows, last_populated,
        )
        return last_populated

    return last_populated


def resolve_season_arg(season_arg: str) -> str:
    """Resolve the `--season` CLI arg to a bare-integer start year string.

    Accepts:
      - "2024" / "2025" / etc. — pass through (already a year)
      - "latest" — invoke detector
    """
    if season_arg.lower() == "latest":
        year = detect_latest_populated_season()
        logger.info("--season=latest resolved to %d", year)
        return str(year)
    return season_arg
