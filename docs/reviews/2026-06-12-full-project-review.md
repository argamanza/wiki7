# Wiki7 Full Project Review — 2026-06-12

> Scope: everything — all subsystems (`cdk/`, `docker/`, `data/`, `.github/`, docs),
> the full git history from the first commit (2025-04-24), the decision record, and the
> active WIP branch `phase-3a-r2/implementation`. Reviewed by four parallel deep-dive
> passes (infra/CI, MediaWiki/skin, WIP pipeline, docs/history) + synthesis.
>
> Conventions: ✅ = fixed on the review branch (`claude/full-repo-project-review-vdd7r1`);
> 📋 = report-only (either WIP-branch territory — to avoid merge conflicts with
> `phase-3a-r2/implementation` — or a decision that's yours to make).
> Severity: 🔴 critical · 🟠 high · 🟡 medium · 🟢 low/info.

---

## 1. Executive summary

**The project is in genuinely good shape, and the way it's run is its biggest asset.**
The probe-then-record loop (research docs → empirical verification → ADRs → phase
close-outs) caught real security holes before this review did (dev `$wgSecretKey` in
prod, secrets in cloud-init logs), and the post-data-loss discipline (deletion
protection, restore drills, the review gate) is consistently applied, not aspirational.

The review found **no landmines in deployed prod behavior**, but did find:

- **4 deployed security/correctness gaps** worth fixing now — all fixed on this branch:
  a WAF rule-ordering hole (spoofed crawler UA bypassed the rate limit), an over-broad
  GitHub OIDC trust policy (any branch could deploy as admin), a live anonymous
  draft-title enumeration hole in Wiki7ReviewGate (`list=allrevisions` + `usercontribs`
  + `Special:Contributions`), and a seed-import force mode that would clobber the live
  homepage/templates on any seed-file change.
- **DR tooling that would have failed in a real incident** — both `scripts/dr-*.sh`
  still targeted the torn-down ECS architecture and validated from a laptop that can't
  reach the DB. Rewritten for the EC2+SSM architecture. Given "backups are sacred" is
  the project's #1 lesson, this was the most ironic finding.
- **Severe doc drift** in everything except `docs/revival-plan.md` — README and
  `docs/architecture.md` still described the torn-down Fargate architecture, wrong test
  counts, and GitHub Secrets that don't exist. All synced.
- **The WIP branch is on a sound trajectory but is NOT prod-push-ready** — §6 below
  lists 4 critical + 9 high findings, headlined by the Pattern A promote flow being
  broken in a way its tests don't catch, the ScraperAPI key persisted into output
  files, and silent data corruption across pre-2000 seasons (dates, lineups,
  home/away results).

Everything applied on this branch avoids the 84 files the WIP branch touches.

---

## 2. What this project is (for the record)

A Hebrew RTL MediaWiki fan wiki ("ויקישבע") for Hapoel Beer Sheva FC at
[wiki7.co.il](https://wiki7.co.il). Three subsystems:

1. **`docker/`** — MediaWiki 1.45.3, the Wiki7 skin (full fork of Citizen 3.17.0),
   Cargo content model, and the in-repo Wiki7ReviewGate extension (bot writes →
   `Draft:` namespace → human review → promote).
2. **`data/`** — Scrapy/ScraperAPI Transfermarkt scraper → Pydantic normalization →
   Hebrew translation (Wikidata → Wikipedia → LLM) → Jinja2 wikitext → mwclient import.
3. **`cdk/`** — single Graviton EC2 (Docker: MW + Redis sidecar) + RDS MariaDB 11.4 +
   CloudFront + WAF + S3, ~$47-52/mo, full observability/backup story.

### Timeline from day one

| Era | Dates | What happened |
|---|---|---|
| AWS-first bring-up | 2025-04-24 → 05-10 | CDK on day 2 (ECS/ALB/RDS); prod live by 04-29; Citizen adopted, **Wiki7 fork** created 05-03; red theme, Hebrew RTL |
| Data pipeline v1 | 2025-05-11 → 06-02 | Cargo adopted (unrecorded decision); third-party TM scraper cloned then **abandoned for an own Scrapy stack**; ScraperAPI; Pydantic; PageForms |
| **Pause 1** | 2025-06 → 2026-02 | ~8.5 months. During this window: stack torn down for cost, **prod DB permanently lost** (`removalPolicy: DESTROY`, zero snapshots), domain lapsed |
| Revival #1 | 2026-02-16/24 | PLAN.md + a 29-commit audit/fix burst (its own Phase 1-6 numbering — unrelated to the later revival-plan phases) |
| Content sprint | 2026-03-02 → 03-07 | Main page, localization, sliders (PRs #12-#18); MW 1.45 attempted on a side branch, not merged |
| **Pause 2** | 2026-03 → 06-04 | ~3 months |
| Revival #2 | 2026-06-04 → 06-08 | The current campaign: revival plan (#22) → Phase 1 modernize (MW 1.45.3 + Citizen 3.17 re-fork, #23) → Phase 2 relaunch (Option A Fargate built in full, then same-day re-decided to Option B single-EC2, ADR-0001, #24) → SEO sweep (#29-#34) → 2.5 hardening (#38) → 2.5c verification (caught 2 security holes) → 2.5d secrets fix (#44) → 2.5b edge caching (#46) → Phase 3a pipeline (#50) → **Phase 3.5 review gate** (#51-#53, ADR-0002) → 3a-content push (113 pages through the gate, #54-#56) → deliberate clean-slate teardown + redeploy (#58-#60) |
| Phase 3a R2 (now) | 2026-06-09 → 06-12 | WIP branch: all-time pipeline scope — 27 commits, 363 tests, inventory + translation overhaul + idempotency + new page types; currently mid "iteration cycle 1", Finding 4 (career stats) handed off |

Notable: content has been lost twice — once accidentally (2025, unrecoverable) and once
deliberately (2026-06-08, by then protected by `resetContent.php` + the gate). The
lesson demonstrably propagated into code, ADRs, and verified drills.

---

## 3. What's working well (don't change these)

- **The decision/verification culture.** ADR-0001 is an exemplary honest cost ADR
  (it surfaced its own 50% overrun and triggered the re-architecture). ADR-0002 +
  research-0001 caught its own research being wrong (`$wgNamespaceProtection` doesn't
  gate reads) via empirical probes. The 2.5c verification matrix caught two real
  security holes. Keep this loop — it outperforms any review.
- **Option B architecture is right-sized.** Single EC2 + managed RDS + CloudFront with
  the cookie-aware `Wiki7DynamicHtml` cache policy (genuinely well-designed and
  well-tested) is the correct shape for this workload. Don't relitigate.
- **The review gate (Phase 3.5)** is the correct buy-vs-build hybrid: maximum reuse
  (ApprovedRevs + Lockdown + Echo) with a thin in-repo extension for the glue. The
  namespace-leak gating approach is sound (and now covers three more surfaces).
- **The skin fork is faithfully documented** — every brand delta in
  `wiki7-skin-customization.md` verified present in code, and the re-fork recipe is real.
- **The WIP branch's process** — decisions documented before implementation
  (research-0002/0003), every change tested (286→363), honest commit messages about
  validation gaps.

---

## 4. Findings — deployed code (master)

### 4.1 Infrastructure / CDK

| # | Sev | Finding | Status |
|---|---|---|---|
| I1 | 🟠 | **WAF: crawler-UA spoofing bypassed the rate limit.** `AllowLegitimateBot` (priority 6) is a *terminating* allow, so anything matching it skipped `RateLimitPerIP` (priority 7) — sending `User-Agent: Googlebot` exempted a client from rate limiting entirely. The in-code comment claimed the opposite. | ✅ Priorities swapped (rate limit now 6, allow 7) + regression test |
| I2 | 🟠 | **GitHub OIDC role trusted `repo:argamanza/wiki7:*`** — any branch/PR workflow could assume deploy-grade credentials (deploy passes the admin `cfn-exec` role to CFN), and PR-triggered `cdk-diff` executes arbitrary PR TypeScript with those creds. | ✅ Split: deploy role trusts only `ref:refs/heads/master` + `environment:production`; new read-only `Wiki7GitHubActionsDiffRole` (bootstrap `lookup` only) for PRs + trust-policy regression tests. **Post-merge note:** the first PR's cdk-diff will fail until one master deploy creates the new role. |
| I3 | 🟠 | **DR scripts rotted to dangerous.** `dr-restore.sh` searched for ECS clusters that no longer exist and gave restore guidance that can't work with the CFN-wired DB endpoint; `dr-test.sh` could never pass — it ran `mysql` from a laptop against an SG that only admits the EC2, swallowed the failure, and printed "DR Test FAILED — no tables", indistinguishable from corrupt backups. | ✅ Both rewritten for EC2: validation now runs in-VPC via SSM on the wiki7 instance (secret fetched by the instance role, `MYSQL_PWD` so nothing hits a command line or SSM history); restore script automates restore + prints the endpoint-preserving **rename dance** for cutover. ⚠️ Drill `dr-test.sh` once against prod to bless it. |
| I4 | 🟡 | **`s3-directories` Lambda crashed on every invocation** (`cfnresponse` needs `ResponseURL`, absent in direct invokes) and succeeded only because `AwsCustomResource` ignores FunctionErrors — meaning real S3 failures were also swallowed. The construct was unnecessary anyway (S3 has no directories; BucketDeployment populates `assets/`). | ✅ Deleted (Lambda, custom resource, `cdk/lambda/s3-directories/`) |
| I5 | 🟡 | **EIP self-association at the START of UserData** stole the origin IP from the old healthy instance minutes before the new one could serve (dnf update + 2 image pulls + container start ≈ 2-4+ min downtime per deploy). | ✅ Moved to the end of UserData behind a wait-for-HTTP loop; cutover gap is now seconds |
| I6 | 🟡 | **S3 uploads had a 7-day undelete window** vs. the DB's 1-year monthly snapshot — inconsistent with the project's own backup philosophy; vandalism noticed after a week was unrecoverable. | ✅ `noncurrentVersionExpiration` 7→30 days |
| I7 | 🟡 | `npm audit`: 6 vulnerabilities (4 high) incl. `aws-cdk-lib` itself; CLI too old for the lib after fix. | ✅ `aws-cdk-lib` → 2.259.0, `aws-cdk` CLI → latest; 0 vulnerabilities; 57/57 tests + full synth pass. Run `cdk diff` before the next deploy (lib minor bumps occasionally re-render resources). |
| I8 | 🟡 | **CrossRegionSsmSync is fire-and-forget**: async invoke (failure = silent success), no dependency ordering vs. the SSM parameter, never re-syncs if the cert ARN ever changes (stale ARN → CloudFront keeps old cert). | 📋 Recommend replacing with CDK `crossRegionReferences: true` (deletes 2 Lambdas + 2 custom resources). Touches deployed resources — do it as its own PR with a careful `cdk diff`. |
| I9 | 🟡 | **CloudFront 5xx alarm permanently dead** (cross-region metric; sits in INSUFFICIENT_DATA forever, displayed in the dashboard as if live). Already documented by you as a Phase 4 deferral — but the fix is ~20 lines in the existing us-east-1 `Wiki7WafStack` + small SNS topic. | 📋 Respected your recorded deferral; recommend pulling it forward — it's cheap and the alarm currently reads as coverage it isn't. |
| I10 | 🟢 | GuardDuty detector lives inside `Wiki7CdkStack` — `cdk destroy` (which the 06-08 clean slate actually did!) silently disables account threat detection; also collides if a detector ever pre-exists. | 📋 Move to the OIDC stack or a never-torn-down "account-baseline" stack |
| I11 | 🟢 | No CPU-credit alarms for the t4g fleet (unlimited mode silently buys surplus credits instead of throttling — a cost, not perf, signal). | 📋 Cheap add to ObservabilityStack |
| I12 | 🟢 | Stale ECS/ALB-era comments in `network-stack.ts`; stale "no SNS actions" docstring in `observability-stack.ts`. SG `GroupDescription` strings are also stale but **immutable** (fixing = SG replacement). | ✅ Comments fixed; descriptions deliberately left with an explanatory note |
| I13 | 🟢 | Dead ECS-era files: `cdk/scripts/run_update_task.py` (references `Wiki7TaskDef` etc. that don't exist) + `cdk/pyproject.toml`/`uv.lock` that existed only to feed it. | ✅ Deleted |
| I14 | 🟢 | No DB secret rotation (post-2.5d choreography was one-time). Acceptable at this budget. | 📋 Noted for the record |

**Over/under-engineering verdict:** managed RDS, the backup stack, and the cache policy
are right. WAF + GuardDuty ≈ 35-40% of monthly spend is the questionable line item for a
1-editor site — ADR-0001 already lists the levers; GuardDuty is the first thing to cut
if budget ever tightens. `CrossRegionSsmSync` and the s3-directories Lambda were the two
genuinely over-engineered spots (one now deleted, one recommended for deletion). The
under-engineered spot was DR tooling (now fixed) — for a project whose #1 value is
restorability, the practiced restore path was a doc, not a script.

### 4.2 CI/CD workflows

| # | Sev | Finding | Status |
|---|---|---|---|
| W1 | 🟡 | `lint-and-test.yml` had no `permissions:` block (default token, potentially write). | ✅ `contents: read` |
| W2 | 🟡 | Third-party actions pinned by moving tag — incl. `sticky-pull-request-comment` holding a `pull-requests: write` token. | ✅ Pinned to commit SHAs (sticky-comment v2.9.4, setup-php v2, setup-uv v3) |
| W3 | 🟢 | `${{ github.ref_name }}` interpolated directly into a `run:` block (canonical script-injection pattern; exposure: collaborators only). | ✅ env-var indirection |
| W4 | 🟢 | Health check used `curl -k` (TLS verification off, no reason) and probed `/` through the edge cache — a freshly broken origin could false-pass on a cached 200. | ✅ `-k` dropped; homepage probe carries a per-deploy `?healthcheck=$GITHUB_SHA` cache-buster |
| W5 | 🟢 | Wasted CI: deploys ran on docs-only pushes (~15 min qemu ARM build each); CDK tests ran twice per master push; an unused `cdk diff` step in deploy. | ✅ `paths-ignore` for `docs/**`/`**.md`; `test-cdk` in lint-and-test now PR-only; dead step removed; `id-token: write` scoped to the deploy job |
| W6 | 🟢 | Deploy summary greped hardcoded hashed CFN output keys (`CloudFrontDistributionIdBFB1951A`) — silently breaks on any construct rename. | ✅ jq prefix-match |

Lint enforcement status (per your Phase-0 plan): PHP/JS/Python lint remain **advisory**
(`continue-on-error`) — consistent with the documented "enforce in Phase 3" decision.
ruff is close to clean on the WIP branch (15 issues, 14 auto-fixable) — flipping it to
blocking belongs in the 3a-R2 PR. There's no linter at all for the CDK TypeScript; 📋
consider eslint there eventually.

### 4.3 MediaWiki / Docker / Wiki7ReviewGate

| # | Sev | Finding | Status |
|---|---|---|---|
| M1 | 🟡→🔴 in spirit | **Anon draft-title enumeration via `list=allrevisions`** — the exact leak class the extension exists to close (its hook only gated `recentchanges` + `allpages`). `arvnamespace=3000` listed every Draft title + revision metadata with no per-title read check. Sibling surfaces: `list=usercontribs` and `Special:Contributions/Wiki7Bot` (a directory of every draft the bot ever wrote). | ✅ All three gated (same fail-open SQL-condition pattern, guarded on the `page` table being in scope). ⚠️ **Re-run the 11-surface leak probe from research-0001 against prod after deploy**, now including `allrevisions`, `usercontribs`, and `Special:Contributions`. |
| M2 | 🟠 | **`import-pages.php` force mode clobbered live pages.** Docstring claimed "manual edits are preserved" — false whenever any seed file changed: the all-files hash flipped force on and **every** mapped page (live homepage + 12 templates) was overwritten; being ApprovedRevs-gated, each also got silently de-published until re-approved. | ✅ Force mode now preserves any page whose latest revision isn't this importer's (detected via the `Auto-import:` summary prefix; hidden/missing comments fail toward preserving). Reported as `PRESERVED` in output. |
| M3 | 🟡 | **Entrypoint idempotency hole:** if `install.php` died midway, the container was stranded with the installer's LocalSettings.php (or none), and restarts could skip the restore branch entirely. | ✅ EXIT trap + boot-time self-heal always restore `LocalSettings.php.custom` |
| M4 | 🟡 | Build reproducibility: `pecl install redis` and the Composer phar floated to latest; (REL1_45 extension branches are also moving targets, but that's a deliberate trade). | ✅ Pinned `redis-6.2.0` + Composer 2.8.6 via ARGs. 📋 If you want bit-exact builds, also pin the four REL1_45 extension clones to commit SHAs. |
| M5 | 🟡 | **Local dev ran MariaDB 10.5 vs prod 11.4.9** — exactly the parity gap that produces reserved-word surprises like the WIP's Cargo `matches` collision (10.5 lacks 11.x's reserved words). PLAN.md Stage 3 called for this fix; never done. | ✅ Compose bumped to `mariadb:11.4` + `restart: unless-stopped` on the MW service. ⚠️ Existing local volumes need `make docker-reset` once. |
| M6 | 🟢 | Telegram token passed through `urlencode()` — tokens contain `:` (`%3A` in the path); worked by luck of server-side decoding. | ✅ Token concatenated verbatim (it's config, not user input) |
| M7 | 🟢 | `extension.json` `MaintenanceScripts` key isn't in the MW 1.45 schema — dead config implying an invocation form that doesn't work (all real call sites use the file path). | ✅ Removed |
| M8 | 🟢 | **Wiki7ReviewGate has zero tests.** For a security-sensitive read gate, even one leak-regression test matrix (anon → each enumeration surface → no NS_DRAFT rows) would have caught M1. | 📋 Recommend a PHPUnit (or even curl-script) leak matrix as part of 3b; the probe list in research-0001 is the spec |
| M9 | 🟢 | `LocalSettings.php` (📋 WIP file): the CloudFront-Viewer-Address → `REMOTE_ADDR` rewrite trusts the header unconditionally — the *only* thing preventing IP forgery is the SG prefix-list rule in CDK. Parsing itself is sound (IPv4/IPv6/ports verified). | 📋 Add an origin-verify shared-secret header at CloudFront and check it before trusting the viewer header, or at least document that the SG rule is load-bearing. Also: the `reviewer` group grant `unapprovedpages` is a right that doesn't exist in ApprovedRevs (dead line); `$wgPingback = true` sends anonymous usage stats to WMF (fine, but a conscious choice to keep). |
| M10 | 🟢 | Hooks.php uses some legacy/global service access (`MediaWikiServices::getInstance()`, root-namespace `Config`/`ApprovedRevs`/`EchoEvent`) — works on 1.45, will need attention at the 1.46 bump. | 📋 Carry to the MW upgrade item below |

### 4.4 Skin (Wiki7 / Citizen fork)

All documented brand deltas verified present and correct (tokens, Header, Drawer +
footer + RTL transform-origin, Menu active-row, fonts module, SkinHooks, mustache
templates, skin.json config). Three small things:

| # | Sev | Finding | Status |
|---|---|---|---|
| S1 | 🟢 | One leftover "Citizen" reference in Wiki7-authored code (`Header.less` comment) — breaks the re-fork recipe's documented "zero references" grep invariant. | ✅ Reworded |
| S2 | 🟢 | Hebrew `@font-face` declared `format('truetype')` for woff2 variable fonts (browsers sniffed past it, but some engines may skip the source). | ✅ `format('woff2-variations')`, matching upstream Citizen's pattern |
| S3 | 🟢 | `Rubik.woff2` (112K) + `OpenSans.woff2` (276K) checked in but never `@font-face`-declared anywhere — 388K dead weight in repo + image. (Also: `--font-family-language-base: 'Roboto'` points at a font that isn't bundled — harmless fallthrough, looks like a copy-paste from another lang module.) | ✅ Deleted the two unused fonts. 📋 Roboto reference left as-is; clean up if you ever touch the module. |

### 4.5 Docs & repo hygiene

| # | Sev | Finding | Status |
|---|---|---|---|
| D1 | 🟠 | **README + `docs/architecture.md` described the torn-down world**: ECS Fargate compute, MariaDB 10.5/t3.micro, "site not deployed / domain NXDOMAIN", pre-fix bugs presented as current ("removalPolicy: DESTROY — must change"), test counts 48+21 (actual: 363 on WIP / 57 CDK), "Required GitHub Secrets" that no workflow reads (secrets moved to Secrets Manager in 2.5d), extensions list missing the entire review-gate/SEO stack. architecture.md is the file README *points to* as "what the code actually deploys". | ✅ Both rewritten against the real EC2 architecture; test counts de-hardcoded; secrets story corrected; review gate now visible in the README |
| D2 | 🟡 | `cdk/README.md` listed 4 of 12 lib files and described "ECS Cluster, Fargate Service, Load Balancer". | ✅ Rewritten |
| D3 | 🟡 | Backlog sprawl: **six** overlapping backlog surfaces (PLAN.md, BACKLOG.md stub, phase-3b-backlog, data/TODO.md, revival-plan checklists, 2.5c deferrals); BACKLOG.md was a 2-line stub the README called a "task bank"; PLAN.md's banner said Stage 1 is "still a valid checklist" though most items shipped in Phase 2/2.5. | ✅ BACKLOG.md → pointer at the two real surfaces; PLAN.md banner updated; README explains the hierarchy. 📋 `data/TODO.md` is WIP-adjacent — fold it into phase-3b-backlog during the 3a-R2 PR. |
| D4 | 🟡 | `.env.example` missing the `WIKI_BOT_USER`/`WIKI_BOT_PASS` pair `run_pipeline.py` reads (documented only deep in BOT_SETUP.md). | ✅ Added with provenance notes |
| D5 | 🟢 | `.idea/` committed despite being gitignored (files predate the rule). | ✅ Untracked (`git rm --cached`) |
| D6 | 🟢 | `Makefile`: `make test` could never fail (`\|\| true`), `pipeline-install` installed **poetry** while the project is on **uv**, pipeline targets used bare `python` (fails without a venv), `.PHONY` incomplete, `make cdk-synth` failed without env vars. | ✅ All fixed (`uv run` everywhere, failing `test`, synth env defaults) |
| D7 | 🟢 | `data/BOT_SETUP.md` predates the review gate (says bot edits land normally; legacy `docker-compose` syntax). | 📋 Not WIP-touched but `data/`-adjacent; fold a one-line "writes now land in Draft:" note into the 3a-R2 doc pass |

---

## 5. Decision record — critique

What exists is high quality (ADR-0001 and ADR-0002 + research-0001 are genuinely
exemplary). Gaps worth closing, each a one-pager at most:

1. **Cargo vs SMW/Wikibase has no written rationale** — the single most load-bearing
   content decision (May 2025, `d85c086 "install cargo"`), now deeply entrenched
   (9 templates `#cargo_store`, schema work, leakage fixes). Write a retroactive
   ADR-0003 even if it's just "evaluated X, chose Cargo because Y" — the next time
   Cargo misbehaves you'll want the original reasoning.
2. **Scraping posture has no ADR** — ScraperAPI choice, TM ToS/robots stance, the
   `.com`-vs-`.us` mirror question, and the credit budget (which already drives
   engineering decisions in phase-3b-backlog) are scattered across operational docs.
3. **MW 1.45 is non-LTS; security support ends ~Dec 2026 (~6 months).** This clock
   exists only as skin-doc "gotcha 8b" — not on any backlog. **Promote to a tracked
   Phase 4 item now** (1.46 bump + Citizen re-fork via the documented recipe + the
   M10 legacy-class cleanup in Wiki7ReviewGate).
4. **ADR-0001's cost table was falsified and never amended** — the KMS orphan-key
   incident (2.5c Finding 6: ~$30/mo of orphans, cleaned up) means top-down readers
   meet three contradicting cost numbers. Add a one-line addendum to ADR-0001.
5. **Citizen rename-fork-vs-child-skin rationale** lives outside the ADR series (in
   the skin doc); a pointer ADR closes the series' biggest visible hole.
6. **Out-of-repo "memories" are a bus-factor risk**: the secret-rotation recipe
   (5 hardcoded ARNs, DB_HOST quirk) lives in assistant-side `[[wiki7-secret-rotation]]`
   referenced from operational-bootstrap. If those memories vanish, the recipe has no
   in-repo home until the Phase 4 `recycle-wiki7.sh` lands. Commit the recipe to
   `docs/` (it contains ARNs, not secret values — safe for the repo).

---

## 6. WIP branch review — `phase-3a-r2/implementation` (📋 all report-only)

**State:** 27 commits ahead of master, 84 files, ~40k lines. `uv run pytest`:
**363/363 pass** (matches the handoff doc exactly). `ruff`: 15 errors (14 auto-fixable
unused imports + 1 F821 lint-only). Plan docs (research-0002/0003, iter-cycle guide)
are faithful to the code with two exceptions noted below.

**Verdict: sound trajectory, not prod-push-ready.** The translation chain order
(Wikidata sitelinks → Wikipedia langlinks → Claude → phonetic) is right; the Cargo
`matches`→`match_reports` rename is consistent end-to-end; the commit discipline is
excellent. But:

### Must fix before the all-time prod push

1. 🔴 **Pattern A promote flow is broken — and its tests test the wrong call shape.**
   `import_players.py:185` resolves with `want_namespace=3000` unconditionally and the
   stored namespace can never be 0, so a reviewer's `Draft:X → X` promotion is never
   synced into the state file. Next run: bot re-writes `Draft:X` over the promotion
   redirect → hidden duplicate draft + the public page orphaned from future updates —
   exactly the failure `page_router.py` says it solves. The latent variant is worse: a
   stored ns=0 would MovePage the *public* page back into Draft. `test_page_router.py`
   passes `want_namespace=0`, a shape production never uses. Fix: honor stored
   namespace (never move mainspace→Draft), sync reviewer moves (move-log scan or
   mainspace-first probe), return ns from the router; add a test that mirrors the real
   call path.
2. 🔴 **ScraperAPI key persisted + logged.** `match_spider.py:83` / `player_spider.py:72`
   store `response.url` — the full proxy URL including `api_key=` — into every output
   record (~70 seasons of plaintext on disk); `LOG_LEVEL=DEBUG` logs it per request;
   the key also travels over `http://` in 8 spiders; an empty key burns the retry
   budget on 401s. Fix: store the target TM URL instead, proxy-auth/header mode or at
   least `https://`, INFO + redaction, fail-fast on missing key.
3. 🔴 **Historical-era silent data corruption** (poisons ~50 seasons, expensive to
   review after import):
   - Home lineups dropped on table-layout (pre-formation) match reports — reproduced
     against the branch's own 1985 fixture (`match_spider.py:311-338`).
   - `DD/MM/YY` dates expand as `20YY` ("25/07/87" → 2087, `helpers.py:254`); season
     pivots map "49/50" → 2049 (`transfers_spider.py:55`, `platzierungen_spider.py:124`);
     match-page titles embed raw English TM dates (unstable identity + RTL scrambling).
   - Win/loss categorization assumes HBS is always home (`match_report.j2:167`,
     `competition_season.j2`) — ~half of all matches miscategorized; penalties/AET
     mishandled; the fixtures spider IndexErrors on old-era result cells → season
     silently yields zero fixtures (and `allow_empty=True` tolerates it). The fixtures
     spider has **zero tests** despite being the single feed for match/competition/
     European pages.
   - Cargo `birth_date` stored as DD/MM/YYYY into a `Date` column — PHP parses M/D/Y;
     every player born after the 12th errors/NULLs, earlier ones get day/month swapped.
4. 🔴 **`resetContent.php` deep reset TRUNCATEs `archive`/`recentchanges`/`logging`/
   `change_tag` site-wide under the DEFAULT scope** — destroys human undelete data and
   the audit log, contradicting the script's own "bot-authored content only" contract,
   with no `WIKI_ENV` guard, on a script advertised for SSM-against-prod use. Scope
   deletes to the bot actor or gate behind `--deep` + a non-prod check.

### High (fix or consciously accept)

5. 🟠 The bot lacks NS_DRAFT `move` (`LocalSettings.php:231` grants it to reviewers
   only) — Pattern A's flagship auto-MovePage will permission-fail unless Wiki7Bot is
   separately in the reviewer group; the "117/117 verified" run was creation-only.
6. 🟠 `--check-changes` wedges itself: hash saved *before* the scrape (a failed scrape
   permanently records "unchanged"), resume-skip means changed data is never actually
   re-fetched without `--force-rescrape`, and the squad-page hash misses match-day
   changes by design.
7. 🟠 Five spiders (incl. the two critical ones: squad, fixtures) bypass ScraperAPI and
   hit TM directly at concurrency 20 / no delay; a blocked first probe makes
   `--season=latest` silently resolve to **2020** (`season_detector.py:84`).
8. 🟠 Loan-page scraped without `saison_id` → today's loanees stamped into all ~70
   historical squads (`squad_spider.py:16`); per-season squad pages render the entire
   merged all-time roster (`import_templates.py:392` — no season filter).
9. 🟠 Competition pages build match links from English fixture data while match pages
   are titled from Hebrew-enriched data — links break whenever translation ran.

### Medium (worth doing in-branch)

- Translation batches: 200 entries × `max_tokens=8000` sits at the truncation point;
  a truncated JSON silently degrades the whole batch to Google Translate — the exact
  quality the overhaul exists to avoid. Halve the batch or check `stop_reason`.
  (The `cache_control` on the ~350-token system prompt silently never caches —
  Sonnet's minimum cacheable prefix is ~2048 tokens; the "saves 75%" comment is false.
  Cost is trivial either way: <$3 per full all-time pass.)
- Per-season `mappings.he.yaml` isolation defeats the manual-override system — 70
  cycles of reviewer corrections evaporate. Make it one global, git-tracked store.
- State file: non-atomic writes (corrupt file → silently start fresh → the duplicate
  problem returns), saved only once at run end. Atomic tmp+rename + incremental saves;
  longer-term, consider the wiki itself (TM IDs are in Cargo) as ground truth instead
  of a local YAML.
- Cargo store gating is call-site-only — move the `{{#ifeq:{{NAMESPACENUMBER}}|0}}`
  guard inside the generated `Template:Cargo/*` so one future unwrapped transclusion
  can't re-leak; curl-test CargoExport/Drilldown/`action=cargoquery` as anon.
- `None` leaks as literal "None" in several templates; `player_page.j2`'s
  `sum(attribute=...)` raises on None values; no wikitext escaping of `|`/`[[` in
  scraped names.
- Handoff doc nits that will block the next agent: wiki admin password is
  `AdminPass1234` (compose), not `Wiki7Admin!23`; `docs/next-session-prompt.md` is
  stale (pre-iter-cycle counts).

### Cost/scale reality check (all-time run)

ScraperAPI ≈ 7-8k credits per full crawl (matches your empirical 7,893) — Hobby tier
supports ~12 full crawls/mo; a TM-ID dedup on player pages would save ~2k credits/run.
Anthropic ≈ **<$3 per full translation pass** — negligible; the risk is truncation
quality, not cost. Wikidata is free but ~1.5-3h wall-clock per full pass at 2 workers.

---

## 7. Branch & PR hygiene

No open PRs. 14 unmerged remote branches; analysis:

| Branch | Disposition |
|---|---|
| `phase-3a-r2/implementation` | **Active WIP** — keep |
| `archive/wip-content`, `feature/content-sections-and-skin-enhancements`, `feature/drawer-fixes-and-content-expansion`, `upgrade/mediawiki-1.45-citizen-3.14` | Salvage sources, kept on purpose per revival plan §3 — keep |
| `fix/node20-deprecation`, `docs/phase-2.5b-done`, `docs/phase2-audit-fixes` | Patch-equivalent to master (git cherry) — **safe to delete** |
| `docs/2.5c-baseline-numbers`, `docs/expand-phase-2.5b`, `docs/phase-2.5c-verification`, `docs/revival-planning` | Stale pre-merge snapshots; master holds newer versions of everything in them — **safe to delete** |
| `claude/plan-session-hw2wD` (120 commits) | The Feb-2026 Revival #1 session; superseded per revival plan (PR #1 closed). Historical only — delete, or tag `archive/revival-1` first if you want the era browsable |
| `claude/sidebar-quick-icons-mxHrR` | Already on the revival plan's "optional later cleanup" list — delete unless you still want the icon-shortcuts idea |
| ~30 already-merged branches (`phase-*`, `fix/*`, `docs/*`, `ops/*`, `perf/*`, …) | Merged into master — safe to bulk-delete (`git branch -r --merged origin/master`) |

---

## 8. What was changed on this branch (summary + required follow-ups)

**Changed** (all outside WIP-touched files): WAF rule ordering + tests · OIDC role
split + tests · EIP-association timing · s3-directories construct deleted · S3
lifecycle 7→30d · CDK deps bumped (0 vulns; 57/57 tests; full synth verified) ·
3 workflows hardened (permissions, SHA pins, injection, cache-busted health check,
paths-ignore, dedupe) · DR scripts rewritten for EC2+SSM · Wiki7ReviewGate: 3 new leak
surfaces gated + Telegram token fix + dead config removed · `import-pages.php`
preserve-guard · entrypoint LocalSettings trap · Dockerfile pins · compose MariaDB 11.4
parity · skin font fixes + 388K dead fonts removed · README / architecture.md /
cdk/README / BACKLOG / PLAN / .env.example / Makefile synced with reality · `.idea/`
untracked.

**Follow-ups the changes create:**

1. **First deploy after merge**: expect an EC2 instance replacement (UserData changed
   — EIP timing) and deletion of the s3-directories Lambda/custom resource (no-op
   delete). Run `cdk diff` first and sanity-check there's no surprise from the
   aws-cdk-lib bump.
2. **cdk-diff on PRs will fail until that first master deploy** creates
   `Wiki7GitHubActionsDiffRole`.
3. **Local dev**: run `make docker-reset` once (MariaDB 10.5 → 11.4 volume).
4. **After deploy, re-run the anon leak probes** (research-0001 list + `allrevisions`,
   `usercontribs`, `Special:Contributions/Wiki7Bot`).
5. **Bless the rewritten `scripts/dr-test.sh` with one real run.**

## 9. Recommended priority order from here

1. Finish 3a-R2, but fix §6 items 1-4 (and decide on 5-9) **before** the all-time prod
   push — the corruption-class items especially, since bad historical pages are cheap
   to prevent and expensive to review away.
2. Merge this branch; do the deploy + follow-ups above.
3. Add the **MW 1.46 upgrade** (EOL Dec 2026) to phase-3b-backlog as a dated item.
4. One short docs pass: ADR-0003 (Cargo), ADR-0001 cost addendum, commit the
   secret-rotation recipe in-repo, branch cleanup from §7.
5. Phase 4 as planned — with I8 (cross-region refs) and I9 (us-east-1 5xx alarm)
   pulled toward the front, and CloudFront→origin TLS staying the top transport item.
