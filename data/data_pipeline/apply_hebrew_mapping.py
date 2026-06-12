"""Apply Hebrew translations from mapping YAML to normalized data.

Reads the reviewed mappings.he.yaml and applies Hebrew translations for
positions, nationalities, player names, club names, and competitions.
Produces players.he.jsonl, transfers.he.jsonl, market_values.he.jsonl,
and matches.he.json (per season).

Usage:
    python -m data_pipeline.apply_hebrew_mapping [--input PATH] [--output PATH] [--mapping PATH]
"""

import json
import logging
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)


def _value(entry) -> str:
    """Resolve a mapping entry to its Hebrew string regardless of shape.

    Phase 3a R2 introduced the nested entry shape (`{he, src, confidence, note}`).
    The legacy flat shape (`Centre-Back: בלם`) still appears in files written
    before the migration. This helper accepts either and returns the Hebrew
    value, so every call-site below can stay shape-agnostic during the
    transition. `auto_translate_hebrew.py` rewrites all entries to nested on
    first load — after that the flat path is just safety.
    """
    if entry is None:
        return ""
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict):
        return entry.get("he", "") or ""
    return ""


def _lookup(section: dict, key: str) -> str:
    """Lookup a key in a category section, returning the Hebrew string."""
    return _value(section.get(key))

DEFAULT_MERGED_DIR = Path(__file__).resolve().parent / "output" / "merged"
DEFAULT_INPUT = DEFAULT_MERGED_DIR / "players.jsonl"
DEFAULT_OUTPUT = DEFAULT_MERGED_DIR / "players.he.jsonl"
DEFAULT_MAPPING = DEFAULT_MERGED_DIR / "mappings.he.yaml"


def load_mapping(mapping_path: Path) -> dict:
    with open(mapping_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def apply_hebrew(player: dict, mapping: dict) -> dict:
    pos_map = mapping.get("positions", {})
    nationality_map = mapping.get("nationalities", {})
    names_map = mapping.get("names", {})
    birth_place_map = mapping.get("birth_places", {})

    if player.get("main_position"):
        translated = _lookup(pos_map, player["main_position"])
        if translated:
            player["main_position"] = translated

    if player.get("nationality"):
        player["nationality"] = [
            _lookup(nationality_map, n) or n for n in player["nationality"]
        ]

    # Name resolution priority:
    #   1. Manual override in mappings.he.yaml (src: manual) — reviewer's word
    #      always wins, even over TM's own Hebrew. This is how a reviewer
    #      shortens "Ben Gordin → בן אנריקה גורדין ענברי" (TM long-canonical) to
    #      "בן גורדין" (common form). Discovered iter-cycle 1 walk 2026-06-12.
    #   2. Existing name_hebrew (from TM scrape OR Wikidata enrichment pass).
    #   3. Anything else in the names map (any src) — for players where TM
    #      gave no Hebrew at all.
    name_english = player.get("name_english", "")
    name_entry = names_map.get(name_english) if name_english else None

    def _is_manual(entry):
        return isinstance(entry, dict) and entry.get("src") == "manual"

    if _is_manual(name_entry):
        manual_he = name_entry.get("he")
        if manual_he:
            player["name_hebrew"] = manual_he
    elif not player.get("name_hebrew"):
        translated_name = _lookup(names_map, name_english)
        if translated_name:
            player["name_hebrew"] = translated_name

    # birth_place — new translation in iter-cycle 1 review walk (2026-06-12).
    # See generate_mapping_stub.py for the corpus build.
    if player.get("birth_place"):
        translated = _lookup(birth_place_map, player["birth_place"])
        if translated:
            player["birth_place"] = translated

    # other_positions — translate each entry via the positions map.
    if player.get("other_positions"):
        player["other_positions"] = [
            _lookup(pos_map, p) or p for p in player["other_positions"]
        ]

    return player


def _translate_club(club_name: str, club_map: dict) -> str:
    """Translate a club name using the mapping, falling back to original."""
    if not club_name:
        return club_name
    return _lookup(club_map, club_name) or club_name


def apply_hebrew_transfers(transfers_input: Path, transfers_output: Path, mapping: dict):
    """Apply Hebrew club name mappings to transfers data."""
    club_map = mapping.get("clubs", {})
    if not transfers_input.exists():
        return
    count = 0
    with open(transfers_input, "r", encoding="utf-8") as fin, \
         open(transfers_output, "w", encoding="utf-8") as fout:
        for line in fin:
            if not line.strip():
                continue
            transfer = json.loads(line)
            transfer["from_club"] = _translate_club(transfer.get("from_club", ""), club_map)
            transfer["to_club"] = _translate_club(transfer.get("to_club", ""), club_map)
            fout.write(json.dumps(transfer, ensure_ascii=False) + "\n")
            count += 1
    logger.info("Applied Hebrew mappings to %d transfers -> %s", count, transfers_output)


def apply_hebrew_market_values(mv_input: Path, mv_output: Path, mapping: dict):
    """Apply Hebrew club name mappings to market values data."""
    club_map = mapping.get("clubs", {})
    if not mv_input.exists():
        return
    count = 0
    with open(mv_input, "r", encoding="utf-8") as fin, \
         open(mv_output, "w", encoding="utf-8") as fout:
        for line in fin:
            if not line.strip():
                continue
            mv = json.loads(line)
            mv["team"] = _translate_club(mv.get("team", ""), club_map)
            fout.write(json.dumps(mv, ensure_ascii=False) + "\n")
            count += 1
    logger.info("Applied Hebrew mappings to %d market values -> %s", count, mv_output)


def _build_name_lookup(mapping: dict, players_he_path: Path | None = None) -> dict:
    """Build a combined English->Hebrew name lookup from the mapping and enriched player data.

    Phase 3a R2: collapses both flat and nested mapping shapes into a single
    flat `{en: he}` dict for the per-name fast-path used by match-event
    translation. Empty Hebrew values are dropped so lookups fall through to
    the original name unchanged.
    """
    raw_names = mapping.get("names", {})
    lookup = {key: _value(entry) for key, entry in raw_names.items()}
    if players_he_path and players_he_path.exists():
        with open(players_he_path, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                p = json.loads(line)
                if p.get("name_hebrew") and p.get("name_english"):
                    lookup.setdefault(p["name_english"], p["name_hebrew"])
    return {k: v for k, v in lookup.items() if v}


def _translate_name(name: str, name_lookup: dict) -> str:
    """Translate a player name using the lookup, falling back to original."""
    if not name:
        return name
    return name_lookup.get(name, name)


def _translate_match(match: dict, club_map: dict, comp_map: dict, name_lookup: dict) -> dict:
    """Apply Hebrew translations to all fields of a single match.

    Phase 3a R2: club/competition lookups route through `_lookup()` so both
    legacy flat and current nested mapping shapes resolve to the Hebrew
    value. The `name_lookup` dict has already been flattened upstream by
    `_build_name_lookup()`, so per-name lookups stay direct.
    """
    if match.get("opponent"):
        match["opponent"] = _lookup(club_map, match["opponent"]) or match["opponent"]
    if match.get("competition"):
        match["competition"] = _lookup(comp_map, match["competition"]) or match["competition"]

    for goal in match.get("goals", []):
        if goal.get("scorer"):
            goal["scorer"] = _translate_name(goal["scorer"], name_lookup)
        if goal.get("assist"):
            goal["assist"] = _translate_name(goal["assist"], name_lookup)
        if goal.get("team"):
            goal["team"] = _lookup(club_map, goal["team"]) or goal["team"]

    for sub in match.get("substitutions", []):
        if sub.get("player_in"):
            sub["player_in"] = _translate_name(sub["player_in"], name_lookup)
        if sub.get("player_out"):
            sub["player_out"] = _translate_name(sub["player_out"], name_lookup)
        if sub.get("team"):
            sub["team"] = _lookup(club_map, sub["team"]) or sub["team"]

    for card in match.get("cards", []):
        if card.get("player"):
            card["player"] = _translate_name(card["player"], name_lookup)
        if card.get("team"):
            card["team"] = _lookup(club_map, card["team"]) or card["team"]

    for pen in match.get("penalties", []):
        if pen.get("player"):
            pen["player"] = _translate_name(pen["player"], name_lookup)
        if pen.get("club"):
            pen["club"] = _lookup(club_map, pen["club"]) or pen["club"]
        if pen.get("team"):
            pen["team"] = _lookup(club_map, pen["team"]) or pen["team"]

    for side in ("home_lineup", "away_lineup"):
        lineup = match.get(side)
        if not lineup:
            continue
        if isinstance(lineup, list):
            for p in lineup:
                for key in ("name_hebrew", "name_english", "name"):
                    if p.get(key):
                        translated = _translate_name(p[key], name_lookup)
                        if translated != p[key]:
                            p[key] = translated
                            break
        elif isinstance(lineup, dict):
            new_lineup = {}
            for pos, players in lineup.items():
                if isinstance(players, str):
                    new_lineup[pos] = _translate_name(players, name_lookup)
                elif isinstance(players, list):
                    new_lineup[pos] = [
                        _translate_name(pl, name_lookup) if isinstance(pl, str) else pl
                        for pl in players
                    ]
                else:
                    new_lineup[pos] = players
            match[side] = new_lineup

    return match


def seed_merged_mapping_from_iter_cycles(
    iter_cycle_dirs: list[Path],
    merged_mapping_path: Path,
) -> int:
    """Reviewer-pass blocker (2026-06-13): the per-season iter-cycle
    mapping files (`output/<year>/mappings.he.yaml`) are where reviewers
    write `src: manual` corrections during walks. The all-time merged
    run reads `output/merged/mappings.he.yaml` — a DIFFERENT file that
    didn't see any of those corrections. Pre-fix, every reviewer
    correction from current cycles was absent from the prod push: the
    all-time run re-translated from scratch, picked bad Wikidata titles,
    and MovePaged everything during reviewer cleanup.

    This helper, run BEFORE the all-time `generate_stub`, merges every
    per-season mapping file into the merged file. Precedence:

      1. Anything already in the merged file (manual edits applied
         against the merged dir directly — rare but possible).
      2. `src: manual` entries from iter-cycles (reviewer's word always
         wins over auto translations).
      3. `src: wikidata` entries (high-confidence auto-translated).
      4. Anything else (auto-llm, auto-google, auto-phonetic).

    Returns the count of new entries added to the merged file.

    `iter_cycle_dirs` is the list of per-season pipeline output dirs to
    scan. Caller passes [`output/2024`, `output/2023`, ...] etc.
    """
    if not iter_cycle_dirs:
        return 0

    SRC_PRIORITY = {"manual": 4, "wikidata": 3, "auto-llm": 2,
                    "auto-google": 1, "auto-phonetic": 1}

    def _src_rank(entry):
        if isinstance(entry, dict):
            return SRC_PRIORITY.get(entry.get("src", ""), 0)
        return 0

    # Start from whatever the merged file already has.
    merged = (
        load_mapping(merged_mapping_path)
        if merged_mapping_path.exists() else {}
    )

    added = 0
    for cycle_dir in iter_cycle_dirs:
        cycle_path = cycle_dir / "mappings.he.yaml"
        if not cycle_path.exists():
            continue
        cycle_mapping = load_mapping(cycle_path)
        for section, entries in cycle_mapping.items():
            if not isinstance(entries, dict):
                continue
            merged_section = merged.setdefault(section, {})
            for key, cycle_entry in entries.items():
                merged_entry = merged_section.get(key)
                if merged_entry is None:
                    merged_section[key] = cycle_entry
                    added += 1
                elif _src_rank(cycle_entry) > _src_rank(merged_entry):
                    # Higher-priority source wins (manual > wikidata > auto).
                    merged_section[key] = cycle_entry

    merged_mapping_path.parent.mkdir(parents=True, exist_ok=True)
    with open(merged_mapping_path, "w", encoding="utf-8") as f:
        yaml.dump(
            merged, f, allow_unicode=True, sort_keys=True,
            default_flow_style=False,
        )
    logger.info(
        "Seeded merged mapping from %d iter-cycle dir(s); added %d new entries -> %s",
        len(iter_cycle_dirs), added, merged_mapping_path,
    )
    return added


def apply_hebrew_fixtures(
    fixtures_input: Path,
    fixtures_output: Path,
    mapping: dict,
):
    """Translate opponent + competition on each fixture record. §6 high #9
    fix from the 2026-06-12 review: competition_season.j2 builds match-
    page links from `fixtures` data (`{{ f.date }} נגד {{ f.opponent }}
    ({{ competition }})`), but match pages themselves are titled from
    Hebrew-enriched `matches.he.json` data. Without translating fixtures
    too, every link broke as soon as the Hebrew mapping ran.

    Output goes to `fixtures.he.json` alongside the existing
    `matches.he.json`. Consumers should prefer `.he.json` when present.
    """
    if not fixtures_input.exists():
        logger.debug("No fixture file at %s, skipping", fixtures_input)
        return
    club_map = mapping.get("clubs", {})
    comp_map = mapping.get("competitions", {})

    with open(fixtures_input, "r", encoding="utf-8") as f:
        fixtures = json.load(f)

    for fixture in fixtures:
        if fixture.get("opponent"):
            fixture["opponent"] = _lookup(club_map, fixture["opponent"]) or fixture["opponent"]
        if fixture.get("competition"):
            fixture["competition"] = (
                _lookup(comp_map, fixture["competition"]) or fixture["competition"]
            )

    with open(fixtures_output, "w", encoding="utf-8") as f:
        json.dump(fixtures, f, ensure_ascii=False, indent=2)

    logger.info(
        "Applied Hebrew mappings to %d fixtures -> %s", len(fixtures), fixtures_output,
    )


def apply_hebrew_matches(
    matches_input: Path,
    matches_output: Path,
    mapping: dict,
    players_he_path: Path | None = None,
):
    """Apply Hebrew translations to match data (opponents, players, competitions)."""
    if not matches_input.exists():
        logger.debug("No match file at %s, skipping", matches_input)
        return

    club_map = mapping.get("clubs", {})
    comp_map = mapping.get("competitions", {})
    name_lookup = _build_name_lookup(mapping, players_he_path)

    with open(matches_input, "r", encoding="utf-8") as f:
        matches = json.load(f)

    translated = [_translate_match(m, club_map, comp_map, name_lookup) for m in matches]

    with open(matches_output, "w", encoding="utf-8") as f:
        json.dump(translated, f, ensure_ascii=False, indent=2)

    logger.info("Applied Hebrew mappings to %d matches -> %s", len(translated), matches_output)


def apply_mappings(
    input_path: Path | None = None,
    output_path: Path | None = None,
    mapping_path: Path | None = None,
):
    """Apply Hebrew mappings to player, transfer, and market value data."""
    resolved_input = input_path or DEFAULT_INPUT
    resolved_output = output_path or DEFAULT_OUTPUT
    resolved_mapping = mapping_path or DEFAULT_MAPPING
    merged_dir = resolved_input.parent

    mapping = load_mapping(resolved_mapping)

    count = 0
    with open(resolved_input, "r", encoding="utf-8") as fin, \
         open(resolved_output, "w", encoding="utf-8") as fout:
        for line in fin:
            if not line.strip():
                continue
            player = json.loads(line)
            player = apply_hebrew(player, mapping)
            fout.write(json.dumps(player, ensure_ascii=False) + "\n")
            count += 1

    logger.info("Applied Hebrew mappings to %d players -> %s", count, resolved_output)

    apply_hebrew_transfers(
        merged_dir / "transfers.jsonl",
        merged_dir / "transfers.he.jsonl",
        mapping,
    )
    apply_hebrew_market_values(
        merged_dir / "market_values.jsonl",
        merged_dir / "market_values.he.jsonl",
        mapping,
    )


def main():
    """CLI entry point."""
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    parser = argparse.ArgumentParser(description="Apply Hebrew mappings to player data")
    parser.add_argument("--input", type=Path, default=None, dest="input_path")
    parser.add_argument("--output", type=Path, default=None, dest="output_path")
    parser.add_argument("--mapping", type=Path, default=None, dest="mapping_path")
    args = parser.parse_args()

    apply_mappings(
        input_path=args.input_path,
        output_path=args.output_path,
        mapping_path=args.mapping_path,
    )


if __name__ == "__main__":
    main()
