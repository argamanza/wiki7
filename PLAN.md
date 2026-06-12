# wiki7 Revival Plan

> ⚠️ **Superseded (2026-06-04).** The current roadmap and sequencing live in
> [`docs/revival-plan.md`](docs/revival-plan.md). This document is retained as a **detailed
> task bank** — its Stage 1 infrastructure/security fixes are still a valid checklist for the
> rebuild. Note that the production stacks have since been torn down (clean-slate rebuild).
>
> **Update 2026-06-12:** most Stage 1–2 items (S3 lockdown, SG fix, RDS SNAPSHOT +
> deletion protection, SQLi/PHP WAF rules, MariaDB 11.4, OIDC, cdk-diff PR comments)
> were completed during Phases 2/2.5 of the revival (2026-06). Cross-check
> [`docs/revival-plan.md`](docs/revival-plan.md) before reusing anything from this file.

Comprehensive plan to bring wiki7 from a stale project to a production-grade, scalable community wiki for Hapoel Beer Sheva FC.

Each stage is designed to be **independently researched, planned, and implemented** by separate agents. Stages touch distinct file domains with minimal overlap.

---

## Stage 1: Infrastructure Hardening & Cost Optimization

**Domain:** `cdk/`, `cdk/lib/`, `cdk/lambda/`, `cdk/test/`

### Research Phase
- Check latest AWS CDK v2 best practices for ECS Fargate, RDS, CloudFront, WAF (Feb 2026)
- Research MariaDB 10.5 EOL status and migration path to 10.11 or 11.x on RDS
- Research AWS WAF managed rule pricing vs. self-managed rule cost tradeoffs
- Research il-central-1 availability for any services used (some services launch late in newer regions)
- Research CloudFront Functions vs Lambda@Edge for redirect logic (current approach is fine, just verify)

### Fixes (Bugs & Security — must do)

1. **Fix WAF rule ordering bug — Googlebot/Bingbot get blocked**
   - `cdk/lib/wiki7-waf-stack.ts` — "AllowLegitimateBot" (priority 9) runs AFTER "BlockSuspiciousMediaWikiPatterns" (priority 8), so bots matching `(bot|crawl|spider|scan)` get blocked before the allow rule fires
   - Fix: Move allow rule to priority 5 (before the block rule), or restructure as a single AND-NOT rule
   - Verify: The bot regex also catches legitimate crawlers like `facebookexternalhit`, `Twitterbot`, `Slackbot`, `Applebot` — expand the allow list or switch approach to only block known-bad UA patterns instead of all bot-like strings

2. **Lock down S3 bucket — public access is wide open**
   - `cdk/lib/application-stack.ts:56-61` — `blockPublicAcls: false`, `blockPublicPolicy: false`, `ignorePublicAcls: false`, `restrictPublicBuckets: false` with `OBJECT_WRITER` ownership
   - The comment says "When using CloudFront with OAC, you should block all public access" but the code does the opposite
   - Fix: Set `BlockPublicAccess.BLOCK_ALL`, change ownership to `BUCKET_OWNER_ENFORCED`, remove ACL-related IAM grants (`s3:PutObjectAcl`)
   - OAC already handles CloudFront → S3 access; public access is unnecessary and dangerous

3. **Fix RDS security group misuse**
   - `cdk/lib/database-stack.ts:19` uses `mediawikiSecurityGroup` for the RDS instance
   - `cdk/lib/network-stack.ts:39-50` creates a separate `databaseSecurityGroup` with proper ingress rules — but it's unused by the database construct
   - Fix: Pass `databaseSecurityGroup` to DatabaseStack and use it for the RDS instance; the MW SG should only be on ECS tasks
   - Update `wiki7-cdk-stack.ts` to pass `network.databaseSecurityGroup` to the database construct

4. **Fix RDS removal policy — currently DESTROY with no deletion protection**
   - `cdk/lib/database-stack.ts:44-45` has `removalPolicy: DESTROY` and `deletionProtection: false`
   - One `cdk destroy` or stack replacement wipes the database with no recovery
   - Fix: Change to `removalPolicy: SNAPSHOT` (creates final snapshot before deletion) and `deletionProtection: true`

5. **Re-add SQLi and PHP WAF managed rules**
   - `cdk/lib/wiki7-waf-stack.ts:107` — SQLi, Linux, and PHP rule sets were removed to save ~$3/month
   - MediaWiki is a PHP app talking to MySQL — these are the most relevant rule sets
   - Fix: Re-add `AWSManagedRulesSQLiRuleSet` and `AWSManagedRulesPHPRuleSet` (skip Linux rules as Fargate abstracts the OS). Cost: +$3/month for significant defense-in-depth

### Optimizations & Hardening

6. **Add HTTPS between CloudFront and ALB**
   - `cdk/lib/cloudfront-stack.ts:28` uses `HTTP_ONLY` for the ALB origin
   - Add an ACM certificate in il-central-1 for the ALB, add HTTPS listener (443), update CloudFront origin to `HTTPS_ONLY`
   - Or at minimum switch to `MATCH_VIEWER` so HTTPS viewers get HTTPS to origin

7. **Add ECS auto-scaling**
   - `cdk/lib/application-stack.ts:229` — `desiredCount: 1` with no scaling policy
   - Add target tracking scaling: min 1, max 3 tasks, scale on CPU > 70%
   - Add health check grace period to avoid thrashing during deployment

8. **Add IPv6 DNS records**
   - `cdk/lib/cloudfront-stack.ts:138-148` — only A records, no AAAA
   - CloudFront supports IPv6 by default; add AAAA alias records for both apex and www

9. **Fix hardcoded S3 bucket name**
   - `cdk/lib/application-stack.ts:53` — `bucketName: 'wiki7-storage'` is globally unique; collision risk
   - Use account/region suffix or let CDK auto-generate: `wiki7-storage-${account}-${region}`

10. **Upgrade MariaDB version**
    - MariaDB 10.5 reached EOL June 2025
    - Upgrade to MariaDB 10.6 LTS (supported until July 2026) or 10.11 LTS (supported until Feb 2028)
    - This is a parameter change in `database-stack.ts`; RDS handles the upgrade with a maintenance window

11. **Add CloudFront caching for static MediaWiki resources**
    - `/load.php`, `/skins/*`, `/extensions/*` are static resources that can be cached
    - Add CloudFront behaviors with appropriate cache policies for these paths
    - Reduces ALB load and improves page load time significantly

12. **Enable RDS SSL connections**
    - `docker/LocalSettings.php:33` has `$wgDBssl = false`
    - Enable SSL to encrypt data in transit between ECS and RDS (defense-in-depth even within VPC)

13. **Remove stale CDK v1 dependencies from package.json**
    - `cdk/package.json` lists `@aws-cdk/aws-ec2`, `@aws-cdk/aws-ecs`, `@aws-cdk/aws-iam`, `@aws-cdk/aws-logs` (v1 packages)
    - These are unused (all imports use `aws-cdk-lib`); remove them to avoid confusion

### Test Updates

14. **Expand CDK test coverage**
    - Current: 20 tests covering 4 constructs (Network, Database, Application, Backup)
    - Missing: CloudFront, WAF, DNS, Certificate, CrossRegionSsmSync, main Wiki7CdkStack
    - Add tests for all new/changed constructs
    - Add tests that verify security properties (S3 block public access, RDS deletion protection, SG rules)

### Documentation Updates

15. **Update `docs/architecture.md`**
    - Current doc references CodePipeline (not used — GitHub Actions is the actual CI/CD)
    - References Nginx container and sidecar containers (not used)
    - References Multi-AZ RDS (not configured)
    - References private subnets (removed in Phase 4 cost optimization)
    - Rewrite to match actual deployed architecture

---

## Stage 2: CI/CD Pipeline & Staging Environment

**Domain:** `.github/workflows/`, `Makefile`, `docker/docker-compose*.yml`, `cdk/bin/wiki7.ts` (staging stack entry only)

### Research Phase
- Research cost-effective staging strategies: always-on vs. on-demand (spin up for PR, tear down after merge)
- Research RDS snapshot copy / `pg_dump` equivalent for MariaDB to sync prod data to staging
- Research GitHub Actions OIDC for AWS (eliminate long-lived access keys)
- Research GitHub Environments with protection rules and required reviewers
- Research blue/green or rolling deployment strategies for ECS Fargate
- Research cost of a minimal staging environment (t3.micro RDS + 256 CPU Fargate)

### CI Pipeline Improvements

1. **Remove `continue-on-error: true` from all CI jobs**
   - `.github/workflows/lint-and-test.yml` — every job has `continue-on-error: true`, meaning CI never actually fails
   - Linting and testing becomes advisory-only, which defeats the purpose
   - Fix: Remove all `continue-on-error` flags; CI should block merges on failure

2. **Switch to GitHub OIDC for AWS authentication**
   - Current: Long-lived `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` in GitHub secrets
   - Fix: Configure an IAM OIDC identity provider for GitHub Actions; use short-lived STS tokens
   - More secure, no key rotation needed, follows AWS best practices

3. **Add PR-level CDK diff as a comment**
   - `.github/workflows/cdk-diff.yml` runs `cdk diff` but doesn't post results to the PR
   - Fix: Capture diff output and post as a PR comment using `peter-evans/create-or-update-comment` or similar
   - Allows reviewers to see infrastructure changes directly in the PR

4. **Add skin asset build step**
   - Wiki7 skin has JS/CSS resources that should be validated/built
   - Add a build step that runs any necessary skin compilation
   - Add visual regression testing if feasible (Percy, Playwright screenshots)

5. **Add security scanning**
   - Add `trivy` or `grype` container image scanning in CI
   - Add `npm audit` for CDK dependencies
   - Add `pip-audit` or `safety` for Python dependencies

### CD Pipeline — Deployment Safety

6. **Add pre-deployment database backup**
   - Before `cdk deploy`, trigger an on-demand AWS Backup or RDS snapshot
   - Ensures a known-good state exists before any infrastructure changes
   - Add as a step in `deploy.yml` before the CDK deploy step

7. **Add post-deployment health check**
   - After deployment, curl the site health endpoint and verify HTTP 200
   - If health check fails, alert (and optionally roll back ECS to previous task definition)
   - Use ECS deployment circuit breaker feature

8. **Add deployment notifications**
   - Post deployment status to Slack/Discord/email
   - Include: commit SHA, deployer, duration, success/failure, link to CloudWatch

9. **Add manual approval gate for production**
   - Use GitHub Environments with `required_reviewers` for the production environment
   - Staging deploys automatically; production requires manual approval

### Staging Environment

10. **Create a staging CDK stack**
    - Add `Wiki7StagingStack` in `cdk/bin/wiki7.ts` using the same constructs but with:
      - Smaller resources (256 CPU / 512MB Fargate, t3.micro RDS)
      - Separate domain: `staging.wiki7.co.il`
      - Separate S3 bucket, separate RDS instance
      - Same WAF rules (or a subset)
      - No CloudFront (direct ALB access is fine for staging)
    - Parameterize constructs to accept environment config (prod vs staging)

11. **Add prod-to-staging data sync**
    - Create a GitHub Action (manual trigger) or Lambda that:
      - Takes the latest RDS automated snapshot from production
      - Restores it to the staging RDS instance (replacing existing data)
      - Optionally sanitizes sensitive data (user emails, passwords)
    - This gives staging real data for testing without manual effort

12. **Add PR preview deployments (optional, if budget allows)**
    - On PR open: deploy a lightweight staging stack for that PR
    - On PR merge/close: tear it down
    - Alternative: single persistent staging that deploys the PR branch on push

### Workflow Restructuring

13. **Create separate workflow files by concern**
    - `ci.yml` — lint + test (runs on every PR and push)
    - `deploy-staging.yml` — deploy to staging (on push to `develop` or manual)
    - `deploy-production.yml` — deploy to prod (on push to `master` with approval gate)
    - `data-sync.yml` — sync prod data to staging (manual trigger)
    - `cdk-diff.yml` — infrastructure review on PR (already exists, enhance it)

14. **Add branch protection rules documentation**
    - Document required branch protection: require CI pass, require 1 review, no force push to master
    - Add a `CONTRIBUTING.md` with the PR/review/deploy workflow

---

## Stage 3: MediaWiki & Skin Upgrades

**Domain:** `docker/` (Dockerfile, LocalSettings.php, skins/, extensions/)

### Research Phase
- Check latest MediaWiki stable version (currently using 1.43; check if 1.44 or later is available as of Feb 2026)
- Check latest Citizen skin version (currently using 3.1.0 in both Citizen base and Wiki7 fork)
- Check Citizen skin changelog since 3.1.0 — identify new features, breaking changes, and files modified
- Research Citizen skin's approach to child themes or extension points (to avoid maintaining a full fork)
- Check extension compatibility matrix: Cargo, PageForms, AWS, VisualEditor, Scribunto with the target MW version
- Research MediaWiki upgrade path: database migrations, extension API changes, skin API changes
- Check if Citizen skin has switched to Codex (MediaWiki's new design system) in newer versions

### Skin Upgrade Strategy

1. **Audit Wiki7 vs Citizen diff — identify actual customizations**
   - Run a comprehensive file-by-file diff between `docker/skins/Citizen/` and `docker/skins/Wiki7/`
   - Categorize changes into:
     - **Namespace renames** (Citizen → Wiki7, SkinCitizen → SkinWiki7) — mechanical, can be re-applied
     - **Config renames** (wgCitizen* → wgWiki7*) — mechanical
     - **Functional changes** (Hebrew fonts, RTL fixes, accessibility, custom components) — these are the real diffs
     - **i18n changes** (Hebrew translations, custom messages) — can be maintained separately
   - Create a manifest file documenting each intentional change and why

2. **Upgrade Citizen base to latest version**
   - Update `docker/skins/Citizen/` to the latest upstream release
   - This is the "clean base" that Wiki7 forks from

3. **Re-apply Wiki7 customizations on top of upgraded Citizen**
   - Using the manifest from step 1, re-apply each functional change to the new Citizen base
   - Resolve any conflicts where Citizen changed the same code
   - Test each customization individually

4. **Consider switching to a child-skin architecture** (if Citizen supports it)
   - Instead of maintaining a full fork, explore if Wiki7 can be a "child skin" that overrides only specific templates, styles, and components
   - This would dramatically reduce the maintenance burden for future upgrades
   - If not natively supported, consider using MediaWiki's skin inheritance + resource module overrides

### MediaWiki Core Upgrade

5. **Upgrade MediaWiki Docker base image**
   - Update `docker/Dockerfile` FROM tag to the latest stable version
   - Run `php maintenance/run.php update` to apply database schema changes
   - Test all extensions load correctly
   - Test LocalSettings.php compatibility

6. **Upgrade extensions**
   - Update Cargo, PageForms submodules to versions compatible with new MW
   - The AWS S3 extension is installed via composer in Dockerfile — update `COMPOSER_ROOT_VERSION`
   - Update bundled extensions (they come with the MediaWiki Docker image, so they update automatically)

7. **Update LocalSettings.php for new MW version**
   - Check for deprecated configuration options
   - Check for new recommended settings
   - Update `$wgMainCacheType` — consider using APCu explicitly instead of `CACHE_ACCEL`
   - Add `$wgWikiEditorEnabled = true;` if needed for newer MW versions

### Docker Improvements

8. **Optimize Dockerfile**
   - Current Dockerfile runs `composer update` at build time (slow, non-deterministic)
   - Pin composer dependencies with `composer.lock` committed to the repo
   - Use multi-stage build: build stage for composer install, runtime stage for the final image
   - Add health check: `HEALTHCHECK CMD curl -f http://localhost/ || exit 1`

9. **Update docker-compose.yml MariaDB version**
   - Match local dev MariaDB version to production RDS version (both should be same major version)
   - Currently both use 10.5; update both to the target version from Stage 1

10. **Add missing extensions to Docker build**
    - Verify all extensions in LocalSettings.php are actually present in the image
    - Some bundled extensions (Echo, LoginNotify, Thanks, etc.) come with the base image — verify they exist for the target MW version

### Testing

11. **Add skin visual regression tests**
    - Use Playwright or Puppeteer to screenshot key pages (Main Page, article, search, user page)
    - Compare before/after upgrade
    - Automate in CI

12. **Add MW upgrade smoke test**
    - Start the Docker container with docker-compose
    - Run `php maintenance/run.php update`
    - Verify the main page loads
    - Verify VisualEditor loads
    - Verify Cargo tables work
    - Automate in CI as an integration test

---

## Stage 4: Data Pipeline Completion & Automation

**Domain:** `data/` (all subdirectories)

### Research Phase
- Check Transfermarkt's current anti-scraping measures and whether ScraperAPI or direct scraping works better (as of Feb 2026)
- Research what additional data is available: match events (goals, cards, subs), season statistics, historical seasons, coaching staff, stadium info
- Research Transfermarkt's data structure for Israeli Premier League specifically
- Research rate limiting best practices for respectful scraping
- Check if Transfermarkt has an official API (they launched one in 2023 — check current status and pricing)
- Research how other MediaWiki sports wikis structure their data (e.g., Football Wiki, Wikipedia football articles)

### Pipeline Gaps & Fixes

1. **Add match event data to scraper**
   - Current `match_spider.py` scrapes match metadata (date, opponent, result, venue)
   - Missing: goals (scorer, minute), cards, substitutions, formation details, possession stats
   - This data makes match pages actually useful
   - Add fields to the match spider and update the Pydantic schema

2. **Add historical season data support**
   - Pipeline defaults to `--season 2024`
   - Add support for scraping multiple seasons in one run: `--season 2020-2024`
   - Add season-aware output directories to avoid overwriting

3. **Fix Hebrew name mapping**
   - `data/data_pipeline/apply_hebrew_mapping.py` exists but needs a complete mapping file
   - Research: Can we use an API or Wikipedia to automatically map English player names to Hebrew?
   - Create/expand the Hebrew mapping YAML with all current and historical squad members

4. **Add data validation layer**
   - Add post-scrape validation: check for empty fields, impossible dates, duplicate entries
   - Add cross-reference validation: every player in squad.json should appear in players.json
   - Log warnings for data quality issues

5. **Expand Jinja2 templates for richer wiki pages**
   - `player_page.j2` — add match statistics section, image placeholder, external links (Transfermarkt profile)
   - `match_report.j2` — add lineup, substitutions, goals, cards sections
   - Add new templates: `season_overview.j2`, `competition_standings.j2`, `coach_page.j2`
   - All templates should use Cargo `#cargo_store` for structured data queries

6. **Add fixture schedule page**
   - The fixtures spider exists but there's no `import_fixtures.py` or template
   - Add fixture import that creates/updates a season schedule page
   - Should show upcoming matches with dates, times, venues, broadcast info

### Automation

7. **Design the pipeline execution strategy**
   - **Initial load**: Full scrape of all available seasons (2020-2025) → normalize → import
   - **Ongoing updates**:
     - During season: Weekly automated run (squad changes, match results, transfers)
     - Transfer windows: Daily runs during January and summer windows
     - Off-season: Monthly runs for market value updates

8. **Create a pipeline Lambda/ECS task for automated runs**
   - Package the pipeline as a Docker container (extend existing `Dockerfile.scraper`)
   - Deploy as an ECS Scheduled Task (EventBridge rule → ECS Fargate task)
   - Or: Lambda + Step Functions for orchestration (scrape → normalize → import steps)
   - Store output artifacts in S3 for auditing

9. **Add admin approval workflow for data changes**
   - On automated pipeline run:
     - Generate a diff of what would change (dry-run mode)
     - Post the diff summary to a designated wiki page or notification channel (email/Slack)
     - Wiki admin reviews and approves
     - On approval: run the actual import
   - Implementation: Pipeline runs with `--dry-run` first, stores diff in S3, triggers notification
   - Admin clicks an approval link (Lambda + API Gateway) that triggers the real import

10. **Add pipeline monitoring and alerting**
    - CloudWatch metrics: scrape success/failure, number of pages created/updated/failed, pipeline duration
    - CloudWatch alarms: alert on pipeline failure, alert on zero pages updated (may indicate scraping blocked)
    - Store run history/logs in S3 for debugging

### Testing

11. **Expand pipeline test coverage**
    - Current: tests exist but coverage is partial
    - Add: integration test that runs the full pipeline in dry-run mode against fixture data
    - Add: spider contract tests (verify expected fields are present in output)
    - Add: template rendering tests (verify generated wikitext is valid)
    - Mock the mwclient Site for import tests

12. **Add data quality dashboard**
    - Create a wiki page (auto-updated by pipeline) showing:
      - Last pipeline run date and status
      - Data coverage: X players, Y matches, Z transfers
      - Data freshness: how old the latest data is
      - Known issues or missing data

---

## Stage 5: Cross-Cutting Concerns

These items span multiple domains and should be addressed after the main stages, or in parallel where they don't conflict.

### Documentation Overhaul

1. **Rewrite `README.md`**
   - Current README needs updating to match actual project state
   - Add: quick start guide, architecture summary, contributing guide link
   - Add: badges (CI status, last deploy, test coverage)

2. **Rewrite `docs/architecture.md`**
   - Remove references to non-existent components (CodePipeline, Nginx, sidecar containers, private subnets, Multi-AZ RDS)
   - Add actual architecture: GitHub Actions CI/CD, public subnets, single-AZ RDS, staging environment
   - Update the ASCII diagram to match reality
   - Add cost breakdown table

3. **Create `CONTRIBUTING.md`**
   - Branch naming conventions
   - PR workflow (create branch → PR → CI passes → review → merge → auto-deploy staging → approve → deploy prod)
   - Skin development guidelines (how to sync with upstream Citizen)
   - Data pipeline development guidelines

4. **Update `BACKLOG.md`**
   - Currently just "Font Selection in Preferences Menu" and "More..."
   - Populate with actual known issues and feature requests
   - Prioritize items

5. **Create `docs/runbook.md`**
   - How to deploy manually
   - How to rollback a deployment
   - How to restore the database from backup
   - How to run the data pipeline manually
   - How to add a new MediaWiki extension
   - How to sync Citizen skin updates
   - Common troubleshooting scenarios

### Directory Structure Cleanup

6. **Consider restructuring top-level directories**
   - Current structure is clean but some naming could be clearer:
     - `docker/` → could stay as-is (it's the MediaWiki application)
     - `data/` → clear
     - `cdk/` → clear
     - `docs/` → clear
   - Ensure `.gitignore` covers all generated files
   - Clean up any stale files or directories

7. **Verify .gitmodules and submodule health**
   - Check that Cargo and PageForms submodules point to the correct upstream repos and branches
   - Ensure submodule versions are compatible with target MW version

### Monitoring & Observability (Production Readiness)

8. **Add CloudWatch alarms**
   - ECS: task count < 1 (service down), CPU > 80% sustained
   - RDS: free storage < 2GB, CPU > 80%, connection count > 50
   - ALB: 5xx error rate > 5%, target response time > 5s
   - CloudFront: 5xx error rate > 1%

9. **Add a health check endpoint in MediaWiki**
   - Create a simple PHP health check that verifies DB connectivity
   - Use as ALB health check target instead of `/` (which is a full page render)
   - Faster, lighter, more reliable health checking

10. **Consider adding basic uptime monitoring**
    - External health check service (e.g., UptimeRobot free tier, or Route53 health checks)
    - Alerts when site is down from outside AWS

---

## Execution Order & Dependencies

```
Stage 1 (Infrastructure)  ──────────────────────────> can start immediately
Stage 2 (CI/CD + Staging) ──────────────────────────> can start immediately (parallel with Stage 1)
Stage 3 (MW + Skin Upgrade) ─────────────────────────> depends on Stage 1 (MariaDB version, Docker changes)
Stage 4 (Data Pipeline) ─────────────────────────────> depends on Stage 3 (needs working MW to test imports)
Stage 5 (Cross-Cutting) ─────────────────────────────> ongoing, finalized after Stages 1-4
```

Stages 1 and 2 are fully independent and can run in parallel.
Stage 3 has a soft dependency on Stage 1 (MariaDB version alignment) but can start research/planning immediately.
Stage 4 depends on Stage 3 being complete (needs the upgraded MW instance to test imports against).
Stage 5 documentation should be updated incrementally as each stage completes.

---

## Estimated Monthly Cost After Stage 1

| Service | Current | After Changes | Notes |
|---------|---------|--------------|-------|
| ECS Fargate | ~$15 | ~$15-30 | $15 base + auto-scale to $30 peak |
| RDS MariaDB | ~$15 | ~$15 | t3.micro unchanged |
| CloudFront | ~$1-5 | ~$1-5 | Depends on traffic |
| WAF | ~$8 | ~$11 | +$3 for SQLi + PHP rules |
| S3 | ~$0.15 | ~$0.15 | Negligible |
| Route53 | ~$0.50 | ~$0.50 | |
| Backups | ~$2 | ~$2 | |
| KMS | ~$1 | ~$1 | Backup key |
| Staging (on-demand) | $0 | ~$5-15 | Only when active |
| **Total** | **~$42** | **~$50-80** | |
