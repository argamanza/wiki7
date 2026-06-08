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
