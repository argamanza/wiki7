"""Tests for data_pipeline.normalize_enrich_players end-to-end."""

import json
import tempfile
from pathlib import Path


from data_pipeline.normalize_enrich_players import (
    main, normalize_player, normalize_transfers, normalize_market_values, normalize_stats,
    _parse_preferred_foot, _parse_height_cm, _latest_market_value,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"


class TestNormalizePlayer:
    def test_normalize_basic_player(self):
        raw = {
            "name_english": "Test Player",
            "profile_url": "https://www.transfermarkt.com/test/profil/spieler/12345",
            "number": "7",
            "season": "2024",
            "loaned": False,
            "facts": {
                "Date of birth/Age": "Jan 1, 1995 (29)",
                "Place of birth": "Tel Aviv, Israel",
                "Citizenship": "Israel",
            },
            "positions": {"main": "Centre-Forward", "others": []},
            "transfers": [],
            "market_value_history": [],
        }
        player = normalize_player(raw)
        assert player.id == "12345"
        assert player.name_english == "Test Player"
        assert player.current_jersey_number == 7
        assert player.current_squad is True

    def test_normalize_loaned_player(self):
        raw = {
            "name_english": "Loaned Player",
            "profile_url": "https://www.transfermarkt.com/test/profil/spieler/99999",
            "number": "-",
            "season": "2024",
            "loaned": True,
            "facts": {},
            "positions": {},
            "transfers": [],
            "market_value_history": [],
        }
        player = normalize_player(raw)
        assert player.current_squad is False
        assert player.current_jersey_number is None

    def test_normalize_hebrew_name(self):
        raw = {
            "name_english": "Sagiv Jehezkel",
            "profile_url": "https://www.transfermarkt.com/test/profil/spieler/503642",
            "number": "10",
            "season": "2024",
            "loaned": False,
            "facts": {
                "Name in home country": "שגיב יחזקאל",
            },
            "positions": {},
            "transfers": [],
            "market_value_history": [],
        }
        player = normalize_player(raw)
        assert player.name_hebrew == "שגיב יחזקאל"


class TestPlayerR2Fields:
    """Phase 3a R2: new player fields (preferred_foot, height_cm,
    contract_expires, current_market_value, other_positions) flow from
    raw spider output through normalization."""

    def test_full_r2_player(self):
        raw = {
            "name_english": "Sagiv Jehezkel",
            "profile_url": "https://www.transfermarkt.com/test/profil/spieler/503642",
            "number": "10",
            "season": "2024",
            "loaned": False,
            "facts": {
                "Foot": "right",
                "Height": "1,78 m",
                "Contract expires": "30/06/2027",
            },
            "positions": {
                "main": "Attacking Midfield",
                "other": ["Second Striker", "Left Winger"],
            },
            "transfers": [],
            "market_value_history": [
                {"date": "Jun 2023", "value": "€1.50m", "team": "Hapoel Beer Sheva"},
                {"date": "Dec 2024", "value": "€2.50m", "team": "Hapoel Beer Sheva"},
            ],
            # Phase 3a R2: squad spider always emits is_captain=False; the
            # actual current-captain derivation happens elsewhere.
            "is_captain": False,
        }
        player = normalize_player(raw)
        assert player.preferred_foot == "right"
        assert player.height_cm == 178
        assert player.contract_expires == "30/06/2027"
        assert player.current_market_value == "€2.50m"
        assert player.other_positions == ["Second Striker", "Left Winger"]
        assert player.is_captain is False

    def test_pre_2003_player_has_no_market_value(self):
        """1985/86-era player: no market_value_history rows. current_market_value
        normalises to None so the infobox can skip rendering the row."""
        raw = {
            "name_english": "Historical Player",
            "profile_url": "https://www.transfermarkt.com/test/profil/spieler/99",
            "number": "-",
            "season": "1985",
            "loaned": False,
            "facts": {},
            "positions": {},
            "transfers": [],
            "market_value_history": [],
        }
        player = normalize_player(raw)
        assert player.current_market_value is None
        assert player.preferred_foot is None
        assert player.height_cm is None
        assert player.other_positions == []


class TestR2Helpers:
    """Helpers behind the R2 player fields. Tested standalone so the
    parsing rules are pinned independent of the full normalize_player path.
    """

    def test_preferred_foot(self):
        assert _parse_preferred_foot("right") == "right"
        assert _parse_preferred_foot("Left") == "left"
        assert _parse_preferred_foot("BOTH") == "both"
        assert _parse_preferred_foot("") is None
        assert _parse_preferred_foot(None) is None
        # Unknown values normalise to None rather than passing through.
        assert _parse_preferred_foot("ambidextrous") is None

    def test_height_cm_european_comma(self):
        assert _parse_height_cm("1,78 m") == 178

    def test_height_cm_anglo_dot(self):
        assert _parse_height_cm("1.78 m") == 178

    def test_height_cm_no_unit_suffix(self):
        assert _parse_height_cm("1.95") == 195

    def test_height_cm_missing(self):
        assert _parse_height_cm("") is None
        assert _parse_height_cm(None) is None

    def test_height_cm_malformed(self):
        assert _parse_height_cm("very tall") is None

    def test_latest_market_value(self):
        history = [
            {"date": "Jun 2023", "value": "€1.50m", "team": "Hapoel Beer Sheva"},
            {"date": "Dec 2024", "value": "€2.50m", "team": "Hapoel Beer Sheva"},
        ]
        assert _latest_market_value(history) == "€2.50m"

    def test_latest_market_value_empty(self):
        assert _latest_market_value([]) is None


class TestNormalizeTransfers:
    def test_basic_transfer(self):
        raw = {
            "profile_url": "https://www.transfermarkt.com/test/profil/spieler/12345",
            "transfers": [
                {
                    "season": "2022/23",
                    "date": "Aug 1, 2022",
                    "from": "Club A",
                    "to": "Club B",
                    "fee": "€1m",
                }
            ],
        }
        transfers = normalize_transfers(raw)
        assert len(transfers) == 1
        assert transfers[0].player_id == "12345"
        assert transfers[0].loan is False

    def test_loan_detection(self):
        raw = {
            "profile_url": "https://www.transfermarkt.com/test/profil/spieler/12345",
            "transfers": [
                {
                    "season": "2023/24",
                    "date": "Jan 1, 2024",
                    "from": "Club A",
                    "to": "Club B",
                    "fee": "Loan fee: €100k",
                }
            ],
        }
        transfers = normalize_transfers(raw)
        assert transfers[0].loan is True


class TestNormalizeMarketValues:
    def test_basic_market_values(self):
        raw = {
            "profile_url": "https://www.transfermarkt.com/test/profil/spieler/12345",
            "market_value_history": [
                {"date": "Dec 2023", "value": "€2.50m", "team": "Hapoel Beer Sheva"},
            ],
        }
        mvs = normalize_market_values(raw)
        assert len(mvs) == 1
        assert mvs[0].value == "€2.50m"


class TestNormalizeStats:
    def test_normalize_stats(self):
        raw_stats = [
            {
                "player_id": "503642",
                "season": "2024",
                "appearances": 30,
                "goals": 8,
                "assists": 5,
                "yellow_cards": 3,
                "red_cards": 0,
                "minutes_played": 2450,
            }
        ]
        stats = normalize_stats(raw_stats)
        assert len(stats) == 1
        assert stats[0].player_id == "503642"
        assert stats[0].appearances == 30
        assert stats[0].goals == 8

    def test_normalize_stats_defaults(self):
        raw_stats = [{"player_id": "123", "season": "2023"}]
        stats = normalize_stats(raw_stats)
        assert stats[0].appearances == 0
        assert stats[0].goals == 0

    def test_normalize_stats_empty(self):
        stats = normalize_stats([])
        assert stats == []


class TestMainFunction:
    def test_end_to_end_with_fixtures(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            main(
                raw_path=str(FIXTURES_DIR / "players_sample.json"),
                out_dir=tmpdir,
            )
            out = Path(tmpdir)
            assert (out / "players.jsonl").exists()
            assert (out / "transfers.jsonl").exists()
            assert (out / "market_values.jsonl").exists()

            with open(out / "players.jsonl") as f:
                players = [json.loads(line) for line in f if line.strip()]
            assert len(players) == 3
            assert players[0]["name_english"] == "Sagiv Jehezkel"
            assert players[0]["name_hebrew"] == "שגיב יחזקאל"

            with open(out / "transfers.jsonl") as f:
                transfers = [json.loads(line) for line in f if line.strip()]
            assert len(transfers) >= 1

            with open(out / "market_values.jsonl") as f:
                mvs = [json.loads(line) for line in f if line.strip()]
            assert len(mvs) >= 1

    def test_end_to_end_with_stats(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            main(
                raw_path=str(FIXTURES_DIR / "players_sample.json"),
                stats_path=str(FIXTURES_DIR / "stats_sample.json"),
                out_dir=tmpdir,
            )
            out = Path(tmpdir)
            assert (out / "stats.jsonl").exists()

            with open(out / "stats.jsonl") as f:
                stats = [json.loads(line) for line in f if line.strip()]
            assert len(stats) == 3
            assert stats[0]["player_id"] == "503642"
