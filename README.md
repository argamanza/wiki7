# wiki7 - Hapoel Beer Sheva Fan Wiki

A MediaWiki-based fan wiki for **Hapoel Beer Sheva FC** at [wiki7.co.il](https://wiki7.co.il), featuring a custom Hebrew RTL skin, automated data pipeline from Transfermarkt, a bot-content review gate, and AWS infrastructure managed with CDK.

## Project Structure

```
wiki7/
├── docker/          # MediaWiki container (1.45.3), custom Wiki7 skin, LocalSettings.php
│   ├── skins/Wiki7/ # Custom skin (forked from Citizen v3.17.0) — brand-red #C8102E, Hebrew RTL,
│   │                #   "you are here" indicator, drawer footer social links
│   │                #   (see docs/wiki7-skin-customization.md for the brand-delta inventory + re-fork recipe)
│   └── extensions/  # Cargo + PageForms (submodules), Wiki7ReviewGate (in-repo review gate:
│                    #   Draft namespace + ApprovedRevs + Lockdown + Echo + Telegram notifications;
│                    #   AWS-S3 / TabberNeue / WikiSEO / Description2 / ApprovedRevs / Lockdown
│                    #   are installed by the Dockerfile)
├── data/
│   ├── tmk-scraper/ # Scrapy spiders scraping Transfermarkt (squad, player, fixtures, match, …)
│   ├── data_pipeline/ # Pydantic normalization + Hebrew translation: raw JSON → structured JSONL
│   ├── wiki_import/   # mwclient + Jinja2: JSONL → MediaWiki pages (routed through the review gate)
│   ├── run_pipeline.py # CLI orchestrator (scrape → normalize → import)
│   └── tests/         # pytest suite
├── cdk/             # AWS CDK (TypeScript) — single Graviton EC2 + RDS, CloudFront, WAF, S3
│   └── test/        # Jest CDK tests
├── .github/workflows/ # CI/CD: lint+test, deploy to AWS, CDK diff on PRs
└── Makefile         # Common targets for docker, test, lint, cdk, pipeline
```

## Quick Start

```bash
# 1. Clone and setup
git clone --recursive https://github.com/argamanza/wiki7.git
cd wiki7
cp .env.example .env   # Fill in your values
make setup

# 2. Start local development
make docker-up          # MediaWiki at localhost:8080, Adminer at localhost:8081

# 3. Run tests
make test               # Python (pytest) + CDK (Jest) suites

# 4. Data pipeline
make pipeline-install   # Install Python dependencies (uv)
make pipeline-dry-run   # Preview wiki pages without importing
```

## Data Pipeline

Scrapes Transfermarkt for Hapoel Beer Sheva player/match data and imports it into MediaWiki.

```
Scrapy spiders → Raw JSON → Pydantic normalization → Hebrew translation → JSONL
  → Jinja2 templates → MediaWiki pages (created in the Draft: namespace, gated by Wiki7ReviewGate)
```

```bash
# Full pipeline (requires SCRAPERAPI_KEY)
cd data && uv run python run_pipeline.py --season 2024 --verbose

# Skip scraping, just normalize + import
uv run python run_pipeline.py --skip-scrape --wiki-url http://localhost:8080

# Dry run — preview generated pages
uv run python run_pipeline.py --skip-scrape --dry-run --verbose
```

Generated page types: player pages (infobox, transfer history, market values), match reports, Cargo table templates, season squad pages, transfer summary pages. Bot writes land in the `Draft:` namespace (or as unapproved revisions on approved pages); a reviewer promotes them via `Special:MovePage` / ApprovedRevs — see [`docs/adr/0002-review-gate-architecture.md`](docs/adr/0002-review-gate-architecture.md).

## AWS Infrastructure

Architecture: **single Graviton EC2 (Docker: MediaWiki + Redis sidecar) + managed RDS**, fronted by CloudFront + WAF — see [`docs/adr/0001-single-ec2-vs-fargate-alb.md`](docs/adr/0001-single-ec2-vs-fargate-alb.md) for why this replaced the original Fargate+ALB design. CDK stacks deploy to `il-central-1` (main) and `us-east-1` (CloudFront cert/WAF):

| Component | Service | Cost |
|-----------|---------|------|
| Compute | EC2 t4g.small (Graviton, Docker) | ~$12/mo |
| Database | RDS MariaDB 11.4 (db.t4g.micro), deletion-protected + PITR | ~$14/mo |
| CDN | CloudFront (PriceClass_100, HTTP/2+3) | ~$1/mo |
| WAF | CoreRuleSet + SQLi/PHP + GeoBlock + RateLimit | ~$13/mo |
| Storage | S3 (versioned, BLOCK_ALL) | ~$0.15/mo |
| DNS | Route53 | ~$0.50/mo |
| Backups | RDS automated (7d) + AWS Backup vault (daily 7d + monthly 365d) | ~$2/mo |
| Threat detection | GuardDuty | ~$3-5/mo |

Total ≈ **$47-52/mo**. Observability: 6 CloudWatch alarms → SNS email, `wiki7` dashboard, UptimeRobot external check.

```bash
make cdk-synth    # Validate templates (uses cached context; no AWS creds needed)
make cdk-diff     # Preview changes
make cdk-deploy   # Deploy (requires AWS credentials)
```

## CI/CD

GitHub Actions workflows:

- **lint-and-test.yml** — PHP/JS/Python lint (advisory) + CDK and Python test suites (blocking)
- **deploy.yml** — push to master: CDK tests, then `cdk deploy --all` + post-deploy health check
- **cdk-diff.yml** — PRs touching `cdk/`: posts CDK diff as a sticky PR comment

AWS credentials use GitHub OIDC — a deploy role for master/production and a read-only diff role for PRs; **no AWS secrets and no app secrets live in GitHub**. Application secrets ($wgSecretKey, DB/admin/bot passwords, Telegram token) live in AWS Secrets Manager and are fetched by the EC2 instance at boot.

## Environment Variables (local dev + pipeline)

See [`.env.example`](.env.example) for all variables. Key ones:

| Variable | Purpose |
|----------|---------|
| `WG_SECRET_KEY` | MediaWiki session signing (local dev; prod uses Secrets Manager) |
| `WG_UPGRADE_KEY` | MediaWiki web installer key (local dev; prod uses Secrets Manager) |
| `SCRAPERAPI_KEY` | ScraperAPI key for Transfermarkt scraping |
| `MEDIAWIKI_DB_PASSWORD` | Database password for local dev |
| `WIKI_BOT_USER` / `WIKI_BOT_PASS` | Bot credentials for pipeline imports (see `data/BOT_SETUP.md`) |

## Development

```bash
make help              # Show all available targets
make docker-up         # Start local env
make docker-logs       # Follow container logs
make docker-shell      # Shell into MediaWiki container
make docker-update-db  # Run MediaWiki maintenance/update
make lint              # Run Python linter (ruff)
make test              # Run all tests
```

## Project Documentation

Living docs in [`docs/`](docs/):

- [**`revival-plan.md`**](docs/revival-plan.md) — the canonical "what we're doing and in what order" plan (Phase 0 → 4). Start here for any new session.
- [**`wiki7-skin-customization.md`**](docs/wiki7-skin-customization.md) — brand-delta inventory, re-fork recipe, gotchas. Read before touching `docker/skins/Wiki7/` or bumping the Citizen base.
- [**`architecture.md`**](docs/architecture.md) — reflects what the CDK code actually deploys (single Graviton EC2 + RDS + CloudFront, live at wiki7.co.il since the 2026-06-06 relaunch).
- [`adr/`](docs/adr/) — architecture decision records (EC2-vs-Fargate, review-gate design).
- [`reviews/`](docs/reviews/) — full-project review reports (latest: [2026-06-12](docs/reviews/2026-06-12-full-project-review.md)).
- [`roadmap.md`](docs/roadmap.md) — *historical*. Superseded by the revival plan.

Backlogs: [`docs/phase-3b-backlog.md`](docs/phase-3b-backlog.md) is the actively curated item list; the revival plan owns sequencing. Repo-root [`PLAN.md`](PLAN.md) and [`BACKLOG.md`](BACKLOG.md) are historical task banks — cross-check the revival plan before reusing anything from them.

## License

This project is for fan/educational purposes. MediaWiki is licensed under GPL-2.0.
