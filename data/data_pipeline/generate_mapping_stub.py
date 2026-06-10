"""Generate a Hebrew mapping stub YAML from normalized player/transfer/fixtures data.

Creates a YAML file with all unique positions, clubs, nationalities, competitions,
and player names that need Hebrew translation. Existing translations are preserved.

Usage:
    python -m data_pipeline.generate_mapping_stub [--players-path PATH] [--transfers-path PATH] [--mapping-path PATH]
"""

import json
import logging
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

FOOTBALL_POSITIONS = {
    "Attacking Midfield": "קשר התקפי",
    "Central Midfield": "קשר",
    "Centre-Back": "בלם",
    "Centre-Forward": "חלוץ",
    "Defensive Midfield": "קשר הגנתי",
    "Goalkeeper": "שוער",
    "Left Winger": "קשר שמאלי",
    "Left-Back": "מגן שמאלי",
    "Right Midfield": "קשר ימני",
    "Right Winger": "קשר ימני",
    "Right-Back": "מגן ימני",
    "Second Striker": "חלוץ שני",
}

DEFAULT_PLAYERS_PATH = Path(__file__).resolve().parent / "output" / "merged" / "players.jsonl"
DEFAULT_TRANSFERS_PATH = Path(__file__).resolve().parent / "output" / "merged" / "transfers.jsonl"
DEFAULT_MAPPING_PATH = Path(__file__).resolve().parent / "output" / "merged" / "mappings.he.yaml"
DEFAULT_SCRAPER_OUTPUT_DIR = Path(__file__).resolve().parent.parent / "tmk-scraper" / "output"


def load_jsonl(path):
    with open(path, "r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def load_existing_mapping(mapping_path):
    if mapping_path.exists():
        with open(mapping_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


def _load_json(path: Path) -> list:
    """Load a JSON file, returning [] if not found or empty."""
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return []
    data = json.loads(text)
    return data if isinstance(data, list) else [data]


def _collect_match_data(scraper_output_dir: Path) -> tuple[set, set]:
    """Collect unique opponent club names and player names from match data.
    Returns (opponent_clubs, player_names).
    """
    opponents = set()
    names = set()
    if not scraper_output_dir.exists():
        return opponents, names
    for season_dir in sorted(scraper_output_dir.iterdir()):
        if not season_dir.is_dir():
            continue
        matches_path = season_dir / "matches.json"
        for match in _load_json(matches_path):
            opp = match.get("opponent", "")
            if opp:
                opponents.add(opp)
            for goal in match.get("goals", []):
                if goal.get("scorer"):
                    names.add(goal["scorer"])
                if goal.get("assist"):
                    names.add(goal["assist"])
            for sub in match.get("substitutions", []):
                if sub.get("player_in"):
                    names.add(sub["player_in"])
                if sub.get("player_out"):
                    names.add(sub["player_out"])
            for card in match.get("cards", []):
                if card.get("player"):
                    names.add(card["player"])
            for side in ("home_lineup", "away_lineup"):
                lineup = match.get(side)
                if not lineup:
                    continue
                if isinstance(lineup, list):
                    # Graphic formation lineup — list of player dicts. Read
                    # `name_english` (slug-derived full name from the post
                    # iter-cycle spider) rather than `name_short` (which is
                    # the surname-only visible text and pollutes the names
                    # corpus with single-token duplicates).
                    for p in lineup:
                        name = p.get("name_hebrew") or p.get("name_english") or p.get("name", "")
                        if name:
                            names.add(name)
                elif isinstance(lineup, dict):
                    # Simple-table lineup — historical matches. Each position
                    # value is a list of player dicts (post iter-cycle) OR a
                    # bare string (legacy data). Manager value is similarly
                    # either a dict or a bare string. Tolerate both shapes
                    # so old cached output doesn't need a wholesale re-scrape.
                    for _pos, players in lineup.items():
                        if isinstance(players, str):
                            names.add(players)
                        elif isinstance(players, dict):
                            # manager slot, post iter-cycle
                            n = players.get("name_english") or players.get("name_short")
                            if n:
                                names.add(n)
                        elif isinstance(players, list):
                            for pl in players:
                                if isinstance(pl, str):
                                    names.add(pl)
                                elif isinstance(pl, dict):
                                    n = pl.get("name_english") or pl.get("name_short")
                                    if n:
                                        names.add(n)
            for pen in match.get("penalties", []):
                if pen.get("player"):
                    names.add(pen["player"])
    return opponents, names


def _collect_competitions(scraper_output_dir: Path) -> set:
    """Collect unique competition names from all season fixtures files."""
    competitions = set()
    if not scraper_output_dir.exists():
        return competitions
    for season_dir in sorted(scraper_output_dir.iterdir()):
        if not season_dir.is_dir():
            continue
        fixtures_path = season_dir / "fixtures.json"
        for f in _load_json(fixtures_path):
            comp = f.get("competition", "")
            if comp and comp != "Unknown":
                competitions.add(comp)
    return competitions


def generate_stub(
    players_path: Path | None = None,
    transfers_path: Path | None = None,
    mapping_path: Path | None = None,
    scraper_output_dir: Path | None = None,
):
    """Generate or update the Hebrew mapping stub YAML."""
    resolved_players = players_path or DEFAULT_PLAYERS_PATH
    resolved_transfers = transfers_path or DEFAULT_TRANSFERS_PATH
    resolved_mapping = mapping_path or DEFAULT_MAPPING_PATH
    resolved_scraper = scraper_output_dir or DEFAULT_SCRAPER_OUTPUT_DIR

    players = load_jsonl(resolved_players)
    transfers = load_jsonl(resolved_transfers)
    existing = load_existing_mapping(resolved_mapping)

    # Extract unique values
    unique_positions = sorted({p["main_position"] for p in players if p.get("main_position")})
    unique_clubs = sorted(
        {t["from_club"] for t in transfers if t.get("from_club")}
        | {t["to_club"] for t in transfers if t.get("to_club")}
    )
    unique_nationalities = sorted({
        nat for p in players for nat in p.get("nationality", []) or []
    })
    missing_name_he = sorted({
        p["name_english"] for p in players if not p.get("name_hebrew")
    })
    unique_competitions = sorted(_collect_competitions(resolved_scraper))

    match_opponents, match_player_names = _collect_match_data(resolved_scraper)

    known_player_names = {p["name_english"] for p in players} | {
        p["name_hebrew"] for p in players if p.get("name_hebrew")
    }

    # Use existing or fallback to empty dict
    position_map = existing.get("positions", {})
    club_map = existing.get("clubs", {})
    nationality_map = existing.get("nationalities", {})
    names_map = existing.get("names", {})
    competition_map = existing.get("competitions", {})

    # Update only missing keys
    for pos in unique_positions:
        position_map.setdefault(pos, FOOTBALL_POSITIONS.get(pos, ""))
    for club in sorted(set(unique_clubs) | match_opponents):
        club_map.setdefault(club, "")
    for nat in unique_nationalities:
        nationality_map.setdefault(nat, "")
    for name in missing_name_he:
        names_map.setdefault(name, "")
    for name in sorted(match_player_names - known_player_names):
        names_map.setdefault(name, "")
    for comp in unique_competitions:
        competition_map.setdefault(comp, "")

    # Combine and write
    updated = {
        "positions": position_map,
        "clubs": club_map,
        "nationalities": nationality_map,
        "competitions": competition_map,
        "names": names_map,
    }

    resolved_mapping.parent.mkdir(parents=True, exist_ok=True)
    with open(resolved_mapping, "w", encoding="utf-8") as f:
        yaml.dump(updated, f, allow_unicode=True, sort_keys=False)

    logger.info("Updated mapping stub saved to %s", resolved_mapping)
    logger.info(
        "  %d positions, %d clubs, %d nationalities, %d competitions, %d names",
        len(position_map), len(club_map), len(nationality_map),
        len(competition_map), len(names_map),
    )


def main():
    """CLI entry point."""
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    parser = argparse.ArgumentParser(description="Generate Hebrew mapping stub YAML")
    parser.add_argument("--players-path", type=Path, default=None)
    parser.add_argument("--transfers-path", type=Path, default=None)
    parser.add_argument("--mapping-path", type=Path, default=None)
    args = parser.parse_args()

    generate_stub(
        players_path=args.players_path,
        transfers_path=args.transfers_path,
        mapping_path=args.mapping_path,
    )


if __name__ == "__main__":
    main()
