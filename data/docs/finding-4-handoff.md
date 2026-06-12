# Finding #4 — Agent Handoff Brief

> **Status:** Active handoff. The previous agent shipped Findings #1, #2, #3
> in this branch (`phase-3a-r2/implementation`) and PAUSED Finding #4 after
> hitting a wall on data extraction. You are picking up #4 cold.
>
> **Read this file in full before running anything.** The next-actions
> checklist at the bottom assumes you've read the constraints.

---

## 1. Mission

Two scoped deliverables, both pushed back to **this branch
(`phase-3a-r2/implementation`)** when done:

1. **Finding #4 — per-club career stats (apps/goals across every club a
   player has played for).** Currently shows only HBS stats. Wikipedia
   infobox convention shows e.g. "Benfica B 2008–2010 · 47 apps · 12 goals
   · Benfica 2010–2014 · 89 apps · 8 goals · Lazio 2014–2018 …" — that
   per-club rollup is what we want to surface on each player page.

2. **Multi-season validation pass (≥5 seasons) for Findings #1, #2, #3.**
   The previous agent validated only against 2024/25 local data. There
   are almost certainly cross-season edge cases that need regex tweaks
   in `is_youth_club_name`, the Wikidata variant generator, and the
   `(כדורגל)` paren stripper. **Recommended span: saison_id 2019–2024
   (6 seasons)**; expand to 2014–2018 (older Russian-era TM data) if the
   2019–2024 pass surfaces edge cases that need older formats to
   trigger. See §6 for details.

When you finish, **push to this branch** and append a "Handoff Back" section
to the bottom of this file describing what you did, what you found, what
remains. The next agent picks up from there.

---

## 2. Branch + commit rules (NON-NEGOTIABLE)

- **Branch:** `phase-3a-r2/implementation`. Stay on it. Do NOT merge to
  `master`. Do NOT push to prod.
- **Commit author:** the user is `Tzahi Argaman <targaman@paloaltonetworks.com>`.
- **NO AI co-author lines.** Never include `Co-Authored-By: Claude`,
  `Generated with Claude Code`, `Made with Cursor`, or any AI tool
  attribution. Commits and PR descriptions must read as if written by a
  human engineer. This is a hard rule from the user's global CLAUDE.md.
- **No `--no-verify`, no `--amend`.** Create NEW commits even after hook
  failures. Investigate failures, don't bypass them.
- **Push to remote** (`origin/phase-3a-r2/implementation`) at the end so
  the originating agent can pull and continue.
- **Don't touch `master` or prod infra.** Local docker + local pipeline
  only. Production wiki (https://wiki7.co.il) stays untouched.

---

## 3. Project context (skim if you know wiki7)

**wiki7** is a Hebrew-language Hapoel Beer Sheva fan wiki built on
MediaWiki 1.45.3 + a custom Wiki7 skin (rename-fork of Citizen 3.17) +
Cargo + ApprovedRevs + Lockdown + Wiki7ReviewGate (in-repo extension).
Production runs on a single Graviton EC2 + RDS (~$50/mo).

A **Python data pipeline** scrapes Transfermarkt (Scrapy + ScraperAPI for
TM, free Wikidata REST API for translations, Claude for fallback
transliteration) and imports via mwclient. The pipeline lives in
`wiki7/data/`.

**Current effort: iteration cycle 1 (2024/25 season).** Code shipped, local
docker running, content walkthrough underway with the user. The user just
finished a review pass that surfaced 4 findings; the previous agent shipped
#1–#3 and paused on #4.

**Key terminology:**
- "Iteration cycle" = one full pipeline run for a season's data.
- "Walkthrough" / "walk" = user manually reviews rendered pages and
  surfaces findings.
- "saison_id" = TM's season-year parameter (2024 means 2024/25 season).
- "Wikidata-first chain" = the Hebrew translation cascade
  (`wikidata_lookup` → `wikipedia_lookup` → Claude → phonetic).
- "Cargo" = MediaWiki extension storing structured data in MySQL for
  cross-page queries; namespace-gated so drafts (NS=3000) don't pollute it.

---

## 4. The 4 findings — context for #4

The user's iteration-cycle 1 review walk surfaced 4 issues. The first
three are done and unit-tested but **not yet multi-season-validated**:

| # | Finding | Status | Where |
|---|---|---|---|
| 1 | Wikidata coverage gaps | shipped (this branch, unit-tested) | `data_pipeline/wikidata_lookup.py` — added `Q103229495` to club P31 set + `_search_variants()` for spacing fallback ("1.FC Nuremberg" → "1. FC Nuremberg") |
| 2 | Strip `(כדורגל)` paren suffix | shipped (this branch, unit-tested) | `data_pipeline/wikidata_lookup.py::_clean_he_label()` — strips only football-disambiguation parens, keeps city/year/`(נשים)` standalone |
| 3 | Youth career section | shipped (this branch, unit-tested) | `data_pipeline/helpers.py::is_youth_club_name()` matches English (U17, Yth., Sub-15) AND Hebrew (תחת N, נוער); template splits at `wiki_import/templates/player_page.j2` |
| 4 | **Per-club apps/goals career stats** | **PAUSED — your work** | (none yet) |

**Why #4 was paused:** the previous agent probed TM's per-player
career-stats page and found it's NOT in the server-rendered HTML. Details
in §5.

---

## 5. Finding #4 — what's known + what failed

### What the feature should look like

For each player page, add a section (or table within the existing career
section) showing per-club appearance + goal totals across the player's
entire career. Example from a Wikipedia infobox:

```
Senior career:
  2008–2010   Benfica B           47 apps · 12 goals
  2010–2014   Benfica             89 apps ·  8 goals
  2014–2018   Lazio               65 apps ·  2 goals
  2018–2024   Hapoel Beer Sheva   142 apps · 9 goals
```

The HBS-tenure stats are already on the player page (via the existing
`stats_spider` which scrapes the HBS team's `leistungsdaten/verein/2976`
page). What's MISSING is per-club totals for the player's OTHER tenures.

### The probe finding (2026-06-12)

The previous agent assumed TM's per-player career-stats page would have
the data in server-rendered HTML. **It does not.**

Endpoints probed (with a real browser User-Agent + Accept headers):

| URL | bytes | `<table>` tags | `class="items"` | hauptlink count |
|---|---|---|---|---|
| `/<slug>/profil/spieler/<id>` | 120 KB | **0** | 0 | 0 |
| `/<slug>/leistungsdaten/spieler/<id>/saison/ges` (career stats) | 101 KB | **0** | 0 | 0 |
| `/<slug>/leistungsdatendetails/spieler/<id>/plus/0?saison=ges` (detailed) | 102 KB | **0** | 0 | 0 |

Note: The pages return HTTP 200 with substantial content (~100KB), the
`<title>` is correct ("Miguel Vítor — Career stats"), the keywords meta
tag mentions Hapoel Beer Sheva + Ligat ha'Al — but the actual stats
TABLE is rendered client-side via JavaScript.

The `<main>` element is only ~17 KB total and contains only the modal
overlays + page header + agent contact box — none of the career data.

### `ceapi` JSON endpoint hypothesis — DISPROVEN by guess

TM exposes some clean JSON via `ceapi/*` URLs:
- ✅ `ceapi/marketValueDevelopment/graph/{id}` — market-value chart data
- ✅ `ceapi/transferHistory/list/{id}` — transfer history with from/to/season/fee

Both are used in `tmk-scraper/tmk_scraper/spiders/player_spider.py`. The
hypothesis was that a similar endpoint exists for career stats. 7
guessed paths ALL returned 404:

```
ceapi/career/list/{id}
ceapi/playerCareer/list/{id}
ceapi/leistungsdaten/{id}
ceapi/playerStats/list/{id}
ceapi/performanceData/{id}
ceapi/spielerLeistungsdaten/{id}
ceapi/karriere/{id}
```

**The hypothesis isn't disproven — the guessed paths just don't match.
The right step is browser DevTools observation, not more guessing.**

---

## 6. Your work plan for #4

### Step 1 — Browser-XHR discovery (PREFERRED PATH — free)

Open a real browser (Chrome/Firefox), enable DevTools → Network tab,
filter to XHR/Fetch, and load:

```
https://www.transfermarkt.com/miguel-vitor/leistungsdaten/spieler/57814/saison/ges
```

Watch for XHR/Fetch requests that return JSON containing club names,
appearances, goals. Common patterns to look for:
- `ceapi/...` (the existing convention)
- `apipro.transfermarkt.com/...`
- `gql.transfermarkt.com/...` (if GraphQL)
- Any URL with `Authorization` or `X-Tm-API-Key` header — if so, capture
  the header rotation pattern (cookie-based? token in initial HTML?)

**Sample request via curl to confirm the endpoint works without browser
session.** If it needs a cookie/token, look in the initial HTML for a
meta tag, script tag, or embedded JSON containing it.

**Sample IDs that should have career stats (foreign players with multi-
club history):**

| Player | TM ID | Why this one |
|---|---|---|
| Miguel Vítor | `57814` | Portuguese, 6 clubs incl. Benfica + Lazio |
| Iuri Medeiros | `298229` | Portuguese, ~10 clubs |
| Antonio Sefer | `565849` | Romanian, multi-club |
| Yoan Stoyanov | `848641` | Bulgarian + Israeli, multi-club |

(Read these from `data_pipeline/output/2024/players.he.jsonl` if you
need others.)

**If you find the endpoint:**
1. Mirror the `parse_market_value` / `parse_transfer_history` pattern in
   `tmk-scraper/tmk_scraper/spiders/player_spider.py`. Add a third
   parse stage that fetches career stats.
2. The spider emits one dict per player to its output. Add a
   `career_stats` field shaped like:
   ```python
   career_stats: list[dict] = [
       {"club": "Lazio", "seasons": "2014–2018", "appearances": 65, "goals": 2},
       {"club": "Benfica", "seasons": "2010–2014", "appearances": 89, "goals": 8},
       ...
   ]
   ```
3. Pipe through `normalize_enrich_players.py` (preserves the field) and
   `apply_hebrew_mapping.py` (translates the `club` names — use the
   existing `_translate_club` helper).
4. Render in `wiki_import/templates/player_page.j2` — likely as a new
   section under `== קריירה ==` or as a column-extended version of the
   existing stats table.

### Step 2 — Fallback if no free endpoint exists (AUTHORIZED — paid)

The user explicitly authorized using ScraperAPI `render=true` mode if
needed.

**Cost reality:** roughly **10× credits per request** (`render=true`
runs a headless browser). For ~30 squad players per run, that's roughly
**+300 credits per pipeline run** vs. current ~600 credits/run total.
Within the Hobby tier's 100k/month budget.

**To switch the player profile request to render=true:**
- `tmk-scraper/tmk_scraper/spiders/player_spider.py` line 28-30: the
  `f"...&render=false"` literal. Switch to `&render=true` ONLY for the
  career-stats fetch — keep the existing market-value + transfer-history
  ceapi fetches at `render=false` (they're already JSON-clean).
- After enabling render, parse the now-server-rendered `<table>` for
  club / apps / goals. The DevTools step above will also tell you the
  exact CSS selectors to use.

**Save the change behind a settings flag** so the cost is opt-in:

```python
# settings.py
USE_SCRAPERAPI_RENDER_FOR_CAREER_STATS = True
```

Default it to `True` since the user authorized it, but flag it so a
future iteration can toggle off cleanly.

### Step 3 — Fallback to partial coverage if BOTH above fail

If the endpoint resists discovery AND render=true doesn't give clean
output, ship a partial solution:

- **HBS-tenure stats**: already in `stats.jsonl` from `stats_spider`.
- **Non-HBS tenures**: render as transfers-only rows with `—` for apps
  and goals.

This is suboptimal (it just leaves the column blank for most clubs) but
ships the structural change so the template is ready when the data
arrives. Document the limitation in the rendered page (HTML comment is
fine).

### Step 4 — Multi-season validation pass (also your scope)

The previous agent only validated Findings #1–#3 against 2024/25 local
data. Goal: ≥5 seasons.

**Default plan:** scrape seasons 2019, 2020, 2021, 2022, 2023, 2024 (6
seasons). For each:

```bash
cd /Users/tzahi.argaman/dev/argamanza/wiki7/data
uv run python run_pipeline.py --season=<YEAR>
```

The pipeline outputs to `data_pipeline/output/<YEAR>/`. After each
season:

1. Inspect `mappings.he.yaml` for any clubs where Wikidata coverage is
   surprising (high-profile clubs falling to LLM). Investigate.
2. Inspect any Hebrew club labels still carrying `(כדורגל...)` paren
   suffixes — means the regex missed a variant. Extend.
3. Spot-check player pages for youth/senior split correctness — older
   players have careers stretching to early 2000s with different TM
   naming conventions.

**Edge cases to specifically watch for:**
- Pre-2010 TM data uses slightly different markup (older HTML).
- B-team / II / Reserve naming conventions vary by country
  (e.g. "Real Madrid Castilla" = Real Madrid B but doesn't carry "B"
  suffix — it's a proper-name reserve team).
- Russian/Cypriot/Greek clubs that fell to LLM might now resolve via
  Wikidata after the Q103229495 P31 addition.

**Expand to earlier seasons** (2014–2018, even back to 2010 if you're
hungry for edge cases) if the 2019–2024 sweep reveals format variation
that needs older data to trigger.

**Cost budget:** at ~600 credits per full-pipeline run, 6 seasons is
~3600 credits. Within budget.

If you adjust any of Findings #1–#3 regex/logic during this pass, add
tests covering the edge case.

---

## 7. Critical project files (file map)

### Where the action is

| File | Purpose |
|---|---|
| `data_pipeline/wikidata_lookup.py` | Wikidata Hebrew-label lookup. Recent additions: `_search_variants`, `_clean_he_label`, `Q103229495` in club P31 set. |
| `data_pipeline/helpers.py` | Filters used by Jinja templates. Recent: `is_youth_club_name` (English + Hebrew). |
| `data_pipeline/apply_hebrew_mapping.py` | Applies `mappings.he.yaml` to scraped data. Translation order: manual override > existing name_hebrew > mapping. |
| `data_pipeline/normalize_enrich_players.py` | Pydantic shape + enrichment pass between scrape and Hebrew mapping. |
| `tmk-scraper/tmk_scraper/spiders/player_spider.py` | **Most likely place to add the career-stats fetch** (after `parse_transfer_history`). |
| `tmk-scraper/tmk_scraper/spiders/stats_spider.py` | Existing HBS team-season stats (apps/goals/cards). Reference for the data shape. |
| `tmk-scraper/tmk_scraper/settings.py` | `USE_SCRAPERAPI`, `SCRAPERAPI_KEY` env-driven config. |
| `wiki_import/import_players.py` | Renders + uploads player pages. Splits `transfers_youth` vs `transfers_senior` before render. |
| `wiki_import/templates/player_page.j2` | Player page Jinja template. Where you'd add the per-club career-stats table. |
| `tests/test_*.py` | 363 tests, all passing. Run via `uv run pytest`. |
| `run_pipeline.py` | End-to-end CLI. `--season=<year>` or `--season=latest`. |

### Where NOT to touch

- `docker/` — local + prod MediaWiki config. The pipeline writes pages
  via the mwclient API; you don't need to edit MediaWiki itself.
- `cdk/` — AWS infrastructure. Production lives here. Hands off.
- `docker/extensions/Wiki7ReviewGate/` — the review-gate extension.

### Memory + working docs

| File | What's in it |
|---|---|
| `/Users/tzahi.argaman/.claude/projects/-Users-tzahi-argaman-dev-argamanza-wiki7/memory/MEMORY.md` | Index of persistent memory entries |
| `…/memory/wiki7-tm-career-stats-investigation.md` | The previous agent's investigation memo for #4 (probe results, ceapi guesses) |
| `…/memory/wiki7-revival-priorities.md` | Order of work, what's done, what's pending |
| `…/memory/wiki7-translation-strategy.md` | Wikidata-first chain rationale + the `_MAX_WORKERS = 2` load-bearing finding |
| `…/memory/wiki7-review-progress.md` | Per-page-type review matrix |
| `wiki7/docs/iter-cycle-review-guide.md` | Iteration-cycle review process |
| `wiki7/data/docs/finding-4-handoff.md` | This file. Append to it when handing back. |

You can read these freely. Write new memory only if you discover
something genuinely new and persistent (not just task progress).

---

## 8. How to run things

### Pipeline (per season)

```bash
cd /Users/tzahi.argaman/dev/argamanza/wiki7/data
uv sync                                     # ensure deps installed
uv run python run_pipeline.py --season=2023 # scrape + normalize + translate + import
uv run python run_pipeline.py --season=latest  # auto-detect latest populated season
```

Output lands in `data_pipeline/output/<YEAR>/`.

### Tests

```bash
cd /Users/tzahi.argaman/dev/argamanza/wiki7/data
uv run pytest                              # all
uv run pytest tests/test_wikidata_lookup.py  # specific module
uv run pytest -x --tb=short -q              # fast iteration mode
```

Current: 363 passing. Add tests for any new code.

### Local docker (only if you need to inspect rendered pages)

```bash
cd /Users/tzahi.argaman/dev/argamanza/wiki7/docker
docker compose up -d
# MediaWiki at http://localhost:8080 (NOT /wiki/ — drop that prefix)
# Adminer at http://localhost:8081
```

Wiki login: `Admin / Wiki7Admin!23`.

### ScraperAPI key

Stored in env: `SCRAPERAPI_KEY`. The spider middleware reads it. If you
toggle `render=true`, the spider URL builder includes `&render=true` in
the query string.

### Anthropic API key (for Claude fallback in translation chain)

Env var: `WIKI7_ANTHROPIC_API_KEY` (NOT `ANTHROPIC_API_KEY` — they're
separate, the previous agent set up the rename to isolate from Claude
Code's subscription billing).

---

## 9. Communication + handoff back to originating agent

When you're done (or stopping for any reason):

1. **Commit your work** with a clear message (no AI co-author lines).
   Multiple small commits OK. Suggested message convention:
   `Finding 4: <what you did>` for #4 work, `Validation: <observation>`
   for cross-season findings, etc.
2. **Push to `origin/phase-3a-r2/implementation`.**
3. **Append a "Handoff Back" section at the bottom of THIS file**
   (`wiki7/data/docs/finding-4-handoff.md`). Include:
   - Date/time.
   - What you did (concrete: files changed, tests added/changed, seasons
     scraped, endpoint URLs discovered).
   - What you found (especially: the actual XHR endpoint URL if you
     discovered it; the credit cost if you switched to render=true).
   - What remains (anything you started but didn't finish; any
     decisions deferred to the user).
   - Any new edge cases in Findings #1–#3 that you fixed.
   - Any new edge cases you DIDN'T fix and why.
4. **Commit + push the handoff edit too.** That's the signal for the
   next agent to pick up.

### If you get stuck

- If the user hasn't authorized something and it's load-bearing: stop
  and document the blocker. Don't guess.
- If a test fails and you can't explain why: stop and document. Don't
  delete or `-x` the test.
- If you need to touch prod, CDK infra, or `master`: stop. Those are
  off-limits for this handoff.

---

## 10. Quick-reference: what the previous agent shipped (so you don't
duplicate or revert it)

### Code changes on this branch (last commit at handoff: see `git log --oneline -1`)

```
data_pipeline/helpers.py             | +45 lines  (is_youth_club_name + regex constants)
data_pipeline/wikidata_lookup.py     | +121 lines (variant generator, paren-suffix cleaner,
                                                  Q103229495 in club P31, _resolve_one
                                                  refactored to retry-with-variant)
tests/test_helpers.py                | +85 lines  (TestIsYouthClubName, 11 cases)
tests/test_wikidata_lookup.py        | +141 lines (TestSearchVariants + paren tests + variant
                                                  fallback test)
wiki_import/import_players.py        | +16 lines  (transfers_youth + transfers_senior split)
wiki_import/templates/player_page.j2 | +32 lines  (== קריירת נוער == section + sorted
                                                  Cargo store)
```

### Test deltas

- Was 359 passing, now 363 passing (+4 from new files; +20 from new
  cases overall).
- No flaky tests. Full suite runs in ~1.5s.

### Empirically validated on 2024/25 local data

- **Finding #1:** "AEK Athens" newly resolves via Wikidata (was LLM
  fallback). "1.FC Nuremberg" now resolves via spacing variant.
- **Finding #2:** 5 high-visibility clubs cleaned: Aberdeen FC, Hapoel
  Tel Aviv, Maccabi Haifa, Genoa, FK Sochi.
- **Finding #3:** 8 current-squad players (Yoan Stoyanov, Miguel Vítor,
  Iuri Medeiros, Ben Gordin, Antonio Sefer, +3) render correct youth/
  senior split. Hebrew-form youth markers (תחת N, נוער) correctly
  recognised after the regex extension.

---

## 11. Next-actions checklist

```
[ ] Read this file in full (you're here)
[ ] Read the investigation memo:
    /Users/tzahi.argaman/.claude/projects/-Users-tzahi-argaman-dev-argamanza-wiki7/memory/wiki7-tm-career-stats-investigation.md
[ ] Read tmk-scraper/tmk_scraper/spiders/player_spider.py to understand
    the ceapi pattern for market-value + transfer-history
[ ] Open browser DevTools on Miguel Vítor's leistungsdaten page
    (https://www.transfermarkt.com/miguel-vitor/leistungsdaten/spieler/57814/saison/ges)
    and capture the XHR that returns career stats JSON
[ ] If found: implement the new spider parse stage; wire through
    normalize + apply_hebrew_mapping; add to player_page.j2; add tests
[ ] If NOT found after a real DevTools session: switch to render=true
    as the documented authorized fallback; document the credit cost
[ ] Run multi-season validation (default span: 2019-2024 = 6 seasons)
[ ] For each season, inspect mappings.he.yaml + render samples and
    adjust Findings #1–#3 regex if cross-season edge cases surface
[ ] uv run pytest — all green before committing
[ ] Commit with clear messages, NO AI co-author lines
[ ] Push to origin/phase-3a-r2/implementation
[ ] Append "Handoff Back" section to the bottom of this file with what
    you did, what you found, what remains
[ ] Final push of the handoff edit
```

---

# Handoff Back

<!-- Append your findings here. Use the template below. Bump the date.

## YYYY-MM-DD HH:MM UTC — <your one-line summary>

### What I did
- ...

### What I found
- XHR endpoint: ...
- Credit cost: ...
- Edge cases discovered in Findings #1-3: ...

### What remains
- ...

### Files changed
- ...

### Tests
- Was 363 passing. Now N passing.

### Anything for the user to decide
- ...
-->
