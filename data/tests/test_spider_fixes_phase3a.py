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
from tmk_scraper.spiders.match_spider import MatchSpider
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
