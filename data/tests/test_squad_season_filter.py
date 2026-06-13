"""Yellow-triage fix tests (reviewer-pass, 2026-06-13): the §6 ③ slice 5
squad-page season filter (`import_squad_page` filters merged players to
those active in the target season) had no direct test — covered only
by the upstream `merge_players` test that stamps `seasons_active`. The
filter logic itself was untested. Pin it here."""

from pathlib import Path
from unittest.mock import patch



def _player(pid, seasons_active=None, **extras):
    """Build a minimal player record matching the shape `_group_players_by_position`
    + the season filter expect."""
    base = {
        "id": pid,
        "name_english": f"Player {pid}",
        "name_hebrew": f"שחקן {pid}",
        "current_squad": True,
        "current_jersey_number": 1,
        "main_position": "Forward",
        "homegrown": False,
        "retired": False,
    }
    if seasons_active is not None:
        base["seasons_active"] = seasons_active
    base.update(extras)
    return base


class TestSquadSeasonFilter:
    def test_filter_to_target_season_drops_inactive_players(self, tmp_path: Path):
        """A merged players file with seasons_active should be filtered
        to only players active in the target season. The pre-fix bug
        rendered the entire all-time roster on every season page."""
        # Three players:
        #   A: active 2002 + 2010 (NOT in 2024)
        #   B: active 2024 only
        #   C: active 2010 + 2024
        players_file = tmp_path / "players.he.jsonl"
        with open(players_file, "w", encoding="utf-8") as f:
            import json
            for p in [
                _player("A", seasons_active=["2002", "2010"]),
                _player("B", seasons_active=["2024"]),
                _player("C", seasons_active=["2010", "2024"]),
            ]:
                f.write(json.dumps(p, ensure_ascii=False) + "\n")

        from wiki_import.import_templates import import_squad_page

        # Capture players that reach the renderer by patching the render
        # path. We don't care about the rendered output — just the
        # filtered roster.
        rendered_with = {}

        def fake_render(template_name, **kwargs):
            rendered_with["players_by_position"] = kwargs.get("players_by_position")
            return "fake content"

        # We also need to short-circuit the wiki-write path. Use dry_run.
        with patch("wiki_import.import_templates._render_template", side_effect=fake_render):
            import_squad_page(
                site=None,
                season="2024",
                players_path=players_file,
                stats_path=tmp_path / "nonexistent_stats.jsonl",
                dry_run=True,
            )

        # Should have rendered with players B and C only (active in 2024).
        # The renderer was called even in dry-run so kwargs were captured.
        rendered_groups = rendered_with["players_by_position"]
        rendered_ids = {
            p["id"]
            for group in rendered_groups.values()
            for p in group
        }
        assert rendered_ids == {"B", "C"}, (
            f"Expected only players active in 2024; got {rendered_ids}"
        )

    def test_no_seasons_active_field_skips_filter(self, tmp_path: Path):
        """Backward compat: when seasons_active is absent (legacy single-
        season pipeline run, where the file already has just that
        season's roster), the filter MUST be a no-op."""
        players_file = tmp_path / "players.he.jsonl"
        with open(players_file, "w", encoding="utf-8") as f:
            import json
            # No seasons_active — single-season iter-cycle output.
            for p in [_player("X"), _player("Y")]:
                f.write(json.dumps(p, ensure_ascii=False) + "\n")

        from wiki_import.import_templates import import_squad_page

        rendered_with = {}

        def fake_render(template_name, **kwargs):
            rendered_with["players_by_position"] = kwargs.get("players_by_position")
            return "fake content"

        with patch("wiki_import.import_templates._render_template", side_effect=fake_render):
            import_squad_page(
                site=None,
                season="2024",
                players_path=players_file,
                stats_path=tmp_path / "nonexistent_stats.jsonl",
                dry_run=True,
            )

        rendered_ids = {
            p["id"]
            for group in rendered_with["players_by_position"].values()
            for p in group
        }
        # All players present — no filter applied.
        assert rendered_ids == {"X", "Y"}
