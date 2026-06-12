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
) -> tuple[str, str, int]:
    """Determine where to actually write this entity's page, MovePaging the
    existing wiki page if its current location differs from where the
    pipeline wants to write next.

    Returns `(final_full_title, action, final_namespace)` where action is
    one of:
      - "create"   : no existing page; bot will create at final_title
      - "update"   : existing page is already at final_full_title; bot updates
      - "moved"    : router just MovePaged the existing page (within the
                     same namespace) to final_full_title; bot updates
      - "stranded" : state record was wrong (page deleted or move-target
                     conflict); bot falls back to a fresh create

    `final_namespace` is the ACTUAL namespace the page lives in (0 or 3000),
    not necessarily `want_namespace` — the caller MUST use it when calling
    `state.upsert()` so the state file records ground truth instead of an
    inferred-from-prefix guess.

    ## Invariants (the §6 ① fix from the 2026-06-12 review)

    1. **Never auto-promote `Draft:X → X` (cross-namespace move).** Only the
       human reviewer decides when a page is ready to publish.
    2. **Never auto-demote `X → Draft:X` (cross-namespace move).** This was
       the latent landmine the review caught: production calls with
       `want_namespace=3000` unconditionally, and the old code, on seeing
       stored_ns=0 vs want_ns=3000, would have MovePaged the live public
       page back into Draft — instantly making the page invisible.
    3. **Mainspace-first probe.** Before generating a new Draft target,
       check whether the page already lives in mainspace at `want_title`.
       If so, the reviewer promoted it (possibly between runs); treat
       mainspace as authoritative and sync state. This is how a
       reviewer's `Draft:X → X` move propagates into the state file
       without us scanning the move log.
    4. **Title drift moves stay within the stored namespace.** A reviewer
       rename `Draft:Old → Draft:New` (or `Old → New` in mainspace) is
       recorded by MovePaging within the same namespace.

    Side effect: nothing (state file is updated by the caller AFTER the
    save succeeds, so a save-time failure doesn't poison the state file).
    """
    tm_id_str = str(tm_id)
    stored = state.get(tm_id_str)

    # Mainspace-first probe — always. Covers:
    #  - reviewer promoted Draft:X -> X BEFORE we ever recorded state for X
    #  - reviewer promoted between our last state save and now (state still
    #    says ns=3000 but mainspace is now authoritative)
    main_full = format_title(want_title, 0)
    if site.pages[main_full].exists:
        logger.info(
            "TM ID %s: page found in mainspace at %s; treating mainspace as "
            "authoritative and syncing state (ns=0). Was: %s",
            tm_id_str, main_full,
            stored if stored else "<no state record>",
        )
        return main_full, "update", 0

    if not stored:
        # First time we've seen this TM ID, and mainspace probe already
        # confirmed no mainspace page exists. Check the Draft target —
        # a previous run may have created it before crashing.
        want_full = format_title(want_title, want_namespace)
        if site.pages[want_full].exists:
            logger.debug(
                "TM ID %s: no state but page exists at %s -> treating as update",
                tm_id_str, want_full,
            )
            return want_full, "update", want_namespace
        return want_full, "create", want_namespace

    stored_title = stored["he_title"]
    stored_ns = int(stored["namespace"])

    # Honor stored namespace. The router NEVER crosses namespace boundaries
    # with a move (see invariants 1 + 2 above). If the state says the page
    # is in mainspace, write to mainspace — the mainspace-first probe above
    # already confirmed it doesn't exist there, so it must have been
    # deleted; fall through to stranded handling. If state says Draft, the
    # write namespace is Draft — even though the caller passed
    # want_namespace=3000, we ignore that for a stored mainspace record.
    write_ns = stored_ns
    write_full = format_title(want_title, write_ns)
    stored_full = format_title(stored_title, stored_ns)

    # No drift — page is at the stored title (which equals want_title) in
    # the same namespace as before. Verify it still exists, then update.
    if stored_title == want_title:
        if site.pages[stored_full].exists:
            return stored_full, "update", stored_ns
        # Stranded: state said the page lives at stored_full but it's gone
        # (reviewer deleted, or move-log race we missed). Fall back to a
        # fresh create at want_namespace — typically Draft — because the
        # stored namespace may no longer reflect intent. The reviewer would
        # repeat the promote on the new draft when ready.
        logger.info(
            "TM ID %s: state said %s exists but it doesn't; writing fresh to %s",
            tm_id_str, stored_full, format_title(want_title, want_namespace),
        )
        return format_title(want_title, want_namespace), "stranded", want_namespace

    # Title drift WITHIN the stored namespace. Move the existing page from
    # stored_full to write_full (both in stored_ns). MovePage with
    # cross-namespace move was the catastrophic latent path — that's exactly
    # what we no longer do.
    stored_page = site.pages[stored_full]
    if not stored_page.exists:
        # Stranded — see above.
        logger.info(
            "TM ID %s: state said %s exists but it doesn't; writing fresh to %s",
            tm_id_str, stored_full, format_title(want_title, want_namespace),
        )
        return format_title(want_title, want_namespace), "stranded", want_namespace

    target_page = site.pages[write_full]
    if target_page.exists:
        # Both old and target exist in the same namespace — duplicate
        # situation. Log + update target; orphan stays for reviewer cleanup.
        logger.warning(
            "TM ID %s: both %s (stored) AND %s (target) exist on wiki. "
            "Updating target; reviewer may need to delete %s manually.",
            tm_id_str, stored_full, write_full, stored_full,
        )
        return write_full, "update", write_ns

    try:
        stored_page.move(
            write_full,
            reason=f"Wiki7Bot pipeline: TM ID {tm_id_str} renamed via mapping override or sitelinks-first",
            no_redirect=True,
        )
        logger.info(
            "TM ID %s: MovePaged %s -> %s (within ns=%d)",
            tm_id_str, stored_full, write_full, write_ns,
        )
        return write_full, "moved", write_ns
    except mwclient.errors.APIError as exc:
        # MovePage can fail for many reasons (rate limit retried but still
        # failed, target exists, source protected, etc.). Log and fall back
        # to writing the new title in want_namespace — leaving the old as
        # orphan that the reviewer can clean up.
        logger.warning(
            "TM ID %s: MovePage %s -> %s failed (%s); writing to new title anyway, "
            "old page will be orphaned",
            tm_id_str, stored_full, write_full, exc,
        )
        return format_title(want_title, want_namespace), "stranded", want_namespace
