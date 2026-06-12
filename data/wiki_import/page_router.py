"""Page-router: decide where to write a TM entity's content + auto-MovePage
on title drift.

Sits between the pipeline (which generates a wikitext blob + thinks it
knows what title to write to) and mwclient's `page.save()` (which needs a
concrete final title). The router:

1. Looks up the entity in the state file
2. Compares the pipeline's intended title to what the state file recorded
3. If the actual wiki page (per the state file's record) is at a different
   title than the pipeline wants to write, MovePage the existing page
4. Updates the state file with the new title

This solves three iter-cycle 1 problems:
- Duplicate-on-promote: reviewer MovePaged `Draft:X` → mainspace `X`; the
  state file knows X is in NS_MAIN now → bot writes to mainspace X.
- Orphan-on-rename: reviewer MovePaged `Draft:הלדר לפופסיק` →
  `Draft:הלדר לופש` to fix the gibberish; state file knows the new title.
- YAML-override-driven rename: reviewer edits mappings.he.yaml + `src: manual`
  with a corrected Hebrew name; next pipeline emits the corrected title;
  router MovePages the existing draft to the new title automatically.

Iter-cycle 1 (2026-06-12): Pattern A.2 of the v1+ re-import architecture.
"""

from __future__ import annotations

import logging
from typing import Optional

import mwclient

from data_pipeline.pipeline_state import PageIndexState

logger = logging.getLogger(__name__)


def format_title(bare_title: str, namespace: int) -> str:
    """Compose a full page title from bare title + namespace number.

    NS_MAIN (0) is the bare title; NS_DRAFT (3000) and NS_TEMPLATE (10)
    get their canonical English prefix. This must match how MediaWiki
    addresses pages via the API — we want to be able to round-trip
    `format_title(parse_title(t))` and get the same string back.
    """
    if namespace == 0:
        return bare_title
    prefix_by_ns = {3000: "Draft", 10: "Template", 6: "File"}
    prefix = prefix_by_ns.get(namespace)
    if not prefix:
        raise ValueError(f"Unknown namespace {namespace}; extend format_title()")
    return f"{prefix}:{bare_title}"


def resolve_target_title(
    site: mwclient.Site,
    state: PageIndexState,
    tm_id: str | int,
    want_title: str,
    want_namespace: int = 3000,
) -> tuple[str, str]:
    """Determine where to actually write this entity's page, MovePaging the
    existing wiki page if its current location differs from where the
    pipeline wants to write next.

    Returns (final_full_title, action) where action is one of:
      - "create"   : no existing page; bot will create at final_title
      - "update"   : existing page is already at final_title; bot updates it
      - "moved"    : existing page was at a different title; we just
                     MovePaged it to final_title; bot then updates it
      - "stranded" : state file said a page exists at title X but the wiki
                     no longer has it (e.g. deleted by reviewer); falls
                     back to fresh-create at final_title

    Side effect: nothing (state file is updated by the caller AFTER the
    save succeeds, so a save-time failure doesn't poison the state file).
    """
    tm_id_str = str(tm_id)
    final_full = format_title(want_title, want_namespace)
    stored = state.get(tm_id_str)

    if not stored:
        # First time we've seen this TM ID
        # Check whether the desired title is already taken (e.g. created by
        # a previous run that crashed before state was saved). If so, treat
        # as update; otherwise create.
        existing_at_target = site.pages[final_full]
        if existing_at_target.exists:
            logger.debug(
                "TM ID %s: no state record but page already at %s -> treating as update",
                tm_id_str, final_full,
            )
            return final_full, "update"
        return final_full, "create"

    stored_title = stored["he_title"]
    stored_ns = int(stored["namespace"])
    stored_full = format_title(stored_title, stored_ns)

    # Did the title or namespace change?
    if stored_title == want_title and stored_ns == want_namespace:
        # No drift — just update in place
        return final_full, "update"

    # Drift detected. Verify the page is actually at the stored location
    # (it might have been deleted, MovePaged manually since last run, etc).
    stored_page = site.pages[stored_full]
    if not stored_page.exists:
        # Stranded record — the wiki doesn't have that page anymore. Treat as
        # fresh write to the new title.
        logger.info(
            "TM ID %s: state said %s exists but it doesn't; writing fresh to %s",
            tm_id_str, stored_full, final_full,
        )
        return final_full, "stranded"

    # Real drift — MovePage the existing page to the new title
    target_page = site.pages[final_full]
    if target_page.exists:
        # The target already exists too. This shouldn't happen normally
        # (would mean duplicate pages), but if it does, log + fall through
        # to update (we'll overwrite the target; the stranded original
        # becomes the responsibility of the reviewer to clean up).
        logger.warning(
            "TM ID %s: both %s (stored) AND %s (target) exist on wiki. "
            "Updating target; reviewer may need to delete %s manually.",
            tm_id_str, stored_full, final_full, stored_full,
        )
        return final_full, "update"

    try:
        stored_page.move(
            final_full,
            reason=f"Wiki7Bot pipeline: TM ID {tm_id_str} renamed via mapping override or sitelinks-first",
            no_redirect=True,
        )
        logger.info(
            "TM ID %s: MovePaged %s -> %s",
            tm_id_str, stored_full, final_full,
        )
        return final_full, "moved"
    except mwclient.errors.APIError as exc:
        # MovePage can fail for many reasons (rate limit retried but still
        # failed, target exists, source protected, etc.). Log and fall back
        # to writing to the new title — leaving the old as orphan that the
        # reviewer can clean up.
        logger.warning(
            "TM ID %s: MovePage %s -> %s failed (%s); writing to new title anyway, "
            "old page will be orphaned",
            tm_id_str, stored_full, final_full, exc,
        )
        return final_full, "stranded"
