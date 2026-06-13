"""Scrape per-season league standings from the Transfermarkt `platzierungen`
page (German "placements" / standings).

Phase 3a R2 addition. Single one-shot request to a club-level URL; emits one
row per (season, competition). The page covers ~1986/87 to current — older
seasons aren't listed at all, so the spider naturally skips them.

Per row TM gives us: season ("25/26"), competition + level, W/D/L, goals
(as "54:23"), goal difference, points, final position rank, and the
season's manager (name + TM coach id). The manager column is the same
data the inventory's per-season-manager extraction was going to derive from
the `startseite` (club homepage) page — so we get both jobs done in one
spider.
"""

import re

import scrapy
from scrapy.http import Request


_GOALS_RE = re.compile(r"(\d+)\s*[:\-]\s*(\d+)")


class PlatzierungenSpider(scrapy.Spider):
    """Per-season standings table from `/platzierungen/verein/2976`."""

    name = "platzierungen"
    allowed_domains = ["transfermarkt.com", "api.scraperapi.com"]

    def __init__(self, season="2024", *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Spider arg is accepted for CLI consistency with the per-season spiders
        # but is unused — the page renders every season in one response.
        self.season = season
        self.base_url = "https://www.transfermarkt.com"

    def start_requests(self):
        from tmk_scraper.scraperapi_proxy import validate_key, wrap

        target = f"{self.base_url}/hapoel-beer-sheva/platzierungen/verein/2976"
        use_scraperapi = self.settings.getbool("USE_SCRAPERAPI", False)
        api_key = validate_key(self.settings.get("SCRAPERAPI_KEY")) if use_scraperapi else None
        url = wrap(target, api_key) if use_scraperapi else target
        yield Request(url=url, callback=self.parse)

    def parse(self, response: scrapy.http.Response, **kwargs):
        count = 0

        for row in response.css("table.items > tbody > tr"):
            cols = row.xpath("./td")
            if len(cols) < 12:
                continue

            season_label = cols[0].css("::text").get("").strip()  # "25/26"
            season = self._season_from_label(season_label)
            if not season:
                continue

            # Competition link + name lives in the hauptlink cell (3rd col).
            competition_link = cols[2].css("a.hauptlink, a")
            competition = (
                competition_link.css("::text").get("").strip()
                or competition_link.attrib.get("title", "").strip()
            )

            tier = self._tier_from_label(cols[3].css("::text").get("").strip())

            wins = self._safe_int(cols[4].css("::text").get(""))
            draws = self._safe_int(cols[5].css("::text").get(""))
            losses = self._safe_int(cols[6].css("::text").get(""))
            goals_for, goals_against = self._split_goals(cols[7].css("::text").get(""))
            # cols[8] is goal differential — derivable; we skip recording it
            # separately since it's `goals_for - goals_against`.
            points = self._safe_int(cols[9].css("::text").get(""))
            # Rank cell wraps the number in <b>.
            rank_text = cols[10].css("b::text").get("") or cols[10].css("::text").get("")
            final_position = self._safe_int(rank_text)
            matches = (wins or 0) + (draws or 0) + (losses or 0)

            # Manager cell — last column.
            manager_link = cols[11].css("a")
            manager_name = (manager_link.css("::text").get("") or "").strip()
            manager_href = manager_link.attrib.get("href", "")
            manager_id = manager_href.rstrip("/").split("/")[-1] if manager_href else ""

            count += 1
            yield {
                "season": season,
                "competition": competition or None,
                "tier": tier,
                "final_position": final_position,
                # `played` not `matches` — Cargo reserved keyword. See schemas.py Coach.
                "played": matches if matches > 0 else None,
                "wins": wins,
                "draws": draws,
                "losses": losses,
                "goals_for": goals_for,
                "goals_against": goals_against,
                "points": points,
                # Manager info threaded through so the same spider also feeds
                # the per-season-manager dataset (Coach.tenure_seasons derivation).
                "manager_name": manager_name or None,
                "manager_id": manager_id or None,
            }

        self.logger.info("Scraped %d season-standing rows", count)

    @staticmethod
    def _season_from_label(label: str) -> str | None:
        """Convert TM's "YY/YY" season label to the bare integer start-year used
        elsewhere as the join key (e.g. "25/26" -> "2025"). Returns None on
        malformed labels.
        """
        if not label or "/" not in label:
            return None
        head = label.split("/", 1)[0].strip()
        if not head.isdigit():
            return None
        yy = int(head)
        # Pivot at 30: yy < 30 → 20yy; yy >= 30 → 19yy. HBS's founding-era
        # seasons require this — "49/50" must bin to 1949, not 2049 (the
        # §6 ③ corruption from the 2026-06-12 review). Same convention as
        # the transfers spider; bump down to 25 by 2030.
        return str(1900 + yy if yy >= 30 else 2000 + yy)

    @staticmethod
    def _tier_from_label(label: str) -> int | None:
        if not label:
            return None
        lower = label.lower()
        if "first" in lower:
            return 1
        if "second" in lower:
            return 2
        if "third" in lower:
            return 3
        return None

    @staticmethod
    def _safe_int(raw: str) -> int | None:
        if raw is None:
            return None
        s = raw.strip()
        if not s or s == "-":
            return None
        try:
            return int(s)
        except ValueError:
            return None

    @staticmethod
    def _split_goals(raw: str) -> tuple[int | None, int | None]:
        """TM renders goals as "54:23" (FOR : AGAINST). Returns the pair as
        integers; returns (None, None) when the format is anything else.
        """
        m = _GOALS_RE.match(raw or "")
        if not m:
            return None, None
        try:
            return int(m.group(1)), int(m.group(2))
        except ValueError:
            return None, None
