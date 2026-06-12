"""Tests for the fixtures spider — reviewer-pass orange #8 (2026-06-13).

The spider previously had ZERO tests (the reviewer flagged this
explicitly). Pre-fix the result-cell extraction at
`fixtures_spider.py:56-60` IndexErrored on old-era rows whose result
cell lacks the modern `<a><span>` markup; combined with
`allow_empty=True` upstream, the whole season silently yielded zero
fixtures, masked as "TM doesn't carry this era".

These tests cover:
  - The modern shape (a single span = clean score)
  - The penalty shape (two spans = score + (penalties))
  - The OLD-era shape (no `<a><span>` — score in plain cell text)
  - The genuinely-empty cell (postponed / no data) — must NOT crash
"""

from pathlib import Path

import pytest
from scrapy.http import HtmlResponse, Request

from tmk_scraper.spiders.fixtures_spider import FixturesSpider


def _make_response(html: str, url: str = "https://www.transfermarkt.com/test"):
    return HtmlResponse(
        url=url, body=html.encode("utf-8"), request=Request(url=url),
    )


def _wrap_table(row_html: str) -> str:
    """Wrap a single fixture row in the table structure the spider
    expects, matching the production page markup."""
    return f"""
    <html><body>
      <div class="box">
        <div class="responsive-table">
          <table>
            <tbody>
              {row_html}
            </tbody>
          </table>
        </div>
      </div>
    </body></html>
    """


def _row_with_result_cell(result_cell_inner: str) -> str:
    """Build a complete fixture row, varying only the result cell. Other
    cells carry the minimum data the spider needs to NOT skip the row
    (matchday, date, time, venue, opponent, etc — all present)."""
    return f"""
    <tr>
      <td>1</td>           <!-- matchday -->
      <td>Aug 15, 2024</td><!-- date -->
      <td>20:00</td>       <!-- time -->
      <td>H</td>           <!-- venue -->
      <td></td>            <!-- col 4 -->
      <td></td>            <!-- col 5 -->
      <td><a href="/club">Opponent FC</a></td>
      <td>4-3-3</td>       <!-- system_of_play -->
      <td>5000</td>        <!-- attendance -->
      <td>{result_cell_inner}</td>
    </tr>
    """


class TestFixturesResultExtraction:
    def setup_method(self):
        self.spider = FixturesSpider(season="2024")

    def test_modern_single_span_clean_score(self):
        """Production happy path: result cell has `<a><span>2:1</span></a>`."""
        html = _wrap_table(_row_with_result_cell(
            '<a href="/m/report"><span>2:1</span></a>'
        ))
        fixtures = list(self.spider.parse(_make_response(html)))
        assert len(fixtures) == 1
        assert fixtures[0]["result"] == "2:1"

    def test_penalty_shape_double_span(self):
        """Penalty marker: result cell has two spans (score + penalties).
        The spider appends "(penalties)" to the result string."""
        html = _wrap_table(_row_with_result_cell(
            '<a href="/m/report"><span>2:2</span><span>(4-3 pen)</span></a>'
        ))
        fixtures = list(self.spider.parse(_make_response(html)))
        assert len(fixtures) == 1
        assert "(penalties)" in fixtures[0]["result"]

    def test_old_era_plain_text_result(self):
        """Reviewer-pass orange #8 fix: pre-2009-style rows have the
        result as plain text in the cell, with no `<a>` or `<span>`.
        Pre-fix the spider IndexErrored on the empty `result_element`
        list. Now it falls back to the cell's normalized text."""
        html = _wrap_table(_row_with_result_cell("3:1"))
        fixtures = list(self.spider.parse(_make_response(html)))
        assert len(fixtures) == 1
        assert fixtures[0]["result"] == "3:1"

    def test_empty_result_cell_does_not_crash(self):
        """Edge: postponed match, or a row TM hasn't filled yet. The
        cell is genuinely empty. Pre-fix this crashed at
        `result_element[0]`; now it returns an empty result string and
        the row is yielded with empty result."""
        html = _wrap_table(_row_with_result_cell(""))
        # Should NOT raise IndexError.
        fixtures = list(self.spider.parse(_make_response(html)))
        # If the row was yielded, its result is empty; if it was skipped,
        # that's also valid behavior — but it must NOT crash.
        for f in fixtures:
            assert f["result"] in ("", None)

    def test_multiple_rows_one_old_one_modern_both_yield(self):
        """Realistic mixed-era table: one row in modern shape, one in
        old-era shape. The pre-fix bug would IndexError on the old row
        and kill the whole season; the fix yields both."""
        modern = _row_with_result_cell(
            '<a href="/m/modern"><span>2:1</span></a>'
        )
        old_era = _row_with_result_cell("0:0")
        html = _wrap_table(modern + old_era)
        fixtures = list(self.spider.parse(_make_response(html)))
        assert len(fixtures) == 2
        results = {f["result"] for f in fixtures}
        assert "2:1" in results
        assert "0:0" in results
