"""Derive each coach's HBS trophy list by joining honours x per-season managers.

Phase 3a R2 addition. The original Coach model came from the `/mitarbeiter/`
spider, which only carries current staff. We don't get historical tenure data
from TM at all — but we DO get the per-season manager name + TM coach id from
the `platzierungen` spider. Joining (honours.json) x (season_standings.json on
coach_id) gives us, for every coach who ever managed HBS:

  - the seasons they were in charge of (tenure_seasons)
  - the trophies HBS won during those seasons (hbs_trophies_won)

This unlocks coach pages that state "Won 2 league titles + 1 cup as HBS
manager" without us needing a separate historical-coach scraper.

Inputs:
  - honours.json: list of {competition, achievement, seasons: [list]}
  - season_standings.json (platzierungen output): list of season rows w/
    manager_name + manager_id
  - coaches.json (mitarbeiter output): current staff list, used to enrich
    current-coach rows with tenure data even though they're already in the
    file with role + appointment dates.

Output: list of Coach-shaped dicts ready to write to coaches_enriched.json.
The pipeline import step prefers coaches_enriched.json when present, falling
back to coaches.json (current-staff-only) when not.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Iterable

logger = logging.getLogger(__name__)


def _load_json(path: Path) -> list[dict]:
    """Load a JSON file; return [] if absent or empty."""
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return []
    data = json.loads(text)
    return data if isinstance(data, list) else [data]


def _season_label_yyyy_yy(season_yyyy: str) -> str:
    """Convert "2024" -> "2024/25" for trophy-label rendering."""
    try:
        start = int(season_yyyy)
    except (TypeError, ValueError):
        return season_yyyy or ""
    return f"{start}/{str(start + 1)[-2:]}"


def _expand_honours_to_per_season(honours: Iterable[dict]) -> dict[str, list[str]]:
    """Build a `season_yyyy -> [trophy label]` map.

    Each honours row looks like:
        {"competition": "Israeli Champion", "achievement": "Winner",
         "seasons": ["24/25", "17/18", ...]}

    Output (per season):
        {"2024": ["Israeli Champion 2024/25"], ...}

    Why per-season-keyed: matching against `season_standings.json` is by
    bare integer season ("2024"), so we pivot the honours rows to that key
    at the start and the per-coach loop becomes O(rows) instead of O(rows x
    seasons).
    """
    out: dict[str, list[str]] = {}
    for h in honours:
        comp = (h.get("competition") or "").strip()
        seasons = h.get("seasons") or []
        if not comp or not seasons:
            continue
        for s in seasons:
            yyyy = _season_label_to_yyyy(s)
            if not yyyy:
                continue
            display = _season_label_yyyy_yy(yyyy)
            out.setdefault(yyyy, []).append(f"{comp} {display}")
    return out


def _season_label_to_yyyy(label: str) -> str | None:
    """Convert honours-row season labels ("24/25", "1996/97", "08/09") to the
    bare integer start-year. Tolerates both 2- and 4-digit prefixes.
    """
    if not label or "/" not in label:
        return None
    head = label.split("/", 1)[0].strip()
    if not head.isdigit():
        return None
    if len(head) == 4:
        return head
    if len(head) == 2:
        yy = int(head)
        return str(1900 + yy if yy >= 50 else 2000 + yy)
    return None


def derive(
    honours_path: Path,
    season_standings_path: Path,
    coaches_current_path: Path,
) -> list[dict]:
    """Build the enriched coach roster.

    Returns a list of dicts shaped like the Coach schema:
        {id, name, role?, tenure_start?, tenure_end?, matches, wins, draws,
         losses, ppm, is_caretaker, tenure_seasons, hbs_trophies_won}

    Every coach who has ever managed HBS (from season_standings.json) gets
    one row; current staff non-managers (assistants, fitness coach, youth
    director) are kept on as-is from coaches_current_path.
    """
    honours = _load_json(honours_path)
    standings = _load_json(season_standings_path)
    current_staff = _load_json(coaches_current_path)

    honours_by_season = _expand_honours_to_per_season(honours)
    logger.info(
        "Honours indexed: %d seasons carry at least one trophy.",
        len(honours_by_season),
    )

    coaches_by_id: dict[str, dict] = {}

    # Walk per-season manager rows. Each emits/updates the coach's
    # tenure_seasons + hbs_trophies_won.
    for row in standings:
        coach_id = (row.get("manager_id") or "").strip()
        coach_name = (row.get("manager_name") or "").strip()
        if not coach_id or not coach_name:
            continue
        season = (row.get("season") or "").strip()
        if not season:
            continue

        coach = coaches_by_id.setdefault(coach_id, {
            "id": coach_id,
            "name": coach_name,
            "role": "Manager",
            "tenure_start": "",
            "tenure_end": "",
            "matches": 0,
            "wins": 0,
            "draws": 0,
            "losses": 0,
            "ppm": "",
            "is_caretaker": False,
            "tenure_seasons": [],
            "hbs_trophies_won": [],
        })
        if season not in coach["tenure_seasons"]:
            coach["tenure_seasons"].append(season)
        for trophy in honours_by_season.get(season, []):
            if trophy not in coach["hbs_trophies_won"]:
                coach["hbs_trophies_won"].append(trophy)
        # Aggregate match counts from the season row when present.
        for key in ("wins", "draws", "losses"):
            val = row.get(key)
            if isinstance(val, int):
                coach[key] += val

    for coach in coaches_by_id.values():
        coach["matches"] = coach["wins"] + coach["draws"] + coach["losses"]
        coach["tenure_seasons"].sort()
        # Sort trophies by the season-suffix so newest is last (display happens
        # in template order; deterministic order keeps idempotent diffs).
        coach["hbs_trophies_won"].sort()

    # Layer current-staff entries on top so non-manager staff (assistants,
    # fitness coach, etc.) and any current-only manager metadata (appointment
    # date, contract expires) survive into the enriched output.
    for s in current_staff:
        cid = (s.get("id") or "").strip()
        if not cid:
            continue
        existing = coaches_by_id.get(cid)
        if existing is None:
            # Non-manager current staff — keep their row as-is, plus the empty
            # tenure_seasons / hbs_trophies_won defaults from the schema.
            coaches_by_id[cid] = {
                **s,
                "is_caretaker": s.get("is_caretaker", False),
                "tenure_seasons": s.get("tenure_seasons", []),
                "hbs_trophies_won": s.get("hbs_trophies_won", []),
            }
        else:
            # Current manager — graft the appointment dates onto the historical
            # row built from platzierungen.
            for key in ("role", "tenure_start", "tenure_end", "ppm"):
                if s.get(key):
                    existing[key] = s[key]

    return sorted(coaches_by_id.values(), key=lambda c: c["name"])


def write_enriched(
    base_dir: Path,
    honours_path: Path | None = None,
    season_standings_path: Path | None = None,
    coaches_current_path: Path | None = None,
    out_path: Path | None = None,
) -> Path:
    """Write coaches_enriched.json under `base_dir/tmk-scraper/output/`."""
    honours_path = honours_path or (base_dir / "honours.json")
    season_standings_path = season_standings_path or (base_dir / "season_standings.json")
    coaches_current_path = coaches_current_path or (base_dir / "coaches.json")
    out_path = out_path or (base_dir / "coaches_enriched.json")

    enriched = derive(honours_path, season_standings_path, coaches_current_path)
    out_path.write_text(
        json.dumps(enriched, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    logger.info(
        "Wrote %d enriched coach rows to %s (with trophies from %d seasons)",
        len(enriched), out_path,
        sum(1 for c in enriched if c["hbs_trophies_won"]),
    )
    return out_path
