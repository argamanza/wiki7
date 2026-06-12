"""Import normalized player data into MediaWiki as wiki pages."""

import hashlib
import json
import logging
from pathlib import Path
from typing import Optional

import jinja2

from data_pipeline.helpers import to_il_date, to_il_fee, to_season_display
import mwclient
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from wiki_import import review_gate

logger = logging.getLogger(__name__)

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"

DEFAULT_PLAYERS_PATH = Path(__file__).resolve().parent.parent / "data_pipeline" / "output" / "players.jsonl"
DEFAULT_TRANSFERS_PATH = Path(__file__).resolve().parent.parent / "data_pipeline" / "output" / "transfers.jsonl"
DEFAULT_MARKET_VALUES_PATH = Path(__file__).resolve().parent.parent / "data_pipeline" / "output" / "market_values.jsonl"
DEFAULT_STATS_PATH = Path(__file__).resolve().parent.parent / "data_pipeline" / "output" / "stats.jsonl"


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
def _edit_page(site: mwclient.Site, title: str, content: str, summary: str) -> bool:
    """Create or update a wiki page. Returns True if the page was changed.

    When WIKI_GATE_ENABLED=1, new mainspace pages are routed to the Draft:
    namespace (see review_gate.route_title); existing mainspace pages are
    edited in place (Approved Revs holds the new revision back from public).
    """
    title = review_gate.route_title(site, title)
    page = site.pages[title]
    if page.exists:
        existing = page.text()
        if _content_hash(existing.strip()) == _content_hash(content.strip()):
            logger.debug("Page '%s' is unchanged, skipping", title)
            return False
    page.save(content, summary=summary)
    logger.info("Saved page: %s", title)
    return True


def _build_player_page(player: dict, transfers: list, market_values: list, stats: list = None) -> str:
    """Render a player wiki page from normalized data."""
    player_transfers = [t for t in transfers if t.get("player_id") == player["id"]]
    player_mvs = [mv for mv in market_values if mv.get("player_id") == player["id"]]
    player_stats = sorted(
        [s for s in (stats or []) if s.get("player_id") == player["id"]],
        key=lambda s: s.get("season", ""),
    )
    return _render_template(
        "player_page.j2",
        player=player,
        transfers=player_transfers,
        market_values=player_mvs,
        stats=player_stats,
    )


def import_players(
    site: Optional[mwclient.Site] = None,
    players_path: Optional[Path] = None,
    transfers_path: Optional[Path] = None,
    market_values_path: Optional[Path] = None,
    stats_path: Optional[Path] = None,
    dry_run: bool = False,
) -> dict:
    """Import all player pages into MediaWiki.

    Args:
        site: An authenticated mwclient.Site instance (None for dry-run).
        players_path: Path to players.jsonl (or players.he.jsonl).
        transfers_path: Path to transfers.jsonl.
        market_values_path: Path to market_values.jsonl.
        stats_path: Path to stats.jsonl (optional).
        dry_run: If True, just preview changes without writing.

    Returns:
        A summary dict with counts of created, updated, skipped, and failed pages.
    """
    resolved_players = players_path or DEFAULT_PLAYERS_PATH
    resolved_transfers = transfers_path or DEFAULT_TRANSFERS_PATH
    resolved_mvs = market_values_path or DEFAULT_MARKET_VALUES_PATH
    resolved_stats = stats_path or DEFAULT_STATS_PATH

    players = _load_jsonl(resolved_players)
    transfers = _load_jsonl(resolved_transfers)
    market_values = _load_jsonl(resolved_mvs)
    stats = _load_jsonl(resolved_stats) if resolved_stats.exists() else []

    summary = {"created": 0, "updated": 0, "skipped": 0, "failed": 0, "errors": []}

    for player in players:
        title = player.get("name_hebrew") or player["name_english"]
        try:
            content = _build_player_page(player, transfers, market_values, stats)

            if dry_run:
                logger.info("[DRY RUN] Would create/update page: %s (%d chars)", title, len(content))
                summary["created"] += 1
                continue

            if site is None:
                raise RuntimeError("site is required when dry_run=False")

            # Phase 3a R2: route through the gate before probing existence so
            # the report layer reflects Draft-namespace reality. Without this,
            # every existing draft re-imports as "created" instead of skipped.
            routed = review_gate.route_title(site, title)
            page = site.pages[routed]
            if page.exists:
                existing = page.text()
                if _content_hash(existing.strip()) == _content_hash(content.strip()):
                    logger.debug("Page '%s' unchanged, skipping", title)
                    summary["skipped"] += 1
                    continue
                _edit_page(site, title, content, summary=f"Updated player page for {title}")
                summary["updated"] += 1
            else:
                _edit_page(site, title, content, summary=f"Created player page for {title}")
                summary["created"] += 1

        except (mwclient.errors.APIError, ConnectionError, RuntimeError) as exc:
            logger.error("Failed to import player '%s': %s", title, exc)
            summary["failed"] += 1
            summary["errors"].append({"page": title, "error": str(exc)})

    logger.info(
        "Player import complete: %d created, %d updated, %d skipped, %d failed",
        summary["created"], summary["updated"], summary["skipped"], summary["failed"],
    )
    return summary
