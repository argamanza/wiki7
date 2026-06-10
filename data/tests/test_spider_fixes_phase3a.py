"""Regression tests for the Phase 3a spider fixes.

The four correctness gaps from `docs/revival-plan.md` §Phase 3:
  1. Match records carry `season`.
  2. Lineup extraction is no longer brittle (selector reverted to `.formation-number-name`
     after a botched `name`→`name_english` schema rename leaked into the CSS class).
  3. The coach spider returns non-empty (URL fixed from /trainer/ to /mitarbeiter/, which
     is the only TM URL that actually resolves as of 2026-06-07).
  4. The transfers spider returns non-empty (page returns ALL seasons; filter by `<h2>`
     "Arrivals YY/YY" / "Departures YY/YY"; row selector reverts from `table.items > tbody
     > tr` to plain `tbody > tr` since TM dropped the `items` class).

Captured fixtures: 2026-06-07 via ScraperAPI against live Transfermarkt URLs.
"""

from pathlib import Path

from scrapy.http import HtmlResponse, Request

from tmk_scraper.spiders.coach_spider import CoachSpider
from tmk_scraper.spiders.match_spider import MatchSpider, _parse_player_link
from tmk_scraper.spiders.records_spider import RecordsSpider
from tmk_scraper.spiders.squad_spider import SquadSpider
from tmk_scraper.spiders.transfers_spider import TransfersSpider

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _fake_response(html_path: Path, url: str = "https://www.transfermarkt.com/test"):
    body = html_path.read_bytes()
    return HtmlResponse(url=url, body=body, request=Request(url=url))


class TestMatchSpiderPhase3a:
    def setup_method(self):
        self.spider = MatchSpider(season="2024")

    def test_yielded_match_carries_season(self):
        response = _fake_response(FIXTURES_DIR / "match_report_sample.html")
        # parse_match_report uses response.meta["match_data"] — feed a minimal fixture
        response.request.meta["match_data"] = {
            "competition": "Ligat ha'Al", "matchday": "6", "venue": "H", "opponent": "H. Jerusalem",
        }
        records = list(self.spider.parse_match_report(response))
        assert len(records) == 1
        assert records[0]["season"] == "2024"

    def test_lineup_selector_extracts_starting_xi(self):
        """The botched `.formation-number-name_english` selector matched zero elements.
        After the fix we get 11 starting players per side.
        """
        response = _fake_response(FIXTURES_DIR / "match_report_sample.html")
        graphic = self.spider.extract_from_graphic_field(response)
        assert "home" in graphic and "away" in graphic
        assert len(graphic["home"]) == 11
        assert len(graphic["away"]) == 11
        # Sanity: every player has name_english and number populated, captain is bool.
        sample = graphic["home"][0]
        assert sample["name_english"]
        assert sample["number"]
        assert isinstance(sample["captain"], bool)

    def test_lineup_emits_full_name_from_slug_not_just_surname(self):
        """Iteration-cycle phase: TM's formation diagram renders surnames only
        (`Eliasi`) but encodes the full English name in the `<a href>` URL
        slug (`/niv-eliasi/profil/spieler/912586`). The spider must surface
        the slug-derived full name as `name_english`; the surname survives
        as `name_short` for compact rendering. Without this, the names
        corpus ends up with both `Eliasi` and `Niv Eliasi` as separate
        translation keys, and the lineup section of every match report
        shows surname-only player references."""
        response = _fake_response(FIXTURES_DIR / "match_report_sample.html")
        graphic = self.spider.extract_from_graphic_field(response)
        for side in ("home", "away"):
            for p in graphic[side]:
                # Full name has at least one space (multi-token) for every
                # real player. Single-name footballers (e.g. Pelé) would
                # break this assertion, but none have appeared on HBS or
                # any 2024/25 opponent rosters.
                assert " " in p["name_english"], (
                    f"Expected full name on {side} lineup but got {p['name_english']!r}"
                )
                assert p["tm_player_id"] is not None
                assert p["tm_player_id"].isdigit()
                # Surname is preserved separately.
                assert p["name_short"] is not None

    def test_event_extractors_carry_tm_player_id(self):
        """Goals, substitutions, cards (and penalties when present) all carry
        a TM player ID so the pipeline can dedupe across name forms and
        the match report renderer can decide link-or-plain via {{#ifexist:}}."""
        response = _fake_response(FIXTURES_DIR / "match_report_sample.html")
        goals = self.spider.extract_goals(response)
        assert goals, "fixture should have goals"
        for g in goals:
            assert g["scorer"]
            assert g["scorer_tm_id"] and g["scorer_tm_id"].isdigit()
            if g["assist"]:
                assert g["assist_tm_id"] and g["assist_tm_id"].isdigit()

        subs = self.spider.extract_substitutions(response)
        assert subs, "fixture should have substitutions"
        for s in subs:
            if s["player_in"]:
                assert s["player_in_tm_id"] and s["player_in_tm_id"].isdigit()
            if s["player_out"]:
                assert s["player_out_tm_id"] and s["player_out_tm_id"].isdigit()

        cards = self.spider.extract_cards(response)
        for c in cards:
            if c["player"]:
                assert c["player_tm_id"] and c["player_tm_id"].isdigit()

    def test_resolve_team_key_is_home_first(self):
        """TM renders home-team box first. The pre-fix code looked up nonexistent
        match.home_team / match.away_team fields and silently fell through to a similar
        first-box-is-home fallback — this test pins the surviving behavior.
        """
        response = _fake_response(FIXTURES_DIR / "match_report_sample.html")
        response.request.meta["match_data"] = {"venue": "H", "opponent": "H. Jerusalem"}
        # First call sees the home team; second call should return "away" regardless of name.
        assert self.spider.resolve_team_key("Hapoel Beer Sheva", response) == "home"
        assert self.spider.resolve_team_key("Hapoel Jerusalem", response) == "away"


class TestParsePlayerLink:
    """Helper used everywhere the spider crosses an <a href> on a TM player.
    Tested in isolation so the spider extractors only need to verify the
    end-to-end glue."""

    def test_lineup_link_yields_full_name_and_id(self):
        assert _parse_player_link("/niv-eliasi/profil/spieler/912586") \
            == ("Niv Eliasi", "912586")

    def test_event_link_with_long_path_yields_full_name_and_id(self):
        # Goals/cards use `/leistungsdatendetails/spieler/<id>/saison/...`
        href = "/ohad-almagor/leistungsdatendetails/spieler/933143/saison/2024/wettbewerb/ISR1"
        assert _parse_player_link(href) == ("Ohad Almagor", "933143")

    def test_accented_slug_strips_diacritics(self):
        # TM slug-normalises non-ASCII; downstream Wikidata search is
        # accent-insensitive so this is fine for translation lookup.
        assert _parse_player_link("/helder-lopes/profil/spieler/171068") \
            == ("Helder Lopes", "171068")

    def test_multi_segment_name_slug(self):
        # Real-world: hyphenated first or last names (e.g. Jean-Luc-Picard).
        # We split on every hyphen — the result is a space-separated string.
        assert _parse_player_link("/jean-luc-picard/profil/spieler/1234") \
            == ("Jean Luc Picard", "1234")

    def test_missing_href_yields_none_pair(self):
        assert _parse_player_link(None) == (None, None)
        assert _parse_player_link("") == (None, None)

    def test_unexpected_first_segment_yields_none(self):
        # Defensive: if TM ever changes URL layout we'd rather emit None
        # than garbage. Confirm the guard against likely-bad first-segments.
        assert _parse_player_link("/spieler/12345") == (None, None)
        assert _parse_player_link("/verein/2976") == (None, None)


class TestMatchSpiderR2Additions:
    """Phase 3a R2: halftime score, stadium, main referee, AET inference,
    referee-team placeholder fields.
    """

    def setup_method(self):
        self.spider = MatchSpider(season="2024")

    def test_extract_halftime_score(self):
        """Legacy: covered fully by the regulation-vs-AET tests below. Kept
        as a smoke that the parse helpers compose into a real score for the
        2024 fixture."""
        response = _fake_response(FIXTURES_DIR / "match_report_sample.html")
        text = self.spider.extract_halbzeit_text(response)
        assert self.spider.parse_halftime_from_halbzeit(text) == "0:1"

    def test_extract_stadium(self):
        response = _fake_response(FIXTURES_DIR / "match_report_sample.html")
        # 2024/25 fixture is at Turner Stadium.
        assert self.spider.extract_stadium(response) == "Toto Jacob Turner Stadium"

    def test_extract_referee_modern_match(self):
        response = _fake_response(FIXTURES_DIR / "match_report_sample.html")
        assert self.spider.extract_referee(response) == "Yoav Mizrahi"

    def test_extract_referee_1985_match(self):
        """Verify the same selector works against TM's 1985/86-era HTML. The
        Sep 14 1985 fixture was refereed by Zvi Sharir (verified during PR A
        probing).
        """
        response = _fake_response(FIXTURES_DIR / "match_report_1985_sample.html")
        assert self.spider.extract_referee(response) == "Zvi Sharir"

    def test_aet_false_when_no_penalties_no_late_goal(self):
        response = _fake_response(FIXTURES_DIR / "match_report_sample.html")
        response.request.meta["match_data"] = {"competition": "Ligat ha'Al"}
        record = next(iter(self.spider.parse_match_report(response)))
        # 3:1 in regulation with no late winner past minute 90.
        assert record["aet"] is False

    def test_extract_halftime_score_regulation_match(self):
        """Regulation match has the halftime score in parens — e.g. (0:1)
        for the HBS 3:1 H. Jerusalem fixture (Sep 2024 IPL game)."""
        response = _fake_response(FIXTURES_DIR / "match_report_sample.html")
        text = self.spider.extract_halbzeit_text(response)
        assert text == "(0:1)"
        assert self.spider.parse_halftime_from_halbzeit(text) == "0:1"
        assert self.spider.is_aet_marker(text) is False

    def test_extract_halftime_score_aet_match(self):
        """Knockout match that went to extra time: TM replaces the
        halftime-score slot with the literal "AET" marker. We must NOT
        treat that string as the halftime score.

        Fixture: HBS 3:2 Maccabi Netanya, Gvia haMedina (Israeli State Cup)
        2023/24 — drawn at 90', HBS scored in extra time. TM serves
        '3:2' final + 'AET' in the halbzeit slot.
        """
        response = _fake_response(FIXTURES_DIR / "match_report_aet_sample.html")
        text = self.spider.extract_halbzeit_text(response)
        assert text == "AET"
        # Halftime score is unknown for AET matches in TM's English layout.
        assert self.spider.parse_halftime_from_halbzeit(text) is None
        # AET marker drives the AET flag straight to True.
        assert self.spider.is_aet_marker(text) is True

    def test_parse_match_report_aet_flag(self):
        """End-to-end through parse_match_report: the AET fixture must yield
        aet=True via the explicit marker, halftime_score=None, and the
        regulation fixture must yield aet=False with a real halftime score.
        """
        # AET fixture
        response = _fake_response(FIXTURES_DIR / "match_report_aet_sample.html")
        response.request.meta["match_data"] = {"competition": "Gvia haMedina"}
        record = next(iter(self.spider.parse_match_report(response)))
        assert record["aet"] is True
        assert record["halftime_score"] is None

        # Regulation fixture — fresh spider instance because resolve_team_key
        # caches "home" on response.meta which mutates state across calls.
        spider = MatchSpider(season="2024")
        response = _fake_response(FIXTURES_DIR / "match_report_sample.html")
        response.request.meta["match_data"] = {"competition": "Ligat ha'Al"}
        record = next(iter(spider.parse_match_report(response)))
        assert record["aet"] is False
        assert record["halftime_score"] == "0:1"

    def test_aet_marker_is_case_insensitive(self):
        assert MatchSpider.is_aet_marker("AET") is True
        assert MatchSpider.is_aet_marker("aet") is True
        assert MatchSpider.is_aet_marker(" AET ") is True
        # German variants for forward-compat (in case ScraperAPI ever serves DE).
        assert MatchSpider.is_aet_marker("n.V.") is True
        assert MatchSpider.is_aet_marker("AP") is True
        # Real halftime scores must NOT trigger the marker.
        assert MatchSpider.is_aet_marker("(0:1)") is False
        assert MatchSpider.is_aet_marker("1:1") is False
        assert MatchSpider.is_aet_marker(None) is False
        assert MatchSpider.is_aet_marker("") is False

    def test_referee_team_placeholder_fields_default_none(self):
        """TM doesn't expose assistants / 4th official / VAR in the match-
        report layout; PR B records them as nullable for hand-curation or a
        future IFA scraper. Spider must emit them as None so the schema
        validates and Cargo skips them on store.
        """
        response = _fake_response(FIXTURES_DIR / "match_report_sample.html")
        response.request.meta["match_data"] = {"competition": "Ligat ha'Al"}
        record = next(iter(self.spider.parse_match_report(response)))
        assert record["assistant_referee_1"] is None
        assert record["assistant_referee_2"] is None
        assert record["fourth_official"] is None
        assert record["var_referee"] is None
        assert record["var_assistant"] is None


class TestSquadSpiderR2Captain:
    """Phase 3a R2 finding: TM's squad page does NOT expose a captain marker
    in any era (audited 2026-06-09 against 2015/16, 1985/86, and current
    fixtures). The is_captain field on the Player model is populated outside
    the squad spider — from the latest match-report's graphic_lineups (which
    already carries a per-match captain bool), or by hand-curation. The squad
    spider emits is_captain=False unconditionally.
    """

    def test_2015_squad_defaults_to_no_captain(self):
        spider = SquadSpider(season="2015")
        response = _fake_response(FIXTURES_DIR / "kader_2015_sample.html")
        # The spider also yields a Request to the loans page at the end;
        # filter it out so we're only asserting against player dicts.
        players = [item for item in spider.parse(response) if isinstance(item, dict)]
        assert len(players) > 0
        assert all(p["is_captain"] is False for p in players)

    def test_1985_squad_defaults_to_no_captain(self):
        spider = SquadSpider(season="1985")
        response = _fake_response(FIXTURES_DIR / "kader_1985_sample.html")
        players = [item for item in spider.parse(response) if isinstance(item, dict)]
        assert len(players) > 0
        assert all(p["is_captain"] is False for p in players)

    def test_loaned_players_default_to_no_captain(self):
        spider = SquadSpider(season="2015")
        response = _fake_response(FIXTURES_DIR / "kader_2015_sample.html")
        loans = list(spider.parse_loans(response))
        for p in loans:
            assert p["is_captain"] is False


class TestRecordsSpiderR2Direction:
    """Phase 3a R2 finding: TM removed the separate departures page. Records
    spider stays single-direction (arrivals only) but every row carries a
    `direction: "in"` marker so downstream code can populate departure rows
    derived from alletransfers into the same shape.
    """

    def test_records_arrivals_carries_direction_marker(self):
        spider = RecordsSpider()
        response = _fake_response(
            FIXTURES_DIR / "transferrekorde_arrivals_sample.html",
            url="https://www.transfermarkt.com/hapoel-beer-sheva/transferrekorde/verein/2976",
        )
        records = list(spider.parse(response))
        assert len(records) > 0
        for r in records:
            assert r["direction"] == "in"
            assert r["player_name"]
            assert r["value"]


class TestCoachSpiderPhase3a:
    def setup_method(self):
        self.spider = CoachSpider(season="2024")

    def test_mitarbeiter_yields_nonempty_current_staff(self):
        """Before the URL fix this returned `[]` because `/trainer/verein/` was a 404
        landing page. The mitarbeiter page yields the current head coach + assistants.
        """
        response = _fake_response(FIXTURES_DIR / "coaches_mitarbeiter_sample.html")
        records = list(self.spider.parse(response))
        assert len(records) >= 4  # at least head coach + a few assistants
        # Identify the head coach via the role field
        managers = [r for r in records if r["role"] == "Manager"]
        assert len(managers) == 1
        m = managers[0]
        assert m["name"] == "Ran Kozuch"
        assert m["id"] == "96723"
        assert m["tenure_start"] == "01/07/2024"
        assert m["tenure_end"] == "30.06.2028"

    def test_assistant_with_open_ended_contract_yields_empty_tenure_end(self):
        """`Contract expires` cell is "-" when the staff member has an open-ended contract.
        We normalise that to an empty string so downstream templates render "—" instead
        of a literal dash.
        """
        response = _fake_response(FIXTURES_DIR / "coaches_mitarbeiter_sample.html")
        records = list(self.spider.parse(response))
        assistant = next(r for r in records if r["role"] == "Assistant Manager")
        assert assistant["name"] == "Ben Binyamin"
        assert assistant["tenure_end"] == ""


class TestTransfersSpiderPhase3a:
    def test_2024_season_yields_arrivals_and_departures(self):
        spider = TransfersSpider(season="2024")
        response = _fake_response(FIXTURES_DIR / "club_transfers_sample.html")
        records = list(spider.parse(response))
        # 2024/25 in the fixture: 15 arrivals + 29 departures
        assert len(records) == 15 + 29
        arrivals = [r for r in records if r["direction"] == "in"]
        departures = [r for r in records if r["direction"] == "out"]
        assert len(arrivals) == 15
        assert len(departures) == 29

    def test_each_record_has_required_fields(self):
        """Every row must have the identity (season + player) + fee. The Beer Sheva side
        of the transfer is always populated; the *other* club can legitimately be empty
        for academy promotions (homegrown arrivals with no source club on TM) or contract
        expiries without a destination — we keep those rows, just with an empty other_club.
        """
        spider = TransfersSpider(season="2024")
        response = _fake_response(FIXTURES_DIR / "club_transfers_sample.html")
        records = list(spider.parse(response))
        for r in records:
            assert r["season"] == "2024"
            assert r["player_name"]
            assert r["player_id"]
            assert r["fee"]
            assert "Hapoel Beer Sheva" in (r["from_club"], r["to_club"])

    def test_homegrown_arrival_has_empty_source_club(self):
        """Eliel Peretz is a known academy promotion in the 24/25 arrivals fixture — TM
        shows no source club, fee=free transfer. The spider must keep the row (Phase 3a
        wiki content benefits from listing homegrowns separately) with `from_club=""`.
        """
        spider = TransfersSpider(season="2024")
        response = _fake_response(FIXTURES_DIR / "club_transfers_sample.html")
        records = list(spider.parse(response))
        homegrown = [r for r in records if r["player_name"] == "Eliel Peretz"]
        assert len(homegrown) == 1
        assert homegrown[0]["direction"] == "in"
        assert homegrown[0]["from_club"] == ""
        assert homegrown[0]["to_club"] == "Hapoel Beer Sheva"

    def test_filters_out_other_seasons(self):
        """Single spider run for season=2023 should NOT return the 24/25 rows even though
        they're in the same HTML response.
        """
        spider = TransfersSpider(season="2023")
        response = _fake_response(FIXTURES_DIR / "club_transfers_sample.html")
        records = list(spider.parse(response))
        assert all(r["season"] == "2023" for r in records)
        # Spot-check a known 24/25 arrival is absent:
        assert not any(r["player_name"] == "Kings Kangwa" for r in records)

    def test_header_parser_maps_yyyy_correctly(self):
        spider = TransfersSpider(season="2024")
        assert spider._parse_header("Arrivals 24/25") == ("in", "2024")
        assert spider._parse_header("Departures 24/25") == ("out", "2024")
        assert spider._parse_header("Arrivals 99/00") == ("in", "1999")
        assert spider._parse_header("Arrivals 00/01") == ("in", "2000")
        assert spider._parse_header("Info") == (None, None)
        assert spider._parse_header("") == (None, None)
