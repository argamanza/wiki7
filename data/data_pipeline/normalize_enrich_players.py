import json
from pathlib import Path
from data_pipeline.schemas import Player, MarketValue, Transfer, PlayerSeasonStats
from data_pipeline.helpers import is_all_hebrew, parse_birth_date, parse_countries, is_homegrown, is_retired
from tqdm import tqdm
from typing import List


DEFAULT_RAW_PATH = Path("../tmk-scraper/output/players.json")
DEFAULT_STATS_PATH = Path("../tmk-scraper/output/stats.json")
DEFAULT_OUT_DIR = Path("output")


def load_raw_players(raw_path=None):
    path = raw_path or DEFAULT_RAW_PATH
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def normalize_player(player) -> Player:
    facts = player.get("facts", {})

    name_hebrew = None  # Default in case the condition fails

    if is_all_hebrew(facts.get("Name in home country", "")):
        name_hebrew = facts.get("Name in home country")

    # Phase 3a R2: derived fields from the existing facts dict + market_value_history.
    # All nullable so historical players whose profiles omit them keep validating.
    preferred_foot = _parse_preferred_foot(facts.get("Foot"))
    height_cm = _parse_height_cm(facts.get("Height"))
    contract_expires = facts.get("Contract expires") or None
    current_market_value = _latest_market_value(player.get("market_value_history", []))
    other_positions = [p for p in player.get("positions", {}).get("other", []) if p]

    return Player(
        id=player["profile_url"].split("/")[-1],
        name_english=player["name_english"],
        name_hebrew=name_hebrew,
        birth_date=parse_birth_date(facts.get("Date of birth/Age", "").split(" (")[0]),
        birth_place=facts.get("Place of birth"),
        nationality=parse_countries(facts.get("Citizenship")),
        main_position=player.get("positions", {}).get("main"),
        current_squad=not player.get("loaned", False),
        current_jersey_number=None if player["number"] == "-" else int(player["number"]),
        homegrown=is_homegrown(player),
        retired=is_retired(player),
        preferred_foot=preferred_foot,
        height_cm=height_cm,
        contract_expires=contract_expires,
        # is_captain comes from the squad-page captain icon — wired through the
        # squad spider when present, otherwise default False.
        is_captain=bool(player.get("is_captain", False)),
        current_market_value=current_market_value,
        other_positions=other_positions,
    )


def _parse_preferred_foot(raw: str | None) -> str | None:
    """TM's `Foot` fact is one of {"right", "left", "both"} (lowercased). The
    label can also be empty when TM doesn't know; return None in that case.
    """
    if not raw:
        return None
    val = raw.strip().lower()
    if val not in {"right", "left", "both"}:
        return None
    return val


def _parse_height_cm(raw: str | None) -> int | None:
    """TM renders height as "1,78 m" or "1.78 m" depending on locale. Convert to
    an int in centimetres; return None when the value is missing or malformed.
    """
    if not raw:
        return None
    cleaned = raw.replace(",", ".").replace("\xa0", " ").strip()
    # Strip the unit; tolerate "1.78 m" or "1.78m".
    for suffix in (" m", "m"):
        if cleaned.endswith(suffix):
            cleaned = cleaned[: -len(suffix)].strip()
            break
    try:
        metres = float(cleaned)
    except ValueError:
        return None
    return int(round(metres * 100))


def _latest_market_value(history: list) -> str | None:
    """Return the most recent market-value string from the player's history, or
    None when the history is empty (pre-2003 players or otherwise absent).
    History entries are dicts with "date" and "value" keys, sorted ascending by
    date in the spider.
    """
    if not history:
        return None
    return history[-1].get("value") or None

def normalize_transfers(player) -> List[Transfer]:
    uid = player["profile_url"].split("/")[-1]
    return [
        Transfer(
            player_id=uid,
            season=tr["season"],
            transfer_date=tr["date"],
            from_club=tr["from"],
            to_club=tr["to"],
            fee=tr["fee"],
            loan=("loan" in tr["fee"].lower())
        )
        for tr in player.get("transfers", [])
    ]

def normalize_market_values(player) -> List[MarketValue]:
    uid = player["profile_url"].split("/")[-1]
    return [
        MarketValue(
            player_id=uid,
            value_date=mv["date"],
            value=mv["value"],
            team=mv["team"]
        )
        for mv in player.get("market_value_history", [])
    ]

def normalize_stats(stats_data: list) -> List[PlayerSeasonStats]:
    """Normalize raw stats spider output into PlayerSeasonStats objects."""
    return [
        PlayerSeasonStats(
            player_id=s["player_id"],
            season=s["season"],
            appearances=s.get("appearances", 0),
            goals=s.get("goals", 0),
            assists=s.get("assists", 0),
            yellow_cards=s.get("yellow_cards", 0),
            second_yellow_cards=s.get("second_yellow_cards", 0),
            red_cards=s.get("red_cards", 0),
            minutes_played=s.get("minutes_played", 0),
        )
        for s in stats_data
    ]


def write_jsonl(data, path):
    with open(path, "w", encoding="utf-8") as f:
        for item in data:
            f.write(item.model_dump_json() + "\n")


def main(raw_path=None, stats_path=None, out_dir=None):
    resolved_raw = raw_path or DEFAULT_RAW_PATH
    resolved_stats = stats_path or DEFAULT_STATS_PATH
    resolved_out = Path(out_dir) if out_dir else DEFAULT_OUT_DIR
    resolved_out.mkdir(parents=True, exist_ok=True)

    raw_players = load_raw_players(resolved_raw)

    all_players = []
    all_transfers = []
    all_values = []

    for p in tqdm(raw_players):
        all_players.append(normalize_player(p))
        all_transfers.extend(normalize_transfers(p))
        all_values.extend(normalize_market_values(p))

    write_jsonl(all_players, resolved_out / "players.jsonl")
    write_jsonl(all_transfers, resolved_out / "transfers.jsonl")
    write_jsonl(all_values, resolved_out / "market_values.jsonl")

    # Normalize stats if available
    resolved_stats = Path(resolved_stats)
    if resolved_stats.exists():
        with open(resolved_stats, "r", encoding="utf-8") as f:
            raw_stats = json.load(f)
        all_stats = normalize_stats(raw_stats)
        write_jsonl(all_stats, resolved_out / "stats.jsonl")


if __name__ == "__main__":
    main()
