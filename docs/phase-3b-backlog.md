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

- **Transfers row drops `age` + `position`** *(Data)* — current TM `/alletransfers/` page no longer surfaces these inline; they're only on the player profile. Could be backfilled by joining against the player spider's output during the normalize step. Phase 3b decision.

- **Coach spider only returns *current* staff (6 entries)** *(Data)* — `/trainer/verein/` and `/trainerhistorie/verein/` are both 404 on TM as of 2026-06-07. `/mitarbeiter/verein/` is the only working URL and lists only current staff. Full historical coach list with per-tenure match records needs either an archive source (web.archive.org snapshot of the old `/trainer/` page) or hand-curated data.

## SEO

- **No per-page `{{#seo:}}` overrides yet** *(SEO)* — site-wide WikiSEO defaults work (verified Phase 2). Phase 3d adds per-page overrides on key pages + embeds `{{#seo:}}` calls inside Cargo templates so player/team/match pages get correct `og:title` / `og:image` / `type=Person|SportsTeam|SportsEvent` automatically. Awaits Cargo declarations (above).

## Cross

- **Multi-purpose Telegram bot beyond review notifications** *(Cross — Phase 4)* — Phase 3.5 ships the review-gate side of the Telegram dispatcher (one-line notifications about pending bot reviews). The operator's broader plan is a community Telegram channel/group where the same bot handles wiki discussions, content announcements, matchday threads, etc. Backlog item covers expanding the in-repo `Wiki7ReviewGate` extension (or extracting a sibling `Wiki7TelegramBridge`) into a fuller Telegram integration: inbound webhook handling, command parsing, threaded discussions per page, push-on-page-update for subscribed pages. // see: docs/adr/0002-review-gate-architecture.md §"Notification — Extension:Echo + custom type".

- **WikiSEO + Approved Revs interaction — verify or fix** *(Cross — Phase 3.5 implementation risk)* — Approved Revs docs say *"most extensions that retrieve the contents of pages will still get the last revision"* with a green-light carve-out for Cargo + Semantic MediaWiki. WikiSEO + Description2 are NOT in the carve-out. If they emit metadata from latest-rev rather than approved-rev, social-share previews leak bot-proposed content for pages whose body is held back. Empirical test on local docker is in the Phase 3.5 implementation plan; this entry is the fallback design item if the test reveals breakage. Fix options: (a) WikiSEOPreAddMetadata hook swap to approved-rev wikitext, (b) accept the leak for personal-wiki traffic levels, (c) alternative metadata source. // see: docs/adr/0002-review-gate-architecture.md §"Open question #1".

- **Image-review support for the Draft namespace** *(Cross — Phase 4)* — Extension:Lockdown's known leakage path #2: images bypass namespace read restrictions because file URLs go through `/images/` directly, not through MediaWiki page rendering. If/when the bot starts uploading player headshots, we need Extension:Image Authorisation or similar to gate image reads, otherwise draft headshots leak to anonymous readers via direct URL. Phase 3.5 explicitly does NOT upload images to drafts; this entry covers when that changes.

- **`docker/scripts/recycle-wiki7.sh` helper for env-file-secret rotation** *(Cross — Phase 4)* — Phase 3.5 Telegram wiring 2026-06-08 surfaced the operational gap: rotating any secret threaded through `/tmp/wiki7.env` requires recreating the container (not `docker restart` — that preserves the original env). The recipe is non-obvious because (a) the instance role has `GetSecretValue` on specific ARNs but not `ListSecrets`, so the script must hardcode all 5 ARNs; (b) `MEDIAWIKI_DB_HOST` is NOT in the DatabaseSecret — it's CDK-templated from `dbInstance.dbInstanceEndpointAddress` at synth time, so any naive secret-only rebuild produces `DB_HOST=null` and 5xx. Fix: commit a `docker/scripts/recycle-wiki7.sh` script that fetches all 5 secrets, reads RDS endpoint via `describe-db-instances`, reads bucket via `list-buckets`, writes the env-file, `docker stop && rm && run`'s the container. SSM-exec'd by hand. ~50 LOC. // see: [[wiki7-secret-rotation]] memory + docs/adr/0002 §Implementation plan step 9.

- **Telegram notification rate-limit / batching during bulk imports** *(Cross — Phase 3a-content gate)* — Phase 3a-content's first prod pipeline run will create ~113 drafts in one go. The Wiki7ReviewGate `PageSaveComplete` handler fires one Echo notification + one Telegram message per save → ~113 messages. Decide before the bulk run: (a) ship a `$wgWiki7ReviewGateQuiet = true` LocalSettings flag the operator sets temporarily during bulk imports (Echo + Telegram both suppressed); (b) batch into a single "Wiki7Bot wrote N drafts" digest at end-of-run; (c) clear `$wgWiki7TelegramChatId` for the bulk pass and restore after; (d) accept the spam as a one-time annoyance (future incremental runs will be 1-5 pages at a time). My recommendation is (c) for the bulk run + add (a) as a small follow-up so future bulks are clean.

- **Telegram presentation tweaks** *(Cross — small UX polish, post 3a-content)* — current message format is `📝 Wiki7Bot wrote draft: <title>\n→ <review_url>` for draft + `🔄 Wiki7Bot proposed update: <title>\n→ <review_url>` for update. Possible improvements: include the page-summary edit comment, link directly to a diff view for updates, add inline approve/reject Telegram buttons (would require an inbound webhook handler + Telegram bot inline-keyboard glue — substantial work, deferred).
