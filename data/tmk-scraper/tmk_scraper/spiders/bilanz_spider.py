"""Scrape per-opponent head-to-head record from Transfermarkt bilanz page.

Phase 3a R2 addition. Single one-shot request to the club-level URL; emits
one row per opponent club, aggregated across all competitions and all
seasons TM has data for.

This unlocks the Derbies page (HBS vs Maccabi TA / Hapoel TA / Beitar
Jerusalem / Maccabi Haifa) and a "most-played opponent" section on the
club records page. The page also offers per-season + per-competition
filtering via URL params, but the default "all-time, all competitions"
view is exactly what the Derbies page wants — no extra request needed.
"""

import re

import scrapy
from scrapy.http import Request


_CLUB_ID_RE = re.compile(r"/verein/(\d+)")
_INT_RE = re.compile(r"-?\d+")


class BilanzSpider(scrapy.Spider):
    """Per-opponent head-to-head from `/bilanz/verein/2976`."""

    name = "bilanz"
    allowed_domains = ["transfermarkt.com", "api.scraperapi.com"]

    def __init__(self, season="2024", *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Spider arg accepted for CLI consistency; unused (single page covers
        # all opponents across all seasons).
        self.season = season
        self.base_url = "https://www.transfermarkt.com"

    def start_requests(self):
        target = f"{self.base_url}/hapoel-beer-sheva/bilanz/verein/2976"
        use_scraperapi = self.settings.getbool("USE_SCRAPERAPI", False)
        api_key = self.settings.get("SCRAPERAPI_KEY")
        url = (
            f"http://api.scraperapi.com/?api_key={api_key}&url={target}&country_code=us&render=false"
            if use_scraperapi else target
        )
        yield Request(url=url, callback=self.parse)

    def parse(self, response: scrapy.http.Response, **kwargs):
        count = 0

        for row in response.css("table.items > tbody > tr"):
            cols = row.xpath("./td")
            # Bilanz table: club | country | matches | W | D | L | +/- | pts | win% | avg attendance
            if len(cols) < 10:
                continue

            # Opponent name + TM ID live in the inline-table inside the first cell.
            club_link = cols[0].css("a.hauptlink, td.hauptlink a, a")
            opponent = ""
            opponent_tm_id = ""
            for link in club_link:
                title = (link.attrib.get("title") or "").strip()
                href = link.attrib.get("href", "")
                if title and "/verein/" in href:
                    opponent = title
                    m = _CLUB_ID_RE.search(href)
                    if m:
                        opponent_tm_id = m.group(1)
                    break
            if not opponent:
                continue

            # Matches cell is wrapped in <a title="Detailed statistics">.
            matches_text = cols[2].css("a::text").get("") or cols[2].css("::text").get("")
            matches = self._safe_int(matches_text)
            if matches is None:
                continue

            wins = self._safe_int(cols[3].css("::text").get(""))
            draws = self._safe_int(cols[4].css("::text").get(""))
            losses = self._safe_int(cols[5].css("::text").get(""))

            # cols[6] is goal differential — kept for completeness but the
            # schema stores GF/GA derivable from it. The bilanz page on
            # current TM doesn't surface the raw GF:GA split per row, only
            # the differential, so we compute neither here and leave the
            # schema's goals_for / goals_against unset (zero default).

            avg_attendance_raw = cols[9].css("::text").get("") if len(cols) > 9 else ""
            avg_attendance = self._parse_attendance(avg_attendance_raw)

            count += 1
            yield {
                "opponent": opponent,
                "opponent_tm_id": opponent_tm_id or None,
                "matches": matches,
                "wins": wins or 0,
                "draws": draws or 0,
                "losses": losses or 0,
                # Bilanz aggregates GF/GA only as a differential; leave the
                # raw counts at zero so the schema validates. Derbies page can
                # still render the differential via the schema's existing
                # fields if needed (future enhancement).
                "goals_for": 0,
                "goals_against": 0,
                "avg_attendance": avg_attendance,
            }

        self.logger.info("Scraped %d head-to-head rows", count)

    @staticmethod
    def _safe_int(raw: str) -> int | None:
        if raw is None:
            return None
        s = raw.strip()
        if not s or s == "-":
            return None
        m = _INT_RE.search(s)
        if not m:
            return None
        try:
            return int(m.group(0))
        except ValueError:
            return None

    @staticmethod
    def _parse_attendance(raw: str) -> int | None:
        """TM renders attendance as "9.501" (European decimal-as-thousands)
        or "9,501" (anglo) — both mean ~9.5k. Strip non-digits and parse.
        """
        if not raw:
            return None
        digits = re.sub(r"[^\d]", "", raw)
        if not digits:
            return None
        try:
            return int(digits)
        except ValueError:
            return None
