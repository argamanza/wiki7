"""Wikidata-based Hebrew label lookup with type-aware disambiguation.

Phase 3a R2 step 10 surfaced that English Wikipedia langlinks only catches
0.8% of the all-time HBS name corpus — most Israeli-league players don't
have English Wikipedia articles, so the existing `wikipedia_lookup` first-
pass falls through to Claude for ~99% of names. Claude produces plausible-
but-not-canonical Hebrew transliterations.

Wikidata is a far better cross-language bridge: `wbsearchentities` returns
Q-IDs even for entities without an English Wikipedia article, and
`wbgetentities&languages=he` returns canonical Hebrew labels populated by
Hebrew Wikipedia editors. Verified empirically (2026-06-10):

  Lior Refaelov     → wbsearch → Q964300 → labels.he → ליאור רפאלוב    ✓
  Hapoel Beer Sheva → wbsearch → Q986529 → labels.he → הפועל באר שבע   ✓
  UEFA CL           → wbsearch → Q18756  → labels.he → ליגת האלופות    ✓
  Israel            → wbsearch → Q801    → labels.he → ישראל            ✓

Strategy:

  1. wbsearchentities(search=<en>, language=en, limit=N)  →  candidate Q-IDs
  2. wbgetentities(ids=Q1|Q2|...)  →  batched fetch of labels+claims
  3. Filter candidates by expected entity type (P31, P641):
       - players:       P31 includes Q5 (human) AND P641 includes Q2736
                        (association football)  — strict footballer match;
                        falls through to description-keyword match for
                        managers / coaches who never played professionally
       - clubs:         P31 ∈ {Q476028, Q22687, Q15642541, Q15944511, ...}
       - competitions:  P31 ∈ {Q500834, Q623109, Q27020041, ...} OR
                        description-keyword match
       - countries:     P31 ∈ {Q6256, Q3624078, Q7275}
  4. Return (he_label, qid) for the first filtered match, or None if no
     candidate matches the type filter / has no Hebrew label.

Type filter is mandatory: "Lior Cohen" alone returns researchers + actors
in the top-5 with NO footballer present. Naive first-hit would resolve to
the wrong entity.

Cost: zero — Wikidata API is free. Polite concurrency (5 workers) +
batched entity-fetch keeps total wall-clock acceptable for ~5k names.
"""

from __future__ import annotations

import logging
import random
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# Unicode bidi-control characters that occasionally appear in Wikidata
# Hebrew labels (LRM, RLM, etc.). Strip them — they're invisible to humans
# but interfere with downstream slug generation, mediawiki title comparison,
# and YAML round-tripping. Verified on Q24450603 ("Eliel Peretz") which
# stores `אליאל פרץ‎‎`.
_BIDI_MARKS_RE = re.compile(r"[‎‏‪-‮⁦-⁩]")

_API_URL = "https://www.wikidata.org/w/api.php"
_USER_AGENT = (
    "wiki7-pipeline/1.0 "
    "(https://github.com/argamanza/wiki7; data-pipeline) "
    "python-requests"
)
_TIMEOUT_S = 15.0
# Wikidata's "client request rate" guidance (mediawiki.org/wiki/API:Etiquette)
# is "no more than ~3 concurrent requests" for non-bot tools. Iteration-cycle
# tuning lowered workers from 5 → 2 after observing systematic empty-search
# responses at 5 workers (only 32/546 names resolving while single-thread
# retests of the same names succeed). The 30% drop in raw throughput is
# more than compensated by the ~10× lift in resolved-coverage.
_MAX_WORKERS = 2
_SEARCH_LIMIT = 10
# wbgetentities allows up to 50 IDs per call; we batch all candidates from
# one search into a single fetch (always <= _SEARCH_LIMIT).

# maxlag is Wikidata's etiquette signal — when set, the server returns an
# error with `Retry-After` header (or a JSON `error.code=maxlag` body) if
# replication lag exceeds the threshold. We retry with backoff on that
# signal rather than silently returning None. 5 seconds is the value the
# Wikidata docs recommend for tools / bots.
_MAXLAG_SECONDS = 5
# Retry policy for transient failures (rate limit, maxlag, 5xx, JSON parse).
# Empirical: at 2 workers we still hit ~7% HTTP 429s on a 546-name batch
# (2026-06-10), and 77% of those final-failures resolved cleanly when
# retried in isolation. So the rate-limit reset window is on the order of
# seconds — a larger retry budget with longer backoff catches them. The
# total wall-clock cost is small because successful calls never retry.
_MAX_RETRIES = 5
_BACKOFF_BASE_S = 1.5
_BACKOFF_CAP_S = 12.0
_BACKOFF_JITTER_S = 0.8


# ---------------------------------------------------------------------------
# Entity-type predicates
# ---------------------------------------------------------------------------

# Wikidata classes (P31 values) we accept per entity type. Lists are
# intentionally broad — a club may be classified as Q476028 (assoc.
# football club), Q11410 (sports club), or one of several sub-types
# depending on the editor's tagging convention. Keeping the list broad
# minimises false negatives at the cost of occasional cross-domain hits,
# which the description-keyword secondary filter trims.
_P31_BY_TYPE = {
    "player": {"Q5"},  # human; P641 enforces football below
    "club": {
        "Q476028",       # association football club
        "Q22687",        # women's association football club
        "Q15942466",     # men's football club (rare)
        "Q15944511",     # football academy
        "Q11410",        # sports club (broadest)
        "Q103229495",    # men's association football team (split-entity
                         # pattern Wikidata now uses for the on-pitch team
                         # vs. the parent club. iter-cycle 1 walk caught
                         # "1. FC Nürnberg (football)" Q97905881 falling
                         # through because we only had the parent-club class.)
    },
    "competition": {
        "Q500834",       # football tournament
        "Q27020041",     # sports league season
        "Q1478437",      # sports league
        "Q623109",       # sports competition
        "Q13406554",     # sports season
        "Q170645",       # tournament (broadest)
    },
    "country": {
        "Q6256",         # country
        "Q3624078",      # sovereign state
        "Q7275",         # state
        "Q185086",       # constitutional state
    },
    "city": {
        "Q515",          # city
        "Q3957",         # town
        "Q486972",       # human settlement
        "Q1549591",      # big city
        "Q1093829",      # city in the United States
        "Q15284",        # municipality (broad, includes Israeli towns)
        "Q532",          # village
        "Q5084",         # hamlet
        "Q15078955",     # populated place in Israel
        "Q1907114",      # metropolitan area
    },
}

# Sport claim (P641) — used as a secondary filter for `player` to weed out
# unrelated humans named the same thing.
_P641_FOOTBALL = "Q2736"

# Description-keyword fallback for `player`: when the strict P641 filter
# misses (e.g. football managers / coaches who never played), accept any
# P31=Q5 candidate whose English description matches these terms.
_PLAYER_DESC_KEYWORDS = (
    "football", "footballer", "soccer",
    "manager", "coach", "midfielder", "defender",
    "forward", "striker", "goalkeeper",
)

# Description-keyword fallback for `competition`: many football
# competitions aren't tagged with P31=Q500834. Falls back to description.
_COMPETITION_DESC_KEYWORDS = (
    "football", "soccer", "association football",
)

# ---------------------------------------------------------------------------
# Hebrew label post-processing
# ---------------------------------------------------------------------------

# Football-disambiguation parenthetical Wikidata sometimes appends to club /
# competition Hebrew labels (e.g. "מנצ'סטר יונייטד (כדורגל)"). It's noise for
# our purposes — the entity-type filter already guarantees the result is a
# football entity, so the suffix is redundant. Strip only the football-
# specific suffix; leave non-football disambiguation alone (e.g. "אל-נסר
# (דובאי)" is a meaningful city disambiguator). Iter-cycle 1 walk 2026-06-12.
_PAREN_FOOTBALL_SUFFIX_RE = re.compile(r"\s*\(כדורגל[^)]*\)\s*$")


def _clean_he_label(raw: str) -> str:
    """Apply bidi-mark stripping + football-suffix trimming to a Hebrew label.

    The two passes commute (bidi marks won't appear inside the paren suffix
    in practice, but the ordering would be safe either way). Used by both
    the sitelink and labels.he paths.
    """
    cleaned = _BIDI_MARKS_RE.sub("", raw)
    cleaned = _PAREN_FOOTBALL_SUFFIX_RE.sub("", cleaned)
    return cleaned.strip()


# ---------------------------------------------------------------------------
# Search-term variant generation
# ---------------------------------------------------------------------------

# Patterns that turn a single TM-style English name into multiple Wikidata
# search variants. wbsearchentities is largely prefix-based — small spacing
# differences cause complete miss. Iter-cycle 1 walk surfaced "1.FC
# Nuremberg" returning ZERO hits while "1. FC Nuremberg" / "1. FC Nürnberg"
# both resolve cleanly. We trial multiple variants, in order, and accept
# the first type-matching one.
_DIGIT_DOT_LETTER_RE = re.compile(r"(\d\.)([A-Z])")


def _search_variants(query: str) -> list[str]:
    """Build an ordered list of query variants to try against Wikidata.

    The original query is always first — most names resolve on it directly.
    Variants come after, deduplicated. The set is intentionally small and
    syntactic (no domain-specific abbreviation expansion, no diacritic
    transliteration). Brittle expansions belong in the manual override
    layer, not here.

    Variants:
      - Insert space after compact "<digit>.<UPPER>" patterns ("1.FC" →
        "1. FC") — TM emits the compact form, Wikidata canonicalises the
        spaced form.
      - Collapse double-spaces.
    """
    variants = [query]
    spaced = _DIGIT_DOT_LETTER_RE.sub(r"\1 \2", query)
    if spaced != query:
        variants.append(spaced)
    # Defensive whitespace collapse after substitutions — a no-op for the
    # common case, but cheap.
    collapsed = re.sub(r"\s+", " ", query).strip()
    if collapsed and collapsed not in variants:
        variants.append(collapsed)
    return variants


def _matches_type(entity: dict, entity_type: str) -> bool:
    """Return True iff this entity satisfies the type filter for `entity_type`.

    Decision rules:
      - empty entity (no claims, no labels) → False
      - player → P31=Q5 AND (P641=Q2736 OR English description matches a
                football-related keyword)
      - club / country → P31 intersects the accepted-class set
      - competition → P31 intersects the accepted-class set OR English
                      description contains "football"/"soccer"
    """
    claims = entity.get("claims") or {}
    p31_values = {
        c.get("mainsnak", {}).get("datavalue", {}).get("value", {}).get("id")
        for c in claims.get("P31", [])
    }
    p31_values.discard(None)
    if not p31_values:
        # Empty / redirect-only entity. Wikidata returns these as shells
        # with no labels/claims/descriptions. Treat as a non-match.
        return False

    en_desc = (
        entity.get("descriptions", {}).get("en", {}).get("value") or ""
    ).lower()

    if entity_type == "player":
        if "Q5" not in p31_values:
            return False
        p641_values = {
            c.get("mainsnak", {}).get("datavalue", {}).get("value", {}).get("id")
            for c in claims.get("P641", [])
        }
        if _P641_FOOTBALL in p641_values:
            return True
        # Manager / coach fallback — common in older HBS rosters where the
        # subject was a coach without a documented playing career on
        # Wikidata. Description-keyword match is intentionally generous;
        # disambiguation is reviewer-validated downstream.
        return any(kw in en_desc for kw in _PLAYER_DESC_KEYWORDS)

    if entity_type == "competition":
        if p31_values & _P31_BY_TYPE["competition"]:
            return True
        return any(kw in en_desc for kw in _COMPETITION_DESC_KEYWORDS)

    accepted = _P31_BY_TYPE.get(entity_type)
    if not accepted:
        return False
    return bool(p31_values & accepted)


# ---------------------------------------------------------------------------
# API calls
# ---------------------------------------------------------------------------


def _api_call(session: requests.Session, params: dict, *, op: str) -> Optional[dict]:
    """Single Wikidata API call with maxlag + retry handling.

    Returns the parsed JSON body on success, None on permanent failure (after
    retries). Permanent failures are logged at WARNING — they're meaningful
    signal (rate-limit, maxlag, transport) that the caller should be able to
    see during a run. Silent None-returns hide rate-limiting and were the
    root cause of the 32/546 batch-mode coverage observed during the first
    2024/25 iteration cycle.

    `op` is a label used in log messages (e.g. "wbsearchentities Eran Zahavi").
    """
    params = {**params, "maxlag": _MAXLAG_SECONDS}
    last_error = None
    for attempt in range(_MAX_RETRIES + 1):
        try:
            response = session.get(_API_URL, params=params, timeout=_TIMEOUT_S)
            response.raise_for_status()
            data = response.json()
            # Wikidata returns HTTP 200 with `error.code=maxlag` body when
            # replication lag exceeds our threshold. Treat that the same as
            # a transport error.
            err = data.get("error") if isinstance(data, dict) else None
            if err and err.get("code") == "maxlag":
                retry_after = float(response.headers.get("Retry-After") or 1)
                last_error = f"maxlag (retry-after={retry_after}s)"
                if attempt < _MAX_RETRIES:
                    time.sleep(retry_after + random.uniform(0, _BACKOFF_JITTER_S))
                    continue
                logger.warning("Wikidata %s gave up after maxlag retries", op)
                return None
            return data
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else None
            last_error = f"HTTP {status}"
            # 429 (rate limit) and 5xx are retryable; 4xx (other) are not.
            if status not in {429, 500, 502, 503, 504} or attempt >= _MAX_RETRIES:
                logger.warning("Wikidata %s failed: %s", op, last_error)
                return None
        except (requests.RequestException, ValueError) as exc:
            last_error = type(exc).__name__
            if attempt >= _MAX_RETRIES:
                logger.warning("Wikidata %s failed after retries: %s", op, last_error)
                return None
        # Exponential backoff with jitter, capped to avoid pathological
        # waits when many calls retry simultaneously and the backoff
        # multiplier compounds.
        sleep_s = min(
            _BACKOFF_BASE_S * (2 ** attempt) + random.uniform(0, _BACKOFF_JITTER_S),
            _BACKOFF_CAP_S,
        )
        time.sleep(sleep_s)
    logger.warning("Wikidata %s exhausted retries: %s", op, last_error)
    return None


def _search(session: requests.Session, query: str) -> list[str]:
    """Top-N candidate Q-IDs for an English query, in Wikidata's relevance
    order. Returns [] on permanent failure so callers fall through to the
    next translation backend.
    """
    params = {
        "action": "wbsearchentities",
        "search": query,
        "language": "en",
        "format": "json",
        "limit": _SEARCH_LIMIT,
        "type": "item",
    }
    data = _api_call(session, params, op=f"wbsearchentities {query!r}")
    if not data:
        return []
    return [hit["id"] for hit in data.get("search", []) if hit.get("id")]


def _get_entities(session: requests.Session, qids: list[str]) -> dict[str, dict]:
    """Batched wbgetentities call. Returns the `entities` dict from the
    response (keyed by Q-ID) or `{}` on permanent failure.

    Caller may pass up to 50 Q-IDs per call (Wikidata limit). We always
    pass <= _SEARCH_LIMIT (10) since that's the search-step ceiling.

    Includes `sitelinks` so the resolver can prefer Hebrew Wikipedia article
    titles over `labels.he` for the common-form name selection. See
    `_resolve_one` for the rationale.
    """
    if not qids:
        return {}
    params = {
        "action": "wbgetentities",
        "ids": "|".join(qids),
        "languages": "he|en",
        "props": "labels|descriptions|claims|sitelinks",
        "sitefilter": "hewiki",
        "format": "json",
    }
    data = _api_call(session, params, op=f"wbgetentities {qids}")
    if not data:
        return {}
    return data.get("entities") or {}


def _resolve_with_variant(
    session: requests.Session,
    query: str,
    entity_type: str,
) -> Optional[tuple[str, str]]:
    """Search + entity-fetch + type-filter for a single concrete query
    string. Returns the first type-matching (he_label, qid) tuple, or None.

    Split out from `_resolve_one` so the variant loop can call it once per
    candidate query.
    """
    qids = _search(session, query)
    if not qids:
        return None
    entities = _get_entities(session, qids)
    # Iterate in Wikidata's search-relevance order (NOT dict order, since
    # wbgetentities may return entities in arbitrary order). The first
    # type-matching entity wins.
    for qid in qids:
        entity = entities.get(qid)
        if not entity:
            continue
        if not _matches_type(entity, entity_type):
            continue
        # Sitelinks-first: prefer the Hebrew Wikipedia article TITLE over
        # Wikidata's free-form `labels.he`. Rationale:
        #
        # Wikidata's `labels.he` is a free-form field that anyone can edit and
        # can drift to either the official full-canonical form ("בן אנריקה
        # גורדין ענברי") or a stale/vandalised value ("הלדר לפופסיק" — Hélder
        # Lopes Q5964151 actually carries this gibberish today). The Hebrew
        # Wikipedia sitelink, in contrast, is the actual article name on
        # he.wikipedia.org — much higher friction to edit, curated by a real
        # community of editors. For Hebrew football coverage specifically,
        # the hewiki article title is what readers expect ("בן גורדין" not
        # "בן אנריקה גורדין ענברי"; "הלדר לופש" not "הלדר לפופסיק").
        #
        # Discovered iter-cycle 1 review walk (2026-06-12); v2 of the lookup
        # chain that was deferred from the original 0003 plan.
        sitelinks = entity.get("sitelinks", {}) or {}
        hewiki_title = sitelinks.get("hewiki", {}).get("title") or ""
        if hewiki_title.strip():
            he_label = _clean_he_label(hewiki_title)
            if he_label:
                return he_label, qid
        # Fallback to labels.he when no hewiki sitelink (common for foreign
        # players without a Hebrew Wikipedia article).
        labels = entity.get("labels", {})
        raw_he = labels.get("he", {}).get("value") or ""
        he_label = _clean_he_label(raw_he)
        if not he_label:
            # Entity matches the type filter but has neither sitelink nor
            # Hebrew label. Don't keep scanning — the first type-match IS
            # the canonical entity for this query, and a missing Hebrew
            # form means Wikidata can't help. Fall through to Claude.
            return None
        return he_label, qid
    return None


def _resolve_one(
    session: requests.Session,
    query: str,
    entity_type: str,
) -> Optional[tuple[str, str]]:
    """Resolve `query` to a (he_label, qid) tuple via Wikidata.

    Generates a small ordered list of query variants from the input (see
    `_search_variants`) and returns the first variant that yields a type-
    matching entity. Most names resolve on the original; the variants
    catch TM's spacing artefacts (compact "1.FC" vs canonical "1. FC").

    Returns:
        (hebrew_label, qid)  — on success
        None                 — when no variant produced a candidate that
                               matched the type filter, or when the matched
                               candidate has no `labels.he` / hewiki title.

    The Hebrew-label-missing case is treated as "Wikidata can't help" —
    caller falls back to Claude, which can transliterate. This is
    intentional: we don't want to half-fill with an English fallback when
    the next backend can produce a real Hebrew form.
    """
    for variant in _search_variants(query):
        result = _resolve_with_variant(session, variant, entity_type)
        if result is not None:
            return result
    return None


# ---------------------------------------------------------------------------
# Public API — parallels wikipedia_lookup
# ---------------------------------------------------------------------------


def lookup_hebrew_label(
    name: str,
    entity_type: str = "player",
) -> Optional[tuple[str, str]]:
    """One-shot lookup. See `lookup_batch` for parallelised many-name use.

    `entity_type` ∈ {"player", "club", "competition", "country"}.
    """
    with requests.Session() as session:
        session.headers.update({"User-Agent": _USER_AGENT})
        return _resolve_one(session, name, entity_type)


def lookup_batch(
    names: list[str],
    entity_type: str = "player",
) -> dict[str, Optional[tuple[str, str]]]:
    """Parallel lookup over many names. Returns a dict mapping each input
    name to either `(hebrew_label, qid)` (on success) or `None` (when no
    candidate matched / no Hebrew label).

    Empty input list returns empty dict — no network calls.
    """
    if not names:
        return {}

    results: dict[str, Optional[tuple[str, str]]] = {name: None for name in names}
    with requests.Session() as session:
        session.headers.update({"User-Agent": _USER_AGENT})
        with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as executor:
            future_to_name = {
                executor.submit(_resolve_one, session, name, entity_type): name
                for name in names
            }
            done = 0
            total = len(names)
            for future in as_completed(future_to_name):
                name = future_to_name[future]
                try:
                    results[name] = future.result()
                except Exception as exc:  # noqa: BLE001 — never fatal
                    logger.debug("Wikidata batch error for %r: %s", name, exc)
                    results[name] = None
                done += 1
                if done % 50 == 0:
                    logger.info(
                        "  Wikidata lookup %d/%d (%s)...", done, total, entity_type,
                    )

    found = sum(1 for v in results.values() if v)
    logger.info(
        "Wikidata lookup (%s): %d/%d resolved (%.1f%% coverage)",
        entity_type, found, total, 100 * found / total if total else 0,
    )
    return results
