"""Scrapy extensions for the tmk_scraper project.

Currently just one: `RedactingLogExtension` attaches the api_key
redacting filter to the root logger's handlers AFTER Scrapy has
configured its own handler. settings.py import time is too early —
Scrapy installs its log handler later in the boot sequence, so a
filter attached at settings.py import-time has nothing to attach to.

Enable in `settings.py` via:

    EXTENSIONS = {
        "tmk_scraper.extensions.RedactingLogExtension": 0,
    }
"""

from __future__ import annotations

import logging

from scrapy import signals

from tmk_scraper.scraperapi_proxy import attach_redacting_filter_to_handlers


class RedactingLogExtension:
    """Attach the api_key redacting log filter to root handlers when the
    Scrapy engine starts.

    Reviewer-pass blocker-4-followup (2026-06-13): the first version of
    the §6 ② fix called `install_redacting_log_filter()` at
    settings.py import time. That call attempted to attach to handlers
    too early — Scrapy hadn't configured its handler yet — and the
    filter was effectively never installed. This extension hooks
    `engine_started`, which fires AFTER Scrapy's logging is fully
    configured.
    """

    def __init__(self):
        self._logger = logging.getLogger(__name__)

    @classmethod
    def from_crawler(cls, crawler):
        ext = cls()
        crawler.signals.connect(ext.engine_started, signal=signals.engine_started)
        return ext

    def engine_started(self):
        # Reviewer-pass note (2026-06-13): there is a narrow window between
        # Scrapy installing its log handler and this signal firing where a
        # key-bearing line emitted at ERROR would slip past the filter.
        # In practice every dangerous emit (RetryMiddleware "Gave up
        # retrying", download errors) happens deep in the crawl after
        # engine_started, and run_pipeline.py's capture-site redact() is
        # a second net for the spider-stderr relay path. Theoretical
        # only; not worth re-engineering.
        attached = attach_redacting_filter_to_handlers()
        self._logger.info(
            "RedactingLogExtension: attached redactor to %d handler(s)",
            attached,
        )
