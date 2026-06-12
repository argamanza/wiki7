import scrapy


class SquadSpider(scrapy.Spider):
    name = "squad"
    allowed_domains = ["transfermarkt.com"]

    def __init__(self, season="2024", *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.season = season
        self.players_scraped = 0
        self.base_url = "https://www.transfermarkt.com"
        self.start_urls = [
            f"{self.base_url}/hapoel-beer-sheva/kader/verein/2976/saison_id/{self.season}"
        ]
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

        # Now follow to the loaned players page
        yield scrapy.Request(
            url=self.loan_url,
            callback=self.parse_loans
        )

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
