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
4. **Right-sized cloud-native architecture** *(decided 2026-06-04, re-decided 2026-06-06; see §5)*. Modern, IaC-pure, stable, with managed-DB data protection. *Originally* planned as Fargate+ALB ("Option A", ~$63/mo); switched to single Graviton EC2 + managed RDS ("Option B", ~$47/mo) mid-Phase-2 after honest re-examination of the actual workload. Migration path back to Fargate preserved at the `archive/option-a-fargate-alb` git tag.
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

### Phase 1 — Modernize the base (local)  *(complete, 2026-06-05; PR [#23](https://github.com/argamanza/wiki7/pull/23))*
*Goal: the local wiki runs on the final, modern platform before we invest in relaunch/content/design.*
- [x] MediaWiki **1.43 → 1.45.3** (Docker base image + `update.php` in `docker-entrypoint.sh`), using PR #20 as the recipe.
- [x] Re-fork the Wiki7 skin from the latest **Citizen v3.17.0** — fresh tarball, scripted Citizen→Wiki7 rename, brand deltas surgically re-applied. Full inventory + re-fork recipe lives in [`docs/wiki7-skin-customization.md`](wiki7-skin-customization.md).
- [x] Bump Cargo (3.9.1) / PageForms (REL1_45 tip `85a09be`) submodules. Pin the two unpinned extensions in the Dockerfile via build ARGs — AWS S3 → `v0.14.0`, TabberNeue → `v3.4.1` — so the build is fully reproducible.
- [x] Update `LocalSettings.php` for 1.45 (TabberNeue, `wgWiki7HeaderPosition`, `wgWiki7DrawerFooterLinks`); all 18 extensions load and Cargo schema migrations succeed.
- [x] Validate locally (`docker compose up` from `phase1/modernize-mediawiki`): main page renders (Hebrew RTL), brand-red tokens served (`hsl__h:350`, `oklch__h:23 c:0.195 l:46%`), drawer footer + social-icon mask images render, Cargo API responsive, VisualEditor API responsive, Search 200.
- [x] Add a server-rendered "you are here" indicator on the drawer's main menu (`SkinHooks::markActiveSidebarItem`) — brand-red active row, subtle hover on inactive rows, matching the social-link affordance pattern.
- [x] Visual diff against current master and iterate: red sidebar, social-icon rendering, hover/active/open states on rail toggles, logo + home-overlay interaction, RTL drawer animation origin — all addressed across commits `02a7d4c` → `a91256d`.
- **Exit:** local wiki runs clean on 1.45.3 + Citizen-3.17-based Wiki7; smoke tests green; visual diff vs master is equivalent or better; PR open into master, ready to merge.

### Phase 2 — Cheap + safe relaunch (modern stack)  *(complete, 2026-06-06; PR [#24](https://github.com/argamanza/wiki7/pull/24))*
*Goal: the modern site is back online at wiki7.co.il, cheap, hardened, with working backups.*
- [x] **Architecture rethink (2026-06-06):** the "balanced cloud-native" Fargate + ALB plan was implemented in full but came in at ~$63/mo (~50% over target). Re-examined the four properties (reliable, fast, modern, secure) against the actual workload and switched to a **single Graviton EC2 + managed RDS** at **~$47/mo**. The Option A implementation is preserved at the [`archive/option-a-fargate-alb`](https://github.com/argamanza/wiki7/commit/3c96252) tag. Full reasoning + migration-back path in [`docs/adr/0001-single-ec2-vs-fargate-alb.md`](adr/0001-single-ec2-vs-fargate-alb.md).
- [x] Harden the CDK: **RDS deletion protection + snapshot-on-delete + automated backups** (the #1 lesson from the prior teardown), MariaDB 10.5 → 11.4.9 LTS, t3 → t4g.micro Graviton, dedicated database SG; S3 `BLOCK_ALL` + `BUCKET_OWNER_ENFORCED` + no `s3:PutObjectAcl`; **WAF rule ordering fix** (Googlebot was being blocked) + re-add SQLi/PHP managed rule sets + expanded crawler allow list; CloudFront static-asset caching for `/load.php`, `/skins/*`, `/extensions/*`; IPv6 AAAA records; remove the stale `postdeploy` script.
- [x] **Verified backup + restore drill (2026-06-06):** on-demand snapshot of the production RDS → restore to a temp `t4g.micro` instance in the same VPC/SG → connect via the EC2 wiki container → `SHOW TABLES;` returns the full MW schema (`page`, `revision`, `user`, `cargo_*`, `echo_*`, …); 15 pages including the Hebrew main page (`עמוד_ראשי`) and the seed-page templates. Temp instance + snapshot torn down.
- [x] Deploy the MW-1.45.3 image (built ARM64 by CDK → ECR → EC2 UserData pulls + runs); seed pages auto-imported via the existing `docker-entrypoint.sh` → `import-pages.php` flow on first boot; CloudFront A/AAAA records created for apex + www; the `ec2.wiki7.co.il` A-record pinned to the static EIP for stable origin DNS; HTTPS confirmed: `https://wiki7.co.il` returns 200, Wiki7 skin renders, `www → apex` 301 redirect works, `api.php?action=query&meta=siteinfo` reports MediaWiki 1.45.3 + PHP 8.3.31 + MariaDB 11.4.9.
- [x] **Performance follow-up (2026-06-06):** Redis sidecar (256 MB cap, `allkeys-lru`, no persistence) added to the EC2 via UserData, sharing a docker bridge with the MW container; PECL `redis` PHP extension installed in the Dockerfile; `LocalSettings.php` wires `RedisBagOStuff` as the backing store for `$wgMainCacheType` / `$wgParserCacheType` / `$wgMessageCacheType` / `$wgSessionCacheType`. Verified live: ~9× speedup on warm parser-cache hits (1.22s → 0.13s). If Redis dies the wiki transparently falls back to the DB. *(Commit `42dd169`.)*
- [x] **Post-relaunch hardening (2026-06-06, follow-up):** observability + ops + SEO foundations + CI polish landed as one batch on PR #24 (commits `307d8c4` → `6dafd20`):
    - **Observability** — new `ObservabilityStack` construct: 6 CloudWatch alarms (RDS storage <5 GB, RDS CPU >85 %, EC2 CPU >85 %, CloudFront 5xx >5 %, MW app errors >5 in 5 min via log metric filter, Redis sidecar down via tighter RedisException filter); two log metric filters back the last two; alarms have no SNS actions yet (Phase 4 wiring).
    - **OS patching** — SSM Patch Manager weekly window Sun 02:30 IDT (Sat 23:30 UTC), AWS-RunPatchBaseline + RebootIfNeeded against the single instance.
    - **Threat detection** — account-level GuardDuty detector (~$3-5/mo, 15-min finding frequency).
    - **Backups** — monthly long-retention rule (1st 02:00 UTC, 365-day retention) alongside the existing daily/7-day rule, ~$0.50/mo.
    - **CloudFront** — `PriceClass_200` (drops SA/AF/AU edges, no IL latency impact) + `HTTP2_AND_3`.
    - **RDS windows** — maintenance + automated-backup windows moved to Fri 22:00-23:30 UTC (Sat early morning IDT, Israeli weekend).
    - **Redis observability** — sidecar now ships logs to the shared CloudWatch group under stream `redis`.
    - **SEO foundations** — Description2 + OpenGraphMeta extensions installed (REL1_43 branches; emit canonical/OG/Twitter meta + per-page description from article intro), static `robots.txt` shipped (the .htaccess rewrite was silently routing /robots.txt → /Robots.txt → MW article 404; verified broken on the live site before this fix); `Wiki7-GenerateSitemap` SSM document for on-demand sitemap generation → S3 (`/assets/sitemap/sitemap-index-wikidb.xml`) → CloudFront; Search Console TXT record claimed and verified (token `yhlEnlTFpaEYo68oJOcHkyfyGN7i2QU-M4foSvnglEY` in `cdk/cdk.json` context); UptimeRobot monitor configured (and the priority-8 WAF bot-heuristic block adjusted to allow `uptimerobot` in the UA — the existing rule would have caught the bot keyword).
    - **CI** — `cdk-diff.yml` now posts the diff as a sticky PR comment via `marocchino/sticky-pull-request-comment`.
- [x] **SEO + social-meta sweep (2026-06-06, PRs #25-#34):** the post-relaunch hardening shipped "SEO foundations" but those turned out to need several rounds of iteration before the share-preview surface was actually right. Sequenced as: workflow hygiene + RDS backup-window format fix + Node-24 action bump + EC2 termination-protection footgun fix (PRs #25-#27) → OpenGraphMeta REL1_43 wfExpandUrl 500 caught by user → drop-and-reimplement attempt closed in favour of migrating to **WikiSEO** REL1_45 (PR #29) → opengraph.xyz audit drove iterative fixes (PR #30: absolute og:image URL, `WikiSEOPreAddMetadata` hook for site-wide defaults; PR #31: brand-forward titles `'ויקישבע - אנציקלופדיית הפועל באר שבע'` / `'<page> - ויקישבע'`, Schema.org logo via the `$wgLogos` PNG-extension workaround (`getWikiLogo` filters by extension and rejects SVG), 1200x630 brand-red share image, full favicon set incl. SVG variant, hyphen-only dashes site-wide; PR #32: HTML `<title>` force-set via a `BeforePageDisplay` hook because WikiSEO's `modifyPageTitle` runs before `WikiSEOPreAddMetadata`, with `page_props['title']` lookup so per-page `{{#seo:title=...}}` overrides survive). Sitemap path bugs surfaced on the first manual generation and were fixed in PR #33 (urlpath must be a path not a full URL; S3 destination needs the `assets/` prefix to match the CloudFront `/assets/*` route) and PR #34 (`updateMethod='NewVersion'` so the SSM document can be updated without CFN rolling back). Live verification: all OG/Twitter/canonical/favicon/Schema.org/Sitemap surfaces correct as of 2026-06-06.
- **Scope note (post-audit 2026-06):** the SEO/social-meta *polish* (favicons, brand-augmented titles, 1200×630 share image, Schema.org logo — PRs #30–#32) was pulled forward from Phase 3; only the *foundations* (WikiSEO/Description2 swap, robots.txt, sitemap pipeline — #29/#33/#34) were strictly Phase 2. Harmless and booked honestly (the Phase 3 favicon item is struck through as done) — recorded so the "polish belongs in Phase 3" guardrail's exception is on the record.
- **Exit:** ✅ modern site reachable over HTTPS at https://wiki7.co.il; restore drill succeeded; observability + patching + threat detection + full SEO/social-meta surface wired; monthly cost ~$47-52/mo (above the original $30–45 band but within the rebalanced expectation; the +$3-5 is GuardDuty, see ADR 0001).

### Phase 2.5 — Pre-Phase-3 hardening  *(planned 2026-06; from the post-Phase-2 infra review)*
*Goal: close the genuine config/ops gaps the infra review surfaced — especially the two that affect Phase 3 content/Cargo work — before pouring in content. The **architecture itself is sound and unchanged** (single Graviton EC2 + managed RDS, see §5 / ADR-0001); these are config/ops fixes, not a re-architecture. Implementation is a dedicated, local-first session + its own PR, then a controlled prod deploy.*

**In this pass (before Phase 3) — implemented 2026-06-06, awaiting prod deploy:**
- [x] **MediaWiki job runner** — `$wgJobRunRate = 0` in `LocalSettings.php`; a host-side `cron.d` entry in the EC2 UserData runs `docker exec wiki7 php maintenance/run.php runJobs --maxtime=55` every minute (AL2023 ships without cron; the UserData now also installs `cronie` and enables `crond`). Validated locally: with `$wgJobRunRate = 0` the queue accumulated 66 jobs after first boot (refreshLinks + htmlCacheUpdate + CategoryCountUpdateJob + …) and a single `runJobs.php --maxtime=30` invocation drained it to zero.
- [x] **CDN-aware MediaWiki** — `$wgUseCdn = true` for `s-maxage` emission; CloudFront's origin request policy switched from `ALL_VIEWER` to the managed `ALL_VIEWER_AND_CLOUDFRONT_2022` so origin requests now carry the unspoofable `CloudFront-Viewer-Address` header (set by CloudFront from the actual TCP viewer connection, can't be overridden by a client). `LocalSettings.php` overwrites `$_SERVER['REMOTE_ADDR']` from this header before MW boots — handles IPv4 `"1.2.3.4:port"` and IPv6 `"[2001:db8::1]:port"`. Validated locally with all four cases (IPv4, IPv6, missing header passthrough, defensive no-port). Deliberately **not** setting `$wgCdnServersNoPurge`: CloudFront's edge ranges are dynamic (the `AmazonIpSpaceChanged` SNS topic publishes updates), hard-coding a snapshot would silently rot, and overriding `REMOTE_ADDR` directly makes the trusted-proxy list unnecessary anyway.
- [x] **SNS on the 6 alarms** — one `wiki7-alarms` topic with an email subscription (address read from `cdk.json` context `alarmEmail`, hard-fail if missing). All 6 alarms (`RdsFreeStorageLow`, `RdsCpuHigh`, `Ec2CpuHigh`, `CloudFront5xxHigh`, `AppErrorRateHigh`, `RedisSidecarDown`) wired via `addAlarmAction`. First deploy will trigger the SNS confirm-subscription email (one click).
- [x] **CloudWatch dashboard** — single `wiki7` dashboard, free within the 3-dashboard tier. Top row: `AlarmStatusWidget` covering all 6 alarms + a graph of RDS CPU / EC2 CPU / RDS free storage. Bottom row: CloudFront 5xx % + the MW error and Redis-exception log-metric counts. Pure existing-metric plumbing — no custom metrics, no Lambda, no extra cost.
- [x] **CloudFront `PriceClass_200` -> `PriceClass_100`** — done. PriceClass_100 covers NA + EU + Israel; the dropped 200-only POPs (Asia / India / extra ME) served traffic we don't have.
- **Exit:** ✅ 5/5 implemented + locally validated + CDK tests green + `cdk diff` clean (EC2 instance replaces on UserData change; SNS topic + dashboard added; CloudFront flips priceClass + originRequestPolicy; all 6 alarms get `AlarmActions`). Awaiting controlled prod deploy + on-prod verification (RecentChanges shows real IPs, alarm email confirmation, dashboard renders, queue draining).

**Phase 2.5b — actual edge caching of MW HTML** *(planned, immediately after #38 validates on prod; before Phase 3)*
*Surface noticed during the #38 review:* `$wgUseCdn = true` is now correct on the MW side, but CloudFront's default behavior is still `CachePolicy.CACHING_DISABLED` — MinTTL=MaxTTL=DefaultTTL=0 — so the `s-maxage` MW now emits is silently ignored at the edge and every dynamic page still hits the origin. The fix is its own design problem with a quiet failure mode, so it's a deliberate follow-up rather than a sixth bullet on this PR.
- **Why now (right after #38), not Phase 4:** the COST of getting cache correctness wrong (stale logged-out UI served to a logged-in editor) scales with traffic and with edit frequency. Phase 3 lands the data-pipeline bot, which is edit-heavy. We want the cache shape settled BEFORE that traffic pattern starts, on a near-zero baseline where misbehavior is easy to spot, not during Phase 3 when content correctness and cache correctness would be entangled. Aligns with the same "modernize-first / finish the platform before content" sequencing as Phase 1.
- **Scope sketch (will research before implementing):**
    - A `Wiki7DynamicHtml` cache policy that honors origin TTL (MinTTL=0, MaxTTL=1d, DefaultTTL=0 → CloudFront trusts the `s-maxage` MW emits).
    - Cookie-aware bypass: MW session cookies (`<prefix>UserID`, `<prefix>UserName`, `<prefix>Token`, `<prefix>_session` per `$wgCookiePrefix`) included in the cache key so anon and logged-in users get separate cache entries — or hashed into a "bypass" key. Enumerate cookies precisely; over-broad cookie keying defeats caching.
    - Edit invalidation: either CloudFront `CreateInvalidation` on edit (Lambda + IAM, low volume on a 1-author wiki = effectively free), or accept the 5-hour `$wgCdnMaxAge` staleness, or shorten `$wgCdnMaxAge`. Decide based on edit cadence and observed cache-hit benefit.
    - Switch the default behavior off `CACHING_DISABLED` onto the new policy; leave the static-asset behaviors as-is (already correct).
- **Validation:** synthetic anon GETs vs logged-in GETs through CloudFront; `Age:` header observation; "edit page → expected staleness window → invalidation behaves" smoke test.
- **References:** `cdk/lib/cloudfront-stack.ts` (default behavior + cache policies), `docker/LocalSettings.php` (the `$wgUseCdn = true` block has the inline note pointing here), [Manual:CloudFront](https://www.mediawiki.org/wiki/Manual:CloudFront), [Manual:Performance tuning](https://www.mediawiki.org/wiki/Manual:Performance_tuning).
- **Exit:** anon edge cache hits visibly, no stale-logged-in-UI regression, edit-to-fresh time matches design choice (immediate via invalidation OR bounded by `$wgCdnMaxAge`); then Phase 3 starts.

**Deferred -> Phase 4 (recorded with reasoning, per the review):**
- **OPcache tuning + APCu local tier** — relies on the stock image OPcache defaults (128 MB / 10k files), no APCu. *Defer:* pure performance, invisible at ~1 user/day; tune when content/traffic grows.
- **`$wgMainStash` -> Redis** (defaults to `CACHE_DB`). *Defer:* DB-backed stash is durable and fine here; only a minor RDS offload.
- **Cache-fallback hardening** — the `CACHE_ACCEL` fallback if `REDIS_HOST` is ever unset is a foot-gun (per-process cache, DB sessions lost on redeploy). *Defer:* Redis is always injected in prod; make it fail-loud later. Low risk.
- **GuardDuty (~$3-5/mo)** — weakest value-per-dollar at idle. *Keep for now:* some value on a public site, small cost; revisit if cost-trimming becomes a priority.
- **WAF SQLi + PHP managed groups (~$2/mo)** — largely redundant with the Common rule set at this traffic. *Keep for now:* defense-in-depth, negligible cost.
- **Zero-downtime deploys** — SSM `docker pull && docker run` swap instead of full-instance replacement (~5-min downtime/deploy; ADR-0001 pre-blesses it). *Defer:* downtime acceptable for a hobby wiki; nice future polish.
- **RDS `force_ssl` / `$wgDBssl`**, **CloudFront->origin TLS**, **sitemap EventBridge automation** — already on the Phase 4 list; in-VPC DB traffic + TTL-based sitemap are fine until content is actively edited.
- **Cosmetic:** remove stale "ALB/ECS" comments in `network-stack.ts`; drop the redundant `$wgUploadPath` S3 override in `LocalSettings.php`.
- **CloudFront 5xx alarm cross-region fix.** *Discovered during the #38 review:* CloudFront publishes distribution metrics to us-east-1 only. The `wiki7-cloudfront-5xx-high` alarm lives in il-central-1 and CDK refuses cross-region alarms (`ValidationError: Cannot create an Alarm in region 'il-central-1' based on metric '5xxErrorRate' in 'us-east-1'`), so it sits in INSUFFICIENT_DATA forever. The dashboard widget is fine (Dashboards CAN render cross-region metrics; only Alarm can't). Real fix is a small us-east-1 sibling stack with just this alarm + an SNS topic (we already deploy us-east-1 stacks for the cert + WAF). *Defer:* UptimeRobot covers user-facing edge errors and `AppErrorRateHigh` covers origin-side 5xx via the MW log filter, so there's coverage; the gap is the specific signal "edge-side problem CloudFront sees but origin doesn't" which is rare on a single-instance origin.

### Phase 3 — Content + data pipeline + finalize design  *(priority #3 — "what matters")*
*Goal: real, correct content; the full pipeline run end-to-end at least once; the design "done."*
- [ ] Stand up the bot account and **run the full data pipeline end-to-end** against local → prod for the first time (`data/BOT_SETUP.md`).
- [ ] Fix pipeline correctness gaps: match records missing `season`, brittle lineup/match-event extraction, empty club spiders (coaches / club transfers).
- [ ] **Human-review the auto-Hebrew translations** before publishing (known quality issues).
- [ ] Re-curate the major content sections with fresh eyes: Did You Know, Fan Culture, kits, anthems, museum, records — using `archive/wip-content` as reference.
- [ ] Fill homepage `TODO` placeholders (current manager/captain, real featured image, attributed quote).
- [ ] **Finalize the visual design** on the modern skin (all design polish lives here).
- ~~Add a favicon~~ ✅ **Done in PR #31 + SVG variant in #31's follow-on commit.** Full set: `docker/assets/favicon.ico` (multi-res 16/32/48), `favicon.svg` (modern crisp), `apple-touch-icon.png` (180x180 iOS), and a copy of `favicon.ico` at the docroot for auto-fetchers that don't parse HTML.
- [ ] Decide on **PageForms** (wire up editor forms, or remove if unused).
- [ ] **Content-driven SEO** *(unblocked by the Phase 2 SEO foundations — WikiSEO + Description2)*: WikiSEO is the framework; per-page customisation happens via the `{{#seo:title=|description=|image=|type=|published_time=}}` parser function in wikitext. The site-wide `<meta name="description">` fallback in LocalSettings.php already covers description-less pages; Phase 3 work is per-page wikitext overrides on key pages plus embedding `{{#seo:}}` calls in Cargo templates so player/team/match pages get correct metadata automatically (e.g. player infobox passes `{{{name}}}` → og:title, `{{{photo}}}` → og:image, `type=Person`). **Schema.org JSON-LD** baseline (Article + Organization) ships free with WikiSEO; richer types (SportsTeam / Person / SportsEvent) come from per-page `|type=` overrides. Also: alt tags on all uploaded images; review internal-linking quality once Cargo "related players" / "team history" queries are populated; decide URL-slug strategy (Hebrew vs transliterated). All require real content to be in place first.
- [ ] **Opengraph.xyz audit follow-ups deferred from Phase 2** (recorded 2026-06-06 after the SEO foundations landed):
    - **Description length**: expand `$wgWiki7FallbackDescription` (currently ~61 chars) toward ~140 chars with keyword-rich content (team history, player positions, league names, "האדומים מהדרום" supporter nickname, etc.) so Google snippet density improves. Trivial one-line edit to `docker/LocalSettings.php` once we know which keywords actually drive traffic — wait until Search Console has a few weeks of impression data.
    - **Headline overlay on the OG image** *(marketing-engagement boost; design call)*: bake the page title text into the share image so social cards carry the headline without a separate read step. Requires either dynamic per-page image generation (server-side render via WikiSEO's `$wgWikiSeoEnableSocialImages` config — exists but disabled now) or a designed static card variant per common page-type. Not obviously worth the design + perf cost for a personal fan wiki; revisit if engagement data justifies.
    - **CTA overlay on the OG image**: marketing-funnel practice for product/SaaS sites ("Try the demo"). Doesn't apply to a fan wiki — reading the wiki *is* the action. **Decision: won't fix.**
    - **og:title length** (currently 36 chars vs the linter's 50-60 "optimal"): the rule is calibrated for English Latin chars; Hebrew chars are visually wider so 36 Heb ≈ 50-60 Latin in preview-card display width. Padding for the char count alone would add filler. **Decision: won't fix unless an OG-specific renderer truncates oddly.**
- **Exit:** pipeline runs clean end-to-end; core content correct; design done; no placeholder TODOs.

### Phase 4 — Ops & automation  *(cross-cutting)*
*Goal: it runs itself and is documented.*
- [x] CloudWatch alarms + an external uptime check. *(Done as part of the Phase 2 post-relaunch hardening pass — 6 alarms in `ObservabilityStack`, UptimeRobot monitoring `https://wiki7.co.il` from Ashburn.)*
- [ ] **Wire SNS notification actions on the existing alarms** — currently they only change state in the console; need a topic with an email/Slack subscription so they actually notify.
- [ ] **CloudFront → EC2 origin TLS** — currently HTTP-only between the edge and the EC2 origin. Real day of work: Caddy/nginx on EC2 with a DNS-01 ACME challenge against the `wiki7.co.il` zone, then flip the CloudFront origin protocol to `HTTPS_ONLY`. Traffic-on-AWS-network-is-not-public so the gap is small, but it's the last "modern best practice" item.
- [ ] **RDS TLS enforcement** — parameter group `rds.force_ssl=1`; current MW→DB connection is plaintext inside the VPC.
- [ ] **Sitemap regeneration automation** — EventBridge weekly schedule that invokes the existing `Wiki7-GenerateSitemap` SSM document. Manual trigger until content stabilises.
- [ ] **Zero-downtime instance update** — wire `aws ssm send-command` against the running instance to `docker pull && docker run` the new image, decoupling image rollouts from CloudFormation instance replacement.
- [ ] Automate the data pipeline (scheduled run) with a dry-run → review → import gate.
- [ ] Write `docs/runbook.md` (deploy, rollback, restore, run pipeline) and `CONTRIBUTING.md`.
- [ ] Populate `BACKLOG.md` from the task bank below.

---

## 5. Cost & architecture *(rebalanced 2026-06-06; deployed as Option B)*

The old design was ECS Fargate + ALB + RDS + CloudFront + WAF ≈ **$65–95/mo** — torn down for cost. The original Phase 2 plan ("balanced cloud-native", below as *Option A*) was implemented in full and synthesized at ~$63/mo — 50% over target. On honest re-examination of the four required properties (reliable, fast, modern, secure) against wiki7's actual workload (personal Hebrew fan wiki, ~1 user/day), the Fargate + ALB shape was optimizing for a multi-tenant SaaS workload that doesn't exist here. ALB alone was ~30% of monthly cost without ever using its multi-target features. Architecture switched to **Option B: single Graviton EC2 + managed RDS** at ~$47/mo.

**Deployed architecture (~$47/mo, ≈ half the old cost):**
- **Compute** — single **t4g.small EC2** (Graviton/ARM64), AL2023, IMDSv2-only, encrypted gp3 root, termination protection **OFF** (dropped in #26/#27 — it blocked every UserData-driven instance replacement and left orphan instances; irreplaceable-data protection lives on RDS `deletionProtection` instead). UserData installs Docker + pulls the CDK-built MediaWiki image from ECR + runs the container with secrets fetched from Secrets Manager at boot. CloudWatch status-check alarm → `ec2:recover` for free auto-recovery.
- **Database** — RDS MariaDB **11.4.9 LTS**, `t4g.micro` Graviton, single-AZ, **deletion protection + snapshot-on-delete + 7-day automated backups + PITR**, encrypted at rest. *Managed DB = robust data-loss protection — the #1 lesson from the prior teardown.*
- **Speed + protection** — CloudFront (TLS, caches static `load.php`/`skins`/`extensions`/`images` → fast; free AWS Shield Standard DDoS) → EC2 via the `ec2.wiki7.co.il` A-record bound to the static EIP. No ALB.
- **Security** — WAF (Common + KnownBadInputs + SQLi + PHP managed rules + geo-block + rate-limit + expanded crawler allow list, with the bot-rule ordering bug fixed); the EC2 SG only accepts port 80 from the CloudFront `com.amazonaws.global.cloudfront.origin-facing` prefix list (instance not reachable from the public internet despite having a public EIP); SSM Session Manager replaces SSH (no port 22 open); S3 locked down via `BLOCK_ALL` + `BUCKET_OWNER_ENFORCED`, CloudFront OAC only.
- **Backups** — RDS automated + AWS Backup vault (KMS-encrypted, daily 7-day retention); restore drill executed 2026-06-06 (took on-demand snapshot → restored to temp `t4g.micro` → verified MW schema + 15 pages including the Hebrew main page → torn down).

**Detailed reasoning, four-property assessment, cost breakdown, and migration path back to the Fargate+ALB design:** [`docs/adr/0001-single-ec2-vs-fargate-alb.md`](adr/0001-single-ec2-vs-fargate-alb.md). The Option A implementation is preserved at the `archive/option-a-fargate-alb` git tag and can be cherry-picked back in ~1 day if traffic ever justifies it.

---

## 6. Decisions

**Locked (2026-06-04):**
- **Sequencing:** modernize-first (Phase 1 before relaunch/content).
- ~~**Architecture:** right-size + fix the existing CDK Fargate/RDS/CloudFront stack (~$30–45/mo). *Override to single-instance still possible if cost must drop.*~~ → **superseded 2026-06-06 (see below).**
- **Domain:** `wiki7.co.il` (renewed 2026-06-04). Verify registrar delegates to the zone's 4 nameservers; records recreated in Phase 2.
- **WIP:** preserved to `archive/wip-content` (`5d84083`).

**Re-decided (2026-06-06):**
- **Architecture:** single Graviton EC2 + managed RDS (~$47/mo). The Fargate + ALB design was built in full but turned out to be over-engineered for wiki7's actual workload. Option A implementation preserved at `archive/option-a-fargate-alb` tag; full ADR at [`docs/adr/0001-single-ec2-vs-fargate-alb.md`](adr/0001-single-ec2-vs-fargate-alb.md).

**Closed:**
- Where to commit planning docs → main line (decided 2026-06-04, executed via PR #22).

---

## 7. Task bank — known issues by area

The detailed infra fixes live in [`PLAN.md`](../PLAN.md) Stage 1 (still a valid checklist).

**Security / infra (Phase 2 — these were the pre-rebuild issues; all addressed by PR #24):** S3 `BlockPublicAccess` all disabled; RDS `DESTROY` + no deletion protection (caused the data loss); WAF bot-allow ordered after bot-block (Googlebot blocked); MariaDB 10.5 (EOL); ALB HTTP-only; no autoscaling; hardcoded S3 bucket name; stale CDK v1 deps; insecure `WG_SECRET_KEY`/`WG_UPGRADE_KEY` dev fallbacks. *(Phase 2 follow-up addressed: automated OS patching via SSM Patch Manager, RDS+EC2+CloudFront CloudWatch alarms, external uptime monitor via UptimeRobot, GuardDuty, monthly long-retention backup, RDS maintenance window moved to the Israeli weekend. Phase 4 carry-overs: CloudFront→origin TLS, RDS-side SSL enforcement, SNS wiring on the existing alarms — none are blocking.)*

**MediaWiki / skin (Phase 1 + 3):** Wiki7 is a full copy of Citizen (no clean upstream-merge path); PageForms installed but unused; homepage TODO placeholders; Records/season pages query Cargo tables only the pipeline creates; `seasons` table declared but unqueried; missing Season / Fan-story infoboxes.

**Data pipeline (Phase 0 + 3):** 3 failing tests; matches carry no `season`; lineup/match-event extraction brittle; coaches / club-transfers spiders return empty; auto-Hebrew quality (manual review required); free Google Translate dependency is fragile; pipeline never run end-to-end live; only 2024 season materialized.

**Docs / CI (Phase 0 + 4):** `architecture.md` was fictional (now fixed); no CONTRIBUTING/runbook; `BACKLOG.md` is a stub; CI was advisory-only.

---

## 8. Reference

- AWS: profile `argamanza`, account `368127906643`, primary region `il-central-1`, CloudFront/cert/WAF in `us-east-1`.
- `wiki7.co.il` zone `Z05358991AQ467TZUH2N6` → NS: ns-1806.awsdns-33.co.uk, ns-1370.awsdns-43.org, ns-662.awsdns-18.net, ns-327.awsdns-40.com.
- Local dev: `cd docker && docker compose up` (wiki :8080, Adminer :8081). Pipeline + bot setup: `data/BOT_SETUP.md`.
- WIP archive: branch `archive/wip-content` (`5d84083`).
