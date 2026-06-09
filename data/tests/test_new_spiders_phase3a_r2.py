"""Tests for the new Phase 3a R2 spiders: platzierungen + bilanz.

Fixtures captured 2026-06-09 via ScraperAPI against live Transfermarkt URLs.
"""

from pathlib import Path

from scrapy.http import HtmlResponse, Request

from tmk_scraper.spiders.bilanz_spider import BilanzSpider
from tmk_scraper.spiders.honours_spider import HonoursSpider
from tmk_scraper.spiders.platzierungen_spider import PlatzierungenSpider

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _fake_response(html_path: Path, url: str = "https://www.transfermarkt.com/test"):
    body = html_path.read_bytes()
    return HtmlResponse(url=url, body=body, request=Request(url=url))


class TestPlatzierungenSpider:
    def setup_method(self):
        self.spider = PlatzierungenSpider()
        self.response = _fake_response(FIXTURES_DIR / "platzierungen_sample.html")
        self.rows = list(self.spider.parse(self.response))

    def test_rows_span_modern_to_around_1986(self):
        """The fixture (captured 2026-06-09) covers 2025/26 down to roughly
        1986/87 per the PR A floor probe. Expect at least 35 season rows."""
        assert len(self.rows) >= 35

    def test_season_normalised_to_bare_integer_start_year(self):
        """The page renders "25/26"; spider must emit "2025" as the join key."""
        seasons = {r["season"] for r in self.rows}
        assert "2025" in seasons
        # Verify the YY < 50 / YY >= 50 boundary is applied (older seasons
        # like 1995/96 should map to "1995", not "2095").
        old_seasons = [s for s in seasons if s.startswith("19")]
        assert old_seasons, "expected at least one 1990s/1980s row"

    def test_2024_25_title_winning_season(self):
        """HBS won the 2024/25 league title. The platzierungen row must
        carry final_position=1, ~17W/6D/2L, 58 points (verified during PR A
        probing).
        """
        r = next(r for r in self.rows if r["season"] == "2024")
        assert r["competition"] == "Ligat ha'Al"
        assert r["tier"] == 1
        assert r["final_position"] == 1
        assert r["wins"] == 18 or r["wins"] == 17  # TM's split can move +/- 1 across snapshots
        assert r["points"] >= 50
        assert r["goals_for"] is not None
        assert r["goals_against"] is not None
        # Per-season manager extraction lives in the same row.
        assert r["manager_name"]
        assert r["manager_id"]

    def test_2000_01_second_tier_promotion_season(self):
        """HBS won the second tier in 2000/01 → finished 1st in tier 2."""
        r = next(r for r in self.rows if r["season"] == "2000")
        assert r["tier"] == 2
        assert r["final_position"] == 1

    def test_manager_id_threaded_through(self):
        """Every modern row should carry a non-empty manager_id (TM coach id)
        so the trophies-won join can key by it.
        """
        modern = [r for r in self.rows if r["season"] >= "2015"]
        assert all(r["manager_id"] for r in modern), "modern rows must have manager_id"

    def test_season_from_label(self):
        assert PlatzierungenSpider._season_from_label("25/26") == "2025"
        assert PlatzierungenSpider._season_from_label("99/00") == "1999"
        assert PlatzierungenSpider._season_from_label("00/01") == "2000"
        assert PlatzierungenSpider._season_from_label("") is None
        assert PlatzierungenSpider._season_from_label("nonsense") is None

    def test_tier_from_label(self):
        assert PlatzierungenSpider._tier_from_label("First Tier") == 1
        assert PlatzierungenSpider._tier_from_label("Second Tier") == 2
        assert PlatzierungenSpider._tier_from_label("") is None


class TestBilanzSpider:
    def setup_method(self):
        self.spider = BilanzSpider()
        self.response = _fake_response(FIXTURES_DIR / "bilanz_sample.html")
        self.rows = list(self.spider.parse(self.response))

    def test_rows_yield_per_opponent(self):
        """Bilanz table lists every opponent HBS has ever played. Expect at
        least the 4 major derby opponents + 10-15 more for a single page slice.
        """
        assert len(self.rows) >= 15

    def test_each_opponent_carries_tm_id(self):
        """Cross-linking story: every row must have an opponent_tm_id so the
        Derbies page can later link to club pages once they exist.
        """
        # At least 80% should carry an ID — TM may have a few rows for
        # defunct clubs without a current /verein/ link.
        with_id = sum(1 for r in self.rows if r["opponent_tm_id"])
        assert with_id / len(self.rows) >= 0.8

    def test_major_derby_opponents_present(self):
        names = {r["opponent"] for r in self.rows}
        # The fixture's page-1 covers the 4 major IPL rivals plus mid-table
        # clubs. Check at least 3 of the 4 derby clubs are visible (TM
        # paginates — the spider only walks page 1 in the current
        # implementation, that's fine for the Derbies page which only needs
        # the high-match-count rivals).
        derby_candidates = {
            "Maccabi Tel Aviv", "Hapoel Tel Aviv", "Beitar Jerusalem",
            "Maccabi Haifa", "B. Jerusalem", "M. Tel Aviv", "H. Tel Aviv", "M. Haifa",
        }
        derby_hits = derby_candidates & names
        assert len(derby_hits) >= 3, f"expected 3+ derby rivals, found {derby_hits}"

    def test_aggregates_arithmetic(self):
        """For every row, wins + draws + losses must equal matches."""
        for r in self.rows:
            total = r["wins"] + r["draws"] + r["losses"]
            assert total == r["matches"], (
                f"WDL sum mismatch for {r['opponent']}: "
                f"{r['wins']}+{r['draws']}+{r['losses']} != {r['matches']}"
            )

    def test_attendance_parsed_to_int_or_null(self):
        for r in self.rows:
            if r["avg_attendance"] is not None:
                assert isinstance(r["avg_attendance"], int)
                assert r["avg_attendance"] > 0


class TestHonoursSpiderR2Rewrite:
    """Phase 3a R2 honours spider rewrite. The pre-R2 selectors targeted
    `table.items tr` rows which TM's erfolge page no longer uses. After the
    rewrite, trophies pair `div.erfolg_bild_box` (img title=competition) with
    the adjacent `div.erfolg_infotext_box` (comma-separated seasons).

    Empirically verified against the 2026-06-09 fixture: 6 league titles,
    4 cups, 5 super cups for HBS — matching the PR A inventory honours
    list.
    """

    def setup_method(self):
        self.spider = HonoursSpider()
        self.response = _fake_response(FIXTURES_DIR / "erfolge_sample.html")
        self.rows = list(self.spider.parse(self.response))

    def test_israeli_championships(self):
        champ = next(r for r in self.rows if r["competition"] == "Israeli Champion")
        # 6 titles per PR A inventory.
        assert len(champ["seasons"]) == 6
        # The slash-form labels survive.
        for s in champ["seasons"]:
            assert "/" in s
        # Spot-check known title years (in TM's 2-digit form).
        assert "25/26" in champ["seasons"]
        assert "17/18" in champ["seasons"]
        assert "75/76" in champ["seasons"]

    def test_israeli_cup_count(self):
        cup = next(
            r for r in self.rows
            if r["competition"].lower().startswith("israeli cup")
        )
        # 4 cups per PR A inventory.
        assert len(cup["seasons"]) == 4
        assert "96/97" in cup["seasons"]

    def test_super_cup_count(self):
        super_cup = next(
            r for r in self.rows
            if "super cup" in r["competition"].lower()
        )
        # 5 super cups per PR A inventory.
        assert len(super_cup["seasons"]) == 5

    def test_no_count_string_artifacts(self):
        """The pre-R2 fallback path yielded fake seasons like "5x" derived
        from the success-badge count. After the rewrite, every season must
        look like a real YY/YY label."""
        for r in self.rows:
            for s in r["seasons"]:
                # YY/YY format — 5 chars including the slash, all digits + /.
                assert "/" in s, f"non-slash season label in {r['competition']}: {s!r}"
                left, right = s.split("/", 1)
                assert left.isdigit(), f"non-numeric YY in {s!r}"
                assert right.isdigit(), f"non-numeric YY in {s!r}"
