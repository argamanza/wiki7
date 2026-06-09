"""Hebrew Wikipedia name lookup via English Wikipedia langlinks.

Phase 3a R2: Hebrew transliteration of foreign player names has multiple
acceptable forms; LLM and phonetic transliteration both produce plausible-
but-not-canonical results. When a player has an English Wikipedia article
with a Hebrew langlink, the Hebrew title IS the canonical form Israeli
football media uses — sourcing from there beats any transliteration.

Empirical coverage check for HBS players (sample, 2026-06-09):
- Lior Refaelov     → ליאור רפאלוב           ✓
- Anthony Nwakaeme  → אנתוני ואקמה           ✓ (canonical form)
- Sagiv Jehezkel    → שגיב יחזקאל             ✓
- Kings Kangwa      → קינגס קאנגווה           ✓ (lesser-known Zambian)
- Eliel Peretz      → אליאל פרץ               ✓ (recent academy promotion)

Strategy:
- For each English name, lookup via English Wikipedia `langlinks` API
  (`prop=langlinks&lllang=he&redirects=1`). The redirect flag handles
  common-name → canonical-page redirects (e.g. "Lior Rafailov" → article
  titled "Lior Refaelov").
- If a Hebrew langlink exists, return the Hebrew title (high confidence).
- If no Hebrew langlink OR no English article exists, return None — the
  caller falls back to Claude / phonetic transliteration.

Cost: zero — Wikipedia API is free. We send a User-Agent header per their
etiquette guidelines + use a small thread pool to keep total wall-clock
acceptable for ~1500-2000 names.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

import requests

logger = logging.getLogger(__name__)

_API_URL = "https://en.wikipedia.org/w/api.php"
_USER_AGENT = (
    "wiki7-pipeline/1.0 "
    "(https://github.com/argamanza/wiki7; data-pipeline) "
    "python-requests"
)
_TIMEOUT_S = 10.0
_MAX_WORKERS = 5  # polite concurrency; Wikipedia handles much more


def _query(session: requests.Session, title: str) -> Optional[str]:
    """Hit the langlinks endpoint for one title. Returns the Hebrew page
    title when present, None when:
      - the page doesn't exist in English Wikipedia, OR
      - the page exists but has no Hebrew langlink, OR
      - the API call errors transiently (treated as 'no result' so the
        caller falls back to Claude — that's better than aborting the
        run for a Wikipedia hiccup).
    """
    params = {
        "action": "query",
        "format": "json",
        "prop": "langlinks",
        "lllang": "he",
        "titles": title,
        "redirects": 1,
        # Limit langlinks response to just the Hebrew one we asked for; saves
        # bytes when the page has dozens of language editions.
        "lllimit": 1,
    }
    try:
        response = session.get(_API_URL, params=params, timeout=_TIMEOUT_S)
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError) as exc:
        logger.debug("Wikipedia lookup failed for %r: %s", title, exc)
        return None

    pages = data.get("query", {}).get("pages", {})
    if not pages:
        return None
    # Pages is keyed by page-id (or "-1" for missing pages).
    page = next(iter(pages.values()))
    if page.get("missing") is not None or page.get("pageid", 0) <= 0:
        return None
    langlinks = page.get("langlinks") or []
    for ll in langlinks:
        if ll.get("lang") == "he":
            hebrew_title = (ll.get("*") or "").strip()
            return hebrew_title or None
    return None


def lookup_hebrew_title(name: str) -> Optional[str]:
    """One-shot lookup of a single name. Returns the canonical Hebrew title
    or None. Use `lookup_batch` for many names — same logic, parallelised.
    """
    with requests.Session() as session:
        session.headers.update({"User-Agent": _USER_AGENT})
        return _query(session, name)


def lookup_batch(names: list[str]) -> dict[str, Optional[str]]:
    """Parallel lookup over many names. Returns a dict mapping each input
    name to its Hebrew title (or None if not found).

    Empty input list returns empty dict — no Wikipedia call made.
    """
    if not names:
        return {}

    results: dict[str, Optional[str]] = {name: None for name in names}
    with requests.Session() as session:
        session.headers.update({"User-Agent": _USER_AGENT})
        with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as executor:
            future_to_name = {
                executor.submit(_query, session, name): name
                for name in names
            }
            done = 0
            total = len(names)
            for future in as_completed(future_to_name):
                name = future_to_name[future]
                try:
                    results[name] = future.result()
                except Exception as exc:  # noqa: BLE001 — never fatal
                    logger.debug("Wikipedia batch lookup error for %r: %s", name, exc)
                    results[name] = None
                done += 1
                if done % 50 == 0:
                    logger.info("  Wikipedia lookup %d/%d...", done, total)

    found = sum(1 for v in results.values() if v)
    logger.info(
        "Wikipedia lookup: %d/%d resolved (%.1f%% coverage)",
        found, total, 100 * found / total if total else 0,
    )
    return results
