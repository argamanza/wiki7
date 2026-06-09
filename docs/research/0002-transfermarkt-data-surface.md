# Research 0002 — Transfermarkt data surface for Hapoel Beer Sheva

- **Status:** PR A — all 8 open questions resolved 2026-06-09; the binding decisions live in §7.
- **Date:** 2026-06-09
- **Phase:** 3a R2 (pipeline finalization, multi-season, all-time).
- **Companion:** [`docs/revival-plan.md`](../revival-plan.md) §4 Phase 3 / Phase 3a R2 sub-phase.

## 1. Why this document exists

Phase 3a's original scope was one-season-end-to-end (2024/25). That run shipped via PRs #50/#54/#55, landed 102 drafts on prod, and the drafts were wiped in the subsequent clean-slate teardown (PR #56 close-out + the destroy/redeploy that followed). The bot, spiders, and pipeline code still stand; what needs re-doing is the content push, this time at all-time scope.

Before we re-run the bot against every season Transfermarkt has data for, we need a comprehensive picture of:

1. What TM actually publishes per entity (player, match, transfer, coach, season, stadium, club records, honours) — across all eras, not just 2024.
2. What the current pipeline covers vs. doesn't (so PR B knows the gap to close).
3. Which gaps are worth closing now vs. deferring to Phase 3b (content re-curation) or later.
4. Where TM's coverage actually starts (the empirical season floor) and where field sparseness kicks in (older seasons have less data).
5. The few decisions that PR B's implementation depends on (season-identifier convention, cross-season aggregate strategy, translation strategy at 70× scale, reviewer-queue scaling).

This is PR A. Open questions get resolved here. PR B implements the resulting plan.

## 2. Empirical season-availability floor

Probed via the `.us` mirror on 2026-06-09 (the `.com` host blocks the WebFetch tool but `.us` resolves the same database). Findings:

| Season | Squad table | Per-player stats | Fixtures + match-report links | Market values per player |
|---|---|---|---|---|
| 1965/66 | ❌ no data | ❌ | ❌ | ❌ |
| 1970/71 | ❌ no data | ❌ | ❌ | ❌ |
| 1972/73 | ❌ no data ("No information") | ❌ | ❌ | ❌ |
| 1974/75 | (not probed — assume present given 1975/76 has it) | ? | ? | ❌ |
| 1975/76 | ✅ 12 rows | ? | ? | ❌ |
| 1976/77 | ✅ 18 rows | ? | ? | ❌ |
| 1980/81 | ❌ "No information" (arrivals/departures + coach exist) | ❌ | ? | ❌ |
| 1985/86 | ✅ 25 rows | ✅ real values | ✅ 34 matches with report links | ❌ (—) |
| 1986/87 | (assumed yes; `platzierungen` page starts here) | ✅ | ✅ | ❌ |
| 1990/91 | ✅ 25 rows | ? | ? | ❌ (—) |
| 2000/01 | ✅ 25 rows | ✅ | ✅ with report links | ✅ aggregate present, per-player needs probing |
| 2015/16 | ✅ | ✅ | ✅ | ✅ |
| 2024/25 | ✅ | ✅ | ✅ | ✅ |

**Practical floors (PR B must reconfirm exactly during the run by walking back season-by-season):**

- **Squad data:** roughly **1974/75–1976/77** with gaps (e.g. 1980/81 has no squad). Pre-1974: no useful data. Some intermediate seasons in the late-1970s / early-1980s have arrivals/departures/coach but no squad.
- **Per-player stats (`leistungsdaten`):** at least **1985/86** with real values. Earlier seasons likely 0 or empty; PR B probes 1980-1985 to find the floor exactly.
- **Fixtures + match-report links:** at least **1985/86**. Older may exist for some seasons.
- **Match reports (lineups + goals + subs + attendance + referee):** verified populated for **1985/86** (Sat Sep 14 1985, vs Kiryat Eliezer, 3,000 attendance, ref Zvi Sharir). Older reports (if linked) likely sparser.
- **Per-player market values:** start ~**2003-2005**. Older seasons show `—` per row.
- **League positions (`platzierungen`):** earliest shown is **1986/87** (11th in Liga Leumit with 36 points).
- **Bilanz / head-to-head season filter:** dropdown goes back to **1976/77**.
- **Coach (`mitarbeiter`):** **current staff only** as of 2026-06-09 (Ran Kozuch, Manager since 2024-07-01; Assistant Manager Ben Binyamin; etc.). No historical-coach list URL is exposed (verified — `/trainer/verein/`, `/trainerhistorie/verein/` are both 404 on TM). Per-season summary pages show that-season's manager(s).
- **Honours:** the `erfolge/verein/2976` page lists every title. Verified contents (2026-06-09): 6 league titles (1974/75, 1975/76, 2015/16, 2016/17, 2017/18, 2025/26), 4 cups (1996/97, 2019/20, 2021/22, 2024/25), 5 Super Cups (1975/76, 2016/17, 2017/18, 2022/23, 2025/26), 1 Second Tier (2000/01), Europa League (2016/17, 2017/18, 2020/21), Conference League (2022/23), promoted to 1st league (2000/01, 2008/09).

**PR B scope for "all-time": seasons 1949/50 → current**, season-by-season, with placeholder discipline (decided 2026-06-09):

- The bot **always emits a season-overview page** for every season from the club's founding (1949/50) to current, ~77 total. This gives the wiki a complete chronological index — `עונת 1965/66` exists as a page even when TM has zero data for that year, rendered from the same template with a "no data available — TM did not cover this season" note + a hand-curate prompt.
- **Per-entity pages** (squad, transfers, competition, match reports) emit only when TM has data for that entity. The season-overview page lists which sub-pages exist (linked) and which are absent (with a one-line "TM coverage starts in 1985/86 for stats" footnote).
- This approach has two practical benefits: (a) reviewers can browse the entire season index in `Special:AllPages?namespace=3000` knowing nothing is silently missing; (b) hand-curated content for pre-TM-coverage seasons (1949-1974) gets a real wiki home — the bot writes the skeleton, future hand-curation fills it in.
- The empirical TM coverage floor becomes a *finding* recorded in the prod-push report ("squad data starts at season X, per-player stats at Y, match reports at Z"), not a config knob set in advance.

Pre-1974 seasons end up as thin pages by design — appropriate for hand-curated "Did You Know" / fan-culture / oral-history work in Phase 3b. The wiki structure supports that work from day one.

## 3. Per-entity walkthrough — what TM publishes vs. what we cover

Each row classifies fields as **TM publishes** (✅), **TM doesn't publish for this club** (❌), or **needs verification** (?). The pipeline column uses ✅ (covered today), ⚠️ (partial / known gap), ❌ (not scraped), or **skip** (deliberately excluded). The recommendation column flags PR B work, Phase 3b, or later/never.

### 3.1 Player

| Field | TM publishes? | In pipeline? | Recommendation | Notes |
|---|---|---|---|---|
| Name (English) | ✅ | ✅ | — | `players.json` `name_english`. |
| Name in home country (often Hebrew) | ✅ for Israeli players | ✅ | — | `facts['Name in home country']` → `name_hebrew` when all-Hebrew. |
| Native-script name (non-Hebrew foreign players, e.g. Cyrillic) | ✅ partial | ⚠️ stored as-is; not transliterated | **Phase 3b** | Currently dropped onto LLM translation; PR B documents but doesn't fix. |
| Date of birth | ✅ | ✅ | — | `facts['Date of birth/Age']`. |
| Place of birth | ✅ | ✅ | — | `facts['Place of birth']`. |
| Citizenship (multi-nationality) | ✅ | ✅ list | — | `facts['Citizenship']` → `parse_countries()`. |
| Main position | ✅ | ✅ | — | `positions.main`. |
| Other positions | ✅ | ✅ list (collected, currently unused) | **PR B: emit on infobox** | Already scraped; just thread through Jinja. |
| Preferred foot | ✅ | ❌ | **PR B implement** | `facts['Foot']`; trivial. |
| Height | ✅ | ❌ | **PR B implement** | `facts['Height']`; trivial. |
| Shirt number (current season) | ✅ | ✅ | — | Squad spider. |
| Current market value | ✅ for post-2003 | ⚠️ derived (latest of `market_value_history`) | **PR B: expose explicitly** | Already in the data, just not surfaced as its own field on infobox. |
| Market value history | ✅ for post-2003 | ✅ via `/ceapi/marketValueDevelopment/graph/` | — | Already in `market_values.jsonl`. |
| Contract expiry | ✅ for current players | ❌ | **PR B implement** | `facts['Contract expires']`. Empty for retired / former players. |
| Agent | ✅ for active players | ❌ | **skip** | Privacy-flavoured; doesn't add wiki value. |
| Date joined / left HBS | ✅ derivable | ✅ via transfer history | — | Already in `transfers.jsonl`. |
| Transfer history (career-wide) | ✅ via `/ceapi/transferHistory/list/` | ✅ | — | `transfers.jsonl` includes from/to/date/fee. |
| Career stats per season (per club) | ✅ via player's `/leistungsdaten/spieler/<id>` | ⚠️ HBS-only | **Phase 3b** | Current pipeline only stores stats *for HBS seasons*. Full career stats would let player pages show "before HBS / at HBS / after HBS" tables. Costs ~1 extra ScraperAPI request per player; defer. |
| Per-competition splits (league vs cup vs Europe) | ✅ via the same `/leistungsdaten` page detailed view | ❌ aggregated only | **Phase 3b** | TM exposes splits via the `plus/0` view ranges; cost is parsing complexity, not extra requests. Defer; aggregate stats are sufficient for v1. |
| National team appearances | ✅ on player profile | ❌ | **Phase 3b** | TM lists international caps on the player profile. Skip for v1 — not core to a club wiki. |
| Injury history | ✅ via `/verletzungen/spieler/<id>` | ❌ | **skip** | Niche; rotting data. |
| Suspension history | ✅ via `/sperren/spieler/<id>` | ❌ | **skip** | Niche; cards data is already in stats. |
| Image (player photo) | ✅ | ❌ skip (copyrighted) | **skip** | Per hybrid workspace policy: reviewer/operator uploads CC-licensed photos manually. |
| Captain status (current) | ✅ on squad page (current season) | ❌ | **PR B implement** | Squad spider misses the captain icon; easy CSS-selector add. |
| Retired flag | ✅ derivable from "Career ended" facts entry | ✅ | — | `is_retired()` helper. |
| Homegrown flag | ✅ via flag icon on squad page | ✅ | — | `is_homegrown()` helper. |

### 3.2 Match

| Field | TM publishes? | In pipeline? | Recommendation | Notes |
|---|---|---|---|---|
| Date | ✅ | ✅ | — | Fixtures spider. |
| Kickoff time | ✅ | ✅ | — | Fixtures spider. |
| Competition | ✅ | ✅ | — | Fixtures spider header row. |
| Season | ✅ derivable from spider arg | ✅ (post-PR #50 fix) | — | Phase 3a closed this gap. |
| Matchday / round | ✅ | ✅ | — | Fixtures col. 0. |
| Venue (H/A or neutral) | ✅ | ✅ | — | Fixtures col. 3. Cup finals on neutral grounds need a "Neutral" handling — verify. |
| Attendance | ✅ post-1985 | ✅ | — | Fixtures col. 8 + match report. |
| Weather | ❌ TM doesn't publish | n/a | — | Skip. |
| Result (final score) | ✅ | ✅ | — | Fixtures col. 9. |
| Halftime score | ✅ in match report | ❌ | **PR B implement** | Match report parses goals but doesn't separately store HT. Cheap extraction. |
| AET indicator (extra time) | ✅ in match report | ⚠️ derived from "(penalties)" suffix | **PR B implement** | Match spider already detects penalty shootouts; should also detect AET. |
| Penalty shootout | ✅ | ✅ | — | Match spider `extract_penalties()`. |
| Scorers with assists + minutes | ✅ | ✅ | — | Match spider `extract_goals()`. |
| Lineups (starting XI + bench) | ✅ post-1985 | ✅ post-PR #50 (graphic + table fallback) | — | But: bench players aren't separately tracked from starters in the current extractor — confirm. |
| Substitutions | ✅ | ✅ | — | Match spider `extract_substitutions()`. |
| Cards (yellow, 2nd-yellow, red) | ✅ | ✅ | — | Match spider `extract_cards()`. |
| Formation (e.g., "4-2-3-1") | ✅ | ⚠️ on fixture row as "system_of_play" | — | Cosmetic; already passed through. |
| Manager (both teams) | ✅ in match report lineups box | ✅ via table fallback `manager` row | — | Verified working. |
| Referee + assistants | ✅ in match report | ❌ | **PR B implement** | Big polish item; ~5 LOC CSS selector. |
| Stadium (per match) | ✅ in match report | ❌ | **PR B implement** | Useful for away matches; ties to "stadium history" linking. |
| Match events timeline (unified) | ⚠️ TM splits goals/cards/subs into separate boxes | ⚠️ pipeline emits per-event-type lists | — | Current Jinja template renders three separate tables. Optionally unify on render-time. **Defer (Phase 3b — cosmetic).** |
| Post-match TM rating per player | ✅ for recent matches; sparse pre-2010 | ❌ | **skip** | Subjective rating; not core wiki data. |
| Manager sanctions (touchline cards) | ✅ rarely | ✅ | — | Match spider `extract_manager_sanctions()`. |

### 3.3 Transfer (per-player career transfer, plus club-level season transfers)

| Field | TM publishes? | In pipeline? | Recommendation | Notes |
|---|---|---|---|---|
| Date | ✅ | ✅ | — | Player spider via `/ceapi/transferHistory/`. |
| Direction | ✅ derivable | ✅ via `from_club/to_club` | — | |
| Player | ✅ | ✅ | — | |
| Fee | ✅ | ✅ | — | |
| Loan flag | ✅ | ✅ | — | |
| Loan-with-option / swap-deal | ⚠️ TM sometimes notes in the fee column | ❌ | **Phase 3b** | Rare; parse if it appears. |
| Contract length | ✅ for some entries | ❌ | **Phase 3b** | Niche. |
| Source/destination club ID | ✅ | ⚠️ stored as plain text only | **PR B implement** | Lets us cross-link to TM IDs / future club pages. |
| Youth promotion (graduated from academy) | ✅ derivable from a "Youth" club entry | ⚠️ derivable from `is_homegrown` | — | Existing helper covers this. |
| End-of-loan return | ✅ as a "loan" with end-date | ⚠️ stored but not flagged distinctly | **Phase 3b** | Cosmetic; transfer table renders fine without it. |

**Club-level transfers page** (`alletransfers/verein/2976`) was fixed in PR #50 and works. The TM page no longer surfaces `age` + `position` inline — already documented in the Phase 3b backlog.

### 3.4 Coach

| Field | TM publishes? | In pipeline? | Recommendation | Notes |
|---|---|---|---|---|
| Current head coach | ✅ via `/mitarbeiter/` | ✅ | — | Ran Kozuch since 2024-07-01. |
| Current assistant + fitness coach + youth director + team manager | ✅ via `/mitarbeiter/` | ✅ (all rows captured) | — | All 6 entries verified 2026-06-09. |
| Historical-coach list | ❌ TM no longer exposes (`/trainer/verein/` + `/trainerhistorie/verein/` → 404) | n/a | **defer to hand-curated** | The per-season page summary box shows that-season's coach(es) with W/D/L; PR B can extract that signal across all seasons. |
| Per-season manager + W/D/L | ✅ via club-season summary page | ❌ | **PR B implement** | Walk each season's `startseite/verein/2976/saison_id/<yr>` page; extract the "Manager" box; deduplicate. This *constructs* a historical-coach list from per-season pages. |
| Coach's playing career | ✅ on coach profile | ❌ | **skip** | Off-club data. |
| Coach's tactical preference (formation) | ✅ on coach profile (recent) | ❌ | **skip** | Subjective + flaky. |
| Trophies won (with HBS) | ✅ derivable from honours + tenure dates | ❌ | **PR B implement** | ~10 LOC join: honours.json (which trophies + when) × per-season manager extraction (who was manager in season Y) → per-coach trophy list. Big payoff for coach pages ("won 2 league titles + 1 cup as HBS manager"). |

### 3.5 Season (per-season summary pages)

| Field | TM publishes? | In pipeline? | Recommendation | Notes |
|---|---|---|---|---|
| Final league position | ✅ via `platzierungen` (since 1986/87) | ❌ | **PR B implement** | New spider. Critical: lets the season-overview page state "finished 1st in Ligat ha'Al" instead of just listing fixtures. |
| Points + W/D/L | ✅ via `platzierungen` | ❌ | **PR B implement** | Same new spider. |
| Goals for / against | ✅ via `platzierungen` | ❌ | **PR B implement** | Same. |
| Cup runs (every competition entered + how far reached) | ✅ derivable from fixtures | ⚠️ shown as fixture lists per competition | **PR B implement** | Compute "Reached: Quarterfinals" / "Won: Final" from competition fixtures. |
| Top scorer | ✅ derivable from stats | ✅ via Python aggregation | — | Season-overview Jinja already does it. |
| Top assister / most apps | ✅ derivable | ✅ | — | Same. |
| Manager(s) for the season | ✅ via club-season summary page | ❌ | **PR B implement** | See coach row. |
| Notable transfers in/out | ✅ derivable | ✅ via squad/transfer pages | — | Already in scope. |
| Season narrative | ❌ TM doesn't publish prose | n/a | **defer to hand-curated** | Phase 3b. |
| Mid-season league restructuring (1990s) | ⚠️ TM shows what happened post-hoc | ⚠️ partial | **PR B graceful** | Mid-1990s saw split seasons. Templates must render correctly when standard fields are missing. |

### 3.6 Stadium / Venue

| Field | TM publishes? | In pipeline? | Recommendation | Notes |
|---|---|---|---|---|
| Current stadium name | ✅ | ✅ | — | "Toto Jacob Turner Stadium". |
| Capacity / seats | ✅ | ✅ | — | 16,126. |
| Surface | ✅ | ✅ | — | Natural grass. |
| Year built | ✅ | ✅ | — | 2015. |
| Address | ✅ | ✅ | — | Etsel St. 6, Beer Sheva. |
| City | ✅ derivable | ✅ | — | |
| Year HBS moved in/out | ❌ TM doesn't track ground-moves explicitly | n/a (known from history: Vasermil → Turner 2015) | **PR B: hand-curate the move-date** | Add to stadium page as a `{{Stadium infobox}}` field. |
| Previous stadium (Vasermil) | ⚠️ TM dropdown shows "Vasermil Stadium" but no separate page | ❌ | **PR B: hand-curate Vasermil page** | Static stub page is fine — name, location, years used, demolished/in-use status. |
| Notable matches there | ❌ TM doesn't track | n/a | **defer to Phase 3b** | Hand-curated. |
| Other amenities (heating, running track) | ✅ | ❌ | **PR B implement** | Trivial; "Undersoil heating: Not available", "Running track: No". |

### 3.7 Club records

| Field | TM publishes? | In pipeline? | Recommendation | Notes |
|---|---|---|---|---|
| Record arrivals (top fees in) | ✅ via `transferrekorde/verein/2976` | ✅ | — | Existing records spider. |
| Record departures (top fees out) | ✅ same page, different tab | ⚠️ only one tab scraped | **PR B implement** | Scrape both tabs. |
| All-time top scorer (HBS-career-only) | ✅ via `torschuetzenkoenig/verein/2976` — but page returned 404 in probe | ⚠️ only "since 1985/86 stats" via leaderboards aggregation | **PR B verify** | If the dedicated page is gone, leaderboards from stats.jsonl is the next best thing. Note: stats start 1985/86, so "all-time top scorer" is "top scorer since 1985/86" — acknowledge this limit in the page footer. |
| Most appearances | ✅ derivable from stats | ✅ via leaderboards | — | Same caveat as above. |
| Longest winning streak / biggest win / loss | ⚠️ TM doesn't surface explicitly | ❌ | **Phase 3b** | Computable from fixtures.json. |
| Head-to-head vs major opponents | ✅ via `bilanz/verein/2976` (W/D/L + GF/GA + avg attendance + season filter) | ❌ | **PR B implement** | New spider. **High-value** — enables a real "Derbies" page comparing HBS vs Maccabi Tel Aviv (121 matches verified), Hapoel Tel Aviv, Beitar Jerusalem, Maccabi Haifa (119 matches verified). |
| European campaign history | ⚠️ `europapokalspiele/verein/2976` → 404; derivable from fixtures by competition name | ⚠️ derivable | **PR B implement** | New aggregator on top of fixtures across seasons. List every European fixture, group by season + round. |

### 3.8 Honours

| Field | TM publishes? | In pipeline? | Recommendation | Notes |
|---|---|---|---|---|
| Championships (title + year) | ✅ via `erfolge/verein/2976` | ✅ | — | Existing honours spider. Empirically verified 2026-06-09: 6 league, 4 cup, 5 super-cup, 1 second-tier. |
| European honours | ✅ partial — only "participant" status, no "winner" rows | ✅ partial | — | TM lists Europa League participant for 16/17, 17/18, 20/21 + Conference League 22/23. Not honours per se; render as a "European campaign history" section instead. |
| Individual awards won by HBS players (Player of the Year, etc.) | ⚠️ Wikipedia has this; TM doesn't | ❌ | **defer to Phase 3b** | Hand-curated. |

## 4. Edge cases per entity (non-exhaustive)

Verified during PR A probing where possible; the rest flagged for the PR B test fixtures to cover.

### 4.1 Player edge cases

- **Diacritics in foreign names** (e.g. Argentinian / Brazilian players from the 1990s) — verify the LLM auto-translate handles them; manual override pattern preserved.
- **Multi-nationality** — `parse_countries()` already returns a list; verify rendering on infobox is right-to-left friendly.
- **Retired / deceased players** — current `is_retired()` helper covers retirement; deceased is a TM-side gap (no field for it). Hand-curate.
- **Mid-season transfers** — a player who joined HBS in winter and left in summer ends up in two seasons' squad lists (the squad spider for both 2024 and 2025 returns the same TM ID). The current merge logic correctly dedupes by TM ID; verify the player page lists both seasons in the stats table.
- **Youth-team graduations** — `is_homegrown()` reads the TM "youth team" badge; check this hasn't broken on the modern TM HTML.
- **Loan recalls** — appear as two rows in transfer history (loan-out, then loan-recall). Already deduped because rows are stored individually with dates.
- **Name changes** — TM updates the name in place; we get the latest spelling. Acceptable.
- **Multi-spell players** (left in 2008, returned in 2014) — TM still has one profile, two transfer rows. Current pipeline handles; verify rendering on the player infobox.

### 4.2 Match edge cases

- **Postponements** — TM shows the rescheduled date as the canonical fixture; old fixture row may be removed. No special handling needed.
- **Walkovers** — historically rare; verify if any post-1985 HBS match had one and how TM renders the score.
- **Abandoned matches** (crowd trouble) — TM may show "Match abandoned" in lieu of a result; pipeline currently parses result as a string, so "Abandoned" passes through. Template should render this gracefully.
- **Cup replays** — historically the State Cup had replays for ties. TM shows them as separate fixtures; current pipeline treats each as a distinct match. Acceptable.
- **AET + penalty shootouts** — already detected; verify halftime + AET score parsing in PR B.
- **Friendlies** — TM lists some friendlies under "Other matches"; current `fixtures_spider` only walks the main competitions. Verify no leakage; if needed, add a friendly filter.
- **Neutral-venue cup finals** — verify the fixture's `venue` field; "Neutral" or specific stadium name may both appear.
- **Derbies** — flag for category cross-link in the match Jinja template (already partially there via `[[קטגוריה:משחקים]]`).
- **Pre-1985 fixture coverage** — verify whether 1980-1984 has fixtures even if no squad table (the `spielplan` page may exist).

### 4.3 Transfer edge cases

- **Free agents** — fee column shows "Free transfer" or "-"; current `loan` detection key is the literal "loan" in fee text. Verify free transfers parse cleanly.
- **End-of-loan returns** — appear as a transfer row with `from_club=<loaning club>, to_club=Hapoel Beer Sheva, fee=End of loan`. Current pipeline treats as a normal transfer.
- **Swap deals** — rare; TM annotates in the fee column. Pass-through OK.
- **Contract terminations** — show as "Termination" in fee. Pass-through OK.
- **Transfer-deadline-day chaos** — multiple rows with same date; existing dedup keys cover.

### 4.4 Coach edge cases

- **Interim spells** — TM shows "(Caretaker)" tag; verify the spider preserves it.
- **Caretaker between permanent appointments** — same handling.
- **Multi-club spells** (left, returned) — current `coach` spider only catches the current row, so this is N/A. The per-season manager extraction (recommended PR B work) handles by listing each tenure as a separate row.
- **Player-coach roles** (1960s/70s Israeli football) — out of scope (pre-1974 floor).

### 4.5 Season edge cases

- **Incomplete seasons** (mid-1990s Israeli league restructuring) — verify the `platzierungen` page handles these; PR B template needs a graceful fallback when "final position" is null.
- **Seasons where HBS was in the second tier** (e.g. 1999/2000 leading to 2000/01 promotion, plus 2007/08 leading to 2008/09 promotion) — render as "Liga Leumit (Tier 2)" with the correct competition link.
- **Cup-only participations** — when HBS was in lower tiers but still entered the State Cup, verify both league + cup competitions render on the season page.

## 5. Open architectural decisions PR B inherits

PR A picks; PR B implements.

### 5.1 Season-identifier convention

**Currently inconsistent:**
- Spider CLI: `season="2024"` (integer start-year)
- Data files: `season: "2024"` (same)
- Jinja `season_display`: `"2024/25"` (slash, 2-digit suffix)
- Page titles: mixed — `סגל 2024` (squad) + `העברות 2024` (transfers) use bare year; `עונת 2024/25` (overview) + `Premier League 2024/25` (competition) use slash format.

**Decision:** normalize all *human-readable surfaces* (page titles, internal cross-links, h1 headings, category names) to the **slash format** `YYYY/YY` (e.g. `2024/25`). Keep the *internal* `season` field on data files as the bare integer start-year (`"2024"`) — this is the join key.

**Hebrew calendar option (`תשפ"ה`):** considered — it's the format used by some Israeli sports websites — but ruled out for v1. Reason: most TM-sourced data carries the slash format, MediaWiki search is happier with hyphen/slash titles than Hebrew calendar quotes, and the slash format is unambiguous when sorting.

**Concretely PR B normalizes:**
- `import_squad_page` title from `סגל 2024` → `סגל 2024/25`.
- `import_transfer_page` title from `העברות 2024` → `העברות 2024/25`.
- `season_overview` continues with `עונת 2024/25` (no change).
- `competition_season` continues with `<comp> 2024/25` (no change).
- Internal cross-links in templates (e.g., `transfer_table.j2`'s `[[עונת {{ season_display }}|...]]`) already use slash — no change.

**Impact:** existing pages on prod under old names get "abandoned" — but prod was wiped in the clean-slate teardown, so this is a green-field rename. No migration needed.

### 5.2 Cross-season aggregate pages — Cargo queries or pipeline-written?

**Options:**
- **Cargo queries**: `{{#cargo_query:tables=player_stats|...}}` against per-season Cargo data emitted by templates.
- **Pipeline-written**: Python aggregates stats.jsonl across seasons, writes flat wikitext tables.

**Recommendation: keep pipeline-written for v1, ALSO emit Cargo rows for ad-hoc queries.**

Why both:
- Pipeline-written gets you correct rendering on day one. Stats aggregate cleanly in Python; the leaderboards/season-overview templates already do this.
- Cargo rows let wiki editors write ad-hoc queries later ("show me every player who scored > 5 goals in a single season vs Maccabi Haifa") without re-running the pipeline.
- The two don't conflict — Cargo rows are emitted via `{{#cargo_store:}}` on each per-season template invocation; the pipeline-written aggregate pages don't read Cargo (they read jsonl directly).

The work split: PR B keeps existing Python aggregation (no rework), adds `{{#cargo_store}}` calls inside the per-page templates (player infobox already declares the Cargo table; just verify all data files participate).

### 5.3 Translation strategy at 70-season scale

`mappings.he.yaml` for 2024/25 is 18 KB. At 70× scale that's ~1.3 MB of YAML covering thousands of names — many overlap (a player on the 2010/11 squad is the same TM ID as the 2011/12 squad). The bottleneck is unique-foreign-name translation, not file size.

**Current shape:** flat `name_en: name_he` map (auto-populated by `auto_translate_hebrew.py` using Google Translate web endpoint; humans review and overwrite).

**Recommended shape change for PR B:**

```yaml
"Lior Refaelov":
  he: "ליאור רפאלוב"
  src: manual       # manual | auto-llm | auto-google
  confidence: high  # high | low (flagged for review)
  note: ""          # optional human comment

"Anthony Nwakaeme":
  he: "אנתוני נוואקיימה"
  src: auto-llm
  confidence: low   # foreign name; manual review recommended
  note: ""
```

**Pipeline change:**
- Auto-translate switches from Google Translate to Claude API. Claude is materially better at Hebrew transliteration of foreign names (Eastern European, African, South American — the historical HBS roster has all three). Cost: ~$0.50 for an all-time first pass with prompt caching; negligible after.
- Output the new fields. Confidence is `high` for known patterns (Israeli names matching the all-Hebrew detector), `low` for everything else. Existing entries with `src: manual` are preserved unchanged.
- Reviewer sees a flagged-only filtered view via a new `--review-flagged-only` flag on the `auto_translate` step.

**Backward compat:** PR B's `apply_hebrew_mapping.py` reads both flat (`name: he`) and nested (`name: {he: ...}`) shapes during the transition; emits the nested shape going forward. Existing 2024/25 file is migrated automatically on first run.

**File layout:** `mappings.he.yaml` stays a single file (not split per season) so a name change is one edit. Foreign players who played multiple eras get one entry covering all of them.

### 5.4 Reviewer-queue scaling

70 seasons × ~30 players × multiple page types per player + match reports per season ≈ **5000+ drafts** from one bulk run. Promoting them one-by-one via `Special:MovePage` is impractical.

**Out of scope for PR B** (it's a 3b workflow problem, not a pipeline problem) — but PR A surfaces it as a Phase 3b prep item:

1. **Per-namespace sub-special-pages** in `Wiki7ReviewGate`:
   - `Special:UnapprovedPlayers` (NS_DRAFT players)
   - `Special:UnapprovedMatches` (NS_DRAFT match reports, grouped by season)
   - `Special:UnapprovedSeasons` (NS_DRAFT season-overview pages)
   Each one filters NS_DRAFT to a known title-prefix pattern.

2. **Batch promotion** via a new `Wiki7ReviewGate:promoteBatch` maintenance script: `promoteBatch --filter="ראיון 19*" --confirm` moves every matching draft to mainspace in one shell call. Useful for promoting an entire decade after spot-checking a handful.

3. **Per-season filter on Special:UnapprovedPages** — a `?season=2010` query param that filters by the `season` Cargo field.

4. **Telegram inline-keyboard approve/reject** (already in the Phase 3b backlog).

PR B files these as Phase 3b backlog entries — no PR B code change.

### 5.5 ScraperAPI credit budget

Operator commits to one month of the ScraperAPI Hobby plan ($49/mo, **100,000 credits/month**) for the v1 all-time crawl; reverts to free tier afterwards for incremental in-season runs.

Estimated all-time consumption (post-dedup):

- ~77 seasons × per-season spiders: ~77 × 4 page-level requests (squad, fixtures, stats, transfers) ≈ **310 requests**. Squad spider chains the loan-page (+77). Seasons where the spider returns empty (1949-1974 sparse era) consume the request but skip the chained children.
- ~1,500 unique players (post-dedup across seasons) × 3 chained `/ceapi/` requests (profile + market values + transfer history) = **4,500 player-level requests**.
- ~45 seasons × ~30 matches × 1 match-report request = **1,350 match-report requests** (~1985/86 onwards; older seasons skipped because no report links).
- Club-level pages (coaches, honours, stadium, records, bilanz, platzierungen, transferrekorde, both record-tabs): **~15 one-shot requests**.

**Estimated total:** ~6,200 ScraperAPI credits. **Headroom on Hobby plan:** ~94% of monthly allowance left untouched. Generous re-crawl budget for fixing a spider bug mid-run, refreshing fixtures after a TM HTML change, or re-running a season with a corrected mapping. The local multi-season test (1985/86 + 2000/01 + 2024/25) is the trial slice the brief asks for; the full all-time prod run can then go end-to-end without staging.

**Cost note** to surface in PR B's prod-push report: actual credits consumed + remaining balance + an estimate of monthly-incremental ongoing cost so the operator can decide whether to keep Hobby or revert to free.

## 6. Curated subset — what makes it into Phase 3a R2

Summary of recommendations from §3, in priority order for PR B work:

### Must-have (PR B implementation gate)

**Spider/extractor closures:**
- Match report: referee, halftime score, AET indicator, stadium per match.
- Player profile: preferred foot, height, contract expiry, captain flag, current market value as its own field.
- Per-season manager extraction from `startseite/verein/2976/saison_id/<yr>` (constructs the historical-coach list).
- New `platzierungen` spider: per-season league position, points, W/D/L, GF/GA.
- New `bilanz` spider: per-opponent W/D/L + goal differential.
- Records spider: scrape both arrivals and departures tabs.

**New page types:**
- Per-season season-page header with "Finished Nth, X points, M-W-D-L, GF:GA".
- Per-season cup-runs summary ("Won the cup", "Reached the QF", etc.).
- Always-emitted **season-overview placeholders** for every season from 1949/50 → current (~77 pages), even when TM has no data. Templates render the existing fields when present; otherwise show "אין מידע זמין על עונה זו" (no information available for this season) + a one-line hand-curate prompt. Gives the wiki a complete chronological index.
- Derbies page (vs Maccabi TA, Hapoel TA, Beitar Jerusalem, Maccabi Haifa) — driven by `bilanz`.
- European campaign history page — derived from fixtures.
- Per-coach trophy list rendered on each coach page (derivable join: honours × per-season manager extraction).

**Pipeline shape changes:**
- Season-identifier normalization: human surfaces → slash format `YYYY/YY`; internal data files stay bare integer start-year as join key (§5.1).
- Cargo emission on every new field + every new entity type (Season, HeadToHead) — enables ad-hoc `{{#cargo_query}}`-driven aggregates from hand-curated 3b pages (§5.2).
- Translation file shape change to nested `{he, src, confidence}` w/ Claude API; backward-compat reader for the existing flat shape (§5.3).
- Idempotency + resume across seasons.
- Graceful degradation for sparse old seasons (null-tolerant Jinja, `#cargo_store` skipping incomplete rows).
- "What's missing" footer on historical-season pages.

**Test coverage:**
- Multi-era HTML fixtures: 2024/25 + 2015/16 (post-Turner move) + 1985/86 (lineups but no market values + no cards in older reports).
- Pytest per-spider, all three eras.
- Plus one fixture for a completely-empty season (e.g. 1965/66) to verify the placeholder path renders.

### Skip — deliberately out of scope

| Item | Why skip |
|---|---|
| Player agent | Privacy-flavoured; doesn't add wiki value. |
| Player injury/suspension history | Niche; rotting data; not core. |
| Player image (TM photo) | Copyrighted. Hybrid workspace policy: reviewer uploads CC photos manually. |
| Coach playing career | Off-club data. |
| Coach tactical preference | Subjective + flaky. |
| Post-match TM rating | Subjective. |
| Weather | TM doesn't publish. |
| Match referee assistants | Decided 2026-06-09 to skip: lead referee captures the operationally-meaningful authority on the match; the linesmen are bookkeeping for a *fan* wiki and would bulk every match infobox + Cargo schema for marginal added value. **Future re-include path** if we change our mind: TM exposes them as additional rows in the match-report's "referee box" (typically labeled "Assistant referee 1", "Assistant referee 2", "Fourth official"); add 3 new fields to `Template:Cargo/Match` (`assistant_referee_1`, `assistant_referee_2`, `fourth_official` — all `String`, all nullable for older matches), thread through `extract_match_officials()` in `match_spider.py` (~10 LOC CSS selector), and update `Match infobox.wikitext` to render when present. ~30 LOC end-to-end; no schema migration concerns because the fields are nullable. |
| TM API for Europa campaigns | `europapokalspiele/verein/2976` 404s; use fixture derivation. |

### Defer to Phase 3b (re-curation phase)

| Item | Why defer |
|---|---|
| Player native-script name for non-Hebrew foreign players | Translation review work, not pipeline work. |
| Per-competition player stats split (league vs cup vs Europe) | Aggregation is enough for v1. |
| Player full-career stats (across all clubs, not just HBS) | One extra ScraperAPI request per player; not core. |
| National team appearances | Not core to a *club* wiki. |
| Match events timeline unified into one render | Cosmetic; three tables work today. |
| Longest winning streak / biggest win-loss | Computable from fixtures; defer. |
| Individual player awards | Hand-curated; not on TM. |
| Vasermil stadium history page | Hand-curated stub. |
| Season narratives ("the title-winning campaign of 2015/16") | Hand-curated; not on TM. |
| Loan-with-option / contract length on transfers | Rare; cosmetic. |
| Reviewer-queue tooling (sub-special-pages, batch promote, Telegram inline approve) | 3b workflow problem, not pipeline. |

## 7. Resolved decisions (signed off 2026-06-09)

All 8 open questions PR A surfaced were resolved before any PR B code was written. Recorded here as the binding decisions PR B implements.

1. **Empirical season floor — RESOLVED: 1949/50 → current, always emit season-overview placeholder.** Pipeline attempts every season from the founding year. Seasons where TM has no data still get a season-overview page rendered from the template with a "no data available — please hand-curate" note + a one-line explanation of TM's coverage start. Sparse seasons get partial pages (overview + arrivals/departures if those exist; no squad/stats if those don't). The empirical floor becomes a *finding* in the prod-push report, not a config knob. Rationale: gives the wiki a complete chronological index from day one + supports Phase 3b hand-curated content for the pre-TM era.

2. **Season identifier — RESOLVED: slash format `YYYY/YY` on all human surfaces; bare integer start-year internally.** Page titles (e.g. `סגל 2024/25`, `העברות 2024/25`), h1 headings, category names, infobox display all use `2024/25`. Internal data files (`season: "2024"`), Cargo `season` column, filesystem dirs (`output/2024/`), spider CLI args (`--season 2024`), and TM `saison_id` URL params all stay as bare integer start-year. One helper `to_season_display(season: str) -> str` does the format flip. Rationale: integer is sortable + matches TM's URL + matches the existing spider arg / filesystem layout; converting only for display avoids churning every test + spider + file path.

3. **Cross-season aggregates — RESOLVED: pipeline-written + Cargo rows for ad-hoc queries.** Pipeline keeps producing the well-known aggregate pages (top scorers, season overview, leaderboards) via Python aggregation — works today, no rework. *Additionally*, every new template (`Season infobox`, `HeadToHead row`, etc.) transcludes the matching Cargo declaration template so the data lands in queryable Cargo tables. Concrete payoff: hand-curated 3b pages (Vasermil stadium history, Did You Know, Fan Culture) can embed `{{#cargo_query}}` calls without any pipeline change. Cost: ~5 lines per new template.

4. **Translation strategy — RESOLVED: Claude API with nested `{he, src, confidence}` shape, backward-compat reader.** Switch `auto_translate_hebrew.py` from Google Translate to Claude API. Output shape becomes nested per entry (manual entries preserved with `src: manual`; auto entries flagged `src: auto-llm` with `confidence: high|low`). `apply_hebrew_mapping.py` reads both shapes during transition; emits nested going forward. Existing 2024/25 file is migrated automatically on first run. ~$0.50 first-pass cost for the all-time corpus with prompt caching; negligible afterwards. Reviewer sees a flagged-only filtered view via new `--review-flagged-only` flag.

5. **Reviewer-queue scaling — RESOLVED: detailed proposal in Phase 3b backlog; PR B's prod-push report invokes it.** PR B does not build the tooling, but the operator confirmed (2026-06-09) they want the best final solution invested in upfront. The proposal lands in `docs/phase-3b-backlog.md` with this level of detail so 3b implementation is unambiguous:

   - **3b.1 Sub-special-pages** (~30 LOC PHP in `Wiki7ReviewGate`). Three new special pages, each filtering NS_DRAFT by title-prefix or Cargo lookup:
     - `Special:UnapprovedPlayers` — drafts NOT matching the seasonal-page title patterns. Pure player profile drafts. Sorted by draft creation time descending.
     - `Special:UnapprovedMatches` — drafts whose title matches the match-report pattern (e.g. `Draft:<date> vs <opponent>`). Grouped by season + competition. Each group is collapsible so the reviewer can ignore irrelevant competitions.
     - `Special:UnapprovedSeasons` — drafts matching `Draft:עונת *`, `Draft:סגל *`, `Draft:העברות *`. Grouped by decade (1950s, 1960s, …). Lets the reviewer triage by era ("the modern era is fine; let me focus on the 1980s").
   - **3b.2 Batch-promote maintenance script** (~80 LOC PHP). `php maintenance/run.php extensions/Wiki7ReviewGate/maintenance/promoteBatch.php --filter='עונת 199*' --dry-run|--confirm`. Walks NS_DRAFT matching the filter glob, moves each to mainspace via the same `MovePage` primitive the UI uses, with the redirect-suppression flag set. `--dry-run` lists what would move; `--confirm` actually moves. Logs to `extensions/Wiki7ReviewGate/maintenance/promote_<timestamp>.log` for audit. Refuses to run if both flags are passed (same safety pattern as the `resetContent` script).
   - **3b.3 Per-season filter on Special:UnapprovedPages** (~20 LOC PHP hook). `Special:UnapprovedPages?season=2010` filters the existing Approved Revs special page by joining against the Cargo `season` field. Same filter applies to the API surface (`list=unapprovedrevs`).
   - **3b.4 Telegram inline-keyboard approve/reject** (already in 3b backlog; cross-referenced here). Phone-based one-tap approval for the operator on the move; reduces the desktop-required surface for fast-path approvals.

   PR B's prod-push report quantifies the draft count post-run (estimated 2,000-2,400 across the all-time corpus) and adds a "before reviewing this, ship 3b.1 + 3b.2 first" pointer to the 3b backlog. That pointer becomes the gate on starting Phase 3b review work.

6. **ScraperAPI strategy — RESOLVED: Hobby plan ($49/mo, 100k credits) for the v1 all-time crawl; no decade-staging.** Operator commits one month of the paid tier; reverts to free after v1 lands. Estimated ~6,200 credits for the full crawl (~6% of monthly headroom) leaves generous slack for re-crawls after spider fixes. Full all-time prod run goes end-to-end; the local multi-season test (1985/86 + 2000/01 + 2024/25 + 1965/66-as-empty-placeholder fixture) is the trial slice. Cost reporting in the prod-push report includes the monthly-incremental ongoing estimate so the operator can decide whether to keep Hobby or revert to free for in-season work.

7. **Skip list — RESOLVED: hold the line, with referee-assistants future-re-include path documented.** Every item in §6's "Skip" table stays out of scope. The "Match referee assistants" row gets an expanded note (in-table) describing exactly how to add the field if a future operator changes their mind: add 3 nullable String fields to `Template:Cargo/Match`, ~10 LOC CSS-selector add in `match_spider.py`, update `Match infobox.wikitext` to render when present. Nullable means no schema migration concerns. ~30 LOC end-to-end. Recorded for traceability.

8. **3b defer list — RESOLVED: move "Coach trophies won (with HBS)" into PR B; keep rest deferred.** The trophy join is ~10 LOC (honours.json × per-season manager extraction, computed at import time) and substantially improves coach pages ("won 2 league titles + 1 cup as HBS manager" reads as the *point* of a coach page). Cost-to-value is too good to defer. Everything else in the defer list stays in 3b for stated reasons.

---

## Appendix A — Cargo schema delta (planned for PR B)

Net-new fields on existing tables:

```diff
 Template:Cargo/Player
+  preferred_foot = String
+  height_cm      = Integer
+  contract_expires = String
+  is_captain     = Boolean
+  current_market_value = String  # convenience surfacing; data already in market_values
+  other_positions = List (,) of String

 Template:Cargo/Match
+  halftime_score = String
+  aet            = Boolean
+  referee        = String
+  stadium        = String

 Template:Cargo/Transfer
+  from_club_tm_id = String   # for future cross-linking
+  to_club_tm_id   = String

 Template:Cargo/Coach
+  is_caretaker   = Boolean
+  tenure_seasons = List (,) of String  # which seasons this coach was active
```

New tables:

```diff
+Template:Cargo/Season
+  season = String              # "2024" (start-year)
+  competition = String         # "Ligat ha'Al"
+  tier = Integer               # 1, 2
+  final_position = Integer
+  matches = Integer
+  wins = Integer
+  draws = Integer
+  losses = Integer
+  goals_for = Integer
+  goals_against = Integer
+  points = Integer

+Template:Cargo/HeadToHead
+  opponent = String
+  matches = Integer
+  wins = Integer
+  draws = Integer
+  losses = Integer
+  goals_for = Integer
+  goals_against = Integer
+  avg_attendance = Integer
```

Each new field documented in a one-line rationale comment in the Cargo template (per PR brief).

## Appendix B — TM URL catalogue (verified 2026-06-09)

For HBS specifically:

| URL pattern | Status | Notes |
|---|---|---|
| `/hapoel-beer-sheva/startseite/verein/2976/saison_id/<yr>` | ✅ | Per-season summary; squad table; arrivals/departures; coach(es). |
| `/hapoel-beer-sheva/kader/verein/2976/saison_id/<yr>` | ✅ | Detailed squad. |
| `/hapoel-beer-sheva/leihspieler/verein/2976` | ✅ | Loaned-out players (current only — no saison_id). |
| `/hapoel-beer-sheva/leistungsdaten/verein/2976/plus/1?saison_id=<yr>` | ✅ | Per-season player stats. |
| `/hapoel-beer-sheva/spielplandatum/verein/2976/saison_id/<yr>` | ✅ | Fixtures + match-report links. |
| `/hapoel-beer-sheva/alletransfers/verein/2976/saison_id/<yr>` | ✅ | Club-level transfers in/out. |
| `/hapoel-beer-sheva/mitarbeiter/verein/2976` | ✅ | Current staff only. |
| `/hapoel-beer-sheva/erfolge/verein/2976` | ✅ | Honours. |
| `/hapoel-beer-sheva/stadion/verein/2976` | ✅ | Stadium. |
| `/hapoel-beer-sheva/transferrekorde/verein/2976` | ✅ | Club transfer records (need both tabs). |
| `/hapoel-beer-sheva/platzierungen/verein/2976` | ✅ | Per-season league position (1986/87+). |
| `/hapoel-beer-sheva/bilanz/verein/2976` | ✅ | Per-opponent head-to-head. |
| `/hapoel-beer-sheva/torschuetzenkoenig/verein/2976` | ❌ 404 | Use stats-aggregation leaderboards instead. |
| `/hapoel-beer-sheva/europapokalspiele/verein/2976` | ❌ 404 | Use fixtures-by-competition derivation. |
| `/hapoel-beer-sheva/trainer/verein/2976` | ❌ 404 | TM removed; use per-season `startseite` extraction. |
| `/hapoel-beer-sheva/trainerhistorie/verein/2976` | ❌ 404 | Same. |
| `/ceapi/marketValueDevelopment/graph/<id>` | ✅ | Per-player MV history. |
| `/ceapi/transferHistory/list/<id>` | ✅ | Per-player transfer history. |
| `/spielbericht/index/spielbericht/<id>` | ✅ | Individual match reports. |

## Appendix C — Honours empirically verified 2026-06-09

Sourced from `/hapoel-beer-sheva/erfolge/verein/2976`. These are the ground truth the pipeline should reproduce:

- **Israeli Champion** (6): 1974/75, 1975/76, 2015/16, 2016/17, 2017/18, 2025/26
- **Israeli Cup winner** (4): 1996/97, 2019/20, 2021/22, 2024/25
- **Israeli Super Cup winner** (5): 1975/76, 2016/17, 2017/18, 2022/23, 2025/26
- **Israeli 2nd-tier champion** (1): 2000/01
- **Europa League participant**: 2016/17, 2017/18, 2020/21
- **Conference League participant**: 2022/23
- **Promoted to 1st league**: 2000/01, 2008/09
