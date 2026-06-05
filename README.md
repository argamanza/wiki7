# wiki7 - Hapoel Beer Sheva Fan Wiki

A MediaWiki-based fan wiki for **Hapoel Beer Sheva FC** at [wiki7.co.il](https://wiki7.co.il), featuring a custom Hebrew RTL skin, automated data pipeline from Transfermarkt, and AWS infrastructure managed with CDK.

## Project Structure

```
wiki7/
├── docker/          # MediaWiki container (1.45.3), custom Wiki7 skin, LocalSettings.php
│   ├── skins/Wiki7/ # Custom skin (forked from Citizen v3.17.0) — brand-red #C8102E, Hebrew RTL,
│   │                #   "you are here" indicator, drawer footer social links
│   │                #   (see docs/wiki7-skin-customization.md for the brand-delta inventory + re-fork recipe)
│   └── extensions/  # Cargo, PageForms
├── data/
│   ├── tmk-scraper/ # Scrapy spiders scraping Transfermarkt (squad, player, fixtures, match)
│   ├── data_pipeline/ # Pydantic normalization: raw JSON → structured JSONL
│   ├── wiki_import/   # mwclient + Jinja2: JSONL → MediaWiki pages
│   ├── run_pipeline.py # CLI orchestrator (scrape → normalize → import)
│   └── tests/         # pytest suite (48 tests)
├── cdk/             # AWS CDK (TypeScript) infrastructure — ECS Fargate, RDS, CloudFront, WAF, S3
│   └── test/        # Jest CDK tests (21 tests)
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
make test               # Python (48) + CDK (21) tests

# 4. Data pipeline
make pipeline-install   # Install Python dependencies
make pipeline-dry-run   # Preview wiki pages without importing
```

## Data Pipeline

Scrapes Transfermarkt for Hapoel Beer Sheva player/match data and imports it into MediaWiki.

```
Scrapy spiders → Raw JSON → Pydantic normalization → JSONL → Jinja2 templates → MediaWiki pages
```

```bash
# Full pipeline (requires SCRAPERAPI_KEY)
cd data && python run_pipeline.py --season 2024 --verbose

# Skip scraping, just normalize + import
python run_pipeline.py --skip-scrape --wiki-url http://localhost:8080

# Dry run — preview generated pages
python run_pipeline.py --skip-scrape --dry-run --verbose
```

Generated page types: player pages (infobox, transfer history, market values), match reports, Cargo table templates, season squad pages, transfer summary pages.

## AWS Infrastructure

CDK stacks deploy to `il-central-1` (main) and `us-east-1` (CloudFront/WAF):

| Component | Service | Cost |
|-----------|---------|------|
| Compute | ECS Fargate (512 CPU / 1024 MB) | ~$15/mo |
| Database | RDS MariaDB 10.5 (db.t3.micro) | ~$15/mo |
| CDN | CloudFront | ~$1-5/mo |
| WAF | CoreRuleSet + GeoBlock + RateLimit | ~$8/mo |
| Storage | S3 (versioned) | ~$0.15/mo |
| DNS | Route53 | ~$0.50/mo |
| Backups | AWS Backup (7-day retention) | ~$2/mo |

```bash
make cdk-synth    # Validate templates
make cdk-diff     # Preview changes
make cdk-deploy   # Deploy (requires AWS credentials)
```

## CI/CD

GitHub Actions workflows:

- **lint-and-test.yml** — Runs on every push: PHP lint, JS lint, CDK tests, Python tests + pipeline dry-run
- **deploy.yml** — Runs on push to master: `cdk deploy --all`
- **cdk-diff.yml** — Runs on PRs touching `cdk/`: posts CDK diff as PR comment

AWS credentials use GitHub OIDC (no AWS secrets needed). Required GitHub Secrets: `WG_SECRET_KEY`, `WG_UPGRADE_KEY`, `SCRAPERAPI_KEY`

## Environment Variables

See [`.env.example`](.env.example) for all required variables. Key ones:

| Variable | Purpose |
|----------|---------|
| `WG_SECRET_KEY` | MediaWiki session signing (64-char hex) |
| `WG_UPGRADE_KEY` | MediaWiki web installer key (16-char hex) |
| `SCRAPERAPI_KEY` | ScraperAPI key for Transfermarkt scraping |
| `MEDIAWIKI_DB_PASSWORD` | Database password for local dev |

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
- [**`architecture.md`**](docs/architecture.md) — reflects what the CDK code actually deploys (multi-stack ECS Fargate + RDS + CloudFront, currently torn down — see revival plan for the rebuild path).
- [`roadmap.md`](docs/roadmap.md) — *historical*. Superseded by the revival plan.

Repo-root planning docs ([`PLAN.md`](PLAN.md), [`BACKLOG.md`](BACKLOG.md)) are task banks fed by the revival plan, not the other way around.

## License

This project is for fan/educational purposes. MediaWiki is licensed under GPL-2.0.
