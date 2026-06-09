# Phase 3b Backlog

Collection point for content / template / structure / data-quality gaps observed during the Phase 3a prod push (and beyond). Phase 3a's scope is single-season pipeline-to-prod end-to-end; everything richer than "renders without errors and the data is there" lands here.

When 3a closes, this file becomes the prioritised work list for Phase 3b.

## How to file an entry

One bullet per gap. Don't pre-judge severity — that comes during 3b planning.

```
- **<terse title>** *(category)* — what's wrong + where it was seen. Optional: `// hypothesis: <suspected cause>` or `// fix: <one-liner>`.
```

Categories (pick the closest; cross-cutting items go under `Cross`):

| Category | Covers |
|---|---|
| `Templates` | MediaWiki `Template:` pages (infoboxes, navboxes) and the Jinja `.j2` templates in `data/wiki_import/templates/` that render to wikitext. |
| `Cargo` | `#cargo_declare` schema design, `#cargo_store` calls inside templates, missing queries on aggregate pages. |
| `Structure` | Page sections missing / in wrong order / too thin; homepage TODO placeholders; navigation / cross-links between page types. |
| `Data` | Pipeline data gaps (missing fields, wrong values, untranslated strings — *not* the auto-translate review of `mappings.he.yaml` itself, which is its own recurring task). |
| `SEO` | Per-page `{{#seo:}}` overrides, alt tags, URL slug strategy, internal-linking quality once Cargo queries are populated. |
| `Cross` | Items that span buckets (e.g. "player infoboxes need both a Cargo declare AND a `{{#seo:}}` block AND an alt-tag wiring"). |

Spot-check protocol: when you see something, just paste the URL or page title + a one-line description. I'll formalise it into the table on commit.

---

## Templates

<!-- file items here -->

## Cargo

- **No `#cargo_declare` anywhere yet** *(Cargo)* — pipeline writes templates with the shape of Cargo-storable fields, but no schema is declared. Result: aggregate pages (records, leaderboards) render the data via Jinja-built tables, not Cargo queries. Phase 3b decides which page types declare schemas (player, match, season) + ships the `cargoRecreateData` backfill. // hypothesis: confirmed during Phase 2.5c review.

## Structure

- **Homepage `TODO` placeholders** *(Structure)* — `docker/wiki-pages/תבנית_עמוד_ראשי_*` carries placeholders for current manager / current captain / attributed quote / real featured image. Phase 3b fills them once content stabilises.

## Data

- **Match `home_lineup` / `away_lineup` are empty in the existing 2024 scrape** *(Data)* — the Phase 3a spider fix corrected the CSS selector, but the existing `tmk-scraper/output/2024/matches.json` was scraped pre-fix and still has `home_lineup: null` / `away_lineup: {}`. Re-running the match spider for 2024 would cost ~45 ScraperAPI requests and refresh lineups; Phase 3a deferred this in favour of credit conservation. // fix: `python run_pipeline.py --season 2024 --spiders match --skip-normalize --skip-merge --skip-hebrew --skip-import`, then re-import.

- **Transfers row drops `age` + `position`** *(Data)* — current TM `/alletransfers/` ("all transfers") page no longer surfaces these inline; they're only on the player profile. Could be backfilled by joining against the player spider's output during the normalize step. Phase 3b decision.

- **Coach spider only returns *current* staff (6 entries)** *(Data)* — `/trainer/verein/` ("coach page") and `/trainerhistorie/verein/` ("coach history") are both 404 on TM as of 2026-06-07. `/mitarbeiter/verein/` ("staff") is the only working URL and lists only current staff. Phase 3a R2 fills the historical gap via the `platzierungen` ("standings") page, which carries the season's manager as a column on each row — see [`docs/research/0002-transfermarkt-data-surface.md`](research/0002-transfermarkt-data-surface.md) §3.4.

## SEO

- **No per-page `{{#seo:}}` overrides yet** *(SEO)* — site-wide WikiSEO defaults work (verified Phase 2). Phase 3d adds per-page overrides on key pages + embeds `{{#seo:}}` calls inside Cargo templates so player/team/match pages get correct `og:title` / `og:image` / `type=Person|SportsTeam|SportsEvent` automatically. Awaits Cargo declarations (above).

## Cross

- **Multi-purpose Telegram bot beyond review notifications** *(Cross — Phase 4)* — Phase 3.5 ships the review-gate side of the Telegram dispatcher (one-line notifications about pending bot reviews). The operator's broader plan is a community Telegram channel/group where the same bot handles wiki discussions, content announcements, matchday threads, etc. Backlog item covers expanding the in-repo `Wiki7ReviewGate` extension (or extracting a sibling `Wiki7TelegramBridge`) into a fuller Telegram integration: inbound webhook handling, command parsing, threaded discussions per page, push-on-page-update for subscribed pages. // see: docs/adr/0002-review-gate-architecture.md §"Notification — Extension:Echo + custom type".

- ~~**WikiSEO + Approved Revs interaction — verify or fix**~~ *(Cross — Phase 3.5 implementation risk)* — **🟢 RESOLVED 2026-06-08 via empirical local test:** anon view of a page where approved != latest shows the approved revision's `og:title` / `og:description` / `og:image`. WikiSEO emits from the page-render path which respects Approved Revs's "show approved to public" behavior. No mitigation needed.

- **Image-review support for the Draft namespace** *(Cross — Phase 4)* — Extension:Lockdown's known leakage path #2: images bypass namespace read restrictions because file URLs go through `/images/` directly, not through MediaWiki page rendering. If/when the bot starts uploading player headshots, we need Extension:Image Authorisation or similar to gate image reads, otherwise draft headshots leak to anonymous readers via direct URL. Phase 3.5 explicitly does NOT upload images to drafts; this entry covers when that changes.

- **`docker/scripts/recycle-wiki7.sh` helper for env-file-secret rotation** *(Cross — Phase 4)* — Phase 3.5 Telegram wiring 2026-06-08 surfaced the operational gap: rotating any secret threaded through `/tmp/wiki7.env` requires recreating the container (not `docker restart` — that preserves the original env). The recipe is non-obvious because (a) the instance role has `GetSecretValue` on specific ARNs but not `ListSecrets`, so the script must hardcode all 5 ARNs; (b) `MEDIAWIKI_DB_HOST` is NOT in the DatabaseSecret — it's CDK-templated from `dbInstance.dbInstanceEndpointAddress` at synth time, so any naive secret-only rebuild produces `DB_HOST=null` and 5xx. Fix: commit a `docker/scripts/recycle-wiki7.sh` script that fetches all 5 secrets, reads RDS endpoint via `describe-db-instances`, reads bucket via `list-buckets`, writes the env-file, `docker stop && rm && run`'s the container. SSM-exec'd by hand. ~50 LOC. // see: [[wiki7-secret-rotation]] memory + docs/adr/0002 §Implementation plan step 9.

- ~~**Telegram notification rate-limit / batching during bulk imports**~~ *(Cross — Phase 3a-content gate)* — **RESOLVED via the `$wgWiki7ReviewGateQuiet` LocalSettings flag (Phase 3.5b).** Operator sets it true → deploy → bulk-import → set false → deploy. Costs ~2 deploy windows (~15-20 min) for one-time bulk runs. See also "single-deploy toggle" item below for the polished version.

- **Single-deploy toggle for `$wgWiki7ReviewGateQuiet`** *(Cross — Phase 4 polish)* — current pattern requires a CDK deploy to flip the flag. After [[wiki7-secret-rotation]]'s `docker/scripts/recycle-wiki7.sh` lands, extend the script with an optional `--quiet=on|off` mode that writes a `WIKI7_REVIEW_GATE_QUIET=1` env-var override into the env-file + recycles the container. The extension's `Hooks::isQuiet()` should fall back to env-var if the LocalSettings config is unset. ~30s toggles instead of ~7-min deploys.

- **Telegram presentation tweaks** *(Cross — small UX polish, post 3a-content)* — current message format is `📝 Wiki7Bot wrote draft: <title>\n→ <review_url>` for draft + `🔄 Wiki7Bot proposed update: <title>\n→ <review_url>` for update. Improvements to consider: include the page edit summary, link to a diff view for updates (rather than the latest-rev page view), translate the message body to Hebrew (titles are already Hebrew so partial RTL works; full Hebrew would be cleaner).

- **Telegram digest mode for rapid-fire bursts** *(Cross — Phase 4 UX polish)* — distinct from the `Quiet` flag (which fully suppresses): a digest-mode that COALESCES 5+ rapid saves into a single message at end-of-burst ("Wiki7Bot wrote 17 drafts in the last minute. Review queue: …"). Requires either a debounce timer in PHP (state) or a deferred-flush mechanism via a maintenance script + cron. Substantial; only worth it if incremental flow ever bursts in real production use.

- **Telegram bot inline approve / reject buttons** *(Cross — Phase 4 UX, substantial)* — currently each notification is one-way (bot → operator). Adding `inline_keyboard` callback buttons under each message would let the operator tap "✅ Approve" / "❌ Reject" / "↗️ Open in browser" directly in Telegram. Requires: (a) Telegram setWebhook config; (b) an HTTPS endpoint on prod that Telegram POSTs callbacks to; (c) handler that authenticates the callback signer + talks to api.php to do approve / move-to-mainspace / revert. ~half-day of work + a new CDK route. Big UX win once we're spending real time in the review queue.

- **Telegram bot slash commands** *(Cross — Phase 4, depends on webhook above)* — once the inbound webhook exists: register `/pending` (replies with current draft count + `Special:UnapprovedPages` link), `/promote <title>` (move a draft to mainspace), `/diff <title>` (DM the bot-vs-approved diff for an update). Per-command BotFather registration via `/setcommands`.

- **BotFather one-time polish (description, about, picture)** *(Cross — operator action, no code)* — open BotFather in Telegram, run `/setdescription`, `/setabouttext`, `/setuserpic` for `@wikisheva_bot`. Adds a profile blurb and picture (recommend a 512×512 PNG cropped from `docker/assets/social-share.png`). Pure profile polish; doesn't affect dispatch.

- **Multi-reviewer Telegram scaling** *(Cross — Phase 4, when reviewer group grows)* — current `$wgWiki7TelegramChatId` is a single integer (operator's DM). For a ~3-10 reviewer group, three options: (i) make `$wgWiki7TelegramChatId` accept a comma-separated list of chat_ids, dispatcher loops; (ii) switch the dispatch target to a private Telegram group and add all reviewers to it (no code change, just a chat_id swap to the group's negative integer); (iii) hybrid — DM for the operator + group for the rest. Option (ii) is simplest.

- **`Wiki7TelegramBridge` extraction (Phase 4)** *(Cross — only when the bot does more than review notifications)* — currently the Telegram dispatch logic lives inside `Wiki7ReviewGate`. If the bot grows to handle community discussions, content announcements, matchday threads, etc., extract a sibling `Wiki7TelegramBridge` extension that handles all Telegram I/O (outbound dispatch + inbound webhook). `Wiki7ReviewGate` would call into it for the review-pending messages. Until that happens, keeping it inline is simpler.

- **IFA match-report scraper for full referee teams** *(Cross — Phase 4)* — TM exposes only the main referee inline in its match-report metadata; assistant referees + 4th official + VAR + VAR assistant are NOT in TM's layout (audited 2026-06-09 against 2024/25 + 2015/16 fixtures). MaccabiPedia + WikiPoel show the full team because they source from elsewhere — IFA (Israel Football Association) publishes per-match PDFs with the full referee team for Israeli Premier League games. PR B keeps the 6-field Cargo schema (`referee`, `assistant_referee_1`, `assistant_referee_2`, `fourth_official`, `var_referee`, `var_assistant`) but only populates `referee` from TM; the others are nullable for reviewer hand-curation. Phase 4 work would be a separate spider against IFA's match center, joining by date + opponent. Effort: probably a half-day if IFA publishes structured HTML / a few days if PDFs need parsing.

- **Switch translation backend from Anthropic API direct to AWS Bedrock** *(Cross — Phase 4, only when translation gets automated)* — PR B uses Anthropic API direct (`ANTHROPIC_API_KEY` env var) for `auto_translate_hebrew.py` because the pipeline runs on a developer machine / CI where IAM-native auth + AWS-bill consolidation give no real win. If/when the translation step gets automated as a Lambda (Phase 4 sitemap-style scheduled run), Bedrock starts to win: the Lambda's execution role grants Bedrock access natively, the cost folds into the existing AWS bill, and there's no separate API key to rotate. The switch is ~10 lines in `auto_translate_hebrew.py` (swap the `anthropic.Anthropic()` client for `boto3.client('bedrock-runtime')` with the matching model ID). Trade-off documented at `docs/research/0002-transfermarkt-data-surface.md` §5.3 + decision 4 in §7.
