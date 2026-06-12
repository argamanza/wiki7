# Wiki7 Architecture

> **Updated 2026-06-12.** This document reflects what the CDK code actually provisions
> and the live deployed state. Earlier revisions of this file described (a) an
> aspirational architecture that was never built, then (b) the pre-relaunch torn-down
> state and the old Fargate+ALB design — both now history. The architecture decision
> trail lives in [`adr/0001-single-ec2-vs-fargate-alb.md`](adr/0001-single-ec2-vs-fargate-alb.md);
> the roadmap lives in [`revival-plan.md`](revival-plan.md).

## TL;DR current state

The site is **live at https://wiki7.co.il** (account `368127906643`, primary region
`il-central-1`), relaunched 2026-06-06 on the "Option B" architecture:

- **Single Graviton EC2** (`t4g.small`, AL2023, IMDSv2-only) running two Docker
  containers: the MediaWiki 1.45.3 image built by CDK, and a Redis 7 cache sidecar.
- **RDS MariaDB 11.4** (`db.t4g.micro`) with **deletion protection, snapshot-on-delete,
  7-day automated backups + PITR**, plus an AWS Backup vault (daily 7-day + monthly
  365-day rules). A restore drill was executed and verified 2026-06-06.
- **CloudFront** in front of everything (TLS, edge caching of static assets *and* anon
  HTML per Phase 2.5b), **WAF** (us-east-1, CLOUDFRONT scope), **S3** for uploads/assets
  via OAC.
- Monthly cost ≈ **$47-52** (see ADR-0001 for the line-item breakdown).

History note: the original 2025 deployment (Fargate+ALB era) was torn down for cost and
its database was **permanently lost** (`removalPolicy: DESTROY`, no snapshots). That event
drives this architecture's non-negotiables: managed DB, deletion protection, verified
restore path. Content was rebuilt from git seed pages + the data pipeline.

---

## What the CDK code provisions (`cdk/`)

The CDK app (TypeScript, entry `cdk/bin/wiki7.ts`) creates **five stacks** across two
regions. The `*-stack.ts` files under `cdk/lib/` are mostly `Construct`s that synthesize
into the single `Wiki7CdkStack` template — not separate CloudFormation stacks.

| Stack | Region | Provisions |
|---|---|---|
| `Wiki7DnsStack` | il-central-1 | Route53 hosted zone; exports zone id/name to SSM |
| `Wiki7CertificateStack` | us-east-1 | ACM cert for apex + www (CloudFront requires us-east-1); SSM-synced to il-central-1 |
| `Wiki7WafStack` | us-east-1 | CLOUDFRONT-scoped WAF WebACL (geo-block, managed rules, rate limit); SSM-synced |
| `Wiki7CdkStack` | il-central-1 | The app: VPC, RDS, EC2 compute, S3, CloudFront, backups, observability, GuardDuty |
| `Wiki7GitHubOidcStack` | il-central-1 | GitHub Actions OIDC roles (deploy + read-only PR diff; no long-lived keys) |

Cross-region wiring is done via **SSM Parameter Store** (a custom `CrossRegionSsmSync`
construct copies the cert ARN and WAF ARN from us-east-1 to il-central-1), because native
CDK cross-stack refs don't cross regions. *(Known weakness: the sync is async fire-and-forget
with no dependency ordering and no re-sync on change — candidate for replacement with CDK's
`crossRegionReferences: true`.)*

### Components (as coded, `cdk/lib/`)

- **Networking** (`network-stack.ts`) — VPC, `maxAzs: 2`, **no NAT gateway** (cost),
  **public subnets only**, S3 gateway endpoint. EC2 and RDS both live in public subnets;
  RDS is `publiclyAccessible: false` and SG-restricted.
- **Database** (`database-stack.ts`) — single **RDS MariaDB 11.4.9 LTS**, `db.t4g.micro`
  Graviton, single-AZ, encrypted, `deletionProtection: true`, `removalPolicy: SNAPSHOT`,
  7-day automated backups + PITR, dedicated SG accepting 3306 only from the MediaWiki SG.
  Maintenance/backup windows on the Israeli weekend (Fri night UTC).
- **Compute** (`compute-stack.ts`) — one **EC2 t4g.small**: UserData installs Docker,
  pulls the CDK-built ARM64 MediaWiki image from ECR, runs it alongside a Redis sidecar
  (256 MB, allkeys-lru, no persistence) on a shared bridge network, then self-attaches
  the Elastic IP once MediaWiki serves HTTP (last step — keeps replacement downtime to
  seconds). Secrets (DB/admin/secret-key/upgrade-key/Telegram token) are fetched from
  **Secrets Manager** at boot into a chmod-0600 env-file (never on a logged command line).
  A host cron drains the MW job queue every minute (`$wgJobRunRate = 0`). SSM Session
  Manager instead of SSH (no port 22); port 80 ingress only from the CloudFront
  origin-facing prefix list. Status-check alarm → `ec2:recover`. Weekly SSM Patch Manager
  window. `Wiki7-GenerateSitemap` SSM document for on-demand sitemap → S3.
- **CDN** (`cloudfront-stack.ts`) — CloudFront, PriceClass_100, HTTP/2+3:
  static behaviors (`/load.php`, `/skins/*`, `/extensions/*`, `/resources/*`, `/images/*`,
  `/assets/*`) long-cached; the **default behavior uses the `Wiki7DynamicHtml` cache
  policy** (Phase 2.5b): anon HTML cached up to `$wgCdnMaxAge` (10 min), keyed on the
  auth-bearing cookies only (`wikidbUserID`, `wikidbToken`, `sessionJwt`) so logged-in
  HTML is never cached. Origin requests carry `CloudFront-Viewer-Address` (managed
  origin-request policy) which LocalSettings.php rewrites into `REMOTE_ADDR`.
  www→apex redirect function; security-headers policy. Origin protocol is **HTTP-only**
  (CloudFront→EC2 TLS is a Phase 4 item).
- **WAF** (`wiki7-waf-stack.ts`) — geo-block (17 countries), AWS managed Common +
  KnownBadInputs + SQLi + PHP rule sets, per-IP rate limit (evaluated *before* the
  crawler allow-list so a spoofed bot UA can't bypass it), crawler allow-list, generic
  bot-UA heuristic block. Block-only logging to CloudWatch.
- **Storage** (`compute-stack.ts`) — versioned S3 bucket, `BLOCK_ALL` public access,
  `BUCKET_OWNER_ENFORCED` (no ACLs), CloudFront OAC only, 30-day noncurrent-version
  retention; `docker/assets/` ships via `BucketDeployment` on every deploy.
- **Backups** (`backup-stack.ts`) — AWS Backup vault + KMS key, daily 7-day +
  monthly 365-day RDS rules, on top of RDS automated backups.
- **Observability** (`observability-stack.ts`) — 6 CloudWatch alarms (RDS storage/CPU,
  EC2 CPU, CloudFront 5xx, MW app-error and Redis-exception log filters) → SNS email;
  single `wiki7` dashboard. *(Known limitation: the CloudFront 5xx alarm can't evaluate
  cross-region from il-central-1 and is permanently INSUFFICIENT_DATA; the real fix is a
  us-east-1 sibling alarm — Phase 4.)*
- **Threat detection** — account-level GuardDuty detector (declared inside `Wiki7CdkStack`;
  note it disappears if that stack is ever destroyed).
- **CI/CD** (`.github/workflows/`) — GitHub Actions via OIDC: `lint-and-test.yml`
  (lint advisory, tests blocking), `cdk-diff.yml` (read-only diff role, sticky PR comment),
  `deploy.yml` (master → CDK tests → `cdk deploy --all` → health check against the live site).

### Request flow

```
User → Route53 (wiki7.co.il) → CloudFront (TLS, WAF)
            ├── /load.php, /skins/*, /extensions/*, /resources/*  → EC2 :80 (long edge TTL)
            ├── /images/*, /assets/*                              → S3 (OAC, cached)
            └── everything else (Wiki7DynamicHtml policy)         → EC2 :80 (anon HTML ≤10 min)
EC2: Apache/MediaWiki container ←bridge→ Redis sidecar; → RDS MariaDB :3306 (in-VPC)
TLS terminates at CloudFront; CloudFront→EC2 and MW→RDS are plaintext inside AWS (Phase 4 items).
```

---

## Application layer (`docker/`)

- **Image:** official `mediawiki:1.45.3` (PHP 8.3, Apache) + pinned extras (PECL redis,
  Composer, mariadb-client). Extensions: **Cargo** + **PageForms** (git submodules),
  **AWS S3**, **TabberNeue**, **Description2**, **WikiSEO**, **ApprovedRevs**, **Lockdown**
  (Dockerfile-installed, pinned), **Wiki7ReviewGate** (in-repo), plus core-bundled
  extensions (Echo, VisualEditor, Scribunto, …). Skins: **Wiki7** (default), Citizen
  (kept verbatim as the upstream diff reference), Vector.
- **Skin:** `Wiki7` is a full fork/rename of **Citizen 3.17.0** with brand-red theming,
  drawer footer (social links), Hebrew web fonts, active-sidebar-item indicator. The
  brand-delta inventory + re-fork recipe is in
  [`wiki7-skin-customization.md`](wiki7-skin-customization.md).
- **Review gate (Phase 3.5):** bot writes land in the custom `Draft:` namespace
  (NS_DRAFT=3000, Lockdown-gated to reviewers) or as unapproved revisions on
  ApprovedRevs-gated pages; the in-repo **Wiki7ReviewGate** extension closes the
  title-enumeration leaks (RecentChanges/API/contribs surfaces) and dispatches Echo +
  Telegram notifications. Design: [`adr/0002-review-gate-architecture.md`](adr/0002-review-gate-architecture.md).
- **Content model:** **Cargo** — table-definition templates `#cargo_declare`, infoboxes
  `#cargo_store` (namespace-gated), collection pages `#cargo_query`. Seed pages live in
  `docker/wiki-pages/` and are imported idempotently on container start by
  `import-pages.php` (create-if-missing; force mode preserves pages edited since import).
- **Config:** `LocalSettings.php` branches on `WIKI_ENV` (production hardens, uses S3 +
  Redis, fail-loud on missing secrets; development is verbose + local storage).
  Hebrew is the content language; anonymous edits disabled.

## Data layer (`data/`)

Python pipeline (Scrapy → Pydantic → Hebrew translation → Jinja2 → mwclient) that scrapes
Transfermarkt (via ScraperAPI), normalizes, translates (Wikidata sitelinks → Wikipedia
langlinks → LLM fallback), and imports player/match/season/club pages through the review
gate. Currently being extended from single-season to all-time scope on branch
`phase-3a-r2/implementation` — see [`revival-plan.md`](revival-plan.md) §Phase 3a R2 and
[`research/0002-transfermarkt-data-surface.md`](research/0002-transfermarkt-data-surface.md).
