# Next-session prompt — Pattern B in flight

> Paste the body of this document (everything below `---`) into the first message of a fresh Claude Code session at `~/dev/argamanza/wiki7/`. The session will read the linked docs + memory files itself and pick up the work without rewarm cost.

---

## Context

**wiki7** is a personal hobby project — a Hapoel Beer Sheva fan wiki at https://wiki7.co.il ("ויקישבע"). MediaWiki 1.45.3 on a single Graviton EC2 + RDS (~$50/mo), with a custom Wiki7 skin (fork of Citizen 3.17), a Phase-3.5 review-gate (`Wiki7ReviewGate` extension + Lockdown + Approved Revs + Echo + Telegram), and a Python data pipeline (Scrapy + Pydantic + Jinja + mwclient + Claude API for Hebrew translation). It's cost-sensitive — operator (Tzahi) tore prod down once for cost, recurring fear of accidentally re-doing that.

## State at the start of THIS session

Branch `phase-3a-r2/implementation` — pushed to origin, working tree clean, **pytest passing**, **CDK Jest 57/57**, **ruff blocking + clean**.

**2026-06-12/13 review rounds are fully closed** (full-project review + reviewer-pass + verification-pass). Pattern A is shipped end-to-end including the redirect-aware resolver. PR #65 against master carries the DR-script source-DB filter fixes (`dr-test.sh` + `dr-restore.sh`), awaiting your merge.

**Concrete correctness patterns from those rounds are captured at memory entry [[wiki7-reviewer-pass-lessons]] — READ IT BEFORE writing any new pipeline / bot save / scraper code.** Especially:
- Python logging filter semantics (filters on a LOGGER don't see propagated child records; install on HANDLERS).
- `Auto-import:` summary prefix is the system-wide "was this ours" signal — reuse, don't reinvent.
- `wiki_import.page_router.resolve_redirect(site, full_title) -> (final_title, was_redirect)` is the shared resolver — code that's about to write to or merge into a page MUST run this first.
- Fail-CLOSED for production-environment checks.
- "response.url is the proxy URL when proxied" — don't persist it; don't `response.urljoin` against it.

## What this session should do

**Mission, in order:**

1. **Pattern B slices (in flight as of 2026-06-13):**
   - **B.1** — `mwparserfromhell`-based surgical wikitext merger. Build on `resolve_redirect` as the shared resolver — surgical-merging onto a redirect is nonsense; merge target is always the resolved content page. Key the merger's "is this ours" discrimination on the `Auto-import:` author/summary-prefix convention `import-pages.php` already uses (same signal, same prefix string). NEW reviewable slice expected here.
   - **B.2** — HTML comment markers (`<!-- wiki7-bot-managed-section start: X -->` … `end: X`) on each bot-rendered section so the merger has stable anchors to splice against. Bot edit summaries carry the `Auto-import:` prefix.
   - **B.3** — wire the merger into `import_players._edit_page` (and any peer save paths).
   - **B.4** — Wiki7ReviewGate `PageMoveComplete` handler. Per operator decision (2026-06-13): notify on BOT moves only, stay silent on reviewer moves. Reuse the same bot-group check the existing `PageSaveComplete` handler uses. When B.3 wires the merger into save calls, treat the move-notification as INDEPENDENT of the content-change notification — don't double-fire for a single logical bot action; pick whichever best represents "a human needs to look at this".
2. **Final docs + memory pass** once B lands: capture the merger's architecture in `docs/architecture.md` (or an ADR), update memory entries to reflect the live state.
3. **Walk preparation** — once Pattern B is live, regenerate the 2024/25 pages, do the multi-season validation (≥5 seasons across 2019-2024 with optional 2014-2018 expansion), prepare for the reviewer walk.

## Critical pointers

- **Branch**: `phase-3a-r2/implementation`. **Do not merge to `master`. Do not push to prod.**
- **Commit hygiene**: NO `Co-Authored-By: Claude` or any AI attribution. Read the global CLAUDE.md for hard rules.
- **Pattern A is shipped + redirect-aware** — `resolve_redirect` lives in `wiki_import.page_router`. Use it.
- **The handoff brief for Finding #4** (TM career-stats discovery, paused) is at `data/docs/finding-4-handoff.md`. Not in scope.

## Quick reference

- **Wiki admin password (local docker)**: `Admin / AdminPass1234` (per `docker/docker-compose.yml`).
- **MediaWiki URL pattern**: `http://localhost:8080/` (no `/wiki/` prefix).
- **AWS profile**: `argamanza` (~/.aws/credentials).
- **Anthropic API key**: `WIKI7_ANTHROPIC_API_KEY` env var (NOT `ANTHROPIC_API_KEY`).
- **ScraperAPI key**: `SCRAPERAPI_KEY` env var; `USE_SCRAPERAPI=True` (fails fast if missing).
- **Run tests**: `cd data && uv run pytest`. `cd cdk && npm test`.
- **Lint**: `cd data && uv run ruff check .` (blocking in CI).
