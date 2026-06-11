# Operational Bootstrap

One-time manual actions that aren't automated by CDK. Run these once per "fresh stack" event — if you tear down + rebuild from scratch, walk this list. For routine ops (rotation, recycle, deploy), see the related docs at the bottom.

## Prerequisites

- AWS CLI configured with the `argamanza` profile (account `368127906643`, primary region `il-central-1`).
- `gh` CLI authenticated.
- Telegram bot created via `@BotFather` (see step 1 below if not yet).

---

## 1. Telegram bot token → `Wiki7TelegramBotSecret`

CDK creates the secret with `{"botToken":""}` as a placeholder; the real token is operator-supplied because BotFather issues tokens out-of-band.

If the bot doesn't exist yet:
1. Open Telegram → `@BotFather`.
2. `/newbot` → pick a name + a `*_bot`-suffixed username.
3. Copy the token (`<digits>:<random>` format, ~46 chars). One-time view — store immediately.

```bash
BOT_TOKEN='<paste from BotFather>'
TG_ARN=$(aws secretsmanager list-secrets --profile argamanza --region il-central-1 \
  --query 'SecretList[?contains(Name,`TelegramBot`)].ARN | [0]' --output text)
aws secretsmanager put-secret-value --profile argamanza --region il-central-1 \
  --secret-id "$TG_ARN" --secret-string "{\"botToken\":\"$BOT_TOKEN\"}"
unset BOT_TOKEN
```

If the EC2 instance already exists at this point, the running container is holding the OLD (empty) token in its env. **Recycle the container** to pick up the new value — see [[wiki7-secret-rotation]] memory or `docker/scripts/recycle-wiki7.sh` (Phase 4 backlog item; for now, follow the memory's recipe).

After populating the secret, also set `$wgWiki7TelegramChatId` in `docker/LocalSettings.php` to the operator's Telegram chat_id. Get the chat_id by messaging `@userinfobot` on Telegram → it replies with `Id: <integer>`. Commit + deploy.

## 2. `Wiki7Bot` MediaWiki account

CDK provisions `Wiki7BotSecret` (auto-generated password) but does NOT create the actual MediaWiki user — that's done via the maintenance script against the live container.

```bash
BOT_PASS=$(aws secretsmanager get-secret-value --profile argamanza --region il-central-1 \
  --secret-id "$(aws secretsmanager list-secrets --profile argamanza --region il-central-1 \
    --query 'SecretList[?contains(Name,`Wiki7BotSecret`)].ARN | [0]' --output text)" \
  --query SecretString --output text | jq -r .password)

INSTANCE_ID=$(aws ec2 describe-addresses --profile argamanza --region il-central-1 \
  --filters "Name=public-ip,Values=16.164.90.60" \
  --query 'Addresses[0].InstanceId' --output text)

aws ssm send-command --profile argamanza --region il-central-1 \
  --instance-ids "$INSTANCE_ID" --document-name AWS-RunShellScript \
  --parameters "commands=[\"docker exec wiki7 php maintenance/run.php createAndPromote --custom-groups=bot --force Wiki7Bot $BOT_PASS\"]"
unset BOT_PASS
```

Verify with `curl -s "https://wiki7.co.il/api.php?action=query&list=users&ususers=Wiki7Bot&usprop=groups&format=json"` — should show `bot` in groups.

## 3. Promote the human operator to `reviewer`

Required for the operator to see drafts + approve unapproved revisions through the Phase 3.5 review gate.

```bash
INSTANCE_ID=$(aws ec2 describe-addresses --profile argamanza --region il-central-1 \
  --filters "Name=public-ip,Values=16.164.90.60" \
  --query 'Addresses[0].InstanceId' --output text)

aws ssm send-command --profile argamanza --region il-central-1 \
  --instance-ids "$INSTANCE_ID" --document-name AWS-RunShellScript \
  --parameters 'commands=["docker exec wiki7 php maintenance/run.php createAndPromote --custom-groups=reviewer --force Admin"]'
```

Note: `Admin` already exists (provisioned at MW install time via `MEDIAWIKI_ADMIN_PASSWORD` env-var). This command just adds them to the `reviewer` group. The command is idempotent — re-running is safe.

For additional reviewers in the future, repeat with their username instead of `Admin`.

## 4. Verify the gate is live end-to-end

Quick smoke test after steps 1-3:

```bash
# Anon should NOT see drafts via API
curl -s 'https://wiki7.co.il/api.php?action=query&list=allpages&apnamespace=3000&format=json' | jq .
# Expected: {"error":{"code":"permissiondenied",...}}

# Authenticated as Admin (in reviewer group) — should succeed
# (use the api.php login flow with Admin / $MEDIAWIKI_ADMIN_PASSWORD)
```

## 5. Pipeline-side SSL trust (corporate-MITM networks only)

If the operator's local network does TLS interception (corporate firewall / inspection proxy — e.g. CyberArk's `PaloSSL` MITM, Cisco Umbrella, Zscaler), Python's bundled `certifi` CA store won't trust the intercepted certificate chain even though curl + browsers will (those use the OS keychain, which is managed by IT and trusts the corporate root). Symptom when running `run_pipeline.py`:

```
SSLError: [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: self-signed certificate in certificate chain
```

Fix — install `pip-system-certs` into the data env (auto-patches `requests` to use the OS keychain via a `.pth` hook, no code changes required):

```bash
cd <repo>/data && uv pip install pip-system-certs
```

The package is intentionally NOT in `pyproject.toml` — operators on clean (non-MITM) networks don't need it and committing the dep would be unnecessary baggage. If you skip the pipeline (e.g. read-only API access), no SSL fix is needed.

To diagnose whether you're behind a MITM: `echo | openssl s_client -connect wiki7.co.il:443 -showcerts 2>/dev/null | grep 's:\|i:'` — if the root CA isn't a public one (DigiCert, ISRG, Let's Encrypt, AWS CA, etc.), you're being intercepted.

## 6. ScraperAPI key (data pipeline only)

If running the data pipeline locally, the bot needs a ScraperAPI key in env. The key is operator-supplied and lives in `~/.zshrc`:

```bash
echo 'export SCRAPERAPI_KEY=<your key>' >> ~/.zshrc
```

Used by `data/run_pipeline.py` to route requests through ScraperAPI (Transfermarkt blocks direct scraping). Not needed for read-only wiki operations.

**Plan tier:** Phase 3a R2's all-time scrape (1949-2025) consumed **7,893 credits** — see [[wiki7-scraperapi-baseline]]. The free tier (1,000 credits/month) is insufficient for an all-time pass; the Hobby tier ($49/mo, 100k credits) supports ~12 all-time iterations per month, plenty for the iteration-cycle phase. Per-season cost varies by data density: sparse historical (1949-1974) ~5-8 credits, modern (1985+) ~100-200 credits.

## 6a. WIKI7_ANTHROPIC_API_KEY — translation backend isolation

Phase 3a R2 PR B step 6 added a dedicated env var for the pipeline's Claude API access. **Use `WIKI7_ANTHROPIC_API_KEY`, not `ANTHROPIC_API_KEY`** — keeping them separate prevents pipeline runs from draining day-to-day Claude Code subscription credits (relevant after Anthropic's 2026-06-15 Agent SDK credit-pool split).

```bash
echo 'export WIKI7_ANTHROPIC_API_KEY=sk-ant-api03-...' >> ~/.zshrc
```

The pipeline reads `WIKI7_ANTHROPIC_API_KEY` first and only falls back to `ANTHROPIC_API_KEY` when the wiki7-specific one is unset. Day-to-day Claude Code subscription work (which reads `ANTHROPIC_API_KEY`) remains untouched.

When both are unset, the pipeline falls back to Google Translate with a warning. For Phase 3a R2 quality, export the key.

---

## 7. Reset prod content (safety valve for the hybrid workspace policy)

When iterating on prod gets messy (bad pipeline output, leftover drafts, want to start fresh), the `Wiki7ReviewGate:resetContent` maintenance script wipes Wiki7Bot-authored content. **Preserves** the docker-install seed homepage + sub-templates, users + groups, secrets, extensions, skin, LocalSettings (those aren't in the DB anyway).

### What it deletes

- All pages in NS_DRAFT (3000), regardless of author.
- Pages in NS_MAIN + NS_TEMPLATE + NS_FILE whose **first revision** was authored by Wiki7Bot (preserves the seed homepage + sub-templates, which were created by the install user during docker-entrypoint's `import-pages.php` run).
- All rows from every `cargo_*_data` table. Cargo schemas are recreated automatically on the next bot run via `#cargo_declare`.
- All rows from `approved_revs` + `approved_revs_files`. Approval state is reset; future bot edits trigger the normal "held back" semantics from scratch.
- All rows from `echo_event` + `echo_notification` where the event agent is Wiki7Bot.

### Invocation

Always run `--dry-run` first to see what would be touched:

```bash
INSTANCE_ID=$(aws ec2 describe-addresses --profile argamanza --region il-central-1 \
  --filters "Name=public-ip,Values=16.164.90.60" \
  --query 'Addresses[0].InstanceId' --output text)

# DRY RUN — prints what would be deleted, no changes
aws ssm send-command --profile argamanza --region il-central-1 \
  --instance-ids "$INSTANCE_ID" --document-name AWS-RunShellScript \
  --parameters 'commands=["docker exec wiki7 php maintenance/run.php extensions/Wiki7ReviewGate/maintenance/resetContent --dry-run"]' \
  --query 'Command.CommandId' --output text

# Then fetch the output:
aws ssm get-command-invocation --profile argamanza --region il-central-1 \
  --command-id <CMD_ID_FROM_ABOVE> --instance-id "$INSTANCE_ID" \
  --query StandardOutputContent --output text
```

Then for real:

```bash
# CONFIRM — actually deletes
aws ssm send-command --profile argamanza --region il-central-1 \
  --instance-ids "$INSTANCE_ID" --document-name AWS-RunShellScript \
  --parameters 'commands=["docker exec wiki7 php maintenance/run.php extensions/Wiki7ReviewGate/maintenance/resetContent --confirm"]'
```

### Scope options

- `--scope=all` (default): everything listed above. Use when starting a fresh iteration cycle.
- `--scope=drafts-only`: just wipes NS_DRAFT. Use when you want to re-run the bot but keep your reviewed mainspace content intact.

### Safety

- Refuses to run if **neither** `--dry-run` nor `--confirm` is passed.
- Refuses to run if **both** are passed simultaneously.
- Idempotent: re-running on an already-empty wiki is a no-op.

See `docker/extensions/Wiki7ReviewGate/maintenance/resetContent.php` for the implementation. The hybrid workspace policy that motivated this script lives in `docs/revival-plan.md` §6b.

---

## 8. Multi-season pipeline recipes (Phase 3a R2+)

### Single-season run (the original 3a flow)

```bash
cd data
uv run python run_pipeline.py --season 2024              # write to local docker
uv run python run_pipeline.py --season 2024 --dry-run    # preview without writing
```

### Multi-season run (Phase 3a R2)

```bash
# All-time (1949 → current). Resume default: spiders whose output already exists
# on disk are skipped. Sparse historical seasons (pre-~1974) get a placeholder
# overview page emitted automatically. Recommended for the v1 corpus.
uv run python run_pipeline.py --seasons 1949-2025

# A focused slice — useful for sanity-checking a spider change.
uv run python run_pipeline.py --seasons 2015,2024
```

**Resume from a partial failure:** the pipeline writes per-spider per-season output to disk as it goes. If a run dies (network hiccup, ScraperAPI rate-limit, anything), restart with the **same command** — non-empty existing output skips the matching spider call. Empty `[]` files re-fetch (so a transient TM block doesn't lock a season into permanent emptiness).

```bash
# Force a full re-fetch even where output exists (after a spider fix, etc.):
uv run python run_pipeline.py --seasons 1949-2025 --force-rescrape
```

The wiki import step is independently idempotent — every `page.save()` does a content-hash compare against the live page text and skips no-op edits.

### Iteration-cycle phase recipe (per-season review on local docker)

Phase 3a R2 step 10 surfaced that bulk all-time review (2,680 pages) is overwhelming for a solo reviewer. The iteration-cycle phase walks season-by-season instead. Recommended order: **2024/25 first → walk backwards → review modern aggregates around the 10-season slice → jump to 1985/86 → walk forward → fill historical placeholders by hand**.

Per-cycle recipe:

```bash
# 1. Reset draft content from last iteration (preserves seed homepage + sub-
#    templates + users + extensions; wipes only Wiki7Bot-authored content).
docker exec docker-mediawiki-1 php /var/www/html/maintenance/run.php \
  /var/www/html/extensions/Wiki7ReviewGate/maintenance/resetContent.php \
  --scope=drafts-only --confirm

# 2. (Optional) wipe local pipeline output for this season to force fresh
#    translation. Resume default would otherwise reuse the cached output.
rm -rf data/data_pipeline/output/<season>/ data/data_pipeline/output/merged/

# 3. Run pipeline for the season.
cd data
export WIKI_URL='http://localhost:8080' \
       WIKI_BOT_USER='Wiki7Bot' \
       WIKI_BOT_PASS='localdev-password-2026' \
       WIKI_GATE_ENABLED='1' \
       WIKI7_ANTHROPIC_API_KEY='<your key>'
uv run python run_pipeline.py --season 2024

# 4. Approve Cargo templates (one-time per fresh stack; see §9 below).
# 5. Review drafts at http://localhost:8080/Special:AllPages?namespace=3000
#    edit/promote (Special:MovePage) per draft, capture issues.
# 6. Fix issues in code, repeat.
```

## 9. Cargo tables — post-import approval + population (fully automatable)

Cargo's table-creation lifecycle has TWO subtle gotchas that bit iter-cycle 1. Read all of §9 before running anything new — there's a fix here for both.

### Namespace gate on `#cargo_store` (iter-cycle 1, 2026-06-11)

Every `#cargo_store` invocation across all 9 templates is wrapped in:

```wiki
{{#ifeq:{{NAMESPACENUMBER}}|0|{{Cargo/X | ... }}|}}
```

So pages in NS_DRAFT (3000) skip the Cargo store entirely; only mainspace pages contribute data. This prevents un-reviewed draft data from leaking via public `#cargo_query` renders (leaderboards etc.) and via `Special:CargoQuery` / `Special:CargoTables` / `Special:CargoDrilldown`. Drafts remain VISUALLY complete (the infobox renders, the match-report renders) — only the SQL-store side is gated.

The magic word `{{NAMESPACENUMBER}}` resolves at parse time in the context of the *transcluding* page, so the gate works correctly regardless of which template wraps the call.

**Promotion flow:** when a reviewer moves a draft to mainspace, MediaWiki's `PageMoveComplete` hook fires Cargo's `onPageMoveComplete` (updates `_pageName` + `_pageNamespace` on any existing rows — none in this case since drafts skip storing) AND enqueues `refreshLinks` for the moved page. `refreshLinks` re-parses the page, the conditional now sees `NAMESPACENUMBER=0`, and `#cargo_store` fires for the first time. The row appears in the Cargo table. **No manual re-import needed.**

**Reviewer workflow caveat:** aggregation pages in NS_DRAFT (e.g. `Draft:מלכי השערים של כל הזמנים`) render with zero rows during draft review because no source pages have stored data yet. To preview aggregation rendering, promote a small test slice (3-5 players) to mainspace first; the leaderboard then shows partial data and confirms the rendering logic. Full data appears as more source pages are promoted.

### Defense 2 — anon revocation of `runcargoqueries`

Cargo's `extension.json` grants `runcargoqueries` to `*` (anon) by default. Combined with the namespace gate above, `LocalSettings.php` revokes this from anon and re-grants it to `reviewer` + `sysop`:

```php
$wgGroupPermissions['*']['runcargoqueries']         = false;
$wgGroupPermissions['reviewer']['runcargoqueries']  = true;
$wgGroupPermissions['sysop']['runcargoqueries']     = true;
```

This is defense-in-depth: even if a future template edit accidentally drops the namespace gate, anon can't reach `Special:CargoQuery` / `Special:CargoTables` to exploit the leak.

**Note:** the revocation does NOT affect `#cargo_query` parser functions embedded in mainspace pages (leaderboards etc.) — those render server-side and the permission check applies only to the special-page UI. Public-facing leaderboards continue to work for anon viewers; only the ad-hoc query UI is locked down.

### How `#cargo_declare` actually behaves

Per the Cargo docs (mediawiki.org/wiki/Extension:Cargo/Storing_data): **`#cargo_declare` does NOT create the SQL table when parsed.** It only registers the schema in MediaWiki's `page_props` table (as `CargoTableName` + `CargoFields`). The actual SQL table creation is a separate, **explicit** action triggered either by:

- Clicking the **"Create data" / "Recreate data"** tab on the template page (UI), or
- Running `cargoRecreateData.php --table <NAME>` (CLI)

**Caveat: `cargoRecreateData.php` WITHOUT `--table` is a no-op on a fresh install** — it iterates over already-registered tables in `cargo_tables`, which is empty until each table is created at least once. Don't be misled by its silent exit-0.

Additionally, **Approved Revs gates the `#cargo_declare` parse:** the template lands as latest-unapproved on the bot's write; the parse that populates `page_props` happens after a reviewer approves the template's current revision.

### Cargo reserved words

`CargoDeclare.php` rejects table names AND field names that match its small reserved list: **`holds`, `matches`, `near`, `within`**. The rejection is silent — `#cargo_declare` just doesn't register the schema in `page_props`, and a small error message appears on the template page. This is what made the table named `matches` and the field named `matches` both silently fail in iter-cycle 1 (now renamed to `match_reports` and `played` respectively). Avoid these four names entirely for both new tables and new fields.

### The recipe (fully automated, no clicking)

After every bot import, run two scripts back-to-back:

```bash
# Step 1 — approve all unapproved revisions. Fires the #cargo_declare parse
# on every Cargo template, which populates page_props. Also approves the 4
# MediaWiki templates + 11 main-page sub-templates + the homepage.
#
# IMPORTANT: pass --username=Admin (or any valid registered user). Without it
# the script runs in CLI context with no user — approvals get attributed to
# the anonymous IP-based actor with NULL user_id, and Special:ApprovedRevs?show=all
# subsequently throws a TypeError on MW 1.45 (`Linker::userLink(null, ...)`).
# Discovered iter-cycle 1; fix is to always pass --username=Admin.
docker exec docker-mediawiki-1 php /var/www/html/maintenance/run.php \
  /var/www/html/extensions/ApprovedRevs/maintenance/approveAllPages.php \
  --username=Admin

# Step 2 — create the SQL table + populate it for each Cargo table. The
# script reads CargoTableName from page_props (populated by step 1) to find
# the declaring template, creates the SQL table, then queues background
# jobs to crawl every page transcluding the template + run #cargo_store.
for table in players match_reports transfers market_values player_stats \
             coaches honours season_standings head_to_head; do
  docker exec docker-mediawiki-1 php /var/www/html/maintenance/run.php \
    /var/www/html/extensions/Cargo/maintenance/cargoRecreateData.php \
    --table=$table --quiet
done

# Step 3 — drain the background-job queue so the data actually lands in
# the Cargo tables before the reviewer starts inspecting. cargoRecreateData
# enqueues `cargoPopulateTable` jobs; runJobs drains them synchronously.
docker exec docker-mediawiki-1 php /var/www/html/maintenance/run.php runJobs.php
```

### Verify

`http://localhost:8080/Special:CargoTables` should now show each table. After draft promotion (or initial v1 prod approve), spot-check one query: `Special:CargoQuery` → table `match_reports` → fields `_pageName, opponent, result, season` → run.

If a table is missing, the most common cause is the same Approved Revs / reserved-word combo: check `page_props WHERE pp_propname='CargoTableName'` to see which schemas registered.

### Local vs prod — when does each step actually run?

The full recipe above runs every iter cycle on local docker. On prod the parts have different semantics — **don't cargo-cult the recipe into routine prod ops**:

| Step | Local (every iter cycle) | Prod (initial v1 launch) | Prod (ongoing ops) |
|------|---|---|---|
| `approveAllPages.php` | Bulk-approve so review can proceed | **Once**, for the canonical bot-produced templates that converged through iter cycles | **NO** — defeats the review gate. Reviewer approves each bot revision individually via the wiki UI (or Telegram inline-approve once Phase 3b ships) |
| `cargoRecreateData.php --table=X` | Run after first import to materialise SQL tables | **Once** — required for SQL tables to exist | **NO** unless a Cargo schema changes (then re-run for the affected table). Ongoing `#cargo_store` calls populate incrementally |
| `runJobs.php` | Drain inline for immediate verification | **NO** — prod has `$wgJobRunRate = 0` and the EC2 host cron drains jobs every minute (Phase 2.5 work) | **NO** — host cron handles it |

The point: on prod, the bulk-approval recipe is a **one-time launch event**, not a regular operation. Once v1 is live, the review gate works as designed: new pages enter NS_DRAFT, reviewer MovePages individually; updates to existing mainspace pages create unapproved revisions, reviewer approves individually. Cargo `#cargo_store` fires on each promoted/approved page automatically. No manual scripts needed.

---

## Related docs

- **Secret rotation choreography** (rolling any env-file-threaded secret without breaking the running container): memory `[[wiki7-secret-rotation]]`.
- **Phase 3.5 review-gate architecture**: `docs/adr/0002-review-gate-architecture.md`.
- **TM data-surface inventory + glossary**: `docs/research/0002-transfermarkt-data-surface.md`.
- **Translation overhaul plan** (Wikidata-based, iteration-cycle plan): `docs/research/0003-translation-overhaul-plan.md`.
- **Open follow-ups** (including the `docker/scripts/recycle-wiki7.sh` helper that would automate step 1's container recycle): `docs/phase-3b-backlog.md`.
- **Live infrastructure snapshot**: memory `[[wiki7-aws-state]]`.
- **ScraperAPI baseline**: memory `[[wiki7-scraperapi-baseline]]`.
- **Translation strategy + status**: memory `[[wiki7-translation-strategy]]`.
- **High-level priorities + sequencing**: memory `[[wiki7-revival-priorities]]`.
