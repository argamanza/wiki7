import scrapy
from scrapy.http import Request


# Known header titles/text to column semantic mapping.
# The title attributes on TM's stats page are on <span> elements nested inside <th>,
# not on the <th> itself.  We check all descendant title/text candidates.
HEADER_ALIASES = {
    "appearances": "appearances",
    "goals": "goals",
    "assists": "assists",
    "yellow cards": "yellow_cards",
    "yellow": "yellow_cards",
    "second yellow cards": "second_yellow_cards",
    "second yellow": "second_yellow_cards",
    "yellow-red": "second_yellow_cards",
    "red cards": "red_cards",
    "red": "red_cards",
    "minutes played": "minutes_played",
    "minutes": "minutes_played",
    # "In squad" is the total squad count, NOT actual match appearances.
    # Do NOT map it here — the real appearances column has title="Appearances".
}


class StatsSpider(scrapy.Spider):
    """Scrape per-season player statistics from the Transfermarkt
    `leistungsdaten` page (German "performance data" — apps, goals,
    assists, cards, minutes)."""

    name = "stats"
    allowed_domains = ["transfermarkt.com", "api.scraperapi.com"]

    def __init__(self, season="2024", *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.season = season
        self.base_url = "https://www.transfermarkt.com"

    async def start(self):
        from tmk_scraper.scraperapi_proxy import validate_key, wrap

        use_scraperapi = self.settings.getbool("USE_SCRAPERAPI", False)
        api_key = validate_key(self.settings.get("SCRAPERAPI_KEY")) if use_scraperapi else None

        target_url = (
            f"{self.base_url}/hapoel-beer-sheva/leistungsdaten/verein/2976"
            f"/plus/1?saison_id={self.season}"
        )

        # urlencode_target=True because target has a `?saison_id=…` query
        # string — unencoded `?` would land in the proxy URL's own params.
        url = (
            wrap(target_url, api_key, urlencode_target=True)
            if use_scraperapi else target_url
        )

        yield Request(url=url, callback=self.parse)

    def _build_column_map(self, response):
        """Build a mapping from semantic field name to effective column index using thead.

        Accounts for colspan on header cells so indices match <td> positions in tbody.
        On Transfermarkt, stat column titles are on nested <span> elements
        (e.g. ``<th><a><span title="Appearances">&nbsp;</span></a></th>``),
        not on the ``<th>`` itself.  We therefore collect title attributes from
        all descendant elements.
        """
        col_map = {}
        headers = response.css("table.items > thead > tr > th")
        effective_idx = 0

        for th in headers:
            # Collect candidates: th's own title, then any descendant title,
            # th text, link text.
            th_title = (th.attrib.get("title") or "").strip().lower()
            # Check nested span/a/img title attributes
            nested_titles = th.css("[title]::attr(title)").getall()
            text = th.css("::text").get("").strip().lower()
            link_text = th.css("a::text").get("").strip().lower()

            candidates = [th_title] + [t.strip().lower() for t in nested_titles] + [text, link_text]

            for candidate in candidates:
                if candidate and candidate in HEADER_ALIASES:
                    col_map[HEADER_ALIASES[candidate]] = effective_idx
                    break

            colspan = int(th.attrib.get("colspan", 1))
            effective_idx += colspan

        self.logger.info("Column map from headers: %s", col_map)
        return col_map

    def parse(self, response: scrapy.http.Response, **kwargs):
        col_map = self._build_column_map(response)

        # Fallback: if header parsing fails, use known default layout
        # Detailed view (plus/1) body columns (0-indexed):
        # 0=#, 1=player, 2=age, 3=nat, 4=in_squad, 5=apps, 6=goals,
        # 7=assists, 8=yellow, 9=2nd_yellow, 10=red, 11=subs_on,
        # 12=subs_off, 13=ppg, 14=minutes
        if not col_map:
            self.logger.warning(
                "Could not parse column headers, using default column layout"
            )
            col_map = {
                "appearances": 5,
                "goals": 6,
                "assists": 7,
                "yellow_cards": 8,
                "second_yellow_cards": 9,
                "red_cards": 10,
                "minutes_played": 14,
            }

        rows = response.css("table.items > tbody > tr")
        stats_count = 0

        for row in rows:
            # Skip spacer rows (those without player links)
            if not row.css("td.hauptlink"):
                continue

            # Extract player profile URL and ID
            link = row.css("td.hauptlink a::attr(href)").get()
            if not link:
                continue

            player_id = link.strip().split("/")[-1]
            name = row.css("td.hauptlink a::text").get("").strip()

            # Extract only direct child td cells (avoid nested table cells)
            all_cells = row.xpath("./td")

            # Check for "Not used during this season" text
            row_text = row.css("::text").getall()
            if any("not used" in t.lower() for t in row_text):
                self.logger.debug("Skipping '%s' — not used during this season", name)
                continue

            appearances = self._extract_cell_int(all_cells, col_map.get("appearances"))
            goals = self._extract_cell_int(all_cells, col_map.get("goals"))
            assists = self._extract_cell_int(all_cells, col_map.get("assists"))
            yellow_cards = self._extract_cell_int(all_cells, col_map.get("yellow_cards"))
            second_yellow_cards = self._extract_cell_int(all_cells, col_map.get("second_yellow_cards"))
            red_cards = self._extract_cell_int(all_cells, col_map.get("red_cards"))
            minutes_played = self._extract_cell_minutes(all_cells, col_map.get("minutes_played"))

            self.logger.debug(
                "Player %s (%s): apps=%d, goals=%d, assists=%d, yellow=%d, "
                "2nd_yellow=%d, red=%d, min=%d",
                name, player_id, appearances, goals, assists, yellow_cards,
                second_yellow_cards, red_cards, minutes_played,
            )

            stats_count += 1
            yield {
                "player_id": player_id,
                "season": self.season,
                "appearances": appearances,
                "goals": goals,
                "assists": assists,
                "yellow_cards": yellow_cards,
                "second_yellow_cards": second_yellow_cards,
                "red_cards": red_cards,
                "minutes_played": minutes_played,
            }

        self.logger.info("Scraped stats for %d players in season %s", stats_count, self.season)

    @staticmethod
    def _extract_cell_int(cells, col_idx):
        """Extract an integer from a cell at the given column index.

        Handles: direct text, text inside <a> tags, '-' as 0, missing cells.
        """
        if col_idx is None or col_idx >= len(cells):
            return 0
        cell = cells[col_idx]
        # Try link text first (appearances often wrapped in <a>)
        raw = cell.css("a::text").get("").strip()
        if not raw:
            raw = cell.css("::text").get("").strip()
        if not raw or raw == "-":
            return 0
        try:
            return int(raw)
        except ValueError:
            return 0

    @staticmethod
    def _extract_cell_minutes(cells, col_idx):
        """Extract minutes played from a cell, handling '2.450', \"2'450\" etc."""
        if col_idx is None or col_idx >= len(cells):
            return 0
        cell = cells[col_idx]
        raw = cell.css("a::text").get("").strip()
        if not raw:
            raw = cell.css("::text").get("").strip()
        if not raw or raw == "-":
            return 0
        try:
            cleaned = raw.replace(".", "").replace("'", "").replace(",", "")
            return int(cleaned)
        except (ValueError, AttributeError):
            return 0
