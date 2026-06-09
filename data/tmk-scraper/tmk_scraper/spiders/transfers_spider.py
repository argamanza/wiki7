import re

import scrapy
from scrapy.http import Request


class TransfersSpider(scrapy.Spider):
    """Scrape club-level arrivals + departures for a single season from
    Transfermarkt.

    The `/alletransfers/verein/2976/saison_id/<season>` page (`alletransfers`
    = "all transfers") returns *all* past seasons in one HTML response (TM
    ignores the saison_id query for the table contents and only uses it for
    navigation). We walk every `div.box`, parse the `<h2>` for direction +
    season, and filter rows to the spider's `self.season`.
    """

    name = "transfers"
    allowed_domains = ["transfermarkt.com", "api.scraperapi.com"]

    # h2 text examples: "Arrivals 24/25", "Departures 24/25", "Arrivals 23/24", ...
    _HEADER_RE = re.compile(r"^\s*(arrivals|departures)\s+(\d{2})/(\d{2})", re.IGNORECASE)

    def __init__(self, season="2024", *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.season = str(season)

    def start_requests(self):
        target = (
            f"https://www.transfermarkt.com/hapoel-beer-sheva/alletransfers/verein/2976"
            f"/saison_id/{self.season}"
        )
        use_scraperapi = self.settings.getbool("USE_SCRAPERAPI", False)
        api_key = self.settings.get("SCRAPERAPI_KEY")
        url = (
            f"http://api.scraperapi.com/?api_key={api_key}&url={target}&country_code=us&render=false"
            if use_scraperapi else target
        )
        yield Request(url=url, callback=self.parse)

    def _parse_header(self, header_text: str):
        """Return (direction, season_yyyy) if the h2 matches; otherwise (None, None).

        `Arrivals 24/25` -> ('in',  '2024')
        `Departures 24/25` -> ('out', '2024')
        TM's "24/25" maps to saison_id=2024 (the year the season started).
        """
        m = self._HEADER_RE.match(header_text or "")
        if not m:
            return None, None
        direction = "in" if m.group(1).lower() == "arrivals" else "out"
        # Two-digit year prefix. Years 50-99 belong to the 1900s, 00-49 to the 2000s
        # (handles the early-2000s seasons in the archive).
        yy = int(m.group(2))
        season_yyyy = 1900 + yy if yy >= 50 else 2000 + yy
        return direction, str(season_yyyy)

    def parse(self, response: scrapy.http.Response, **kwargs):
        count = 0

        for box in response.css("div.box"):
            header = (box.css("h2::text").get() or "").strip()
            direction, season_yyyy = self._parse_header(header)
            if not direction or season_yyyy != self.season:
                continue

            # Rows are <tr> directly under the box's <tbody>. The current TM transfers table
            # does not use the `items` class on its <table>, so the historical
            # `table.items > tbody > tr` selector misses every row.
            for row in box.css("tbody > tr"):
                name_link = row.css("td.hauptlink a")
                if not name_link:
                    continue
                player_name = name_link.css("::text").get("").strip()
                profile_url = name_link.attrib.get("href", "")
                player_id = profile_url.rstrip("/").split("/")[-1] if profile_url else ""
                if not player_name or not player_id:
                    continue

                # Club name comes from the `<img title=...>` on the wappen cell or the text
                # of the no-border-links anchor (which is sometimes a shortened display name).
                other_club = (
                    row.css("td.no-border-rechts img::attr(title)").get()
                    or row.css("td.no-border-links a::text").get()
                    or ""
                ).strip()
                fee = (row.css("td.rechts::text").get() or row.css("td.rechts a::text").get() or "").strip() or "-"
                is_loan = "loan" in fee.lower()

                if direction == "in":
                    from_club, to_club = other_club, "Hapoel Beer Sheva"
                else:
                    from_club, to_club = "Hapoel Beer Sheva", other_club

                count += 1
                yield {
                    "season": self.season,
                    "player_name": player_name,
                    "player_id": player_id,
                    # TM's current club-transfers table no longer surfaces age + position
                    # inline; they're only on the player profile. Empty defaults keep the
                    # ClubTransfer schema's optional fields populated.
                    "age": "",
                    "position": "",
                    "from_club": from_club,
                    "to_club": to_club,
                    "fee": fee,
                    "loan": is_loan,
                    "direction": direction,
                }

        self.logger.info("Scraped %d transfers for season %s", count, self.season)
