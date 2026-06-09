import scrapy


class HonoursSpider(scrapy.Spider):
    """Scrape club honours/trophies from the Transfermarkt `erfolge` page
    (German "successes / honours")."""

    name = "honours"
    allowed_domains = ["transfermarkt.com"]

    def __init__(self, season="2024", *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.season = season
        self.base_url = "https://www.transfermarkt.com"
        self.start_urls = [
            f"{self.base_url}/hapoel-beer-sheva/erfolge/verein/2976"
        ]

    def parse(self, response: scrapy.http.Response, **kwargs):
        count = 0

        # Honours are listed in success boxes / tables
        # Each achievement has a header (competition) and detail rows (seasons)
        for box in response.css("div.box"):
            header = box.css("div.table-header::text, h2::text").get("")
            if not header.strip():
                continue

            competition = header.strip()

            # Look for achievement entries within the box
            for row in box.css("table.items tr, div.erfolg_infobox_wrapper"):
                # Try to extract achievement type and seasons
                achievement = row.css("td.hauptlink a::text, div.erfolg_titel::text").get("")
                if not achievement:
                    achievement = row.css("td.hauptlink::text").get("")
                achievement = achievement.strip()
                if not achievement:
                    continue

                seasons = row.css("td.zentriert::text, div.erfolg_saison::text").getall()
                seasons = [s.strip() for s in seasons if s.strip()]

                if seasons:
                    count += 1
                    yield {
                        "competition": competition,
                        "achievement": achievement,
                        "seasons": seasons,
                    }

        # Fallback: parse from success-data badges in the header
        if count == 0:
            for badge in response.css("a.data-header__success-data"):
                title = badge.attrib.get("title", "").strip()
                number = badge.css("span.data-header__success-number::text").get("").strip()
                if title:
                    count += 1
                    yield {
                        "competition": title,
                        "achievement": "Winner",
                        "seasons": [f"{number}x"] if number else [],
                    }

        self.logger.info("Scraped %d honour entries", count)
