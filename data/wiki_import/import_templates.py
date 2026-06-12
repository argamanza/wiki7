"""Create/update Cargo table definition templates and season summary pages."""

import hashlib
import json
import logging
from pathlib import Path
from typing import Optional

import jinja2
import mwclient
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from data_pipeline.helpers import to_il_date, to_il_fee, to_season_display
from wiki_import import review_gate

logger = logging.getLogger(__name__)

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
MEDIAWIKI_TEMPLATE_DIR = Path(__file__).resolve().parent / "mediawiki_templates"

DEFAULT_PLAYERS_PATH = Path(__file__).resolve().parent.parent / "data_pipeline" / "output" / "players.jsonl"
DEFAULT_TRANSFERS_PATH = Path(__file__).resolve().parent.parent / "data_pipeline" / "output" / "transfers.jsonl"
DEFAULT_STATS_PATH = Path(__file__).resolve().parent.parent / "data_pipeline" / "output" / "stats.jsonl"

# Hapoel Beer Sheva related keywords for detecting incoming/outgoing transfers
HBS_KEYWORDS = ("hapoel beer sheva", "beer sheva", "h. beer sheva", "hapoel be'er sheva")


DEFAULT_SCRAPER_OUTPUT_DIR = Path(__file__).resolve().parent.parent / "tmk-scraper" / "output"


# Mapping of MediaWiki template page titles to their wikitext source files.
MEDIAWIKI_TEMPLATES = {
    "Template:Tooltip": "Tooltip.wikitext",
    "Template:Player infobox": "Player_infobox.wikitext",
    "Template:Match infobox": "Match_infobox.wikitext",
    "Template:Stadium infobox": "Stadium_infobox.wikitext",
}


CARGO_TABLES = {
    "Template:Cargo/Player": {
        "table": "players",
        "fields": {
            "tmk_id": "String",
            "name_english": "String",
            "name_hebrew": "String",
            "birth_date": "Date",
            "birth_place": "String",
            "nationality": "List (,) of String",
            "main_position": "String",
            "current_squad": "Boolean",
            "current_jersey_number": "Integer",
            "homegrown": "Boolean",
            "retired": "Boolean",
            # Phase 3a R2 additions — scraped from the TM player profile facts.
            # All nullable for historical players whose profiles omit them.
            "preferred_foot": "String",      # "right" / "left" / "both"
            "height_cm": "Integer",          # height in centimetres
            "contract_expires": "String",    # e.g. "30/06/2027"
            "is_captain": "Boolean",         # current squad captain flag
            "current_market_value": "String",  # latest entry of MV history
            "other_positions": "List (,) of String",
        },
    },
    "Template:Cargo/Transfer": {
        "table": "transfers",
        "fields": {
            "player_id": "String",
            "season": "String",
            "transfer_date": "String",
            "from_club": "String",
            "to_club": "String",
            "fee": "String",
            "loan": "Boolean",
            # Phase 3a R2: TM club IDs for cross-linking once club pages exist.
            "from_club_tm_id": "String",
            "to_club_tm_id": "String",
        },
    },
    "Template:Cargo/MarketValue": {
        "table": "market_values",
        "fields": {
            "player_id": "String",
            "value_date": "String",
            "value": "String",
            "team": "String",
        },
    },
    "Template:Cargo/Match": {
        # `match_reports` not `matches` — `matches` is a Cargo reserved word
        # for both fields AND tables (CargoDeclare.php's $cargoReservedWords).
        # Empirically verified iter-cycle 1: the table-rejection is silent
        # (page_props for Template:Cargo/Match remained empty while the other
        # 8 Cargo declarations registered cleanly). Same constraint applied
        # to the `matches` field in three other tables (renamed to `played`).
        "table": "match_reports",
        "fields": {
            "competition": "String",
            "matchday": "String",
            "match_date": "String",
            "match_time": "String",
            "venue": "String",
            "opponent": "String",
            "result": "String",
            "system_of_play": "String",
            "attendance": "String",
            "season": "String",
            # Phase 3a R2: match-detail additions.
            "halftime_score": "String",      # "0:0" / "1:2"
            "aet": "Boolean",                # extra time played
            "stadium": "String",             # per-match stadium (away matches)
            # Referee team. TM exposes only `referee` (main) inline in the
            # match-report metadata; the other 5 fields are nullable forward-
            # compat slots for hand-curation by reviewers + a future IFA
            # scraper (filed as Phase 4 backlog).
            "referee": "String",
            "assistant_referee_1": "String",
            "assistant_referee_2": "String",
            "fourth_official": "String",
            "var_referee": "String",         # populated 2022/23+ when hand-curated
            "var_assistant": "String",       # populated 2022/23+ when hand-curated
        },
    },
    "Template:Cargo/PlayerStats": {
        "table": "player_stats",
        "fields": {
            "player_id": "String",
            "season": "String",
            "appearances": "Integer",
            "goals": "Integer",
            "assists": "Integer",
            "yellow_cards": "Integer",
            "second_yellow_cards": "Integer",
            "red_cards": "Integer",
            "minutes_played": "Integer",
        },
    },
    "Template:Cargo/Coach": {
        "table": "coaches",
        "fields": {
            "coach_id": "String",
            "name": "String",
            "tenure_start": "String",
            "tenure_end": "String",
            # `played` not `matches` — Cargo reserves `matches` as a SQL-ish
            # keyword (CargoDeclare.php $cargoReservedWords). Same constraint
            # applies to season_standings + head_to_head schemas below.
            "played": "Integer",
            "wins": "Integer",
            "draws": "Integer",
            "losses": "Integer",
            "ppm": "String",
            # Phase 3a R2: tenure context + trophies-won join.
            "is_caretaker": "Boolean",
            "tenure_seasons": "List (,) of String",
            "hbs_trophies_won": "List (,) of String",
        },
    },
    "Template:Cargo/Honour": {
        "table": "honours",
        "fields": {
            "competition": "String",
            "achievement": "String",
            "seasons": "List (,) of String",
        },
    },
    # Phase 3a R2: new tables.
    "Template:Cargo/SeasonStanding": {
        "table": "season_standings",
        "fields": {
            "season": "String",              # "2024" (start-year)
            "competition": "String",         # e.g. "Ligat ha'Al"
            "tier": "Integer",               # 1 = top flight, 2 = second tier
            "final_position": "Integer",
            "played": "Integer",             # NOT `matches` — Cargo reserved (see Coach above)
            "wins": "Integer",
            "draws": "Integer",
            "losses": "Integer",
            "goals_for": "Integer",
            "goals_against": "Integer",
            "points": "Integer",
        },
    },
    "Template:Cargo/HeadToHead": {
        "table": "head_to_head",
        "fields": {
            "opponent": "String",
            "opponent_tm_id": "String",
            "played": "Integer",             # NOT `matches` — Cargo reserved (see Coach above)
            "wins": "Integer",
            "draws": "Integer",
            "losses": "Integer",
            "goals_for": "Integer",
            "goals_against": "Integer",
            "avg_attendance": "Integer",
        },
    },
}


def _content_hash(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def _render_template(template_name: str, **kwargs) -> str:
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(TEMPLATE_DIR)),
        keep_trailing_newline=True,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    # Phase 3a R2: expose to_season_display as a filter so per-row template
    # contexts (e.g. multi-season stats tables on player pages) can convert
    # each row's bare-integer season to the slash display without inlining
    # the format math. Usage: `{{ s.season | season_display }}`.
    env.filters["season_display"] = to_season_display
    # Iter-cycle 1 (2026-06-12): Israeli DD/MM/YYYY date format + Hebrew
    # transfer-fee translation. Used in match_report.j2 etc.
    env.filters["il_date"] = to_il_date
    env.filters["il_fee"] = to_il_fee
    template = env.get_template(template_name)
    return template.render(**kwargs)


def _load_jsonl(path: Path) -> list:
    if not path.exists():
        raise FileNotFoundError(f"Data file not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


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
    # See wiki_import.review_gate for the Phase 3.5 routing rules.
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


def _build_cargo_template(table_name: str, fields: dict) -> str:
    """Build Cargo table declaration wikitext."""
    lines = ["<noinclude>", f"This template defines the Cargo table '''{ table_name }'''.", ""]
    lines.append("{{#cargo_declare:")
    lines.append(f"_table={table_name}")
    for field_name, field_type in fields.items():
        lines.append(f"|{field_name}={field_type}")
    lines.append("}}")
    lines.append("</noinclude>")
    lines.append("<includeonly>")
    lines.append("{{#cargo_store:")
    lines.append(f"_table={table_name}")
    for field_name in fields:
        lines.append(f"|{field_name}={{{{{{{field_name}|}}}}}}")
    lines.append("}}")
    lines.append("</includeonly>")
    return "\n".join(lines)


def import_mediawiki_templates(
    site: Optional[mwclient.Site] = None,
    dry_run: bool = False,
) -> dict:
    """Import static MediaWiki templates (infoboxes, tooltip, etc.) from wikitext files."""
    summary = {"created": 0, "updated": 0, "skipped": 0, "failed": 0, "errors": []}

    for title, filename in MEDIAWIKI_TEMPLATES.items():
        try:
            filepath = MEDIAWIKI_TEMPLATE_DIR / filename
            if not filepath.exists():
                raise FileNotFoundError(f"Template file not found: {filepath}")
            content = filepath.read_text(encoding="utf-8")

            if dry_run:
                logger.info("[DRY RUN] Would create/update MediaWiki template: %s (%d chars)", title, len(content))
                summary["created"] += 1
                continue

            if site is None:
                raise RuntimeError("site is required when dry_run=False")

            page = site.pages[title]
            if page.exists:
                existing = page.text()
                if _content_hash(existing.strip()) == _content_hash(content.strip()):
                    summary["skipped"] += 1
                    continue
                _edit_page(site, title, content, summary=f"Updated MediaWiki template: {title}")
                summary["updated"] += 1
            else:
                _edit_page(site, title, content, summary=f"Created MediaWiki template: {title}")
                summary["created"] += 1

        except (mwclient.errors.APIError, ConnectionError, RuntimeError, FileNotFoundError) as exc:
            logger.error("Failed to import MediaWiki template '%s': %s", title, exc)
            summary["failed"] += 1
            summary["errors"].append({"page": title, "error": str(exc)})

    logger.info(
        "MediaWiki template import: %d created, %d updated, %d skipped, %d failed",
        summary["created"], summary["updated"], summary["skipped"], summary["failed"],
    )
    return summary


def import_cargo_templates(
    site: Optional[mwclient.Site] = None,
    dry_run: bool = False,
) -> dict:
    summary = {"created": 0, "updated": 0, "skipped": 0, "failed": 0, "errors": []}

    for title, config in CARGO_TABLES.items():
        try:
            content = _build_cargo_template(config["table"], config["fields"])

            if dry_run:
                logger.info("[DRY RUN] Would create/update Cargo template: %s (%d chars)", title, len(content))
                summary["created"] += 1
                continue

            if site is None:
                raise RuntimeError("site is required when dry_run=False")

            page = site.pages[title]
            if page.exists:
                existing = page.text()
                if _content_hash(existing.strip()) == _content_hash(content.strip()):
                    summary["skipped"] += 1
                    continue
                _edit_page(site, title, content, summary=f"Updated Cargo table template: {title}")
                summary["updated"] += 1
            else:
                _edit_page(site, title, content, summary=f"Created Cargo table template: {title}")
                summary["created"] += 1

        except (mwclient.errors.APIError, ConnectionError, RuntimeError) as exc:
            logger.error("Failed to create Cargo template '%s': %s", title, exc)
            summary["failed"] += 1
            summary["errors"].append({"page": title, "error": str(exc)})

    logger.info(
        "Cargo template import: %d created, %d updated, %d skipped, %d failed",
        summary["created"], summary["updated"], summary["skipped"], summary["failed"],
    )
    return summary


POSITION_GROUP_ORDER = {
    "Goalkeeper": "GK",
    "Centre-Back": "DF", "Left-Back": "DF", "Right-Back": "DF", "Defender": "DF",
    "Central Midfield": "MF", "Defensive Midfield": "MF", "Attacking Midfield": "MF",
    "Left Midfield": "MF", "Right Midfield": "MF", "Midfielder": "MF",
    "Left Winger": "FW", "Right Winger": "FW", "Centre-Forward": "FW",
    "Second Striker": "FW", "Forward": "FW", "Striker": "FW",
}


def _group_players_by_position(players: list) -> dict:
    """Group players into position buckets: GK, DF, MF, FW, OTHER."""
    groups = {"GK": [], "DF": [], "MF": [], "FW": [], "OTHER": []}
    for p in players:
        pos = p.get("main_position", "") or ""
        group = POSITION_GROUP_ORDER.get(pos, "OTHER")
        groups[group].append(p)
    return groups


def import_squad_page(
    site: Optional[mwclient.Site] = None,
    season: str = "2024",
    players_path: Optional[Path] = None,
    stats_path: Optional[Path] = None,
    dry_run: bool = False,
) -> dict:
    resolved_players = players_path or DEFAULT_PLAYERS_PATH
    resolved_stats = stats_path or DEFAULT_STATS_PATH
    players = _load_jsonl(resolved_players)

    # Enrich players with season-specific stats
    if resolved_stats.exists():
        all_stats = _load_jsonl(resolved_stats)
        stats_by_player = {}
        for s in all_stats:
            if s.get("season") == season:
                stats_by_player[s["player_id"]] = s
        for p in players:
            p["stats"] = stats_by_player.get(p["id"])
    else:
        for p in players:
            p["stats"] = None

    players_by_position = _group_players_by_position(players)
    # Phase 3a R2: pass both `season` (bare integer join key, used in cargo
    # store calls within the template) and `season_display` (slash form, used
    # for the rendered h2 + category text). Internal join key stays bare.
    season_display = to_season_display(season)
    content = _render_template(
        "squad_table.j2",
        season=season,
        season_display=season_display,
        players=players,
        players_by_position=players_by_position,
    )
    title = f"סגל {season_display}"

    summary = {"created": 0, "updated": 0, "skipped": 0, "failed": 0, "errors": []}

    try:
        if dry_run:
            logger.info("[DRY RUN] Would create/update page: %s (%d chars)", title, len(content))
            summary["created"] += 1
            return summary

        if site is None:
            raise RuntimeError("site is required when dry_run=False")

        # Phase 3a R2: route through the gate before probing existence so the
        # report layer reflects Draft-namespace reality (see _import_single_page
        # comment).
        routed = review_gate.route_title(site, title)
        page = site.pages[routed]
        if page.exists:
            existing = page.text()
            if _content_hash(existing.strip()) == _content_hash(content.strip()):
                summary["skipped"] += 1
                return summary
            _edit_page(site, title, content, summary=f"Updated squad page for season {season}")
            summary["updated"] += 1
        else:
            _edit_page(site, title, content, summary=f"Created squad page for season {season}")
            summary["created"] += 1

    except (mwclient.errors.APIError, ConnectionError, RuntimeError) as exc:
        logger.error("Failed to create squad page: %s", exc)
        summary["failed"] += 1
        summary["errors"].append({"page": title, "error": str(exc)})

    return summary


def import_transfer_page(
    site: Optional[mwclient.Site] = None,
    season: str = "2024",
    players_path: Optional[Path] = None,
    transfers_path: Optional[Path] = None,
    dry_run: bool = False,
) -> dict:
    resolved_players = players_path or DEFAULT_PLAYERS_PATH
    resolved_transfers = transfers_path or DEFAULT_TRANSFERS_PATH

    players = _load_jsonl(resolved_players)
    transfers = _load_jsonl(resolved_transfers)

    name_map = {p["id"]: p.get("name_hebrew") or p["name_english"] for p in players}

    season_transfers = [t for t in transfers if t.get("season", "").startswith(season[:4])]

    incoming = []
    outgoing = []
    for t in season_transfers:
        t["player_name"] = name_map.get(t.get("player_id"), t.get("player_id", "Unknown"))
        to_club = (t.get("to_club") or "").lower()
        from_club = (t.get("from_club") or "").lower()

        if any(kw in to_club for kw in HBS_KEYWORDS):
            incoming.append(t)
        elif any(kw in from_club for kw in HBS_KEYWORDS):
            outgoing.append(t)

    # Phase 3a R2: pass both bare `season` (join key) and `season_display`
    # (slash form, rendered in the h2 + category).
    season_display = to_season_display(season)
    content = _render_template(
        "transfer_table.j2",
        season=season,
        season_display=season_display,
        incoming=incoming,
        outgoing=outgoing,
    )
    title = f"העברות {season_display}"

    summary = {"created": 0, "updated": 0, "skipped": 0, "failed": 0, "errors": []}

    try:
        if dry_run:
            logger.info("[DRY RUN] Would create/update page: %s (%d chars)", title, len(content))
            summary["created"] += 1
            return summary

        if site is None:
            raise RuntimeError("site is required when dry_run=False")

        # Phase 3a R2: route through the gate before probing existence.
        routed = review_gate.route_title(site, title)
        page = site.pages[routed]
        if page.exists:
            existing = page.text()
            if _content_hash(existing.strip()) == _content_hash(content.strip()):
                summary["skipped"] += 1
                return summary
            _edit_page(site, title, content, summary=f"Updated transfer page for season {season}")
            summary["updated"] += 1
        else:
            _edit_page(site, title, content, summary=f"Created transfer page for season {season}")
            summary["created"] += 1

    except (mwclient.errors.APIError, ConnectionError, RuntimeError) as exc:
        logger.error("Failed to create transfer page: %s", exc)
        summary["failed"] += 1
        summary["errors"].append({"page": title, "error": str(exc)})

    return summary


def _load_json(path: Path) -> list:
    """Load a JSON file, returning [] if not found or empty."""
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return []
    data = json.loads(text)
    return data if isinstance(data, list) else [data]


def _import_single_page(
    site, title: str, content: str, dry_run: bool, summary: dict
):
    """Helper to import a single wiki page with dry-run support.

    Phase 3a R2: routes the title through the review gate BEFORE checking
    `page.exists`. Without the routing, the existence probe hits mainspace
    (which never has the draft) and the counts always report "created"
    even when the existing Draft: page is unchanged. The downstream
    `_edit_page` does its own content-hash check + correctly skips the
    no-op write, so the wiki state stays right — but the report layer
    here would silently miscount everything as a create.
    """
    try:
        if dry_run:
            logger.info("[DRY RUN] Would create/update page: %s (%d chars)", title, len(content))
            summary["created"] += 1
            return

        if site is None:
            raise RuntimeError("site is required when dry_run=False")

        routed_title = review_gate.route_title(site, title)
        page = site.pages[routed_title]
        if page.exists:
            existing = page.text()
            if _content_hash(existing.strip()) == _content_hash(content.strip()):
                summary["skipped"] += 1
                return
            _edit_page(site, title, content, summary=f"Updated page: {title}")
            summary["updated"] += 1
        else:
            _edit_page(site, title, content, summary=f"Created page: {title}")
            summary["created"] += 1

    except (mwclient.errors.APIError, ConnectionError, RuntimeError) as exc:
        logger.error("Failed to import page '%s': %s", title, exc)
        summary["failed"] += 1
        summary["errors"].append({"page": title, "error": str(exc)})


def import_coaches_page(
    site=None,
    coaches_path: Optional[Path] = None,
    dry_run: bool = False,
) -> dict:
    """Import the Manager History page from coaches.json."""
    summary = {"created": 0, "updated": 0, "skipped": 0, "failed": 0, "errors": []}

    path = coaches_path or DEFAULT_SCRAPER_OUTPUT_DIR / "coaches.json"
    coaches = _load_json(path)
    if not coaches:
        logger.warning("No coach data found at %s", path)
        return summary

    content = _render_template("coach_page.j2", coaches=coaches)
    _import_single_page(site, "היסטוריית מאמנים", content, dry_run, summary)

    logger.info(
        "Coach import: %d created, %d updated, %d skipped, %d failed",
        summary["created"], summary["updated"], summary["skipped"], summary["failed"],
    )
    return summary


def import_honours_page(
    site=None,
    honours_path: Optional[Path] = None,
    dry_run: bool = False,
) -> dict:
    """Import the Honours page from honours.json."""
    summary = {"created": 0, "updated": 0, "skipped": 0, "failed": 0, "errors": []}

    path = honours_path or DEFAULT_SCRAPER_OUTPUT_DIR / "honours.json"
    honours = _load_json(path)
    if not honours:
        logger.warning("No honours data found at %s", path)
        return summary

    content = _render_template("honours_page.j2", honours=honours)
    _import_single_page(site, "תארים", content, dry_run, summary)

    logger.info(
        "Honours import: %d created, %d updated, %d skipped, %d failed",
        summary["created"], summary["updated"], summary["skipped"], summary["failed"],
    )
    return summary


def import_stadium_page(
    site=None,
    stadium_path: Optional[Path] = None,
    dry_run: bool = False,
) -> dict:
    """Import the Stadium page from stadium.json."""
    summary = {"created": 0, "updated": 0, "skipped": 0, "failed": 0, "errors": []}

    path = stadium_path or DEFAULT_SCRAPER_OUTPUT_DIR / "stadium.json"
    stadium_data = _load_json(path)
    if not stadium_data:
        logger.warning("No stadium data found at %s", path)
        return summary

    stadium = stadium_data[0] if isinstance(stadium_data, list) else stadium_data
    title = stadium.get("name", "Stadium")
    content = _render_template("stadium_page.j2", stadium=stadium)
    _import_single_page(site, title, content, dry_run, summary)

    logger.info(
        "Stadium import: %d created, %d updated, %d skipped, %d failed",
        summary["created"], summary["updated"], summary["skipped"], summary["failed"],
    )
    return summary


def import_records_page(
    site=None,
    records_path: Optional[Path] = None,
    dry_run: bool = False,
) -> dict:
    """Import the Club Records page from records.json."""
    summary = {"created": 0, "updated": 0, "skipped": 0, "failed": 0, "errors": []}

    path = records_path or DEFAULT_SCRAPER_OUTPUT_DIR / "records.json"
    records = _load_json(path)
    if not records:
        logger.warning("No records data found at %s", path)
        return summary

    # Group by category
    records_by_category = {}
    for r in records:
        cat = r.get("category", "Other")
        records_by_category.setdefault(cat, []).append(r)

    content = _render_template("records_page.j2", records_by_category=records_by_category)
    _import_single_page(site, "שיאי המועדון", content, dry_run, summary)

    logger.info(
        "Records import: %d created, %d updated, %d skipped, %d failed",
        summary["created"], summary["updated"], summary["skipped"], summary["failed"],
    )
    return summary


def import_season_overview(
    site=None,
    season: str = "2024",
    players_path: Optional[Path] = None,
    stats_path: Optional[Path] = None,
    fixtures_path: Optional[Path] = None,
    standings_path: Optional[Path] = None,
    dry_run: bool = False,
) -> dict:
    """Import a Season Overview page aggregating squad, stats, fixtures, and
    standings data.

    Phase 3a R2: always emits a page even when no TM data exists for the
    season — sparse historical seasons get a placeholder banner; partial
    seasons get a "what's missing" footer. The wiki ends up with a complete
    chronological index from 1949/50 onwards, with hand-curation prompts on
    the empty / partial pages.
    """
    summary = {"created": 0, "updated": 0, "skipped": 0, "failed": 0, "errors": []}

    resolved_players = players_path or DEFAULT_PLAYERS_PATH
    resolved_stats = stats_path or DEFAULT_STATS_PATH

    # Load players for name mapping
    try:
        players = _load_jsonl(resolved_players)
    except FileNotFoundError:
        players = []
    name_map = {p["id"]: p.get("name_hebrew") or p["name_english"] for p in players}

    # Load and filter stats for this season
    try:
        all_stats = _load_jsonl(resolved_stats)
    except FileNotFoundError:
        all_stats = []
    season_stats = [s for s in all_stats if s.get("season") == season]

    # Compute aggregates
    total_appearances = sum(s.get("appearances", 0) for s in season_stats)
    total_goals = sum(s.get("goals", 0) for s in season_stats)
    total_assists = sum(s.get("assists", 0) for s in season_stats)
    total_yellows = sum(s.get("yellow_cards", 0) for s in season_stats)
    total_reds = sum(s.get("red_cards", 0) for s in season_stats)

    # Top scorers and appearances
    for s in season_stats:
        s["player_name"] = name_map.get(s.get("player_id"), s.get("player_id", "Unknown"))
    top_scorers = sorted(season_stats, key=lambda s: s.get("goals", 0), reverse=True)
    top_appearances = sorted(season_stats, key=lambda s: s.get("appearances", 0), reverse=True)
    top_assists = sorted(season_stats, key=lambda s: s.get("assists", 0), reverse=True)

    # Load fixtures if available
    resolved_fixtures = fixtures_path or DEFAULT_SCRAPER_OUTPUT_DIR / season / "fixtures.json"
    fixtures = _load_json(resolved_fixtures) if resolved_fixtures.exists() else []
    fixtures_by_competition = {}
    for f in fixtures:
        comp = f.get("competition", "Unknown")
        fixtures_by_competition.setdefault(comp, []).append(f)

    # Phase 3a R2: load the per-season standings row (from platzierungen
    # spider). When present, gives us "Finished Nth, X points, M-W-D-L, GF:GA".
    resolved_standings = standings_path or DEFAULT_SCRAPER_OUTPUT_DIR / "season_standings.json"
    standings_all = _load_json(resolved_standings) if resolved_standings.exists() else []
    standings = next(
        (row for row in standings_all if row.get("season") == season),
        None,
    )

    # Phase 3a R2: derive presence flags + missing-data notes for the
    # template's graceful-degradation footer.
    season_dir = DEFAULT_SCRAPER_OUTPUT_DIR / season
    has_squad = (season_dir / "squad.json").exists() and _load_json(season_dir / "squad.json")
    has_transfers = (season_dir / "transfers.json").exists() and _load_json(season_dir / "transfers.json")

    missing_notes = []
    if not standings:
        missing_notes.append(
            "Transfermarkt לא מספק מידע על מיקום בליגה לעונה זו "
            "(`platzierungen` מתחיל בשנת 1986/87)."
        )
    if not season_stats:
        missing_notes.append(
            "Transfermarkt לא מספק סטטיסטיקות שחקנים לעונה זו "
            "(`leistungsdaten` מתחיל בעיקר משנת 1985/86)."
        )
    if not has_squad:
        missing_notes.append("Transfermarkt לא מספק רשימת סגל לעונה זו.")
    if not fixtures:
        missing_notes.append(
            "Transfermarkt לא מספק לוח משחקים לעונה זו "
            "(לוחות משחקים זמינים בעיקר משנת 1985/86)."
        )

    season_display = to_season_display(season)

    content = _render_template(
        "season_overview.j2",
        season=season,
        season_display=season_display,
        stats=season_stats,
        total_appearances=total_appearances,
        total_goals=total_goals,
        total_assists=total_assists,
        total_yellows=total_yellows,
        total_reds=total_reds,
        top_scorers=top_scorers,
        top_appearances=top_appearances,
        top_assists=top_assists,
        fixtures_by_competition=fixtures_by_competition,
        # Phase 3a R2 additions:
        standings=standings,
        has_squad_page=bool(has_squad),
        has_transfers_page=bool(has_transfers),
        missing_notes=missing_notes,
    )
    title = f"עונת {season_display}"
    _import_single_page(site, title, content, dry_run, summary)

    logger.info(
        "Season overview import for %s: %d created, %d updated, %d skipped, %d failed",
        season, summary["created"], summary["updated"], summary["skipped"], summary["failed"],
    )
    return summary


def import_leaderboards(
    site=None,
    stats_path: Optional[Path] = None,
    players_path: Optional[Path] = None,
    dry_run: bool = False,
) -> dict:
    """Import all-time leaderboard pages (top scorers, most appearances, assist leaders)."""
    summary = {"created": 0, "updated": 0, "skipped": 0, "failed": 0, "errors": []}

    resolved_stats = stats_path or DEFAULT_STATS_PATH
    resolved_players = players_path or DEFAULT_PLAYERS_PATH

    try:
        all_stats = _load_jsonl(resolved_stats)
    except FileNotFoundError:
        logger.warning("No stats data for leaderboards")
        return summary

    try:
        players = _load_jsonl(resolved_players)
    except FileNotFoundError:
        players = []
    name_map = {p["id"]: p.get("name_hebrew") or p["name_english"] for p in players}

    # Aggregate per player across all seasons
    player_totals = {}
    for s in all_stats:
        pid = s.get("player_id", "")
        if pid not in player_totals:
            player_totals[pid] = {
                "player_name": name_map.get(pid, pid),
                "appearances": 0,
                "goals": 0,
                "assists": 0,
            }
        player_totals[pid]["appearances"] += s.get("appearances", 0)
        player_totals[pid]["goals"] += s.get("goals", 0)
        player_totals[pid]["assists"] += s.get("assists", 0)

    all_players = list(player_totals.values())

    leaderboards = [
        ("מלכי השערים של כל הזמנים", "שערים", "goals", sorted(all_players, key=lambda p: p["goals"], reverse=True)),
        ("בעלי ההופעות של כל הזמנים", "הופעות", "appearances", sorted(all_players, key=lambda p: p["appearances"], reverse=True)),
        ("מלכי הבישולים של כל הזמנים", "בישולים", "assists", sorted(all_players, key=lambda p: p["assists"], reverse=True)),
    ]

    for title, value_label, key, sorted_list in leaderboards:
        # Only include players with > 0 of the stat
        entries = [{"player_name": p["player_name"], "value": p[key]} for p in sorted_list if p[key] > 0][:50]
        if not entries:
            continue

        content = _render_template(
            "leaderboard.j2",
            title=title,
            value_label=value_label,
            entries=entries,
            scope="all_time",
        )
        _import_single_page(site, title, content, dry_run, summary)

    logger.info(
        "Leaderboard import: %d created, %d updated, %d skipped, %d failed",
        summary["created"], summary["updated"], summary["skipped"], summary["failed"],
    )
    return summary


def import_attendance(
    site=None,
    seasons: list = None,
    dry_run: bool = False,
) -> dict:
    """Import attendance statistics page from fixtures data across seasons."""
    import re

    summary = {"created": 0, "updated": 0, "skipped": 0, "failed": 0, "errors": []}

    if not seasons:
        logger.warning("No seasons provided for attendance import")
        return summary

    season_stats = []
    for season in seasons:
        fixtures_path = DEFAULT_SCRAPER_OUTPUT_DIR / season / "fixtures.json"
        fixtures = _load_json(fixtures_path)
        if not fixtures:
            continue

        attendances = []
        for f in fixtures:
            raw = f.get("attendance", "")
            if raw:
                cleaned = re.sub(r"[^\d]", "", str(raw))
                if cleaned:
                    attendances.append(int(cleaned))

        if attendances:
            season_stats.append({
                "season": to_season_display(season),
                "total_matches": len(attendances),
                "total_attendance": sum(attendances),
                "average": sum(attendances) // len(attendances),
                "highest": max(attendances),
                "lowest": min(attendances),
            })

    if not season_stats:
        logger.warning("No attendance data found")
        return summary

    content = _render_template("attendance.j2", season_stats=season_stats)
    _import_single_page(site, "סטטיסטיקות קהל", content, dry_run, summary)

    logger.info(
        "Attendance import: %d created, %d updated, %d skipped, %d failed",
        summary["created"], summary["updated"], summary["skipped"], summary["failed"],
    )
    return summary


# Phase 3a R2: Derbies page driven by the bilanz spider's head_to_head.json.
# Four major Israeli football rivalries. TM's "B. Jerusalem" / "M. Tel Aviv"
# short forms appear in the data; we accept both the short and the canonical
# long form as aliases when looking up each derby's row.
_MAJOR_DERBIES = [
    {
        "display_name": "מכבי תל אביב",
        "aliases": ["Maccabi Tel Aviv", "M. Tel Aviv"],
    },
    {
        "display_name": "הפועל תל אביב",
        "aliases": ["Hapoel Tel Aviv", "H. Tel Aviv"],
    },
    {
        "display_name": "בית\"ר ירושלים",
        "aliases": ["Beitar Jerusalem", "B. Jerusalem"],
    },
    {
        "display_name": "מכבי חיפה",
        "aliases": ["Maccabi Haifa", "M. Haifa"],
    },
]


def import_derbies_page(
    site=None,
    head_to_head_path: Optional[Path] = None,
    dry_run: bool = False,
) -> dict:
    """Import the Derbies page driven by `head_to_head.json` (bilanz spider).

    Phase 3a R2: high-value derived page. Walks the 4 canonical Israeli
    football rivalries, plus a "top opponents" tail of the highest-match-
    count rows. The bilanz row may be absent if TM data for that opponent
    is empty — the template handles that with a fallback note.
    """
    summary = {"created": 0, "updated": 0, "skipped": 0, "failed": 0, "errors": []}

    path = head_to_head_path or DEFAULT_SCRAPER_OUTPUT_DIR / "head_to_head.json"
    rows = _load_json(path)
    if not rows:
        logger.warning("No head_to_head data found at %s; skipping derbies page", path)
        return summary

    # Index by opponent name for derby lookups.
    rows_by_name = {r["opponent"]: r for r in rows}

    major_derbies = []
    for derby in _MAJOR_DERBIES:
        row = next(
            (rows_by_name[alias] for alias in derby["aliases"] if alias in rows_by_name),
            None,
        )
        major_derbies.append({**derby, "row": row})

    # Tail: the other opponents sorted by match count descending. Exclude any
    # alias of the major derbies so the bottom table doesn't duplicate them.
    derby_aliases = {a for derby in _MAJOR_DERBIES for a in derby["aliases"]}
    other_opponents = sorted(
        (r for r in rows if r["opponent"] not in derby_aliases),
        key=lambda r: r.get("played", 0),
        reverse=True,
    )

    content = _render_template(
        "derbies.j2",
        major_derbies=major_derbies,
        other_opponents=other_opponents,
    )
    _import_single_page(site, "דרבים", content, dry_run, summary)

    logger.info(
        "Derbies page import: %d created, %d updated, %d skipped, %d failed",
        summary["created"], summary["updated"], summary["skipped"], summary["failed"],
    )
    return summary


# Phase 3a R2: European-campaign page derives from fixtures.json across all
# seasons. These competition names match what TM serves in English. New
# competitions joined here as TM gives them; the page falls back gracefully
# when nothing matches.
_EUROPEAN_COMPETITIONS = {
    "UEFA Champions League",
    "Champions League",
    "Champions League qualifying",
    "Champions League Qualifying",
    "UEFA Champions League Qualifying",
    "UEFA Europa League",
    "Europa League",
    "Europa League qualifying",
    "Europa League Qualifying",
    "UEFA Europa League Qualifying",
    "UEFA Conference League",
    "Conference League",
    "UEFA Conference League Qualifying",
    "Conference League qualifying",
    "Conference League Qualifying",
    "UEFA Europa Conference League",
    "UEFA Cup",
    "Cup Winners' Cup",
    "Intertoto Cup",
}


def import_european_campaign_page(
    site=None,
    seasons: list = None,
    dry_run: bool = False,
) -> dict:
    """Import the European campaign history page derived from fixtures.

    Phase 3a R2: walks every season's fixtures.json, filters to European
    competitions, groups by (season, competition). Renders one row per
    (season, competition) on a sortable table + a sub-summary.
    """
    summary = {"created": 0, "updated": 0, "skipped": 0, "failed": 0, "errors": []}

    if not seasons:
        return summary

    campaigns = []  # list of {season_display, competition, match_count}
    competition_counts: dict[str, int] = {}

    for season in sorted(seasons):
        fixtures_path = DEFAULT_SCRAPER_OUTPUT_DIR / season / "fixtures.json"
        if not fixtures_path.exists():
            continue
        fixtures = _load_json(fixtures_path)
        # Group by competition name within this season.
        per_comp: dict[str, list] = {}
        for f in fixtures:
            comp = (f.get("competition") or "").strip()
            if comp in _EUROPEAN_COMPETITIONS:
                per_comp.setdefault(comp, []).append(f)
        for comp, comp_fixtures in per_comp.items():
            campaigns.append({
                "season": season,
                "season_display": to_season_display(season),
                "competition": comp,
                "match_count": len(comp_fixtures),
            })
            competition_counts[comp] = competition_counts.get(comp, 0) + len(comp_fixtures)

    total_seasons = len({c["season"] for c in campaigns})
    total_matches = sum(c["match_count"] for c in campaigns)
    competition_breakdown = dict(
        sorted(competition_counts.items(), key=lambda kv: kv[1], reverse=True)
    )

    content = _render_template(
        "european_campaign.j2",
        campaigns=campaigns,
        total_seasons=total_seasons,
        total_matches=total_matches,
        competition_breakdown=competition_breakdown,
    )
    _import_single_page(site, "היסטוריית קמפיינים אירופיים", content, dry_run, summary)

    logger.info(
        "European campaign page import: %d created, %d updated, %d skipped, %d failed",
        summary["created"], summary["updated"], summary["skipped"], summary["failed"],
    )
    return summary


def import_competition_pages(
    site=None,
    seasons: list = None,
    dry_run: bool = False,
) -> dict:
    """Import per-competition per-season pages from fixtures data."""
    summary = {"created": 0, "updated": 0, "skipped": 0, "failed": 0, "errors": []}

    if not seasons:
        return summary

    for season in seasons:
        fixtures_path = DEFAULT_SCRAPER_OUTPUT_DIR / season / "fixtures.json"
        fixtures = _load_json(fixtures_path)
        if not fixtures:
            continue

        # Group by competition
        by_competition = {}
        for f in fixtures:
            comp = f.get("competition", "Unknown")
            by_competition.setdefault(comp, []).append(f)

        season_display = to_season_display(season)

        for comp, comp_fixtures in by_competition.items():
            if not comp or comp == "Unknown":
                continue
            content = _render_template(
                "competition_season.j2",
                competition=comp,
                season=season,
                season_display=season_display,
                fixtures=comp_fixtures,
            )
            title = f"{comp} {season_display}"
            _import_single_page(site, title, content, dry_run, summary)

    logger.info(
        "Competition pages import: %d created, %d updated, %d skipped, %d failed",
        summary["created"], summary["updated"], summary["skipped"], summary["failed"],
    )
    return summary
