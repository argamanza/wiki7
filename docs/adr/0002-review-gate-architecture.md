# ADR 0002 — Review-gate architecture for bot-written content

- **Status:** Accepted (all 🔴 open questions resolved via empirical local testing 2026-06-08)
- **Date:** 2026-06-08
- **Phase:** 3.5 (review-gate scaffolding, between Phase 3a pipeline scaffolding and Phase 3b content re-curation)
- **Research:** [`docs/research/0001-mediawiki-review-gate.md`](../research/0001-mediawiki-review-gate.md) — full cited findings.

## Context

Phase 3a built the pipeline scaffolding: a `Wiki7Bot` MediaWiki user, a Python scraper+importer that writes pages via the API, spider correctness fixes against 2024 Transfermarkt data, and end-to-end verification against a local MediaWiki. The plan was to push 2024's content to live prod in Phase 3a and let Phase 3b polish it in place.

That plan was reframed during 3a once we articulated the long-term vision: bot output must always pass human review before reaching public readers, and that gate must scale from a solo reviewer (now) to a small trusted group (~3–10) within a year. Pushing 2024 content to live prod without the gate would either special-case the first season or create rework when the gate ships. The disciplined response is to **build the gate before any content lands publicly** — Phase 3.5.

## What the workflow needs to do

1. The bot writes pages via the MW API.
2. **NEW pages** (the bot hasn't written this title before) land in a state where only the reviewer group can read or edit them. Public readers see nothing — the wiki keeps working for them with its existing pages.
3. **UPDATES to already-promoted pages** don't silently overwrite. The bot's proposed revision is held back from public view; public readers continue to see the previously-approved revision. The reviewer sees the bot's proposed page vs. the last-approved page as a diff.
4. Reviewer approves wholesale, edits-then-approves, or rejects. Manual edits the reviewer made BETWEEN bot runs are preserved.
5. Reviewer gets notified there's work — in-wiki for MVP, Slack/email later.
6. The architecture works at 1 reviewer and 10 reviewers without rework — just by adding users to the reviewer group.

## Decision

Two extensions + one custom namespace + custom Echo notification type:

| Concern | Solution |
|---|---|
| **NEW pages — hide from public** | Custom `Draft:` namespace + **Extension:Lockdown** for read gating + `$wgNonincludableNamespaces` to block transclusion leakage. Bot writes `Draft:Foo` with `action=edit&createonly=1`. Reviewer approves by `Special:MovePage` → mainspace. |
| **UPDATE pages — hold back from public, show as diff** | **Extension:Approved Revs** with `$egApprovedRevsAutomaticApprovals=false`. Bot writes mainspace with `action=edit&nocreate=1&bot=1`. Public sees the previously-approved revision; reviewer sees the bot's proposed revision via `Special:UnapprovedPages`. |
| **Notification** | **Extension:Echo** with a custom `wiki7-bot-review-pending` notification type for in-wiki. Out-of-wiki: a small PHP listener in the same in-repo extension dispatches to the **Telegram Bot API** (`https://api.telegram.org/bot<token>/sendMessage`) — piggybacks on the wiki's already-planned Telegram channel + bot for community discussions/updates. Extension:SlackNotifications is unmaintained (last release 2020) and not used. **`$wgWiki7ReviewGateQuiet`** site-wide kill-switch (added Phase 3.5b) suppresses both channels at once — intended for one-time bulk imports. |
| **Scale 1 → 10 reviewers** | `reviewer` group on the wiki. All gates are group-scoped, not per-user. Adding a user to the group gives them all the permissions. |

This is **the recommended primary architecture from the research** with one update: the original synthesis used `$wgNamespaceProtection` for the Draft read gate, but probing confirmed that variable only controls write access. Lockdown is required for read gating.

### Detailed design

#### Custom Draft namespace

```php
// LocalSettings.php — Draft namespace
define('NS_DRAFT', 3000);
define('NS_DRAFT_TALK', 3001);
$wgExtraNamespaces[NS_DRAFT]      = 'Draft';
$wgExtraNamespaces[NS_DRAFT_TALK] = 'Draft_talk';

// Exclude from default search + content NS so drafts don't appear in
// Special:Search results, sitemap, allpages defaults.
$wgNamespacesToBeSearchedDefault[NS_DRAFT] = false;
// (Intentionally NOT added to $wgContentNamespaces so RecentChanges/AllPages
// don't surface drafts in default listings.)

// Block transclusion of drafts into public pages (one of Lockdown's known
// leakage paths — addressed by core MW config not by Lockdown itself).
$wgNonincludableNamespaces[] = NS_DRAFT;

// Block the namespace from being indexed by sitemap generators.
// Our existing Wiki7-GenerateSitemap SSM doc needs the --namespace 0 filter;
// confirm in implementation phase.
```

#### Extension:Lockdown — read+edit restricted to the reviewer group

```php
// LocalSettings.php — Lockdown
wfLoadExtension('Lockdown');

$wgNamespacePermissionLockdown[NS_DRAFT]['read']    = ['reviewer'];
$wgNamespacePermissionLockdown[NS_DRAFT]['edit']    = ['reviewer', 'bot'];
$wgNamespacePermissionLockdown[NS_DRAFT]['create']  = ['reviewer', 'bot'];
$wgNamespacePermissionLockdown[NS_DRAFT]['move']    = ['reviewer'];  // promote = move
$wgNamespacePermissionLockdown[NS_DRAFT_TALK]['read'] = ['reviewer'];
$wgNamespacePermissionLockdown[NS_DRAFT_TALK]['edit'] = ['reviewer', 'bot'];
```

#### Custom `reviewer` group

```php
// LocalSettings.php — reviewer group
$wgGroupPermissions['reviewer']['read']             = true;
$wgGroupPermissions['reviewer']['edit']             = true;
$wgGroupPermissions['reviewer']['move']             = true;
$wgGroupPermissions['reviewer']['approverevisions'] = true;   // Approved Revs
$wgGroupPermissions['reviewer']['unapprovedpages']  = true;   // see Special:UnapprovedPages

// Make 'reviewer' assignable by sysop (and self-removable).
$wgAddGroups['sysop'][] = 'reviewer';
$wgRemoveGroups['sysop'][] = 'reviewer';
$wgRemoveGroups['reviewer'][] = 'reviewer';
```

#### Extension:Approved Revs — UPDATE gating in mainspace

```php
// LocalSettings.php — Approved Revs
wfLoadExtension('ApprovedRevs');

// CRITICAL: disable auto-approval on edit. Without this, a reviewer touching
// a page with an existing approved revision would silently re-approve it
// instead of approving the bot's proposed revision deliberately.
$egApprovedRevsAutomaticApprovals     = false;
$egApprovedRevsFileAutomaticApprovals = false;

// Apply only to the namespaces where bot updates land — NOT to the Draft
// namespace (which uses the move-to-promote model instead).
$egApprovedRevsEnabledNamespaces = [
    NS_MAIN     => true,
    NS_TEMPLATE => true,    // bot rebuilds template pages too
    NS_FILE     => true,    // for future bot-uploaded images
];
$egApprovedRevsEnabledNamespaces[NS_DRAFT] = false;

// Show the approval banner above unapproved revisions so reviewers always
// know what state they're looking at.
$egApprovedRevsShowApproveLatest = true;

// IMPORTANT: leave $egApprovedRevsBlankIfUnapproved at the default (false).
// We're NOT blanking unapproved pages globally — that would blank every
// existing page until back-approved one-by-one. Approved Revs's "hold
// LATEST back; serve APPROVED to public" mode covers our UPDATE case
// without the blanking flag, as long as a page has at least one approval.
```

#### Bot pipeline — NEW vs UPDATE routing

The bot makes one extra API call before every page write to discover the page state, then routes:

| Bot intent | Wiki state | Bot action | Result |
|---|---|---|---|
| Write player Foo | `Foo` doesn't exist; `Draft:Foo` doesn't exist | POST `action=edit&title=Draft:Foo&createonly=1` | Page created in Draft namespace, invisible to public. |
| Write player Foo (re-run) | `Foo` doesn't exist; `Draft:Foo` exists | POST `action=edit&title=Draft:Foo&nocreate=1` | Updates the in-review draft (reviewer hasn't promoted yet). |
| Write player Foo | `Foo` already exists in mainspace | POST `action=edit&title=Foo&nocreate=1&bot=1` | New revision in mainspace; public still sees the previously-approved one until the reviewer approves. |

The `createonly`/`nocreate` flags make the routing atomic and race-free — verified Edit API behaviour (research Finding 8).

Pipeline-side LOC estimate: ~50 lines across `data/run_pipeline.py` + `data/wiki_import/import_*.py`. The change adds an early `prober.page_state(title)` call that returns one of `{absent, draft, published}` and gates the title prefix + create flags from there.

#### Reviewer UX

- **Drafts:** `Special:AllPages?namespace=3000` shows pending drafts. Click a draft, review, edit if needed, then `Special:MovePage` to mainspace. The move preserves full history; the first approved revision in mainspace is the bot's content (potentially edited).
- **Pending updates:** `Special:UnapprovedPages` lists every mainspace page with an unapproved revision newer than the approved one. Click → see the page; the banner says "this is the latest revision (unapproved). Click 'view approved revision' or 'approve this revision'." Diff button shows the bot's proposed vs. the last-approved (this is the wiki-native diff view — high quality).
- **Reject:** revert to the prior revision and click approve. No "reject" verb in Approved Revs — rejection is "approve the prior revision."
- **Notification:** Echo notification icon shows pending work; clicking opens a feed of "Wiki7Bot wrote draft Draft:Foo" / "Wiki7Bot proposed an update to Bar" entries.

#### Notification — Extension:Echo + custom type

```php
// LocalSettings.php — Echo
wfLoadExtension('Echo');

// Make our custom type opt-out for the reviewer group (still per-user-overridable).
$wgDefaultUserOptions['echo-subscriptions-web-wiki7-bot-review-pending']    = true;
$wgDefaultUserOptions['echo-subscriptions-email-wiki7-bot-review-pending']  = true;
```

```php
// In a small in-repo extension (docker/extensions/Wiki7ReviewGate/extension.json):
"Hooks": {
  "BeforeCreateEchoEvent": "Wiki7\\ReviewGate\\Hooks::onBeforeCreateEchoEvent",
  "PageSaveComplete":      "Wiki7\\ReviewGate\\Hooks::onPageSaveComplete"
}
```

The custom extension is ~80 LOC of PHP — declares the notification type, fires the event on every `PageSaveComplete` where the saver is `Wiki7Bot` AND the page is either in `Draft:` namespace OR has an unapproved revision. Subscribers in the reviewer group see it in their Echo feed.

**Telegram dispatch** (~30 more LOC in the same extension): on the same `PageSaveComplete` event, POST to `https://api.telegram.org/bot${TOKEN}/sendMessage` with a `chat_id` (configurable via `$wgWiki7TelegramChatId` in LocalSettings) and a one-line message ("Wiki7Bot wrote draft Draft:Foo — review at <url>"). Bot token comes from a new retained Secret (`Wiki7TelegramBotSecret`) threaded via the existing Phase 2.5d env-file pattern: container env var `WIKI7_TELEGRAM_BOT_TOKEN` → `getenv()` in PHP. The Telegram bot itself is the same one the user plans to spin up for community discussions; this gate just piggybacks on it.

**Slack/email** is *not* planned — Telegram covers the out-of-wiki notification surface.

## Forces evaluated

The research evaluated each candidate against 10 criteria. Summary of why we land where we land:

### Why not FlaggedRevs

FlaggedRevs is the only extension whose default behaviour natively matches the UPDATE-to-existing-page hold-back model. It would be the technically-cleanest fit. But:

- **MediaWiki.org banner**: *"complex, poorly documented, very clunky, and not recommended for production use, despite the stable tag."*
- **Last new Wikimedia deployment: 2014.** No new wikis are taking it on. T185664 (WMF stewardship review): *"No one in particular"* maintains it. T381044 (Nov 2024): WMF *"will no longer deploy the extension on more wikis"*; *"no replacement or alternative mapped or planned."*
- A 2021–2024 cleanup (T277883) halved the codebase and dropped multi-tier review. So it's leaner than its reputation — but the maintenance commitment is still absent.

Trade-off accepted: we forgo the native pending-revisions UI. We get it back via Approved Revs's `Special:UnapprovedPages` + the wiki's built-in diff view, which is uglier but operational.

This is the runner-up architecture. If Approved Revs hits a blocker we can't engineer around, fall back to FlaggedRevs on mainspace + Draft namespace for NEW pages. Document the blocker and the migration in an addendum.

### Why not Extension:Moderation

Two showstoppers:
- The `bot` user group is granted `skip-moderation` BY DEFAULT — bot edits BYPASS the queue. We could override with `$wgGroupPermissions['bot']['skip-moderation'] = false`, but we'd be swimming upstream against the extension's design.
- MW.org has an explicit section titled *"Non-recommended use: Moderation as pre-publish review extension"* warning that strict quality-control use *"creates problems: Other users can't improve the article until it is Approved."*

It's an anti-spam tool for new untrusted users, not a pre-publish review tool. Different problem.

### Why a separate Draft namespace for NEW pages

We could in theory use Approved Revs everywhere and set `$egApprovedRevsBlankIfUnapproved = true` to hide unapproved pages from the public. But that variable is wiki-wide. Turning it on would blank every existing mainspace page (including the homepage built in Phase 2) until they get back-approved one-by-one. That's the wrong primitive for "new bot-created pages should be invisible."

A separate Draft namespace is surgical: only bot-NEW pages are hidden, mainspace stays exactly as it is today.

### Why Telegram instead of Slack/email

Slack/email aren't planned for this wiki — the operator intends to run a Telegram channel + bot for community discussions, so the review-gate's out-of-wiki notifications piggyback on that infra. The bridge is a `curl`-equivalent POST to `https://api.telegram.org/bot<token>/sendMessage` — no extension needed, no third-party service to authenticate against, and the same Telegram bot doubles as the discussion surface. Extension:SlackNotifications was eliminated separately on its own merits (last release 2020, MW 1.45 untested).

## Consequences

### What gets simpler

- The data pipeline becomes safe to re-run frequently — no risk of clobbering reviewer edits.
- Adding reviewers in the future is one shell command (`maintenance/run.php createAndPromote` or wiki UI `Special:UserRights`).
- The same architecture works for Phase 3a's 2024 batch *and* full-history Phase 3b runs *and* future automated scheduled runs in Phase 4. No re-architecture.
- The 2024 content push (DoD item from Phase 3a) finally has a sane home: drafts.

### What gets harder

- Bot pipeline now needs a `prober.page_state(title)` call before every write (one extra API request per page).
- Reviewer workflow has UI friction: drafts use `Special:MovePage` to promote; updates use `Special:UnapprovedPages`. Two UX paths instead of one. Acceptable for MVP.
- Lockdown's read-restriction primitive is best-effort, not bulletproof — known leakage paths (transclusion, images, special pages) need explicit testing.

### What's unchanged

- Mainspace's public behaviour today (Phase 2 homepage, all existing pages) is untouched. Approved Revs with `$egApprovedRevsBlankIfUnapproved=false` does NOT change the rendering of any page that doesn't have an approved-rev/latest-rev divergence.
- The bot account, CDK pattern, infrastructure are all unchanged.

## Open questions / risks

All 6 questions were probed during the Phase 3.5 design pass (2026-06-08). Status after probing:

1. **🟢 WikiSEO / Description2 interaction with Approved Revs — RESOLVED GREEN.** Original concern: Approved Revs docs say *"most extensions that retrieve the contents of pages will still get the last revision"* with a Cargo/SMW carve-out; WikiSEO/Description2 weren't in the carve-out. **Empirical test on local docker (`docker compose up`, ApprovedRevs REL1_45 + WikiSEO REL1_45):** created page → set `{{#seo:title=ORIGINAL|description=ORIGINAL|image=original.png}}` → approved revid 134 → edited to `{{#seo:title=PROPOSED|description=PROPOSED|image=proposed.png}}` (becomes latest unapproved). Anon view: page body shows ORIGINAL_BODY, `og:title=ORIGINAL`, `og:description=ORIGINAL`, `og:image=original.png` — the **approved** revision's metadata. The parse API explicitly asked for the latest revision returns PROPOSED, but the public page render (which is what social-share crawlers fetch) returns ORIGINAL. **No mitigation required.** WikiSEO is Approved-Revs-aware because it reads through the page renderer, not the revision row directly.

2. **🟡 Lockdown read-restriction leakage edges — RESOLVED PARTIAL.** Empirical probe across 11 surfaces (direct GET, Search by title fragment, Search by body content, AllPages NS=3000, AllPages NS=0, RecentChanges, RecentChanges filtered to NS=3000, API list=allpages, API list=recentchanges, API parse, transclusion):
    - 🟢 **Body content fully protected** in all probes. Direct GET on a draft URL returns no body; transclusion is blocked by `$wgNonincludableNamespaces`; `action=parse` returns redacted body; search by body content was a false positive (the secret string only appeared as the user's echoed search query, not an indexed snippet).
    - 🔴 **Titles leak in 3 surfaces:** Special:AllPages?namespace=3000, Special:RecentChanges (including via filtered namespace selector + via CSS class names on each row), and the corresponding API endpoints (`list=allpages`, `list=recentchanges`).
    - Decision: **add a `ChangesListSpecialPageQuery` + `SpecialAllPagesQuery` filter hook to the in-repo `Wiki7ReviewGate` extension** (~30 LOC). Filters out NS_DRAFT rows for users not in the `reviewer` group. Same hooks cover the matching APIs.
    - Sitemap on prod: the existing `Wiki7-GenerateSitemap` SSM document needs an explicit `--namespace 0` filter (or `--exclude-namespace 3000`); confirm during step 8 of the implementation plan.

3. **🟢 Approved Revs MW 1.45.3 install — RESOLVED GREEN.** All three extensions have published `REL1_45` branches in upstream Wikimedia git (verified via `git ls-remote`). Built into the docker image via `git clone --depth 1 --branch REL1_45` (same pattern as Description2 / WikiSEO). `php maintenance/run.php update --quick` completed cleanly with all ApprovedRevs + Echo schema migrations applied. Latest stable v2.4.1 (May 2026); requires MW 1.42+.

4. **🟢 Notification opt-in — RESOLVED ACCEPTABLE.** Echo notifications are per-user-opt-in by default, but `$wgDefaultUserOptions['echo-subscriptions-{web,email}-wiki7-bot-review-pending'] = true` makes the custom notification opt-out by default. Reviewers don't need to visit Preferences.

5. **🟢 Cargo + Approved Revs — UNCHANGED GREEN.** Documented green-light from Approved Revs upstream. Not re-tested empirically since the WikiSEO test (1 above) already validated the page-render-path uses approved revisions, and Cargo is specifically called out as one of the few extensions that hit the approved revision rather than the latest. Confidence high.

6. **🟢 PageForms interaction — UNCHANGED.** Hook integration exists, no documented gotchas. Phase 3b decides whether to keep PageForms. Not blocking.

## Implementation plan

Local-first, mirroring the Phase 2.5d pattern. Each step is reversible; nothing touches prod until step 8.

| # | Step | Effort | Touches |
|---|---|---|---|
| 1 | Install Approved Revs via composer in local docker; verify install on MW 1.45.3 | 30-60 min | `docker/composer.local.json`, smoke test |
| 2 | Install Extension:Lockdown locally; smoke test the Draft-namespace read gate as anon + as reviewer | 30 min | `docker/composer.local.json`, `docker/LocalSettings.php` |
| 3 | Add Draft namespace, reviewer group, all permissions, Approved Revs config to `LocalSettings.php` | ~60 lines, 30 min | `docker/LocalSettings.php` |
| 4 | **EMPIRICAL TEST: WikiSEO + Description2 vs. approved/latest divergence** (Open Question #1). If broken, design + implement the WikiSEOPreAddMetadata fix. | 60-120 min | small scratch wikitext, possibly `docker/LocalSettings.php` hook |
| 5 | **EMPIRICAL TEST: draft-namespace leakage edges** — try to expose draft titles via every documented surface (RecentChanges, AllPages, Search, sitemap, API, transclusion, move, image upload) as an anon user | 60 min | smoke test only, no code |
| 6 | Build the small `Wiki7ReviewGate` in-repo extension (custom Echo notification type + PageSaveComplete listener + **Telegram dispatcher** + **`ChangesListSpecialPageQuery`/`SpecialAllPagesQuery` filter hooks** for the title-leak mitigation from Open Question #2) | ~140 lines PHP + 1 i18n file, 2-3h | `docker/extensions/Wiki7ReviewGate/` |
| 7 | Modify the data pipeline: add `prober.page_state(title)` + route NEW/UPDATE via `createonly`/`nocreate` + Draft prefixing | ~50 lines Python, 60 min + tests | `data/wiki_import/import_*.py`, `data/run_pipeline.py`, `data/tests/` |
| 8 | CDK: add `Wiki7TelegramBotSecret` (retained, env-file threaded per Phase 2.5d), add `$wgWiki7TelegramChatId` config. Pin extension versions in `docker/composer.local.json`, rebuild image, deploy | ~25 lines + deploy window (~7 min) | `cdk/lib/compute-stack.ts`, `docker/Dockerfile`, `docker/LocalSettings.php` |
| 9 | Live-verify on prod with one test player page (NEW + UPDATE flow), confirm Telegram message received, then merge backfill data | 30 min | prod |

Total optimistic effort: **~6-8 hours of focused work + 1 deploy window** (assuming Open Question #1's WikiSEO interaction has a clean fix).

Telegram bot creation (`@BotFather` flow, channel/group setup, chat_id discovery) is a manual step the operator handles before step 9.

## Decision rationale, one paragraph

We pick Approved Revs + Draft namespace + Lockdown + Echo because Approved Revs is the only actively-maintained MediaWiki-native extension that holds bot updates back from public view while preserving the reviewer's manual edits as a wiki-native diff, Cargo-compatible by upstream documentation, and the only "modern" gate that MW.org actively points at; Draft + Lockdown isolates the new-page case surgically so the rest of the wiki keeps working unchanged; Echo + a small in-repo extension gives us the notification spine without adding any external service. The architecture works at 1 reviewer today and 10 reviewers next year by adding users to a single group. The runner-up (FlaggedRevs + Draft) is a one-line fallback if WikiSEO interaction (the only remaining risk) proves un-fixable cleanly.
