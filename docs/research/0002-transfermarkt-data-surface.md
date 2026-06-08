# Research 0002 — Transfermarkt data surface for Hapoel Beer Sheva

- **Status:** PR A draft — awaiting alignment on the curated-subset decisions before PR B implements.
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

**Recommended PR B scope for "all-time": seasons 1974/75 → current** (probe each season; skip absent ones gracefully; expect ~50 seasons present out of the ~52 calendar slots, with gaps in the late-70s/early-80s).

**Pre-1974 is out of scope** for the bot — content for the 1949 founding through the early 1970s lives in hand-curated articles (Phase 3b's "Did You Know" / fan-culture work), not the data pipeline.

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
| Trophies won (with HBS) | ✅ derivable from honours + tenure dates | ❌ | **Phase 3b** | Computable from existing data; defer. |

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

ScraperAPI free tier is 1,000 requests/month (verify operator tier). All-time scope estimated:

- ~50 seasons × per-season spiders: ~50 × 4 page-level requests (squad, fixtures, stats, transfers) = **200 requests** (squad spider chains the loan-page = +50).
- ~50 seasons × 30 players × 3 chained `/ceapi/` requests (profile + market values + transfer history) = **4,500 player-level requests**. Players are heavily overlapping across seasons, so dedup by TM ID before fetching — realistic post-dedup is ~1,500 unique players × 3 = **4,500** dedup'd ceiling.
- ~50 seasons × ~30 matches × 1 match-report request = **1,500 match-report requests** (a lot; cap-driven decision).
- Club-level pages (coaches, honours, stadium, records, bilanz, platzierungen): **~15 one-shot requests**.

**Optimistic total:** ~6,000-7,000 ScraperAPI requests.

**Decision:** PR B's "local multi-season test" runs end-to-end against a small slice first (1985/86 + 2000/01 + 2024/25) to validate the schema + Cargo + idempotency story. The full all-time prod run then runs in slices (decade-by-decade), with credits verified at the start of each slice. If we exhaust the free tier, the operator either tops up the plan or accepts that the long tail (1985-1995) stays sparse until next month.

**Cost note** to surface in PR B's prod-push report: actual credits consumed per slice + remaining balance.

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
- Derbies page (vs Maccabi TA, Hapoel TA, Beitar Jerusalem, Maccabi Haifa) — driven by `bilanz`.
- European campaign history page — derived from fixtures.

**Pipeline shape changes:**
- Season-identifier normalization (§5.1).
- Translation file shape change to nested w/ confidence (§5.3).
- Idempotency + resume across seasons.
- Graceful degradation for sparse old seasons (null-tolerant Jinja, `#cargo_store` skipping incomplete rows).
- "What's missing" footer on historical-season pages.

**Test coverage:**
- Multi-era HTML fixtures: 2024/25 + 2015/16 (post-Turner move) + 1985/86 (lineups but no market values + no cards in older reports).
- Pytest per-spider, all three eras.

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
| Match referee assistants | Stretch; lead referee is enough for the wiki. |
| TM API for Europa campaigns | `europapokalspiele/verein/2976` 404s; use fixture derivation. |

### Defer to Phase 3b (re-curation phase)

| Item | Why defer |
|---|---|
| Player native-script name for non-Hebrew foreign players | Translation review work, not pipeline work. |
| Per-competition player stats split (league vs cup vs Europe) | Aggregation is enough for v1. |
| Player full-career stats (across all clubs, not just HBS) | One extra ScraperAPI request per player; not core. |
| National team appearances | Not core to a *club* wiki. |
| Trophies won by coach (with HBS) | Derivable post-hoc. |
| Match events timeline unified into one render | Cosmetic; three tables work today. |
| Longest winning streak / biggest win-loss | Computable from fixtures; defer. |
| Individual player awards | Hand-curated; not on TM. |
| Vasermil stadium history page | Hand-curated stub. |
| Season narratives ("the title-winning campaign of 2015/16") | Hand-curated; not on TM. |
| Loan-with-option / contract length on transfers | Rare; cosmetic. |
| Reviewer-queue tooling (sub-special-pages, batch promote, Telegram inline approve) | 3b workflow problem, not pipeline. |

## 7. Question for the user (resolve during PR A review)

These are decisions PR A puts forward; the user signs off (or redirects) before PR B starts:

1. **Empirical season floor** — recommended 1974/75 to current, probe each season, skip absent. Acceptable?
2. **Season identifier** — slash format `YYYY/YY` on all page titles + headings (§5.1). Acceptable?
3. **Cross-season aggregates** — keep Python-driven *and* additionally emit Cargo rows for ad-hoc queries (§5.2). Acceptable?
4. **Translation strategy** — switch auto-translate to Claude API with nested `{he, src, confidence}` shape (§5.3). The auto-translation file pattern is backward-compatible. ~$0.50 first-pass cost. Acceptable?
5. **Reviewer-queue scaling** — surface as Phase 3b prep; PR B doesn't build it. Acceptable?
6. **ScraperAPI strategy** — stage the prod run in decade slices, verify credits between slices (§5.5). Acceptable?
7. **Skip list** (§6 "Skip — deliberately out of scope") — anything that should move back into scope?
8. **3b defer list** (§6 "Defer to Phase 3b") — anything that should move into PR B?

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
