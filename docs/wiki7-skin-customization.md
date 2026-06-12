# Wiki7 Skin Customization

> **Purpose:** when (not if) we re-fork the Wiki7 skin from a newer Citizen release,
> this is the inventory of what makes Wiki7 *Wiki7* on top of Citizen, the recipe for
> applying those deltas cleanly, and the gotchas (eight from the 3.1.0 → 3.17.0
> re-fork + a 9th found in production after Phase 2 launch on 2026-06-06) that ate hours in the 3.1.0 → 3.17.0
> re-fork on 2026-06-05.

Wiki7 is a verbatim rename-fork of [Citizen](https://github.com/StarCitizenTools/mediawiki-skins-Citizen)
with a small, carefully scoped set of brand customizations on top. Keeping the fork as
"renamed Citizen + thin delta" instead of an organic divergence is a deliberate choice:
it means every Citizen release is mergeable and we don't accidentally drift into being
our own skin.

The flip side: each new Citizen tag needs a **fresh re-fork**, not a `git merge`. The
renames make a 3-way merge pathological, but the brand deltas are small enough that
re-applying them is faster than fighting the merge.

---

## Brand-delta inventory

These are the **only** intentional differences between Wiki7 and stock Citizen. When
re-forking, apply exactly these — nothing more.

### Theme tokens — `resources/skins.wiki7.tokens/`

| File | What we change | Why |
|---|---|---|
| `tokens-theme-base.less` | `--color-progressive-oklch__l: 46%`, `__c: 0.195`, `__h: 23` plus a `__l--base: 46%` for dark-mode parity. HSL fallback `__h: 350 __s: 85% __l: 42%`. | Maps Codex's "primary action color" onto Hapoel Beer Sheva brand red **#C8102E**. Every Codex surface/state token resolves through `--color-progressive-*`, so this single override re-paints links, focus rings, active states, accent backgrounds, etc. without per-component edits. |
| `tokens-theme-dark.less` | `--color-progressive-oklch__l: 62%`, `--color-progressive-hsl__l: 62%`. | Same hue/chroma, lifted lightness so the brand still reads against dark surfaces. (Even though we currently disable dark mode for the skin, keep this aligned — Codex still computes some surface tokens off the dark theme branch.) |

### Components — `resources/skins.wiki7.styles/components/`

| File | What we change | Why |
|---|---|---|
| `Header.less` | The fixed `.wiki7-header` rail gets `background-color: oklch(<brand>)` and `--filter-invert: none`. A single consolidated rule on `.wiki7-dropdown-summary` paints all five rail buttons (search, drawer, Preferences, UserMenu, Notifications) white-on-red by default and brand-red on a near-white box on `:hover` + `[open]`. The `[open]` rule **must** include `.cdx-button` in its selector to outrank Citizen's own `Dropdown.less` open-state override (see gotcha #1). | The rail is the most visible piece of brand identity. The summary-level scope means we never touch component internals. |
| `Drawer.less` | (a) `&__footer / &__footer-links / &__footer-link` styling for the social row. (b) `&__footer-icon--<name>` mask classes with **inline-SVG data URIs** for Facebook / X / Instagram / YouTube / linkExternal. (c) An RTL `.rtl #wiki7-drawer__card { transform-origin: calc(100% - var(--space-xs)) top !important }` so the drawer expands from the top of the right rail in Hebrew. | The footer is a new feature on top of Citizen. OOUI's iconpack doesn't bundle third-party brand logos (gotcha #3), so we embed them directly. The RTL origin override is the difference between the drawer animating elegantly and slamming open from the wrong corner. |
| `IconAnimation.less` | One line: the logo-hover color flips from `var(--color-base)` to `#C8102E`. | The home-icon overlay on the logo button is brand-red on hover; otherwise everything else in this file is upstream. |
| `Menu.less` | Drawer main-menu rules: `(a)` inactive rows shift `--color-base → --color-emphasized` on hover so the affordance feels alive without being loud, `(b)` `.mw-list-item-active > a` paints brand-red bg + white text/icon as the "you are here" state. | Mirrors the social-link hover pattern in `Drawer.less` for visual consistency. Active state needs the PHP hook below to actually mark the row. |

### Skin manifest — `skin.json`

- Three custom config keys:
  - `EnableHEFonts` (bool, default `false`) — toggles the bundled Hebrew webfont module.
  - `DrawerFooterLinks` (array, default `[]`) — list of `{label, href, icon, external}` objects rendered into `DrawerFooter.mustache`.
  - `HeaderPosition` (`left`/`right`/`top`/`bottom`, default `left`) — also exists upstream in 3.17, but our default is `left` (auto-flips to right in RTL).
- One extra ResourceModule: `skins.wiki7.styles.fonts.he` pointing at `resources/skins.wiki7.styles.fonts.he/index.less`.
- DarkMode is disabled for `wiki7` (inherited from Citizen attributes, unchanged).

### Hebrew fonts — `resources/skins.wiki7.styles.fonts.he/`

A self-contained directory: `index.less` overrides `--font-family-wiki7-base` / `--font-family-wiki7-serif` to Noto Sans / Serif Hebrew, `he.less` declares the `@font-face` blocks, and `fonts/` holds the two `.woff2` files (Noto Sans Hebrew, Noto Serif Hebrew), checked into the repo so the skin is self-sufficient. *(Rubik and Open Sans were also checked in originally but were never `@font-face`-declared anywhere — removed as dead weight in the 2026-06-12 review. The `@font-face` blocks use `format('woff2-variations')`, matching upstream's pattern for variable fonts.)*

### PHP — `includes/`

| File | What we change | Why |
|---|---|---|
| `SkinWiki7.php` | (a) Register `Wiki7EnableHEFonts → skins.wiki7.styles.fonts.he` in `OPTIONAL_FONT_MODULES`. (b) In `getTemplateData()`, copy `$wgWiki7DrawerFooterLinks` into the template data as `array-drawer-footer-items`. | Wires the two custom config keys through to the renderer. |
| `Hooks/SkinHooks.php` | Add `markActiveSidebarItem()` (called from the existing `onSidebarBeforeOutput`). Walks every sidebar item, marks the one whose `href` matches `$title->getLocalURL()` (with a special case for `n-mainpage` when `$title->isMainPage()`) by appending the standard `mw-list-item-active` class. | MediaWiki doesn't natively mark sidebar items active (it does for page tabs); this is what powers the "you are here" CSS in `Menu.less`. **Do not** add nested arrays under `link-html-attribs` — that throws `UnexpectedValueException` (gotcha #2). |

### Templates — `templates/`

| File | What we change | Why |
|---|---|---|
| `Drawer.mustache` | One added line: `{{>DrawerFooter}}` inside the menu card content, after the main menu. | Hook point for the footer partial. |
| `DrawerFooter.mustache` | New file — iterates `array-drawer-footer-items` and renders the social row. Uses self-contained `wiki7-drawer__footer-icon--<name>` classes (which Drawer.less paints via mask-image), not the `mw-ui-icon-wikimedia-*` OOUI classes (gotcha #3). | New partial for a new feature. |

### Configuration — `docker/LocalSettings.php`

- `$wgWiki7EnableHEFonts = true;` — turn on the Hebrew fonts module (must be set *before* `wfLoadSkin('Wiki7')`).
- `$wgWiki7HeaderPosition = 'left';` — rail on the left in LTR, auto-flipped right in RTL.
- `$wgWiki7DrawerFooterLinks = [ ... ];` — the actual social-link content for Hapoel Beer Sheva (Facebook, X, Instagram, YouTube, official site).
- `wfLoadExtension('TabberNeue');` — registers the tabbed-content extension.

---

## Re-fork recipe (3.x.y → 3.x+n.y)

Run these against a clean working tree from `master` (or a fresh branch off it). Total
elapsed time on the 3.1.0 → 3.17.0 jump was about 90 minutes including iteration; the
mechanical steps are 10 minutes, the rest is re-applying brand deltas + visual diffing.

```bash
# 0. Branch
git checkout -b refork/citizen-<new-tag>

# 1. Pull a clean upstream Citizen tarball
cd /tmp
curl -sL https://github.com/StarCitizenTools/mediawiki-skins-Citizen/archive/refs/tags/v<NEW>.tar.gz \
  | tar -xz
cd -

# 2. Replace docker/skins/Citizen with the fresh tarball (it's the verbatim
#    upstream reference, kept for future diffs)
git rm -rf docker/skins/Citizen
rm -rf docker/skins/Citizen
cp -R /tmp/mediawiki-skins-Citizen-<NEW> docker/skins/Citizen

# 3. Stage Wiki7 from the fresh Citizen, then run the rename
git rm -rf docker/skins/Wiki7
rm -rf docker/skins/Wiki7
cp -R docker/skins/Citizen docker/skins/Wiki7

# Case-aware rename: CITIZEN→WIKI7, Citizen→Wiki7, citizen→wiki7
# across file contents AND filenames AND directory names.
python3 docs/scripts/rename_wiki7.py

# 4. Sanity-check: zero "citizen" references should remain in Wiki7
grep -rE "Citizen|citizen|CITIZEN" --exclude-dir=.git docker/skins/Wiki7/ | wc -l
# Expect: 0
```

Now re-apply the brand deltas using the inventory above as the checklist. Don't be
clever — apply them surgically to the *new* file paths (which may have changed; e.g.
3.17 moved tokens into `resources/skins.wiki7.tokens/` from `resources/skins.wiki7.styles/`).

The PR for the 3.1.0 → 3.17.0 re-fork ([#23](https://github.com/argamanza/wiki7/pull/23))
is the canonical reference — each delta lives in its own commit, named per surface.

---

## Validation checklist

Before opening the PR:

1. `cd docker && docker compose down -v && docker compose up -d --build`
2. Wait for `Starting Apache` in `docker compose logs mediawiki`.
3. Smoke tests via `curl`:
   - `curl -sI localhost:8080/` → `200 OK`.
   - `curl -s "localhost:8080/api.php?action=query&meta=siteinfo&format=json"` → version is the new MW + lang `he`.
   - `curl -s "localhost:8080/api.php?action=cargotables&format=json"` → `200` with `cargotables: []` or your tables.
   - `curl -sLI "localhost:8080/api.php?action=visualeditor&page=Main_Page&paction=metadata&format=json"` → `200`.
   - `curl -s "localhost:8080/load.php?modules=skins.wiki7.tokens&only=styles" | grep -oE "color-progressive-(oklch|hsl)__[hcl]:[^;]+"` → expects `oklch__h:23`, `c:0.195`, `l:46%`, `hsl__h:350`.
4. Visual diff vs current master, in a real browser, **with hover and `[open]` interaction**:
   - Rail base: brand-red bg, white icons.
   - Rail hover / `[open]`: bright bg, brand-red icons. **All five** dropdown summaries must behave identically (the gotcha #1 trap).
   - Logo: wiki7 logo image visible by default; home icon appears only on hover.
   - Drawer open: "you are here" row in brand red; other rows soft-hover only.
   - Drawer footer: social icons render as proper Facebook/X/Instagram/YouTube glyphs (not red squares — gotcha #3).
   - Hebrew RTL layout: drawer expands from the top-right; menu card is anchored correctly.

Use [`docs/scripts/snap-drawer-open.py`](scripts/snap-drawer-open.py) for headless
`[open]`-state screenshots — it fetches the home HTML, force-opens the drawer
`<details>`, and captures via Chrome headless. Useful for PR review evidence.

---

## Gotchas (lessons from the 3.17.0 re-fork)

### 1. Citizen's `.cdx-button` specificity bump

`components/Dropdown.less` line 76 has a defensive rule:

```less
.wiki7-dropdown .wiki7-dropdown-details[open] > .wiki7-dropdown-summary.cdx-button {
  color: var( --color-emphasized );
}
```

The extra `.cdx-button` is **deliberate** — it bumps the rule's specificity to (0,5,0)
so it outranks Codex's quiet-button baseline. Any Wiki7 override on the open-state
summary **must** also include `.cdx-button` in its selector, otherwise it loses the
cascade and your brand color silently gets stomped to `--color-emphasized`. This was
~30 minutes of head-scratching in the 3.17.0 re-fork: the rule was served, looked
right in DevTools, but `currentColor` was being set by a higher-specificity rule
nobody had warned us about.

**Rule of thumb:** when overriding any state on `.wiki7-dropdown-summary`, mirror
Citizen's `.cdx-button` pattern in your own selector.

### 2. `link-html-attribs` does not exist as a sidebar-item key in MW 1.45

The first cut of `SkinHooks::markActiveSidebarItem` tried to set
`$item['link-html-attribs']['aria-current'] = 'page'` to add `aria-current` to the
matched `<a>`. MediaWiki interpreted the literal string `link-html-attribs` as an
HTML attribute name (because the key wasn't recognized) and threw
`UnexpectedValueException: HTML attribute link-html-attribs can not contain a list
of values`.

Setting `$item['class']` (which appends to the `<li>`) works fine, and that's what
the current implementation uses. If a future MW version supports per-link attribute
maps under a different key, the comment in `markActiveSidebarItem` is the place to
revisit. For now, **CSS-only via `.mw-list-item-active`** is the supported path.

### 3. OOUI iconpack doesn't ship third-party brand logos

The first cut of `DrawerFooter.mustache` used `mw-ui-icon-wikimedia-logo-Facebook`,
`-logo-X`, etc., assuming OOUI bundled them like it does `logo-Wikidata`. It does
not — only Wikimedia foundation logos (CC, MediaWiki, MetaWiki, Wikibooks, Wikidata,
Wikifunctions…) ship with the iconpack. Result: every social link rendered as a red
square because the mask-image URL was empty.

**Fix in place:** `Drawer.less` declares `.wiki7-drawer__footer-icon--<name>` classes
with inline-SVG data URIs for each brand. Self-contained, no external deps, survives
future Citizen icon-module reshuffles. Worth eventually extracting to standalone
`.svg` files (smaller CSS bundle) but not for Phase 1.

### 4. CSS-variable cascade vs. component scope

Earlier passes set `--background-color-button-quiet--hover/--active` directly on
`.wiki7-header` to brighten the rail-button hover. That cascade reaches **everything
inside the header**, including the drawer card (which is a DOM descendant of the
drawer details, which is a descendant of `.wiki7-header`). Result: every drawer menu
item also rendered with a near-white hover bg — invisible against the white menu
text.

**Fix in place:** the bright-bg overrides live on `.wiki7-header .wiki7-dropdown-summary`
only, not on `.wiki7-header`. Tight scope = no leak.

**Rule of thumb:** when overriding Codex-state CSS variables, put them on the
narrowest selector that needs them. If you can't, reset them explicitly on
inner surfaces.

### 5. `.wiki7-header__logo` and the transparent-home-icon trick

`IconAnimation.less` keeps the home-icon overlay hidden by default with
`.skin-wiki7 .wiki7-header__logo .cdx-button { color: transparent }`. This is what
makes the logo show the wiki7 image until you hover, at which point the home glyph
fades in.

Any blanket `.wiki7-header .wiki7-ui-icon { color: white }` rule clobbers this —
the home overlay becomes visible behind the logo at all times.

**Fix in place:** all blanket icon-color rules use `.wiki7-dropdown-summary` as the
scope, not `.wiki7-header .wiki7-ui-icon`. Since the logo isn't a dropdown summary,
the trick survives untouched.

### 6. MediaWiki's `mw-list-item-active` class doesn't exist on sidebar items by default

MediaWiki *does* mark page tabs (article/talk/edit/history) with active classes via
`SkinTemplate::buildContentNavigationUrlsInternal`, but it does **not** do this for
sidebar / drawer items — those come from `Skin::buildSidebar` and only carry the
fixed IDs like `n-mainpage`, `n-recentchanges`, etc. If you want active styling on
the drawer menu, you have to add it. The `SkinHooks::markActiveSidebarItem` we wrote
is reusable; just match `$title->getLocalURL()` against `$item['href']` plus a
special case for the main page (`$title->isMainPage() && $item['id'] === 'n-mainpage'`).

### 7. Less `&` placement in nested rules

`details[open] > &` does **not** mean "the summary when its details parent is
open" — Less expands `&` to the parent *selector chain*, so it compiles to
something like `details[open] > .wiki7-header > .wiki7-search > .wiki7-dropdown-summary`
which is structurally impossible. The first attempt at the open-state rule did this
and silently matched zero elements.

**Use:** explicit sibling rule with `[open]` in the desired position
(`.wiki7-dropdown-details[open] > .wiki7-dropdown-summary`), not nested `&`.

### 8a. Lazy-loaded Codex modules need the `.cdx-button` chain on idle-state rules too

Discovered in production after Phase 2 launch (2026-06-06). The base/idle-state
rule in `Header.less` —

```less
.wiki7-header .wiki7-dropdown-summary {  // specificity (0,2,0)
    color: white;
    ...
}
```

— wins the cascade on first paint but is **overridden the moment a user hovers
the search button**: `commandPalette.js` calls `bindIntentPrefetch` on the
search summary, which on first `pointerenter`/`focus`/`touchstart` fires
`mw.loader.load('skins.wiki7.commandPalette')`. That module's CSS, injected
later as a runtime `<style>` block, contains Codex's
`.cdx-button:enabled, .cdx-button.cdx-button--fake-button--enabled { color: ... }`
at the same `(0,2,0)` specificity. Same specificity + later source = injected
wins. Symptom: hovering search recolors **every** rail cdx-button (search *and*
menu, because the injected rule isn't scoped) and the change persists for the
session because the stylesheet stays loaded.

This is gotcha #1's twin — same `.cdx-button` chain, just on the idle rule
instead of `[open]`.

**Fix:** chain `.cdx-button` to bump to `(0,3,0)`:

```less
.wiki7-header .wiki7-dropdown-summary.cdx-button {  // (0,3,0) — survives lazy load
    color: white;
    ...
}
```

**General rule:** any rail rule whose color/background matters at the "base"
state must use the `.cdx-button` chain, not just the `[open]` state, because
intent-prefetch on the search/preferences/notifications/share summaries can
inject Codex's CdxButton base styles at any point during the session.

### 8b. MW 1.45 is **not** the current LTS

We pinned MW 1.45.3 because the goal was "modernize first, do PHP 8.2+/Codex
breaking changes once." But 1.45 security support ends **December 2026** —
about 6 months from the re-fork date. The current LTS is 1.43 (supported through
December 2027). Plan a small 1.45 → 1.46 (or next LTS) bump in late 2026; it will
be much smaller than the 1.43 → 1.45 jump because the heavy lifts (PHP version,
heading DOM, legacy media removal, Codex adoption) are already done.

---

## File-by-file diff summary (3.17.0 baseline → Wiki7)

Run this at any point to audit how much we've diverged from upstream:

```bash
# Components where we have brand deltas
for f in Header Drawer Menu IconAnimation; do
  echo "=== components/$f.less ==="
  diff <(sed 's/citizen/wiki7/g; s/Citizen/Wiki7/g' \
      docker/skins/Citizen/resources/skins.citizen.styles/components/$f.less) \
    docker/skins/Wiki7/resources/skins.wiki7.styles/components/$f.less | head -50
done

# Token files
for f in tokens-theme-base tokens-theme-dark; do
  echo "=== tokens/$f.less ==="
  diff <(sed 's/citizen/wiki7/g; s/Citizen/Wiki7/g' \
      docker/skins/Citizen/resources/skins.citizen.tokens/$f.less) \
    docker/skins/Wiki7/resources/skins.wiki7.tokens/$f.less
done
```

Expected line counts (`grep -cE "^[<>]"` on each diff) as of 3.17.0:

| File | Lines |
|---|---|
| `components/Header.less` | ~40 |
| `components/Drawer.less` | ~90 (most is inline-SVG mask data URIs) |
| `components/Menu.less` | ~30 |
| `components/IconAnimation.less` | 2 |
| `tokens/tokens-theme-base.less` | ~16 |
| `tokens/tokens-theme-dark.less` | 4 |

If a file's delta grows substantially between re-forks, look for upstream changes
that may have made our customization redundant or that we should adopt instead.
