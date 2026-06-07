# Research 0001 — MediaWiki Review-Gate Architecture

> Deep-research workflow run 2026-06-08. Full cited report below. Architectural decision lives in `docs/adr/0002-review-gate-architecture.md`.

## Question

How should we build a content-review workflow on MediaWiki 1.45.3 LTS where a bot pipeline writes pages, but bot output must pass human review before reaching public readers? Specifically: NEW pages hidden from public until promoted; UPDATES held back as proposed-vs-last-approved diffs; manual interim edits preserved; reviewer pool scaling solo → small group; MW-native extensions preferred.

## Run stats

- 101 agents, ~10 minutes
- 5 search angles fanned out: primary/official extensions; current maintenance reality; the bot-update-diff problem; Moderation extension practitioner reports; namespace-gate alternative + Echo notifications
- 19 sources fetched → 93 claims extracted → 25 verified (3-vote adversarial) → **20 confirmed, 5 refuted**
- 10 findings after synthesis

## Headline finding

**Recommended:** custom `Draft:` namespace (core MW only) for NEW pages + **Extension:Approved Revs** (Yaron Koren) for UPDATE handling. **NOT** FlaggedRevs (unmaintained-by-policy). **NOT** Moderation extension (bot group bypasses by design).

## Verified findings (3-0 unless noted)

### Finding 1 — FlaggedRevs is officially not recommended for production *(high confidence)*

MediaWiki.org banner on Extension:FlaggedRevs: *"Flagged Revisions is complex, poorly documented, very clunky, and not recommended for production use, despite the stable tag"* and *"has not been deployed (newly installed) to any Wikimedia wiki since 2014."*

Phabricator T185664 (WMF stewardship review): *"after a while it became virtually without any maintainer. Technical debt in this code is unimaginable... in matter of UX/UI it's non-standard and outdated"* with *"Current developers/maintainers: No one in particular."*

Phabricator T381044 (Nov 2024, WMF Moderator Tools team): the extension is *"deployed but no longer deployable"* across nearly 50 Wikimedia projects, and *"we stated that we will no longer deploy the extension on more wikis."* *"No replacement or alternative mapped or planned."*

Sources: [Extension:FlaggedRevs](https://www.mediawiki.org/wiki/Extension:FlaggedRevs), [T185664](https://phabricator.wikimedia.org/T185664), [T381044](https://phabricator.wikimedia.org/T381044), [Meta:Flagged Revisions](https://meta.wikimedia.org/wiki/Flagged_Revisions).

### Finding 2 — FlaggedRevs WAS cleaned up 2021-2024 (T277883) but reputation persists *(high confidence)*

T277883 closed 2024-07-10: codebase halved, tier-2 ("quality") and tier-3 ("pristine") support removed, single-tier/single-dimension model. The MW.org "not recommended for production use" warning *persists* post-cleanup (page last edited 2026-02-08). T185664 still Open. So: leaner than its reputation, but still no maintainer commitment.

Sources: [T277883](https://phabricator.wikimedia.org/T277883), [Extension:FlaggedRevs](https://www.mediawiki.org/wiki/Extension:FlaggedRevs).

### Finding 3 — FlaggedRevs *does* architecturally solve the UPDATE problem *(high confidence)*

*"Existing pages: Updated edits create pending revisions awaiting reviewer approval... Users see either the latest or stable version depending on configuration."* Configurable via `$wgFlaggedRevsOverride` globally and `action=stabilize` per-page. Has `Special:PendingChanges` as a built-in reviewer queue. Production-proven on de.wikipedia, Wikinews, Liquipedia.

This is the only verified extension whose default behavior natively matches the UPDATE-to-existing-page hold-back model — which is why it remains our runner-up despite the maintenance story.

Sources: [Extension:FlaggedRevs](https://www.mediawiki.org/wiki/Extension:FlaggedRevs), [Meta:Flagged Revisions](https://meta.wikimedia.org/wiki/Flagged_Revisions).

### Finding 4 — Extension:Moderation is actively maintained but no tagged 1.45 release *(high confidence)*

GitHub commits in June 2026 explicitly target MW 1.46 (e.g., 9bd524d 2026-05-30 *"(1.46) Replace deprecated hook EditFilter"*). v1.9.3 (2025-07-11) advertises 1.43-1.44; v1.8.31 (2025-06-13) advertises 1.39-1.43. None of 14 releases mention 1.45. Master branch is moving toward 1.46.

Implication: a Moderation deployment on MW 1.45.3 would require running master or REL1_46 branch, accepting some compatibility risk.

Sources: [edwardspec/mediawiki-moderation](https://github.com/edwardspec/mediawiki-moderation), [releases](https://github.com/edwardspec/mediawiki-moderation/releases).

### Finding 5 — Extension:Moderation is the WRONG tool for this spec *(high confidence)*

Two showstoppers regardless of maintenance status:

(a) The `bot` user group is granted `skip-moderation` BY DEFAULT — bot edits BYPASS the queue out of the box, the exact inverse of our requirement. From extension.json: `bot` group has both `skip-moderation = true` and `skip-move-moderation = true`. We could override with `$wgGroupPermissions['bot']['skip-moderation'] = false` but we'd be swimming upstream against the extension's design.

(b) MW.org has an explicit section titled *"Non-recommended use: Moderation as pre-publish review extension"* warning that strict quality-control use *"creates problems: Other users can't improve the article until it is Approved."*

The README positions it as *"a powerful anti-spam extension... sends all edits/uploads from new users to moderation."* It's an anti-spam tool, not a pre-publish review tool.

Sources: [Extension:Moderation](https://www.mediawiki.org/wiki/Extension:Moderation), [edwardspec/mediawiki-moderation](https://github.com/edwardspec/mediawiki-moderation).

### Finding 6 — Extension:Approved Revs is the viable primary recommendation *(high confidence)*

Two overrides flip its default behavior to match our spec:

- `$egApprovedRevsBlankIfUnapproved = true` — pages with no approved revision show blank to public readers (with parallel `$egApprovedRevsBlankFileIfUnapproved` for files). Default is "show latest."
- `$egApprovedRevsAutomaticApprovals = false` — disables auto-approval-on-edit by users with `approverevisions` right. Prevents the gate from being accidentally bypassed (e.g., a reviewer touching a page silently re-approving).

MW.org explicitly recommends it as the modern alternative to FlaggedRevs: *"As of 2025, Extension:Approved Revs has a similar purpose and is reasonably maintained."*

Sources: [Extension:Approved Revs](https://www.mediawiki.org/wiki/Extension:Approved_Revs), [Working with MediaWiki ch.14](https://workingwithmediawiki.com/book/chapter14.html).

### Finding 7 — Historical Moderation+ApprovedRevs interaction bug, fixed upstream *(high confidence)*

MW.org topic Uaeeicjtn4kms8ac + Phabricator T191175: edits became publicly visible without ApprovedRevs approval when a moderator with `approverevisions` approved a Moderation-queued edit from a non-approver. Root cause: ApprovedRevs's `checkPermission()` relied on `$wgUser` rather than the edit author. **Fixed upstream** by passing `$user` explicitly + using `$wikiPage->getUser()` in `ApprovedRevsHooks::updateLinksAfterEdit()`.

Implication: even though the bug is fixed, the documented historical conflict is an argument against unnecessarily stacking the two extensions. Our recommendation uses ApprovedRevs alone (no Moderation).

Sources: [Topic:Uaeeicjtn4kms8ac](https://www.mediawiki.org/wiki/Topic:Uaeeicjtn4kms8ac), [T191175](https://phabricator.wikimedia.org/T191175).

### Finding 8 — MW core Edit API has no draft semantics, but `createonly` / `nocreate` enable race-free routing *(high confidence)*

Every successful `action=edit` call is immediately published — no draft, pending, or hold-for-review flag exists in core. But:

- `createonly=1` — refuse if page exists. The bot pipeline uses this on the NEW path: atomic create-or-fail into `Draft:`.
- `nocreate=1` — refuse if page does not exist. The bot uses this on the UPDATE path: atomic update-or-fail in mainspace.

Both are stable across LTS versions including 1.45.3. The two flags give the bot a deterministic, race-free way to route NEW vs UPDATE down separate code paths.

Sources: [API:Edit](https://www.mediawiki.org/wiki/API:Edit).

### Finding 9 — Synthesis: the primary architecture *(high confidence)*

1. **NEW pages → custom `Draft:` namespace.** Create via `$wgExtraNamespaces`; restrict read+edit to a `reviewer` group via `$wgNamespaceProtection` / `$wgGroupPermissions` so public readers cannot see drafts. Bot writes `Draft:Foo` with `action=edit&createonly=1`. Reviewer approves by `Special:MovePage` → mainspace (preserves full history).
2. **UPDATE pages → mainspace + Approved Revs.** Install with `$egApprovedRevsAutomaticApprovals = false`; reviewer group granted `approverevisions`. Bot writes with `action=edit&nocreate=1&bot=1`. Edit is held as "latest unapproved" while public sees the previously-approved revision.
3. **Reviewer queue → Special:UnapprovedPages.** See diff via `Special:ComparePages` or page action=history. Approve wholesale or edit-then-approve.
4. **Notifications → Extension:Echo** for in-wiki. Slack/email webhook bridge as a follow-up via `EchoGetDefaultNotifiedUsers` hook or custom EmailNotification template.
5. **Scaling** — same config works at 1 or 10 reviewers; just add users to the `reviewer` group.

Why this split rather than ApprovedRevs everywhere: `$egApprovedRevsBlankIfUnapproved` is wiki-wide. Turning it on would blank every existing mainspace page (including the homepage) until back-approved one-by-one. The Draft namespace is surgical: only bot-NEW pages are hidden; mainspace stays exactly as it is today.

Sources: [Manual:Using custom namespaces](https://www.mediawiki.org/wiki/Manual:Using_custom_namespaces), [Manual:$wgNamespaceProtection](https://www.mediawiki.org/wiki/Manual:$wgNamespaceProtection), [Extension:Approved Revs](https://www.mediawiki.org/wiki/Extension:Approved_Revs), [API:Edit](https://www.mediawiki.org/wiki/API:Edit), [Extension:Echo](https://www.mediawiki.org/wiki/Extension:Echo).

### Finding 10 — Runner-up: FlaggedRevs on mainspace + Draft namespace for NEW *(medium confidence)*

If Approved Revs hits a blocker (the 4 open questions below), fall back to FlaggedRevs for mainspace UPDATE handling (it's still the only extension whose default behavior natively matches the spec). Keep the Draft namespace for NEW pages either way.

Accept the trade-off: explicitly-not-recommended-for-production extension in exchange for the only built-in pending-revisions UI in the ecosystem. Post-T277883 (July 2024) the codebase is leaner than its historical reputation suggests, and it works (~50 Wikimedia projects in active deployment) — it's just not recommended for new deployments.

Sources: [Extension:FlaggedRevs](https://www.mediawiki.org/wiki/Extension:FlaggedRevs), [T277883](https://phabricator.wikimedia.org/T277883).

## Refuted claims (5 killed, listed for transparency)

| Vote | Refuted claim |
|---|---|
| 0-3 | "The Moderation extension queues edits in a pre-publication state where the page remains unchanged until a moderator approves; queued edits do not appear in page history or RecentChanges, matching the spec." — refuted: bot bypasses by default. |
| 0-3 | "Documented MediaWiki compatibility tops out at 1.43+ (master branch) with no explicit mention of 1.45." — refuted: master is moving toward 1.46, not stuck at 1.43. |
| 1-2 | "FlaggedRevs is described by Yaron Koren's book as clunky and not recommended for production use." — refuted as misattribution: that framing comes from MW.org, not Koren. |
| 1-2 | "Moderation and ApprovedRevs can be intentionally combined as a hybrid pattern." — refuted: there's a documented conflict (Finding 7), not a recommended hybrid. |
| 1-2 | "The Edit API provides edit-conflict detection via basetimestamp/starttimestamp." — refuted as overstated: those flags exist, but they don't help the specific "preserve manual edits between bot runs" case. |

## Open questions / risks

These need direct verification (probes) before implementation:

1. **Does Extension:Approved Revs have a published REL1_45 branch / installs cleanly on MW 1.45.3 LTS?** The "reasonably maintained as of 2025" framing comes from MW.org's FlaggedRevs page — a passing endorsement, not formal stewardship.
2. **How does Approved Revs interact with our existing Cargo / PageForms / WikiSEO / Description2 stack?** When approved-rev and latest-rev diverge, does Cargo index the approved revision or the latest? Same for WikiSEO/Description2 metadata. If they index the latest, public pages could carry bot-proposed metadata even when bot content is held back. **This is the highest-risk unknown.**
3. **Notification surface.** Echo doesn't have a built-in "unapproved bot edit on watched page" notification type. Options: bot emits a webhook post-edit-run (simplest); custom Echo notification type (most native); EchoGetDefaultNotifiedUsers hook wrapping ApprovedRevs approval events.
4. **Does `$wgNamespaceProtection` alone fully hide drafts?** Need to verify across Special:RecentChanges, Special:AllPages, search, sitemap generation, `list=allpages` API. Known edge cases where draft titles can leak even when content is hidden — may need `$wgNamespacesToBeSearchedDefault`, `$wgContentNamespaces`, and sitemap-generation tweaks.

## Caveats from the research itself

- Notification design (Echo + Slack/email webhooks) was not directly verified by the surviving claims; included as a follow-up direction rather than a primary recommendation.
- The "Approved Revs as MW-recommended modern alternative to FlaggedRevs" framing is a passing endorsement on the FlaggedRevs page, not a formal stewardship designation.
- The Moderation+ApprovedRevs historical conflict was fixed upstream, so this caveat mainly argues against unnecessary stacking, not a blocker.
- `$wgNamespaceProtection`-based draft hiding should be tested specifically — known edge cases via Special:RecentChanges, transclusion, sitemap can leak draft titles even when content is hidden.

## Sources (19, primary unless noted)

1. [Extension:FlaggedRevs](https://www.mediawiki.org/wiki/Extension:FlaggedRevs)
2. [Extension:Moderation](https://www.mediawiki.org/wiki/Extension:Moderation)
3. [Working with MediaWiki, ch.14](https://workingwithmediawiki.com/book/chapter14.html)
4. [Wikipedia: Flagged Revisions](https://en.wikipedia.org/wiki/Flagged_Revisions) *(secondary)*
5. [Topic:Ufqzj4pup0z7s2tx](https://www.mediawiki.org/wiki/Topic:Ufqzj4pup0z7s2tx) *(forum)*
6. [Topic:Uaeeicjtn4kms8ac](https://www.mediawiki.org/wiki/Topic:Uaeeicjtn4kms8ac)
7. [Phabricator T185664](https://phabricator.wikimedia.org/T185664)
8. [Phabricator T277883](https://phabricator.wikimedia.org/T277883)
9. [Phabricator T381044](https://phabricator.wikimedia.org/T381044)
10. [API:Edit](https://www.mediawiki.org/wiki/API:Edit)
11. [github.com/edwardspec/mediawiki-moderation](https://github.com/edwardspec/mediawiki-moderation)
12. [Extension:Moderation/Hooks/ModerationIntercept](https://www.mediawiki.org/wiki/Extension:Moderation/Hooks/ModerationIntercept)
13. [edwardspec/mediawiki-moderation/releases](https://github.com/edwardspec/mediawiki-moderation/releases)
14. [Manual:$wgNamespaceProtection](https://www.mediawiki.org/wiki/Manual:$wgNamespaceProtection)
15. [Extension:Lockdown](https://www.mediawiki.org/wiki/Extension:Lockdown)
16. [Manual:Using custom namespaces](https://www.mediawiki.org/wiki/Manual:Using_custom_namespaces)
17. [Extension:Echo](https://www.mediawiki.org/wiki/Extension:Echo)
18. [Notifications/Developer guide](https://www.mediawiki.org/wiki/Notifications/Developer_guide)
19. [Extension:SlackNotifications](https://www.mediawiki.org/wiki/Extension:SlackNotifications)
