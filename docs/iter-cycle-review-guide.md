# Iteration-cycle review guide

This guide tells the reviewer (Tzahi) how to walk through a per-season bot import on local docker — what to look at, where the friction will be, how to report findings, and how to apply fixes. It's the durable companion to the per-cycle recipe in [`operational-bootstrap.md`](operational-bootstrap.md) §8 (run pipeline) + §9 (Cargo + review setup).

Born during iter cycle 1 (2024/25) in 2026-06-11; the goal is to make each subsequent cycle (walking backward through modern seasons, then 1985/86 onward) cheaper because the workflow is already documented.

## Mindset

The point of iter-cycle review is **not** to perfect a single season. It's to find **categories of issues** that would manifest at scale across 77 seasons — translation gaps, template / layout bugs, spider data quality, broken cross-page links. Spot-checking 2-3 pages per category is more valuable than exhaustive coverage of THIS season.

When in doubt: skim widely, sample deeply, escalate patterns.

## 🚨 The "Helder-Lopes trap" — read before reviewing

**To fix a player's name or page title, edit `data_pipeline/output/<season>/mappings.he.yaml` (set `src: manual` on the entry) — renaming the page on the wiki or editing the infobox to "correct" the Hebrew name gets overwritten on the next import.**

Pattern B's surgical wikitext merger preserves prose, hatnotes, categories, and any content OUTSIDE bot-managed sections (`<!-- wiki7-bot-managed-section start: ... -->` / `end: ...`). But the infobox sits INSIDE a managed section, and the page title comes from `player.name_hebrew` which the bot re-renders from the mapping every run. Fix the *source* (the mapping file), not the *rendered output*.

The named "Helder-Lopes" case: a reviewer correcting a wikidata-pulled gibberish name ("הלדר לפופסיק" → "הלדר לופש") by editing the wiki page got their fix evaporated on the next import; once the mapping was set to `src: manual` with the right value, the bot auto-MovePages the draft to the corrected title AND renders the infobox with the corrected name, every run. See [[wiki7-translation-strategy]] memory for the `src: manual` precedence rules.

## Pre-review setup (one-time per fresh docker)

The full recipe lives in [`operational-bootstrap.md`](operational-bootstrap.md). The TL;DR is:

```bash
# 1. Reset wiki + clear pipeline output
docker exec docker-mediawiki-1 php /var/www/html/maintenance/run.php \
  /var/www/html/extensions/Wiki7ReviewGate/maintenance/resetContent.php --scope=all --confirm
rm -rf data/data_pipeline/output/<season>/ data/data_pipeline/output/merged/

# 2. Run pipeline (skip-scrape if the cached scrape is current)
cd data && export WIKI_URL='http://localhost:8080' WIKI_BOT_USER='Wiki7Bot' \
       WIKI_BOT_PASS='localdev-password-2026' WIKI_GATE_ENABLED='1' \
       WIKI7_ANTHROPIC_API_KEY="$WIKI7_ANTHROPIC_API_KEY"
uv run python run_pipeline.py --season <YYYY> --skip-scrape

# 3. Bulk-approve templates + materialise Cargo tables (see operational-bootstrap §9)
docker exec docker-mediawiki-1 php /var/www/html/maintenance/run.php \
  /var/www/html/extensions/ApprovedRevs/maintenance/approveAllPages.php --username=Admin
for table in players match_reports transfers market_values player_stats \
             coaches honours season_standings head_to_head; do
  docker exec docker-mediawiki-1 php /var/www/html/maintenance/run.php \
    /var/www/html/extensions/Cargo/maintenance/cargoRecreateData.php --table=$table --quiet
done
docker exec docker-mediawiki-1 php /var/www/html/maintenance/run.php runJobs.php
```

Login as `Admin` / `AdminPass1234` (already in `reviewer` + `sysop`).

## Phase B — quality spot-checks (~20-30 min)

Sample 2-3 pages per category. Stop when you see the pattern.

### 1. One Israeli HBS regular

Pick from `http://localhost:8080/Special:AllPages?namespace=3000` — e.g. `Draft:אליאל פרץ` (Eliel Peretz).

Check:
- Hebrew name accurate? (Wikidata Q-ID resolution: most should be canonical)
- Infobox fields populated (DOB, position, nationality)?
- Per-season stats table renders?
- Transfers history renders?

### 2. One foreign HBS player — translation quality bellwether

E.g. `Draft:הלדר לפופסיק` (Hélder Lopes). Wikidata's `labels.he` is sometimes stale/vandalised (this name is gibberish in the entity); the `sitelinks.hewiki.title` would give the correct `הלדר לופש`. Reviewer-override candidate. See [`research/0003-translation-overhaul-plan.md`](research/0003-translation-overhaul-plan.md) for the deferred sitelinks-first v2 fallback.

### 3. One match page

E.g. `Draft:Sun_20/10/24_נגד_מכבי_פתח_תקווה_(ליגת_העל_בכדורגל)`. Verify:
- Lineup shows **full names** (not surnames) — iter-cycle 1 fix
- HBS players are blue links to drafts, opposing players are plain text
- Goals / substitutions / cards / penalties tables populate correctly
- Click an HBS player in the lineup → navigates to their draft

### 4. Season overview

`Draft:עונת_2024/25` — central aggregator. Sanity-check.

### 5. (Optional) Leaderboard preview

By design, leaderboards in NS_DRAFT render with **zero rows** because the namespace gate keeps drafts out of Cargo SQL. To test rendering:

1. MovePages 3-5 player drafts to mainspace via the wiki UI (`action=move` tab)
2. Revisit `Draft:מלכי_השערים_של_כל_הזמנים`
3. The leaderboard now shows partial data sourced from those promoted players

This confirms the aggregation logic without requiring full promotion.

### 6. Skim the aggregates

Derbies, European campaign, Honours, Coaches, Stadium, Records. Look for obvious weirdness — empty sections, broken templates, wrong Hebrew labels.

## Phase C — translation pass (~10-15 min)

Open `data/data_pipeline/output/<season>/mappings.he.yaml`. Two productive scans:

1. **Search for `confidence: low`** — chain-flagged entries the translation chain itself wasn't confident about. Reviewer eyes are the validation.

2. **Spot-check `src: wikidata`** — Wikidata's Hebrew labels are usually canonical but sometimes stale/vandalised (the Hélder Lopes case). Sample 5-10 to gauge how often this happens.

To override a translation:

```yaml
# Before
Helder Lopes:
  he: הלדר לפופסיק
  src: wikidata
  confidence: high
  wikidata_qid: Q5964151

# After (reviewer edit)
Helder Lopes:
  he: הלדר לופש        # corrected from Hebrew Wikipedia
  src: manual          # auto-fill will now skip this entry forever
  confidence: high
  wikidata_qid: Q5964151
  note: 'Corrected from sitelinks.hewiki.title 2026-06-11'
```

Then re-run the pipeline (`--skip-scrape` keeps the cached scrape, only re-translates + re-imports):

```bash
cd data && uv run python run_pipeline.py --season <YYYY> --skip-scrape
```

Manual entries are preserved across all future runs.

## How to record findings

The most efficient feedback loop with Claude (or future-you):

- **Translation corrections** — batch up + edit the YAML directly + re-run, OR send the list to Claude to edit + re-run
- **Template / layout issues** — describe what's wrong + which page; Claude fixes in code + you re-import
- **Spider / data gaps** — describe what's missing + which TM page surfaces it; Claude extends the spider + you re-scrape (or re-import if no spider change needed)

For broader patterns that affect multiple seasons, capture in [`phase-3b-backlog.md`](phase-3b-backlog.md) — that's the long-tail home.

## What to escalate vs. note-and-move-on

- **Escalate immediately:** anything that suggests a pattern across many pages (e.g. "every player page is missing nationality flag"), template-system issues, anything that blocks prod push.
- **Note-and-move-on:** single-name typos, individual missing fields, isolated weirdness. Catalog these and fix in bulk later.

## Quick links

| Need | URL |
|------|-----|
| All drafts (current season + ever-accumulated club-level pages) | `http://localhost:8080/Special:AllPages?namespace=3000` |
| Templates to approve (initial pre-cycle setup) | `http://localhost:8080/Special:ApprovedRevs?show=unapproved` |
| All approved revisions | `http://localhost:8080/Special:ApprovedRevs?show=all` |
| Cargo tables | `http://localhost:8080/Special:CargoTables` |
| Cargo ad-hoc query (reviewer + sysop only — anon is revoked) | `http://localhost:8080/Special:CargoQuery` |
| Drilldown for a table | `http://localhost:8080/Special:Drilldown/players` |
| Mapping file | `data/data_pipeline/output/<season>/mappings.he.yaml` |
| Login | `http://localhost:8080/index.php?title=Special:UserLogin` (`Admin` / `AdminPass1234`) |

## Per-season iteration order

Per [`research/0003-translation-overhaul-plan.md`](research/0003-translation-overhaul-plan.md) §4:

1. **2024/25 first** — latest data, easiest for reviewer to verify because the season is fresh in memory
2. **Walk backwards: 2024/25 → 2023/24 → ... → ~2010/11** — modern era with richest data; deal with template + style issues first
3. **Review aggregates** at the 10-season modern slice (Derbies populated, leaderboards meaningful)
4. **Jump to 1985/86 (oldest with rich data)** — different data shape, sparser fields
5. **Walk forward: 1985/86 → 1986/87 → ... → 2009/10** — fill the historical gap
6. **Historical placeholders (1949/50 → 1984/85)** — manual hand-curation rather than bot-driven; the always-emit-placeholder support keeps the structure intact

After ~5 modern seasons reviewed, pause for a strategic check-in on whether the iteration-cycle approach is working before continuing.
