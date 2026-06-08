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
    matches: int = 0
    wins: int = 0
    draws: int = 0
    losses: int = 0
    ppm: Optional[str] = None  # Points per match


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
