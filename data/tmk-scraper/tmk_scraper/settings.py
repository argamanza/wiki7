import os

# === Project identity ===
BOT_NAME = "tmk_scraper"
SPIDER_MODULES = ["tmk_scraper.spiders"]
NEWSPIDER_MODULE = "tmk_scraper.spiders"

# === ScraperAPI toggle ===
USE_SCRAPERAPI = True
SCRAPERAPI_KEY = os.environ.get("SCRAPERAPI_KEY", "")

# === Logging ===
# §6 ② fix (2026-06-12 review): DEBUG-level scrapy logging emits the
# fully-constructed proxy URL on every request, including `api_key=…`.
# INFO is verbose enough for normal operations; flip to DEBUG temporarily
# (and only when SCRAPERAPI_KEY is rotated immediately after) for any
# real debugging session.
LOG_LEVEL = "INFO"

# Reviewer-pass blocker (2026-06-13) + blocker-4-followup (2026-06-13):
# LOG_LEVEL=INFO alone is NOT sufficient — Scrapy's RetryMiddleware logs
# "Gave up retrying <GET …api_key=KEY…>" at ERROR regardless of
# LOG_LEVEL. The first fix tried `install_redacting_log_filter()` at
# settings.py import time, but that's BEFORE Scrapy configures its log
# handler — so nothing got attached to anything that matters. The
# correct hook is a Scrapy extension that fires on `engine_started`
# (after Scrapy logging is fully configured). RedactingLogExtension does
# exactly that.
EXTENSIONS = {
    "tmk_scraper.extensions.RedactingLogExtension": 0,
}

# === Concurrency ===
CONCURRENT_REQUESTS = 20
CONCURRENT_REQUESTS_PER_DOMAIN = 20

# === Retry behavior ===
RETRY_ENABLED = True
RETRY_TIMES = 5
RETRY_HTTP_CODES = [500, 503, 504, 522, 524, 408, 429]
RETRY_BACKOFF_BASE = 2

# === User-Agent rotation ===
DOWNLOADER_MIDDLEWARES = {
    'scrapy.downloadermiddlewares.useragent.UserAgentMiddleware': None,
    'scrapy.downloadermiddlewares.retry.RetryMiddleware': None,
    'scrapy_fake_useragent.middleware.RandomUserAgentMiddleware': 400,
    'scrapy_fake_useragent.middleware.RetryUserAgentMiddleware': 401,
    'scrapy.downloadermiddlewares.httpproxy.HttpProxyMiddleware': 410,
}

FAKEUSERAGENT_PROVIDERS = [
    'scrapy_fake_useragent.providers.FakerProvider',
    'scrapy_fake_useragent.providers.FixedUserAgentProvider',
]

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/123.0.0.0 Safari/537.36"
)

DEFAULT_REQUEST_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "DNT": "1",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

# === Output ===
FEED_FORMAT = "json"
FEED_EXPORT_ENCODING = "utf-8"

# === Conditional tuning ===
if USE_SCRAPERAPI:
    DOWNLOAD_DELAY = 0
    AUTOTHROTTLE_ENABLED = False
else:
    DOWNLOAD_DELAY = 3
    AUTOTHROTTLE_ENABLED = True
    AUTOTHROTTLE_START_DELAY = 3
    AUTOTHROTTLE_MAX_DELAY = 30
    AUTOTHROTTLE_TARGET_CONCURRENCY = 1.0
    AUTOTHROTTLE_DEBUG = False
