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

import logging
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


class _RedactingLogFilter(logging.Filter):
    """logging.Filter that runs `redact()` on every record's message AND
    arg values.

    Reviewer-pass blocker-4-followup (2026-06-13): the first version of
    this helper attached the filter to the root LOGGER. Python's logging
    semantics make that ineffective for the records that actually leak:
    filters on a logger only run for records emitted DIRECTLY on that
    logger. Records from child loggers (`scrapy.downloadermiddlewares.*`,
    run_pipeline's own `getLogger(__name__)` relay) propagate UP to
    root's HANDLERS without passing through the root logger's filters.
    So the filter has to live on the HANDLERS — see
    `attach_redacting_filter_to_handlers` for the install path.

    Catches once attached to handlers:
      - Scrapy's `Gave up retrying <GET …api_key=KEY…>` ERROR
      - DownloaderMiddleware's per-request DEBUG/INFO lines
      - run_pipeline's stderr-relay of spider failures (it forwards the
        last 10 lines verbatim, which include the proxy URL)
      - any other log path that picks up a request URL we didn't predict.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str) and "api_key=" in record.msg:
            record.msg = redact(record.msg)
        if record.args:
            try:
                record.args = tuple(
                    redact(a) if isinstance(a, str) and "api_key=" in a else a
                    for a in record.args
                )
            except TypeError:
                # `record.args` may be a dict-like for %()s named formatting.
                pass
        return True


def _handler_has_redactor(handler: logging.Handler) -> bool:
    return any(isinstance(f, _RedactingLogFilter) for f in handler.filters)


def attach_redacting_filter_to_handlers(logger: logging.Logger | None = None) -> int:
    """Attach the redacting filter to every handler on `logger` (default:
    the root logger). Returns the number of handlers it was newly
    attached to (skipping any that already had it — idempotent).

    Call this AFTER your logging is configured:
      - In run_pipeline: right after `logging.basicConfig`.
      - In the Scrapy spider process: from an extension's
        `engine_started` signal (or `from_crawler`) — settings.py
        import time is too early; Scrapy hasn't installed its handler yet.

    The filter is cheap (substring check + regex only on hits), so
    attaching unconditionally has no meaningful perf cost.
    """
    target = logger if logger is not None else logging.getLogger()
    attached = 0
    for h in target.handlers:
        if not _handler_has_redactor(h):
            h.addFilter(_RedactingLogFilter())
            attached += 1
    return attached


# Backward-compat alias kept for any §6 ② call sites still pointing at
# the old name. Note the semantic change: this now routes to the
# handler-attach path (the only effective one for propagated records).
# The §6 ② settings.py + run_pipeline call sites are updated to call
# `attach_redacting_filter_to_handlers` directly in the right places.
def install_redacting_log_filter() -> int:
    return attach_redacting_filter_to_handlers()
