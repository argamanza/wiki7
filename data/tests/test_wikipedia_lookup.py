"""Tests for the Hebrew Wikipedia name lookup helper.

Live HTTP is mocked via `requests-mock` style monkeypatching on the
session object so tests stay offline + fast.
"""

from unittest.mock import MagicMock, patch


from data_pipeline import wikipedia_lookup


def _mock_response(payload: dict) -> MagicMock:
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json = MagicMock(return_value=payload)
    return resp


def _query_payload(hebrew_title: str | None, *, missing: bool = False) -> dict:
    """Build a Wikipedia langlinks API response payload.

    `missing=True` simulates "no English article exists".
    `hebrew_title=None` simulates "page exists but no Hebrew langlink".
    """
    if missing:
        return {"query": {"pages": {"-1": {"ns": 0, "title": "X", "missing": ""}}}}
    page = {"pageid": 12345, "ns": 0, "title": "X"}
    if hebrew_title is not None:
        page["langlinks"] = [{"lang": "he", "*": hebrew_title}]
    return {"query": {"pages": {"12345": page}}}


class TestLookupHebrewTitle:
    def test_resolves_known_player(self):
        """English article with Hebrew langlink → returns Hebrew title."""
        with patch.object(wikipedia_lookup.requests, "Session") as mock_session_cls:
            mock_session = MagicMock()
            mock_session.headers = {}
            mock_session.get.return_value = _mock_response(
                _query_payload("ליאור רפאלוב")
            )
            mock_session.__enter__ = lambda self: mock_session
            mock_session.__exit__ = lambda *a, **kw: None
            mock_session_cls.return_value = mock_session
            assert wikipedia_lookup.lookup_hebrew_title("Lior Refaelov") == "ליאור רפאלוב"

    def test_page_missing_returns_none(self):
        """Player has no English Wikipedia article → None."""
        with patch.object(wikipedia_lookup.requests, "Session") as mock_session_cls:
            mock_session = MagicMock()
            mock_session.headers = {}
            mock_session.get.return_value = _mock_response(
                _query_payload(None, missing=True)
            )
            mock_session.__enter__ = lambda self: mock_session
            mock_session.__exit__ = lambda *a, **kw: None
            mock_session_cls.return_value = mock_session
            assert wikipedia_lookup.lookup_hebrew_title("Obscure Player") is None

    def test_page_exists_but_no_hebrew_langlink(self):
        """English article without Hebrew langlink → None (Claude fallback)."""
        with patch.object(wikipedia_lookup.requests, "Session") as mock_session_cls:
            mock_session = MagicMock()
            mock_session.headers = {}
            mock_session.get.return_value = _mock_response(
                _query_payload(None)  # exists but no langlinks
            )
            mock_session.__enter__ = lambda self: mock_session
            mock_session.__exit__ = lambda *a, **kw: None
            mock_session_cls.return_value = mock_session
            assert wikipedia_lookup.lookup_hebrew_title("Some Player") is None

    def test_transient_http_error_returns_none(self):
        """Wikipedia hiccup must NOT abort the pipeline — return None and
        let the caller fall back to Claude."""
        import requests

        with patch.object(wikipedia_lookup.requests, "Session") as mock_session_cls:
            mock_session = MagicMock()
            mock_session.headers = {}
            mock_session.get.side_effect = requests.RequestException("transient")
            mock_session.__enter__ = lambda self: mock_session
            mock_session.__exit__ = lambda *a, **kw: None
            mock_session_cls.return_value = mock_session
            assert wikipedia_lookup.lookup_hebrew_title("Anyone") is None

    def test_unparseable_json_returns_none(self):
        with patch.object(wikipedia_lookup.requests, "Session") as mock_session_cls:
            mock_session = MagicMock()
            mock_session.headers = {}
            bad_resp = MagicMock()
            bad_resp.raise_for_status = MagicMock()
            bad_resp.json.side_effect = ValueError("not json")
            mock_session.get.return_value = bad_resp
            mock_session.__enter__ = lambda self: mock_session
            mock_session.__exit__ = lambda *a, **kw: None
            mock_session_cls.return_value = mock_session
            assert wikipedia_lookup.lookup_hebrew_title("Anyone") is None


class TestLookupBatch:
    def test_empty_input_returns_empty(self):
        # Empty input doesn't even hit the network.
        assert wikipedia_lookup.lookup_batch([]) == {}

    def test_mixed_resolved_and_unresolved(self):
        """A batch where some names resolve + some don't."""
        # Use a fake _query rather than mocking requests.Session — the
        # batch path constructs the session and threads it through.
        payloads = {
            "Lior Refaelov": "ליאור רפאלוב",
            "Sagiv Jehezkel": "שגיב יחזקאל",
            "Made Up Name": None,
        }

        def fake_query(session, title):
            return payloads[title]

        with patch.object(wikipedia_lookup, "_query", side_effect=fake_query):
            results = wikipedia_lookup.lookup_batch(list(payloads.keys()))

        assert results["Lior Refaelov"] == "ליאור רפאלוב"
        assert results["Sagiv Jehezkel"] == "שגיב יחזקאל"
        assert results["Made Up Name"] is None

    def test_exceptions_in_one_lookup_dont_kill_the_batch(self):
        def fake_query(session, title):
            if title == "Bad":
                raise RuntimeError("boom")
            return f"HE:{title}"

        with patch.object(wikipedia_lookup, "_query", side_effect=fake_query):
            results = wikipedia_lookup.lookup_batch(["Good", "Bad", "Other"])

        assert results["Good"] == "HE:Good"
        assert results["Bad"] is None
        assert results["Other"] == "HE:Other"
