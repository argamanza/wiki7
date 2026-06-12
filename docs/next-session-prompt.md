# Next-session prompt — Phase 3a R2 reviewer walk + Pattern B

> Paste the body of this document (everything below `---`) into the first message of a fresh Claude Code session at `~/dev/argamanza/wiki7/`. The session will read the linked docs + memory files itself and pick up the work without rewarm cost.

---

## Context

**wiki7** is a personal hobby project — a Hapoel Beer Sheva fan wiki at https://wiki7.co.il ("ויקישבע"). MediaWiki 1.45.3 on a single Graviton EC2 + RDS (~$50/mo), with a custom Wiki7 skin (fork of Citizen 3.17), a Phase-3.5 review-gate (`Wiki7ReviewGate` extension + Lockdown + Approved Revs + Echo + Telegram), and a Python data pipeline (Scrapy + Pydantic + Jinja + mwclient + Claude API for Hebrew translation). It's cost-sensitive — operator (Tzahi) tore prod down once for cost, recurring fear of accidentally re-doing that.

## State at the start of THIS session

Branch `phase-3a-r2/implementation` — pushed to origin, working tree clean, **410/410 pytest** + **57/57 CDK Jest** passing, ruff clean (now blocking in CI).

**Substantial work landed since master diverged**, with the high-level shape:

1. Findings #1, #2, #3 from the 2024/25 review walk (Wikidata coverage gaps, `(כדורגל)` paren suffix, youth-career section). Single-season-validated; multi-season validation pending.
2. **2026-06-12 full-project review (master commits `42c2fcc` etc., see `docs/reviews/2026-06-12-full-project-review.md`)** — merged into the branch + every actionable §6 finding addressed. Crit-class items shipped first; high + medium triaged with explicit "accepted-for-now" notes for the three items deferred to follow-up PRs (per-season mapping consolidation, in-template Cargo gating, wikitext escaping for scraped names).
3. **DR drill blessed** end-to-end against real AWS (`scripts/dr-test.sh` fix cherry-picked to master as **PR #65**, awaiting your review).
4. **One small PR open against master**: PR #65 — `dr-test.sh` source-DB filter exclusion. Operators run dr-test from master; the fix shouldn't wait for 3a-R2 merge.

The branch is **functionally ready for the next iteration-cycle walk** but the operator wants to do that walk in fresh context, against the regenerated 2024/25 pages with all the corrections live.

## What this session should do

**Mission, in order:**

1. **Pattern B (tasks #23–#26 from the prior session's queue)** — the deferred work block:
   - **B.1** — `mwparserfromhell`-based surgical wikitext merger so reviewer-edited content survives bot rewrites by section, not by total content hash. This was the architectural piece deferred from PR B step 12 to give the iter-cycle review walk priority.
   - **B.2** — HTML comment markers (`<!-- wiki7-bot-managed-section start: X -->` … `end: X`) on each bot-rendered section so the merger has stable anchors to splice against. Bot edit summaries should reflect "Patched section X, preserved Y".
   - **B.3** — wire the merger into `import_players._edit_page` (and any peer save paths) so it actually fires on every save, not just when the reviewer has demonstrably touched the page.
2. **Final docs + memory pass** (task #26 from the prior queue):
   - Capture the new architecture (`mwparserfromhell`, comment markers, edit-summary format) in `docs/architecture.md` or a new ADR.
   - Memory entries — update `wiki7-revival-priorities`, `wiki7-translation-strategy`, add anything new from this session.
3. **Walk preparation** — once Pattern B is live, regenerate the 2024/25 pages, do the multi-season validation (≥5 seasons) the Findings #1–#3 work didn't have, and prepare for the next reviewer walk with Tzahi.

## Critical pointers

- **Branch**: `phase-3a-r2/implementation`. **Do not merge to `master`. Do not push to prod.**
- **Commit hygiene**: NO `Co-Authored-By: Claude` or any AI attribution. Read the global CLAUDE.md for the full hard rules.
- **The review doc on master is the authoritative correctness reference**: `docs/reviews/2026-06-12-full-project-review.md`. The Pattern B work touches the page-save path; verify nothing in that file flags a regression risk in your design.
- **DR drill is now blessed** (script PR #65 + the run that fixed it landed in `f23b462` on this branch). Don't re-run unless something material changes.
- **The handoff brief for Finding #4** (TM career-stats discovery, paused) is at `data/docs/finding-4-handoff.md`. Not in scope for this session — Tzahi will revisit when he chooses, possibly with a stronger model.

## Quick reference

- **Wiki admin password (local docker)**: `Admin / AdminPass1234` (NOT `Wiki7Admin!23`, which lived in earlier handoff revisions and was incorrect).
- **MediaWiki URL pattern**: `http://localhost:8080/` (no `/wiki/` prefix).
- **AWS profile**: `argamanza` (~/.aws/credentials).
- **Anthropic API key**: `WIKI7_ANTHROPIC_API_KEY` env var (NOT `ANTHROPIC_API_KEY` — kept separate to isolate from Claude Code's subscription).
- **ScraperAPI key**: `SCRAPERAPI_KEY` env var; `USE_SCRAPERAPI=True` in `tmk-scraper/tmk_scraper/settings.py` (fails fast if key missing, as of §6 ② fix).
- **Run tests**: `cd data && uv run pytest` (1.5s, 410 passing). `cd cdk && npm test` (~18s, 57 passing).
- **Lint**: `cd data && uv run ruff check .` (now blocking in CI).
