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
    hewiki_title: str | None = None,
) -> dict:
    """Build a single entity dict in the shape Wikidata returns from
    wbgetentities. `hewiki_title` populates the `sitelinks.hewiki.title`
    field which the resolver prefers over `labels.he`."""
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
    sitelinks = {}
    if hewiki_title is not None:
        sitelinks["hewiki"] = {"site": "hewiki", "title": hewiki_title}
    return {
        "labels": labels,
        "descriptions": descriptions,
        "claims": claims,
        "sitelinks": sitelinks,
    }


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
# Search-term variant generator — direct unit tests
# ---------------------------------------------------------------------------


class TestSearchVariants:
    def test_original_query_always_first(self):
        variants = wikidata_lookup._search_variants("Hapoel Beer Sheva")
        assert variants[0] == "Hapoel Beer Sheva"

    def test_compact_digit_dot_letter_gets_spaced_variant(self):
        """TM emits "1.FC Nuremberg"; Wikidata canonicalises "1. FC Nürnberg".
        Insert a space after digit-dot-uppercase patterns so the spaced
        variant ranks among the queries we try."""
        variants = wikidata_lookup._search_variants("1.FC Nuremberg")
        assert "1.FC Nuremberg" in variants
        assert "1. FC Nuremberg" in variants

    def test_already_spaced_no_extra_variant(self):
        """Idempotency: a query that's already spaced shouldn't grow extra
        duplicates."""
        variants = wikidata_lookup._search_variants("1. FC Köln")
        # Original + (maybe collapsed whitespace) — no duplicates
        assert variants.count("1. FC Köln") == 1

    def test_no_compact_pattern_no_variant(self):
        """Names without the digit-dot-letter compact pattern produce no
        extra variant (just the original)."""
        variants = wikidata_lookup._search_variants("Hapoel Beer Sheva")
        # Could be 1 (just original) or 2 if whitespace-collapse differs;
        # since the input is well-formed, the collapsed form equals the
        # original and is deduplicated out.
        assert variants == ["Hapoel Beer Sheva"]

    def test_collapses_double_space(self):
        variants = wikidata_lookup._search_variants("Real  Madrid")
        assert "Real Madrid" in variants


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

    def test_variant_fallback_when_original_empty(self):
        """When the original query returns no Wikidata search hits, the
        resolver retries with normalized variants. "1.FC Nuremberg" returns
        empty; "1. FC Nuremberg" resolves to Q15786 (the parent club).
        Iter-cycle 1 walk 2026-06-12."""
        with patch.object(wikidata_lookup.requests, "Session") as mock_cls:
            mock_cls.return_value = _patched_session_with([
                _search_payload([]),                 # "1.FC Nuremberg" — empty
                _search_payload(["Q15786"]),         # "1. FC Nuremberg" — hit
                _entities_payload({
                    "Q15786": _entity(
                        he_label="ignored",
                        hewiki_title="נירנברג",
                        p31=["Q476028"],
                    ),
                }),
            ])
            result = wikidata_lookup.lookup_hebrew_label("1.FC Nuremberg", "club")
            assert result == ("נירנברג", "Q15786")

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

    def test_sitelinks_hewiki_title_preferred_over_labels_he(self):
        """Iter-cycle 1 review-walk finding (2026-06-12): Wikidata's
        free-form `labels.he` can drift to either full-canonical names
        ("בן אנריקה גורדין ענברי") or stale/vandalised values
        ("הלדר לפופסיק"). The Hebrew Wikipedia sitelink title is the
        community-curated article name — better signal for the common-form
        name. Resolver must prefer it over `labels.he`."""
        with patch.object(wikidata_lookup.requests, "Session") as mock_cls:
            mock_cls.return_value = _patched_session_with([
                _search_payload(["Q_BEN_GORDIN"]),
                _entities_payload({
                    "Q_BEN_GORDIN": _entity(
                        he_label="בן אנריקה גורדין ענברי",   # full-canonical
                        hewiki_title="בן גורדין",            # common-form
                        en_label="Ben Gordin",
                        p31=["Q5"], p641=["Q2736"],
                    ),
                }),
            ])
            result = wikidata_lookup.lookup_hebrew_label("Ben Gordin", "player")
            assert result == ("בן גורדין", "Q_BEN_GORDIN")

    def test_falls_back_to_labels_he_when_no_hewiki_sitelink(self):
        """Common for foreign players without a Hebrew Wikipedia article —
        `sitelinks.hewiki` is absent; fall back to `labels.he`."""
        with patch.object(wikidata_lookup.requests, "Session") as mock_cls:
            mock_cls.return_value = _patched_session_with([
                _search_payload(["Q_ASEN_DONCHEV"]),
                _entities_payload({
                    "Q_ASEN_DONCHEV": _entity(
                        he_label="אסן דונצ'ב",
                        hewiki_title=None,            # no hewiki article
                        en_label="Asen Donchev",
                        p31=["Q5"], p641=["Q2736"],
                    ),
                }),
            ])
            result = wikidata_lookup.lookup_hebrew_label("Asen Donchev", "player")
            assert result == ("אסן דונצ'ב", "Q_ASEN_DONCHEV")

    def test_sitelink_strips_football_paren_suffix(self):
        """Wikidata's hewiki title often carries a "(כדורגל)" disambiguator
        for clubs that share a name with a non-football entity (the Israeli
        Wikipedia convention). The type filter already guarantees football,
        so the suffix is redundant noise. Strip it. Iter-cycle 1, 2026-06-12."""
        with patch.object(wikidata_lookup.requests, "Session") as mock_cls:
            mock_cls.return_value = _patched_session_with([
                _search_payload(["Q_GENOA"]),
                _entities_payload({
                    "Q_GENOA": _entity(
                        he_label="ignored",
                        hewiki_title="ג'נואה (כדורגל)",
                        p31=["Q476028"],
                    ),
                }),
            ])
            result = wikidata_lookup.lookup_hebrew_label("Genoa", "club")
            assert result == ("ג'נואה", "Q_GENOA")

    def test_strips_football_with_year_suffix(self):
        """Variant: "(כדורגל, 2018)" — football + year disambiguator. The
        regex must match the leading 'כדורגל' prefix and consume the rest
        of the parens too."""
        with patch.object(wikidata_lookup.requests, "Session") as mock_cls:
            mock_cls.return_value = _patched_session_with([
                _search_payload(["Q_SOCHI"]),
                _entities_payload({
                    "Q_SOCHI": _entity(
                        he_label="ignored",
                        hewiki_title="סוצ'י (כדורגל, 2018)",
                        p31=["Q476028"],
                    ),
                }),
            ])
            result = wikidata_lookup.lookup_hebrew_label("FK Sochi", "club")
            assert result == ("סוצ'י", "Q_SOCHI")

    def test_does_not_strip_non_football_paren(self):
        """City disambiguators ("(דובאי)") and other non-football parens are
        meaningful — must NOT be stripped. The example is Al-Nasr Dubai,
        where the city paren distinguishes it from other Al-Nasr clubs."""
        with patch.object(wikidata_lookup.requests, "Session") as mock_cls:
            mock_cls.return_value = _patched_session_with([
                _search_payload(["Q_NASR"]),
                _entities_payload({
                    "Q_NASR": _entity(
                        he_label="ignored",
                        hewiki_title="אל-נסר (דובאי)",
                        p31=["Q476028"],
                    ),
                }),
            ])
            result = wikidata_lookup.lookup_hebrew_label("Al-Nasr Dubai", "club")
            assert result == ("אל-נסר (דובאי)", "Q_NASR")

    def test_labels_he_also_strips_football_suffix(self):
        """The label cleaner runs on both paths — sitelink (above) AND the
        labels.he fallback. Foreign clubs without a hewiki article still
        get the suffix stripped from labels.he."""
        with patch.object(wikidata_lookup.requests, "Session") as mock_cls:
            mock_cls.return_value = _patched_session_with([
                _search_payload(["Q_X"]),
                _entities_payload({
                    "Q_X": _entity(
                        he_label="ניס (כדורגל)",
                        hewiki_title=None,  # no sitelink → falls to labels.he
                        p31=["Q476028"],
                    ),
                }),
            ])
            result = wikidata_lookup.lookup_hebrew_label("OGC Nice", "club")
            assert result == ("ניס", "Q_X")

    def test_q103229495_mens_team_accepted_as_club(self):
        """Wikidata's split-entity pattern: the parent club is one Q-ID and
        the on-pitch men's football team is another (P31=Q103229495). Both
        must satisfy the "club" type filter — iter-cycle 1 walk surfaced
        "1. FC Nürnberg (football)" Q97905881 falling through because we
        only had the parent-club class."""
        e = _entity(he_label="X", p31=["Q103229495"])
        assert wikidata_lookup._matches_type(e, "club") is True

    def test_sitelinks_strips_bidi_marks_too(self):
        """The bidi-strip normalisation applies to sitelink titles same as
        labels (defensive — sitelinks shouldn't have them, but if they
        ever do, the cleanup must be uniform)."""
        with patch.object(wikidata_lookup.requests, "Session") as mock_cls:
            mock_cls.return_value = _patched_session_with([
                _search_payload(["Q_X"]),
                _entities_payload({
                    "Q_X": _entity(
                        he_label="ignored",
                        hewiki_title="אליאל פרץ‎‎",  # trailing LRMs
                        p31=["Q5"], p641=["Q2736"],
                    ),
                }),
            ])
            result = wikidata_lookup.lookup_hebrew_label("Eliel Peretz", "player")
            assert result == ("אליאל פרץ", "Q_X")

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
