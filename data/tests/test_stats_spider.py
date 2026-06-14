"""Tests for the stats spider's header-driven column parsing."""

from pathlib import Path
from scrapy.http import HtmlResponse, Request

from tmk_scraper.spiders.stats_spider import StatsSpider

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _fake_response(html_path: str, url: str = "https://www.transfermarkt.com/test"):
    """Create a fake Scrapy HtmlResponse from an HTML file."""
    body = Path(html_path).read_bytes()
    return HtmlResponse(url=url, body=body, request=Request(url=url))


class TestStatsSpiderParse:
    def setup_method(self):
        self.spider = StatsSpider(season="2024")

    def test_column_map_from_headers(self):
        response = _fake_response(FIXTURES_DIR / "leistungsdaten_sample.html")
        col_map = self.spider._build_column_map(response)

        # 15 columns: #(0), Player(1), Age(2), Nat.(3), InSquad(4),
        # Apps(5), Goals(6), Assists(7), Yellow(8), 2ndYellow(9), Red(10),
        # SubsOn(11), SubsOff(12), PPG(13), Minutes(14)
        assert col_map["appearances"] == 5
        assert col_map["goals"] == 6
        assert col_map["assists"] == 7
        assert col_map["yellow_cards"] == 8
        assert col_map["second_yellow_cards"] == 9
        assert col_map["red_cards"] == 10
        assert col_map["ppg"] == 13
        assert col_map["minutes_played"] == 14

    def test_parse_goalkeeper(self):
        response = _fake_response(FIXTURES_DIR / "leistungsdaten_sample.html")
        items = list(self.spider.parse(response))

        # Filter to goalkeeper (Ohad Levita, id 348992)
        gk = next(i for i in items if i["player_id"] == "348992")
        assert gk["appearances"] == 10
        assert gk["goals"] == 0
        assert gk["assists"] == 0
        assert gk["yellow_cards"] == 1
        assert gk["second_yellow_cards"] == 0
        assert gk["red_cards"] == 0
        assert gk["minutes_played"] == 900
        assert gk["ppg"] == 1.5

    def test_parse_midfielder(self):
        response = _fake_response(FIXTURES_DIR / "leistungsdaten_sample.html")
        items = list(self.spider.parse(response))

        mid = next(i for i in items if i["player_id"] == "503642")
        assert mid["appearances"] == 30
        assert mid["goals"] == 8
        assert mid["assists"] == 5
        assert mid["yellow_cards"] == 3
        assert mid["second_yellow_cards"] == 1
        assert mid["red_cards"] == 0
        assert mid["minutes_played"] == 2450
        assert mid["ppg"] == 2.1

    def test_parse_defender_with_cards(self):
        response = _fake_response(FIXTURES_DIR / "leistungsdaten_sample.html")
        items = list(self.spider.parse(response))

        defender = next(i for i in items if i["player_id"] == "101577")
        assert defender["appearances"] == 20
        assert defender["goals"] == 2
        assert defender["assists"] == 1
        assert defender["yellow_cards"] == 5
        assert defender["second_yellow_cards"] == 1
        assert defender["red_cards"] == 1
        assert defender["minutes_played"] == 1600
        assert defender["ppg"] == 1.8

    def test_not_used_player_skipped(self):
        response = _fake_response(FIXTURES_DIR / "leistungsdaten_sample.html")
        items = list(self.spider.parse(response))

        # "Bench Player" (999999) has "Not used during this season" — should be skipped
        ids = [i["player_id"] for i in items]
        assert "999999" not in ids

    def test_all_dashes_yields_zeros(self):
        response = _fake_response(FIXTURES_DIR / "leistungsdaten_sample.html")
        items = list(self.spider.parse(response))

        young = next(i for i in items if i["player_id"] == "888888")
        assert young["appearances"] == 0
        assert young["goals"] == 0
        assert young["assists"] == 0
        assert young["yellow_cards"] == 0
        assert young["second_yellow_cards"] == 0
        assert young["red_cards"] == 0
        assert young["minutes_played"] == 0
        assert young["ppg"] is None   # "-" PPG → None (not 0.0)

    def test_total_players_parsed(self):
        response = _fake_response(FIXTURES_DIR / "leistungsdaten_sample.html")
        items = list(self.spider.parse(response))

        # 5 players in fixture, 1 skipped ("not used") = 4 yielded
        assert len(items) == 4

    def test_season_field(self):
        response = _fake_response(FIXTURES_DIR / "leistungsdaten_sample.html")
        items = list(self.spider.parse(response))

        for item in items:
            assert item["season"] == "2024"


class TestExtractHelpers:
    def test_extract_cell_int_with_link(self):
        """Appearances wrapped in <a> tag should be extracted correctly."""
        from scrapy import Selector
        sel = Selector(text='<td class="zentriert"><a href="/foo">25</a></td>')
        cells = sel.css("td")
        assert StatsSpider._extract_cell_int(cells, 0) == 25

    def test_extract_cell_int_plain_text(self):
        from scrapy import Selector
        sel = Selector(text='<td class="zentriert">3</td>')
        cells = sel.css("td")
        assert StatsSpider._extract_cell_int(cells, 0) == 3

    def test_extract_cell_int_dash(self):
        from scrapy import Selector
        sel = Selector(text='<td class="zentriert">-</td>')
        cells = sel.css("td")
        assert StatsSpider._extract_cell_int(cells, 0) == 0

    def test_extract_cell_int_out_of_range(self):
        from scrapy import Selector
        sel = Selector(text='<td>1</td>')
        cells = sel.css("td")
        assert StatsSpider._extract_cell_int(cells, 5) == 0

    def test_extract_cell_int_none_index(self):
        from scrapy import Selector
        sel = Selector(text='<td>1</td>')
        cells = sel.css("td")
        assert StatsSpider._extract_cell_int(cells, None) == 0

    def test_extract_cell_minutes_dot_format(self):
        from scrapy import Selector
        sel = Selector(text='<td class="rechts">2.450</td>')
        cells = sel.css("td")
        assert StatsSpider._extract_cell_minutes(cells, 0) == 2450

    def test_extract_cell_minutes_apostrophe_format(self):
        from scrapy import Selector
        sel = Selector(text="<td class='rechts'>1'600</td>")
        cells = sel.css("td")
        assert StatsSpider._extract_cell_minutes(cells, 0) == 1600

    def test_extract_cell_minutes_plain(self):
        from scrapy import Selector
        sel = Selector(text='<td class="rechts">900</td>')
        cells = sel.css("td")
        assert StatsSpider._extract_cell_minutes(cells, 0) == 900

    def test_extract_cell_minutes_dash(self):
        from scrapy import Selector
        sel = Selector(text='<td class="rechts">-</td>')
        cells = sel.css("td")
        assert StatsSpider._extract_cell_minutes(cells, 0) == 0
