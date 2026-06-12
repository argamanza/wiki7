.PHONY: help setup docker-up docker-down docker-logs docker-restart docker-reset docker-shell docker-update-db \
	test lint cdk-synth cdk-diff cdk-deploy \
	pipeline-install pipeline-scrape pipeline-normalize pipeline-dry-run pipeline-import

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

# --- Setup ---
setup: ## Initial project setup
	cp -n .env.example .env || true
	git submodule update --init --recursive
	cd docker && docker compose build

# --- Docker (Local Development) ---
docker-up: ## Start local development environment
	cd docker && docker compose up -d

docker-down: ## Stop local development environment
	cd docker && docker compose down

docker-logs: ## Follow logs from all containers
	cd docker && docker compose logs -f

docker-restart: ## Restart all containers
	cd docker && docker compose restart

docker-reset: ## Reset everything (WARNING: deletes database!)
	cd docker && docker compose down -v
	cd docker && docker compose up -d --build

docker-shell: ## Open a shell in the MediaWiki container
	cd docker && docker compose exec mediawiki bash

docker-update-db: ## Run MediaWiki database update
	cd docker && docker compose exec mediawiki php maintenance/run.php update

# --- Testing ---
# No `|| true` here on purpose: `make test` must FAIL when tests fail, or it's
# useless as a pre-push / scripted gate.
test: ## Run all tests
	@echo "Running data pipeline tests..."
	cd data && uv run pytest --tb=short -v
	@echo "Running CDK tests..."
	cd cdk && npm test

lint: ## Run all linters
	@echo "Running Python linter..."
	cd data && uv run ruff check .

# --- CDK Infrastructure ---
cdk-synth: ## Synthesize CDK stacks (uses cached context; no AWS creds needed)
	cd cdk && CDK_DEFAULT_ACCOUNT=$${CDK_DEFAULT_ACCOUNT:-368127906643} CDK_DEFAULT_REGION=$${CDK_DEFAULT_REGION:-il-central-1} npx cdk synth

cdk-diff: ## Show CDK diff against deployed stacks
	cd cdk && npx cdk diff

cdk-deploy: ## Deploy CDK stacks (requires AWS credentials)
	cd cdk && npx cdk deploy --all

# --- Data Pipeline ---
# The pipeline is managed with uv (see data/uv.lock); `uv sync` creates/updates
# the project venv, `uv run` executes inside it — no manual activation needed.
pipeline-install: ## Install data pipeline dependencies
	cd data && uv sync

pipeline-scrape: ## Run the full scraping pipeline
	cd data && uv run python run_pipeline.py --skip-normalize --skip-import

pipeline-normalize: ## Run data normalization
	cd data && uv run python run_pipeline.py --skip-scrape --skip-import

pipeline-dry-run: ## Preview wiki import (no actual changes)
	cd data && uv run python run_pipeline.py --skip-scrape --dry-run -v

pipeline-import: ## Import data to wiki (requires WIKI_URL)
	cd data && uv run python run_pipeline.py --skip-scrape -v
