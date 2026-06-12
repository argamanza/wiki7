# Bot Account Setup (Local Docker Wiki)

> **Review-gate note (Phase 3.5+):** bot writes no longer land directly in public
> mainspace. With `WIKI_GATE_ENABLED=1` the pipeline routes new pages to the
> `Draft:` namespace and updates become unapproved revisions (ApprovedRevs);
> a reviewer promotes them via `Special:MovePage` / `Special:UnapprovedPages`.
> See `docs/adr/0002-review-gate-architecture.md`. The setup below still applies —
> it creates the account the gate-routed pipeline logs in with.

## Prerequisites

- Docker (with the compose plugin) installed
- Wiki7 Docker environment available (`wiki7/docker/`)

## Steps

### 1. Start the local wiki

```bash
cd wiki7/docker && docker compose up -d
```

Wait for it to be ready at http://localhost:8080 (first start takes ~30 seconds for DB init).

### 2. Log in as Admin

- Go to http://localhost:8080/index.php/Special:UserLogin
- Username: `Admin`
- Password: `AdminPass1234` (from docker-compose.yml)

### 3. Create a bot account

- Go to http://localhost:8080/index.php/Special:CreateAccount
- Username: `Wiki7Bot`
- Password: choose a password (e.g., `BotPass1234`)

### 4. Grant bot rights

- Go to http://localhost:8080/index.php/Special:UserRights
- Enter `Wiki7Bot` in the username field
- Add to groups: **bot**, **sysop** (sysop gives full edit rights for testing)
- Click Save

### 5. Configure environment variables

```bash
export WIKI_URL="localhost:8080"
export WIKI_BOT_USER="Wiki7Bot"
export WIKI_BOT_PASS="BotPass1234"
```

### 6. Run the pipeline

```bash
cd wiki7/data

# Dry run first (no wiki writes)
uv run python run_pipeline.py --season 2024 --dry-run -v

# Real run
uv run python run_pipeline.py --season 2024 -v

# Multi-season
uv run python run_pipeline.py --seasons 2015-2025 -v
```

(Dependencies are managed with uv — `make pipeline-install` / `uv sync` once first.)

## Troubleshooting

- **"Anonymous users cannot edit"**: Bot account not set up or env vars not exported
- **"Login failed"**: Check WIKI_BOT_USER/WIKI_BOT_PASS match what you created
- **Connection refused**: Docker wiki not running, check `docker compose ps`
- **Cargo table errors**: Run `php maintenance/run.php cargoRecreateData` inside the container
