"""Tests for Phase 3a R2 resume + idempotency semantics."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch


import run_pipeline


class TestHasUsefulData:
    def test_missing_file_returns_false(self, tmp_path: Path):
        assert run_pipeline._has_useful_data(tmp_path / "missing.json") is False

    def test_empty_list_returns_false(self, tmp_path: Path):
        path = tmp_path / "empty.json"
        path.write_text("[]")
        assert run_pipeline._has_useful_data(path) is False

    def test_non_empty_list_returns_true(self, tmp_path: Path):
        path = tmp_path / "data.json"
        path.write_text('[{"key": "value"}]')
        assert run_pipeline._has_useful_data(path) is True

    def test_malformed_json_returns_false(self, tmp_path: Path):
        path = tmp_path / "broken.json"
        path.write_text("not json at all")
        assert run_pipeline._has_useful_data(path) is False


class TestRunSpiderResume:
    """The spider runner skips when output exists with `resume=True`, and
    forces a re-run when `resume=False`."""

    @patch("run_pipeline.subprocess.run")
    def test_resume_skips_existing_output(self, mock_run, tmp_path, monkeypatch):
        monkeypatch.setattr(run_pipeline, "SCRAPER_OUTPUT_DIR", tmp_path)
        season_dir = tmp_path / "2015"
        season_dir.mkdir()
        (season_dir / "squad.json").write_text('[{"name": "Test Player"}]')

        ok = run_pipeline._run_spider("squad", "2015", "squad.json", resume=True)
        assert ok is True
        # The spider subprocess must NOT have been called — that's the whole
        # point of resume.
        mock_run.assert_not_called()

    @patch("run_pipeline.subprocess.run")
    def test_resume_runs_when_output_empty(self, mock_run, tmp_path, monkeypatch):
        """An `[]` output file (the "TM returned nothing" case) must NOT be
        treated as already-done. Otherwise a transient TM block would lock
        the season into a permanently-empty state."""
        monkeypatch.setattr(run_pipeline, "SCRAPER_OUTPUT_DIR", tmp_path)
        season_dir = tmp_path / "2015"
        season_dir.mkdir()
        (season_dir / "squad.json").write_text("[]")

        mock_run.return_value = MagicMock(returncode=0, stderr="")
        # Re-create the output after the (mocked) spider "runs" so the
        # post-run validation passes.
        def side_effect(*args, **kwargs):
            (season_dir / "squad.json").write_text('[{"name": "X"}]')
            return MagicMock(returncode=0, stderr="")
        mock_run.side_effect = side_effect

        ok = run_pipeline._run_spider("squad", "2015", "squad.json", resume=True)
        assert ok is True
        mock_run.assert_called_once()

    @patch("run_pipeline.subprocess.run")
    def test_force_rescrape_overrides_existing_output(self, mock_run, tmp_path, monkeypatch):
        monkeypatch.setattr(run_pipeline, "SCRAPER_OUTPUT_DIR", tmp_path)
        season_dir = tmp_path / "2015"
        season_dir.mkdir()
        out = season_dir / "squad.json"
        out.write_text('[{"name": "Original"}]')

        def side_effect(*args, **kwargs):
            out.write_text('[{"name": "Refreshed"}]')
            return MagicMock(returncode=0, stderr="")
        mock_run.side_effect = side_effect

        ok = run_pipeline._run_spider("squad", "2015", "squad.json", resume=False)
        assert ok is True
        mock_run.assert_called_once()
        # The file was overwritten by the mocked spider call.
        assert json.loads(out.read_text())[0]["name"] == "Refreshed"

    @patch("run_pipeline.subprocess.run")
    def test_missing_output_triggers_spider_regardless_of_resume_flag(
        self, mock_run, tmp_path, monkeypatch,
    ):
        monkeypatch.setattr(run_pipeline, "SCRAPER_OUTPUT_DIR", tmp_path)
        season_dir = tmp_path / "2015"
        season_dir.mkdir()
        out = season_dir / "squad.json"

        def side_effect(*args, **kwargs):
            out.write_text('[{"name": "X"}]')
            return MagicMock(returncode=0, stderr="")
        mock_run.side_effect = side_effect

        ok = run_pipeline._run_spider("squad", "2015", "squad.json", resume=True)
        assert ok is True
        mock_run.assert_called_once()


class TestImportIdempotency:
    """Phase 3a R2: the import step is idempotent via content-hash compare in
    _edit_page (templates, players, matches all use the same pattern).
    Re-running import on an already-imported wiki should produce zero edits
    when the rendered wikitext matches what's on the wiki.
    """

    def test_edit_page_skips_when_content_matches(self):
        from wiki_import.import_templates import _edit_page

        site = MagicMock()
        page = MagicMock()
        page.exists = True
        page.text.return_value = "existing content"
        site.pages.__getitem__.return_value = page

        # Same content → page.save must NOT be called.
        result = _edit_page(site, "TestPage", "existing content", "summary")
        assert result is False
        page.save.assert_not_called()

    def test_edit_page_saves_when_content_differs(self):
        from wiki_import.import_templates import _edit_page

        site = MagicMock()
        page = MagicMock()
        page.exists = True
        page.text.return_value = "old content"
        site.pages.__getitem__.return_value = page

        result = _edit_page(site, "TestPage", "new content", "summary")
        assert result is True
        page.save.assert_called_once()

    def test_edit_page_saves_when_page_new(self):
        from wiki_import.import_templates import _edit_page

        site = MagicMock()
        page = MagicMock()
        page.exists = False
        site.pages.__getitem__.return_value = page

        result = _edit_page(site, "NewPage", "fresh content", "summary")
        assert result is True
        page.save.assert_called_once()
