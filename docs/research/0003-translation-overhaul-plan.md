# Research 0003 — Hebrew translation overhaul + iteration-cycle plan

- **Status:** Draft — next session implements
- **Date:** 2026-06-10 (alignment session closing notes)
- **Phase:** post-Phase-3a R2 PR B step 10 (local validation), pre-prod-push
- **Companions:** [`docs/research/0002-transfermarkt-data-surface.md`](0002-transfermarkt-data-surface.md), [`docs/revival-plan.md`](../revival-plan.md)

## Why this document exists

Phase 3a R2 PR B step 10 successfully imported the all-time HBS corpus to local docker (2,680 pages, 0 failures). Two major issues surfaced during review:

1. **Translation coverage is far worse than expected.** The Wikipedia first-pass (English Wikipedia → Hebrew via `langlinks`) hit only **0.8%** of the 5,794 name corpus. The remaining 5,749 names went to Claude, which produces plausible-but-not-canonical Hebrew transliterations.
2. **Translation changes between runs cause duplicate pages.** Switching backends (Google → Wikipedia/Claude) produced different Hebrew names for the same player, leaving an orphan `Draft:<old-name>` alongside the new `Draft:<new-name>`. There's no rename / archive flow.

Plus a smaller issue: Cargo template approval workflow + table-creation timing isn't documented anywhere reviewers will encounter it.

And a strategic finding: bulk-importing all 77 seasons at once is **overwhelming for a single reviewer**. Going season-by-season is more practical.

This doc records the research + decisions for the next phase. The implementation lands in a new session.

## 1. Translation overhaul — switch to Wikidata as the canonical bridge

### Research findings (2026-06-10)

| Source | Coverage observation |
|---|---|
| English Wikipedia langlinks (`prop=langlinks&lllang=he`) | **0.8%** on the all-time HBS name corpus. Only catches names that have BOTH an English Wikipedia article AND a Hebrew langlink. Most Israeli league players don't have English articles. |
| Hebrew Wikipedia per-club player categories | **Not used by Hebrew Wikipedia.** Probed `קטגוריה:שחקני הפועל באר שבע` — does not exist. Hebrew Wikipedia organizes player articles by national team / position / decade, not by club. So a category-walk approach won't work. |
| Wikidata `wbsearchentities` + `wbgetentities` | **Works as a true cross-language bridge.** Verified for Lior Refaelov: English search → Q964300 → `wbgetentities&languages=he` returns canonical `ליאור רפאלוב`. **Wikidata has Hebrew labels for entities even when no Hebrew Wikipedia article exists.** This is the silver bullet. |
| Hebrew Wikipedia `action=opensearch` | Works as a fuzzy fallback. Given a rough Hebrew transliteration (e.g. from Claude), can find the closest article. |

### Recommended translation pipeline

For each English entity in `mappings.he.yaml`, try in order:

```
1. Wikidata wbsearchentities (search=<english_name>, language=en)
     ↓
   Top-N candidates with Q-IDs

2. Disambiguate via expected entity type:
     - players:       P31=Q5 (human) + P641=Q2736 (assoc. football)
     - clubs:         P31=Q476028 (football club)
     - tournaments:   P31=Q500834 (football tournament) or sub-types
     - countries:     P31=Q6256 (country)
     ↓
   Best-match Q-ID

3. wbgetentities&languages=he&props=labels
     ↓
   Hebrew label → confidence=high, src=wikidata

4. (fallback) Hebrew Wikipedia opensearch with Claude-suggested transliteration
     ↓
   First match → confidence=medium, src=wikipedia-he-fuzzy

5. (fallback) Claude API
     ↓
   confidence as Claude self-rates, src=auto-llm

6. (fallback) Phonetic transliteration
     ↓
   confidence=low, src=auto-translit
```

### Expected coverage

- **Players:** 60-80% via Wikidata (broad coverage of footballers, including Israeli-league regulars). The remainder via Claude.
- **Clubs:** ~95% via Wikidata (nearly every notable football club has a Wikidata entry with multilingual labels).
- **Tournaments:** ~99% via Wikidata.
- **Nationalities (countries):** 100% via Wikidata.
- **Positions:** keep current Claude path — Wikidata's labels for positions are unreliable.

### Apply to all categories, not just names

Current implementation only does Wikipedia lookup for the `names` category. Wikidata-based approach should apply to **clubs, tournaments, nationalities** too — they all have Wikidata Q-IDs and high-quality multilingual labels.

### Implementation notes for next session

- New module `data_pipeline/wikidata_lookup.py` (parallels the existing `wikipedia_lookup.py`).
- Modify `auto_translate_hebrew._fill_section` to:
  - For names + clubs + competitions + nationalities → Wikidata first, Claude fallback
  - For positions → keep Claude direct
- Add `src: wikidata` as a new provenance value alongside existing `manual`, `auto-llm`, `auto-google`, `auto-translit`, `wikipedia`.
- Persist Wikidata Q-IDs in `mappings.he.yaml` so re-runs can skip the search step:
  ```yaml
  "Lior Refaelov":
    he: "ליאור רפאלוב"
    src: wikidata
    confidence: high
    wikidata_qid: Q964300
    note: ""
  ```
- Manual entries still win — auto-fill never overwrites `src: manual`.

## 2. Duplicate-page problem — pipeline tracks `tm_id → he_name`

### Problem statement

The pipeline generates page titles from the player's Hebrew name (`p.name_hebrew`). When a translation changes between runs, the same TM player gets a new page title. The previous page becomes an orphan draft. The wiki ends up with:

```
Draft:אנתוני נוואקיימה  (Google's transliteration from run 1)
Draft:אנתוני ואקמה       (Wikipedia's canonical from run 2)
```

Both represent the same TM player (Anthony Nwakaeme, TM ID 318107).

### Proposed fix

Pipeline maintains a per-environment state file `pipeline-state/he_name_by_tm_id.yaml`:

```yaml
"318107":              # TM player ID
  he_name: "אנתוני ואקמה"
  last_seen: "2026-06-10"
```

On each player-import run:

1. Look up the player's prior Hebrew name in the state file by TM ID
2. Render the page title from the current Hebrew name
3. If prior `he_name` exists AND differs from current:
   - MovePage API call: rename `Draft:<old>` → `Draft:<new>` (mwclient supports this)
   - Suppresses redirect creation (or keeps it; reviewer decides)
   - Logs the rename for reviewer audit
4. Update the state file

State file is per-environment (local vs prod) — stored under `pipeline-state/` (git-ignored).

### Why not "just reset drafts before re-translating"

That works for `--scope=drafts-only` reset cycles. But once a reviewer **promotes** a draft to mainspace, the reset doesn't touch it. The mainspace page's title is now permanent (or requires a MovePage anyway). So the state-file approach is the proper long-term fix.

For the iteration-cycle phase (next), the reset-then-re-import flow is fine because no promotion is happening. So we can defer the state-file implementation until the first season is reviewer-approved.

## 3. Cargo template approval workflow — document the post-approval refresh

### What happens today

When the bot writes a `Template:Cargo/Foo` page (a Cargo declaration), the page lands in `NS_TEMPLATE`. `NS_TEMPLATE` has Approved Revs enabled (`LocalSettings.php`'s `$egApprovedRevsEnabledNamespaces[NS_TEMPLATE] = true`). So the bot's write is held back as "latest unapproved" — the previously-approved revision (none, for a new template) is what the parser uses.

For a brand-new Cargo declaration template:
- The template exists in NS_TEMPLATE as latest-unapproved
- The Cargo extension hasn't been notified to create the SQL table
- Player pages that transclude the template skip the `#cargo_store` call entirely
- Cargo table list shows "Template:Cargo/Foo defines transfers — Table not yet created"

### Reviewer workflow needed for v1 Cargo tables to materialize

1. Reviewer goes to `Special:UnapprovedPages` and approves each `Template:Cargo/*` (16 templates currently — 9 Cargo + 4 MediaWiki templates + 3 other infoboxes)
2. The first approved render of each Cargo declaration template causes Cargo to create the SQL table
3. Existing player/match pages that transclude the template need a re-parse to fire their `#cargo_store` calls. Options:
   - Manual: edit + save each page (impractical for 552 player pages)
   - Background: `php maintenance/runJobs.php` (slow drain)
   - Forced: `php extensions/Cargo/maintenance/cargoRecreateData.php` (recommended)

### What to document

A new section in `docs/operational-bootstrap.md` or `data/README.md`:

> ### After bot import: making Cargo tables live
> 
> The bot's Cargo template writes land as "latest unapproved" in NS_TEMPLATE (per Approved Revs gating). Cargo doesn't create tables until the template is approved. Once approved, existing player/match pages need a re-parse to populate their data rows.
> 
> One-time steps after each bot import:
> 
> 1. Visit `Special:UnapprovedPages` as Admin, approve each `Template:Cargo/*` and the 4 MediaWiki templates.
> 2. SSH into the container + run `cargoRecreateData.php` per table:
>    ```bash
>    docker exec docker-mediawiki-1 php /var/www/html/extensions/Cargo/maintenance/cargoRecreateData.php --table=players
>    docker exec docker-mediawiki-1 php /var/www/html/extensions/Cargo/maintenance/cargoRecreateData.php --table=matches
>    # ... repeat per Cargo table
>    ```
> 3. Verify on `Special:Cargo` that each table now reports row counts.

## 4. Season-by-season iteration plan

### Why switch from bulk all-time to per-season

The 2,680-page bulk import is **overwhelming for a single reviewer**. Promoting and reviewing all of them sequentially would take days. Iterating one season at a time:

- Reviewer can give each season's data a thorough review
- Issues caught early before they propagate across all 77 seasons
- Lets us iterate on templates / styles based on feedback
- Aligns with how a wiki organically grows
- Lower cognitive load per cycle

### Pipeline support — already in place

The pipeline accepts `--season <YYYY>` for single-season runs:

```bash
uv run python run_pipeline.py --season 2024
```

This runs:
1. Scrape that single season (~80 ScraperAPI credits per modern season)
2. Normalize that season only
3. Hebrew enrichment for the names that appeared in that season
4. Import that season's pages + update club-level pages (Derbies, European campaign, Honours, etc.)

### What changes across iterations

Club-level pages (Derbies, European campaign, Coaches, Honours, Stadium, Records) are derived from **cumulative club-level spider output** (bilanz, platzierungen, mitarbeiter, erfolge), which is constant. They don't depend on which seasons are imported — they show all-time data.

Player pages aggregate stats/transfers from **all imported seasons**. Importing a player in 2024 first then in 2023 means his page gets a new row in the stats table on the second import.

Leaderboards aggregate stats from all imported seasons. Each new season import updates them.

### Recommended iteration order

1. **Start with 2024/25** — latest data, easiest for reviewer to verify because they remember the season
2. **Walk backwards: 2024/25 → 2023/24 → ... → ~2010/11** — modern era with richest data, deal with template + style issues first
3. **Review aggregates** at the 10-season modern slice (Derbies populated, leaderboards meaningful)
4. **Jump to 1985/86 (oldest with rich data)** — different data shape, sparser fields
5. **Walk forward: 1985/86 → 1986/87 → ... → 2009/10** — fill the historical gap
6. **Historical placeholders (1949/50 → 1984/85)** — manual hand-curation rather than bot-driven; the always-emit-placeholder support keeps the structure intact

### Recipe per iteration

```bash
# 1. Reset draft content from last iteration
docker exec docker-mediawiki-1 php /var/www/html/maintenance/run.php \
  /var/www/html/extensions/Wiki7ReviewGate/maintenance/resetContent.php \
  --scope=drafts-only --confirm

# 2. Wipe local pipeline output for this season (forces fresh translation)
rm -rf data/data_pipeline/output/<season>/ data/data_pipeline/output/merged/

# 3. Run pipeline for the season (resume default keeps cached scrape)
export WIKI_URL='http://localhost:8080' \
       WIKI_BOT_USER='Wiki7Bot' \
       WIKI_BOT_PASS='localdev-password-2026' \
       WIKI_GATE_ENABLED='1' \
       WIKI7_ANTHROPIC_API_KEY='<key>'
cd data && uv run python run_pipeline.py --season 2024

# 4. Approve Cargo templates (one-time per fresh stack)
# Visit http://localhost:8080/wiki/Special:UnapprovedPages
# Approve each Template:Cargo/*

# 5. Refresh Cargo data for this season's data
docker exec docker-mediawiki-1 php /var/www/html/extensions/Cargo/maintenance/cargoRecreateData.php

# 6. Review draft pages, edit/promote as appropriate
# Visit http://localhost:8080/wiki/Special:UnapprovedPages
# Visit http://localhost:8080/wiki/Special:AllPages?namespace=3000

# 7. Note issues found, fix in code, repeat
```

### What about prod push

Prod push remains the same recipe in PR A — flip the quiet flag, recycle container, run pipeline against prod, flip back. The question becomes: at what point in the iteration cycle do we deploy to prod?

**My recommendation:** prod push happens AFTER we've iterated through ~5-10 modern seasons locally and reviewed/fixed everything. The all-time push goes to prod once. Then prod stays in maintenance mode (per-season incremental updates as TM data arrives).

This is consistent with the user's note: "I'd like to do many more iterations on local first."

## 5. Open architectural questions for the next session

1. **Stable page identity** — even with the state-file approach for the duplicate-name problem, do we want page titles to be eventually-stable Hebrew names, or always-stable TM-ID-based slugs with Hebrew names as display? (My recommendation: stable Hebrew names with state-file-managed rename on translation drift. TM-ID slugs would feel un-wiki-like.)

2. **Wikidata disambiguation** — for players with common names (e.g. "Lior Cohen"), Wikidata returns multiple Q-IDs. How do we pick the right one? Options: use TM player ID as a Wikidata external ID (P-property check), check P641 (sport) = Q2736 (football), match by birth year (we have it from the player spider). Need to validate.

3. **Wikidata fallback when Hebrew label missing** — what if Wikidata has the Q-ID but no Hebrew label? Falls back to Claude. We saw this on the manual test for Lior Refaelov but let me re-verify the broader pattern.

4. **State file location** — `pipeline-state/he_name_by_tm_id.yaml` per environment. Local vs prod? Probably both, tracked separately. Git-ignored.

5. **Cargo population automation** — should the pipeline run `cargoRecreateData.php` automatically after the import step? This is an SSM exec on prod; on local it's a docker exec. Could thread through `WIKI_REFRESH_CARGO=1` env var.

## 6. Backlog rolled into Phase 3b prep

These items don't go into the next iteration session — they belong in Phase 3b:

- Reviewer-queue sub-special-pages (per-namespace filtering)
- Batch-promote maintenance script
- Telegram inline-keyboard approve / reject
- IFA scraper for full referee teams (researched in PR A appendix)
- Per-competition stats split
- Player full-career stats (cross-club)
- National team appearances
- Native-script foreign-player names
- Stable migration from Hebrew title to a Wikidata-Q-ID-based slug if we choose to

## 7. ScraperAPI consumption — empirical baseline

For future planning:

| Run | Credits consumed |
|---|---|
| Phase 3a single-season (2024 only) | ~80 (from prior runs) |
| Phase 3a R2 PR B step 1 (fixtures capture, 13 files) | 13 |
| Phase 3a R2 PR B step 10 all-time scrape (77 seasons) | **7,893** |

Per-season cost varies massively by data density:
- Sparse historical (1949-1974): ~5-8 credits per season (squad+player chains return mostly empty)
- Mid-historical (1975-1984): ~20-40 credits (some real data)
- Modern (1985-2025): ~100-200 credits per season (full match-report scrape, player profiles, market values)

**Monthly Hobby tier (100k credits) supports ~12 all-time iterations** — comfortable headroom for the iteration phase.
