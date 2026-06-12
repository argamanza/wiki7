import scrapy
import json
from scrapy.http import Request
from datetime import datetime

from tmk_scraper.scraperapi_proxy import redact


class PlayerSpider(scrapy.Spider):
    name = "player"
    allowed_domains = ["transfermarkt.com", "api.scraperapi.com"]

    def __init__(self, season="2024", *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.season = season

    async def start(self):
        from tmk_scraper.scraperapi_proxy import validate_key, wrap

        use_scraperapi = self.settings.getbool("USE_SCRAPERAPI", False)
        api_key = validate_key(self.settings.get("SCRAPERAPI_KEY")) if use_scraperapi else None

        # Load player URLs from output of squad spider (season-specific dir)
        squad_path = f"output/{self.season}/squad.json"
        with open(squad_path, encoding="utf-8") as f:
            players = json.load(f)

        for player in players:
            target_url = player["profile_url"]
            url = wrap(target_url, api_key) if use_scraperapi else target_url

            yield Request(
                url=url,
                callback=self.parse_profile,
                meta={
                    "player_data": player,
                    "use_scraperapi": use_scraperapi,
                    "api_key": api_key,
                    # §6 ② fix: thread target_url for non-leaking persistence.
                    "target_url": target_url,
                },
            )

    def parse_profile(self, response):
        player = response.meta["player_data"]
        use_scraperapi = response.meta["use_scraperapi"]
        api_key = response.meta["api_key"]

        # Facts
        keys = response.css("div.spielerdatenundfakten span.info-table__content--regular::text").getall()
        values = response.css("div.spielerdatenundfakten span.info-table__content--bold").xpath("string()").getall()
        facts = {
            k.strip().rstrip(":"): v.strip().replace("\xa0", " ") for k, v in zip(keys, values) if v
        }

        # Positionד
        main_position = response.css("div.detail-position__box dd.detail-position__position::text").get()
        position_divs = response.css("div.detail-position__box div.detail-position__position")
        other_positions = position_divs.css("dd.detail-position__position::text").getall() if len(position_divs) > 0 else []

        # Extract player ID from profile URL (last numeric segment)
        player_id = player["profile_url"].split("/")[-1]

        from tmk_scraper.scraperapi_proxy import wrap

        # Construct AJAX request for market value history
        mv_target_url = f"https://www.transfermarkt.com/ceapi/marketValueDevelopment/graph/{player_id}"
        mv_url = wrap(mv_target_url, api_key) if use_scraperapi else mv_target_url

        # Store interim player object in meta and call market value endpoint
        meta = {
            "player_data": {
                **player,
                # §6 ② fix (2026-06-12 review): persist the TM target URL,
                # NOT response.url — the latter carries the ScraperAPI
                # api_key when proxied, leaking it into output records on
                # disk. The target was threaded through `target_url` meta
                # from the original start() call.
                # Reviewer-pass (2026-06-13): wrap the fallback in redact()
                # so even if `target_url` is somehow missing from meta, the
                # key is still scrubbed before it hits disk.
                "profile_scraped_from": response.meta.get("target_url") or redact(response.url),
                "facts": facts,
                "positions": {
                    "main": main_position.strip() if main_position else None,
                    "other": [pos.strip() for pos in other_positions if pos.strip()]
                }
            },
            "player_id": player_id,
            "use_scraperapi": use_scraperapi,
            "api_key": api_key,
        }

        yield Request(url=mv_url, callback=self.parse_market_value, meta=meta)

    _MV_DATE_FORMATS = ["%b %d, %Y", "%d/%m/%Y"]

    def _parse_mv_date(self, raw: str) -> str | None:
        """Try multiple date formats Transfermarkt has used over time."""
        for fmt in self._MV_DATE_FORMATS:
            try:
                return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
            except (ValueError, TypeError):
                continue
        return None

    def parse_market_value(self, response):
        player = response.meta["player_data"]
        player_id = response.meta["player_id"]
        use_scraperapi = response.meta["use_scraperapi"]
        api_key = response.meta["api_key"]

        try:
            data = json.loads(response.text)
            history = []
            for p in data.get("list", []):
                parsed_date = self._parse_mv_date(p.get("datum_mw", ""))
                if parsed_date:
                    history.append({
                        "date": parsed_date,
                        "value": p["mw"],
                        "team": p["verein"],
                    })
                else:
                    self.logger.warning(f"Skipping market value entry with unparseable date: {p.get('datum_mw')}")
            player["market_value_history"] = sorted(history, key=lambda x: x["date"])
        except Exception as e:
            self.logger.warning(f"Failed to parse market value history: {e}")
            player["market_value_history"] = []

        from tmk_scraper.scraperapi_proxy import wrap

        # Proceed to transfer history
        transfer_target_url = f"https://www.transfermarkt.com/ceapi/transferHistory/list/{player_id}"
        transfer_url = wrap(transfer_target_url, api_key) if use_scraperapi else transfer_target_url

        yield Request(url=transfer_url, callback=self.parse_transfer_history, meta={"player_data": player})

    def parse_transfer_history(self, response):
        player = response.meta["player_data"]

        try:
            data = json.loads(response.text)
            history = [
                {
                    "season": t.get("season"),
                    "date": t.get("dateUnformatted"),
                    "from": t.get("from", {}).get("clubName"),
                    "to": t.get("to", {}).get("clubName"),
                    "fee": t.get("fee")
                }
                for t in data.get("transfers", [])
            ]
            player["transfers"] = sorted(history, key=lambda x: x["date"])
        except Exception as e:
            self.logger.warning(f"Failed to parse transfer history: {e}")
            player["transfers"] = []

        yield player
