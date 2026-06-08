"""Helpers for routing bot writes through the Phase 3.5 review gate.

For the architecture see `docs/adr/0002-review-gate-architecture.md`. In short:

- The bot must write NEW pages to `Draft:<title>` (Lockdown + the custom Draft
  namespace hide them from public readers until a reviewer promotes them by
  `Special:MovePage` -> mainspace).
- The bot must write UPDATES (the page already exists in mainspace, presumably
  already approved by a reviewer) to mainspace as a normal edit; ApprovedRevs
  holds the new revision back from public view as "latest unapproved" while
  public continues to see the previously-approved revision until the reviewer
  explicitly approves the update.

The router decides which path a given title belongs in by asking the wiki: does
`<title>` exist in mainspace? If yes -> mainspace UPDATE. If no -> Draft:.

The gate is opt-in via the env var `WIKI_GATE_ENABLED=1`; defaults to OFF so
existing tests + local docker workflows that pre-date Phase 3.5 keep working
unchanged. The pipeline runner (run_pipeline.py) sets it explicitly when
talking to the gated wiki.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    import mwclient

logger = logging.getLogger(__name__)

DRAFT_PREFIX = "Draft:"

# Title prefixes the gate leaves alone — these point at non-mainspace namespaces
# whose review-gate semantics are handled by Approved Revs alone (Template + File
# are gated by $egApprovedRevsEnabledNamespaces in docker/LocalSettings.php; the
# rest don't get bot-written). The pipeline never writes to MediaWiki: / User: /
# Help: / Project: / Module:, but they're listed here for safety so the router
# doesn't trip on a future write.
_NON_DRAFT_PREFIXES = (
    DRAFT_PREFIX, "Template:", "Category:", "File:", "User:",
    "Help:", "MediaWiki:", "Module:", "Project:",
)


def gate_enabled() -> bool:
    """True when WIKI_GATE_ENABLED=1 (or true/yes/on)."""
    return os.environ.get("WIKI_GATE_ENABLED", "").lower() in {"1", "true", "yes", "on"}


def route_title(site: "Optional[mwclient.Site]", title: str) -> str:
    """Decide where the bot should write `title`.

    Rules (when the gate is enabled — WIKI_GATE_ENABLED=1):
      - Already-namespaced title (Template:, Category:, File:, …) → leave alone.
        Approved Revs handles update gating for the gated namespaces.
      - Bare mainspace title that EXISTS → leave alone (bot's edit is an UPDATE
        and Approved Revs holds it as latest-unapproved).
      - Bare mainspace title that does NOT exist → prefix `Draft:`. Lockdown
        hides the draft from public until a reviewer promotes it.

    When the gate is disabled (default), returns the title unchanged.

    `site` may be None during dry runs; we assume "new page" and prefix
    `Draft:` so dry-run output reflects what would actually happen on prod.
    """
    if not gate_enabled():
        return title
    if title.startswith(_NON_DRAFT_PREFIXES):
        return title
    if site is None:
        return DRAFT_PREFIX + title
    try:
        if site.pages[title].exists:
            return title
        return DRAFT_PREFIX + title
    except Exception as exc:
        # Fail closed: if we can't tell whether the page exists, route to Draft.
        # Reviewer can then promote (or merge back). Beats silently overwriting
        # an already-approved mainspace page when the API hiccupped.
        logger.warning("route_title: could not probe '%s' (%s); routing to Draft", title, exc)
        return DRAFT_PREFIX + title
