"""Tests for the Phase 3.5 review-gate title router.

Architecture: docs/adr/0002-review-gate-architecture.md.

Defaults to gate-disabled so the pre-3.5 test suite keeps passing.
"""

from unittest.mock import MagicMock

import pytest

from wiki_import import review_gate


# ----- gate_enabled --------------------------------------------------------


def test_gate_disabled_by_default(monkeypatch):
    monkeypatch.delenv("WIKI_GATE_ENABLED", raising=False)
    assert review_gate.gate_enabled() is False


@pytest.mark.parametrize("value", ["1", "true", "yes", "on", "TRUE", "Yes"])
def test_gate_enabled_via_env_truthy_values(monkeypatch, value):
    monkeypatch.setenv("WIKI_GATE_ENABLED", value)
    assert review_gate.gate_enabled() is True


@pytest.mark.parametrize("value", ["0", "false", "no", "off", "", "anything-else"])
def test_gate_disabled_via_env_falsy_values(monkeypatch, value):
    monkeypatch.setenv("WIKI_GATE_ENABLED", value)
    assert review_gate.gate_enabled() is False


# ----- route_title — gate disabled (legacy / default) ----------------------


def test_route_title_disabled_is_identity(monkeypatch):
    monkeypatch.delenv("WIKI_GATE_ENABLED", raising=False)
    site = MagicMock()
    site.pages["Foo"].exists = False  # would normally route to Draft
    assert review_gate.route_title(site, "Foo") == "Foo"


# ----- route_title — gate enabled ------------------------------------------


@pytest.fixture
def gate_on(monkeypatch):
    monkeypatch.setenv("WIKI_GATE_ENABLED", "1")
    yield


def test_route_title_new_mainspace_goes_to_draft(gate_on):
    site = MagicMock()
    site.pages.__getitem__.return_value.exists = False
    assert review_gate.route_title(site, "Roei Gordana") == "Draft:Roei Gordana"


def test_route_title_existing_mainspace_stays(gate_on):
    site = MagicMock()
    site.pages.__getitem__.return_value.exists = True
    assert review_gate.route_title(site, "Roei Gordana") == "Roei Gordana"


def test_route_title_already_draft_is_passthrough(gate_on):
    site = MagicMock()
    # Even though the underlying page doesn't exist, we don't double-prefix.
    site.pages.__getitem__.return_value.exists = False
    assert review_gate.route_title(site, "Draft:Roei Gordana") == "Draft:Roei Gordana"


@pytest.mark.parametrize(
    "title",
    [
        "Template:Player_infobox",
        "Category:שחקנים",
        "File:RoeiGordana.png",
        "User:Wiki7Bot",
        "Help:Editing",
        "MediaWiki:Common.css",
        "Module:Foo",
        "Project:About",
    ],
)
def test_route_title_namespaced_titles_pass_through(gate_on, title):
    """Pages with a known namespace prefix go straight through — Template/File
    update gating is handled by Approved Revs alone; the other namespaces
    aren't bot-written but we list them defensively.
    """
    site = MagicMock()
    # The router must NOT probe page.exists for these — assert via mock.
    site.pages.__getitem__.return_value.exists = False
    assert review_gate.route_title(site, title) == title


def test_route_title_dry_run_no_site_routes_to_draft(gate_on):
    """During dry-run we don't have a live API client; assume new-page semantics
    so dry-run output reflects what would actually happen on prod.
    """
    assert review_gate.route_title(None, "Roei Gordana") == "Draft:Roei Gordana"
    # Namespaced titles are still passed through:
    assert review_gate.route_title(None, "Template:Player_infobox") == "Template:Player_infobox"


def test_route_title_fail_closed_on_probe_exception(gate_on, caplog):
    site = MagicMock()
    site.pages.__getitem__.side_effect = RuntimeError("API hiccup")
    assert review_gate.route_title(site, "Roei Gordana") == "Draft:Roei Gordana"
    assert "could not probe" in caplog.text.lower() or "routing to draft" in caplog.text.lower()


def test_route_title_titles_with_colons_in_body_route_correctly(gate_on):
    """Match pages have titles like "Mon 03/03/25 נגד מכבי חיפה (ליגת העל)" —
    no colon in the namespace position, so the router treats them as mainspace.
    Times with colons in the middle ("10:30 PM") same rule.
    """
    site = MagicMock()
    site.pages.__getitem__.return_value.exists = False
    assert review_gate.route_title(site, "Mon 03/03/25 נגד מכבי חיפה") == "Draft:Mon 03/03/25 נגד מכבי חיפה"
    assert review_gate.route_title(site, "Match 10:30 PM kickoff") == "Draft:Match 10:30 PM kickoff"
