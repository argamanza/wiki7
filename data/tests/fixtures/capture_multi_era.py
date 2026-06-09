"""Capture multi-era TM HTML fixtures for the Phase 3a R2 spider work.

One-shot script run during PR B (2026-06-09). Not part of CI. Routes the same
way the production spiders do (ScraperAPI proxy + country_code=us so the
column-header CSS aliases keep working).

Usage:
    SCRAPERAPI_KEY=... uv run python data/tests/fixtures/capture_multi_era.py

Fixtures captured:
  - kader_<yr>_sample.html        : squad page for 2015/16, 1985/86
  - leistungsdaten_<yr>_sample.html : per-player stats for 2015/16, 1985/86
  - match_report_<yr>_sample.html : one match report from 2015/16, 1985/86
  - platzierungen_sample.html     : per-season league position table (all seasons)
  - bilanz_sample.html            : per-opponent head-to-head (all seasons)
  - transferrekorde_arrivals_sample.html : club record arrivals
  - transferrekorde_departures_sample.html : club record departures (NEW tab)
  - startseite_<yr>_sample.html   : club summary page for per-season manager extraction
  - startseite_1965_empty_sample.html : 1965/66 empty-placeholder verification

Existing fixtures (already in repo) are NOT re-fetched:
  - match_report_sample.html (2024/25 era)
  - leistungsdaten_sample.html (2024 era)
  - coaches_mitarbeiter_sample.html
  - club_transfers_sample.html
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from urllib.parse import quote

import requests

API_KEY = os.environ.get("SCRAPERAPI_KEY")
if not API_KEY:
    print("SCRAPERAPI_KEY env var not set", file=sys.stderr)
    sys.exit(1)

FIXTURES_DIR = Path(__file__).resolve().parent
BASE = "https://www.transfermarkt.com"
HBS = "hapoel-beer-sheva"
HBS_ID = "2976"

# (filename, target TM URL)
TARGETS: list[tuple[str, str]] = [
    # Multi-era squad pages
    ("kader_2015_sample.html", f"{BASE}/{HBS}/kader/verein/{HBS_ID}/saison_id/2015"),
    ("kader_1985_sample.html", f"{BASE}/{HBS}/kader/verein/{HBS_ID}/saison_id/1985"),

    # Multi-era stats pages
    ("leistungsdaten_2015_sample.html",
     f"{BASE}/{HBS}/leistungsdaten/verein/{HBS_ID}/plus/1?saison_id=2015"),
    ("leistungsdaten_1985_sample.html",
     f"{BASE}/{HBS}/leistungsdaten/verein/{HBS_ID}/plus/1?saison_id=1985"),

    # Multi-era match reports — picked one canonical match per era
    # 2015/16: the 28-Jun-2016 final game where they clinched the title (vs Ironi Kiryat Shmona)
    #   — fetch by walking the spielplan page IDs; use a known representative id below.
    # 1985/86: the Sep 14 1985 fixture vs Kiryat Eliezer — id 4828269 verified during PR A probing.
    ("match_report_1985_sample.html", f"{BASE}/spielbericht/index/spielbericht/4828269"),
    # 2015/16: a representative match report id from that season — pick from fixtures list afterwards.
    # Using a placeholder Liga Leumit fixture from that season; spider tests will validate.
    # We'll fetch the spielplan first then the match report for the first home game.
    ("spielplan_2015_sample.html",
     f"{BASE}/{HBS}/spielplandatum/verein/{HBS_ID}/saison_id/2015"),

    # New-spider club-level pages (no season — current snapshot)
    ("platzierungen_sample.html", f"{BASE}/{HBS}/platzierungen/verein/{HBS_ID}"),
    ("bilanz_sample.html", f"{BASE}/{HBS}/bilanz/verein/{HBS_ID}"),

    # Transfer records — both tabs (the existing spider only scrapes arrivals)
    ("transferrekorde_arrivals_sample.html",
     f"{BASE}/{HBS}/transferrekorde/verein/{HBS_ID}/plus/1?ids=a&sa=&saison_id=alle&saison_id_bis=alle&nat=&pos=&w_s=&altersklasse=&leihe=&intern=0"),
    ("transferrekorde_departures_sample.html",
     f"{BASE}/{HBS}/transferrekorde/verein/{HBS_ID}/plus/1?ids=a&sa=1&saison_id=alle&saison_id_bis=alle&nat=&pos=&w_s=&altersklasse=&leihe=&intern=0"),

    # Per-season club summary pages — for per-season manager extraction
    ("startseite_2024_sample.html",
     f"{BASE}/{HBS}/startseite/verein/{HBS_ID}/saison_id/2024"),
    ("startseite_1965_empty_sample.html",
     f"{BASE}/{HBS}/startseite/verein/{HBS_ID}/saison_id/1965"),
]


def fetch(target_url: str) -> bytes:
    """Fetch a URL via ScraperAPI proxy with the same params our spiders use."""
    proxied = (
        f"http://api.scraperapi.com/?api_key={API_KEY}"
        f"&url={quote(target_url, safe='')}"
        f"&country_code=us&render=false"
    )
    resp = requests.get(proxied, timeout=120)
    resp.raise_for_status()
    return resp.content


def main() -> None:
    fetched = 0
    skipped = 0
    failed: list[str] = []

    for filename, target_url in TARGETS:
        out = FIXTURES_DIR / filename
        if out.exists():
            print(f"SKIP   {filename} (already exists)")
            skipped += 1
            continue

        print(f"FETCH  {filename}  <-  {target_url}")
        try:
            content = fetch(target_url)
        except requests.exceptions.RequestException as exc:
            print(f"FAIL   {filename}: {exc}", file=sys.stderr)
            failed.append(filename)
            continue

        out.write_bytes(content)
        size_kb = len(content) // 1024
        print(f"  ->   wrote {size_kb} KB")
        fetched += 1

        # Be polite to ScraperAPI rate limits.
        time.sleep(1.0)

    print()
    print(f"Done. fetched={fetched} skipped={skipped} failed={len(failed)}")
    if failed:
        print(f"Failed targets: {failed}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
