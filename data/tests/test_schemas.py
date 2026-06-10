"""Tests for data_pipeline.schemas Pydantic models."""

from datetime import date
from data_pipeline.schemas import (
    Player, Transfer, MarketValue, PlayerSeasonStats,
    Coach, Match, SeasonStanding, HeadToHead, SeasonManager,
)


class TestPlayerSchema:
    def test_valid_player(self):
        player = Player(
            id="503642",
            name_english="Sagiv Jehezkel",
            name_hebrew="שגיב יחזקאל",
            nationality=["Israel"],
            birth_date=date(2000, 1, 14),
            birth_place="Be'er Sheva, Israel",
            main_position="Attacking Midfield",
            current_squad=True,
            current_jersey_number=10,
            homegrown=True,
            retired=False,
        )
        assert player.id == "503642"
        assert player.name_english == "Sagiv Jehezkel"
        assert player.name_hebrew == "שגיב יחזקאל"

    def test_minimal_player(self):
        player = Player(
            id="123",
            name_english="Test Player",
            name_hebrew=None,
            nationality=None,
            birth_date=None,
            birth_place=None,
            main_position=None,
            current_squad=False,
            current_jersey_number=None,
            homegrown=False,
            retired=False,
        )
        assert player.id == "123"
        assert player.name_hebrew is None

    def test_serialization(self):
        player = Player(
            id="503642",
            name_english="Sagiv Jehezkel",
            name_hebrew=None,
            nationality=["Israel"],
            birth_date=date(2000, 1, 14),
            birth_place=None,
            main_position="Attacking Midfield",
            current_squad=True,
            current_jersey_number=10,
            homegrown=True,
            retired=False,
        )
        data = player.model_dump()
        assert data["id"] == "503642"
        assert data["birth_date"] == date(2000, 1, 14)

    def test_json_serialization(self):
        player = Player(
            id="1",
            name_english="Test",
            name_hebrew=None,
            nationality=None,
            birth_date=None,
            birth_place=None,
            main_position=None,
            current_squad=True,
            current_jersey_number=None,
            homegrown=False,
            retired=False,
        )
        json_str = player.model_dump_json()
        assert '"id":"1"' in json_str


class TestTransferSchema:
    def test_valid_transfer(self):
        transfer = Transfer(
            player_id="503642",
            season="2022/23",
            transfer_date="Aug 1, 2022",
            from_club="Hapoel Beer Sheva U19",
            to_club="Hapoel Beer Sheva",
            fee="-",
            loan=False,
        )
        assert transfer.player_id == "503642"
        assert transfer.loan is False

    def test_loan_transfer(self):
        transfer = Transfer(
            player_id="101577",
            season="2020/21",
            transfer_date="Aug 10, 2020",
            from_club="Charlton Athletic",
            to_club="Bnei Sakhnin",
            fee="Loan",
            loan=True,
        )
        assert transfer.loan is True
        assert transfer.fee == "Loan"


class TestMarketValueSchema:
    def test_valid_market_value(self):
        mv = MarketValue(
            player_id="503642",
            value_date="Dec 2023",
            value="€2.50m",
            team="Hapoel Beer Sheva",
        )
        assert mv.player_id == "503642"
        assert mv.value == "€2.50m"


class TestPlayerSeasonStatsSchema:
    def test_valid_stats(self):
        stats = PlayerSeasonStats(
            player_id="503642",
            season="2024",
            appearances=30,
            goals=8,
            assists=5,
            yellow_cards=3,
            red_cards=0,
            minutes_played=2450,
        )
        assert stats.player_id == "503642"
        assert stats.season == "2024"
        assert stats.appearances == 30
        assert stats.goals == 8

    def test_defaults(self):
        stats = PlayerSeasonStats(player_id="123", season="2023")
        assert stats.appearances == 0
        assert stats.goals == 0
        assert stats.assists == 0
        assert stats.yellow_cards == 0
        assert stats.red_cards == 0
        assert stats.minutes_played == 0

    def test_serialization(self):
        stats = PlayerSeasonStats(
            player_id="503642",
            season="2024",
            appearances=30,
            goals=8,
        )
        data = stats.model_dump()
        assert data["player_id"] == "503642"
        assert data["appearances"] == 30
        json_str = stats.model_dump_json()
        assert '"player_id":"503642"' in json_str


class TestPlayerR2Additions:
    """Phase 3a R2 additions to Player. All nullable so existing minimal payloads
    keep validating, but new fields surface when the spider extracts them.
    """

    def test_phase_3a_r2_fields_default_to_null_or_empty(self):
        player = Player(
            id="1", name_english="Test", name_hebrew=None, nationality=None,
            birth_date=None, birth_place=None, main_position=None,
            current_squad=False, current_jersey_number=None, homegrown=False, retired=False,
        )
        assert player.preferred_foot is None
        assert player.height_cm is None
        assert player.contract_expires is None
        assert player.is_captain is False
        assert player.current_market_value is None
        assert player.other_positions == []

    def test_fully_populated_player(self):
        player = Player(
            id="503642", name_english="Sagiv Jehezkel", name_hebrew="שגיב יחזקאל",
            nationality=["Israel"], birth_date=date(2000, 1, 14),
            birth_place="Be'er Sheva, Israel", main_position="Attacking Midfield",
            current_squad=True, current_jersey_number=10, homegrown=True, retired=False,
            preferred_foot="right", height_cm=178, contract_expires="30/06/2027",
            is_captain=True, current_market_value="€2.50m",
            other_positions=["Second Striker", "Left Winger"],
        )
        assert player.preferred_foot == "right"
        assert player.height_cm == 178
        assert player.is_captain is True
        assert player.current_market_value == "€2.50m"
        assert "Left Winger" in player.other_positions


class TestTransferR2Additions:
    def test_tm_ids_default_null(self):
        t = Transfer(
            player_id="503642", season="2022/23", transfer_date="Aug 1, 2022",
            from_club="Hapoel Beer Sheva U19", to_club="Hapoel Beer Sheva",
            fee="-", loan=False,
        )
        assert t.from_club_tm_id is None
        assert t.to_club_tm_id is None

    def test_tm_ids_populated(self):
        t = Transfer(
            player_id="503642", season="2024", transfer_date="Jul 1, 2024",
            from_club="Hapoel Beer Sheva", to_club="Maccabi Tel Aviv",
            fee="€1.00m", loan=False,
            from_club_tm_id="2976", to_club_tm_id="869",
        )
        assert t.from_club_tm_id == "2976"
        assert t.to_club_tm_id == "869"


class TestCoachR2Additions:
    def test_phase_3a_r2_defaults(self):
        c = Coach(id="96723", name="Ran Kozuch", role="Manager")
        assert c.is_caretaker is False
        assert c.tenure_seasons == []
        assert c.hbs_trophies_won == []

    def test_caretaker_with_trophies(self):
        c = Coach(
            id="12345", name="Test Coach", role="Manager",
            is_caretaker=True,
            tenure_seasons=["2015", "2016", "2017"],
            hbs_trophies_won=[
                "Israeli Champion 2015/16",
                "Israeli Champion 2016/17",
                "Israeli Super Cup 2016/17",
            ],
        )
        assert c.is_caretaker is True
        assert len(c.tenure_seasons) == 3
        assert "Israeli Champion 2015/16" in c.hbs_trophies_won


class TestMatchSchema:
    def test_minimal_match(self):
        m = Match(season="2024")
        assert m.season == "2024"
        assert m.aet is False
        assert m.referee is None
        assert m.var_referee is None

    def test_full_match_with_referee_team(self):
        m = Match(
            season="2024", competition="Ligat ha'Al", matchday="6",
            date="Sun, 06/10/24", time="7:15 PM", venue="H",
            opponent="H. Jerusalem", result="3:0", attendance="7.510",
            halftime_score="1:0", aet=False, stadium="Toto Jacob Turner Stadium",
            referee="Yoav Mizrahi",
            assistant_referee_1="Reut Hadar", assistant_referee_2="Hadar Ben Eliyahu",
            fourth_official="Eitan Shamir",
            var_referee="Eli Hakmon", var_assistant="Eliyahu Kaspo",
        )
        assert m.referee == "Yoav Mizrahi"
        assert m.assistant_referee_1 == "Reut Hadar"
        assert m.var_referee == "Eli Hakmon"
        assert m.halftime_score == "1:0"


class TestSeasonStandingSchema:
    def test_minimal(self):
        s = SeasonStanding(season="2024", competition="Ligat ha'Al")
        assert s.tier is None
        assert s.final_position is None

    def test_full(self):
        s = SeasonStanding(
            season="2024", competition="Ligat ha'Al", tier=1,
            final_position=1, played=24, wins=17, draws=4, losses=3,
            goals_for=54, goals_against=23, points=55,
        )
        assert s.final_position == 1
        assert s.points == 55


class TestHeadToHeadSchema:
    def test_minimal(self):
        h = HeadToHead(opponent="Maccabi Tel Aviv")
        assert h.played == 0
        assert h.wins == 0

    def test_full(self):
        h = HeadToHead(
            opponent="Maccabi Tel Aviv", opponent_tm_id="869",
            played=121, wins=35, draws=42, losses=44,
            goals_for=140, goals_against=160, avg_attendance=12500,
        )
        assert h.opponent_tm_id == "869"
        assert h.played == 121
        assert h.wins + h.draws + h.losses == 121


class TestSeasonManagerSchema:
    def test_full(self):
        sm = SeasonManager(
            season="2015", coach_id="68820", coach_name="Barak Bakhar",
            played=46, wins=30, draws=11, losses=5, ppm="2.20",
        )
        assert sm.coach_id == "68820"
        assert sm.is_caretaker is False
        assert sm.ppm == "2.20"
