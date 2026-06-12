import scrapy


class SquadSpider(scrapy.Spider):
    name = "squad"
    # §6 high #7 fix (2026-06-12 review): squad spider used to hit TM
    # directly. Now allowed_domains includes the ScraperAPI host so
    # start_requests() can route through the proxy when enabled.
    allowed_domains = ["transfermarkt.com", "api.scraperapi.com"]

    def __init__(self, season="2024", *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.season = season
        self.players_scraped = 0
        self.base_url = "https://www.transfermarkt.com"
        self.target_url = (
            f"{self.base_url}/hapoel-beer-sheva/kader/verein/2976/saison_id/{self.season}"
        )
        # §6 high #8 fix (2026-06-12 review): the loan-page URL must carry
        # `/saison_id/{year}` too. Without it, TM serves TODAY's loanees
        # regardless of which historical season we asked for, so a 2024
        # snapshot of loaned-out players got stamped into every historical
        # season's roster — contaminating ~70 squads with anachronistic
        # players. Empirically TM accepts the saison_id parameter on the
        # leihspieler URL (probed 2026-06-12); for pre-modern seasons it
        # simply yields no rows, which is the correct behavior.
        self.loan_url = (
            f"{self.base_url}/hapoel-beer-sheva/leihspieler/verein/2976"
            f"/saison_id/{self.season}"
        )

    def start_requests(self):
        from tmk_scraper.scraperapi_proxy import validate_key, wrap

        use_scraperapi = self.settings.getbool("USE_SCRAPERAPI", False)
        api_key = validate_key(self.settings.get("SCRAPERAPI_KEY")) if use_scraperapi else None
        url = wrap(self.target_url, api_key) if use_scraperapi else self.target_url
        # Stash for the loan-page fetch in parse() so it doesn't have to
        # re-validate the key from settings.
        yield scrapy.Request(url=url, callback=self.parse, meta={
            "use_scraperapi": use_scraperapi, "api_key": api_key,
        })

    def parse(self, response: scrapy.http.Response, **kwargs):
        rows = response.css("table.items > tbody > tr")

        for row in rows:
            number = row.css("div.rn_nummer::text").get()
            name = row.css("td.hauptlink a::text").get()
            link = row.css("td.hauptlink a::attr(href)").get()

            if name and link:
                self.players_scraped += 1
                # Phase 3a R2: TM's modern squad page does NOT expose a captain
                # marker (audited 2026-06-09 against 2015/16 and 1985/86 kader
                # fixtures + the current squad page — no `kapitaenicon-*`
                # classes, no "Captain" label, no "C" badge). The is_captain
                # field on the Player model is therefore populated *outside*
                # the squad spider: the latest match-report's graphic_lineups
                # carries a per-match captain bool already, which the import
                # step can use to derive "current captain". Hand-curation is
                # the fallback. Default False here.
                yield {
                    "name_english": name.strip(),
                    "profile_url": response.urljoin(link.strip()),
                    "number": number.strip() if number else "-",
                    "season": self.season,
                    "loaned": False,
                    "is_captain": False,
                }

        # Now follow to the loaned players page. Wrap the same way the
        # initial squad request was wrapped — §6 high #7 fix.
        from tmk_scraper.scraperapi_proxy import wrap as _proxy_wrap
        use_scraperapi = response.meta.get("use_scraperapi", False)
        api_key = response.meta.get("api_key")
        loan_url = (
            _proxy_wrap(self.loan_url, api_key) if use_scraperapi else self.loan_url
        )
        yield scrapy.Request(url=loan_url, callback=self.parse_loans)

    def parse_loans(self, response: scrapy.http.Response):
        rows = response.css("table.items > tbody > tr")

        for row in rows:
            name = row.css("td > table.inline-table td.hauptlink a::text").get()
            link = row.css("td > table.inline-table td.hauptlink a::attr(href)").get()

            if name and link:
                self.players_scraped += 1
                yield {
                    "name_english": name.strip(),
                    "profile_url": response.urljoin(link.strip()),
                    "number": "-",  # loan page doesn't include jersey number
                    "season": self.season,
                    "loaned": True,
                    # Loaned-out players can't be the active captain of HBS.
                    "is_captain": False,
                }

        self.logger.info(f"Scraped total {self.players_scraped} players for season {self.season}")
