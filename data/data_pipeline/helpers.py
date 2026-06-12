import re
from dateutil.parser import parse
from datetime import date
from typing import List, Optional, Set
import pycountry


# Iter-cycle 1 review walk (2026-06-12): TM emits transfer-fee values as
# free-text strings rather than structured types. The fixed-vocab strings
# below leak into Hebrew transfer tables in their English form unless
# translated. Numeric values (€350k, €1.20m) pass through unchanged — the
# € symbol is football-fan convention.
_TRANSFER_FEE_HEBREW = {
    "free transfer": "העברה חופשית",
    "loan transfer": "השאלה",
    "end of loan": "סוף השאלה",
    "loan fee": "דמי השאלה",
    "?": "",
    "-": "",
}


# Iter-cycle 1 review walk (2026-06-12): TM emits youth-team tenures with
# age-group / academy suffixes ("Benfica U17", "Sporting Yth.", etc). The
# player_page transfers table previously listed these inline with senior
# transfers — Wikipedia-style infoboxes typically split "Youth career"
# (U-teams + academies) from senior career to make the pro debut visible.
# B-teams / II / Reserves are NOT classified here as youth — they're a
# senior reserve tier and the player on them is already professional.
# English youth markers — TM-supplied form, present before Hebrew mapping.
_YOUTH_SUFFIX_RE_EN = re.compile(
    r"\b(?:U\d{2}|Sub-?\d{2}|Yth|Youth|Juvenil|Junior|Cadete)\.?\s*$",
    flags=re.IGNORECASE,
)

# Hebrew youth markers — present AFTER apply_hebrew_mapping has rewritten
# the club names. "תחת N" literally "under N" is the Hebrew rendering of
# "U-N"; "נוער" is the Hebrew word for "youth". TM-translation pipeline
# emits "Benfica U17" → "בנפיקה תחת 17" and "Sporting Yth" → "ספורטינג
# נוער", so the youth classifier must recognise both forms — it's called
# on whichever stage of the data the renderer sees. Iter-cycle 1 walk
# (2026-06-12) surfaced this when 53 Hebrew-form youth transfers were
# being silently bucketed as senior.
_YOUTH_SUFFIX_RE_HE = re.compile(
    r"(?:תחת\s+\d{2}|נוער)\s*$",
)


def is_youth_club_name(name: str | None) -> bool:
    """True iff this club name names a youth / academy team.

    Matches youth markers as a TRAILING token in either English (TM raw
    form: "Benfica U17", "Sporting Yth.", "Sporting Sub-15") or Hebrew
    (post-translation form: "בנפיקה תחת 17", "ספורטינג נוער"). Tolerates
    the trailing-dot variant and case-insensitive matches for English.

    Returns False for empty / None inputs — those are senior-by-default
    so a missing club name doesn't accidentally bucket the transfer into
    "Youth career".
    """
    if not name:
        return False
    if _YOUTH_SUFFIX_RE_EN.search(name):
        return True
    return bool(_YOUTH_SUFFIX_RE_HE.search(name))


def to_il_fee(raw: str | None) -> str:
    """Translate a TM transfer-fee string to Hebrew form, or pass numeric
    values through unchanged.

    TM emits a small fixed vocabulary for non-numeric fees ("free transfer",
    "loan transfer", "End of loan") plus the special-cased `Loan fee:<br/>
    <i>€X</i>` HTML shape for loan-with-fee deals. Numeric € values stay
    in original form (football convention; localising to ₪ would add
    FX-rate noise).
    """
    if not raw or not isinstance(raw, str):
        return ""
    s = raw.strip()
    if not s:
        return ""
    # Loan-fee HTML shape: extract the inner €X amount.
    m = re.match(r"Loan fee:.*?<i[^>]*>(€[^<]+)</i>", s, re.IGNORECASE)
    if m:
        return f"דמי השאלה: {m.group(1)}"
    # Bare lookup against fixed vocab (case-insensitive).
    he = _TRANSFER_FEE_HEBREW.get(s.lower())
    if he is not None:
        return he
    # Numeric € amounts + everything else: pass through.
    return s


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


def to_il_date(raw: str | None) -> str:
    """Convert a date string to Israeli DD/MM/YYYY format.

    Tolerates several common input shapes seen on TM:
      - ISO `YYYY-MM-DD`             → `DD/MM/YYYY`
      - Already-Israeli `DD/MM/YYYY` → pass through unchanged
      - Dot format `DD.MM.YYYY`      → normalised to slashes
      - TM match-date `Thu 25/07/24` → `25/07/2024` (strip day prefix,
                                        expand 2-digit year)
      - Bare `DD/MM/YY`              → `DD/MM/YYYY` (expand 2-digit year)
      - Empty / `?` / `-` / None    → empty string

    Iter-cycle 1 review walk (2026-06-12): Israeli convention is
    DD/MM/YYYY with slashes. Used on government forms, banks, sports
    media. The pipeline previously rendered ISO `YYYY-MM-DD` on every
    date surface (birth_date, transfer_date, market-value date) which
    reviewers flagged as non-idiomatic. Day-of-week prefix on match
    dates is stripped to keep the output uniform — the date itself is
    the canonical thing readers reference; the day-of-week can be
    derived if needed.

    Returns the original string if parsing fails (defensive — don't
    blank a date the user might still want to see, even if oddly formed).
    """
    if not raw or not isinstance(raw, str):
        return ""
    s = raw.strip()
    if not s or s in ("?", "-"):
        return ""
    # Strip TM's day-of-week prefix ("Thu 25/07/24" → "25/07/24"). The day-
    # name is English; in a Hebrew-target wiki it'd be jarring. The date is
    # the canonical thing readers care about.
    s = re.sub(r"^(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+", "", s, flags=re.IGNORECASE)
    # Already DD/MM/YYYY?
    if re.fullmatch(r"\d{2}/\d{2}/\d{4}", s):
        return s
    # DD/MM/YY → expand 2-digit year (assume 20XX for now; revisit if pre-2000
    # data starts flowing through this filter).
    m = re.fullmatch(r"(\d{2})/(\d{2})/(\d{2})", s)
    if m:
        return f"{m.group(1)}/{m.group(2)}/20{m.group(3)}"
    # DD.MM.YYYY → slashes
    m = re.fullmatch(r"(\d{2})\.(\d{2})\.(\d{4})", s)
    if m:
        return f"{m.group(1)}/{m.group(2)}/{m.group(3)}"
    # ISO YYYY-MM-DD
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return f"{m.group(3)}/{m.group(2)}/{m.group(1)}"
    # Permissive parse via dateutil — handles less-common shapes
    try:
        return parse(s).date().strftime("%d/%m/%Y")
    except Exception:
        return s


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
