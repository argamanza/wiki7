# Wiki7 Data Pipeline

Scrapy → Pydantic → Jinja → mwclient, with Hebrew enrichment in the middle. Builds the bot-written content that lands on the Hapoel Beer Sheva fan wiki ([wiki7.co.il](https://wiki7.co.il)).

## Architecture

```
┌───────────────┐   ┌──────────────┐   ┌─────────────┐   ┌────────────┐   ┌──────────────┐
│ Transfermarkt │ → │ Scrapy       │ → │ Normalize   │ → │ Hebrew     │ → │ MediaWiki    │
│  (via         │   │ spiders      │   │ + Merge     │   │ enrichment │   │ via mwclient │
│   ScraperAPI) │   │  (10 spiders)│   │  (Pydantic) │   │  (Claude)  │   │  (gate-aware)│
└───────────────┘   └──────────────┘   └─────────────┘   └────────────┘   └──────────────┘
                                                                                  │
                                                                                  ▼
                                                                          ┌──────────────┐
                                                                          │ Wiki7        │
                                                                          │ ReviewGate   │
                                                                          │   Draft:*    │
                                                                          │   for NEW    │
                                                                          │   mainspace+ │
                                                                          │   ApprovedRevs│
                                                                          │   for UPDATE │
                                                                          └──────────────┘
```

Three persisted layers:

| Layer | Path | What's here |
|---|---|---|
| Raw scrape | `tmk-scraper/output/<season>/*.json` + `tmk-scraper/output/*.json` (club-level) | One file per Scrapy spider. Reads from disk for resume. |
| Normalized | `data_pipeline/output/<season>/*.jsonl` and `…/merged/` | Pydantic-validated rows + per-season → merged aggregate. |
| Hebrew | `data_pipeline/output/<season>/*.he.jsonl` + `mappings.he.yaml` | English-to-Hebrew lookup file + translated data files. |

## Spiders

10 Scrapy spiders, all in `tmk-scraper/tmk_scraper/spiders/`. TM page names retain the German used in TM's URL paths (see `docs/research/0002-transfermarkt-data-surface.md` §1.1 for the glossary).

| Spider | TM page (German term) | Per-season? | What it scrapes |
|---|---|---|---|
| `squad` | `kader` (squad) | yes | Current + loaned-out players for the season. |
| `player` | player profile chain | yes | Per-player facts, market value history (via `ceapi`), transfer history. |
| `stats` | `leistungsdaten` (performance data) | yes | Per-season player stats: apps, goals, assists, cards, minutes. |
| `fixtures` | `spielplandatum` (schedule by date) | yes | The season's fixture list with match-report URLs. |
| `match` | `spielbericht` (match report) | yes | Per-match lineups, goals, subs, cards, halftime, AET, stadium, main referee. |
| `transfers` | `alletransfers` (all transfers) | yes | Club-level arrivals + departures per season. |
| `coach` | `mitarbeiter` (staff) | no — once | Current staff list (head coach + assistants + admin). |
| `honours` | `erfolge` (successes / honours) | no — once | Club's trophy list. |
| `stadium` | `stadion` (stadium) | no — once | Current stadium info. |
| `records` | `transferrekorde` (transfer records) | no — once | Record arrivals (departures derived from `alletransfers`). |
| `platzierungen` | `platzierungen` (standings) | no — once | **Phase 3a R2**: per-season league position + W/D/L + manager. |
| `bilanz` | `bilanz` (head-to-head record) | no — once | **Phase 3a R2**: per-opponent all-time record (matches, W/D/L, attendance). |

Per-season spiders accept `--season=<YYYY>` (the bare integer start-year — matches TM's `saison_id` URL param). Club-level spiders ignore the season arg and emit one rows-set per request, covering every season TM has data for.

## Domain choice — `transfermarkt.com` + ScraperAPI `country_code=us`

The spiders hit `transfermarkt.com` URLs routed through ScraperAPI with `country_code=us`. Both `transfermarkt.com` and `transfermarkt.us` hit the same TM database — localization (chrome strings, date formatting) is render-time only. We use `.com` because that's TM's canonical TLD, and we lock the rendering language with `country_code=us` because the spiders' CSS selectors and stats column-header aliases all require English column titles. **Don't swap one for the other** for cosmetic consistency — switching the country code would silently break the stats spider; switching to `.us` for the host is functionally a no-op. See `docs/research/0002-transfermarkt-data-surface.md` §5.6 for the audit.

## How to run

```bash
cd data
uv sync                                      # install deps (incl. anthropic SDK)
export SCRAPERAPI_KEY=<your key>             # see docs/operational-bootstrap.md §6
export ANTHROPIC_API_KEY=<your key>          # for the Claude translation backend (PR B step 6)
```

### Single season (the original 3a flow)

```bash
uv run python run_pipeline.py --season 2024 --dry-run    # preview the import
uv run python run_pipeline.py --season 2024              # write to the configured wiki
```

### Multi-season (Phase 3a R2 default)

```bash
# All-time (1949 → current). Resume default: spiders whose output already exists
# on disk are skipped. Sparse historical seasons (pre-~1974) get a placeholder
# overview page emitted automatically.
uv run python run_pipeline.py --seasons 1949-2025

# A focused slice — useful for sanity-checking a spider change before re-running
# the all-time corpus.
uv run python run_pipeline.py --seasons 2015,2024
```

### Resume from a partial failure

The pipeline writes per-spider per-season output to disk as it goes. If a run dies (network hiccup, ScraperAPI rate-limit, anything), restart with the **same command** — spiders whose non-empty output already exists are skipped. Empty `[]` files don't count as "done" (they're the "TM returned nothing" case and re-run on restart).

If you genuinely want to re-fetch every spider's output even where files exist (e.g. after a spider fix that affects already-scraped seasons):

```bash
uv run python run_pipeline.py --seasons 1949-2025 --force-rescrape
```

The wiki import step is independently idempotent — every `page.save()` does a content-hash compare against the live page text and skips no-op edits. Re-running import after a partial failure produces zero edits on already-imported pages.

### Two-phase Hebrew review workflow

For a focused review pass on uncertain Claude translations:

```bash
# Phase 1: scrape + normalize + merge + auto-translate (writes mappings.he.yaml with
# `src` + `confidence` metadata), then STOP for review.
uv run python run_pipeline.py --seasons 2021-2025 --review-mappings

# Phase 2: review the flagged entries. Either edit mappings.he.yaml in place, or
# get a focused report of just `confidence: low` entries:
uv run python -m data_pipeline.auto_translate_hebrew --review-flagged-only

# Phase 3: re-run with --skip-scrape so the (now-corrected) mappings apply.
uv run python run_pipeline.py --seasons 2021-2025 --skip-scrape --skip-normalize --skip-merge
```

## Wiki target configuration

The import step writes to whatever `WIKI_URL` resolves to. Three common configurations:

```bash
# Local docker (cd ../docker && docker compose up first):
export WIKI_URL=http://localhost:8080
export WIKI_BOT_USER=Wiki7Bot
export WIKI_BOT_PASS=<localdev-password>
export WIKI_GATE_ENABLED=1

# Production (the gate is always on):
export WIKI_URL=https://wiki7.co.il
export WIKI_BOT_USER=Wiki7Bot
export WIKI_BOT_PASS=<from secrets manager>
export WIKI_GATE_ENABLED=1
```

When `WIKI_GATE_ENABLED=1`, the bot writes new pages to the `Draft:` namespace (where the `reviewer` group + bot can read/edit; public can't) and updates to mainspace as latest-unapproved revisions held back by Approved Revs. See `docs/adr/0002-review-gate-architecture.md` for the architecture.

## Translation backend

PR B step 6 switched the primary backend from Google Translate to the Anthropic API direct (Claude Sonnet 4.6). The file shape changed too:

```yaml
# Legacy (pre-R2) flat:
Centre-Back: בלם

# Phase 3a R2 nested with provenance:
Centre-Back:
  he: בלם
  src: auto-llm        # manual | auto-llm | auto-google | auto-translit
  confidence: high     # high | low
  note: ""
```

The reader (`apply_hebrew_mapping.py`) is shape-agnostic — both layouts coexist legally during the transition. The auto-fill migrates flat → nested on first load (marking pre-existing entries `src: manual, confidence: high` since they were human-curated before R2).

Backend selection:
- `ANTHROPIC_API_KEY` set → Claude (primary).
- Key unset → Google Translate (fallback, with a warning).
- `--use-google` flag → force the Google path even with the key set.

## Output dir layout (after a multi-season run)

```
data_pipeline/output/
├── 2024/                        # per-season normalized (scraper → Pydantic)
│   ├── players.jsonl
│   ├── transfers.jsonl
│   ├── market_values.jsonl
│   └── stats.jsonl
├── 2015/                        # same structure per season
├── …
└── merged/                      # all-seasons aggregate (consumed by import)
    ├── players.jsonl            # dedup'd by TM ID
    ├── players.he.jsonl         # after Hebrew enrichment
    ├── transfers.jsonl
    ├── transfers.he.jsonl
    ├── market_values.jsonl
    ├── stats.jsonl
    └── mappings.he.yaml         # human-review surface

tmk-scraper/output/              # raw scrape (input to normalize)
├── 2024/                        # per-season spider outputs
│   ├── squad.json
│   ├── players.json
│   ├── stats.json
│   ├── fixtures.json
│   ├── matches.json
│   └── transfers.json
├── 2015/
├── …
├── coaches.json                 # club-level (one per club, not per-season)
├── coaches_enriched.json        # honours × per-season-manager join (Phase 3a R2)
├── honours.json
├── stadium.json
├── records.json
├── season_standings.json        # platzierungen output (Phase 3a R2)
└── head_to_head.json            # bilanz output (Phase 3a R2)
```

## Test fixtures

`tests/fixtures/` has HTML samples for multi-era spider testing (Phase 3a R2 captures): 1985/86 (oldest reliable data), 2015/16 (Europa League era), 2024/25 (current). Plus an empty 1965/66 fixture for the always-emit-placeholder path. The `capture_multi_era.py` script reproduces the captures from TM if a fresh batch is ever needed (~13 ScraperAPI credits).

## See also

- `docs/research/0002-transfermarkt-data-surface.md` — TM data inventory + glossary
- `docs/adr/0002-review-gate-architecture.md` — how the bot's writes land safely
- `docs/operational-bootstrap.md` — one-time post-deploy actions (bot account, etc.)
- `docs/revival-plan.md` — overall phase plan
- `BOT_SETUP.md` — historical bot-setup notes from Phase 3a-pipeline
