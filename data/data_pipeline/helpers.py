import re
from dateutil.parser import parse
from datetime import date
from typing import List, Optional, Set
import pycountry


def is_all_hebrew(text: str) -> bool:
    return bool(re.fullmatch(r'[\u0590-\u05FF\s]+', text))

def parse_birth_date(raw: str) -> Optional[date]:
    if not raw:
        return None
    try:
        return parse(raw.split(" (")[0]).date()
    except Exception:
        return None

def parse_countries(country_string: str) -> List[str]:
    if not country_string or not country_string.strip():
        return []

    cleaned = re.sub(r'\s+', ' ', country_string.strip())
    cleaned = re.sub(r'[,;|/]', ' ', cleaned)
    cleaned = re.sub(r'\s+', ' ', cleaned)

    country_variants = _get_country_variants()
    found_countries = _greedy_country_match(cleaned, country_variants)

    return [_standardize_country_name(country) for country in found_countries]


def _get_country_variants() -> Set[str]:
    variants = set()

    for country in pycountry.countries:
        variants.add(country.name.lower())
        if hasattr(country, 'common_name'):
            variants.add(country.common_name.lower())
        if hasattr(country, 'official_name'):
            variants.add(country.official_name.lower())

    variants.update([
        "united states of america", "usa", "us", "uk", "great britain", "britain",
        "bosnia", "russia", "south korea", "north korea", "iran", "venezuela",
        "syria", "cote d'ivoire", "ivory coast"
    ])

    return variants


def _greedy_country_match(text: str, country_variants: Set[str]) -> List[str]:
    text_lower = text.lower()
    found_countries = []
    sorted_variants = sorted(country_variants, key=len, reverse=True)
    used_positions = set()

    for variant in sorted_variants:
        start = 0
        while True:
            pos = text_lower.find(variant, start)
            if pos == -1:
                break

            end_pos = pos + len(variant)

            if any(i in used_positions for i in range(pos, end_pos)):
                start = pos + 1
                continue

            if _is_valid_word_boundary(text_lower, pos, end_pos):
                used_positions.update(range(pos, end_pos))
                original_case = text[pos:end_pos]
                found_countries.append((pos, original_case))

            start = pos + 1

    found_countries.sort(key=lambda x: x[0])
    return [country for _, country in found_countries]


def _is_valid_word_boundary(text: str, start: int, end: int) -> bool:
    if start > 0 and text[start - 1].isalnum():
        return False
    if end < len(text) and text[end].isalnum():
        return False
    return True


def _standardize_country_name(country_name: str) -> str:
    name_lower = country_name.lower().strip()

    for country in pycountry.countries:
        if (country.name.lower() == name_lower or
                (hasattr(country, 'common_name') and country.common_name.lower() == name_lower) or
                (hasattr(country, 'official_name') and country.official_name.lower() == name_lower)):
            return country.name

    special_cases = {
        "usa": "United States", "us": "United States",
        "united states of america": "United States",
        "uk": "United Kingdom", "great britain": "United Kingdom", "britain": "United Kingdom",
        "bosnia": "Bosnia and Herzegovina", "russia": "Russian Federation",
        "south korea": "Korea, Republic of", "north korea": "Korea, Democratic People's Republic of",
        "iran": "Iran, Islamic Republic of", "venezuela": "Venezuela, Bolivarian Republic of",
        "syria": "Syrian Arab Republic", "cote d'ivoire": "Côte d'Ivoire",
        "ivory coast": "Côte d'Ivoire"
    }

    return special_cases.get(name_lower, country_name.title())

def is_homegrown(player: dict) -> bool:
    youth_keywords = ["H. B. Sheva U19", "Hapoel Beer Sheva U19"]
    transfers = player.get("transfers", [])
    return any(
        any(keyword in transfer.get("from", "") for keyword in youth_keywords)
        for transfer in transfers
    )

def is_retired(player: dict) -> bool:
    transfers = player.get("transfers", [])
    for transfer in transfers:
        to_club = transfer.get("to", "").lower()
        if "retired" in to_club:
            return True
    return False


def to_season_display(season: str | int) -> str:
    """Convert a bare-integer start-year season ("2024" or 2024) to TM's
    human-readable display format ("2024/25").

    Phase 3a R2: the pipeline's internal join key is the bare integer
    start-year (matches the spider's --season arg, TM's saison_id URL
    param, the filesystem dir layout, and the Cargo `season` column).
    Human-visible surfaces — page titles, h1 headings, category names,
    infobox-rendered season strings — normalise via this helper to the
    slash format. Single helper means all the page-title strings emit
    the same shape; single integer-start-year join key means all the
    data-layer code stays simple.

    Tolerates either str or int input. Returns the original string
    unchanged when conversion isn't possible (defensive: a season
    label that was already in slash form, or a malformed value).
    """
    if season is None:
        return ""
    s = str(season).strip()
    if not s:
        return ""
    # Already in slash form? Pass through.
    if "/" in s:
        return s
    if not s.isdigit():
        return s
    start = int(s)
    return f"{start}/{str(start + 1)[-2:]}"
