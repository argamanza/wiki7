import scrapy


class HonoursSpider(scrapy.Spider):
    """Scrape club honours/trophies from the Transfermarkt `erfolge` page
    (German "successes / honours")."""

    name = "honours"
    # §6 high #7 fix (2026-06-12 review): route through ScraperAPI.
    allowed_domains = ["transfermarkt.com", "api.scraperapi.com"]

    def __init__(self, season="2024", *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.season = season
        self.base_url = "https://www.transfermarkt.com"
        self.target_url = f"{self.base_url}/hapoel-beer-sheva/erfolge/verein/2976"

    def start_requests(self):
        from tmk_scraper.scraperapi_proxy import validate_key, wrap

        use_scraperapi = self.settings.getbool("USE_SCRAPERAPI", False)
        api_key = validate_key(self.settings.get("SCRAPERAPI_KEY")) if use_scraperapi else None
        url = wrap(self.target_url, api_key) if use_scraperapi else self.target_url
        yield scrapy.Request(url=url, callback=self.parse)

    # Phase 3a R2 fix: the erfolge page no longer uses table.items rows for
    # each trophy. Each trophy is a pair of:
    #   div.erfolg_bild_box   — contains the trophy <img title="Israeli Champion">
    #   div.erfolg_infotext_box — contains comma-separated seasons ("25/26, 17/18, ...")
    # The pair sits inside a wrapper div which TM keys by row index. We walk
    # the bild_box → next infotext_box pairs and yield one entry per trophy.

    def parse(self, response: scrapy.http.Response, **kwargs):
        count = 0

        bild_boxes = response.css("div.erfolg_bild_box")
        info_boxes = response.css("div.erfolg_infotext_box")

        # Pair them up by document order. The two CSS selectors return lists
        # in DOM order; for a well-formed page they line up 1-to-1.
        for bild, info in zip(bild_boxes, info_boxes):
            img = bild.css("img")
            competition = (
                img.attrib.get("title", "")
                or img.attrib.get("alt", "")
            ).strip()
            if not competition:
                continue

            # The seasons cell is one logical run of text fragments separated
            # by ",&nbsp;" but TM emits each season + the comma as separate
            # text nodes wrapped in tons of whitespace. Flatten to one string
            # via xpath string(), then split on commas. Each "25/26" or
            # "75/76" piece survives.
            flat = info.xpath("string()").get("") or ""
            flat = flat.replace("\xa0", " ")
            seasons = [
                piece.strip()
                for piece in flat.split(",")
                if piece.strip()
            ]

            if not seasons:
                continue

            count += 1
            yield {
                "competition": competition,
                "achievement": "Winner",
                "seasons": seasons,
            }

        self.logger.info("Scraped %d honour entries", count)
