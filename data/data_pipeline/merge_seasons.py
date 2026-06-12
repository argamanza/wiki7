"""Merge normalized data from multiple seasons into a single dataset.

Deduplicates players by tmk_id (latest season wins for mutable fields)
and concatenates transfers, market values, and stats across all seasons.
"""

import json
import logging
from pathlib import Path
from typing import List

logger = logging.getLogger(__name__)


def load_jsonl(path: Path) -> list:
    """Load newline-delimited JSON file."""
    if not path.exists():
        return []
    with open(path, "r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def write_jsonl(data: list, path: Path):
    """Write list of dicts to a JSONL file."""
    with open(path, "w", encoding="utf-8") as f:
        for item in data:
            f.write(json.dumps(item, ensure_ascii=False, default=str) + "\n")


def merge_players(season_dirs: List[Path]) -> list:
    """Merge player records across seasons. Latest season wins for mutable fields.

    §6 high #8 fix (2026-06-12 review): also accumulates `seasons_active`
    — a sorted list of bare-integer season strings the player appeared in
    that season's squad. Used downstream by `import_squad_page` to render
    the correct squad per season. Without this list the per-season squad
    page rendered the entire merged all-time roster (no season filter).
    """
    players_by_id = {}

    for season_dir in sorted(season_dirs):  # sorted = chronological order
        season = season_dir.name  # bare integer e.g. "2024"
        players = load_jsonl(season_dir / "players.jsonl")
        for player in players:
            pid = player["id"]
            if pid in players_by_id:
                existing = players_by_id[pid]
                # Update mutable fields from more recent season
                existing["current_squad"] = player["current_squad"]
                existing["current_jersey_number"] = player["current_jersey_number"]
                # Profile data: prefer non-None values from latest
                for field in ("birth_date", "birth_place", "nationality", "main_position", "name_hebrew"):
                    if player.get(field):
                        existing[field] = player[field]
                # Homegrown/retired: True if ever true
                existing["homegrown"] = existing["homegrown"] or player["homegrown"]
                existing["retired"] = existing["retired"] or player["retired"]
                # Track every season this player appears in.
                if season not in existing["seasons_active"]:
                    existing["seasons_active"].append(season)
            else:
                player["seasons_active"] = [season]
                players_by_id[pid] = player

    logger.info("Merged %d unique players from %d seasons", len(players_by_id), len(season_dirs))
    return list(players_by_id.values())


def merge_appendable(season_dirs: List[Path], filename: str) -> list:
    """Concatenate records from all seasons with exact-record deduplication."""
    all_records = []
    seen = set()

    for season_dir in sorted(season_dirs):
        records = load_jsonl(season_dir / filename)
        for record in records:
            key = json.dumps(record, sort_keys=True, default=str)
            if key not in seen:
                seen.add(key)
                all_records.append(record)

    logger.info("Merged %d records from '%s' across %d seasons", len(all_records), filename, len(season_dirs))
    return all_records


def merge_seasons(base_dir: Path, seasons: List[str], output_dir: Path):
    """Merge all per-season normalized data into a single merged dataset."""
    output_dir.mkdir(parents=True, exist_ok=True)

    season_dirs = [base_dir / s for s in seasons if (base_dir / s).exists()]
    if not season_dirs:
        raise FileNotFoundError(f"No season directories found in {base_dir} for seasons: {seasons}")

    found_seasons = [d.name for d in season_dirs]
    missing = set(seasons) - set(found_seasons)
    if missing:
        logger.warning("Missing season directories: %s", sorted(missing))
    logger.info("Merging data from %d seasons: %s", len(season_dirs), found_seasons)

    # Merge players (dedup by tmk_id)
    merged_players = merge_players(season_dirs)
    write_jsonl(merged_players, output_dir / "players.jsonl")

    # Concatenate transfers, market values, stats
    for filename in ("transfers.jsonl", "market_values.jsonl", "stats.jsonl"):
        merged = merge_appendable(season_dirs, filename)
        write_jsonl(merged, output_dir / filename)

    logger.info("Merge complete. Output written to %s", output_dir)
