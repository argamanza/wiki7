from pydantic import BaseModel
from typing import List, Optional
from datetime import date

class Transfer(BaseModel):
    player_id: str
    season: str
    transfer_date: str
    from_club: str
    to_club: str
    fee: str
    loan: bool
    # Phase 3a R2: TM club IDs for future cross-linking (e.g. linking to opponent
    # clubs' wiki pages once they exist). Nullable: free agents have no source club ID.
    from_club_tm_id: Optional[str] = None
    to_club_tm_id: Optional[str] = None

class MarketValue(BaseModel):
    player_id: str
    value_date: str
    value: str
    team: str

class PlayerSeasonStats(BaseModel):
    player_id: str
    season: str
    appearances: int = 0
    goals: int = 0
    assists: int = 0
    yellow_cards: int = 0
    second_yellow_cards: int = 0
    red_cards: int = 0
    minutes_played: int = 0


class Coach(BaseModel):
    id: str  # Transfermarkt coach ID
    name: str
    role: Optional[str] = None  # "Manager", "Assistant Manager", etc. (from /mitarbeiter/)
    tenure_start: Optional[str] = None
    tenure_end: Optional[str] = None
    # Field is `played` not `matches` — `matches` is a Cargo reserved keyword
    # (CargoDeclare.php $cargoReservedWords). Discovered iter-cycle 1 review
    # when the HeadToHead Cargo template approval surfaced the error. Same
    # constraint applies to all four Cargo schemas using games-played counts.
    played: int = 0
    wins: int = 0
    draws: int = 0
    losses: int = 0
    ppm: Optional[str] = None  # Points per match
    # Phase 3a R2: interim/caretaker marker for managers between permanent
    # appointments. TM annotates these with "(Caretaker)" in the role text.
    is_caretaker: bool = False
    # Phase 3a R2: which seasons this coach was active. Populated by joining
    # the per-season-manager extraction across all seasons; useful for
    # rendering per-coach tenure cards and for the trophies-won join below.
    tenure_seasons: List[str] = []
    # Phase 3a R2: derived list of HBS trophies won during this coach's
    # tenure (join: honours x tenure_seasons). Strings like "Israeli
    # Champion 2016/17", "Israeli Cup 2019/20".
    hbs_trophies_won: List[str] = []


class Honour(BaseModel):
    competition: str
    achievement: str  # "Winner", "Runner-up", etc.
    seasons: List[str]


class Stadium(BaseModel):
    name: str
    capacity: Optional[int] = None
    surface: Optional[str] = None
    opening_year: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None


class ClubRecord(BaseModel):
    category: str  # "Most appearances", "Most goals", etc.
    player_name: str
    player_id: Optional[str] = None
    value: str  # "245 apps", "€2.5m", etc.


class ClubTransfer(BaseModel):
    season: str
    player_name: str
    player_id: Optional[str] = None
    age: Optional[str] = None
    position: Optional[str] = None
    from_club: str
    to_club: str
    fee: str
    loan: bool = False
    direction: str  # "in" or "out"


class Player(BaseModel):
    id: str  # Transfermarkt ID
    name_english: str
    name_hebrew: Optional[str]
    nationality: Optional[List[str]]
    birth_date: Optional[date]
    birth_place: Optional[str]
    main_position: Optional[str]
    current_squad: bool
    current_jersey_number: Optional[int]
    homegrown: bool
    retired: bool
    # Phase 3a R2: additional player facts surfaced on the TM profile page
    # but not previously captured by the spider. All nullable so historical
    # players (whose profiles often omit these) still validate.
    preferred_foot: Optional[str] = None  # "right" | "left" | "both"
    height_cm: Optional[int] = None
    contract_expires: Optional[str] = None
    is_captain: bool = False  # current captain flag (squad-page icon)
    current_market_value: Optional[str] = None  # "€1.50m"; convenience field, latest entry of MV history
    other_positions: List[str] = []  # secondary positions; already scraped, now exposed on the model


class Match(BaseModel):
    """A single match record. Phase 3a R2: typed model for what the match
    spider yields, so the schema additions (halftime, AET, stadium, referee
    team) have one canonical home. Fields the spider doesn't populate today
    are nullable / empty so existing tests keep working.
    """
    season: str
    competition: Optional[str] = None
    matchday: Optional[str] = None
    date: Optional[str] = None
    time: Optional[str] = None
    venue: Optional[str] = None  # "H" / "A" / "N" (neutral) — from fixtures spider
    opponent: Optional[str] = None
    result: Optional[str] = None
    system_of_play: Optional[str] = None
    attendance: Optional[str] = None

    # Phase 3a R2: new fields
    halftime_score: Optional[str] = None  # "0:0" / "1:2"
    aet: bool = False  # extra time played
    stadium: Optional[str] = None  # per-match stadium (away matches)

    # Referee team — PR B note: TM exposes only `referee` (main referee)
    # inline in the match-report metadata. The other 5 fields are nullable
    # forward-compat slots for hand-curation by reviewers + a future IFA
    # scraper (filed as a Phase 4 backlog item).
    referee: Optional[str] = None
    assistant_referee_1: Optional[str] = None
    assistant_referee_2: Optional[str] = None
    fourth_official: Optional[str] = None
    var_referee: Optional[str] = None
    var_assistant: Optional[str] = None


class SeasonStanding(BaseModel):
    """One row per season-and-competition from the platzierungen page.
    Populated for seasons >= 1986/87. Older seasons may be absent.
    """
    season: str  # "2024" (start-year)
    competition: str  # e.g. "Ligat ha'Al"
    tier: Optional[int] = None  # 1 = top flight, 2 = second tier
    final_position: Optional[int] = None
    played: Optional[int] = None  # NOT `matches` — Cargo reserved keyword (see Coach above).
    wins: Optional[int] = None
    draws: Optional[int] = None
    losses: Optional[int] = None
    goals_for: Optional[int] = None
    goals_against: Optional[int] = None
    points: Optional[int] = None


class HeadToHead(BaseModel):
    """One row per opponent from the bilanz page. Aggregated across all
    seasons (TM's bilanz view defaults to "All"). Unlocks the Derbies
    page (vs Maccabi TA, Hapoel TA, Beitar Jerusalem, Maccabi Haifa)
    and the Records page's "most-played opponent" line.
    """
    opponent: str
    opponent_tm_id: Optional[str] = None  # for cross-linking
    played: int = 0  # NOT `matches` — Cargo reserved keyword (see Coach above).
    wins: int = 0
    draws: int = 0
    losses: int = 0
    goals_for: int = 0
    goals_against: int = 0
    avg_attendance: Optional[int] = None


class SeasonManager(BaseModel):
    """One row per (season, manager) parsed from the per-season startseite
    page. Joining across all seasons gives the historical-coach list that
    TM no longer exposes via /trainer/. Source for Coach.tenure_seasons +
    Coach.hbs_trophies_won derivation.
    """
    season: str
    coach_id: str
    coach_name: str
    is_caretaker: bool = False
    played: Optional[int] = None  # NOT `matches` — Cargo reserved keyword (see Coach above).
    wins: Optional[int] = None
    draws: Optional[int] = None
    losses: Optional[int] = None
    ppm: Optional[str] = None
