"""Tests for the Wikidata Hebrew-label lookup helper.

All HTTP is mocked. Tests stay offline + fast. The mock helpers reproduce
the shape of the two endpoints we hit (`wbsearchentities` and
`wbgetentities`) precisely enough to exercise the type-filter logic.
"""

from unittest.mock import MagicMock, patch

from data_pipeline import wikidata_lookup


# ---------------------------------------------------------------------------
# Mock payload builders
# ---------------------------------------------------------------------------


def _search_payload(qids: list[str]) -> dict:
    return {"search": [{"id": q, "label": q, "description": ""} for q in qids]}


def _entity(
    *,
    he_label: str | None,
    en_label: str = "X",
    en_desc: str = "",
    p31: list[str] | None = None,
    p641: list[str] | None = None,
) -> dict:
    """Build a single entity dict in the shape Wikidata returns from
    wbgetentities."""
    labels = {"en": {"value": en_label}}
    if he_label is not None:
        labels["he"] = {"value": he_label}
    descriptions = {"en": {"value": en_desc}} if en_desc else {}
    claims = {}
    if p31:
        claims["P31"] = [
            {"mainsnak": {"datavalue": {"value": {"id": q}}}} for q in p31
        ]
    if p641:
        claims["P641"] = [
            {"mainsnak": {"datavalue": {"value": {"id": q}}}} for q in p641
        ]
    return {"labels": labels, "descriptions": descriptions, "claims": claims}


def _entities_payload(entities: dict[str, dict]) -> dict:
    return {"entities": entities}


def _mock_response(payload: dict) -> MagicMock:
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json = MagicMock(return_value=payload)
    return resp


def _patched_session_with(responses: list[dict]):
    """Patch `requests.Session()` so each .get() call returns the next
    payload in `responses` (in order). Lets us drive search → get sequences
    explicitly in tests.
    """
    mock_session = MagicMock()
    mock_session.headers = {}
    mock_session.get.side_effect = [_mock_response(p) for p in responses]
    mock_session.__enter__ = lambda self: mock_session
    mock_session.__exit__ = lambda *a, **kw: None
    return mock_session


# ---------------------------------------------------------------------------
# Type filter — direct unit tests on _matches_type
# ---------------------------------------------------------------------------


class TestMatchesType:
    def test_player_strict_p31_p5_p641_q2736(self):
        e = _entity(he_label="ליאור רפאלוב", p31=["Q5"], p641=["Q2736"])
        assert wikidata_lookup._matches_type(e, "player") is True

    def test_player_human_but_wrong_sport_rejected(self):
        e = _entity(he_label="X", p31=["Q5"], p641=["Q5369"])  # baseball
        assert wikidata_lookup._matches_type(e, "player") is False

    def test_player_human_no_sport_but_football_description_accepted(self):
        # Coach / manager case — no P641 on Wikidata but description shows
        # football role. Without this fallback we'd miss managers.
        e = _entity(
            he_label="X", p31=["Q5"], en_desc="Israeli football manager",
        )
        assert wikidata_lookup._matches_type(e, "player") is True

    def test_player_not_human_rejected(self):
        # "Lior Cohen" search returns researchers with P31=Q5 but also
        # objects, places etc — non-Q5 is always a no-match for player.
        e = _entity(he_label="X", p31=["Q1248784"])
        assert wikidata_lookup._matches_type(e, "player") is False

    def test_club_football_club_accepted(self):
        e = _entity(he_label="הפועל באר שבע", p31=["Q476028"])
        assert wikidata_lookup._matches_type(e, "club") is True

    def test_club_basketball_club_rejected(self):
        # "Hapoel Be'er Sheva" search returns football club AND basketball
        # club. The basketball club (Q13027888 = pro basketball team) must
        # NOT match.
        e = _entity(he_label="X", p31=["Q13027888"])
        assert wikidata_lookup._matches_type(e, "club") is False

    def test_competition_p31_football_tournament_accepted(self):
        e = _entity(he_label="ליגת האלופות", p31=["Q500834"])
        assert wikidata_lookup._matches_type(e, "competition") is True

    def test_competition_description_fallback(self):
        # Sometimes a competition has P31=Q170645 (tournament, generic) but
        # the description says "football tournament" — fallback path.
        e = _entity(
            he_label="X", p31=["Q170645"], en_desc="football tournament",
        )
        assert wikidata_lookup._matches_type(e, "competition") is True

    def test_country_q6256_accepted(self):
        e = _entity(he_label="ישראל", p31=["Q6256"])
        assert wikidata_lookup._matches_type(e, "country") is True

    def test_country_male_given_name_rejected(self):
        # "Israel" search also surfaces male given names (Q19819746) —
        # those must NOT match `country`.
        e = _entity(he_label="ישראל", p31=["Q12308941"])
        assert wikidata_lookup._matches_type(e, "country") is False

    def test_empty_entity_rejected(self):
        # Wikidata occasionally returns empty entity shells (no claims).
        # Must not crash, must not match.
        e = {"labels": {}, "descriptions": {}, "claims": {}}
        assert wikidata_lookup._matches_type(e, "player") is False
        assert wikidata_lookup._matches_type(e, "club") is False


# ---------------------------------------------------------------------------
# lookup_hebrew_label — single-name path
# ---------------------------------------------------------------------------


class TestLookupHebrewLabel:
    def test_resolves_known_player(self):
        """Happy path: search → get → first hit matches → Hebrew label returned."""
        with patch.object(wikidata_lookup.requests, "Session") as mock_cls:
            mock_cls.return_value = _patched_session_with([
                _search_payload(["Q964300"]),
                _entities_payload({
                    "Q964300": _entity(
                        he_label="ליאור רפאלוב",
                        en_label="Lior Refaelov",
                        p31=["Q5"], p641=["Q2736"],
                    ),
                }),
            ])
            result = wikidata_lookup.lookup_hebrew_label("Lior Refaelov", "player")
            assert result == ("ליאור רפאלוב", "Q964300")

    def test_disambiguation_skips_non_footballers(self):
        """`Lior Cohen` returns 3 humans (researcher, actor, footballer) —
        first two must be skipped, third returned."""
        with patch.object(wikidata_lookup.requests, "Session") as mock_cls:
            mock_cls.return_value = _patched_session_with([
                _search_payload(["Q_RESEARCHER", "Q_ACTOR", "Q_FOOTBALLER"]),
                _entities_payload({
                    "Q_RESEARCHER": _entity(
                        he_label="חוקר", p31=["Q5"],
                        en_desc="gravitational wave researcher",
                    ),
                    "Q_ACTOR": _entity(
                        he_label="שחקן", p31=["Q5"],
                        en_desc="Israeli stage actor",
                    ),
                    "Q_FOOTBALLER": _entity(
                        he_label="ליאור כהן", p31=["Q5"], p641=["Q2736"],
                        en_desc="Israeli footballer",
                    ),
                }),
            ])
            result = wikidata_lookup.lookup_hebrew_label("Lior Cohen", "player")
            assert result == ("ליאור כהן", "Q_FOOTBALLER")

    def test_first_match_with_missing_he_label_returns_none(self):
        """When the FIRST type-matching entity has no Hebrew label, return
        None so the caller falls back to Claude — don't keep scanning to
        find a less-relevant entity that happens to have a Hebrew label."""
        with patch.object(wikidata_lookup.requests, "Session") as mock_cls:
            mock_cls.return_value = _patched_session_with([
                _search_payload(["Q_BEST", "Q_WORSE"]),
                _entities_payload({
                    "Q_BEST": _entity(
                        he_label=None,  # no Hebrew label
                        en_label="Obscure Player",
                        p31=["Q5"], p641=["Q2736"],
                    ),
                    "Q_WORSE": _entity(
                        he_label="שם אחר",
                        p31=["Q5"], p641=["Q2736"],
                    ),
                }),
            ])
            result = wikidata_lookup.lookup_hebrew_label("Obscure Player", "player")
            assert result is None

    def test_no_search_hits_returns_none(self):
        with patch.object(wikidata_lookup.requests, "Session") as mock_cls:
            mock_cls.return_value = _patched_session_with([
                _search_payload([]),
            ])
            assert wikidata_lookup.lookup_hebrew_label("Zzzzz", "player") is None

    def test_no_type_match_returns_none(self):
        """Search returns candidates but none satisfy the type filter."""
        with patch.object(wikidata_lookup.requests, "Session") as mock_cls:
            mock_cls.return_value = _patched_session_with([
                _search_payload(["Q_ACTOR", "Q_RESEARCHER"]),
                _entities_payload({
                    "Q_ACTOR": _entity(he_label="שחקן", p31=["Q5"],
                                       en_desc="actor"),
                    "Q_RESEARCHER": _entity(he_label="חוקר", p31=["Q5"],
                                            en_desc="biologist"),
                }),
            ])
            assert wikidata_lookup.lookup_hebrew_label("Lior Cohen", "player") is None

    def test_empty_entity_shell_skipped(self):
        """Wikidata occasionally returns empty entity shells for stale Q-IDs.
        Must skip them without crashing."""
        with patch.object(wikidata_lookup.requests, "Session") as mock_cls:
            mock_cls.return_value = _patched_session_with([
                _search_payload(["Q_STALE", "Q_REAL"]),
                _entities_payload({
                    "Q_STALE": {"labels": {}, "descriptions": {}, "claims": {}},
                    "Q_REAL": _entity(
                        he_label="ליאור רפאלוב",
                        p31=["Q5"], p641=["Q2736"],
                    ),
                }),
            ])
            result = wikidata_lookup.lookup_hebrew_label("Lior Refaelov", "player")
            assert result == ("ליאור רפאלוב", "Q_REAL")

    def test_transient_http_error_returns_none(self):
        """Wikidata hiccup must NOT abort the pipeline — return None and
        let the caller fall back to Claude. Patches time.sleep to skip the
        backoff so this test stays fast."""
        import requests as r

        with patch.object(wikidata_lookup.requests, "Session") as mock_cls, \
             patch.object(wikidata_lookup.time, "sleep"):
            mock_session = MagicMock()
            mock_session.headers = {}
            mock_session.get.side_effect = r.RequestException("transient")
            mock_session.__enter__ = lambda self: mock_session
            mock_session.__exit__ = lambda *a, **kw: None
            mock_cls.return_value = mock_session
            assert wikidata_lookup.lookup_hebrew_label("Anyone", "player") is None

    def test_search_relevance_order_preserved(self):
        """wbgetentities may return entities in arbitrary dict order; the
        function must iterate in search-relevance order, not response order."""
        with patch.object(wikidata_lookup.requests, "Session") as mock_cls:
            # Search returns A first (most relevant), then B
            mock_cls.return_value = _patched_session_with([
                _search_payload(["Q_FIRST", "Q_SECOND"]),
                # Entity dict has B first — dict iteration order would
                # incorrectly prefer B if the code relied on it
                _entities_payload({
                    "Q_SECOND": _entity(
                        he_label="שני", p31=["Q5"], p641=["Q2736"],
                    ),
                    "Q_FIRST": _entity(
                        he_label="ראשון", p31=["Q5"], p641=["Q2736"],
                    ),
                }),
            ])
            result = wikidata_lookup.lookup_hebrew_label("Anyone", "player")
            assert result == ("ראשון", "Q_FIRST")

    def test_club_lookup(self):
        with patch.object(wikidata_lookup.requests, "Session") as mock_cls:
            mock_cls.return_value = _patched_session_with([
                _search_payload(["Q_FOOTBALL_CLUB", "Q_BASKETBALL_CLUB"]),
                _entities_payload({
                    "Q_FOOTBALL_CLUB": _entity(
                        he_label="הפועל באר שבע", p31=["Q476028"],
                    ),
                    "Q_BASKETBALL_CLUB": _entity(
                        he_label="הפועל באר שבע כדורסל", p31=["Q13027888"],
                    ),
                }),
            ])
            result = wikidata_lookup.lookup_hebrew_label("Hapoel Beer Sheva", "club")
            assert result == ("הפועל באר שבע", "Q_FOOTBALL_CLUB")

    def test_country_lookup(self):
        with patch.object(wikidata_lookup.requests, "Session") as mock_cls:
            mock_cls.return_value = _patched_session_with([
                _search_payload(["Q801"]),
                _entities_payload({
                    "Q801": _entity(
                        he_label="ישראל", p31=["Q6256", "Q3624078"],
                    ),
                }),
            ])
            assert wikidata_lookup.lookup_hebrew_label("Israel", "country") \
                == ("ישראל", "Q801")

    def test_unicode_bidi_marks_stripped(self):
        """Wikidata Hebrew labels occasionally have trailing LRM (U+200E)
        or RLM marks — observed empirically on Q24450603 (Eliel Peretz).
        They're invisible to humans but break slug generation and YAML
        round-trip. Must be stripped at the source."""
        with patch.object(wikidata_lookup.requests, "Session") as mock_cls:
            mock_cls.return_value = _patched_session_with([
                _search_payload(["Q_DIRTY"]),
                _entities_payload({
                    "Q_DIRTY": _entity(
                        he_label="אליאל פרץ‎‎",  # trailing LRMs
                        p31=["Q5"], p641=["Q2736"],
                    ),
                }),
            ])
            result = wikidata_lookup.lookup_hebrew_label("Eliel Peretz", "player")
            assert result == ("אליאל פרץ", "Q_DIRTY")
            # Verify no invisible chars sneak through.
            assert "‎" not in result[0]
            assert "‏" not in result[0]


# ---------------------------------------------------------------------------
# lookup_batch — many-name parallel path
# ---------------------------------------------------------------------------


class TestLookupBatch:
    def test_empty_input_returns_empty(self):
        assert wikidata_lookup.lookup_batch([], "player") == {}

    def test_mixed_resolved_and_unresolved(self):
        """Three names — one resolves to Wikidata, one returns None
        (no type match), one raises in the worker."""
        payloads = {
            "Lior Refaelov": ("ליאור רפאלוב", "Q964300"),
            "Made Up Player": None,
        }

        def fake_resolve(session, name, entity_type):
            return payloads[name]

        with patch.object(wikidata_lookup, "_resolve_one", side_effect=fake_resolve):
            results = wikidata_lookup.lookup_batch(
                list(payloads.keys()), "player",
            )

        assert results["Lior Refaelov"] == ("ליאור רפאלוב", "Q964300")
        assert results["Made Up Player"] is None

    def test_exceptions_in_one_lookup_dont_kill_the_batch(self):
        def fake_resolve(session, name, entity_type):
            if name == "Bad":
                raise RuntimeError("boom")
            return (f"HE:{name}", f"Q_{name}")

        with patch.object(wikidata_lookup, "_resolve_one", side_effect=fake_resolve):
            results = wikidata_lookup.lookup_batch(
                ["Good", "Bad", "Other"], "player",
            )

        assert results["Good"] == ("HE:Good", "Q_Good")
        assert results["Bad"] is None
        assert results["Other"] == ("HE:Other", "Q_Other")
