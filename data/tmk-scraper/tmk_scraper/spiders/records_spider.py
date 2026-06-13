import scrapy


class RecordsSpider(scrapy.Spider):
    """Scrape transfer records from the Transfermarkt `transferrekorde`
    page (German "transfer records").

    Phase 3a R2 finding (audited 2026-06-09): TM **no longer exposes a
    separate "Record departures" page** — `/teuerstetransfers/` ("most
    expensive transfers"), `/transfererloese/` ("transfer revenues"), and
    `?sa=1` query variants all 404 or fall back to the arrivals view. The
    single `transferrekorde/verein/2976` URL serves arrivals only. Each
    emitted row carries `direction: "in"` so downstream templates and
    Cargo queries can stay forward-compatible: when a future pipeline
    derives departures from `alletransfers` (filter by `direction: "out"`
    + sort by fee), it can populate `direction: "out"` rows into the same
    shape and the records page rendering doesn't need to change.

    Inventory recommendation moved: "scrape both tabs" is replaced with
    "derive departure records from `alletransfers`" — landing as part of
    the records-page rendering step instead of as a spider change.
    """

    name = "records"
    # §6 high #7 fix (2026-06-12 review): route through ScraperAPI.
    allowed_domains = ["transfermarkt.com", "api.scraperapi.com"]

    def __init__(self, season="2024", *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.season = season
        self.base_url = "https://www.transfermarkt.com"
        self.target_url = f"{self.base_url}/hapoel-beer-sheva/transferrekorde/verein/2976"

    def start_requests(self):
        from tmk_scraper.scraperapi_proxy import validate_key, wrap

        use_scraperapi = self.settings.getbool("USE_SCRAPERAPI", False)
        api_key = validate_key(self.settings.get("SCRAPERAPI_KEY")) if use_scraperapi else None
        url = wrap(self.target_url, api_key) if use_scraperapi else self.target_url
        yield scrapy.Request(url=url, callback=self.parse)

    def parse(self, response: scrapy.http.Response, **kwargs):
        count = 0

        # Page title contains the category (e.g., "Record arrivals")
        page_title = response.css("title::text").get("").split("|")[0].strip()
        category = page_title.replace("Hapoel Beer Sheva - ", "").strip() or "Record arrivals"

        for row in response.css("table.items > tbody > tr"):
            # Only process data rows (odd/even)
            row_class = row.attrib.get("class", "")
            if "odd" not in row_class and "even" not in row_class:
                continue

            # Find player name — look for links that point to player profiles (contain /profil/)
            player_links = row.css("td.hauptlink a[href*='/profil/']")
            if not player_links:
                # Fallback: first hauptlink that isn't a club link
                player_links = row.css("td.hauptlink a")

            if not player_links:
                continue

            player_name = player_links[0].css("::text").get("").strip()
            profile_url = player_links[0].attrib.get("href", "")
            player_id = profile_url.strip().split("/")[-1] if "/profil/" in profile_url else ""

            # Get the transfer fee (typically in td.rechts)
            rechts = row.css("td.rechts a::text, td.rechts::text").getall()
            rechts = [r.strip() for r in rechts if r.strip()]
            value = rechts[0] if rechts else ""

            if not value:
                zentriert = row.css("td.zentriert::text").getall()
                zentriert = [z.strip() for z in zentriert if z.strip()]
                value = zentriert[-1] if zentriert else ""

            if player_name:
                count += 1
                yield {
                    "category": category,
                    "direction": "in",
                    "player_name": player_name,
                    "player_id": player_id,
                    "value": value,
                }

        self.logger.info("Scraped %d record arrivals", count)
