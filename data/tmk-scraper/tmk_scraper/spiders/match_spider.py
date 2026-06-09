import scrapy
import json
import re
from scrapy.http import Request


class MatchSpider(scrapy.Spider):
    name = "match"
    allowed_domains = ["transfermarkt.com", "api.scraperapi.com"]

    def __init__(self, season="2024", *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.season = season

    async def start(self):
        use_scraperapi = self.settings.getbool("USE_SCRAPERAPI", False)
        api_key = self.settings.get("SCRAPERAPI_KEY")

        # Load fixtures from season-specific dir
        fixtures_path = f"output/{self.season}/fixtures.json"
        with open(fixtures_path, encoding="utf-8") as f:
            fixtures = json.load(f)

        for match in fixtures:
            target_url = match["match_report_url"]
            url = (
                f"http://api.scraperapi.com/?api_key={api_key}&url={target_url}&country_code=us&render=false"
                if use_scraperapi else target_url
            )
            yield Request(
                url=url,
                callback=self.parse_match_report,
                meta={"match_data": match}
            )

    def parse_match_report(self, response):
        match = response.meta["match_data"]

        graphic_lineups = self.extract_from_graphic_field(response)
        table_lineups = self.extract_from_simple_table(response)
        goals = self.extract_goals(response)
        penalties = self.extract_penalties(response)
        halbzeit_text = self.extract_halbzeit_text(response)

        data = {
            "season": self.season,
            **match,
            "report_scraped_from": response.url,
            "home_lineup": graphic_lineups.get("home") or table_lineups.get("home"),
            "away_lineup": graphic_lineups.get("away") or table_lineups.get("away"),
            "goals": goals,
            "substitutions": self.extract_substitutions(response),
            "cards": self.extract_cards(response),
            "manager_sanctions": self.extract_manager_sanctions(response),
            # Phase 3a R2 additions: match-detail fields surfaced inline in the
            # match-report metadata box (`.sb-zusatzinfos`) or derived from the
            # score / goal markers.
            "halftime_score": self.parse_halftime_from_halbzeit(halbzeit_text),
            "stadium": self.extract_stadium(response),
            "referee": self.extract_referee(response),
        }

        if penalties:
            data["penalties"] = penalties

        # Knockout football progression: a draw after 90' (+ stoppage time)
        # goes to **extra time** (2x15min). A draw after 120' goes to
        # **penalties**. So the AET signal is: TM's explicit "AET" / "n.V."
        # marker in .sb-halbzeit (strongest), OR penalties exist (you can't
        # reach a shootout without playing ET first), OR any goal scored
        # after minute 90 (the "scored in ET" path TM marks with minutes
        # 91-120). The pre-R2 implementation relied only on the third
        # signal which misses the explicit marker case and would have
        # ignored the marker for halftime extraction too. See
        # docs/research/0002-transfermarkt-data-surface.md §3.2.
        data["aet"] = (
            self.is_aet_marker(halbzeit_text)
            or bool(penalties)
            or any((g.get("minute") or 0) > 90 for g in goals)
        )

        # Phase 3a R2 referee-team placeholder fields. TM only exposes the main
        # referee on its match-report layout (audited 2026-06-09 against 2024/25
        # + 2015/16 fixtures). The other 5 slots stay null on the data file so
        # reviewers + a future IFA spider can fill them later without schema
        # churn. See docs/research/0002-transfermarkt-data-surface.md §3.2.
        data["assistant_referee_1"] = None
        data["assistant_referee_2"] = None
        data["fourth_official"] = None
        data["var_referee"] = None
        data["var_assistant"] = None

        yield data

    # TM markers that indicate the match did NOT end at 90' — for those, the
    # `.sb-halbzeit` slot carries the marker instead of the halftime score.
    # English-localised: "AET" (after extra time), "AP" (after penalties).
    # German-localised (in case ScraperAPI ever serves us through DE): "n.V."
    # (nach Verlängerung), "i.E." (im Elfmeterschiessen). Case-insensitive
    # because TM has been observed serving both "AET" and "aet" on different
    # match shapes.
    _AET_MARKERS = frozenset({"AET", "AP", "N.V.", "I.E."})

    @staticmethod
    def extract_halbzeit_text(response) -> str | None:
        """Return the raw text content of the FIRST `.sb-endstand .sb-halbzeit`
        slot, or None if absent. TM duplicates the scoreboard markup on the
        page (once in the main header, once in a compact summary box), so we
        take only the first occurrence.

        For regulation matches this looks like "(0:1)" — parentheses around
        the halftime score. For AET / penalty-shootout matches this looks
        like "AET" or "AP" (the halftime slot is repurposed to flag that the
        match didn't end at 90').
        """
        first = response.css(".sb-endstand .sb-halbzeit").get()
        if not first:
            return None
        from scrapy.selector import Selector
        text = "".join(Selector(text=first).css("::text").getall())
        return text.strip() or None

    @classmethod
    def parse_halftime_from_halbzeit(cls, halbzeit_text: str | None) -> str | None:
        """If `halbzeit_text` is a halftime score, return it as "0:1". If it
        carries an AET / penalties marker instead, return None — that match
        doesn't expose its halftime score on TM's English-localised view.
        """
        if not halbzeit_text:
            return None
        if cls.is_aet_marker(halbzeit_text):
            return None
        cleaned = halbzeit_text.replace("(", "").replace(")", "").strip()
        # Sanity check: a real halftime score matches digits-colon-digits. If
        # TM puts anything else here (a future format change), bail to None
        # rather than emit garbage.
        if not cleaned or ":" not in cleaned:
            return None
        left, _, right = cleaned.partition(":")
        if not (left.strip().isdigit() and right.strip().isdigit()):
            return None
        return cleaned

    @classmethod
    def is_aet_marker(cls, halbzeit_text: str | None) -> bool:
        """True when the halbzeit slot carries TM's AET / penalties marker
        (the match went past 90 minutes). Case-insensitive."""
        if not halbzeit_text:
            return False
        return halbzeit_text.upper().strip() in cls._AET_MARKERS

    @staticmethod
    def extract_stadium(response) -> str | None:
        """Stadium name from `.sb-zusatzinfos a[href*='/stadion/']`."""
        stadium = response.css('.sb-zusatzinfos a[href*="/stadion/"]::text').get()
        return stadium.strip() if stadium else None

    @staticmethod
    def extract_referee(response) -> str | None:
        """Main referee from `.sb-zusatzinfos a[href*='/schiedsrichter/']`.

        Falls back to the `title` attribute when the inline text is empty (TM
        sometimes wraps the referee name in an `<a>` whose text comes from a
        non-default rendering path).
        """
        link = response.css('.sb-zusatzinfos a[href*="/schiedsrichter/"]')
        if not link:
            return None
        name = link.css("::text").get() or link.attrib.get("title", "")
        return name.strip() or None

    def extract_goals(self, response):
        goals = []
        for li in response.css("#sb-tore li"):
            sprite_style = li.css(".sb-aktion-uhr span::attr(style)").get()
            extra_text = li.css(".sb-aktion-uhr span::text").re_first(r"\+(\d+)")
            pos = self.parse_background_position(sprite_style) if sprite_style else None

            goals.append({
                "sprite_position": f"{pos[0]}x{pos[1]}" if pos else None,
                "minute": self.estimate_minute_from_sprite(pos) if pos else None,
                "extra_time": int(extra_text) if extra_text else None,
                "score": li.css(".sb-aktion-spielstand b::text").get(),
                "scorer": li.css(".sb-aktion-aktion a::text").get(),
                "assist": self.extract_assist(li),
                "team": li.css(".sb-aktion-wappen img::attr(alt)").get(),
                "details": " ".join(li.css(".sb-aktion-aktion::text").getall()).strip(),
            })
        return goals

    def extract_assist(self, li):
        links = li.css(".sb-aktion-aktion a::text").getall()
        return links[1] if len(links) > 1 else None

    def parse_background_position(self, style):
        try:
            parts = re.findall(r"-?\d+", style)
            return int(parts[0]), int(parts[1]) if len(parts) == 2 else (None, None)
        except Exception:
            return None, None

    def estimate_minute_from_sprite(self, pos):
        if not pos or None in pos:
            return None
        x, y = map(abs, pos)
        return ((y // 36) * 10 + (x // 36) + 1) if x < 360 and y < 432 else None

    def extract_from_simple_table(self, response):
        result = {}
        for box in response.css("div.aufstellung-box, div.large-6.columns"):
            team_name = box.css(".aufstellung-unterueberschrift-mannschaft a::text").get()
            if not team_name:
                continue
            team_key = self.resolve_team_key(team_name, response)
            players = {}
            for row in box.css("table tr"):
                pos = row.css("td b::text").get()
                names = row.css("td:nth-child(2) a::text").getall()
                if pos:
                    players[pos.strip().lower()] = names
                elif row.css("td:nth-child(1)::text").re(".*manager.*"):
                    players["manager"] = row.css("td:nth-child(2) a::text").get()
            result[team_key] = players
        return result

    def extract_from_graphic_field(self, response):
        result = {}
        for box in response.css("div.box > div.large-6.columns"):
            team_name = box.css(".aufstellung-unterueberschrift-mannschaft a::text").get()
            if not team_name:
                continue
            team_key = self.resolve_team_key(team_name, response)
            players = [
                {
                    "name_english": p.css(".formation-number-name a::text").get(default="").strip(),
                    "number": p.css(".tm-shirt-number::text").get(default="").strip(),
                    "captain": bool(p.css(".kapitaenicon-formation")),
                }
                for p in box.css(".formation-player-container")
                if p.css(".formation-number-name a::text")
            ]
            if players:
                result[team_key] = players
        return result

    def resolve_team_key(self, team_name, response):
        # Transfermarkt renders home-first on the match-report page (left column = home,
        # right column = away). The fixture dict we ride along on doesn't carry the home/away
        # team names (only venue=H/A + opponent), so we just track box order: first
        # `aufstellung-unterueberschrift-mannschaft` we see is home, the second is away.
        if "home" not in response.meta:
            response.meta["home"] = team_name.lower()
            return "home"
        return "away"

    def extract_penalties(self, response):
        items = response.css("#sb-elfmeterscheissen li")
        if not items:
            return None

        penalties = []
        for li in items:
            team = "home" if "sb-aktion-heim" in li.attrib.get("class", "") else "away"
            result = li.css(".sb-aktion-uhr span::attr(title)").get()
            score = li.css(".sb-aktion-spielstand b::text").get()
            player = li.css(".sb-aktion-aktion a::text").get()
            club = li.css(".sb-aktion-wappen a::attr(title)").get()

            penalties.append({
                "team": team,
                "result": result,
                "score": score,
                "player": player,
                "club": club
            })
        return penalties

    def extract_substitutions(self, response):
        subs = []
        for li in response.css("#sb-wechsel li"):
            team = "home" if "sb-aktion-heim" in li.attrib.get("class", "") else "away"
            style = li.css(".sb-aktion-uhr span::attr(style)").get()
            extra_text = li.css(".sb-aktion-uhr span::text").re_first(r"\+(\d+)")
            pos = self.parse_background_position(style) if style else None

            subs.append({
                "team": team,
                "minute": self.estimate_minute_from_sprite(pos) if pos else None,
                "extra_time": int(extra_text) if extra_text else None,
                "sprite_position": f"{pos[0]}x{pos[1]}" if pos else None,
                "player_in": li.css(".sb-aktion-wechsel-ein a::text").get(),
                "player_out": li.css(".sb-aktion-wechsel-aus a::text").get(),
                "reason": li.css(".sb-aktion-wechsel-aus span.hide-for-small::text").re_first(r"[A-Za-z]+")
            })
        return subs

    def extract_cards(self, response):
        cards = []
        for li in response.css("#sb-karten li"):
            team = "home" if "sb-aktion-heim" in li.attrib.get("class", "") else "away"
            style = li.css(".sb-aktion-uhr span::attr(style)").get()
            extra_text = li.css(".sb-aktion-uhr span::text").re_first(r"\+(\d+)")
            pos = self.parse_background_position(style) if style else None

            card_class = li.css(".sb-aktion-spielstand span::attr(class)").get()
            card_type = None
            if card_class:
                if "sb-gelbrot" in card_class:
                    card_type = "second_yellow"
                elif "sb-rot" in card_class:
                    card_type = "red"
                elif "sb-gelb" in card_class:
                    card_type = "yellow"

            cards.append({
                "team": team,
                "minute": self.estimate_minute_from_sprite(pos) if pos else None,
                "extra_time": int(extra_text) if extra_text else None,
                "sprite_position": f"{pos[0]}x{pos[1]}" if pos else None,
                "player": li.css(".sb-aktion-aktion a::text").get(),
                "card": card_type,
                "reason": li.css(".sb-aktion-aktion::text").re_first(r",\s*(.+)")
            })
        return cards

    def extract_manager_sanctions(self, response):
        sanctions = []
        for li in response.css("#sb-sanktionen li"):
            team = "home" if "sb-aktion-heim" in li.attrib.get("class", "") else "away"
            style = li.css(".sb-aktion-uhr span::attr(style)").get()
            extra_text = li.css(".sb-aktion-uhr span::text").re_first(r"\+(\d+)")
            pos = self.parse_background_position(style) if style else None

            sanction_class = li.css(".sb-aktion-spielstand span::attr(class)").get()
            sanction = None
            if sanction_class:
                if "sb-rot" in sanction_class:
                    sanction = "red"
                elif "sb-gelb" in sanction_class:
                    sanction = "yellow"

            sanctions.append({
                "team": team,
                "minute": self.estimate_minute_from_sprite(pos) if pos else None,
                "extra_time": int(extra_text) if extra_text else None,
                "sprite_position": f"{pos[0]}x{pos[1]}" if pos else None,
                "manager": li.css(".sb-aktion-aktion a::text").get(),
                "sanction": sanction
            })
        return sanctions
