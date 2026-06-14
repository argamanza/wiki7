"""Derive per-competition (and keeper) statistics for HBS players from our
own match-event corpus — no Transfermarkt re-scrape required.

Background
----------
The club-level ``leistungsdaten`` page TM exposes (and that ``stats_spider``
scrapes) gives one *season total* per player for seven outfield fields
(appearances / goals / assists / yellow / second-yellow / red / minutes).
It carries **no goalkeeper columns** (clean sheets, goals conceded) and **no
per-competition split**. Those live only on TM's JS-rendered per-player page.

Rather than adopt a brittle headless-render scrape, we reconstruct the missing
dimensions from the match reports we already hold (``matches.he.json``). Every
match carries: ``venue`` (H/A), ``result`` ("home:away"), ``competition``,
``home_lineup``/``away_lineup`` (with ``tm_player_id``), ``goals[]`` (with
``scorer_tm_id``/``assist_tm_id``/``team``/``minute``/``details``),
``cards[]`` (``player_tm_id``/``card``/``team``), and ``substitutions[]``
(``player_in_tm_id``/``player_out_tm_id``/``team``/``minute``). Partitioning
those events by ``competition`` yields the per-competition breakdown FOR HBS;
the keeper metrics fall out of the scoreline.

This is HBS-only by construction — a player's stats at *other* clubs are
structurally absent from our corpus (that remains the sole job of a future
TM render scrape).

Verified correctness rules (each backed by a regression test)
-------------------------------------------------------------
* **Own goals.** ``goals[].team`` is the *benefiting* team, so an HBS player's
  own goal is stored under the OPPONENT's name with ``"Own-goal"`` in
  ``details``. Such rows must NOT credit the scorer a goal nor the (rare)
  assist a helper, and they ARE counted toward keeper goals-conceded (the team
  did concede). An opponent's own goal (``team == "Hapoel Beer Sheva"``) is not
  attributed to any HBS player.
* **Goals conceded.** For a keeper who played the whole match the authoritative
  figure is the opponent's score from ``result`` (robust to the occasional
  duplicated ``goals[]`` row — see the Maccabi 2024 1:1 case where the list
  shows two opponent goals but the scoreline says one). Only for keeper-sub
  windows do we fall back to counting minute-bounded ``goals[]`` events.
* **Keeper substitutions.** A keeper is credited keeper metrics only when on
  the pitch *as the keeper*: a starter, or a genuine keeper-for-keeper swap.
  A keeper subbed on for an OUTFIELDER (a mis-recorded sub) is flagged and does
  NOT receive keeper metrics, though it still counts as a sub appearance.
* **Second yellows.** A dismissal-by-second-yellow is logged in ``cards[]`` as
  BOTH a ``"yellow"`` and a ``"second_yellow"``; TM's ``yellow_cards`` total
  counts only the first, so ``yellow_cards = count(yellow) - count(second_yellow)``.
* **League merge.** TM splits the Israeli top flight into a regular season and a
  championship round (two ``competition`` strings); per operator decision we
  collapse both into a single league row.

The season totals for the seven club-page fields stay authoritative (we do not
overwrite them); we only ADD the derived keeper / own-goal / subs / PPG keys to
each ``stats.jsonl`` row and emit a separate ``competition_stats.jsonl`` for the
per-competition table. A tiered integrity gate reconciles the match-derived
per-competition sums against the club-page season totals before anything is
written (see :func:`reconcile`).
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from pathlib import Path

from data_pipeline.helpers import hbs_perspective

logger = logging.getLogger(__name__)

HBS_TEAM = "Hapoel Beer Sheva"

# League regular-season + championship/relegation rounds collapse to one row.
# TM names them e.g. "ליגת העל בכדורגל" and "ליגת העל - שלב האליפות"; matching on
# the shared prefix also folds a future relegation-round string in cleanly.
LEAGUE_PREFIX = "ליגת העל"
LEAGUE_LABEL = "ליגת העל"

# Per-competition fields that reconcile against the club-page season totals.
# fail-loud: a mismatch means a logic/data-corruption bug (verified 197/197).
RECONCILE_EXACT = ("goals", "red_cards", "second_yellow_cards")
# warn-and-report: small drift is expected (TM definition quirks + the
# unrecoverable missing-lineup matches).
RECONCILE_WARN = ("appearances", "assists", "yellow_cards")
RECONCILE_WARN_TOLERANCE = 1
# Minutes are clock-derived (90', 120' for AET, sub-window-adjusted) and can't
# match the club-page season total exactly — TM counts stoppage time and we
# can't model red-card early exits. Reported (not fail-loud) when the per-comp
# sum drifts past this from the club-page season minutes. Validated: median
# drift 1', and the large outliers are the known missing-lineup matches.
RECONCILE_MINUTES_TOLERANCE = 45

# Match-derived season-total keys added to each stats.jsonl row. Keeper keys
# are left None for outfielders so Cargo stores NULL (renders "-").
KEEPER_KEYS = ("clean_sheets", "goals_conceded")
# Match-derived season-total keys augment adds to each stats.jsonl row. PPG is
# NOT here — it's scraped from the club page (the source of truth).
EXTRA_KEYS = ("subs_on", "subs_off", "own_goals")

# Per-competition row schema. minutes_played is clock-DERIVED here (the club
# page only has season totals, and TM's per-competition render is unreliable to
# scrape) — it follows the same match-corpus basis as the other per-comp fields,
# so it excludes the unrecoverable missing-lineup matches just like appearances.
COMPETITION_FIELDS = (
    "appearances", "goals", "assists",
    "yellow_cards", "second_yellow_cards", "red_cards",
    "own_goals", "clean_sheets", "goals_conceded", "minutes_played",
)

# Minutes a player gets for a full match: 90', or 120' when extra time is played.
FULL_TIME = 90
EXTRA_TIME = 120

_START_POS = (0, 0)              # kickoff — a starting keeper's window start
_END_POS = (10_000, 0)           # sentinel "final whistle" for an un-subbed keeper


# --------------------------------------------------------------------------- #
# Pure helpers (unit-tested in isolation)
# --------------------------------------------------------------------------- #
def iter_lineup_ids(lineup) -> set[str]:
    """Return the set of ``tm_player_id`` strings in a lineup.

    Handles both shapes the scraper emits — a flat ``list`` of player dicts and
    a position-keyed ``dict`` (``goalkeeper``/``defenders``/.../``manager``)
    whose values are player dicts or lists of them. ``manager`` is skipped (a
    coach is not an appearance). ``None`` (a missing lineup) yields an empty set.
    """
    ids: set[str] = set()
    if isinstance(lineup, list):
        for entry in lineup:
            if isinstance(entry, dict) and entry.get("tm_player_id"):
                ids.add(str(entry["tm_player_id"]))
    elif isinstance(lineup, dict):
        for key, value in lineup.items():
            if key == "manager":
                continue
            members = value if isinstance(value, list) else [value]
            for entry in members:
                if isinstance(entry, dict) and entry.get("tm_player_id"):
                    ids.add(str(entry["tm_player_id"]))
    return ids


def is_own_goal(goal: dict) -> bool:
    """True iff this ``goals[]`` row is an own goal (``"Own-goal"`` in details)."""
    return "own-goal" in str(goal.get("details") or "").lower()


def normalize_competition(competition: str | None) -> str:
    """Collapse the two league strings into one label; pass everything else
    through unchanged. Empty/None becomes ``""`` (the caller skips it)."""
    comp = (competition or "").strip()
    if comp.startswith(LEAGUE_PREFIX):
        return LEAGUE_LABEL
    return comp


def hbs_side(venue: str | None) -> str | None:
    """Map ``venue`` to the ``"home"``/``"away"`` token used by ``cards[]`` and
    ``substitutions[]`` for HBS's side, or ``None`` for unusable venues."""
    v = (venue or "").strip().upper()
    if v == "H":
        return "home"
    if v == "A":
        return "away"
    return None


def event_pos(event: dict) -> tuple[int, int]:
    """Chronological position of a goal/substitution as ``(minute, extra_time)``.

    Compared as a TUPLE — NOT summed — so stoppage time orders correctly without
    crossing half boundaries: a 45'+5 goal ``(45, 5)`` precedes a 46' halftime
    keeper change ``(46, 0)`` (naive ``45+5=50`` would wrongly place it after),
    while a 90'+3 goal ``(90, 3)`` follows a 90' substitution ``(90, 0)``. Used
    to bound keeper-substitution conceded windows."""
    minute = event.get("minute") if isinstance(event.get("minute"), int) else 0
    extra = event.get("extra_time") if isinstance(event.get("extra_time"), int) else 0
    return (minute, extra)


def match_goals(match: dict, perspective: tuple[int, int] | None = None) -> tuple[int, int]:
    """Return ``(hbs_goals, opp_goals)`` for the 90'+ET match.

    Prefers the authoritative scoreline (``result``), which is robust to the
    occasional duplicated ``goals[]`` row. Falls back to counting ``goals[]``
    when the result can't be parsed — chiefly penalty-shootout cup ties whose
    ``result`` is the *shootout* score ("7:6 (penalties)"), NOT the match score.
    ``goals[]`` holds only real match goals (shootout penalties are never listed
    there), so the count is the true 90'+ET score. ``team`` is the benefiting
    side, so this attributes own goals correctly (an HBS own goal → opp, an
    opponent own goal → HBS)."""
    if perspective is None:
        perspective = hbs_perspective(match.get("result"), match.get("venue"))
    if perspective is not None:
        return perspective
    goals = match.get("goals", [])
    hbs_g = sum(1 for g in goals if g.get("team") == HBS_TEAM)
    opp_g = sum(1 for g in goals if g.get("team") != HBS_TEAM)
    return hbs_g, opp_g


# --------------------------------------------------------------------------- #
# Core computation
# --------------------------------------------------------------------------- #
def _new_cell() -> dict:
    return {f: 0 for f in ("appearances", "goals", "assists",
                           "yellow_raw", "second_yellow_cards", "red_cards",
                           "own_goals", "clean_sheets", "goals_conceded",
                           "minutes_played")}


def player_match_minutes(played: bool, started: bool, on_minute: int | None,
                         off_minute: int | None, full_time: int) -> int:
    """Minutes a single player gets for one match (clock model).

    ``full_time`` is 90, or 120 when the match went to extra time. A starter who
    isn't subbed off plays the full time; a sub-on plays from their entry minute
    to full time; a player subbed off plays until their exit minute. Sub minutes
    include stoppage time, clamped to ``full_time``. Does NOT model red-card
    early exits (a rare over-count, surfaced by the minutes reconcile). Returns 0
    if the player didn't play."""
    if not played:
        return 0
    start = min(on_minute, full_time) if (on_minute is not None and not started) else 0
    end = min(off_minute, full_time) if off_minute is not None else full_time
    return max(0, end - start)


def compute_stats(matches_by_season: dict[str, list], keeper_ids: set[str],
                  roster: set[str] | None = None) -> dict:
    """Compute per-(player, season, competition) cells plus per-(player, season)
    extras from the match corpus.

    Args:
        matches_by_season: ``{season: [match, ...]}`` (Hebrew-enriched matches).
        keeper_ids: ``tm_player_id`` strings whose ``main_position`` is keeper.
        roster: ``tm_player_id`` strings of players we hold records for (the
            ``players.he.jsonl`` ids ∪ ``stats.jsonl`` ids). Attribution is gated
            to this set so a match participant with no player record/page (a
            mis-attributed or filtered id) never spawns a phantom Cargo row that
            the reconcile (which only iterates club-page season totals) can't
            catch. ``None`` disables gating (used by focused unit tests).

    Returns a dict with:
        ``comp``: ``{(player_id, season, competition): cell}``
        ``season_extra``: ``{(player_id, season): {subs_on, subs_off, ppg, ...}}``
        ``report``: diagnostics (missing-lineup matches, anomalies, reconcile notes).
    """
    def allowed(pid: str | None) -> bool:
        return bool(pid) and (roster is None or pid in roster)
    comp: dict[tuple[str, str, str], dict] = defaultdict(_new_cell)
    # season-scoped scratch for subs (not competition-split). PPG is NOT computed
    # here — it's scraped from the SSR club page (the source of truth, immune to
    # our missing-lineup matches); see PlayerSeasonStats.ppg.
    subs_on: dict[tuple[str, str], int] = defaultdict(int)
    subs_off: dict[tuple[str, str], int] = defaultdict(int)

    report = {
        "missing_lineup": [],     # matches with no HBS lineup (starters lost)
        "false_keeper_subs": [],  # keeper subbed on for an outfielder
        "scoreline_goals_mismatch": [],  # goals[] count != scoreline (e.g. dup rows)
        "unusable_venue": [],
        "matches_processed": 0,
    }

    for season in sorted(matches_by_season):
        for idx, match in enumerate(matches_by_season[season]):
            side = hbs_side(match.get("venue"))
            if side is None:
                # No venue signal → can't map result/lineups/cards to HBS's side.
                report["unusable_venue"].append(
                    {"season": season, "idx": idx, "opponent": match.get("opponent"),
                     "venue": match.get("venue"), "result": match.get("result")}
                )
                continue
            report["matches_processed"] += 1
            # Scoreline is authoritative when parseable; penalty-shootout ties
            # ("X:Y (penalties)") fall back to the goals[] count for the real
            # 90'+ET score. The match is ALWAYS processed (its appearances /
            # goals / cards are real) — only the scoreline may be unusable.
            perspective = hbs_perspective(match.get("result"), match.get("venue"))
            scoreline_clean = perspective is not None
            _hbs_goals, opp_goals = match_goals(match, perspective)
            competition = normalize_competition(match.get("competition"))
            if not competition:
                competition = "—"

            hbs_lineup = (match.get("home_lineup") if side == "home"
                          else match.get("away_lineup"))
            starter_ids = iter_lineup_ids(hbs_lineup)
            if not starter_ids:
                report["missing_lineup"].append(
                    {"season": season, "idx": idx, "opponent": match.get("opponent"),
                     "competition": match.get("competition"), "result": match.get("result")}
                )

            subs = [s for s in match.get("substitutions", []) if s.get("team") == side]
            subbed_on = {str(s["player_in_tm_id"]) for s in subs if s.get("player_in_tm_id")}
            subbed_off = {str(s["player_out_tm_id"]) for s in subs if s.get("player_out_tm_id")}
            on_min = {str(s["player_in_tm_id"]): event_pos(s)[0] + event_pos(s)[1]
                      for s in subs if s.get("player_in_tm_id")}
            off_min = {str(s["player_out_tm_id"]): event_pos(s)[0] + event_pos(s)[1]
                       for s in subs if s.get("player_out_tm_id")}
            full_time = EXTRA_TIME if match.get("aet") else FULL_TIME

            played_ids = {pid for pid in (starter_ids | subbed_on) if allowed(pid)}
            for pid in played_ids:
                cell = comp[(pid, season, competition)]
                cell["appearances"] += 1
                cell["minutes_played"] += player_match_minutes(
                    played=True, started=pid in starter_ids,
                    on_minute=on_min.get(pid), off_minute=off_min.get(pid),
                    full_time=full_time,
                )
            for pid in subbed_on:
                if allowed(pid):
                    subs_on[(pid, season)] += 1
            for pid in subbed_off:
                if allowed(pid):
                    subs_off[(pid, season)] += 1

            # Goals + assists + own goals (roster-gated).
            for goal in match.get("goals", []):
                scorer = str(goal["scorer_tm_id"]) if goal.get("scorer_tm_id") else None
                assister = str(goal["assist_tm_id"]) if goal.get("assist_tm_id") else None
                if is_own_goal(goal):
                    # Own goal BY an HBS player → benefits opponent (team != HBS).
                    if allowed(scorer) and goal.get("team") != HBS_TEAM:
                        comp[(scorer, season, competition)]["own_goals"] += 1
                    # No goal/assist credit for any own goal.
                    continue
                if goal.get("team") == HBS_TEAM:
                    if allowed(scorer):
                        comp[(scorer, season, competition)]["goals"] += 1
                    if allowed(assister):
                        comp[(assister, season, competition)]["assists"] += 1

            # Cards (HBS side only, roster-gated).
            for card in match.get("cards", []):
                pid = str(card["player_tm_id"]) if card.get("player_tm_id") else None
                if card.get("team") != side or not allowed(pid):
                    continue
                kind = card.get("card")
                if kind == "yellow":
                    comp[(pid, season, competition)]["yellow_raw"] += 1
                elif kind == "second_yellow":
                    comp[(pid, season, competition)]["second_yellow_cards"] += 1
                elif kind == "red":
                    comp[(pid, season, competition)]["red_cards"] += 1

            # Keeper clean sheets / goals conceded.
            _attribute_keeper(
                comp, season, competition, opp_goals, scoreline_clean, match, side,
                starter_ids, keeper_ids, report, idx,
            )

    # Finalise yellow (subtract second yellows) and assemble season extras.
    for cell in comp.values():
        cell["yellow_cards"] = max(0, cell.pop("yellow_raw") - cell["second_yellow_cards"])

    season_extra: dict[tuple[str, str], dict] = {}
    for key in set(subs_on) | set(subs_off):
        season_extra[key] = {
            "subs_on": subs_on.get(key, 0),
            "subs_off": subs_off.get(key, 0),
        }

    return {"comp": dict(comp), "season_extra": season_extra, "report": report}


def _attribute_keeper(comp, season, competition, opp_goals, scoreline_clean, match, side,
                      starter_ids, keeper_ids, report, idx):
    """Attribute clean sheets / goals conceded to the keeper(s) on the pitch.

    A keeper qualifies for keeper metrics only as a starter or via a genuine
    keeper-for-keeper swap; a keeper subbed on for an outfielder is flagged and
    skipped (still counted as a sub appearance upstream). Full-match keepers use
    the authoritative scoreline; partial windows count minute-bounded events.
    """
    starting_keepers = starter_ids & keeper_ids
    # Conceded events: every goal credited to a team OTHER than HBS (this
    # includes HBS players' own goals — the team conceded them — and excludes
    # opponents' own goals, which are credited to HBS). Positions are
    # (minute, extra_time) tuples so stoppage goals order correctly.
    conceded_events = sorted(
        event_pos(g)
        for g in match.get("goals", [])
        if g.get("team") != HBS_TEAM
    )

    # Per-keeper substitution-off positions (stoppage-time aware tuples).
    sub_off_pos = {str(s["player_out_tm_id"]): event_pos(s)
                   for s in match.get("substitutions", [])
                   if s.get("team") == side and s.get("player_out_tm_id")}

    windows: list[tuple[str, tuple, tuple]] = []  # (keeper_id, start_pos, end_pos)
    for kid in starting_keepers:
        windows.append((kid, _START_POS, sub_off_pos.get(kid, _END_POS)))

    # Keeper substitutions: only genuine keeper-for-keeper swaps create a window.
    for s in match.get("substitutions", []):
        if s.get("team") != side:
            continue
        pin = str(s["player_in_tm_id"]) if s.get("player_in_tm_id") else None
        pout = str(s["player_out_tm_id"]) if s.get("player_out_tm_id") else None
        if pin and pin in keeper_ids:
            if pout and pout in keeper_ids:
                windows.append((pin, event_pos(s), _END_POS))
            else:
                # Mis-recorded: a keeper "on" for a non-keeper. Not a keeper change.
                report["false_keeper_subs"].append(
                    {"season": season, "idx": idx, "keeper_in": pin,
                     "player_out": pout, "opponent": match.get("opponent")}
                )

    # Reconcile note: when the scoreline is authoritative, the per-event goal
    # count should match it; a mismatch flags a duplicated/mis-attributed
    # goals[] row (e.g. the Maccabi 2024 1:1 case).
    if scoreline_clean and len(conceded_events) != opp_goals:
        report["scoreline_goals_mismatch"].append(
            {"season": season, "idx": idx, "opponent": match.get("opponent"),
             "result": match.get("result"), "goals_list": len(conceded_events),
             "scoreline_opp": opp_goals}
        )

    for kid, start, end in windows:
        full_match = (start == _START_POS and end == _END_POS)
        if full_match:
            conceded = opp_goals  # scoreline is authoritative
        else:
            conceded = sum(1 for pos in conceded_events if start <= pos < end)
        finished = (end == _END_POS)
        cell = comp[(kid, season, competition)]
        cell["goals_conceded"] += conceded
        if conceded == 0 and finished:
            cell["clean_sheets"] += 1


# --------------------------------------------------------------------------- #
# Integrity gate
# --------------------------------------------------------------------------- #
class ReconcileError(RuntimeError):
    """Raised when a fail-loud field (goals/red/second-yellow) fails to
    reconcile against the club-page season totals — a data-integrity stop."""


def reconcile(computed: dict, season_totals: dict[tuple[str, str], dict],
              seasons_covered: set[str] | None = None) -> dict:
    """Reconcile match-derived per-competition sums against club-page season
    totals. ``RECONCILE_EXACT`` fields must match exactly (else raise);
    ``RECONCILE_WARN`` fields are reported when they drift past tolerance.

    ``season_totals`` is ``{(player_id, season): stats_row}`` from stats.jsonl.
    ``seasons_covered`` restricts the check to seasons we actually computed from
    match data — a club-stats season with NO match reports (so derived=0) would
    otherwise register exact breaks and fail-loud the whole run; we skip it
    instead. ``None`` checks every row (used by focused unit tests).
    Returns a summary; raises :class:`ReconcileError` on any exact-field break.
    """
    comp = computed["comp"]
    # Sum competitions back to a per-(player, season) total.
    derived: dict[tuple[str, str], dict] = defaultdict(_new_cell)
    for (pid, season, _competition), cell in comp.items():
        agg = derived[(pid, season)]
        for f in ("appearances", "goals", "assists",
                  "second_yellow_cards", "red_cards", "yellow_cards",
                  "own_goals", "clean_sheets", "goals_conceded", "minutes_played"):
            agg[f] = agg.get(f, 0) + cell.get(f, 0)

    exact_breaks: list[dict] = []
    warn_drift: list[dict] = []
    minutes_drift: list[dict] = []
    checked = 0
    for key, truth in season_totals.items():
        if seasons_covered is not None and key[1] not in seasons_covered:
            continue
        checked += 1
        agg = derived.get(key, {})
        for f in RECONCILE_EXACT:
            d = agg.get(f, 0) - int(truth.get(f) or 0)
            if d != 0:
                exact_breaks.append({"player_season": key, "field": f,
                                     "derived": agg.get(f, 0), "club": truth.get(f), "delta": d})
        for f in RECONCILE_WARN:
            d = agg.get(f, 0) - int(truth.get(f) or 0)
            if abs(d) > RECONCILE_WARN_TOLERANCE:
                warn_drift.append({"player_season": key, "field": f,
                                   "derived": agg.get(f, 0), "club": truth.get(f), "delta": d})
        if truth.get("minutes_played") is not None:
            d = agg.get("minutes_played", 0) - int(truth["minutes_played"])
            if abs(d) > RECONCILE_MINUTES_TOLERANCE:
                minutes_drift.append({"player_season": key, "derived": agg.get("minutes_played", 0),
                                      "club": truth.get("minutes_played"), "delta": d})

    summary = {
        "exact_breaks": exact_breaks,
        "warn_drift": warn_drift,
        "minutes_drift": minutes_drift,
        "season_rows_checked": checked,
    }
    if exact_breaks:
        sample = exact_breaks[:8]
        raise ReconcileError(
            f"{len(exact_breaks)} exact-field reconciliation break(s) against club-page "
            f"season totals (goals/red/second-yellow must match). Sample: {sample}"
        )
    return summary


def self_check(computed: dict) -> list[str]:
    """Internal consistency checks for net-new keeper / own-goal fields that
    have no club-page oracle. Returns a list of warning strings (non-fatal)."""
    warnings: list[str] = []
    per_keeper: dict[tuple[str, str], dict] = defaultdict(lambda: {"cs": 0, "conceded": 0, "apps": 0})
    for (pid, season, _c), cell in computed["comp"].items():
        agg = per_keeper[(pid, season)]
        agg["cs"] += cell.get("clean_sheets", 0)
        agg["conceded"] += cell.get("goals_conceded", 0)
        agg["apps"] += cell.get("appearances", 0)
    for key, agg in per_keeper.items():
        if agg["cs"] > agg["apps"]:
            warnings.append(f"{key}: clean_sheets {agg['cs']} > appearances {agg['apps']}")
        if agg["conceded"] < 0:
            warnings.append(f"{key}: negative goals_conceded {agg['conceded']}")
    return warnings


# --------------------------------------------------------------------------- #
# I/O + orchestration
# --------------------------------------------------------------------------- #
def _load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def load_keeper_ids(players_he_path: Path) -> set[str]:
    """tm_player_ids whose ``main_position`` marks them a goalkeeper."""
    keepers: set[str] = set()
    for player in _load_jsonl(players_he_path):
        pos = str(player.get("main_position") or "")
        if "שוער" in pos or "Goalkeeper" in pos:
            pid = player.get("id") or player.get("tm_player_id")
            if pid:
                keepers.add(str(pid))
    return keepers


def load_roster(players_he_path: Path) -> set[str]:
    """All HBS player ids we hold a record (and thus a page) for — the gate that
    keeps a match participant with no player record from spawning a phantom
    Cargo row. All ``stats.jsonl`` ids are a subset of these, so gating never
    drops a reconcile target."""
    roster: set[str] = set()
    for player in _load_jsonl(players_he_path):
        pid = player.get("id") or player.get("tm_player_id")
        if pid:
            roster.add(str(pid))
    return roster


def load_matches(seasons: list[str], scraper_output_dir: Path) -> dict[str, list]:
    """Load ``matches.he.json`` for each season (skipping seasons with none)."""
    out: dict[str, list] = {}
    for season in seasons:
        path = scraper_output_dir / season / "matches.he.json"
        if path.exists():
            out[season] = json.loads(path.read_text(encoding="utf-8"))
        else:
            logger.warning("compute_competition_stats: no matches.he.json for season %s", season)
    return out


def build_competition_rows(computed: dict, keeper_ids: set[str]) -> list[dict]:
    """Materialise ``competition_stats.jsonl`` rows from the computed cells.

    Keeper-only fields (clean_sheets / goals_conceded) are ``None`` for
    outfielders so Cargo stores NULL. Rows with no activity are dropped."""
    rows = []
    for (pid, season, competition), cell in sorted(computed["comp"].items()):
        is_keeper = pid in keeper_ids
        row = {"player_id": pid, "season": season, "competition": competition}
        for f in ("appearances", "goals", "assists",
                  "yellow_cards", "second_yellow_cards", "red_cards",
                  "own_goals", "minutes_played"):
            row[f] = cell.get(f, 0)
        row["clean_sheets"] = cell.get("clean_sheets", 0) if is_keeper else None
        row["goals_conceded"] = cell.get("goals_conceded", 0) if is_keeper else None
        if any(row[f] for f in ("appearances", "goals", "assists",
                                "yellow_cards", "second_yellow_cards", "red_cards",
                                "own_goals", "minutes_played")) \
                or row["clean_sheets"] or row["goals_conceded"]:
            rows.append(row)
    return rows


def augment_season_totals(stats_rows: list[dict], computed: dict, keeper_ids: set[str]) -> list[dict]:
    """Add match-derived keeper / own-goal / subs keys onto each existing
    stats.jsonl row IN PLACE. The club-page fields (the seven scraped stats AND
    the scraped ``ppg``) are left untouched — PPG is authoritative from the club
    page, not recomputed here.

    Keeper keys are ``None`` for outfielders → Cargo NULL → renders "-".
    """
    # Aggregate keeper + own_goals back to season totals from the cells.
    derived: dict[tuple[str, str], dict] = defaultdict(lambda: {"clean_sheets": 0, "goals_conceded": 0, "own_goals": 0})
    for (pid, season, _c), cell in computed["comp"].items():
        agg = derived[(pid, season)]
        agg["clean_sheets"] += cell.get("clean_sheets", 0)
        agg["goals_conceded"] += cell.get("goals_conceded", 0)
        agg["own_goals"] += cell.get("own_goals", 0)

    extra = computed["season_extra"]
    for row in stats_rows:
        key = (str(row.get("player_id")), str(row.get("season")))
        is_keeper = key[0] in keeper_ids
        agg = derived.get(key, {})
        ex = extra.get(key, {})
        row["clean_sheets"] = agg.get("clean_sheets", 0) if is_keeper else None
        row["goals_conceded"] = agg.get("goals_conceded", 0) if is_keeper else None
        row["own_goals"] = agg.get("own_goals", 0)
        row["subs_on"] = ex.get("subs_on", 0)
        row["subs_off"] = ex.get("subs_off", 0)
        # row["ppg"] intentionally NOT set — it comes from the club-page scrape.
    return stats_rows


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def main(data_dir: Path, seasons: list[str], scraper_output_dir: Path) -> dict:
    """Pipeline entry point. Reads ``players.he.jsonl`` + season ``matches.he.json``,
    computes per-competition + keeper stats, runs the integrity gate, then writes
    ``competition_stats.jsonl`` and augments ``stats.jsonl`` IN PLACE.

    Returns a summary dict (counts + diagnostics). Raises :class:`ReconcileError`
    if a fail-loud field fails to reconcile (data-integrity stop).
    """
    data_dir = Path(data_dir)
    scraper_output_dir = Path(scraper_output_dir)

    keeper_ids = load_keeper_ids(data_dir / "players.he.jsonl")
    roster = load_roster(data_dir / "players.he.jsonl")
    matches_by_season = load_matches(seasons, scraper_output_dir)
    stats_rows = _load_jsonl(data_dir / "stats.jsonl")

    # No match corpus → nothing to derive. Bail BEFORE touching stats.jsonl so we
    # never overwrite previously-augmented rows with zeros, and never false-fail
    # the reconcile against club totals we have no match data for.
    if not matches_by_season:
        logger.warning("compute_competition_stats: no matches.he.json for any of %s — "
                       "skipping keeper/per-competition derivation (stats.jsonl untouched).", seasons)
        return {"skipped": "no match data", "seasons_requested": seasons,
                "competition_rows": 0, "season_rows_augmented": 0}

    computed = compute_stats(matches_by_season, keeper_ids, roster)
    season_totals = {(str(r["player_id"]), str(r["season"])): r for r in stats_rows}
    seasons_covered = set(matches_by_season)

    recon = reconcile(computed, season_totals, seasons_covered)   # may raise — fail loud
    sc_warn = self_check(computed)

    competition_rows = build_competition_rows(computed, keeper_ids)
    augment_season_totals(stats_rows, computed, keeper_ids)

    _write_jsonl(data_dir / "competition_stats.jsonl", competition_rows)
    _write_jsonl(data_dir / "stats.jsonl", stats_rows)

    report = computed["report"]
    summary = {
        "keepers": len(keeper_ids),
        "roster": len(roster),
        "seasons": sorted(matches_by_season),
        "matches_processed": report["matches_processed"],
        "competition_rows": len(competition_rows),
        "season_rows_augmented": len(stats_rows),
        "season_rows_reconciled": recon["season_rows_checked"],
        "missing_lineup_matches": len(report["missing_lineup"]),
        "false_keeper_subs": len(report["false_keeper_subs"]),
        "unusable_venue_matches": len(report["unusable_venue"]),
        "scoreline_goals_mismatch": len(report["scoreline_goals_mismatch"]),
        "reconcile_warn_drift": len(recon["warn_drift"]),
        "reconcile_minutes_drift": len(recon["minutes_drift"]),
        "self_check_warnings": sc_warn,
    }

    # Data-loss vigilance: surface counts loudly, never silently zero.
    logger.info("compute_competition_stats: %s", summary)
    if recon["minutes_drift"]:
        logger.warning("compute_competition_stats: %d player-season(s) where derived per-competition "
                       "minutes drift >%d' from the club-page season minutes (clock model can't model "
                       "stoppage/red-cards; large drifts are the missing-lineup matches): %s",
                       len(recon["minutes_drift"]), RECONCILE_MINUTES_TOLERANCE, recon["minutes_drift"][:8])
    if report["unusable_venue"]:
        logger.warning("compute_competition_stats: %d match(es) DROPPED for unusable venue "
                       "(no H/A signal — can't map to HBS's side): %s",
                       len(report["unusable_venue"]), report["unusable_venue"])
    if report["missing_lineup"]:
        logger.warning("compute_competition_stats: %d match(es) with NO HBS lineup — "
                       "starters' appearances are unrecoverable and were skipped (not zeroed): %s",
                       len(report["missing_lineup"]), report["missing_lineup"])
    if report["false_keeper_subs"]:
        logger.warning("compute_competition_stats: %d mis-recorded keeper sub(s) skipped for "
                       "keeper metrics: %s", len(report["false_keeper_subs"]), report["false_keeper_subs"])
    for w in sc_warn:
        logger.warning("compute_competition_stats self-check: %s", w)

    return summary
