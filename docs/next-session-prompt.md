# Next-session prompt — Phase 3a R2 iteration cycle + Wikidata translation overhaul

> Paste the body of this document (everything below `---`) into the first message of a fresh Claude Code session at `~/dev/argamanza/wiki7/`. The session will read the linked docs + memory files itself and pick up the work without rewarm cost.

---

## Context

**wiki7** is a personal hobby project — a Hapoel Beer Sheva fan wiki at https://wiki7.co.il ("ויקישבע"). MediaWiki 1.45.3 on a single Graviton EC2 + RDS (~$50/mo), with a custom Wiki7 skin (fork of Citizen 3.17), a Phase-3.5 review-gate (`Wiki7ReviewGate` extension + Lockdown + Approved Revs + Echo + Telegram), and a Python data pipeline (Scrapy + Pydantic + Jinja + mwclient + Claude API for Hebrew translation). It's cost-sensitive — operator (Tzahi) tore prod down once for cost, recurring fear of accidentally re-doing that.

Phase 3a R2 just finished its development phase. **PR A merged** (`docs/research/0002-transfermarkt-data-surface.md` — the comprehensive TM data-surface inventory). **PR B steps 1-10 + 12 done** on branch `phase-3a-r2/implementation` (16 commits ahead of master, pushed to origin, not yet merged). **PR B step 11 = prod push DEFERRED** behind the iteration-cycle phase that this session is starting.

## What the previous session left

- Branch `phase-3a-r2/implementation` — 16 commits, **pushed to origin**, working tree clean, 249/249 pytest passing.
- Local docker stack (`docker ps` shows 3 containers up) — true clean slate: 15 live pages (seed homepage + sub-templates only), 0 archive / recentchanges / logging, `ss_total_edits = 17`.
- Pipeline output dirs (`data/data_pipeline/output/`, `data/tmk-scraper/output/`) are empty — next iteration cycle re-scrapes the season(s) it works on.
- All five memory files updated to reflect the new state.
- `WIKI7_ANTHROPIC_API_KEY` is in `~/.zshrc`. `ANTHROPIC_API_KEY` is deliberately NOT set globally — keeps day-to-day Claude Code subscription work isolated from pipeline cost (Anthropic moves Agent SDK to a separate credit pool on 2026-06-15).

## What this session is about

The previous session's all-time validation surfaced four strategic findings that pushed prod push back behind a longer iteration-cycle phase:

1. **English-Wikipedia-langlinks coverage is 0.8%** of the all-time name corpus (45/5,794). Wikidata is the right cross-language bridge — `wbsearchentities` returns Q-IDs even for Israeli league players without English articles, and `wbgetentities&languages=he` returns canonical Hebrew labels.
2. **Translation drift between runs causes duplicate `Draft:` pages** with different Hebrew names for the same TM player. Fix is a `tm_id → he_name` state file + MovePage on drift (deferred until first reviewer-approval).
3. **Cargo template approval workflow** needs documenting + ideally automating (Cargo tables don't materialise until reviewer approves `Template:Cargo/*`).
4. **Bulk all-time review (2,680 pages) is overwhelming for a solo reviewer** → switching to season-by-season iteration cycles.

This session's work:

1. **Implement Wikidata-based translation lookup** — new `data_pipeline/wikidata_lookup.py` paralleling the existing `wikipedia_lookup.py`. Wire it into `auto_translate_hebrew._fill_section` ahead of the Wikipedia + Claude paths. Apply to **names + clubs + tournaments + nationalities** (not just names). Persist Wikidata Q-IDs in `mappings.he.yaml`. Expected coverage: ~70%+ players, ~95% clubs, ~99% tournaments, 100% countries.
2. **Test on a single 2024/25 season import** on local docker, verify quality of the new translations.
3. **Begin the per-season iteration cycle** — 2024/25 first, walking backwards. Each cycle: deep-reset (the now-extended `resetContent.php --scope=all`) → fresh translate via the new Wikidata chain → import → review on local wiki → fix templates / spiders / translations → repeat.
4. **Document findings + groom backlog** as the iteration cycle exposes issues.

## Documents to read in order (with one-line "why")

Read these first thing — don't skip:

1. `docs/research/0003-translation-overhaul-plan.md` — the plan for the work you're about to do. Sections 1 (Wikidata bridge), 2 (duplicate-page state file — defer), 3 (Cargo approval workflow), 4 (season-by-season recipe), 5 (open architectural questions).
2. `docs/operational-bootstrap.md` — §6 (ScraperAPI), §6a (`WIKI7_ANTHROPIC_API_KEY` isolation), §8 (multi-season recipe + `--force-rescrape` semantics), §9 (Cargo approval + `cargoRecreateData.php`).
3. `docs/research/0002-transfermarkt-data-surface.md` — TM data-surface inventory + the §1.1 German term glossary. Useful when discussing spider behavior or TM page names.
4. `docs/revival-plan.md` — full phase plan. Skim §4 Phase 3, especially the Phase 3a R2 sub-phase.

Then read the memory files via `[[memory-name]]`:

- `[[wiki7-revival-priorities]]` — sequencing + start-fresh policy
- `[[wiki7-translation-strategy]]` — what we learned about translation
- `[[wiki7-scraperapi-baseline]]` — credits per run shape (all-time = 7,893)
- `[[wiki7-aws-state]]` — what's deployed on prod (don't push to prod this session)
- `[[wiki7-secret-rotation]]` — how to roll secrets without breaking the container (if you ever need to)

## Concrete first actions

```bash
cd ~/dev/argamanza/wiki7
git checkout phase-3a-r2/implementation
git pull origin phase-3a-r2/implementation
git log --oneline master..HEAD | head -20    # should show 16 commits
cd data && uv run pytest                      # should show 249 passed
docker ps                                     # 3 containers up
echo "WIKI7 key set: ${WIKI7_ANTHROPIC_API_KEY:+yes}"
```

If `uv run pytest` fails with SSL errors on Wikipedia/Wikidata lookups, install `pip-system-certs`:

```bash
cd data && uv pip install pip-system-certs
```

(this is required on corporate-MITM networks; `uv sync` removes it if it's not in `pyproject.toml`).

Then start the Wikidata lookup module per `docs/research/0003-translation-overhaul-plan.md` §1.

## Operational gotchas (these all bit the previous session)

1. **`pip-system-certs` MUST be installed for Python `requests` to work on the corporate-MITM network.** Wikipedia/Wikidata API calls fail silently (returning `None`) when this is missing. Symptom: the lookup ran but resolved 0/N.
2. **Use `WIKI7_ANTHROPIC_API_KEY`, NOT `ANTHROPIC_API_KEY`.** The pipeline reads the wiki7-specific one first.
3. **`Bash` tool shells don't auto-source `~/.zshrc`.** When running the pipeline in a background bash, explicitly thread the key through: `export WIKI7_ANTHROPIC_API_KEY="$WIKI7_ANTHROPIC_API_KEY"` at the start of the command. Or `source ~/.zshrc` at the top.
4. **`docker-mediawiki-1` doesn't auto-reload `LocalSettings.php`** — `docker cp docker/LocalSettings.php docker-mediawiki-1:/var/www/html/LocalSettings.php` after edits to test. Full container rebuild only needed if Dockerfile changes.
5. **Cargo tables don't materialise until reviewer approves `Template:Cargo/*`** — see operational-bootstrap §9 for the recipe.
6. **`_import_single_page` was reporting "created" for every existing draft** before the step 10 fix. Don't re-introduce.
7. **The container `docker-db-1` uses `root` / `rootpass`** for direct MySQL queries (`MYSQL_ROOT_PASSWORD=rootpass` in `docker-compose.yml`). `docker exec docker-db-1 mysql -u root -prootpass wikidb -e "..."`.
8. **Local Admin password is `AdminPass1234`** (hardcoded in `docker-compose.yml`). Login at `http://localhost:8080/index.php?title=Special:UserLogin`.
9. **Reset script knows the deep-truncate now.** `docker exec docker-mediawiki-1 php /var/www/html/maintenance/run.php /var/www/html/extensions/Wiki7ReviewGate/maintenance/resetContent.php --scope=all --confirm` brings the wiki back to fresh-install state (16 edits, 0 archive / recentchanges / logging).

## Communication preferences (from `~/.claude/CLAUDE.md`)

- **No `Co-Authored-By: Claude` or AI-tool attribution** in commits or PR descriptions. Tzahi is firm on this.
- **Terse responses.** No trailing summaries. No "I'll now..." narration.
- **Pause-and-confirm for destructive ops on prod** — container recycle, CDK deploy, secret rotation, anything visible to public. Local docker is fair game.
- **Prefer `Edit`/`Read`/`Bash` over MCP** unless specifically called for.
- **Memory files** at `/Users/tzahi.argaman/.claude/projects/-Users-tzahi-argaman-dev-argamanza-wiki7/memory/` — update them when you learn something new.

## What's NOT in scope for this session

- **Prod push** — gates on iteration cycle reaching v1 quality (5-10 modern seasons reviewer-approved locally first).
- **Merging the PR B branch to master** — branch stays open as a working checkpoint. Eventual merge happens after iteration cycle wraps.
- **Phase 3b items** — reviewer-queue sub-special-pages, batch-promote, Telegram inline approve, IFA referee scraper, etc.
- **The `tm_id → he_name` state file** — deferred until a reviewer actually approves a draft.

## Reporting cadence

- After Wikidata lookup is implemented + tested on a small slice: brief report with the actual coverage rate (the 70%/95%/99%/100% expectations were extrapolations from spot-checks; real numbers matter for sequencing decisions).
- After first iteration cycle (2024/25) reaches "looks clean" on local: pause for review before walking back to 2023/24.
- After ~5 modern seasons reviewed: pause for a strategic check-in on whether the iteration-cycle approach is working.
- Don't touch prod, don't merge the PR. The session's deliverables are: Wikidata lookup module + tests + first season clean + findings recorded.
