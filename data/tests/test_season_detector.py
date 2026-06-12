"""Tests for the --season=latest detector (Pattern A.3)."""

from unittest.mock import MagicMock, patch

import pytest
import requests

from data_pipeline.season_detector import (
    SeasonDetectionFailure,
    _count_squad_rows,
    detect_latest_populated_season,
    resolve_season_arg,
)


def _mock_response(html: str = "", status: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.text = html
    resp.status_code = status
    resp.raise_for_status = MagicMock()
    if status >= 400:
        resp.raise_for_status.side_effect = requests.HTTPError(f"HTTP {status}")
    return resp


class TestCountSquadRows:
    def test_empty_html(self):
        assert _count_squad_rows("") == 0

    def test_real_squad_pattern(self):
        html = """
        <tr><td><a class="hauptlink" href="...">Player 1</a></td></tr>
        <tr><td><a class="hauptlink" href="...">Player 2</a></td></tr>
        <tr><td><a class="hauptlink" href="...">Player 3</a></td></tr>
        """
        assert _count_squad_rows(html) == 3

    def test_distinguishes_populated_from_stub(self):
        """Real seasons have ~29 hauptlink rows; stub seasons have ~4."""
        full = "\n".join(['<a class="hauptlink">X</a>'] * 29)
        stub = "\n".join(['<a class="hauptlink">X</a>'] * 4)
        assert _count_squad_rows(full) == 29
        assert _count_squad_rows(stub) == 4


class TestDetectLatestPopulatedSeason:
    def _session_returning(self, html_by_year: dict[int, str]) -> MagicMock:
        """Build a mock session that returns each year's html."""
        session = MagicMock(spec=requests.Session)

        def fake_get(url, timeout=None):
            import re
            m = re.search(r"saison_id/(\d+)", url)
            year = int(m.group(1)) if m else 0
            html = html_by_year.get(year, "")
            return _mock_response(html)

        session.get.side_effect = fake_get
        return session

    def test_finds_highest_populated(self):
        """The iter-cycle-1 scenario: 2024 + 2025 populated, 2026 sparse.
        Detector returns 2025."""
        populated = "\n".join(['<a class="hauptlink">X</a>'] * 29)
        sparse = "\n".join(['<a class="hauptlink">X</a>'] * 4)
        session = self._session_returning({
            2024: populated,
            2025: populated,
            2026: sparse,
            2027: sparse,
        })
        result = detect_latest_populated_season(
            session=session, start_year=2024, end_year=2030,
        )
        assert result == 2025

    def test_stops_at_first_sparse(self):
        """Detector should NOT continue past the first sparse year (sparseness
        propagates forward; probing further wastes HTTP calls)."""
        populated = "\n".join(['<a class="hauptlink">X</a>'] * 29)
        sparse = "\n".join(['<a class="hauptlink">X</a>'] * 4)
        session = self._session_returning({
            2024: populated,
            2025: sparse,
            2026: populated,  # Wouldn't get probed
        })
        result = detect_latest_populated_season(
            session=session, start_year=2024, end_year=2030,
        )
        # 2024 is last populated; 2025 sparse stops the search.
        assert result == 2024
        # And we should NOT have probed 2026
        urls = [call.args[0] for call in session.get.call_args_list]
        assert all("saison_id/2026" not in u for u in urls)

    def test_network_failure_returns_last_populated(self):
        """If a probe HTTP-fails (network down, TM serving 500), return the
        last known populated year — don't crash the pipeline."""
        populated = "\n".join(['<a class="hauptlink">X</a>'] * 29)
        session = MagicMock(spec=requests.Session)
        call_count = [0]

        def flaky_get(url, timeout=None):
            call_count[0] += 1
            if call_count[0] >= 3:
                raise requests.ConnectionError("transient")
            return _mock_response(populated)

        session.get.side_effect = flaky_get
        result = detect_latest_populated_season(
            session=session, start_year=2024, end_year=2030,
        )
        # First 2 probes succeed (populated for 2024 + 2025); 3rd fails.
        # Last populated = 2025.
        assert result == 2025

    def test_first_probe_blocked_raises_not_silent_2020(self):
        """§6 high #7 fix (2026-06-12 review): when the very FIRST probe
        fails (e.g. ScraperAPI bypassed + TM blocking direct requests),
        the detector used to silently return `start_year` (which means
        --season=latest resolved to 2020 on every blocked run). Now it
        raises SeasonDetectionFailure so the operator sees the actual
        failure."""
        session = MagicMock(spec=requests.Session)
        session.get.side_effect = requests.ConnectionError("blocked")
        with pytest.raises(SeasonDetectionFailure) as exc:
            detect_latest_populated_season(
                session=session, start_year=2020, end_year=2030,
            )
        assert "no successful probe" in str(exc.value)
        # The error must explain the recovery action (pass --season=<year>).
        assert "--season" in str(exc.value)

    def test_first_probe_sparse_raises_not_silent_start_year(self):
        """Edge: TM IS reachable but the first probe (e.g. 2020) returns
        sparse data — old code silently returned 2020. The new code
        treats this as 'cannot confidently identify latest' and raises."""
        sparse = "\n".join(['<a class="hauptlink">X</a>'] * 4)
        session = MagicMock(spec=requests.Session)
        session.get.return_value = _mock_response(sparse)
        with pytest.raises(SeasonDetectionFailure) as exc:
            detect_latest_populated_season(
                session=session, start_year=2020, end_year=2030,
            )
        # The error mentions the row count + threshold so the operator can
        # decide whether to lower the threshold or fix TM.
        assert "sparse" in str(exc.value)

    def test_threshold_boundary(self):
        """Edge case: a season has exactly _MIN_SQUAD_ROWS rows. Should be
        counted as populated."""
        boundary_html = "\n".join(['<a class="hauptlink">X</a>'] * 15)
        session = self._session_returning({
            2024: boundary_html,
            2025: "\n".join(['<a class="hauptlink">X</a>'] * 14),
        })
        result = detect_latest_populated_season(
            session=session, start_year=2024, end_year=2030,
            min_squad_rows=15,
        )
        assert result == 2024  # 2025's 14 rows are below threshold


class TestResolveSeasonArg:
    def test_year_passes_through(self):
        assert resolve_season_arg("2024") == "2024"
        assert resolve_season_arg("2025") == "2025"

    def test_latest_invokes_detector(self):
        """--season=latest delegates to detect_latest_populated_season."""
        with patch(
            "data_pipeline.season_detector.detect_latest_populated_season",
            return_value=2025,
        ):
            result = resolve_season_arg("latest")
            assert result == "2025"

    def test_latest_case_insensitive(self):
        with patch(
            "data_pipeline.season_detector.detect_latest_populated_season",
            return_value=2025,
        ):
            assert resolve_season_arg("LATEST") == "2025"
            assert resolve_season_arg("Latest") == "2025"
