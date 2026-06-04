# Wiki7 Architecture

> **Updated 2026-06-04.** This document was previously aspirational and described
> components that were never built (CodePipeline, Nginx, sidecar containers, Multi-AZ
> RDS, private subnets, PostgreSQL). It now reflects (a) what the CDK code actually
> provisions, (b) the current deployed state, and (c) the target for the rebuild.
> See [`revival-plan.md`](revival-plan.md) for the roadmap.

## TL;DR current state

The application is **not currently deployed.** The "heavy" stacks were torn down for
cost. What remains in AWS (account `368127906643`, `il-central-1`):

- `Wiki7DnsStack` — the `wiki7.co.il` Route53 hosted zone (the **`.co.il` domain itself
  has lapsed → NXDOMAIN**).
- `CDKToolkit` bootstrap stacks (both regions).
- **No** RDS, ECS, ALB, CloudFront, WAF, or media S3 bucket.
- ⚠️ **No RDS snapshots and an empty backup vault** — the prior production database is
  unrecoverable. A rebuild reconstructs content from git seed pages + the data pipeline.
- Current cost ≈ **$0.50/mo** (hosted zone).

---

## What the CDK code provisions (`cdk/`)

When deployed, the CDK app (TypeScript, entry `cdk/bin/wiki7.ts`) creates **five stacks**
across two regions. Note: the `*-stack.ts` files under `cdk/lib/` are mostly `Construct`s
that synthesize into the single `Wiki7CdkStack` template — not separate CloudFormation stacks.

| Stack | Region | Provisions |
|---|---|---|
| `Wiki7DnsStack` | il-central-1 | Route53 hosted zone; exports zone id/name to SSM |
| `Wiki7CertificateStack` | us-east-1 | ACM cert for apex + www (CloudFront requires us-east-1); SSM-synced to il-central-1 |
| `Wiki7WafStack` | us-east-1 | CLOUDFRONT-scoped WAF WebACL (geo-block, managed rules, rate limit); SSM-synced |
| `Wiki7CdkStack` | il-central-1 | The app: VPC, RDS, ECS/Fargate, ALB, S3, CloudFront, backups |
| `Wiki7GitHubOidcStack` | il-central-1 | GitHub Actions OIDC role (no long-lived keys) |

Cross-region wiring is done via **SSM Parameter Store** (a custom `CrossRegionSsmSync`
construct copies the cert ARN and WAF ARN from us-east-1 to il-central-1), because native
CDK cross-stack refs don't cross regions.

### Components (as coded)

- **Networking** — VPC, `maxAzs: 2`, **no NAT gateway** (cost), **public subnets only**.
  S3 gateway endpoint. (There is *no* private subnet tier, despite older docs.)
- **Database** — single **RDS MariaDB 10.5**, `db.t3.micro`, single-AZ, encrypted, in a
  public subnet but `publiclyAccessible: false`. ⚠️ `removalPolicy: DESTROY` +
  `deletionProtection: false` (the cause of the data loss — must change to SNAPSHOT +
  protection in the rebuild).
- **Compute** — ECS Fargate, **1 task** (512 CPU / 1024 MiB), no autoscaling, MediaWiki
  image built from `docker/`. Public subnet + public IP (needed for image pulls, no NAT).
- **Load balancing** — internet-facing ALB, **HTTP :80 only** (TLS terminates at CloudFront).
- **CDN** — CloudFront: default behavior → ALB (no caching, dynamic); `images/*` + `assets/*`
  → S3 via OAC (long TTL); www→apex redirect function; security-headers policy.
- **WAF** — geo-block (17 countries), AWS managed Common + KnownBadInputs, per-IP rate limit,
  MediaWiki pattern rules. ⚠️ bot-allow rule is ordered *after* the bot-block rule
  (legitimate crawlers get blocked).
- **Storage** — versioned S3 bucket (`RETAIN`). ⚠️ all four `BlockPublicAccess` flags are
  `false` (should be locked down; CloudFront uses OAC).
- **Backups** — AWS Backup vault + KMS key, daily RDS backup, 7-day retention. (Was empty at
  teardown — backup coverage must be verified in the rebuild.)
- **CI/CD** — GitHub Actions via OIDC: `lint-and-test.yml` (advisory-only — `continue-on-error`),
  `cdk-diff.yml` (PR comment), `deploy.yml` (`cdk deploy --all` on push to master).

### Request flow (when deployed)

```
User → Route53 (wiki7.co.il) → CloudFront → WAF
                                   ├── images/*, assets/*  → S3 (OAC, cached)
                                   └── everything else      → ALB :80 → Fargate (MediaWiki) → RDS MariaDB
TLS terminates at CloudFront; ALB↔origin and DB traffic are HTTP/SQL inside the VPC.
```

---

## Application layer (`docker/`)

- **Image:** official `mediawiki:1.43` (PHP 8.1, Apache). Extensions: **Cargo** + **PageForms**
  (git submodules), **AWS S3** (composer), **TabberNeue**; core-bundled extensions enabled in
  `LocalSettings.php`. Skins: **Wiki7** (default), Citizen, Vector.
- **Skin:** `Wiki7` is a full fork/rename of **Citizen 3.1.0** with brand-red theming, a drawer
  footer (social links), Hebrew web fonts, and a search keyboard hint.
- **Content model:** **Cargo** — table-definition templates `#cargo_declare`, infoboxes
  `#cargo_store`, collection pages `#cargo_query`. Seed pages live in `docker/wiki-pages/` and are
  imported idempotently on container start by `import-pages.php` (+ `cargo-repopulate.php`).
- **Config:** `LocalSettings.php` branches on `WIKI_ENV` (production hardens + uses S3 storage;
  development is verbose + local storage). Hebrew is the content language; anonymous edits disabled.

## Data layer (`data/`)

Python pipeline (Scrapy → Pydantic → Jinja2 → mwclient) that scrapes Transfermarkt, normalizes,
merges multi-season, auto-translates to Hebrew, and imports player/match/season/club pages. See
[`revival-plan.md`](revival-plan.md) §7 for known gaps.

---

## Target architecture (rebuild — decision pending)

The rebuild prioritizes **low cost** and **safe backups**. Two options are under consideration
(see [`revival-plan.md`](revival-plan.md) §5):

- **A. Single small instance** (~$10–16/mo) — one ARM instance running the existing
  `docker-compose` (MediaWiki + co-located MariaDB) on EBS, EBS snapshots for backup, optional
  CloudFront. *Recommended for cost.*
- **B. Optimized Fargate** (~$45–55/mo) — keep the CDK Fargate/RDS design, right-sized.

Either way, the rebuild must fix: DB snapshot-on-delete + deletion protection, verified restore,
S3 public-access lockdown, WAF bot-rule ordering, MariaDB ≥10.6, and CI enforcement.
