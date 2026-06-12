"""Tests for the ScraperAPI proxy URL helper — §6 ② fix from the
2026-06-12 full-project review.

The helper consolidates 8 spiders' previously-inline URL construction.
These tests cover:
  - HTTPS-only transport (the http:// regression)
  - Empty-key fail-fast (no more burning the retry budget on 401s)
  - Optional URL-encoding for query-string-bearing targets
  - Redaction (preventing accidental key leaks in logs / output records)
"""

import pytest

from tmk_scraper.scraperapi_proxy import (
    ScraperApiKeyMissingError,
    redact,
    validate_key,
    wrap,
)


class TestWrap:
    def test_emits_https(self):
        """The original bug: every spider used `http://api.scraperapi.com`,
        sending the api_key in cleartext until ScraperAPI's edge."""
        url = wrap("https://transfermarkt.com/club/x", "MY_KEY")
        assert url.startswith("https://api.scraperapi.com/")
        assert "http://" not in url[8:]  # skip the leading https://

    def test_default_query_params(self):
        url = wrap("https://transfermarkt.com/club/x", "MY_KEY")
        assert "api_key=MY_KEY" in url
        assert "url=https://transfermarkt.com/club/x" in url
        assert "country_code=us" in url
        assert "render=false" in url

    def test_render_true(self):
        url = wrap("https://transfermarkt.com/x", "K", render=True)
        assert "render=true" in url

    def test_custom_country(self):
        url = wrap("https://transfermarkt.com/x", "K", country="de")
        assert "country_code=de" in url

    def test_target_unencoded_by_default(self):
        """7 of 8 legacy spiders relied on un-encoded path-style targets.
        Preserve that default behavior."""
        url = wrap("https://transfermarkt.com/hbs/bilanz/verein/2976", "K")
        assert "url=https://transfermarkt.com/hbs/bilanz/verein/2976" in url

    def test_urlencode_target_for_query_string_target(self):
        """stats_spider's target carries `?saison_id=2024` — without
        encoding, the `?` and `&` get interpreted as part of the proxy
        URL's own query string."""
        target = "https://transfermarkt.com/hbs/leistungsdaten/verein/2976/plus/1?saison_id=2024"
        url = wrap(target, "K", urlencode_target=True)
        assert "saison_id%3D2024" in url
        # The literal `?saison_id=` should NOT appear (it'd be inside the
        # encoded `url=` value).
        assert "?saison_id=2024" not in url


class TestValidateKey:
    def test_returns_stripped_key(self):
        assert validate_key("MY_KEY") == "MY_KEY"
        assert validate_key("  MY_KEY  ") == "MY_KEY"

    def test_empty_string_raises(self):
        with pytest.raises(ScraperApiKeyMissingError) as exc:
            validate_key("")
        assert "SCRAPERAPI_KEY" in str(exc.value)

    def test_whitespace_only_raises(self):
        with pytest.raises(ScraperApiKeyMissingError):
            validate_key("   \n\t  ")

    def test_none_raises(self):
        with pytest.raises(ScraperApiKeyMissingError):
            validate_key(None)

    def test_error_message_mentions_settings(self):
        """The fail-fast is meant to surface a config error obviously —
        verify the error message points the operator at the right knobs."""
        with pytest.raises(ScraperApiKeyMissingError) as exc:
            validate_key("")
        msg = str(exc.value)
        assert "SCRAPERAPI_KEY" in msg
        assert "USE_SCRAPERAPI" in msg


class TestRedact:
    def test_redacts_proxy_url(self):
        url = wrap("https://transfermarkt.com/x", "REAL_KEY_SECRET_DO_NOT_LOG")
        redacted = redact(url)
        assert "REAL_KEY_SECRET_DO_NOT_LOG" not in redacted
        assert "api_key=REDACTED" in redacted

    def test_redacts_arbitrary_string(self):
        """Useful for log messages that may contain a key-bearing URL
        embedded in larger text."""
        msg = "fetching https://api.scraperapi.com/?api_key=ABCD1234&url=tm.com"
        redacted = redact(msg)
        assert "ABCD1234" not in redacted
        assert "api_key=REDACTED" in redacted

    def test_passthrough_when_no_key(self):
        msg = "no proxy here, just a plain string"
        assert redact(msg) == msg

    def test_none_input(self):
        """Defensive: callers may pass response.url or similar where the
        value could be None."""
        assert redact(None) == ""

    def test_non_string_input(self):
        """Defensive: stringification before redaction."""
        result = redact(12345)
        assert result == "12345"

    def test_case_insensitive_match(self):
        """The proxy form lowercases the param; defensive against case
        variations from elsewhere."""
        result = redact("?API_KEY=SECRET&x=1")
        assert "SECRET" not in result


class TestRedactingLogFilter:
    """Reviewer-pass blocker (2026-06-13): LOG_LEVEL=INFO alone doesn't
    stop Scrapy's RetryMiddleware emitting `Gave up retrying <GET …
    api_key=KEY…>` at ERROR. run_pipeline.py:139-141 then re-propagates
    spider stderr (which includes that line) to its own logger. The
    redacting root filter is the catch-all. These tests pin its behavior
    so a future refactor that removes the install_… call surfaces here."""

    def _captured_emission(self, msg: str, args=()) -> str:
        """Capture what the filter does to a single log record's text."""
        import logging
        from tmk_scraper.scraperapi_proxy import (
            _RedactingLogFilter,
        )
        # Apply the filter directly (avoid global state).
        record = logging.LogRecord(
            name="test", level=logging.ERROR, pathname=__file__, lineno=0,
            msg=msg, args=args, exc_info=None,
        )
        _RedactingLogFilter().filter(record)
        try:
            return record.getMessage()
        except TypeError:
            # If args mutated unexpectedly, return raw msg
            return record.msg

    def test_redacts_in_plain_message(self):
        out = self._captured_emission(
            "Gave up retrying <GET https://api.scraperapi.com/?api_key=ABC123&url=tm.com>"
        )
        assert "ABC123" not in out
        assert "api_key=REDACTED" in out

    def test_redacts_in_args(self):
        out = self._captured_emission(
            "Request failed: %s",
            args=("https://api.scraperapi.com/?api_key=KEY_SECRET&url=tm.com",),
        )
        assert "KEY_SECRET" not in out
        assert "REDACTED" in out

    def test_clean_message_passes_through(self):
        out = self._captured_emission("Normal log line, no key here")
        assert out == "Normal log line, no key here"

    def test_install_is_idempotent(self):
        """Calling install_redacting_log_filter twice should not double-
        register the filter on the root logger."""
        import logging
        from tmk_scraper.scraperapi_proxy import install_redacting_log_filter, _RedactingLogFilter
        root = logging.getLogger()
        before_count = sum(1 for f in root.filters if isinstance(f, _RedactingLogFilter))
        install_redacting_log_filter()
        install_redacting_log_filter()
        install_redacting_log_filter()
        after_count = sum(1 for f in root.filters if isinstance(f, _RedactingLogFilter))
        # Either it was already installed (before > 0, after unchanged) or
        # newly installed (before 0, after 1). Never more than one instance.
        assert after_count <= max(1, before_count)
