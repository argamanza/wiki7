"""ScraperAPI proxy URL builder + key-redaction helpers.

Centralises three pieces of key-hygiene logic that were duplicated across
8 spiders before the §6 ② fix from the 2026-06-12 full-project review:

  1. **HTTPS transport.** The proxy URL was built as
     `http://api.scraperapi.com/...?api_key=...`. The api_key travelled
     in cleartext over every hop until ScraperAPI's edge — anyone on the
     path (corporate proxy, ISP, MITM) could capture it. `wrap()` now
     emits `https://` unconditionally.

  2. **Empty-key fail-fast.** When `USE_SCRAPERAPI=True` but
     `SCRAPERAPI_KEY` was empty (env var unset, CI default, fresh dev
     setup), the spider would build `…?api_key=&url=…` and burn the
     entire retry budget on 401s. `validate_key()` raises before the
     first request when the key is missing or whitespace-only.

  3. **Redaction for logging.** Spiders that wanted to log the wrapped
     URL for debugging had to remember to redact the key manually. Most
     didn't. `redact()` strips the `api_key=…` segment, replacing it
     with `api_key=REDACTED`, for any string that may otherwise leak.

The 8 spiders that previously inlined this:
match_spider, player_spider, bilanz_spider, transfers_spider,
coach_spider, stats_spider, platzierungen_spider, squad_spider.
"""

from __future__ import annotations

import re
from typing import Any


_PROXY_HOST = "https://api.scraperapi.com"
_API_KEY_RE = re.compile(r"(api_key=)[^&\s]*", re.IGNORECASE)


class ScraperApiKeyMissingError(RuntimeError):
    """Raised when USE_SCRAPERAPI is enabled but no SCRAPERAPI_KEY is set.

    Caught at spider-start time (before the first request) so the operator
    sees an obvious config error instead of cryptic 401 retries.
    """


def validate_key(api_key: str | None) -> str:
    """Return `api_key` stripped, or raise `ScraperApiKeyMissingError`.

    Callers should invoke this once at the top of their `start()` method
    when `USE_SCRAPERAPI=True`. Empty / whitespace-only / None → raise.
    Returning the stripped string makes the helper convenient as a
    single-line replacement for the previous `api_key = settings.get(...)`
    pattern.
    """
    s = (api_key or "").strip()
    if not s:
        raise ScraperApiKeyMissingError(
            "USE_SCRAPERAPI is enabled but SCRAPERAPI_KEY is missing or empty. "
            "Set SCRAPERAPI_KEY in the environment, or set USE_SCRAPERAPI=False "
            "in tmk_scraper/settings.py for direct (un-proxied) scraping."
        )
    return s


def wrap(
    target_url: str,
    api_key: str,
    *,
    country: str = "us",
    render: bool = False,
    urlencode_target: bool = False,
) -> str:
    """Build the ScraperAPI proxy URL that wraps `target_url`.

    HTTPS-only — see module docstring §1. The `country` and `render`
    parameters expose ScraperAPI's two most-used flags; defaults match
    the pre-fix behavior so the 8 spiders' substitutions are mechanical.

    `urlencode_target=False` (the default) matches the legacy behavior of
    7/8 spiders: ScraperAPI accepts un-encoded path-style targets
    (`/hapoel-beer-sheva/bilanz/verein/2976`). Set `urlencode_target=True`
    when the target carries a query string (`?saison_id=…`) so the `?`
    and `&` don't get interpreted as part of the proxy URL's own query
    — that's what `stats_spider` always did, manually, with
    `quote(target_url, safe='')`.
    """
    from urllib.parse import quote

    encoded_target = quote(target_url, safe="") if urlencode_target else target_url
    return (
        f"{_PROXY_HOST}/"
        f"?api_key={api_key}"
        f"&url={encoded_target}"
        f"&country_code={country}"
        f"&render={'true' if render else 'false'}"
    )


def redact(maybe_proxy_url: Any) -> str:
    """Return a copy of `maybe_proxy_url` with any `api_key=...` segment
    replaced by `api_key=REDACTED`.

    Safe to call on non-strings (returns `str(arg)` after redaction) and
    on strings that don't contain a key (returns unchanged). Use anywhere
    a wrapped URL might leak — log messages, output records that should
    track the *target* URL but accidentally got the proxy URL.
    """
    s = str(maybe_proxy_url) if maybe_proxy_url is not None else ""
    return _API_KEY_RE.sub(r"\1REDACTED", s)
