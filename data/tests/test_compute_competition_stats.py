"""Regression tests for data_pipeline.compute_competition_stats.

Each test pins one of the verified correctness rules from the keeper /
per-competition derivation (see the module docstring + docs/research/0004-*).
Synthetic matches model the exact edge cases found in the real corpus:
penalty-shootout ties, own goals (inverted team attribution), second-yellow
double-logging, mis-recorded keeper subs, dict-shaped lineups, missing lineups,
and the league regular/championship split.
"""

from __future__ import annotations

import pytest

from data_pipeline import compute_competition_stats as ccs

KEEPER = "K1"
KEEPER2 = "K2"
LEAGUE_REG = "ליגת העל בכדורגל"
LEAGUE_CHAMP = "ליגת העל - שלב האליפות"
CUP = "גביע המדינה בכדורגל"
HBS = ccs.HBS_TEAM


def _player(pid, **extra):
    return {"tm_player_id": pid, **extra}


def _match(*, venue="H", result="1:0", competition=LEAGUE_REG, home=None, away=None,
           goals=None, cards=None, subs=None, opponent="Opp"):
    return {
        "venue": venue, "result": result, "competition": competition,
        "opponent": opponent,
        "home_lineup": home if home is not None else [],
        "away_lineup": away if away is not None else [],
        "goals": goals or [], "cards": cards or [], "substitutions": subs or [],
    }


# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #
def test_iter_lineup_ids_list_shape():
    lu = [_player("a"), _player("b"), {"name": "no id"}]
    assert ccs.iter_lineup_ids(lu) == {"a", "b"}


def test_iter_lineup_ids_dict_shape_skips_manager():
    lu = {
        "goalkeeper": [_player("gk")],
        "defenders": [_player("d1"), _player("d2")],
        "manager": {"tm_player_id": "coach"},  # must be skipped
    }
    assert ccs.iter_lineup_ids(lu) == {"gk", "d1", "d2"}


def test_iter_lineup_ids_none_is_empty():
    assert ccs.iter_lineup_ids(None) == set()


def test_normalize_competition_merges_league():
    assert ccs.normalize_competition(LEAGUE_REG) == ccs.LEAGUE_LABEL
    assert ccs.normalize_competition(LEAGUE_CHAMP) == ccs.LEAGUE_LABEL
    assert ccs.normalize_competition(CUP) == CUP


def test_is_own_goal():
    assert ccs.is_own_goal({"details": ", Own-goal"})
    assert not ccs.is_own_goal({"details": ", Right-footed shot"})
    assert not ccs.is_own_goal({"details": None})


def test_match_goals_prefers_scoreline_over_duplicated_list():
    # The Maccabi 2024 1:1 case: goals[] lists two opponent goals but the
    # scoreline says one. The scoreline wins.
    m = _match(venue="A", result="1:1", goals=[
        {"team": HBS, "scorer_tm_id": "x", "minute": 12},
        {"team": "Opp", "scorer_tm_id": KEEPER, "minute": 33, "details": ", Own-goal"},
        {"team": "Opp", "scorer_tm_id": "o", "minute": 33},
    ])
    # venue=A → result home:away = Opp:HBS = 1:1 → (hbs, opp) = (1, 1)
    assert ccs.match_goals(m) == (1, 1)


def test_match_goals_falls_back_to_list_for_penalties():
    # "7:6 (penalties)" is unparseable → count goals[] (the real 90'+ET score).
    m = _match(venue="H", result="7:6 (penalties)", goals=[
        {"team": HBS, "scorer_tm_id": "x", "minute": 68},
        {"team": "Opp", "scorer_tm_id": "o", "minute": 98},
    ])
    assert ccs.match_goals(m) == (1, 1)


# --------------------------------------------------------------------------- #
# Goals / assists / own goals
# --------------------------------------------------------------------------- #
def test_goal_and_assist_credit():
    m = _match(home=[_player("s"), _player("a")], goals=[
        {"team": HBS, "scorer_tm_id": "s", "assist_tm_id": "a", "minute": 10},
    ])
    out = ccs.compute_stats({"2024": [m]}, set())
    assert out["comp"][("s", "2024", ccs.LEAGUE_LABEL)]["goals"] == 1
    assert out["comp"][("a", "2024", ccs.LEAGUE_LABEL)]["assists"] == 1


def test_own_goal_by_hbs_player_not_credited_as_goal_and_counts_own_goals():
    # HBS player own goal → stored under opponent team, "Own-goal" in details.
    m = _match(venue="H", result="0:1", home=[_player("d")], goals=[
        {"team": "Opp", "scorer_tm_id": "d", "minute": 50, "details": ", Own-goal"},
    ])
    out = ccs.compute_stats({"2024": [m]}, set())
    cell = out["comp"][("d", "2024", ccs.LEAGUE_LABEL)]
    assert cell["goals"] == 0          # own goal is NOT a goal
    assert cell["own_goals"] == 1


def test_own_goal_assist_not_credited():
    # An own-goal row carrying an assist must not credit the assister (the
    # assister's cell still exists because they were in the lineup).
    m = _match(venue="H", result="0:1", home=[_player("d"), _player("a")], goals=[
        {"team": "Opp", "scorer_tm_id": "d", "assist_tm_id": "a", "minute": 50,
         "details": ", Own-goal"},
    ])
    out = ccs.compute_stats({"2024": [m]}, set())
    assert out["comp"][("a", "2024", ccs.LEAGUE_LABEL)]["assists"] == 0


# --------------------------------------------------------------------------- #
# Cards (second-yellow subtraction)
# --------------------------------------------------------------------------- #
def test_second_yellow_subtracted_from_yellows():
    # Real data logs a second-yellow dismissal as [yellow, second_yellow]; TM's
    # yellow_cards column nets the dismissal's first yellow out (verified
    # 196/197 reconcile of yellow_raw - second_yellow). So a standalone yellow
    # in match 1 plus a dismissal in match 2 → yellow_cards=1, second_yellow=1.
    booking = _match(home=[_player("p")], cards=[
        {"team": "home", "player_tm_id": "p", "card": "yellow", "minute": 30},
    ])
    dismissal = _match(home=[_player("p")], cards=[
        {"team": "home", "player_tm_id": "p", "card": "yellow", "minute": 20},
        {"team": "home", "player_tm_id": "p", "card": "second_yellow", "minute": 80},
    ])
    out = ccs.compute_stats({"2024": [booking, dismissal]}, set())
    cell = out["comp"][("p", "2024", ccs.LEAGUE_LABEL)]
    assert cell["yellow_cards"] == 1
    assert cell["second_yellow_cards"] == 1


def test_lone_second_yellow_dismissal_nets_yellow_to_zero():
    # A player whose only booking sequence in a competition is a 2nd-yellow
    # dismissal contributes 0 to yellow_cards (TM reclassifies it).
    m = _match(home=[_player("p")], cards=[
        {"team": "home", "player_tm_id": "p", "card": "yellow", "minute": 20},
        {"team": "home", "player_tm_id": "p", "card": "second_yellow", "minute": 80},
    ])
    out = ccs.compute_stats({"2024": [m]}, set())
    cell = out["comp"][("p", "2024", ccs.LEAGUE_LABEL)]
    assert cell["yellow_cards"] == 0
    assert cell["second_yellow_cards"] == 1


def test_cards_only_counted_for_hbs_side():
    m = _match(venue="H", home=[_player("p")], cards=[
        {"team": "away", "player_tm_id": "opp", "card": "yellow", "minute": 20},
    ])
    out = ccs.compute_stats({"2024": [m]}, set())
    assert ("opp", "2024", ccs.LEAGUE_LABEL) not in out["comp"]


# --------------------------------------------------------------------------- #
# Keeper clean sheets / goals conceded
# --------------------------------------------------------------------------- #
def test_keeper_full_match_conceded_from_scoreline():
    m = _match(venue="H", result="3:1", home=[_player(KEEPER)], goals=[
        {"team": "Opp", "scorer_tm_id": "o", "minute": 70},
    ])
    out = ccs.compute_stats({"2024": [m]}, {KEEPER})
    cell = out["comp"][(KEEPER, "2024", ccs.LEAGUE_LABEL)]
    assert cell["goals_conceded"] == 1
    assert cell["clean_sheets"] == 0


def test_keeper_clean_sheet():
    m = _match(venue="H", result="2:0", home=[_player(KEEPER)])
    out = ccs.compute_stats({"2024": [m]}, {KEEPER})
    cell = out["comp"][(KEEPER, "2024", ccs.LEAGUE_LABEL)]
    assert cell["goals_conceded"] == 0
    assert cell["clean_sheets"] == 1


def test_keeper_conceded_uses_scoreline_not_duplicated_list():
    # Scoreline 1 conceded even though goals[] shows two opp rows (one a dup).
    m = _match(venue="A", result="1:1", away=[_player(KEEPER)], goals=[
        {"team": HBS, "scorer_tm_id": "x", "minute": 12},
        {"team": "Opp", "scorer_tm_id": KEEPER, "minute": 33, "details": ", Own-goal"},
        {"team": "Opp", "scorer_tm_id": "o", "minute": 33},
    ])
    out = ccs.compute_stats({"2024": [m]}, {KEEPER})
    cell = out["comp"][(KEEPER, "2024", ccs.LEAGUE_LABEL)]
    assert cell["goals_conceded"] == 1
    assert cell["clean_sheets"] == 0
    assert out["report"]["scoreline_goals_mismatch"]  # the dup is flagged


def test_genuine_keeper_swap_windows_conceded():
    # K1 starts, conceded a goal at min 30, then K2 comes on at 60; the goal at
    # min 75 belongs to K2's window.
    m = _match(venue="H", result="0:2", home=[_player(KEEPER)],
               goals=[
                   {"team": "Opp", "scorer_tm_id": "o1", "minute": 30},
                   {"team": "Opp", "scorer_tm_id": "o2", "minute": 75},
               ],
               subs=[{"team": "home", "player_in_tm_id": KEEPER2,
                      "player_out_tm_id": KEEPER, "minute": 60}])
    out = ccs.compute_stats({"2024": [m]}, {KEEPER, KEEPER2})
    k1 = out["comp"][(KEEPER, "2024", ccs.LEAGUE_LABEL)]
    k2 = out["comp"][(KEEPER2, "2024", ccs.LEAGUE_LABEL)]
    assert k1["goals_conceded"] == 1   # min 30 only
    assert k1["clean_sheets"] == 0     # subbed off → not a clean sheet
    assert k2["goals_conceded"] == 1   # min 75 only
    assert k2["clean_sheets"] == 0


def test_false_keeper_sub_for_outfielder_flagged_and_no_keeper_metrics():
    # K2 (a keeper) comes on for an OUTFIELDER — a mis-recorded sub. K2 must NOT
    # be credited keeper metrics, but still counts as a sub appearance. The
    # starting keeper K1 keeps the full-match figures.
    m = _match(venue="H", result="5:1", home=[_player(KEEPER)],
               goals=[{"team": "Opp", "scorer_tm_id": "o", "minute": 20}],
               subs=[{"team": "home", "player_in_tm_id": KEEPER2,
                      "player_out_tm_id": "outfielder", "minute": 72}])
    out = ccs.compute_stats({"2024": [m]}, {KEEPER, KEEPER2})
    assert out["report"]["false_keeper_subs"]
    # K2 not credited keeper metrics
    k2 = out["comp"][(KEEPER2, "2024", ccs.LEAGUE_LABEL)]
    assert k2["goals_conceded"] == 0
    assert k2["clean_sheets"] == 0
    assert k2["appearances"] == 1      # but did make a sub appearance
    # K1 owns the full-match conceded
    k1 = out["comp"][(KEEPER, "2024", ccs.LEAGUE_LABEL)]
    assert k1["goals_conceded"] == 1


# --------------------------------------------------------------------------- #
# Penalty matches, missing lineups, league merge
# --------------------------------------------------------------------------- #
def test_penalty_match_is_processed_not_skipped():
    m = _match(venue="H", result="7:6 (penalties)", competition=CUP,
               home=[_player("s"), _player(KEEPER)],
               goals=[
                   {"team": HBS, "scorer_tm_id": "s", "minute": 68},
                   {"team": "Opp", "scorer_tm_id": "o", "minute": 98},
               ])
    out = ccs.compute_stats({"2019": [m]}, {KEEPER})
    assert out["report"]["unusable_venue"] == []
    assert out["report"]["matches_processed"] == 1
    assert out["comp"][("s", "2019", CUP)]["goals"] == 1
    assert out["comp"][(KEEPER, "2019", CUP)]["goals_conceded"] == 1


def test_missing_lineup_reported_and_subbed_on_still_counts():
    # No HBS lineup, but a player came on as sub → they appear; starters lost.
    m = _match(venue="H", result="6:0", home=None,
               subs=[{"team": "home", "player_in_tm_id": "subbed_on", "minute": 60}])
    out = ccs.compute_stats({"2024": [m]}, set())
    assert len(out["report"]["missing_lineup"]) == 1
    assert out["comp"][("subbed_on", "2024", ccs.LEAGUE_LABEL)]["appearances"] == 1


def test_unusable_venue_skipped_and_reported():
    m = _match(venue="N", result="1:0")
    out = ccs.compute_stats({"2024": [m]}, set())
    assert len(out["report"]["unusable_venue"]) == 1
    assert out["report"]["matches_processed"] == 0


def test_league_regular_and_championship_merge_into_one_row():
    m1 = _match(venue="H", result="1:0", competition=LEAGUE_REG,
                home=[_player("p")], goals=[{"team": HBS, "scorer_tm_id": "p", "minute": 5}])
    m2 = _match(venue="H", result="2:0", competition=LEAGUE_CHAMP,
                home=[_player("p")], goals=[{"team": HBS, "scorer_tm_id": "p", "minute": 9}])
    out = ccs.compute_stats({"2024": [m1, m2]}, set())
    cell = out["comp"][("p", "2024", ccs.LEAGUE_LABEL)]
    assert cell["appearances"] == 2
    assert cell["goals"] == 2
    # no separate championship-round row
    assert ("p", "2024", LEAGUE_CHAMP) not in out["comp"]


def test_subs_on_off_and_ppg_season_extras():
    win = _match(venue="H", result="2:0", home=[_player("starter")],
                 subs=[{"team": "home", "player_in_tm_id": "benchie",
                        "player_out_tm_id": "starter", "minute": 80}])
    draw = _match(venue="A", result="1:1", away=[_player("starter"), _player("benchie")])
    out = ccs.compute_stats({"2024": [win, draw]}, set())
    # benchie: 1 sub-on; starter: 1 sub-off; ppg over their appearances
    assert out["season_extra"][("benchie", "2024")]["subs_on"] == 1
    assert out["season_extra"][("starter", "2024")]["subs_off"] == 1
    # starter played both (win=3, draw=1) over 2 apps → 2.0
    assert out["season_extra"][("starter", "2024")]["ppg"] == 2.0


# --------------------------------------------------------------------------- #
# Integrity gate
# --------------------------------------------------------------------------- #
def test_reconcile_raises_on_exact_field_break():
    computed = {"comp": {("p", "2024", ccs.LEAGUE_LABEL): dict(ccs._new_cell(), goals=1)}}
    # club says 3 goals; derived 1 → exact break on goals → raise
    totals = {("p", "2024"): {"goals": 3, "red_cards": 0, "second_yellow_cards": 0,
                              "appearances": 0, "assists": 0, "yellow_cards": 0}}
    with pytest.raises(ccs.ReconcileError):
        ccs.reconcile(computed, totals)


def test_reconcile_tolerates_appearance_drift_within_one():
    cell = dict(ccs._new_cell(), goals=2, appearances=9)
    computed = {"comp": {("p", "2024", ccs.LEAGUE_LABEL): cell}}
    totals = {("p", "2024"): {"goals": 2, "red_cards": 0, "second_yellow_cards": 0,
                              "appearances": 10, "assists": 0, "yellow_cards": 0}}
    summary = ccs.reconcile(computed, totals)   # within tolerance → no raise
    assert summary["warn_drift"] == []


def test_reconcile_warns_on_large_appearance_drift():
    cell = dict(ccs._new_cell(), goals=2, appearances=7)
    computed = {"comp": {("p", "2024", ccs.LEAGUE_LABEL): cell}}
    totals = {("p", "2024"): {"goals": 2, "red_cards": 0, "second_yellow_cards": 0,
                              "appearances": 10, "assists": 0, "yellow_cards": 0}}
    summary = ccs.reconcile(computed, totals)
    assert len(summary["warn_drift"]) == 1
    assert summary["warn_drift"][0]["field"] == "appearances"


# --------------------------------------------------------------------------- #
# Row materialisation + season-total augmentation
# --------------------------------------------------------------------------- #
def test_build_competition_rows_keeper_vs_outfielder():
    computed = {"comp": {
        (KEEPER, "2024", ccs.LEAGUE_LABEL): dict(ccs._new_cell(), appearances=10, clean_sheets=4, goals_conceded=8),
        ("of", "2024", ccs.LEAGUE_LABEL): dict(ccs._new_cell(), appearances=10, goals=3),
        ("ghost", "2024", CUP): ccs._new_cell(),  # no activity → dropped
    }, "season_extra": {}}
    rows = ccs.build_competition_rows(computed, {KEEPER})
    by_pid = {r["player_id"]: r for r in rows}
    assert "ghost" not in by_pid
    assert by_pid[KEEPER]["clean_sheets"] == 4
    assert by_pid[KEEPER]["goals_conceded"] == 8
    assert by_pid["of"]["clean_sheets"] is None     # outfielder → NULL
    assert by_pid["of"]["goals_conceded"] is None


def test_augment_season_totals_adds_keys_without_touching_club_fields():
    computed = {
        "comp": {(KEEPER, "2024", ccs.LEAGUE_LABEL): dict(ccs._new_cell(), clean_sheets=5, goals_conceded=12, own_goals=0)},
        "season_extra": {(KEEPER, "2024"): {"subs_on": 1, "subs_off": 0, "ppg": 1.5}},
    }
    stats = [{"player_id": KEEPER, "season": "2024", "appearances": 30, "goals": 0}]
    out = ccs.augment_season_totals(stats, computed, {KEEPER})[0]
    assert out["appearances"] == 30 and out["goals"] == 0   # untouched
    assert out["clean_sheets"] == 5 and out["goals_conceded"] == 12
    assert out["subs_on"] == 1 and out["ppg"] == 1.5


def test_augment_outfielder_keeper_keys_are_none():
    computed = {"comp": {}, "season_extra": {("of", "2024"): {"subs_on": 0, "subs_off": 2, "ppg": 1.0}}}
    stats = [{"player_id": "of", "season": "2024", "appearances": 20, "goals": 5}]
    out = ccs.augment_season_totals(stats, computed, {KEEPER})[0]
    assert out["clean_sheets"] is None and out["goals_conceded"] is None
    assert out["subs_off"] == 2


# --------------------------------------------------------------------------- #
# Review fixes: roster gating, PPG penalty, finished-gate, extra_time tuples,
# self_check, reconcile season-filter.
# --------------------------------------------------------------------------- #
def test_roster_gating_drops_non_roster_participants():
    # 'ghost' is in the lineup + scores + is carded, but isn't on our roster →
    # no phantom Cargo cell (no player page to render it on).
    m = _match(home=[_player("known"), _player("ghost")],
               goals=[{"team": HBS, "scorer_tm_id": "ghost", "minute": 10},
                      {"team": HBS, "scorer_tm_id": "known", "minute": 20}],
               cards=[{"team": "home", "player_tm_id": "ghost", "card": "yellow", "minute": 5}])
    out = ccs.compute_stats({"2024": [m]}, set(), roster={"known"})
    assert ("known", "2024", ccs.LEAGUE_LABEL) in out["comp"]
    assert ("ghost", "2024", ccs.LEAGUE_LABEL) not in out["comp"]
    assert out["comp"][("known", "2024", ccs.LEAGUE_LABEL)]["goals"] == 1


def test_penalty_tie_counts_as_draw_for_ppg():
    # goals[] nets 2:0 but the result is a shootout → it was a DRAW at 90'+ET,
    # so PPG must credit 1 point, not 3 (the WIN the raw goal count implies).
    m = _match(venue="H", result="2:0 (penalties)", competition=CUP, home=[_player("p")],
               goals=[{"team": HBS, "scorer_tm_id": "p", "minute": 80},
                      {"team": HBS, "scorer_tm_id": "p", "minute": 95}])
    out = ccs.compute_stats({"2024": [m]}, set())
    assert out["season_extra"][("p", "2024")]["ppg"] == 1.0


def test_keeper_subbed_off_with_zero_conceded_gets_no_clean_sheet():
    # K1 starts, K2 (a keeper) comes on at 46; nobody scores. K1 did NOT finish
    # → no clean sheet; K2 finished a goalless window → clean sheet.
    m = _match(venue="H", result="0:0", home=[_player(KEEPER)],
               subs=[{"team": "home", "player_in_tm_id": KEEPER2,
                      "player_out_tm_id": KEEPER, "minute": 46}])
    out = ccs.compute_stats({"2024": [m]}, {KEEPER, KEEPER2})
    assert out["comp"][(KEEPER, "2024", ccs.LEAGUE_LABEL)]["clean_sheets"] == 0
    assert out["comp"][(KEEPER2, "2024", ccs.LEAGUE_LABEL)]["clean_sheets"] == 1


def test_first_half_stoppage_goal_charged_to_pre_halftime_keeper():
    # A 45'+5 goal precedes a 46' halftime keeper change → first keeper concedes
    # it. Naive minute+extra (45+5=50 > 46) would wrongly charge the substitute;
    # tuple ordering ((45,5) < (46,0)) gets it right.
    m = _match(venue="H", result="0:1", home=[_player(KEEPER)],
               goals=[{"team": "Opp", "scorer_tm_id": "o", "minute": 45, "extra_time": 5}],
               subs=[{"team": "home", "player_in_tm_id": KEEPER2,
                      "player_out_tm_id": KEEPER, "minute": 46}])
    out = ccs.compute_stats({"2024": [m]}, {KEEPER, KEEPER2})
    assert out["comp"][(KEEPER, "2024", ccs.LEAGUE_LABEL)]["goals_conceded"] == 1
    assert out["comp"][(KEEPER2, "2024", ccs.LEAGUE_LABEL)]["goals_conceded"] == 0


def test_late_stoppage_goal_charged_to_substitute_keeper():
    # A 90'+3 goal falls in the replacement keeper's window.
    m = _match(venue="H", result="0:1", home=[_player(KEEPER)],
               goals=[{"team": "Opp", "scorer_tm_id": "o", "minute": 90, "extra_time": 3}],
               subs=[{"team": "home", "player_in_tm_id": KEEPER2,
                      "player_out_tm_id": KEEPER, "minute": 80}])
    out = ccs.compute_stats({"2024": [m]}, {KEEPER, KEEPER2})
    assert out["comp"][(KEEPER, "2024", ccs.LEAGUE_LABEL)]["goals_conceded"] == 0
    assert out["comp"][(KEEPER2, "2024", ccs.LEAGUE_LABEL)]["goals_conceded"] == 1


def test_self_check_flags_clean_sheets_exceeding_appearances():
    computed = {"comp": {(KEEPER, "2024", ccs.LEAGUE_LABEL):
                         dict(ccs._new_cell(), appearances=1, clean_sheets=2)}}
    warnings = ccs.self_check(computed)
    assert any("clean_sheets" in w for w in warnings)


def test_self_check_clean_when_consistent():
    computed = {"comp": {(KEEPER, "2024", ccs.LEAGUE_LABEL):
                         dict(ccs._new_cell(), appearances=5, clean_sheets=3, goals_conceded=4)}}
    assert ccs.self_check(computed) == []


def test_player_match_minutes_full_partial_aet():
    assert ccs.player_match_minutes(True, True, None, None, 90) == 90       # full 90
    assert ccs.player_match_minutes(True, True, None, None, 120) == 120     # full AET
    assert ccs.player_match_minutes(True, True, None, 70, 90) == 70         # subbed off at 70
    assert ccs.player_match_minutes(True, False, 70, None, 90) == 20        # subbed on at 70
    assert ccs.player_match_minutes(True, False, 70, None, 120) == 50       # AET sub on
    assert ccs.player_match_minutes(False, False, None, None, 90) == 0      # didn't play


def test_per_competition_minutes_full_match_is_90():
    m = _match(venue="H", result="1:0", home=[_player("p")])
    out = ccs.compute_stats({"2024": [m]}, set())
    assert out["comp"][("p", "2024", ccs.LEAGUE_LABEL)]["minutes_played"] == 90


def test_per_competition_minutes_aet_is_120():
    m = _match(venue="H", result="2:1", home=[_player("p")])
    m["aet"] = True
    out = ccs.compute_stats({"2024": [m]}, set())
    assert out["comp"][("p", "2024", ccs.LEAGUE_LABEL)]["minutes_played"] == 120


def test_per_competition_minutes_substitution_split():
    m = _match(venue="H", result="1:0", home=[_player("starter")],
               subs=[{"team": "home", "player_in_tm_id": "sub",
                      "player_out_tm_id": "starter", "minute": 60}])
    out = ccs.compute_stats({"2024": [m]}, set())
    assert out["comp"][("starter", "2024", ccs.LEAGUE_LABEL)]["minutes_played"] == 60
    assert out["comp"][("sub", "2024", ccs.LEAGUE_LABEL)]["minutes_played"] == 30


def test_minutes_reconcile_warns_not_fails():
    cell = dict(ccs._new_cell(), goals=0, minutes_played=2700)
    computed = {"comp": {("p", "2024", ccs.LEAGUE_LABEL): cell}}
    totals = {("p", "2024"): {"goals": 0, "red_cards": 0, "second_yellow_cards": 0,
                              "appearances": 0, "assists": 0, "yellow_cards": 0,
                              "minutes_played": 2900}}
    summary = ccs.reconcile(computed, totals)   # 200' drift > tolerance → warn, no raise
    assert len(summary["minutes_drift"]) == 1
    assert summary["exact_breaks"] == []


def test_build_competition_rows_includes_minutes():
    computed = {"comp": {("of", "2024", ccs.LEAGUE_LABEL):
                         dict(ccs._new_cell(), appearances=10, minutes_played=850)},
                "season_extra": {}}
    rows = ccs.build_competition_rows(computed, set())
    assert rows[0]["minutes_played"] == 850


def test_reconcile_skips_seasons_without_match_data():
    # Club stats for 2019 but we only computed 2024 → 2019 must NOT fail-loud
    # (derived=0 vs club=8 would otherwise be a goals break).
    computed = {"comp": {("p", "2024", ccs.LEAGUE_LABEL): dict(ccs._new_cell(), goals=3)}}
    totals = {
        ("p", "2024"): {"goals": 3, "red_cards": 0, "second_yellow_cards": 0,
                        "appearances": 0, "assists": 0, "yellow_cards": 0},
        ("q", "2019"): {"goals": 8, "red_cards": 0, "second_yellow_cards": 0,
                        "appearances": 0, "assists": 0, "yellow_cards": 0},
    }
    summary = ccs.reconcile(computed, totals, seasons_covered={"2024"})
    assert summary["season_rows_checked"] == 1   # only 2024 checked
    assert summary["exact_breaks"] == []
