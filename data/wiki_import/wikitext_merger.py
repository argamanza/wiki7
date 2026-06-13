"""Surgical wikitext merger that preserves reviewer edits.

Pattern B.1 (Pattern B in flight as of 2026-06-13). Replaces the §6 ⑨
M2-style page-level preserve-or-clobber decision with section-level
merging: when the bot rewrites a page, content INSIDE bot-managed
sections (delimited by `<!-- wiki7-bot-managed-section start: <id> -->`
… `end: <id>` markers) is updated; everything outside those markers is
preserved verbatim. This lets reviewers add hand-curated paragraphs,
fix layout, attach images, etc. without losing the work on every
re-import.

## "Was this ours" — two-prefix taxonomy (reviewer-pass fix, 2026-06-13)

Discrimination uses TWO prefixes, not one. The reviewer caught a
two-run wedge in the original single-prefix design: bot creates with
`Auto-import:`, reviewer copyedits OUTSIDE markers (no prefix on
their save), bot runs again and surgical-merges (saving with
`Auto-import:` per the original design), THIRD bot run sees
`Auto-import:` as the last summary, decides clean-rewrite is safe,
and overwrites the reviewer's paragraph.

The fix is a two-prefix taxonomy:

  - `Auto-import:` — bot OWNS this page; the whole content is bot
    output. Clean-rewrite is safe. Used when:
      * The page didn't exist before (first save).
      * The previous content was identical to what we'd produce now
        (a no-op re-import on a never-touched page).

  - `Auto-merge:` — bot OWNS the managed sections; a reviewer has
    touched something outside markers, so the surgical-merge result
    must NOT be eligible for clean-rewrite on the next run. Used
    every time `merge()` returns `surgical_merge`.

Both prefixes are "ours" for ownership purposes (the move-notification
gate, the §6 ⑨ M2 preserve-edit guard, etc.). The merger
distinguishes them ONLY for the clean-rewrite decision via
`is_clean_rewrite_eligible()`. Caller must use the correct prefix
based on the action returned by `merge()`:

  - action == "clean_rewrite" → save with `Auto-import:` prefix
  - action == "surgical_merge" → save with `Auto-merge:` prefix
  - action == "no_change" → don't save at all

The PHP side (`docker/import-pages.php`) only knows about
`Auto-import:` today — its preserve-edit guard treats `Auto-merge:` as
a "reviewer-touched" signal, which is the correct semantic (a merged
page DID have reviewer content). The constants are pinned by
`TestSharedConvention` to alarm if either side drifts.

## Why regex (not mwparserfromhell)

The original Pattern-B plan named `mwparserfromhell`. After the
2026-06-13 design review, the markers are HTML comments with a
well-defined unambiguous shape:

    <!-- wiki7-bot-managed-section start: <id> -->
    ...
    <!-- wiki7-bot-managed-section end: <id> -->

A two-line regex correctly identifies start/end pairs. mwparserfromhell
would parse the entire wikitext into a Wikicode tree, walk every node,
and we'd still need to pattern-match against comment contents to find
ours — strictly more work for no robustness gain on the marker shape we
control. If we ever hit an edge case where regex confuses (e.g. a
reviewer types literal marker syntax inside a quoted code block we want
preserved), revisit and route through mwparserfromhell's
`.filter_comments()` then — the API is the same shape, so the call
sites here won't change.

## Carried-forward constraint (a) — redirect-aware

This module does NOT call `resolve_redirect` itself. The caller (the
bot save path, `import_players._edit_page`) is responsible for
resolving redirects BEFORE invoking `merge()` — the merger should never
operate on a redirect page's wikitext (a redirect is a one-line
`#REDIRECT [[Target]]`, not content). Surgical-merging onto a redirect
is nonsense; the surgical merge would either lose the redirect (bad —
reviewers' rename is undone) or splice bot sections into a one-line
file (worse).

The `_edit_page` caller pattern is:

    final_title, ... = resolve_target_title(...)  # already redirect-aware
    page = site.pages[final_title]
    existing_text = page.text()
    merged_text, action = merge(
        existing_text, new_text, last_summary,
    )
    page.save(merged_text, summary=f"{BOT_EDIT_SUMMARY_PREFIX} ...")
"""

from __future__ import annotations

import logging
import re
from typing import Literal

logger = logging.getLogger(__name__)


# Two-prefix taxonomy (reviewer-pass 2026-06-13). Both prefixes mean
# "bot owns this revision" for ownership purposes; only Auto-import:
# means "whole page is bot output, clean-rewrite is safe". See
# `is_clean_rewrite_eligible` + module docstring.
#
# IMPORTANT: PHP-side `docker/import-pages.php` reads `BOT_EDIT_SUMMARY_PREFIX`
# (line 222 + 270 — both literal `"Auto-import:"`). If either side
# changes the prefix, BOTH must change in lockstep; the
# `TestSharedConvention` test pins this. See `wiki7-reviewer-pass-lessons`
# memory entry: `Auto-import:` is the system-wide "was this ours" signal.
BOT_EDIT_SUMMARY_PREFIX = "Auto-import:"
BOT_MERGE_SUMMARY_PREFIX = "Auto-merge:"


# Marker shape — multi-line tolerant (the warning text baked into the
# START marker spans multiple lines for readability in wikitext editors).
# Non-greedy `.*?` with re.DOTALL matches the SHORTEST `-->` after the id
# so adjacent start/end markers on the same line don't confuse parsing.
_MARKER_START_RE = re.compile(
    r"<!--\s*wiki7-bot-managed-section\s+start:\s*(?P<id>[A-Za-z][A-Za-z0-9_-]*).*?-->",
    re.IGNORECASE | re.DOTALL,
)
_MARKER_END_RE = re.compile(
    r"<!--\s*wiki7-bot-managed-section\s+end:\s*(?P<id>[A-Za-z][A-Za-z0-9_-]*)\s*-->",
    re.IGNORECASE,
)

# Section id syntax (constraint from B.2 marker placement review,
# 2026-06-13): kebab-case, alphanumeric + hyphen + underscore, must
# start with a letter, no spaces. Whitespace in ids would make the
# multi-line marker regex ambiguous AND clash with the ad-hoc
# `start:\s+(id)` shape; safer to flat-out reject. Used by
# `validate_section_id()` for fail-fast at template-render time.
_SECTION_ID_SYNTAX_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")


# Marker constants — used by templates to wrap a bot-managed span.
#
# Marker placement constraints (B.2 reviewer review, 2026-06-13):
#   1. The START marker bakes in a HUMAN-READABLE warning so editors
#      who go to wikitext source view see "do not edit inside" without
#      having to consult external docs. Edits inside managed sections
#      are silently lost on re-import; the warning is the user's only
#      in-place signal.
#   2. Section ids are a PERMANENT CONTRACT. Renaming an id orphans
#      the existing section across every page that uses it AND appends
#      a duplicate with the new id. Treat renames as schema migrations
#      with a one-time MovePage-style cleanup. `TestSharedConvention`
#      should pin the active id set; CI fails if a template drifts.
#   3. Markers MUST be flat — never nested. Nested ids would double-
#      count under the section-extraction logic. The merger's
#      `_find_section_spans` enforces flatness implicitly by tracking
#      a single open id at a time.
#   4. Ids must be space-free; enforced by the regex + `validate_section_id`.
MARKER_START = (
    "<!-- wiki7-bot-managed-section start: {id} | "
    "DO NOT EDIT INSIDE — Wiki7Bot overwrites this section on every "
    "re-import. Edit outside the markers, or update the data pipeline. -->"
)
MARKER_END = "<!-- wiki7-bot-managed-section end: {id} -->"


# Action labels returned by `merge`. Kept as string literals so callers
# can switch on them without importing an enum.
MergeAction = Literal["clean_rewrite", "surgical_merge", "no_change"]


def validate_section_id(section_id: str) -> str:
    """Return `section_id` if it's a valid kebab-case identifier; raise
    `ValueError` otherwise. Use at template-render time so a typo
    surfaces immediately instead of producing a marker the regex can't
    parse later.

    Valid: `infobox`, `career`, `youth-career`, `match_categories`,
    `fixtures-table`.
    Invalid: `youth career` (space), `1infobox` (leading digit),
    `-infobox` (leading hyphen), `''`, `None`.
    """
    if not section_id or not _SECTION_ID_SYNTAX_RE.match(section_id):
        raise ValueError(
            f"invalid section_id {section_id!r}: must be kebab/snake-case, "
            "alphanumeric + `-` + `_`, starting with a letter, no spaces. "
            "Section ids are a permanent contract — see "
            "data/wiki_import/wikitext_merger.py for the schema rules."
        )
    return section_id


def make_start_marker(section_id: str) -> str:
    """Build a START marker for `section_id`, validating first."""
    return MARKER_START.format(id=validate_section_id(section_id))


def make_end_marker(section_id: str) -> str:
    """Build an END marker for `section_id`, validating first."""
    return MARKER_END.format(id=validate_section_id(section_id))


# Permanent contract: the section ids each bot-managed template emits.
#
# Rename WARNING (B.2 review constraint, 2026-06-13): renaming an id
# orphans the old section across every existing page AND appends a
# duplicate at the new id. Treat any change to this dict as a SCHEMA
# MIGRATION: ship a one-time MovePage-style cleanup before deploying
# the rename. CI contract test (`TestKnownTemplateSections`) fails if a
# template renders an id outside its registered set.
#
# Conditional sections (e.g. `stats` only present when the player has
# rendered statistics) are still in the registered set — the contract
# is "the template MAY emit this id", not "MUST emit it".
KNOWN_TEMPLATE_SECTIONS: dict[str, frozenset[str]] = {
    "player_page.j2": frozenset({
        "infobox",       # always
        "youth-career",  # only when transfers_youth present
        "career",        # always (carries senior transfers + Cargo store)
        "stats",         # only when stats present
        "market-values", # only when market_values present
        "categories",    # always
    }),
    # Other templates added as B.2 expands beyond player pages —
    # match_report, competition_season, season_overview, etc.
}


def is_bot_authored(last_edit_summary: str | None) -> bool:
    """True iff the last edit summary starts with EITHER `Auto-import:`
    or `Auto-merge:` — i.e. the bot owns this revision in some form.

    This is the "ownership" check used by:
      - Wiki7ReviewGate's PageMoveComplete handler (notify on bot moves only)
      - `import-pages.php`'s preserve-edit guard (PHP currently only
        knows `Auto-import:`; it treats `Auto-merge:` as reviewer-touched,
        which is the CORRECT semantic — a merged page DID have reviewer
        content)
      - Any future "is this revision bot output" check

    For the more specific "is this safe to clean-rewrite over" check, use
    `is_clean_rewrite_eligible()` — only `Auto-import:` qualifies.

    Defensive against None / empty / whitespace-prefixed strings — only
    a prefix match on the leading non-whitespace token counts.
    """
    if not last_edit_summary:
        return False
    s = last_edit_summary.lstrip()
    return (
        s.startswith(BOT_EDIT_SUMMARY_PREFIX)
        or s.startswith(BOT_MERGE_SUMMARY_PREFIX)
    )


def is_clean_rewrite_eligible(last_edit_summary: str | None) -> bool:
    """True iff the last edit summary marks the page as fully bot-owned
    content (the `Auto-import:` prefix specifically). Reviewer-pass fix
    (2026-06-13): this is STRICTER than `is_bot_authored` — a previous
    surgical-merge save (which uses `Auto-merge:`) is NOT clean-rewrite-
    eligible, because that save preserved reviewer content. Treating
    `Auto-merge:` as clean-rewrite-eligible would create a two-run
    wedge where a reviewer's outside-marker edit gets clobbered on the
    third bot run.

    Used internally by `merge()` only. External callers should use
    `is_bot_authored` for ownership decisions.
    """
    if not last_edit_summary:
        return False
    return last_edit_summary.lstrip().startswith(BOT_EDIT_SUMMARY_PREFIX)


def _find_section_spans(text: str) -> dict[str, tuple[int, int]]:
    """Find every bot-managed section in `text`. Returns a dict mapping
    section_id to `(start_index, end_index)` where `start_index` is the
    position of the first character of the START marker and `end_index`
    is the position of the character AFTER the END marker (so
    `text[start_index:end_index]` is the section INCLUDING both markers).

    Defensive against the four classes of malformed input the reviewer
    flagged (B.2 review, 2026-06-13):

      - START marker without matching END: skip + warn (page may be
        mid-edit; better to leave it alone than corrupt it).
      - END marker without matching START: skip + warn.
      - **END marker BEFORE its START** (inverted order): skip + warn.
        Pre-fix this silently produced a negative-length span that
        corrupted the splice. Caught by the reviewer 2026-06-13.
      - Duplicate markers for same id (multiple starts OR multiple
        ends): first occurrence wins, rest skip + warn.

    Flat-marker enforcement (constraint #3): markers must NEVER be
    nested. A start marker for section X followed by another start
    marker for section Y before X's end marker is nested. The current
    pass logs a warning and treats the inner span as orphaned (skipped).
    Pattern B v1 keeps templates flat by convention; CI contract test
    is the long-term enforcement.
    """
    spans: dict[str, tuple[int, int]] = {}
    starts: dict[str, int] = {}

    for m in _MARKER_START_RE.finditer(text):
        section_id = m.group("id")
        if section_id in starts:
            logger.warning(
                "wikitext_merger: duplicate START marker for section %r "
                "at offset %d; ignoring (first marker at offset %d wins).",
                section_id, m.start(), starts[section_id],
            )
            continue
        starts[section_id] = m.start()

    for m in _MARKER_END_RE.finditer(text):
        section_id = m.group("id")
        start_offset = starts.get(section_id)
        if start_offset is None:
            logger.warning(
                "wikitext_merger: END marker for section %r at offset %d "
                "with no matching START; skipping.",
                section_id, m.start(),
            )
            continue
        # Reviewer-pass fix (2026-06-13): defend against inverted
        # marker order. Without this, an end-before-start would produce
        # text[start:end] with end < start → empty string → silent
        # corruption when spliced.
        if m.end() <= start_offset:
            logger.warning(
                "wikitext_merger: END marker for section %r at offset %d "
                "precedes its START at offset %d; skipping (inverted "
                "markers — reviewer needs to fix manually).",
                section_id, m.start(), start_offset,
            )
            continue
        if section_id in spans:
            # First end marker for this id wins; later duplicates are
            # treated as reviewer/template error.
            logger.warning(
                "wikitext_merger: duplicate END marker for section %r "
                "at offset %d; first end wins.",
                section_id, m.start(),
            )
            continue
        spans[section_id] = (start_offset, m.end())

    return spans


def extract_managed_sections(text: str) -> dict[str, str]:
    """Return a `{section_id: full_section_text}` mapping for every
    bot-managed section in `text`. The returned section_text INCLUDES
    the surrounding START + END markers. Mostly useful for testing and
    debugging the merger; the merge path uses `_find_section_spans`
    directly for performance."""
    return {
        sid: text[start:end]
        for sid, (start, end) in _find_section_spans(text).items()
    }


def surgical_merge(existing_text: str, new_text: str) -> str:
    """Replace each bot-managed section in `existing_text` with its
    counterpart from `new_text`. Preserves:
      - everything OUTSIDE the markers in `existing_text` (reviewer-edited
        content, hand-added sections that aren't bot-managed, layout
        tweaks, image placements, etc.)
      - sections present in `existing_text` whose ids are absent from
        `new_text` (e.g. the bot template no longer renders a section but
        the reviewer wants it kept — leave it alone).

    Appends to the end of the page:
      - sections present in `new_text` whose ids are absent from
        `existing_text` (new section the template introduced — the
        reviewer can move it later).

    This is the section-level analogue of the page-level "preserve if
    reviewer touched it" guard in `import-pages.php`'s M2 fix.
    """
    existing_spans = _find_section_spans(existing_text)
    new_spans = _find_section_spans(new_text)

    # Walk existing_text from left to right, replacing each matched span
    # with the new content (or leaving it if there's no new counterpart).
    out_parts: list[str] = []
    cursor = 0
    # Sort by start offset so we splice in document order.
    ordered = sorted(existing_spans.items(), key=lambda kv: kv[1][0])
    for sid, (start, end) in ordered:
        out_parts.append(existing_text[cursor:start])
        if sid in new_spans:
            new_start, new_end = new_spans[sid]
            out_parts.append(new_text[new_start:new_end])
        else:
            # Bot template no longer renders this section — preserve the
            # existing content as-is. Reviewer can clean up the dangling
            # markers if they want, but we don't strip them on our own.
            out_parts.append(existing_text[start:end])
        cursor = end
    out_parts.append(existing_text[cursor:])

    # Append sections that only exist in new_text.
    new_only_ids = [sid for sid in new_spans if sid not in existing_spans]
    if new_only_ids:
        # Insert a separator newline so we don't end up with the previous
        # content's trailing whitespace immediately abutting the new
        # marker — preserves wikitext block boundaries.
        for sid in new_only_ids:
            ns_start, ns_end = new_spans[sid]
            out_parts.append("\n")
            out_parts.append(new_text[ns_start:ns_end])
        logger.info(
            "wikitext_merger: appended %d new section(s): %s",
            len(new_only_ids), ", ".join(new_only_ids),
        )

    return "".join(out_parts)


def merge(
    existing_text: str | None,
    new_text: str,
    last_edit_summary: str | None,
) -> tuple[str, MergeAction]:
    """Top-level merger entrypoint. Returns `(merged_text, action)`.

    Action semantics:
      - "clean_rewrite" — the page didn't exist OR the last edit was
        ours (`Auto-import:` prefix). The whole page is bot output; we
        rewrite verbatim. The returned text is identical to `new_text`.
      - "surgical_merge" — a reviewer has edited the page since the last
        bot save. We splice the bot-managed sections from `new_text`
        into the existing wikitext, preserving everything outside
        markers. Returned text is the merged result.
      - "no_change" — surgical merge ran but yielded the same content
        as `existing_text`; the caller can skip the save call entirely.

    The caller MUST resolve redirects before invoking `merge` — see
    module docstring (Pattern B carried-forward constraint a). Passing
    a redirect page's wikitext through here will produce nonsense.
    """
    # Empty / nonexistent existing page → clean rewrite is the only sane
    # option; there's nothing to merge into.
    if not existing_text or not existing_text.strip():
        return new_text, "clean_rewrite"

    # Reviewer-pass fix (2026-06-13): use the STRICTER
    # `is_clean_rewrite_eligible` check, not `is_bot_authored`. Only the
    # `Auto-import:` prefix marks a page as fully-bot-owned (safe to
    # replace whole). `Auto-merge:` means a prior surgical merge
    # preserved reviewer content — replacing the whole page would
    # silently lose those reviewer edits on the next run. See module
    # docstring for the two-run wedge this defends against.
    if is_clean_rewrite_eligible(last_edit_summary):
        return new_text, "clean_rewrite"

    merged = surgical_merge(existing_text, new_text)
    if merged == existing_text:
        return merged, "no_change"
    return merged, "surgical_merge"
