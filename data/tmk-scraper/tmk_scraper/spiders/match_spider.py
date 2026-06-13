import scrapy
import json
import re
from scrapy.http import Request

from tmk_scraper.scraperapi_proxy import redact


# TM player profile / event links share two structural pieces inside the href:
# the URL slug (segment 1) which is a kebab-case full English name, and the
# numeric player ID following `/spieler/`. Examples observed on 2024/25:
#   lineup:  /niv-eliasi/profil/spieler/912586
#   event:   /ohad-almagor/leistungsdatendetails/spieler/933143/saison/...
# The slug is materially richer than the visible text on lineup entries,
# where TM only renders the surname (the formation diagram is space-tight).
# Capturing both lets us emit full names + a stable cross-page identity.
_TM_PLAYER_ID_RE = re.compile(r"/spieler/(\d+)")


def _parse_player_link(href: str | None) -> tuple[str | None, str | None]:
    """Return (name_english, tm_player_id) parsed from a TM player <a href>,
    or (None, None) when the href is missing / shaped unexpectedly.

    The slug-derived name loses non-ASCII accents (TM slug-normalizes
    `Hélder Lopes` → `helder-lopes`); the Wikidata + Wikipedia lookups
    downstream are accent-insensitive, so this is acceptable for v1.
    """
    if not href:
        return None, None
    parts = href.strip("/").split("/")
    if not parts:
        return None, None
    name_slug = parts[0]
    # Defensive — if TM ever changes link layout to put `spieler` or a club
    # alias first, treat as no-match rather than emit garbage.
    if name_slug.lower() in {"spieler", "verein", "team", "saison", "wettbewerb"}:
        return None, None
    name = " ".join(seg.capitalize() for seg in name_slug.split("-") if seg)
    m = _TM_PLAYER_ID_RE.search(href)
    tm_id = m.group(1) if m else None
    return name or None, tm_id


class MatchSpider(scrapy.Spider):
    name = "match"
    allowed_domains = ["transfermarkt.com", "api.scraperapi.com"]

    def __init__(self, season="2024", *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.season = season

    async def start(self):
        from tmk_scraper.scraperapi_proxy import validate_key, wrap

        use_scraperapi = self.settings.getbool("USE_SCRAPERAPI", False)
        api_key = validate_key(self.settings.get("SCRAPERAPI_KEY")) if use_scraperapi else None

        # Load fixtures from season-specific dir
        fixtures_path = f"output/{self.season}/fixtures.json"
        with open(fixtures_path, encoding="utf-8") as f:
            fixtures = json.load(f)

        for match in fixtures:
            target_url = match["match_report_url"]
            url = wrap(target_url, api_key) if use_scraperapi else target_url
            yield Request(
                url=url,
                callback=self.parse_match_report,
                # §6 ② fix: thread target_url through meta so the output
                # record can persist the TM URL instead of response.url
                # (which contains the proxy api_key).
                meta={"match_data": match, "target_url": target_url},
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
            # §6 ② fix (2026-06-12 review): persist the TM target URL, NOT
            # response.url — the latter contains the ScraperAPI api_key
            # when proxied, which leaks into ~70 seasons of output records.
            # Reviewer-pass (2026-06-13): wrap the fallback in redact()
            # so even if `target_url` is somehow missing from meta (caller
            # bug, mid-migration data), the key is still scrubbed before
            # it hits disk.
            "report_scraped_from": response.meta.get("target_url") or redact(response.url),
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

            scorer_a = li.css(".sb-aktion-aktion a")
            scorer_name = scorer_a[0].css("::text").get() if scorer_a else None
            scorer_id = _parse_player_link(scorer_a[0].attrib.get("href"))[1] if scorer_a else None
            assist_name, assist_id = self.extract_assist(li)

            goals.append({
                "sprite_position": f"{pos[0]}x{pos[1]}" if pos else None,
                "minute": self.estimate_minute_from_sprite(pos) if pos else None,
                "extra_time": int(extra_text) if extra_text else None,
                "score": li.css(".sb-aktion-spielstand b::text").get(),
                "scorer": scorer_name,
                "scorer_tm_id": scorer_id,
                "assist": assist_name,
                "assist_tm_id": assist_id,
                "team": li.css(".sb-aktion-wappen img::attr(alt)").get(),
                "details": " ".join(li.css(".sb-aktion-aktion::text").getall()).strip(),
            })
        return goals

    def extract_assist(self, li):
        """Return (assist_name, assist_tm_id) from a goal-event <li>. The
        second `<a>` inside `.sb-aktion-aktion` is the assist link when
        present (the first is the scorer)."""
        links = li.css(".sb-aktion-aktion a")
        if len(links) < 2:
            return None, None
        assist_text = links[1].css("::text").get()
        _, assist_id = _parse_player_link(links[1].attrib.get("href"))
        return assist_text, assist_id

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
        """Older / fallback lineup format that renders as a position-grouped
        table (used on historical matches where TM doesn't have the formation
        diagram). Player entries gain `name_english` (slug-derived full name)
        + `tm_player_id` keyed by position; the legacy bare-string list is
        preserved alongside as `players_short` for backward compat with any
        existing match-data consumer reading position lists directly.

        First box in document order is home, second is away (TM renders
        left column = home, right column = away). The first/second tracking
        is LOCAL to this call — sharing it with `extract_from_graphic_field`
        via `response.meta` was the §6 ③ "home lineup dropped" regression:
        for pre-formation matches, `extract_from_graphic_field` ran first,
        found zero formation containers but DID set `response.meta["home"]`
        from the team-name selector that's identical between layouts;
        then this function saw "home already taken" for every box and
        keyed both as "away", dropping the home lineup entirely.
        Reproduced against `match_report_1985_sample.html` 2026-06-12.
        """
        result = {}
        seen_home = False
        for box in response.css("div.aufstellung-box, div.large-6.columns"):
            team_name = box.css(".aufstellung-unterueberschrift-mannschaft a::text").get()
            if not team_name:
                continue
            team_key = "home" if not seen_home else "away"
            seen_home = True
            players = {}
            for row in box.css("table tr"):
                pos = row.css("td b::text").get()
                if pos:
                    cells = row.css("td:nth-child(2) a")
                    players[pos.strip().lower()] = [
                        {
                            "name_english": _parse_player_link(a.attrib.get("href"))[0]
                                            or (a.css("::text").get() or "").strip(),
                            "tm_player_id": _parse_player_link(a.attrib.get("href"))[1],
                            "name_short": (a.css("::text").get() or "").strip() or None,
                        }
                        for a in cells
                    ]
                elif row.css("td:nth-child(1)::text").re(".*manager.*"):
                    a = row.css("td:nth-child(2) a")
                    if a:
                        mgr_name, mgr_id = _parse_player_link(a.attrib.get("href"))
                        players["manager"] = {
                            "name_english": mgr_name or (a.css("::text").get() or "").strip(),
                            "tm_player_id": mgr_id,
                            "name_short": (a.css("::text").get() or "").strip() or None,
                        }
            result[team_key] = players
        return result

    def extract_from_graphic_field(self, response):
        """Standard formation-diagram lineup. TM's visible text in this view
        is the surname only (`Eliasi` not `Niv Eliasi`) because the diagram
        is space-constrained — but the `<a href>` carries the URL slug
        (`/niv-eliasi/profil/spieler/912586`) which encodes the full English
        name + TM player ID. Use both: slug for the canonical `name_english`,
        href for `tm_player_id`, original text retained as `name_short` for
        per-jersey-number compact rendering.
        """
        result = {}
        seen_home = False
        for box in response.css("div.box > div.large-6.columns"):
            team_name = box.css(".aufstellung-unterueberschrift-mannschaft a::text").get()
            if not team_name:
                continue
            # First box = home, second = away. Local state (was: shared via
            # response.meta with extract_from_simple_table, which dropped
            # home lineups for pre-formation matches — §6 ③ fix).
            team_key = "home" if not seen_home else "away"
            seen_home = True
            players = []
            for p in box.css(".formation-player-container"):
                a = p.css(".formation-number-name a")
                if not a:
                    continue
                surname = (a.css("::text").get() or "").strip()
                name_english, tm_player_id = _parse_player_link(a.attrib.get("href"))
                players.append({
                    "name_english": name_english or surname,
                    "name_short": surname or None,
                    "tm_player_id": tm_player_id,
                    "number": p.css(".tm-shirt-number::text").get(default="").strip(),
                    "captain": bool(p.css(".kapitaenicon-formation")),
                })
            if players:
                result[team_key] = players
        return result

    # NOTE: `resolve_team_key()` was removed in the §6 ③ fix (2026-06-12).
    # Its use of `response.meta["home"]` was shared between both lineup
    # extractors and caused the home-lineup-dropped regression on pre-
    # formation match reports. Both extractors now use a local `seen_home`
    # counter — there's no longer a need for an instance method here.

    def extract_penalties(self, response):
        items = response.css("#sb-elfmeterscheissen li")
        if not items:
            return None

        penalties = []
        for li in items:
            team = "home" if "sb-aktion-heim" in li.attrib.get("class", "") else "away"
            result = li.css(".sb-aktion-uhr span::attr(title)").get()
            score = li.css(".sb-aktion-spielstand b::text").get()
            player_a = li.css(".sb-aktion-aktion a")
            player = player_a.css("::text").get() if player_a else None
            _, player_id = _parse_player_link(player_a.attrib.get("href")) if player_a else (None, None)
            club = li.css(".sb-aktion-wappen a::attr(title)").get()

            penalties.append({
                "team": team,
                "result": result,
                "score": score,
                "player": player,
                "player_tm_id": player_id,
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

            in_a = li.css(".sb-aktion-wechsel-ein a")
            out_a = li.css(".sb-aktion-wechsel-aus a")
            player_in = in_a.css("::text").get() if in_a else None
            player_out = out_a.css("::text").get() if out_a else None
            _, in_id = _parse_player_link(in_a.attrib.get("href")) if in_a else (None, None)
            _, out_id = _parse_player_link(out_a.attrib.get("href")) if out_a else (None, None)

            subs.append({
                "team": team,
                "minute": self.estimate_minute_from_sprite(pos) if pos else None,
                "extra_time": int(extra_text) if extra_text else None,
                "sprite_position": f"{pos[0]}x{pos[1]}" if pos else None,
                "player_in": player_in,
                "player_in_tm_id": in_id,
                "player_out": player_out,
                "player_out_tm_id": out_id,
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

            player_a = li.css(".sb-aktion-aktion a")
            player_name = player_a.css("::text").get() if player_a else None
            _, player_id = _parse_player_link(player_a.attrib.get("href")) if player_a else (None, None)

            cards.append({
                "team": team,
                "minute": self.estimate_minute_from_sprite(pos) if pos else None,
                "extra_time": int(extra_text) if extra_text else None,
                "sprite_position": f"{pos[0]}x{pos[1]}" if pos else None,
                "player": player_name,
                "player_tm_id": player_id,
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
