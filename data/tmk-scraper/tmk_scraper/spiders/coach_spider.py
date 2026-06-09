import scrapy
from scrapy.http import Request


class CoachSpider(scrapy.Spider):
    """Scrape current coaching staff from the Transfermarkt `mitarbeiter`
    page (German "staff" — head coach + assistants + admin).

    Phase 3a note: the historical-coach URLs (`/trainer/verein/` = "coach
    page", `/trainerhistorie/verein/` = "coach history") both return 404 on
    TM as of 2026-06-07; only the current-staff page `/mitarbeiter/verein/`
    resolves. So this spider yields the *current* coaching staff (head coach
    + assistants + fitness coach + admin staff), not a full historical list.

    Phase 3a R2 fills the historical gap differently: per-season manager data
    is extracted from the `platzierungen` (standings) page, which carries
    "Manager" as a column on every season row. See `platzierungen_spider.py`
    + `derive_coach_trophies.py`.
    """

    name = "coach"
    allowed_domains = ["transfermarkt.com", "api.scraperapi.com"]

    def __init__(self, season="2024", *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.season = season

    custom_settings = {
        "HTTPERROR_ALLOWED_CODES": [404],
    }

    def start_requests(self):
        target = "https://www.transfermarkt.com/hapoel-beer-sheva/mitarbeiter/verein/2976"
        use_scraperapi = self.settings.getbool("USE_SCRAPERAPI", False)
        api_key = self.settings.get("SCRAPERAPI_KEY")
        url = (
            f"http://api.scraperapi.com/?api_key={api_key}&url={target}&country_code=us&render=false"
            if use_scraperapi else target
        )
        yield Request(url=url, callback=self.parse)

    def parse(self, response: scrapy.http.Response, **kwargs):
        if response.status == 404:
            self.logger.warning("Mitarbeiter page not available (404)")
            return

        count = 0
        # The page renders one staff member per top-level <tr> inside the responsive table.
        # Each row's first <td> holds an inline-table with image + name (row 1) + role (row 2);
        # the remaining cells (all `td.zentriert`) carry age, appointed date, contract-expires,
        # and an optional last-club cell. The `Contract expires` cell is "-" when open-ended.
        for row in response.css("div.responsive-table table > tbody > tr"):
            name_link = row.css("td.hauptlink a")
            if not name_link:
                continue
            name = name_link.css("::text").get("").strip()
            profile_url = name_link.attrib.get("href", "")
            coach_id = profile_url.rstrip("/").split("/")[-1] if profile_url else ""
            if not name or not coach_id:
                continue

            role = row.css("td:first-child table tr:nth-child(2) td::text").get("").strip()
            centered = [c.strip() for c in row.css("td.zentriert::text").getall() if c.strip()]
            # Centered cells in order: [age, appointed, contract_expires, (optional last_club_marker)]
            appointed = centered[1] if len(centered) > 1 else ""
            contract_expires = centered[2] if len(centered) > 2 else ""
            if contract_expires in {"-", ""}:
                contract_expires = ""

            count += 1
            yield {
                "id": coach_id,
                "name": name,
                "role": role,
                "tenure_start": appointed,
                "tenure_end": contract_expires,
                # The mitarbeiter page does not surface per-tenure match records; zero them
                # out so the downstream pipeline can still validate against the Coach schema.
                # Historical match records would come from a separate Phase 3b source.
                "matches": 0,
                "wins": 0,
                "draws": 0,
                "losses": 0,
                "ppm": "",
            }

        self.logger.info("Scraped %d staff entries", count)
