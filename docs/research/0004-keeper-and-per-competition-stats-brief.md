# Ultracode brief — goalkeeper stats + the per-player TM scrape gap

> **How to use this doc:** This is a self-contained brief for an **Ultracode (multi-agent
> orchestration)** session. Paste it as the task, or point the session at this file
> (`docs/research/0004-keeper-and-per-competition-stats-brief.md`). It captures a finding from
> the `iter-cycle-walk/modern-era` review walk, the full investigation already done, the
> trade-offs to weigh, and the quality bar for implementation. **Do the research/verify +
> question phase FIRST; implement only after the operator confirms a direction.**

---

## 0. The mandate (read first)

This is a **two-phase** job. Optimize for a *perfect* result, not for tokens or time.

**Phase 1 — Research, verify, and decide (no implementation yet).**
Independently re-verify every claim in this brief (don't trust it blindly — the investigation
was time-boxed and some facts are single-sample). Map the full blast radius. Produce a clear
trade-off analysis covering: what a full `render=true` per-player scrape gains us, the gaps in
the current scraping process, and the downstream *potential* (what new wiki features the richer
data unlocks). Then **ask the operator the open questions** in §7 and get an explicit decision
on scope + approach before writing a line of production code.

**Phase 2 — Implement (only after confirmation).**
Full implementation of the confirmed scope with: complete regression test coverage (unit +
fixture-based + template-contract), future-proofing against TM markup drift, schema-migration
safety, data-loss vigilance, and adherence to every project convention referenced below. No
shortcuts.

This is the kind of work that benefits from a workflow: parallel readers to map the pipeline,
a judge panel on the approach (compute-vs-scrape-vs-hybrid), adversarial verification of the
scrape parser against real TM HTML, and a completeness critic before sign-off.

---

## 1. The trigger

During the modern-era review walk, the operator opened the goalkeeper **Niv Eliasi**
(`http://localhost:8080/ניב אליאסי`, TM player id **912586**, position `שוער`/Goalkeeper) and
noticed his **seasonal stats table shows only outfield columns** — Appearances, Goals, Assists,
cards, Minutes. For a keeper, `Goals = 0` / `Assists = 0` are technically correct but useless;
the *informative* keeper metrics — **clean sheets** and **goals conceded** — are absent.

Operator's request, verbatim intent:
1. If a player is a goalkeeper, also scrape + store keeper stats (clean sheets, goals conceded, …).
2. Show those stats for keepers in the wiki — and determine whether TM shows them *in addition
   to* or *instead of* goals/assists.
3. Hypothesis to validate: these stats might also exist for **defenders**.

---

## 2. What the investigation already established (verify, don't assume)

### 2.1 Where today's stats come from
- The pipeline scrapes **one club-level page per season**:
  `https://www.transfermarkt.com/hapoel-beer-sheva/leistungsdaten/verein/2976/plus/1?saison_id=<season>`
- Spider: `data/tmk-scraper/tmk_scraper/spiders/stats_spider.py`. It parses a server-rendered
  `table.items`, builds a column map from header `title=` attrs (`HEADER_ALIASES`), and yields
  **7 fields**: `appearances, goals, assists, yellow_cards, second_yellow_cards, red_cards,
  minutes_played`.
- This page is **SSR** (plain HTML, no JS needed). Confirmed columns (from fixture
  `data/tests/fixtures/leistungsdaten_sample.html`): Appearances, Goals, Assists, Yellow,
  Second yellow, Red, **Substitutions on**, **Substitutions off**, Minutes played.
- **The club page has NO keeper columns for anyone** (no clean sheets / goals conceded). Verified
  against the 2015 fixture too.

### 2.2 Where the keeper stats actually live — and the catch
- Keeper columns live on the **per-player** performance page:
  `https://www.transfermarkt.com/<slug>/leistungsdaten/spieler/<id>/plus/1`
  (e.g. `.../niv-eliasi/leistungsdaten/spieler/912586/plus/1`).
- **This page is client-side (JS) rendered.** Probed via ScraperAPI:
  - `render=false` → 95 KB, **zero `<table>` tags** — just a `<tm-player-performance>` Svelte
    component + a `window.__…` JS data blob. Keeper columns absent.
  - `render=true` (ScraperAPI headless browser) → 308 KB, **"Goals conceded" + "Clean sheets"
    present**, but rendered as a **Svelte grid with SVG-icon headers and hashed class names**
    (`svelte-1rkjw9x`), NOT the stable `table.items` markup every existing spider relies on.
  - A saved copy of the render=true HTML was left at `/tmp/tm_keeper_render.html` (ephemeral —
    re-fetch with the recipe in §6 if gone).
- `render` is ScraperAPI's flag to execute page JS in a headless browser before returning HTML.
  The project's `data/tmk-scraper/tmk_scraper/scraperapi_proxy.py::wrap(..., render: bool=False)`
  already exposes it, but **no spider currently uses it** — adopting it here would be the
  pipeline's first JS-render dependency. Cost: ~**10–25 ScraperAPI credits per render request**
  vs ~1 for a plain fetch; also much slower; also brittle (hashed Svelte classes shift on TM
  redeploys). See [[wiki7-scraperapi-baseline]] memory for the credit budget (Hobby tier,
  ~7,893 credits = one all-time run; ~12 runs/month).

### 2.3 Full column inventory of the per-player page (from the render=true probe)
| Column | On club page we scrape? | We store it today? |
|---|---|---|
| Appearances, Goals, Assists, Yellow/2Y/Red, Minutes | ✅ | ✅ |
| **Substitutions on / off** | ✅ (in the table) | ❌ not extracted |
| **Own goals** | ❌ | ❌ |
| **Goals conceded** (keeper) | ❌ | ❌ |
| **Clean sheets** (keeper) | ❌ | ❌ |
| **Per-competition split** (league/cup/Europe…) | ❌ (club page = one season total) | ❌ |

- The per-player page breaks each season down **by competition** — 13 competition codes seen for
  Niv Eliasi (`ISR1` Israeli league, `ISRF` State Cup, `ISPO` Super Cup, `CL` Champions League,
  `ELQ`/`ECLQ` Europa/Conference qualifying, plus `GB1`/`IT1`/`ES1` from his career elsewhere).
- **What's NOT available**: probed for and found **no Saves, no Save %, no Penalties saved, no
  Minutes-per-goal, no PPG**. TM's keeper data here is *only* Goals conceded + Clean sheets.

### 2.4 Answers to the operator's questions (validate)
- **In addition, not instead.** Keepers keep all standard columns AND gain the 2 keeper columns.
- **Defenders: NOT on TM.** Clean sheets / goals conceded are goalkeeper-only columns. A
  defender "clean sheets while on pitch" would have to be **computed** (see §3 option A), not
  scraped.

---

## 3. The three candidate approaches (the core trade-off to analyze)

### Option A — Compute from our OWN match data (no TM scrape)
We already scrape full match reports. Each record (`data/tmk-scraper/output/<season>/matches.he.json`)
carries everything needed:
- `home_lineup` / `away_lineup`: players with `tm_player_id`, `number`, `captain`.
- `goals`: each with `minute`, `score`, `team` (`Hapoel Beer Sheva` vs opponent), scorer ids.
- `substitutions`: `player_in_tm_id`, `player_out_tm_id`, `minute` — covers a keeper subbed off
  (red card/injury) or a sub keeper coming on.
- Plus `result`, `halftime_score`, `aet`, `competition`, `season`, `date`, `opponent`.

Compute: for each HBS keeper, `goals_conceded` = opponent goals in matches they were on the
pitch; `clean_sheet` = a match they played where the opponent scored 0.
- **Pros:** zero new scrape, zero render credits, robust (no JS/Svelte brittleness), and
  **HBS-specific** ("clean sheets *for HBS*"), which is arguably *more* relevant for a fan wiki
  than TM's all-clubs number.
- **Cons:** covers only HBS matches we hold (not the keeper's loans/other clubs); it's *our*
  computed number, not TM's authoritative one; edge cases to get right (which lineup is HBS —
  there's no `is_home` flag, infer from `venue`/`goals.team`; keeper subbed on/off mid-match;
  own-goals attribution; abandoned/AET matches).

### Option B — Scrape TM per-player page with `render=true`
Add a keeper-only scrape step that fetches each keeper's per-player page (render=true) and
parses the Svelte grid for goals conceded + clean sheets (+ optionally own goals +
per-competition split).
- **Pros:** TM-authoritative; all competitions and all clubs; unlocks own goals + the
  per-competition dimension (see §5).
- **Cons:** ~10–25 credits/keeper/season; a brittle parser (hashed Svelte classes, SVG-icon
  headers) that is the pipeline's first JS-render dependency and will need a markup-drift guard;
  per-season historical coverage must be verified (does TM have keeper data for 2019–2024 HBS
  keepers? probe before committing).

### Option C — Cheap independent win (orthogonal to A/B)
`Substitutions on` / `Substitutions off` are **already on the club page** the existing spider
fetches — the spider just doesn't map them in `HEADER_ALIASES`. Extracting + storing them needs
**no render, no new page** — a small extension of `stats_spider.py` + schema. Decide whether
that's in scope.

> **Hybrid worth considering:** Option A for the keeper headline numbers (robust, free) +
> Option B *only if* the operator wants TM-authoritative all-competition figures or the
> per-competition breakdown. The two are not mutually exclusive.

---

## 4. Gaps in the current scraping process (the broader picture the operator asked for)
1. **No JS-render capability in use.** `wrap()` supports `render=true` but no spider uses it; any
   TM data that is client-rendered is currently unreachable.
2. **We discard data we already fetch** — subs on/off are on the club page, dropped.
3. **Season totals only, no per-competition granularity** — we can't distinguish league vs Europe.
4. **No own goals, no keeper metrics.**
5. **Current-state drift** — player profile fields (`current_squad`, market value, etc.) reflect
   TM's "now" at scrape time (see the "Player-page data-freshness model" item in
   `docs/phase-3b-backlog.md`). Adjacent concern; flag if the design touches it.
6. **Prior abandoned attempt** — the memory [[wiki7-tm-career-stats-investigation]] records that
   per-club apps/goals (career "Finding #4") was deferred because the leistungsdaten detail is
   JS-rendered and 7 guessed `ceapi` JSON endpoints 404'd. **Discovering TM's internal XHR/JSON
   endpoint** (via browser-devtools network inspection of the `tm-player-performance` component)
   is a viable Phase-1 research thread — a JSON endpoint would be far cheaper and more robust
   than render=true HTML scraping if it exists and is reachable through ScraperAPI.

---

## 5. The potential — what the richer data unlocks (for the trade-off's "upside" column)
If we land per-player + per-competition scraping (Option B's full form):
- **Goalkeeper pages** with clean sheets / goals conceded / goals-against per season.
- **Per-competition stats tables** on player pages (league vs State Cup vs Europe).
- **European-campaign player stats** — the existing `european_campaign.j2` page could show
  per-player Europe stats instead of only team-level.
- **Competition-filtered leaderboards** (top scorer *in the league* vs *in Europe*; most clean
  sheets) via Cargo — `import_leaderboards` already aggregates `player_stats`.
- **Career per-competition breakdowns** and richer `#cargo_query` consumers (the open 3b item).
Quantify which of these the operator actually wants — the per-competition split is a schema
change (likely a new Cargo row shape: one row per player/season/**competition**), so its value
must justify the modelling cost.

---

## 6. Reproduction recipe (probe TM without a full run)
```python
# from data/, with SCRAPERAPI_KEY in env (it is set)
import os, sys, urllib.request
sys.path.insert(0, 'tmk-scraper')
from tmk_scraper.scraperapi_proxy import wrap, validate_key
key = validate_key(os.environ['SCRAPERAPI_KEY'])
target = "https://www.transfermarkt.com/niv-eliasi/leistungsdaten/spieler/912586/plus/1"
html = urllib.request.urlopen(
    wrap(target, key, urlencode_target=True, render=True), timeout=190
).read().decode('utf-8', 'ignore')   # render=True ≈ 10-25 credits; render=False ≈ 1 (but no table)
```
Other keeper samples in the corpus: `data/data_pipeline/output/merged/players.he.jsonl` filtered
on `main_position` containing `שוער` (e.g. the per-foot probe earlier surfaced ids; reuse it).

---

## 7. Open questions for the operator (ask BEFORE implementing)
1. **Source of truth:** Option A (compute from our matches), Option B (TM render=true scrape),
   or a hybrid? (Investigation lead recommends evaluating the hybrid; the operator leaned toward
   "scrape TM" originally but had not seen the brittleness/credit reality.)
2. **Scope of new fields:** keeper-only (clean sheets, goals conceded)? + own goals? + subs on/off
   (the free Option C)? + the per-competition breakdown (schema change)?
3. **Historical coverage:** which seasons must be backfilled, and is the credit cost acceptable?
   (Full `--force-rescrape` ≈ 700 credits/season; a keeper-only render add ≈ 10–25/keeper/season.)
4. **Display:** show keeper columns only when `main_position` is a goalkeeper (conditional
   columns in the seasonal-stats table)? Keep goals/assists for keepers or hide them?
5. **Per-competition:** if in scope, model as new Cargo rows (player×season×competition) and a
   per-competition table on player pages? Or keep season totals and only *add* keeper columns?
6. **ceapi JSON endpoint hunt:** worth a research spike (could obviate render=true entirely)?

---

## 8. Implementation touch-points (for Phase-2 planning; verify each)
- **Scrape:** `data/tmk-scraper/tmk_scraper/spiders/stats_spider.py` (extend or add a sibling
  keeper/per-player spider); `scraperapi_proxy.py` (render path). Mind the
  **concurrency→HTTP 429** gotcha — [[wiki7-translation-strategy]] notes `_MAX_WORKERS = 2` is
  load-bearing for TM/Wikidata; render requests are heavier, so cap concurrency.
- **Normalize:** `data/data_pipeline/normalize_enrich_players.py` (stats normalization path);
  the raw→`stats.jsonl` step run by `data/run_pipeline.py` (stages list, `CRITICAL_SPIDERS`).
- **Merge:** multi-season stats merge (see `data/data_pipeline/` merge logic + `tests/test_merge.py`).
- **Cargo schema:** `data/wiki_import/import_templates.py` → `Template:Cargo/PlayerStats` (add
  **nullable** columns so existing/outfield rows still validate). NS_TEMPLATE is ApprovedRevs-
  gated — schema changes need template re-approval + `cargoRecreateData.php` (recipe in
  `docs/operational-bootstrap.md` §9).
- **Templates:** `data/wiki_import/templates/player_page.j2` (seasonal-stats section, ~lines
  83–116, incl. the `#cargo_store` PlayerStats block and the totals row that uses
  `map(attribute=…)|map('default',0)|sum`); `data/wiki_import/mediawiki_templates/Player_infobox.wikitext`
  if any infobox surfacing is wanted.
- **Leaderboards:** `import_leaderboards` in `import_templates.py` (if competition/keeper boards).

## 9. Quality bar + conventions to honor (non-negotiable in Phase 2)
- **Tests (mirror the walk's matrix):** Python boundary tests for the scraper/normalizer/compute
  logic; **fixture-based** spider tests (add a real captured HTML fixture under
  `data/tests/fixtures/` — for render output, save a representative Svelte-grid sample);
  template-contract tests in `tests/test_template_section_contract.py` and wikitext-shape tests
  in `tests/test_wiki_import.py` / `tests/test_wikitext_merger.py`. Every test must exercise the
  *specific* edge case (keeper subbed off; opponent own-goal; AET/penalty match; competition
  with no keeper data; a season where TM lacks keeper stats). Confirm each new test **fails
  against pre-fix code**.
- **Future-proofing:** a markup-drift guard for the Svelte parser (fail loud, not silent-zero, if
  the keeper columns can't be located — silent zeros are a data-loss bug). Per
  [[wiki7-data-loss-vigilance]], after any run audit each external surface and **report counts**;
  data integrity > throughput.
- **Fail-soft on unknown shapes** (cf. `hbs_match_outcome` falling through to `""`), but
  **fail-loud on "expected data missing"** during scraping. Don't conflate the two.
- **Don't fix rendered wiki output — fix the source** (`mappings.he.yaml` / `*.j2` / spider). See
  the "Helder-Lopes trap" in `docs/iter-cycle-review-guide.md`.
- **Secrets:** never persist proxy URLs containing the API key (`response.url` is the proxy URL
  when proxied — thread `request.meta["target_url"]`, resolve relatives against `self.base_url`).
  See [[wiki7-reviewer-pass-lessons]].
- **Credits:** confirm the exact cost with the operator before any `--force-rescrape` or bulk
  render run.
- **Branching:** do this on its **own** feature branch off `master` (NOT on
  `iter-cycle-walk/modern-era`, which is the unrelated review-walk PR). Conventional commits, no
  AI attribution in commits/PRs.
- **Suites that must stay green:** `cd data && uv run pytest -q` (currently 525) + `uv run ruff
  check .`; `cd cdk && npm test` if CDK touched (it shouldn't be).

## 10. Reference index
- **Spiders/proxy:** `data/tmk-scraper/tmk_scraper/spiders/stats_spider.py`,
  `data/tmk-scraper/tmk_scraper/spiders/player_spider.py`,
  `data/tmk-scraper/tmk_scraper/scraperapi_proxy.py`,
  `data/tmk-scraper/tmk_scraper/settings.py` (`SCRAPERAPI_KEY`).
- **Pipeline:** `data/run_pipeline.py`, `data/data_pipeline/normalize_enrich_players.py`.
- **Templates/schema:** `data/wiki_import/import_templates.py` (`Template:Cargo/PlayerStats`,
  `import_leaderboards`), `data/wiki_import/templates/player_page.j2`,
  `data/wiki_import/mediawiki_templates/Player_infobox.wikitext`.
- **Data:** `data/tmk-scraper/output/<season>/matches.he.json` (match reports),
  `data/data_pipeline/output/merged/players.he.jsonl` (players w/ positions),
  `data/data_pipeline/output/<season>/stats.jsonl`.
- **Fixtures:** `data/tests/fixtures/leistungsdaten_sample.html` (club page; no keeper cols).
- **Docs:** `docs/research/0002-transfermarkt-data-surface.md` (TM data surface),
  `docs/phase-3b-backlog.md` (TM career-stats / per-competition / data-freshness items),
  `docs/operational-bootstrap.md` (pipeline + Cargo recipe §8–9),
  `docs/iter-cycle-review-guide.md` (Helder-Lopes trap), `docs/architecture.md`.
- **Memories** (`~/.claude/projects/-Users-tzahi-argaman-dev-argamanza-wiki7/memory/`):
  `wiki7-tm-career-stats-investigation.md` (JS-render note + ceapi 404s),
  `wiki7-scraperapi-baseline.md` (credit budget), `wiki7-data-loss-vigilance.md`,
  `wiki7-reviewer-pass-lessons.md` (secrets/correctness traps),
  `wiki7-translation-strategy.md` (`_MAX_WORKERS=2` 429 gotcha),
  `wiki7-post-pipeline-automation.md` (Cargo recreate recipe).
