# Wiki7 Revival Plan (2026-06)

> **Status:** Active planning document. Created 2026-06-04 after ~3 months of dormancy.
> Supersedes the root [`PLAN.md`](../PLAN.md) (kept as a task bank) and [`docs/roadmap.md`](roadmap.md) (historical).
> This is the canonical "what we're doing and in what order" document.

---

## 1. Where things stand (June 2026)

Wiki7 is a Hebrew RTL MediaWiki fan wiki for **הפועל באר שבע** ("ויקישבע"). It is a strong, mostly-working project that went dormant after a burst of work in late-Feb → early-Mar 2026. Last commit: `7c2905b`, 2026-03-07.

**Three subsystems:**
- **`docker/`** — MediaWiki 1.43, custom **Wiki7 skin** (a full fork of Citizen 3.1.0), content modeled with **Cargo** (declare → store → query) + infoboxes + collection pages. *Healthy.*
- **`data/`** — Python pipeline: Scrapy (Transfermarkt) → Pydantic → Jinja2 → mwclient, with auto-Hebrew translation. 85 tests (**3 currently failing** on stale assertions). *Works; never run end-to-end against a live wiki.*
- **`cdk/`** — AWS CDK (TypeScript), cost-optimized multi-stack design. *Clean code, but several security/config issues; the deployed stacks are torn down.*

**Production reality (verified via AWS `argamanza` profile, account 368127906643, il-central-1):**
- The "heavy" stacks (**RDS, ECS/Fargate, ALB, CloudFront, WAF, S3 media bucket**) were **torn down for cost**.
- ⚠️ **The production database is permanently lost** — zero RDS snapshots, empty backup vault. Direct consequence of the CDK `removalPolicy: DESTROY` + `deletionProtection: false` bug. Everything must be reproduced from git + the data pipeline.
- The **`wiki7.co.il` domain was renewed 2026-06-04**; its Route53 hosted zone survived teardown. It won't serve a page until the rebuild recreates DNS records.
- Current wiki7 spend ≈ **$0.50/mo** (hosted zone only).

**Bottom line:** this is a **clean-slate rebuild, not a restore** — which is why the strategy below is "start fresh + modernize first."

---

## 2. Guiding decisions (the strategy)

1. **Start fresh from a clean `master`.** Scrap the stale feature branches and open PRs; cherry-pick salvageable content from git history as each phase needs it. (Closing a PR loses nothing — commits remain on `origin`.)
2. **Local-first.** The free local Docker env is the iteration + testing surface (including the data pipeline, per `data/BOT_SETUP.md`). Prod is for "online / share / test on devices."
3. **Modernize first** *(decided 2026-06-04)*. Because this is a clean-slate rebuild, we upgrade **MediaWiki 1.43→1.45** and re-fork the skin from **Citizen 3.14** *before* relaunching and before the content push. The relaunched site is then the final platform from day one — no live migration later, and no content/templates/CSS authored on 1.43 needing post-upgrade rework (1.45 changes heading DOM, drops legacy media markup, moves to Codex).
4. **Balanced cloud-native architecture** *(decided 2026-06-04; see §5)*. Right-size and fix the existing CDK Fargate/RDS/CloudFront stack rather than replacing it with a hand-managed box — modern, IaC-pure, stable, with managed-DB data protection, at ~half the old cost.
5. **Backups are non-negotiable.** The data-loss event is the #1 lesson. The rebuilt stack must enable deletion protection, snapshot-on-delete, automated backups, and a **verified restore path** *before* it holds real content.

---

## 3. Branch & PR triage

All four open PRs get closed; salvage first. Nothing is destroyed (recoverable from `origin`).

| PR / branch | Action | Salvage before closing |
|---|---|---|
| **[#21](https://github.com/argamanza/wiki7/pull/21)** drawer-fixes-and-content-expansion | Close | The **~28 content wikitext pages + Cargo table/infobox templates + data-pipeline templates** → reused in Phase 3. **Discard** the skin patches (Drawer.less, dropdown.js, SkinHooks) — redone in Phase 1 on the new Citizen base. |
| **[#20](https://github.com/argamanza/wiki7/pull/20)** upgrade/mediawiki-1.45-citizen-3.14 | Close | Keep as the **MW 1.45 upgrade recipe** for Phase 1 (documents exactly what the upgrade + Citizen 3.14 re-fork entails). Redo fresh against the then-latest Citizen. |
| **[#19](https://github.com/argamanza/wiki7/pull/19)** content-sections-and-skin-enhancements | Close | Superseded by #21. Only the **"On this day" + "Rivalry"** content-template ideas are unique — grab if wanted. |
| **[#1](https://github.com/argamanza/wiki7/pull/1)** claude/plan-session | Close | Nothing — superseded by this document. |

**Stale local branches / worktrees to prune:** `feature/hebrew-localization`, `feature/modern-main-page`, `worktree-revert-pr`, `claude/sidebar-quick-icons-mxHrR`, plus 3 leftover worktrees under `.claude/worktrees/` and `~/.cursor/worktrees/`.

**WIP — done ✅:** the untracked Did-You-Know (`הידעת`) / Fan-Culture (`תרבות אוהדים`) work was preserved to branch **`archive/wip-content`** (commit `5d84083`) on 2026-06-04, to be re-curated in Phase 3.

---

## 4. The phased roadmap *(modernize-first order)*

### Phase 0 — Clean slate & alignment  *(substantially complete, 2026-06-04)*
*Goal: a clean, trustworthy baseline + a free local test env.*
- [x] Preserve untracked WIP to `archive/wip-content` (commit `5d84083`).
- [x] Commit planning docs to the main line — branch `docs/revival-planning`, **PR #22**.
- [x] Verify the data-pipeline suite on master: **85/85 green**. (The "3 failing tests" exist only on the #21 content branch — templates were expanded, tests not updated → moved to **Phase 3**.)
- [x] Close PRs #1, #19, #20, #21 (with comments; branches **kept** as salvage sources). Pruned stale worktrees; deleted merged branch `feature/modern-main-page`.
- [x] Local smoke test: `cd docker && docker compose up` from `master` — wiki boots cleanly (confirmed 2026-06-04).
- [ ] CI enforcement (re-timed, not a gap): the **test** steps already block. **ruff** → enforce with the Phase 3 pipeline fixes (its 7 issues overlap the broken transfers spider). **PHP/JS** lint → enforce after the Phase 1 skin re-fork (runs on the skin we replace).
- **Salvage-source branches kept on purpose:** `feature/content-sections-and-skin-enhancements` (#19), `upgrade/mediawiki-1.45-citizen-3.14` (#20), `feature/drawer-fixes-and-content-expansion` (#21), `archive/wip-content`. Optional later cleanup: `claude/sidebar-quick-icons-mxHrR`, `worktree-revert-pr`, `feature/hebrew-localization`.
- **Exit:** ✅ **Phase 0 complete** — green tests, clean baseline, local wiki boots, planning docs in PR #22, stale PRs closed.

### Phase 1 — Modernize the base (local)  *(decided to do first)*
*Goal: the local wiki runs on the final, modern platform before we invest in relaunch/content/design.*
- [ ] MediaWiki **1.43 → 1.45** (Docker base image + `update.php`), using PR #20 as the recipe.
- [ ] Re-fork the Wiki7 skin from the latest **Citizen** (3.14+); re-apply brand-red theming, drawer footer, Hebrew fonts, RTL fixes.
- [ ] Bump Cargo / PageForms submodules to MW-1.45-compatible versions.
- [ ] Update `LocalSettings.php` for 1.45; verify all extensions load and Cargo tables work.
- [ ] Validate locally: main page, VisualEditor, Cargo queries, search.
- **Exit:** local wiki runs clean on 1.45 + upgraded skin; visual sanity-check passes.

### Phase 2 — Cheap + safe relaunch (modern stack)  *(priorities #1 + #2)*
*Goal: the modern site is back online at wiki7.co.il, cheap, hardened, with working backups.*
- [ ] Fix the CDK stack: **RDS deletion protection + snapshot-on-delete + automated backups**, S3 public-access lockdown, WAF bot-rule ordering, MariaDB →10.11/11.x, SSL to DB, CloudFront static-asset caching, Graviton + optional Fargate Spot, auto-heal/right-size, remove stale CDK v1 deps. (See `PLAN.md` Stage 1 for the full checklist.)
- [ ] **Verified backup + restore drill** before importing real content.
- [ ] Deploy the MW-1.45 image; reimport seed pages (`docker/import-pages.php`); create the `wiki7.co.il` A/alias records; confirm the registrar delegates to the zone's 4 nameservers; validate HTTPS + main page.
- **Exit:** modern site reachable over HTTPS at wiki7.co.il; a test restore succeeds; monthly cost at target (~$30–45/mo).

### Phase 3 — Content + data pipeline + finalize design  *(priority #3 — "what matters")*
*Goal: real, correct content; the full pipeline run end-to-end at least once; the design "done."*
- [ ] Stand up the bot account and **run the full data pipeline end-to-end** against local → prod for the first time (`data/BOT_SETUP.md`).
- [ ] Fix pipeline correctness gaps: match records missing `season`, brittle lineup/match-event extraction, empty club spiders (coaches / club transfers).
- [ ] **Human-review the auto-Hebrew translations** before publishing (known quality issues).
- [ ] Re-curate the major content sections with fresh eyes: Did You Know, Fan Culture, kits, anthems, museum, records — using `archive/wip-content` as reference.
- [ ] Fill homepage `TODO` placeholders (current manager/captain, real featured image, attributed quote).
- [ ] **Finalize the visual design** on the modern skin (all design polish lives here).
- [ ] Decide on **PageForms** (wire up editor forms, or remove if unused).
- **Exit:** pipeline runs clean end-to-end; core content correct; design done; no placeholder TODOs.

### Phase 4 — Ops & automation  *(cross-cutting)*
*Goal: it runs itself and is documented.*
- [ ] CloudWatch alarms + an external uptime check.
- [ ] Automate the data pipeline (scheduled run) with a dry-run → review → import gate.
- [ ] Write `docs/runbook.md` (deploy, rollback, restore, run pipeline) and `CONTRIBUTING.md`.
- [ ] Populate `BACKLOG.md` from the task bank below.

---

## 5. Cost & architecture *(decided — balanced cloud-native)*

The old design was ECS Fargate + ALB + RDS + CloudFront + WAF ≈ **$65–95/mo** — torn down for cost. The rebuild keeps the cloud-native shape (reusing the existing, well-written CDK) but fixes and right-sizes it:

**Chosen architecture (~$30–45/mo, ≈ half the old cost):**
- **Compute** — ECS Fargate, 1 task, **Graviton/ARM**, small (0.25 vCPU / 0.5–1 GB), optional **Fargate Spot** (~70% compute saving; CloudFront caching hides the rare restart).
- **Database** — RDS MariaDB (→10.11/11.x LTS), `t4g.micro`, single-AZ, **deletion protection + snapshot-on-delete + automated backups + PITR**, encrypted, SSL. *Managed DB = robust data-loss protection.*
- **Speed + protection** — CloudFront (TLS, caches static `load.php`/`skins`/`extensions`/`images` → fast; free AWS Shield Standard DDoS) → ALB (the unavoidable ~$18/mo for stable Fargate ingress in il-central-1).
- **Security** — trimmed WAF (Common + KnownBadInputs + SQLi + PHP), geo-block, rate-limit; S3 locked down (OAC).
- **Backups** — RDS automated + AWS Backup; **verified restore** before content.

**Why not the alternatives:** a single small instance (~$15–25/mo) is cheaper but trades away managed-DB safety, elasticity, and "future-ready" — it's the fallback if cost must drop further. App Runner isn't available in il-central-1; Lightsail is less future-ready / region-uncertain.

---

## 6. Decisions

**Locked (2026-06-04):**
- **Sequencing:** modernize-first (Phase 1 before relaunch/content).
- **Architecture:** right-size + fix the existing CDK Fargate/RDS/CloudFront stack (~$30–45/mo). *Override to single-instance still possible if cost must drop.*
- **Domain:** `wiki7.co.il` (renewed 2026-06-04). Verify registrar delegates to the zone's 4 nameservers; records recreated in Phase 2.
- **WIP:** preserved to `archive/wip-content` (`5d84083`).

**Still open:**
- Final thumbs-up on the architecture (or switch to single-instance).
- Where to commit these planning docs (main line vs. a docs branch).

---

## 7. Task bank — known issues by area

The detailed infra fixes live in [`PLAN.md`](../PLAN.md) Stage 1 (still a valid checklist).

**Security / infra (Phase 2):** S3 `BlockPublicAccess` all disabled; RDS `DESTROY` + no deletion protection (caused the data loss); WAF bot-allow ordered after bot-block (Googlebot blocked); MariaDB 10.5 (EOL); ALB HTTP-only; no autoscaling; hardcoded S3 bucket name; stale CDK v1 deps; insecure `WG_SECRET_KEY`/`WG_UPGRADE_KEY` dev fallbacks.

**MediaWiki / skin (Phase 1 + 3):** Wiki7 is a full copy of Citizen (no clean upstream-merge path); PageForms installed but unused; homepage TODO placeholders; Records/season pages query Cargo tables only the pipeline creates; `seasons` table declared but unqueried; missing Season / Fan-story infoboxes.

**Data pipeline (Phase 0 + 3):** 3 failing tests; matches carry no `season`; lineup/match-event extraction brittle; coaches / club-transfers spiders return empty; auto-Hebrew quality (manual review required); free Google Translate dependency is fragile; pipeline never run end-to-end live; only 2024 season materialized.

**Docs / CI (Phase 0 + 4):** `architecture.md` was fictional (now fixed); no CONTRIBUTING/runbook; `BACKLOG.md` is a stub; CI was advisory-only.

---

## 8. Reference

- AWS: profile `argamanza`, account `368127906643`, primary region `il-central-1`, CloudFront/cert/WAF in `us-east-1`.
- `wiki7.co.il` zone `Z05358991AQ467TZUH2N6` → NS: ns-1806.awsdns-33.co.uk, ns-1370.awsdns-43.org, ns-662.awsdns-18.net, ns-327.awsdns-40.com.
- Local dev: `cd docker && docker compose up` (wiki :8080, Adminer :8081). Pipeline + bot setup: `data/BOT_SETUP.md`.
- WIP archive: branch `archive/wip-content` (`5d84083`).
