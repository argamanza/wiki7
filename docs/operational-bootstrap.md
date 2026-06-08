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

If running the data pipeline locally, the bot needs a ScraperAPI key in env. The key is operator-supplied (free tier from <https://www.scraperapi.com/>) and lives in `~/.zshrc`:

```bash
echo 'export SCRAPERAPI_KEY=<your key>' >> ~/.zshrc
```

Used by `data/run_pipeline.py` to route requests through ScraperAPI (Transfermarkt blocks direct scraping). Not needed for read-only wiki operations.

---

## Related docs

- **Secret rotation choreography** (rolling any env-file-threaded secret without breaking the running container): memory `[[wiki7-secret-rotation]]`.
- **Phase 3.5 review-gate architecture**: `docs/adr/0002-review-gate-architecture.md`.
- **Open follow-ups** (including the `docker/scripts/recycle-wiki7.sh` helper that would automate step 1's container recycle): `docs/phase-3b-backlog.md`.
- **Live infrastructure snapshot**: memory `[[wiki7-aws-state]]`.
- **High-level priorities + sequencing**: memory `[[wiki7-revival-priorities]]`.
