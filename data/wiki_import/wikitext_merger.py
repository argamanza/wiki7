"""Surgical wikitext merger that preserves reviewer edits.

Pattern B.1 (Pattern B in flight as of 2026-06-13). Replaces the §6 ⑨
M2-style page-level preserve-or-clobber decision with section-level
merging: when the bot rewrites a page, content INSIDE bot-managed
sections (delimited by `<!-- wiki7-bot-managed-section start: <id> -->`
… `end: <id>` markers) is updated; everything outside those markers is
preserved verbatim. This lets reviewers add hand-curated paragraphs,
fix layout, attach images, etc. without losing the work on every
re-import.

## "Was this ours" discrimination (carried-forward constraint b)

Per the operator's directive (2026-06-13), discrimination uses the same
`Auto-import:`-prefix convention `docker/import-pages.php` already uses
for its own preserve-edit guard:

  - If the LAST edit on the page started with `Auto-import:`, the whole
    page is bot output from a prior run — clean-rewrite is safe.
  - Otherwise, a reviewer has touched the page since the last bot save;
    do a surgical merge, preserving everything outside markers.

The `BOT_EDIT_SUMMARY_PREFIX` constant is the single source of truth for
this prefix string; PHP-side and Python-side code agree on it by sharing
the literal `"Auto-import:"`.

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


# Carried-forward constraint (b): the `Auto-import:` prefix is the
# system-wide "was this ours" signal. Same string `import-pages.php` uses.
BOT_EDIT_SUMMARY_PREFIX = "Auto-import:"


# Marker shape. Two-line regex captures the section id.
# Tolerant of extra whitespace around the colon and inside the comment
# delimiters so a reviewer who reformats the wikitext doesn't break the
# match. Section id is the first non-whitespace token after "start:" / "end:".
_MARKER_START_RE = re.compile(
    r"<!--\s*wiki7-bot-managed-section\s+start:\s*(?P<id>\S+)\s*-->",
    re.IGNORECASE,
)
_MARKER_END_RE = re.compile(
    r"<!--\s*wiki7-bot-managed-section\s+end:\s*(?P<id>\S+)\s*-->",
    re.IGNORECASE,
)


# Sentinel patterns that callers can use to insert markers around a
# section body. Kept as a module-level helper so the rendering templates
# don't have to hand-paste the (already-correct) comment shape.
MARKER_START = "<!-- wiki7-bot-managed-section start: {id} -->"
MARKER_END = "<!-- wiki7-bot-managed-section end: {id} -->"


# Action labels returned by `merge`. Kept as string literals so callers
# can switch on them without importing an enum.
MergeAction = Literal["clean_rewrite", "surgical_merge", "no_change"]


def is_bot_authored(last_edit_summary: str | None) -> bool:
    """True iff the last edit summary starts with the bot-import prefix.

    Used by the merger to decide between clean-rewrite (whole page is
    bot output, safe to replace) and surgical-merge (a reviewer has
    touched the page since our last save).

    Defensive against None / empty / whitespace-prefixed strings — only
    a prefix match on the leading non-whitespace token counts.
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

    Skips a section if it has a start marker but no matching end marker
    (defensive against a partial / mid-edit page), logging a warning.
    The reviewer can clean up the partial markers manually.

    Repeated start markers for the same section id resolve to the
    FIRST occurrence (a duplicated marker pair is treated as a
    reviewer error; we defensively pick the first).
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

    # Last edit was ours → safe to replace the whole page.
    if is_bot_authored(last_edit_summary):
        return new_text, "clean_rewrite"

    merged = surgical_merge(existing_text, new_text)
    if merged == existing_text:
        return merged, "no_change"
    return merged, "surgical_merge"
