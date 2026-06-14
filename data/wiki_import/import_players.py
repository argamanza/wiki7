"""Import normalized player data into MediaWiki as wiki pages."""

import hashlib
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from data_pipeline.pipeline_state import PageIndexState

import jinja2

from data_pipeline.helpers import is_youth_club_name, to_il_date, to_il_fee, to_season_display
import mwclient
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from wiki_import import review_gate

logger = logging.getLogger(__name__)

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"

DEFAULT_PLAYERS_PATH = Path(__file__).resolve().parent.parent / "data_pipeline" / "output" / "players.jsonl"
DEFAULT_TRANSFERS_PATH = Path(__file__).resolve().parent.parent / "data_pipeline" / "output" / "transfers.jsonl"
DEFAULT_MARKET_VALUES_PATH = Path(__file__).resolve().parent.parent / "data_pipeline" / "output" / "market_values.jsonl"
DEFAULT_STATS_PATH = Path(__file__).resolve().parent.parent / "data_pipeline" / "output" / "stats.jsonl"
DEFAULT_COMPETITION_STATS_PATH = Path(__file__).resolve().parent.parent / "data_pipeline" / "output" / "competition_stats.jsonl"

# Per-competition rows render newest-season-first, then by descending
# appearances, so a player's primary competition leads each season block.
_COMPETITION_SORT = lambda r: (r.get("season", ""), -(r.get("appearances") or 0), r.get("competition", ""))  # noqa: E731


def _load_jsonl(path: Path) -> list:
    """Load newline-delimited JSON file into a list of dicts."""
    if not path.exists():
        raise FileNotFoundError(f"Data file not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _content_hash(text: str) -> str:
    """Return an MD5 hex digest of the given text for change detection."""
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def _render_template(template_name: str, **kwargs) -> str:
    """Render a Jinja2 template with the given context."""
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(TEMPLATE_DIR)),
        keep_trailing_newline=True,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    # Phase 3a R2: same `season_display` filter the import_templates renderer
    # exposes. Player pages render per-row season labels in the stats table
    # via `{{ s.season | season_display }}`.
    env.filters["season_display"] = to_season_display
    # Iter-cycle 1 (2026-06-12): Israeli DD/MM/YYYY date format + Hebrew
    # transfer-fee translation. Used on birth_date, transfer dates, market
    # value dates throughout player_page.j2.
    env.filters["il_date"] = to_il_date
    env.filters["il_fee"] = to_il_fee
    # Pattern B.2 (reviewer-pass 2026-06-13): expose the validated marker
    # builders as Jinja globals so templates wrap each bot-managed section
    # in `{{ bot_section_start('id') }}` / `{{ bot_section_end('id') }}`.
    # Each call validates the id (typo → ValueError at render time, not
    # in production) and bakes in the "DO NOT EDIT INSIDE" warning.
    from wiki_import.wikitext_merger import make_start_marker, make_end_marker
    env.globals["bot_section_start"] = make_start_marker
    env.globals["bot_section_end"] = make_end_marker
    template = env.get_template(template_name)
    return template.render(**kwargs)


# Iteration-cycle 2026-06-10: bumped from 3 attempts/~6s to 6 attempts /
# ~3min total to tolerate MediaWiki's `ratelimited` API error on burst
# writes. See import_matches.py _edit_page for the rationale.
@retry(
    stop=stop_after_attempt(6),
    wait=wait_exponential(multiplier=5, min=5, max=60),
    retry=retry_if_exception_type((mwclient.errors.APIError, ConnectionError)),
    reraise=True,
)
def _fetch_last_revision_summary(page: mwclient.page.Page) -> str | None:
    """Read the REAL last-revision edit summary from the live page,
    via the MediaWiki API.

    Pattern B.3 contract (reviewer-pass blocker, 2026-06-13): the entire
    surgical-merge wedge defense depends on this summary being accurate.
    A query that grabs the wrong revision's comment silently reopens the
    Auto-import: vs Auto-merge: wedge (the two-run wedge described in
    `wikitext_merger.py` module docstring) — the next bot run would see
    an outdated summary, take the wrong path, and lose reviewer content.

    Uses `page.revisions(limit=1, prop="comment")` — fetches the most
    recent revision's comment directly via the API, bypassing any cached
    page state. Returns None if the page has no revisions or the API
    call fails (caller treats None as "not bot-authored", i.e.
    surgical-merges, which is the safe default).
    """
    try:
        revs = list(page.revisions(limit=1, prop="comment"))
    except mwclient.errors.APIError as exc:
        logger.warning(
            "_fetch_last_revision_summary: API error reading %s: %s",
            page.name, exc,
        )
        return None
    if not revs:
        return None
    return revs[0].get("comment", "")


def _save_with_merger(
    page: mwclient.page.Page,
    new_content: str,
    summary_detail: str,
) -> str:
    """Pattern B.3 — merger-aware save. Returns one of:

      - "created"  — new page; saved with `Auto-import:` prefix.
      - "updated"  — clean-rewrite path (last save was ours OR existing
                     equals new); saved with `Auto-import:`.
      - "merged"   — surgical-merge path (reviewer touched the page
                     since the last bot save); saved with `Auto-merge:`
                     prefix to mark the revision NOT clean-rewrite-
                     eligible on the next run.
      - "skipped"  — surgical merge yielded identical text; no save.

    The action → prefix mapping IS the wedge defense. Stamping a
    surgical-merge save with `Auto-import:` would tell the next bot
    run "safe to clean-rewrite" and silently lose the reviewer's
    outside-marker content. See `wikitext_merger.merge` docstring.

    The page is assumed to be already routed (gate-resolved + redirect-
    resolved) by the caller. This function does NOT call
    `resolve_redirect` itself.
    """
    from wiki_import.wikitext_merger import (
        BOT_EDIT_SUMMARY_PREFIX,
        BOT_MERGE_SUMMARY_PREFIX,
        merge,
    )

    if not page.exists:
        summary = f"{BOT_EDIT_SUMMARY_PREFIX} {summary_detail}".strip()
        page.save(new_content, summary=summary)
        logger.info("Created page: %s", page.name)
        return "created"

    existing = page.text()
    # B.3 contract: read the REAL last-revision summary from the live
    # API. Falling back to a stale/cached value silently reopens the
    # wedge. See _fetch_last_revision_summary docstring + module test.
    last_summary = _fetch_last_revision_summary(page)
    merged, action = merge(existing, new_content, last_summary)

    if action == "no_change":
        logger.debug("Page '%s' unchanged after merge; skipping save", page.name)
        return "skipped"

    if action == "clean_rewrite":
        prefix = BOT_EDIT_SUMMARY_PREFIX
        result = "updated"
    elif action == "surgical_merge":
        prefix = BOT_MERGE_SUMMARY_PREFIX
        result = "merged"
    else:
        raise AssertionError(f"unknown merge action {action!r}")

    summary = f"{prefix} {summary_detail}".strip()
    page.save(merged, summary=summary)
    logger.info("Saved page: %s (action=%s)", page.name, action)
    return result


def _edit_page(site: mwclient.Site, title: str, content: str, summary_detail: str) -> bool:
    """Legacy create/update entry point used by the non-state-aware path.

    Pattern B.3 (2026-06-13): now routes through `_save_with_merger` so
    reviewer edits outside bot-managed sections survive every re-import.
    The `summary_detail` parameter is appended to the action-appropriate
    prefix (`Auto-import:` or `Auto-merge:`) by `_save_with_merger`.

    Returns True if the page was changed (created / updated / merged),
    False if the save was skipped (no_change).
    """
    title = review_gate.route_title(site, title)
    # Pattern B constraint (a): resolve redirects BEFORE reading content.
    # Surgical-merging onto a redirect is nonsense; the merger always
    # wants the real content page.
    from wiki_import.page_router import resolve_redirect
    resolved_title, _was_redirect = resolve_redirect(site, title)
    page = site.pages[resolved_title]
    result = _save_with_merger(page, content, summary_detail)
    return result != "skipped"


def _build_player_page(player: dict, transfers: list, market_values: list,
                       stats: list = None, competition_stats: list = None) -> str:
    """Render a player wiki page from normalized data."""
    player_transfers = [t for t in transfers if t.get("player_id") == player["id"]]
    player_mvs = [mv for mv in market_values if mv.get("player_id") == player["id"]]
    player_stats = sorted(
        [s for s in (stats or []) if s.get("player_id") == player["id"]],
        key=lambda s: s.get("season", ""),
    )
    player_competition_stats = sorted(
        [c for c in (competition_stats or []) if c.get("player_id") == player["id"]],
        key=_COMPETITION_SORT,
    )
    # Iter-cycle 1 walk (2026-06-12): bucket transfers by destination-club
    # youth marker. A transfer is "youth" iff its `to_club` carries a
    # youth-team suffix ("Benfica U17", "Sporting Yth."). The pro-debut
    # transfer (youth → senior) lands in `transfers_senior` because its
    # destination is the senior club. Order within each bucket follows
    # the input order, which the upstream sort already places chronologically.
    transfers_youth = [
        t for t in player_transfers if is_youth_club_name(t.get("to_club"))
    ]
    transfers_senior = [
        t for t in player_transfers if not is_youth_club_name(t.get("to_club"))
    ]
    return _render_template(
        "player_page.j2",
        player=player,
        transfers=player_transfers,
        transfers_youth=transfers_youth,
        transfers_senior=transfers_senior,
        market_values=player_mvs,
        stats=player_stats,
        competition_stats=player_competition_stats,
    )


def import_players(
    site: Optional[mwclient.Site] = None,
    players_path: Optional[Path] = None,
    transfers_path: Optional[Path] = None,
    market_values_path: Optional[Path] = None,
    stats_path: Optional[Path] = None,
    competition_stats_path: Optional[Path] = None,
    dry_run: bool = False,
    state: Optional["PageIndexState"] = None,
) -> dict:
    """Import all player pages into MediaWiki.

    Args:
        site: An authenticated mwclient.Site instance (None for dry-run).
        players_path: Path to players.jsonl (or players.he.jsonl).
        transfers_path: Path to transfers.jsonl.
        market_values_path: Path to market_values.jsonl.
        stats_path: Path to stats.jsonl (optional).
        dry_run: If True, just preview changes without writing.
        state: Pattern A.1 page-index state. Pass through to enable
               auto-MovePage on title drift. None = no state tracking
               (backward-compatible; mid-iter-cycle 1 fallback).

    Returns:
        A summary dict with counts of created, updated, skipped, and failed pages.
    """
    from wiki_import.page_router import resolve_target_title

    resolved_players = players_path or DEFAULT_PLAYERS_PATH
    resolved_transfers = transfers_path or DEFAULT_TRANSFERS_PATH
    resolved_mvs = market_values_path or DEFAULT_MARKET_VALUES_PATH
    resolved_stats = stats_path or DEFAULT_STATS_PATH
    resolved_competition_stats = competition_stats_path or DEFAULT_COMPETITION_STATS_PATH

    players = _load_jsonl(resolved_players)
    transfers = _load_jsonl(resolved_transfers)
    market_values = _load_jsonl(resolved_mvs)
    stats = _load_jsonl(resolved_stats) if resolved_stats.exists() else []
    competition_stats = _load_jsonl(resolved_competition_stats) if resolved_competition_stats.exists() else []

    summary = {"created": 0, "updated": 0, "skipped": 0, "failed": 0, "moved": 0, "errors": []}

    for player in players:
        bare_title = player.get("name_hebrew") or player["name_english"]
        tm_id = str(player["id"])
        try:
            content = _build_player_page(player, transfers, market_values, stats, competition_stats)

            if dry_run:
                logger.info("[DRY RUN] Would create/update page: %s (%d chars)", bare_title, len(content))
                summary["created"] += 1
                continue

            if site is None:
                raise RuntimeError("site is required when dry_run=False")

            # Pattern A (iter-cycle 1 v1+ architecture): when a state file is
            # provided, route through it. The router handles MovePages on title
            # drift (reviewer-fixed Hebrew names, draft -> mainspace promotions,
            # sitelinks-first overrides) so the bot writes to the page's CURRENT
            # location instead of creating duplicates or orphans.
            #
            # Without state, fall back to the legacy gate-routed path so this
            # function stays backward-compatible.
            if state is not None:
                # Fresh bot writes want Draft (want_namespace=3000); the router
                # may override to 0 if it discovers the page is already in
                # mainspace (reviewer promoted it). The router returns the
                # ACTUAL namespace as final_ns — that's what we record in
                # state, NOT a parsed-from-prefix guess. The §6 ① fix.
                final_full, action, final_ns = resolve_target_title(
                    site, state, tm_id, bare_title, want_namespace=3000,
                )
                if action == "moved":
                    summary["moved"] += 1
            else:
                final_full = review_gate.route_title(site, bare_title)
                action = None
                final_ns = None

            # Pattern B.3 (2026-06-13): merger-aware save. The state-aware
            # path skipped the legacy `_edit_page` for gate-routing reasons,
            # but now needs the same merger pipeline so reviewer edits
            # outside bot-managed sections survive re-imports. Title is
            # already gate-routed + redirect-resolved by
            # `resolve_target_title` (see Pattern A's redirect-aware
            # `resolve_redirect` integration), so we go straight to
            # `_save_with_merger` without re-routing.
            page = site.pages[final_full]
            save_result = _save_with_merger(
                page, content, f"player page for {bare_title}",
            )
            if save_result == "skipped":
                summary["skipped"] += 1
            elif save_result == "created":
                summary["created"] += 1
            elif save_result == "updated":
                summary["updated"] += 1
            elif save_result == "merged":
                # Surgical merge — bot-managed sections updated, reviewer
                # content outside markers preserved. Counted in `updated`
                # tally too for the operator-facing summary, but logged
                # separately for visibility.
                summary["updated"] += 1
                summary.setdefault("merged", 0)
                summary["merged"] += 1

            # State file: record where the page lives now. Done AFTER save
            # succeeded — if save throws, state stays consistent with reality.
            if state is not None:
                state.upsert(tm_id, bare_title, final_ns)

        except (mwclient.errors.APIError, ConnectionError, RuntimeError) as exc:
            logger.error("Failed to import player '%s': %s", bare_title, exc)
            summary["failed"] += 1
            summary["errors"].append({"page": bare_title, "error": str(exc)})

    # Pattern B.4 (2026-06-13): surface the merger + mover counts to the
    # operator-facing digest. `merged` is the surgical-merge count from
    # B.3 (how many pages had reviewer content preserved this run);
    # `moved` is Pattern A's auto-MovePage count. Pure client-side
    # visibility — there is intentionally NO server-side bot-move
    # notification channel (reviewers see moves via the queue + move log;
    # a push notification for content-less renames would just erode the
    # save notifications that actually carry the content change).
    logger.info(
        "Player import complete: %d created, %d updated (%d merged), "
        "%d skipped, %d moved, %d failed",
        summary["created"], summary["updated"], summary.get("merged", 0),
        summary["skipped"], summary.get("moved", 0), summary["failed"],
    )
    return summary
