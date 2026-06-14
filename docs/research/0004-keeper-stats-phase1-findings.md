# 0004 — Keeper & per-competition stats: Phase-1 findings & decision packet

> **Status:** Phase 1 complete (research / verify / decide). **No production code written.**
> This doc is the decision packet for the operator. Pick a scope+approach from §6 before any
> Phase-2 implementation. Companion to the brief `0004-keeper-and-per-competition-stats-brief.md`.
> Sources: 5 parallel verification readers, a 3-lens judge panel, and 3 adversarial refutation passes.

---

## 1. Executive summary

The trigger is real: a goalkeeper's seasonal-stats table shows only outfield columns
(Goals/Assists ≈ 0), so the *informative* keeper metrics — **clean sheets** and **goals
conceded** — are missing. Phase-1 confirmed the brief's structural picture (one SSR club page
per season → 7 stat fields; keeper columns live only on the JS-rendered per-player page) but
**corrected several decision-critical claims** the brief got wrong because it was time-boxed and
single-sample.

The headline reconciliation: **Option A (compute clean sheets / goals conceded from our own
match data) is feasible and verified end-to-end** — I computed Niv Eliasi's real numbers (2023:
32 starts / 12 CS / 31 conceded; 2024: 36 starts / 13 CS / 34 conceded) directly from match
files, including the minute-aware keeper-sub case. It is free, render-free, internally
cross-checkable, and produces the *more fan-relevant* "clean sheets FOR HBS" metric. **Option B
(render=true scrape)** is the only path to TM-authoritative all-clubs / per-competition figures,
but it is the pipeline's first JS-render dependency on a brittle hashed-class Svelte grid, carries
a silent-zero data-loss risk, and its per-competition row reshape has a wider blast radius than
the brief lists. **Option C (subs on/off)** is a near-free spider win but solves none of the
keeper trigger. Credit cost — the brief's headline worry — turns out to be a **non-issue**: a
single per-player render returns the keeper's whole career, so a full backfill is ~14 renders
(~140–350 credits, < 5% of one all-time run), not 1/keeper/season.

All three judge lenses (data authority, cost/robustness, product value) and the adversarial
passes converge on the same shape: **ship a Hybrid — Option A now as the authoritative HBS
keeper headline, Option C folded in as a free ride-along, and Option B deferred behind a real
per-competition / all-clubs product trigger.** Option A's feasibility is *conditional* on
handling ~6 verified data traps (dual lineup schema, 6 missing 2024 lineups, penalty-shootout
result poisoning, false keeper-subs, a corrupt goals record, and a required join to player
positions). None are blockers; all are testable. One genuine completeness gap remains: **no
network probe was run** (a hard constraint of Phase 1), so historical-keeper-data availability on
TM and the cheap-JSON-endpoint hypothesis are both **unverified** — neither is needed for the
recommended Option-A path.

---

## 2. Verified-vs-brief corrections (what Phase-1 changed)

| # | Brief claimed | Phase-1 found | Impact on decision |
|---|---|---|---|
| 1 | "No `is_home` flag — infer HBS side from `venue`/`goals.team`." (§3 Cons) | **REFUTED.** A `venue` field exists with values `H`/`A` on **100% of 286 matches** (0 missing). `venue='H'` ⇒ HBS is `home_lineup`. Cross-validated: 224 matches, 0 mismatches vs scorers. | Side-detection is trivial and reliable — removes a stated Option-A risk. **Note:** `result` is `home_score:away_score` (NOT HBS:opp); combine with venue. |
| 2 | "10–25 credits / keeper / **season**" framing (§2.2, §3, §7.3) | **OVERSTATES cost ~2.4×.** The per-player URL (`.../spieler/<id>/plus/1`, no `saison_id`) returns the **entire career** season×competition in ONE render. Realistic backfill = **~14 renders** (1/keeper) ≈ 140–350 credits; 8 if scoped to keepers with HBS data. Worst-case naive = 825 credits = 10.5% of one all-time run. | Credit cost is a **non-issue** for B. Reframes the trade-off: B's real cost is brittleness + schema blast radius, not credits. |
| 3 | "Verified [no keeper cols] against the 2015 fixture too." (§2.1) | **UNVERIFIABLE.** Only `leistungsdaten_sample.html` exists under `tests/fixtures/`; no 2015 club fixture present. The structural claim (club page = no keeper cols) holds for the available fixture. | Minor. Structural conclusion stands; the secondary corroboration cannot be re-checked. |
| 4 | "`_MAX_WORKERS = 2` is load-bearing" for the TM scrape (§8) | **MIS-ATTRIBUTED.** `_MAX_WORKERS=2` lives in the **Wikidata enrichment layer** (`wikidata_lookup.py:76`), NOT the Scrapy scraper. The scraper's throttle is `CONCURRENT_REQUESTS=20`/`CONCURRENT_REQUESTS_PER_DOMAIN=20` (`settings.py:34-35`). | A render path needs its **own** concurrency cap in `tmk-scraper/settings.py` — don't reach for the wrong knob. 429-as-retryable is already wired. |
| 5 | "13 competition codes for Niv Eliasi … `GB1`/`IT1`/`ES1` from his career elsewhere." (§2.3) | **NUANCED.** 13 `/wettbewerb/` codes appear, but **6 (`CL`,`GB1`,`IT1`,`ES1`,`L1`,`FIWC`) are homepage "Recommendations" nav noise**, not his competitions. Real stats-grid set = **7** (`ISR1`,`ISRF`,`ISPO`,`ECLQ`,`ELQ`,`IRLP`,`FS`). | A naive `grep /wettbewerb/` over-counts by 6. A B-parser must scope competition extraction to `/leistungsdatendetails/spieler/<id>/wettbewerb/` anchors. |
| 6 | "render=true 308 KB" / general per-player page framing | **NUANCED.** Saved file is 301–308 KB. More importantly it is **season-scoped to 25/26** by default (`<title>` = "Stats 25/26", `season="2025"`); the *summary* grid shows the selected season's per-competition rows. Historical seasons need per-season fetches OR the no-`saison_id` career URL (which DID return full career — see #2). | The "one fetch = whole career" win (#2) depends on using the no-`saison_id` URL; the season-filtered view is per-season. Parser must target the career URL. |
| 7 | ceapi/JSON endpoint "would be far cheaper … if it exists and is reachable" (§4.6) framed as a viable thread | **Stays a HYPOTHESIS — and the strong version FAILS.** No `ceapi`/`/api/`/`graphql`/`fetch`/inline-JSON in the 308 KB HTML (all grep = 0). Only signal: an `api-type="tmapi"` attribute on the Svelte mount tag — a component param, not an endpoint. The URL is compiled into a minified CDN bundle that was never fetched. Prior art: **7 guessed ceapi paths already 404'd.** | Do NOT plan as if a cheap JSON path exists. Odds = MEDIUM at best, recoverable only by static analysis of `player-performance-proxy/bundle.js` (a free CDN GET). Keep it a cost-optimization spike, not a load-bearing assumption. |
| 8 | "Substitutions on/off … confirmed columns" (§2.1, §2.3) | **CONFIRMED + extended.** Subs on/off ARE in the club table (header + body at idx 11/12) and unmapped. **PPG is also present** (a third free column the brief omits). The spider author already documented the full layout in a comment (`stats_spider.py:99-101`) and deliberately skipped them. | Option C is a deliberate, low-risk ~8–10-line spider change. PPG is available too if scope grows. |
| 9 | Option A: "from our own match data" (self-contained) | **NUANCED.** Match lineups carry **no position field**, so keeper identification REQUIRES joining `tm_player_id` against `players.he.jsonl` `main_position=='שוער'`. Option A is **not self-contained** — it depends on the merged players file and must run **post-normalize/merge**. | A's compute step slots between merge and import (needs normalized positions), not in the scrape block. A guard must fail loud if a keeper id is absent from the position map. |
| 10 | §8 touch-points list of stats consumers | **INCOMPLETE.** Two stats consumers the brief omits — `import_season_overview` and `import_squad_page` — also read `stats.jsonl` and would **double-count / silently drop rows** under a per-competition reshape. | Materially widens Option B's per-competition blast radius. Argues for a SEPARATE per-competition table over reshaping `player_stats`. |

### Adversarial corrections to Phase-1's own findings (second-order)
- **"3 keeper subs" → only 2 are real.** The corpus has **2 genuine GK→GK swaps** (2019 idx4, 2021 idx45). The third (2024 idx31, Marciano in / Peretz [midfielder] out) is an **outfield sub by a keeper** — a *false keeper-sub*. Rule must be: keeper change only when BOTH player_in AND player_out are position-confirmed keepers.
- **"every keeper-match is computable" → "every keeper-match WITH a resolvable lineup".** 3 of the 6 missing/empty 2024 lineups are HBS-home (idx 11/14/32) with no keeper-revealing sub — those keeper-matches are **unrecoverable** from match data (all 3 are clean sheets that would silently vanish). Must skip-and-report, not zero.
- **A Phase-1 reader itself mis-mapped the render grid columns** (reported ISR1 conceded=7/cs=2 when header-order + the unambiguous Total row prove conceded=19/cs=7). This is direct proof that the B-parser's column alignment is a real trap — Appearances and the competition label are `<a>` elements while other stats are `<div class=tm-grid__cell>`, so a `tm-grid__cell`-only parser drops a column and shifts everything.

---

## 3. Per-option trade-off

### Option A — Compute from our own match data
- **Data authority:** HBS-specific ("clean sheets FOR HBS"), our computed number not TM's. Killer property: goals_conceded has **two independent internal sources** (goals-list count AND result+venue) that cross-check — 278/279 agree, and the 1 disagreement *exposes* a corrupt source record. Corruption is detectable, not silent.
- **Cost:** Zero scrape/render credits, zero ScraperAPI exposure. 100% SSR.
- **Robustness/maintainability:** No JS/markup-drift exposure, but the **most bespoke correctness logic to own**: dual lineup schema, missing lineups, penalty-shootout poisoning, false keeper-subs, minute-aware attribution, cross-join to player positions, a new derive step wired into BOTH single- and multi-season paths.
- **Feature upside:** Keeper headline numbers on keeper pages + an all-time clean-sheets leaderboard (small Python-only add). **Cannot** produce per-competition / European-campaign / all-clubs figures.
- **Effort/blast-radius:** Moderate, well-scoped. New post-merge derive step + 3 silent-drop chokepoints + additive nullable Cargo columns. No schema-shape migration.
- **Judge scores:** authority 7, cost/robust 7.5, product 7.

### Option B — Render=true per-player scrape
- **Data authority:** TM-authoritative; the ONLY source for all-clubs + per-competition + own_goals. But weakest integrity profile: silent-zero if render JS fails; creates a SECOND "clean sheet" figure that must never be silently merged with A's HBS number.
- **Cost:** Credits are a non-issue (~140–350 for full backfill, < 5% of one run). Real cost is elsewhere.
- **Robustness/maintainability:** Worst. First JS-render dependency; hashed-class Svelte div-grid (`role=row`, no `<td>`) with base64-SVG headers; from-scratch parser unlike every `table.items` spider; N=1 fixture (one player, one season); needs a fail-loud markup-drift guard + concurrency cap. Mitigable by anchoring on ARIA roles + header `title=` label text (NOT svelte-* hashes), but that approach is untested here.
- **Feature upside:** Highest ceiling — per-competition tables, European-campaign player stats, competition-filtered leaderboards, TM-authoritative all-clubs keeper totals, own goals.
- **Effort/blast-radius:** Largest. Per-competition reshape double-counts in `player_page.j2` totals, `import_leaderboards`, **`import_season_overview`, `import_squad_page`** (last two omitted by the brief) unless modelled as a SEPARATE table.
- **Judge scores:** authority 6, cost/robust 4, product 6.

### Option C — Subs on/off (free win)
- **Data authority:** Cleanest single-source — data already in the SSR club page we fetch. Only nuance: store NULL (not 0) for historical/outfield rows where TM never exposed it.
- **Cost:** Zero new credits.
- **Robustness/maintainability:** Highest. ~8–10 spider lines, same `table.items` markup, fits `HEADER_ALIASES`, additive nullable column.
- **Feature upside:** Low — subs on/off is bench/rotation trivia; orthogonal to the keeper gap. (PPG also available for free if wanted.)
- **Effort/blast-radius:** Smallest. No new stage, no render, no new spider.
- **Judge scores:** authority 8, cost/robust 9, product 3.

### Hybrid (recommended)
- A owns the HBS keeper headline (free, cross-checkable, correct semantic); C rides along as a free integrity win; B is reserved strictly for the all-clubs / per-competition dimension A structurally cannot produce — kept as distinct, clearly-labeled metrics, never reconciled into one figure.
- **Judge scores:** authority 9, cost/robust 8, product 9 — the top score on all three lenses.

---

## 4. Compact trade-off matrix

| Dimension | Option A (compute) | Option B (render) | Option C (subs) | Hybrid (A+C now, B later) |
|---|---|---|---|---|
| **Data authority** | HBS-specific, our number; 2 cross-checking internal sources | TM-authoritative, all-clubs/per-comp; silent-zero risk | Single clean source (SSR) | Each source owns what it's authoritative for |
| **ScraperAPI cost** | 0 | ~140–350 cr full backfill (<5% of a run) | 0 | A+C free now; B's small cost only if triggered |
| **Robustness / maint.** | SSR, but most bespoke logic to own | First JS-render dep; brittle Svelte grid; from-scratch parser | Highest — reuses existing idiom | High — routine pipeline stays render-free |
| **Feature upside** | Keeper headline + CS leaderboard; no per-comp | Highest ceiling: per-comp, Europe, comp-filtered boards, own goals | Low (bench trivia) | Ships headline now, keeps premium ceiling reachable |
| **Effort / blast-radius** | Moderate; additive columns, new derive step | Largest; per-comp reshape breaks 4–5 aggregators | Smallest; ~8–10 lines | Moderate now; defer B's reshape until justified |
| **Judge (auth/cost/prod)** | 7 / 7.5 / 7 | 6 / 4 / 6 | 8 / 9 / 3 | **9 / 8 / 9** |

Shared downstream cost for ANY new stat column: thread the **3 silent-drop chokepoints**
(`normalize_enrich_players.py:128-140`, `player_page.j2:104-114`, `import_templates.py:127-137`);
on prod run the ApprovedRevs re-approval + `cargoRecreateData.php --table=player_stats` + `runJobs.php`
recipe (`operational-bootstrap.md §9`). New/keeper fields store **NULL when absent, not 0**.

---

## 5. Recommendation (proposal — operator to confirm)

**Ship the Hybrid, phased:**

1. **Phase 2a (now): Option A + Option C.**
   - **A** delivers clean sheets + goals conceded on keeper pages from our own match data, plus a
     "most clean sheets" all-time leaderboard. Authoritative metric: "FOR HBS, 2019–2024."
     Implement with the **explicit hybrid conceded source** (result+venue for full-90 single-keeper
     starts; goals-list for penalty rows and GK-sub windows) and a reconciliation assertion that
     flags any match where the two disagree.
   - **C** folds in subs on/off (and optionally PPG) as a free ride-along since the schema and
     spider are already being touched — but ONLY because the cost is ~zero; it has low standalone value.
   - Keeper columns are **additive nullable** to `Template:Cargo/PlayerStats` (NOT a per-competition
     reshape), so the existing season-keyed consumers stay untouched. Conditional keeper columns in
     `player_page.j2` reuse the existing goalkeeper predicate (`'שוער' in pos`, line 148).

2. **Phase 2b (deferred, behind a real trigger): Option B.**
   - Adopt render=true ONLY when the operator concretely wants TM-authoritative all-clubs figures
     OR a per-competition / European-campaign table. At that point model per-competition rows as a
     **SEPARATE Cargo table** (e.g. `PlayerStatsComp`) to avoid the double-count blast radius in
     `import_season_overview` / `import_squad_page` / leaderboards / j2 totals.
   - Precede any B build with a **zero-cost discovery spike**: fetch the static CDN bundle
     `player-performance-proxy/bundle.js` (render=false, ~0–1 credit) and statically analyze it for
     the `tmapi` endpoint. If a JSON path is found and reachable, it obviates the brittle Svelte parser.

**Why this shape:** it maximizes shipped value per unit cost (A's headline is the universally-wanted
fix, free, and internally verifiable), pays B's brittleness/credit/reshape tax only when a concrete
premium feature justifies it, and never silently merges two different "clean sheet" numbers. All
three judge lenses score the Hybrid highest (9/8/9). The decision is **not** made — confirm scope
via the open questions below.

---

## 6. Open questions for the operator (refined from brief §7)

### Q1 — Source of truth for keeper headline numbers
- **Options:** (a) Option A compute only; (b) Option B render scrape only; (c) Hybrid A-now / B-later.
- **Recommendation:** **(c) Hybrid** — A as the authoritative HBS headline now, B deferred behind a real per-competition/all-clubs trigger. A is free, cross-checkable, and the more fan-relevant metric; B's brittleness/reshape tax is only worth paying for what A structurally can't produce.

### Q2 — Scope of new fields in Phase 2a
- **Options:** keeper-only (clean sheets + goals conceded); + subs on/off (free C); + PPG (free); + own goals (needs B or A-from-goals-list).
- **Recommendation:** **keeper-only + subs on/off (Option C)**. Add PPG only if the operator wants it (free either way). Defer own_goals to Phase 2b (cleanest from TM, ambiguous to compute reliably).

### Q3 — Historical coverage & the conceded metric's stated scope
- **Options:** (a) compute A for all seasons we hold (2019–2024) and label clearly "for HBS, these seasons"; (b) backfill TM-authoritative via B for full career.
- **Recommendation:** **(a)** for Phase 2a, with explicit UI labeling. Note the unrecoverable cases: 3 HBS-home 2024 matches have missing lineups (idx 11/14/32, all clean sheets) — **report these as a count, never silently zero**. Optionally backfill those 5 lineups by re-scraping the match reports (a 2024 scrape gap).

### Q4 — Keeper display: hide goals/assists?
- **Options:** (a) keep all standard columns AND add keeper columns (TM's "in addition" behavior); (b) hide goals/assists for keepers.
- **Recommendation:** **(a) keep + add.** Matches TM (verified §2.4) and is the smaller change (just wrap extra `<th>/<td>` in the existing goalkeeper predicate). Hiding requires an additional conditional with little upside.

### Q5 — Per-competition modelling (only if/when B is triggered)
- **Options:** (a) reshape `player_stats` to one-row-per-player-season-**competition**; (b) keep season-total `player_stats` untouched and add a SEPARATE per-competition table.
- **Recommendation:** **(b) separate table.** Reshaping `player_stats` double-counts in 4–5 aggregators (incl. `import_season_overview` + `import_squad_page`, which the brief omits). A separate table leaves every season-keyed consumer intact. This question only becomes live in Phase 2b.

### Q6 — ceapi / tmapi JSON endpoint hunt
- **Options:** (a) run a zero-cost static-analysis spike on the CDN bundle before any B build; (b) skip and go straight to render=true; (c) skip entirely (Option A path).
- **Recommendation:** **(a) — but only as a precursor to B, not a blocker for A.** Treat the JSON path as FALSE-until-probed. If Phase 2a (Hybrid) is chosen, this stays dormant until B is triggered. Do bundle static analysis, never blind path-guessing (7 prior guesses 404'd).

---

## 7. Completeness check — what is unrun / unverified / un-surfaced

- **No network probe was run (hard Phase-1 constraint).** Consequently:
  - **Historical keeper-data availability on TM (2019–2021) is UNVERIFIED.** The saved fixture is one keeper, one season (25/26). 6 of 14 keepers have zero stat rows in any season; a B backfill might fetch empty/other-club pages. **Must-probe before any B backfill** (recommend metered probes: Setkus 147028 for 2019, Glazer 347268 for 2021). Does NOT affect the recommended Option-A path.
  - **The cheap-JSON (`tmapi`) endpoint is UNVERIFIED** — existence, path, params, reachability, and whether it needs browser-only auth are all unknown. The strong "it exists and is reachable" claim FAILS on current evidence; only a MEDIUM-odds hypothesis survives, recoverable by free bundle static analysis.
- **B's parser is built from N=1.** No outfield-player fixture and no historical-season fixture exist to test column-substitution drift (keeper Goals-conceded/Clean-sheets occupy the slots that hold Goals/Assists for outfielders). Before any bulk B run, capture ≥1 outfield + ≥1 historical fixture and a markup-drift contract test.
- **Abandoned/awarded matches are undetectable** — no abandonment field, 0 in corpus. A future abandoned match would be silently mis-scored. Recommend a guard: any `result` not matching `^\d+:\d+( \(penalties\))?$` ⇒ fail-loud/skip-with-report.
- **The 2019 keeper rows (Setkus, Levita) live ONLY in `tmk-scraper/output/<s>/stats.json`** — `data_pipeline/output/<s>/stats.jsonl` exists only for 2020–2024. Any normalized-stats consumer for Option A must mind this 2019 gap.
- **No blocker un-surfaced.** Every Option-A trap is verified and testable; Option C is trivially safe; Option B's risks are all named with mitigations.

---

## 8. Phase-2 implementation notes (carry-forward, not yet actioned)

- **Silent-drop chokepoints (thread every new field through all 3):** `normalize_enrich_players.py:128-140`, `player_page.j2:104-114` (+ display row/totals `:87-100`), `import_templates.py:127-137`. Merge is field-agnostic/safe.
- **Option A placement:** new derive step **post-normalize/merge** (needs `players.jsonl` `main_position`); wire into BOTH multi-season (post-merge) AND single-season (`PIPELINE_OUTPUT_DIR/<season>`) paths or it no-ops on `--season` runs. Precedent: `derive_coach_trophies` (but that runs in the scrape block; A needs positions, so later).
- **Option A correctness rules:** GK-change only when BOTH in+out are confirmed keepers; explicit hybrid conceded precedence (result+venue for full-90 starts, goals-list for penalty/AET/sub-window); detect `(penalties)` and never parse result as conceded; dual-lineup normalizer (flat list + positional dict + manager bucket + null/empty); fail loud if a keeper id is absent from the position map; skip-and-report missing lineups.
- **Option C:** add ~4 `HEADER_ALIASES` entries + 2 `_extract_cell_int` calls + 2 yield keys + 2 fallback col_map keys in `stats_spider.py`; update `test_stats_spider.py:21-34` col-index assertions in lockstep.
- **Cargo (prod):** additive nullable Integer columns; on prod `approveAllPages.php --username=Admin --force` → `cargoRecreateData.php --table=player_stats` → `runJobs.php`. New/keeper fields store NULL when absent, not 0.
- **Option B (if triggered):** anchor parser on ARIA `role=table`/`role=row` + header `title=`/`alt=` label text, never svelte-* hashes or positions; extract cells across `<a>` AND `<div>` by width-styled order (Appearances/competition are `<a>`); assert per-row cell-count == header-count; coerce `-`→0 for present columns but RAISE on absent expected column; concurrency cap in `tmk-scraper/settings.py`; never persist `response.url` (proxy URL leaks the key — thread `request.meta["target_url"]`).
- **Branching:** own feature branch off `master` (NOT `iter-cycle-walk/modern-era`). Gates: `cd data && uv run pytest -q` (525 baseline) + `uv run ruff check .`.

---

## 9. Reframe re-evaluation (operator: per-competition-for-HBS via Option A)

> **Added 2026-06-14.** The operator reframed the scope: **per-competition stats FOR HBS are Option-A-derivable**
> (partition match events by `match.competition`; NO `render=true`), and Option B now narrows to **all-clubs /
> full-career** data only (a player's stats OUTSIDE HBS). Directive: **ship everything feasible now**, and decide
> whether **PPG** is a real, valuable stat. This section re-synthesizes §1–§8 under that reframe, grounded in a
> fresh read-only re-verification of the 286-match corpus plus an adversarial refutation pass.

### 9.1 Executive summary — what the reframe changes

The reframe is **correct and stronger than the original brief feared**. The brief's two "CRITICAL" worries —
that **assists** and **cards** might be absent from match data — are both **FALSE**: every match carries a full
`cards[]` array (`player_tm_id` + card type + minute; 1499 yellow / 54 red / 40 second_yellow across 286/286
matches, 0 missing ids) and every `goals[]` entry carries `assist` + `assist_tm_id` (465/724 goals assisted).
`competition` is present on 100% of 286 matches (8 distinct values). So **per-competition-for-HBS is genuinely
derivable from match events**, with a clean field-by-field reliability split confirmed by reconciling
match-derived totals against the authoritative club-page season totals across all 197 player-seasons.

The honest feasibility outcome is a **three-tier field cut**, not a blanket yes:

- **Reconciles EXACTLY (197/197) → ship with a fail-loud gate:** goals, red_cards, second_yellow_cards.
- **Reconciles after a known, characterized rule / within |delta|<=1 → ship with a warn-and-report gate:**
  appearances (100% for 2019–2023; all 21 misses isolated to the 3 known missing-lineup 2024 matches),
  assists (195/197; 2 source-data off-by-1), yellow_cards (196/197 **only** after the load-bearing
  second-yellow subtraction rule — without it, 182/197).
- **Won't reconcile by equality → omit or label approximate:** minutes_played (clock-model exact only ~32%;
  −180..+41 delta range). Keep club-page season minutes as the headline; do **not** publish per-competition
  minutes as authoritative.

Net effect on the §1 recommendation: the **Hybrid still stands**, but Phase 2a **expands** from "keeper headline
only" to "keeper headline + per-competition-for-HBS breakdown (6 of 7 reconcilable fields) + subs on/off". Option B
shrinks to a genuinely-deferred all-clubs/career-only path. This is a **maximal ship now**, not a deferral.

### 9.2 What ships NOW (Option A, render-free)

1. **Keeper headline on player pages** — `clean_sheets` + `goals_conceded` as additive **nullable** columns on
   `player_stats`, season-total, written onto the existing one-row-per-player-season row (no reshape). Conditional
   keeper columns in `player_page.j2` reuse the existing predicate `'שוער' in pos or 'Goalkeeper' in pos`
   (line 147–148). Outfield/historical rows store NULL → render `-`.
2. **All-time "most clean sheets" leaderboard** — Python-only aggregation over the new keeper columns.
3. **Subs on/off** (Option C, free) — already-present columns on the SSR club page documented at
   `stats_spider.py:99–101` (idx 11/12). Additive nullable, NULL for historical rows.
4. **Per-competition-for-HBS breakdown table** — NEW separate Cargo table `player_competition_stats`, one row per
   `(player_id, season, competition)`, rendered as a new gated `player_page.j2` section. **Fields that make the
   cut:** `appearances`, `goals`, `assists`, `yellow_cards`, `second_yellow_cards`, `red_cards`, plus keeper
   `clean_sheets` / `goals_conceded`. **minutes_played: emit NULL** (or omit) — not reconcilable.
5. **own_goals BY HBS players** (15 total, all roster-attributable; re-verified this pass) — match-derived,
   net-new. Ship as a season-total nullable column AND a per-competition figure; it has NO club-page oracle so it
   is self-checked only (sum-of-comp == season), not gated.
6. **Integrity gate** as a read-only post-merge reconcile pass (tiered fail-loud/warn — see §9.8).

### 9.3 What genuinely defers to Option B (render=true)

- **All-clubs / full-career** stats — a player's apps/goals/etc. **outside HBS** (other clubs, national team).
  Structurally absent from our HBS-only match corpus.
- **TM-authoritative per-competition figures for non-HBS competitions** (e.g. a player's Bundesliga rows).
- **Any field with no match-event source** — there are none left for the per-competition-for-HBS cut; the only
  match-derived field we deliberately drop is **per-competition minutes** (kept as season-total club-page headline).
- Option B's prior precursors stand unchanged: zero-cost CDN-bundle static-analysis spike for the `tmapi`
  endpoint, network probe for historical-keeper availability, ≥1 outfield + ≥1 historical render fixture.

### 9.4 PPG verdict — points-per-game

PPG = **team points per appearance** (a 3/1/0 team-result average over the player's matches), present as a free
column on the SSR club page (`stats_spider.py:99–101`, idx 13). **Recommendation: EXCLUDE** from the default ship
(make it opt-in only). Reasoning: PPG is a **team-outcome** metric attributed to an individual — it conflates the
player with the squad's form and is heavily confounded by which matches a fringe player happens to feature in
(small-sample players get extreme values). It is **not a player-skill stat** and is non-additive (you cannot sum
or average it across seasons meaningfully without re-weighting by appearances). On a **fan wiki** it adds noise and
invites misreading ("keeper X has better PPG than striker Y") with negligible upside next to clean sheets, goals,
assists. It costs ~zero to grab if the operator insists, but it does not earn a column. **Exclude; revisit only as
an explicit, clearly-labeled team-form annotation if ever requested.**

### 9.5 Own goals verdict

**DERIVABLE — ship it.** Re-verified this pass: own-goals are flagged by the `'Own-goal'` substring inside
`goals[].details` (raw English, NOT Hebrew-translated even in `matches.he.json`). Corpus totals reconcile to the
adversarial tripwire exactly: **27 own-goal goal-rows total, 12 benefiting HBS** (opponent OG, stored
`team='Hapoel Beer Sheva'`), **15 BY HBS players** (`team=opponent` AND `scorer_tm_id` in the HBS roster:
2019=1, 2020=3, 2021=3, 2022=3, 2023=3, 2024=2). All 15 are roster-attributable. Two hard rules are load-bearing:
(a) **team attribution is inverted** — `goals[].team` is the BENEFITING team, so an HBS player's own-goal shows
`team=opponent`; (b) the own-goal exclusion must apply to **goals, assists, AND keeper goals_conceded** (3 own-goal
rows carry an `assist_tm_id`, 2 crediting HBS — without exclusion an HBS player is wrongly credited an assist on an
opponent's own-goal). Net-new, no club-page oracle → self-check only. **Defer to Option B ONLY if a TM-authoritative
own_goals number is later wanted**; the HBS-derived one ships now.

### 9.6 Per-competition verdict — final field cut

| Field | Cut? | Basis (197 player-seasons reconciled vs club-page totals) |
|---|---|---|
| `competition` (partition key) | **SHIP** | 100% present, 8 distinct values |
| `goals` | **SHIP (fail-loud gate)** | 197/197 EXACT (after own-goal exclusion) |
| `red_cards` | **SHIP (fail-loud gate)** | 197/197 EXACT |
| `second_yellow_cards` | **SHIP (fail-loud gate)** | 197/197 EXACT (literal value `second_yellow`, underscore) |
| `appearances` | **SHIP (warn gate)** | 100% 2019–2023; all 21 misses = the 3 missing-lineup 2024 matches |
| `assists` | **SHIP (warn, |delta|<=1)** | 195/197 (2 TM source-report off-by-1) |
| `yellow_cards` | **SHIP (warn, second-yellow rule)** | 196/197 with rule; 182/197 without — rule is mandatory |
| `clean_sheets` (keeper) | **SHIP (self-check only)** | net-new; no oracle; sum-of-comp == season; time-bound keeper subs |
| `goals_conceded` (keeper) | **SHIP (self-check only, FIXED formula)** | net-new; use result+venue minus HBS own-goals, NOT `count(team!='HBS')` |
| `own_goals` (by HBS) | **SHIP (self-check only)** | net-new; 15 attributable; English-substring tripwire |
| `subs_on` / `subs_off` | **SHIP (free, club page)** | additive nullable |
| `minutes_played` | **OMIT per-competition / NULL** | clock model ~32% exact, −180..+41 — not reconcilable by equality |
| `ppg` | **EXCLUDE** | team-outcome metric, non-additive, low fan value (§9.4) |

**League-merge policy:** league is **two** competition strings (`ליגת העל בכדורגל` regular = 156 +
`ליגת העל - שלב האליפות` championship round = 60). Cardinality is **8, not the intuitive 7**. Decision needed
(§9.9 Q-B); default recommendation is **keep both rows** (faithful to TM) with a UI note.

### 9.7 Adversarial corrections folded in (re-verified directly this pass)

- **`goals_conceded` formula `count(goals where team != 'Hapoel Beer Sheva')` is WRONG.** Re-verified on 2024 idx44
  (away 1:1 vs Maccabi TA): Eliasi (912586) own-goal at min33 is stored `team='Maccabi Tel Aviv'`, so the naive
  count yields conceded=2 vs true 1. **Use opponent goals from result+venue** (`opp = result[1] if venue=='H' else
  result[0]`), then subtract HBS own-goals that landed as `team=opponent`. Add a per-match guard asserting derived
  conceded == result-derived opp goals.
- **Invisible keeper substitution.** Re-verified on 2024 idx31 (home 5:1): Marciano (112008, keeper) comes IN at
  min72 with `player_out_tm_id=444018` — an **outfielder**, not the keeper Eliasi who started. The keeper change is
  **undetectable from `substitutions[]`**, so naive minute-gating mis-charges Eliasi for a later goal. Rule: detect
  keeper change by the keeper-id **set** present in BOTH lineup AND subs; where `player_out` is mis-recorded, flag
  the match as a named data-quality caveat rather than dual-crediting.
- **Three lineup shapes** (flat list / position-keyed dict `{goalkeeper,defenders,...,manager}` / `None`). A single
  `iter_lineup()` normalizer is mandatory: it must skip the `manager` entry (`tm_player_id=None`) and the None
  variant, and **list vs dict carry different name semantics** (list `name_english` holds Hebrew; dict
  `name_english` holds English). Most dict-shaped matches are State Cup → a non-normalizing iterator silently zeroes
  cup apps. (Already branched in `apply_hebrew_mapping.py:229–249`; factor out, don't re-implement.)
- **Gate only checks the SUM.** The club-page oracle has no per-competition dimension, so the gate validates
  sum-over-competitions == season total only; a bug that moves an appearance from cup→league while preserving the
  sum is **invisible**. Document this limit explicitly; do not over-claim per-row validation.
- **Fragile English literals.** Both HBS-goal detection (`team=='Hapoel Beer Sheva'`) and own-goal detection
  (`'Own-goal'` in `details`) are raw-English and survive Hebrew enrichment today. Add tripwires: assert the literal
  `'Hapoel Beer Sheva'` still appears (~450 goal rows) and own-goal rows == 27, so a future translation regression
  **fails loud** instead of silently returning zero/garbage.
- **Tiny-sample competitions** (Super Cup `אלוף האלופים` = 3 matches over 6 seasons; some European rounds 3–6) are
  statistically thin and individually unverifiable — render them but treat as informational.

### 9.8 Phase-2 implementation plan (ordered; NOT yet actioned)

1. **New module `data_pipeline/compute_competition_stats.py`** with `main(data_dir, seasons, scraper_output_dir)`.
   - Load `players.he.jsonl` → `id → main_position`; flag keepers via `'שוער' in main_position`.
   - Factor `iter_lineup(lineup)` handling list / position-keyed dict / None; skip `manager` + None ids; skip-and-
     **report** the 3 known missing-lineup 2024 matches (idx 11/14/32), never silent-zero.
   - Per match: `venue` H/A → HBS lineup = `home_lineup` if H else `away_lineup`; HBS sub-side token = `'home'`/
     `'away'` accordingly; `opp_goals = result[1] if venue=='H' else result[0]` (reuse/refactor
     `helpers.py hbs_match_outcome` into a shared `_hbs_opp_goals`).
   - Per `(player_id, competition)`: appearances (lineup ∪ subbed-in HBS-side), goals/assists from `goals[]`
     (`scorer_tm_id` / `assist_tm_id`, **own-goal-excluded**), cards from `cards[]` (apply second-yellow rule),
     subs from `substitutions[]`; keepers add clean_sheets/goals_conceded (result+venue − own-goals, minute-gated
     for keeper subs); own_goals BY HBS.
2. **Schema migration (additive, nullable):**
   - `player_stats` (import_templates.py L86–137) gains nullable Integer cols: `clean_sheets`, `goals_conceded`,
     `subs_on`, `subs_off`, `own_goals` (season totals; keeper-only ones NULL for outfielders). Same precedent as
     the players-table nullable block ("All nullable for historical players").
   - NEW table entry `"Template:Cargo/PlayerCompetitionStats" → "player_competition_stats"` in `CARGO_TABLES`
     (the dict is iterated generically by `_build_cargo_template`/`import_cargo_templates`, so +1 entry auto-emits
     declare/store templates). Fields: player_id, season, competition (String); appearances, goals, assists,
     yellow_cards, second_yellow_cards, red_cards, clean_sheets, goals_conceded, subs_on, subs_off, own_goals
     (Integer, keeper ones nullable); minutes_played NULL/omitted.
3. **Pipeline wiring (`run_pipeline.py`):** insert the compute step BETWEEN Step 4 (Hebrew enrichment, ends ~L952)
   and Step 5 (Import, ~L956) — the only valid point (needs `players.he.jsonl` positions + `matches.he.json`).
   Wire into BOTH multi-season (post-merge) AND single-season (`--season`) paths or it no-ops on season runs.
   Outputs: (a) `data_dir/competition_stats.jsonl` (new per-comp rows); (b) merge season-total keeper/own_goal/subs
   keys INTO existing `stats.jsonl` rows in place (adds keys only).
4. **Template changes (`player_page.j2`, inside bot-section markers):**
   - Add `{% set is_keeper = 'שוער' in (player.main_position or '') or 'Goalkeeper' in (player.main_position or '') %}`.
   - Existing stats section: conditional keeper `<th>/<td>` for goals_conceded + clean_sheets (`.get(...) | default('-')`),
     plus the `#cargo_store PlayerStats` block gains `| clean_sheets = …`, `| goals_conceded = …`, `| own_goals = …`,
     subs (empty string for outfielders → NULL). Keep goals/assists columns for keepers ("in addition, not instead").
   - NEW gated section `{% if competition_stats %} {{ bot_section_start('competition-stats') }} == סטטיסטיקה לפי מפעל ==`
     rendering a sortable wikitable grouped by season then competition + the per-comp `#cargo_store` loop under the
     mainspace-only `{{#ifeq:{{NAMESPACENUMBER}}|0|…}}` gate.
   - Register `'competition-stats'` in `KNOWN_TEMPLATE_SECTIONS['player_page.j2']` (wikitext_merger.py:212–218) or
     the `TestKnownTemplateSections` contract test fails.
   - `import_players._build_player_page` gains a `competition_stats` kwarg, filtered by player id (mirror how `stats`
     is filtered).
5. **Integrity gate** = final `reconcile()` pass in the compute module, run before writing outputs, WARN-logging:
   - **Fail-loud (abort)** ONLY for the 197/197 fields: goals, red_cards, second_yellow_cards.
   - **Warn-and-report** for appearances/assists/yellow/minutes with tolerance |delta|<=1 + the auto-detected
     missing-lineup match list; **denominator subtracts only genuinely-unrecoverable starters** (idx11's 11), NOT a
     blanket −3 (idx14/idx32 are partially event-recoverable → over-subtraction risk).
   - **Self-check only** for clean_sheets/goals_conceded/own_goals (sum-of-comp == season; CS <= keeper apps).
   - Tripwires: assert literal `'Hapoel Beer Sheva'` present (~450 rows) + own-goal rows == 27.
   - Summary line per data-loss-vigilance memory: "competition-stats: N players, M (player,season) pairs, K apps
     drift, 3 matches skipped (missing lineup)".
6. **Test matrix (per operational-bootstrap §9 + repo gates):** unit tests for `iter_lineup` (all 3 shapes +
   manager skip), own-goal exclusion (goals/assists/conceded), second-yellow rule, keeper-sub minute-gating
   (idx31 false-out, idx44 own-goal conceded), result+venue conceded; the reconcile gate's tiered behavior; the
   `TestKnownTemplateSections` contract; full `uv run pytest -q` (525 baseline) + `uv run ruff check .`.
   On prod: ApprovedRevs re-approval → `cargoRecreateData.php --table=player_stats` + `--table=player_competition_stats`
   → `runJobs.php`.
7. **Avoids double-count (verified):** every aggregator (`import_season_overview`, `import_squad_page`,
   `import_leaderboards`) reads ONLY season-total `stats.jsonl`; the new per-comp table is in its own file/table
   that no aggregator opens. Keeper headline cols live on the one season-total row → cannot double-count.
8. **Branching:** feature branch off `master` (NOT `iter-cycle-walk/modern-era`).

### 9.9 Remaining decisions for operator sign-off

- **Q-A (PPG):** include / exclude / opt-in. **Default: EXCLUDE** (§9.4) — not a player-skill stat; non-additive;
  low fan value.
- **Q-B (league split):** keep both league rows (8 comps) / merge into one "league" row (~7). **Default: keep both**
  (faithful to TM) with a UI note that league = regular + championship round.
- **Q-C (keeper season-total source):** match-derived (HBS-only, may undercount apps by competition-coverage drift,
  e.g. 912586 2023 31 vs 32) / strictly club number. **Default: match-derive clean_sheets/goals_conceded/own_goals**
  (the club page has no such column anyway) but keep apps/goals/etc. headline = club number.
- **Q-D (minutes per-competition):** NULL / omit column / publish-as-approximate. **Default: NULL** the per-comp
  minutes; keep club-page season minutes as the only minutes headline.
- **Q-E (competition code/tier):** ship Hebrew display label only / also emit a stable `competition_code`/`tier`.
  **Default: label only now**; add a code column later if a "league vs Europe" leaderboard is wanted (the European
  set already exists at import_templates.py ~L1059–1079).
- **Q-F (Option B trigger):** still deferred; confirm it is reserved strictly for all-clubs/career data and that a
  future render=true write must NEVER land in the same HBS `stats.jsonl` (would make the gate reconcile against a
  superset and false-fail).

---

## 11. Phase 2 — implemented & verified (2026-06-14)

Shipped on branch `keeper-stats/per-competition-derive` (off `master`), entirely match-derived (zero ScraperAPI credits):

- **`data/data_pipeline/compute_competition_stats.py`** — pipeline Step 4.5 (between Hebrew enrichment and import). Derives per-(player, season, competition) appearances/goals/assists/cards + keeper clean-sheets/goals-conceded + own-goals, and per-season subs-on/off + PPG. Writes `competition_stats.jsonl` and augments `stats.jsonl` in place.
- **Schema** (`import_templates.py`): additive nullable columns on `player_stats` + separate `Template:Cargo/PlayerCompetitionStats` table.
- **Template** (`player_page.j2`): conditional keeper columns (goals/assists kept), subs/PPG, conditional own-goals column, new `competition-stats` section.
- **Tests**: 41 new (compute edge cases + table well-formedness + section contract); suite 565 green, ruff clean.

**Verified correctness rules** (each regression-tested): scoreline-authoritative goals-conceded (robust to duplicated `goals[]` rows); own-goal direction (`team` is the benefiting side); `yellow = count('yellow') − count('second_yellow')`; penalty-shootout result → `goals[]` fallback + PPG forced to draw; keeper metrics only for a starter or genuine keeper↔keeper swap; (minute, extra_time) **tuple** window comparison (halftime-boundary safe); league regular+championship merged. Integrity gate: fail-loud on goals/red/2Y (197/197 exact vs club totals), warn on appearances/assists/yellow (±1; the 3 missing-2024-lineup matches are the known gap), self-check on keeper fields; roster-gated to `players.he.jsonl`; reconcile filtered to seasons with match data; empty corpus short-circuits.

**Local-wiki verification:** re-imported templates + 128 players to local docker, ran §9 Cargo recipe; ניב אליאסי renders both sections (2023 12 CS / 31 GC / 1.84 PPG, 2024 13 CS / 33 GC / 2.19 PPG); `cargo__player_stats` + `cargo__player_competition_stats` populated for mainspace players (drafts gated as designed).

**Operator decisions applied:** PPG included (match-derived, season-only); league rounds merged.

**Per-competition minutes (follow-up, 2026-06-15):** added — computed via an AET-aware clock model (full=90', AET=120', sub-window-adjusted). Logic validated exactly against a scraped TM render (25/26 detailed grid); values validated vs the authoritative club-page season minutes (median drift 1', 88% within 45'; the ~23 drifters are the missing-lineup/red-card edge cases, reported not fail-loud). Eliasi 2023 sums to club exactly. Seasonal minutes were already present (club-page). Scrape findings: TM's season-filtered render is unreliable (returns no Svelte grid) so historical per-comp can't be scraped; the SSR club page carries authoritative PPG/subs/minutes (validated: subs exact, PPG within 0.04) — optionally source season PPG from it for exactness.

**Schema-migration gotcha discovered:** `approveAllPages --force` does NOT refresh `page_props` for a *changed* Cargo template, so `cargoRecreateData` recreates with the stale schema — a column ADD needs a forced re-parse (`api purge&forcelinkupdate=1`) of the template BEFORE `cargoRecreateData`. New tables are unaffected. (Recorded in memory `wiki7-keeper-per-competition-stats`.)
