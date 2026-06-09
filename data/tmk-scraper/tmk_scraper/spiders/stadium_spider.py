import re

import scrapy


class StadiumSpider(scrapy.Spider):
    """Scrape stadium information from the Transfermarkt `stadion` page
    (German "stadium")."""

    name = "stadium"
    allowed_domains = ["transfermarkt.com"]

    def __init__(self, season="2024", *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.season = season
        self.base_url = "https://www.transfermarkt.com"
        self.start_urls = [
            f"{self.base_url}/hapoel-beer-sheva/stadion/verein/2976"
        ]

    def parse(self, response: scrapy.http.Response, **kwargs):
        data = {
            "name": "",
            "capacity": None,
            "surface": "",
            "opening_year": "",
            "address": "",
            "city": "",
        }

        # Parse profilheader table (th: label, td: value)
        for row in response.css("table.profilheader tr"):
            label = row.css("th::text").get("").strip().rstrip(":").lower()
            value = row.css("td::text").get("").strip()

            if not label or not value:
                continue

            if "name of stadium" in label:
                data["name"] = value
            elif "total capacity" in label or label == "capacity":
                cleaned = re.sub(r"[^\d]", "", value)
                data["capacity"] = int(cleaned) if cleaned else None
            elif label == "seats":
                # Use seats as capacity if total capacity not found
                if data["capacity"] is None:
                    cleaned = re.sub(r"[^\d]", "", value)
                    data["capacity"] = int(cleaned) if cleaned else None
            elif "surface" in label:
                data["surface"] = value
            elif "built" in label or "opening" in label:
                data["opening_year"] = value
            elif "address" in label:
                # Address is sometimes the stadium name repeated; skip if identical
                if value != data["name"]:
                    data["address"] = value

        # If address was skipped because it matched name, try to get street address
        # from subsequent td-only rows
        if not data["address"]:
            address_parts = []
            in_address = False
            for row in response.css("table.profilheader tr"):
                label = row.css("th::text").get("").strip().rstrip(":").lower()
                value = row.css("td::text").get("").strip()
                if "address" in label:
                    in_address = True
                    continue
                if in_address and not label and value:
                    address_parts.append(value.replace("\xa0", " ").strip())
                elif in_address and label:
                    break
            if address_parts:
                data["address"] = ", ".join(address_parts)

        # Fallback: get name from content-box-headline
        if not data["name"]:
            for h2 in response.css("h2.content-box-headline::text").getall():
                h2 = h2.strip()
                if h2 and h2 not in ("Info", "Contact", "Pricing information",
                                      "Home ground for...:"):
                    data["name"] = h2
                    break

        if data["name"]:
            yield data
            self.logger.info("Scraped stadium: %s (capacity: %s)", data["name"], data["capacity"])
        else:
            self.logger.warning("Could not find stadium information")
