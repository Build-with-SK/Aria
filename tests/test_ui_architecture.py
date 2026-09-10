"""
tests/test_ui_architecture.py
=============================
ONE intelligence, ONE route per purpose — enforced, not merely intended.

The interface had grown six surfaces onto one daemon: "ARIA", "Live Mind",
"Cognitive Brain", a live thought stream, a memory browser and a chat. Each
looked like a peer of the others, so a user could reasonably ask "is Live Mind
thinking, or is the Brain?" — and nothing in the app answered.

Consolidation is easy to do once and easy to undo by accident, so these tests
read the actual source and fail if the shape comes back. They are deliberately
structural: they assert what EXISTS and what is REACHABLE, not how anything
looks.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

SRC = Path("frontend/src")
APP = SRC / "App.jsx"
SIDEBAR = SRC / "components" / "Sidebar.jsx"
PAGES = SRC / "pages"

pytestmark = pytest.mark.skipif(not APP.exists(), reason="frontend not present")


def _app() -> str:
    return APP.read_text(encoding="utf-8")


def _sidebar() -> str:
    return SIDEBAR.read_text(encoding="utf-8")


def _jsx_sources() -> list[Path]:
    return sorted(list(PAGES.glob("*.jsx"))
                  + list((SRC / "components").glob("*.jsx")))


def _strip_comments(text: str) -> str:
    """Only the code. A comment EXPLAINING that Live Mind was removed is not a
    Live Mind, and must not fail the test that checks it is gone."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"^\s*//.*$", "", text, flags=re.M)


# ── Live Mind is gone as a user-facing concept ──────────────────────────────

def test_no_page_named_live_mind_exists():
    for p in _jsx_sources():
        assert "livemind" not in p.name.lower().replace("-", "").replace("_", "")


def test_no_user_facing_string_says_live_mind():
    """It must be gone from what a USER sees, not hidden with CSS."""
    offenders = []
    for p in _jsx_sources():
        code = _strip_comments(p.read_text(encoding="utf-8"))
        if re.search(r"live\s*mind", code, re.I):
            offenders.append(p.name)
    assert not offenders, (
        f"'Live Mind' is still a user-facing label in {offenders}. There is one "
        f"intelligence; a second name for it recreates the confusion.")


def test_the_brain_hub_tab_shell_is_gone():
    """BrainHub was a TabBar with a 'Live mind' tab — the exact shape that made
    one daemon look like two things."""
    assert not (PAGES / "BrainHub.jsx").exists()
    assert "BrainHub" not in _app()


def test_live_mind_paths_redirect_into_the_brain():
    app = _app()
    for path in ("/thinking", "/live-mind", "/memory", "/chat"):
        assert f'path="{path}"' in app, f"{path} no longer resolves at all"
        block = app[app.index(f'path="{path}"'):][:220]
        assert "/brain" in block, (
            f"{path} does not redirect into the brain — a bookmark to the old "
            f"live mind must land on the one intelligence, not 404")


# ── one route, one purpose ─────────────────────────────────────────────────

REQUIRED_ROUTES = ("/brain", "/research", "/market", "/portfolio",
                   "/strategies", "/daily-report", "/track-record", "/system")


def test_every_workspace_has_exactly_one_destination():
    app = _app()
    for route in REQUIRED_ROUTES:
        assert f'path="{route}"' in app, f"{route} is not routed"


def test_the_rail_lists_exactly_the_eight_workspaces():
    bar = _sidebar()
    labels = re.findall(r"label:\s*'([A-Z ]+)'", bar)
    assert labels == ["BRAIN", "RESEARCH", "MARKET", "PORTFOLIO", "STRATEGIES",
                      "DAILY REPORT", "TRACK RECORD", "SYSTEM"], labels


def test_the_rail_offers_no_second_intelligence():
    """No COGNITIVE BRAIN entry beside BRAIN, no LIVE MIND, no ARIA-vs-BRAIN."""
    bar = _strip_comments(_sidebar())
    labels = set(re.findall(r"label:\s*'([A-Z ]+)'", bar))
    for banned in ("LIVE MIND", "COGNITIVE BRAIN", "THINKING", "CHAT", "MEMORY"):
        assert banned not in labels, (
            f"the rail offers '{banned}' as a destination beside BRAIN")


def test_the_landing_page_and_brain_are_the_same_destination():
    app = _app()
    root = re.search(r'path="/"\s+element=\{([^}]+)\}', app)
    brain = re.search(r'path="/brain"\s+element=\{([^}]+)\}', app)
    assert root and brain
    assert root.group(1).strip() == brain.group(1).strip(), (
        "'/' and '/brain' render different things — that is two front doors "
        "to one mind")


def test_no_route_points_at_a_deleted_page():
    app = _app()
    for module in re.findall(r"from '\./pages/(\w+)'", app) + \
                  re.findall(r"import\('\./pages/(\w+)'\)", app):
        assert (PAGES / f"{module}.jsx").exists(), (
            f"App.jsx imports pages/{module}.jsx, which does not exist")


def test_no_orphaned_page_is_left_behind():
    """A page nobody routes is dead weight that will be resurrected by accident."""
    app = _app()
    referenced = set(re.findall(r"pages/(\w+)", app))
    # Pages reached by composition rather than by route.
    composed = {"Chat", "Login", "BrainPublic"}
    for p in PAGES.glob("*.jsx"):
        name = p.stem
        if name in referenced or name in composed:
            continue
        used_elsewhere = any(
            f"pages/{name}" in q.read_text(encoding="utf-8")
            or f"from './{name}'" in q.read_text(encoding="utf-8")
            for q in _jsx_sources() if q != p)
        assert used_elsewhere, (
            f"pages/{name}.jsx is routed by nothing and imported by nothing")


# ── the Brain contains what Live Mind used to hold ─────────────────────────

def test_the_brain_page_contains_chat_memory_cognition_and_world():
    brain = (PAGES / "AriaBrain.jsx").read_text(encoding="utf-8")
    assert "Chat" in brain, "the Brain has no conversation"
    assert "LiveCognition" in brain, "the Brain does not show its reasoning"
    assert "searchMemory" in brain, "memory is not searchable from the Brain"
    assert "useBrain" in brain, "the Brain does not read the one brain state"
    for panel in ("MEMORY", "ASK ARIA", "CURRENT WORLD"):
        assert panel in brain, f"the Brain is missing its {panel} section"


def test_the_brain_header_puts_aria_above_the_regime():
    """The regime is a market condition, not the subject of the page. Putting
    'Expansion (Goldilocks)' above ARIA made a weather reading the hero."""
    brain = (PAGES / "AriaBrain.jsx").read_text(encoding="utf-8")
    assert brain.index(">\n          ARIA\n        </div>") \
        < brain.index("CURRENT REGIME"), (
        "the regime is rendered above the ARIA wordmark")


def test_the_cognition_stream_shows_activity_not_chain_of_thought():
    comp = (SRC / "components" / "LiveCognition.jsx").read_text(encoding="utf-8")
    assert "s.activity" in comp, "the stream does not render activity summaries"
    assert not re.search(r"\bs\.thought\b", comp), (
        "the cognition stream renders the model's raw narration again")


# ── daily report vs live news ──────────────────────────────────────────────

def test_the_daily_report_is_its_own_destination_not_a_card():
    assert (PAGES / "DailyReport.jsx").exists()
    assert 'path="/daily-report"' in _app()
    brain = (PAGES / "AriaBrain.jsx").read_text(encoding="utf-8")
    # The Brain may LINK to it and summarise it; it must not embed the report.
    assert 'to="/daily-report"' in brain
    assert "from './DailyReport'" not in brain and "pages/DailyReport" not in brain, (
        "the full daily report component is embedded inside the Brain again — "
        "the Brain may summarise it and link to it, not contain it")


def test_live_news_and_the_daily_report_are_different_components():
    news = SRC / "components" / "LiveNews.jsx"
    report = PAGES / "DailyReport.jsx"
    assert news.exists() and report.exists()
    assert "fetchLiveNews" in news.read_text(encoding="utf-8")
    assert "fetchLiveNews" not in report.read_text(encoding="utf-8"), (
        "the daily report polls the live feed — they are supposed to be the "
        "slow product and the fast one, not the same thing twice")


def test_the_report_page_states_when_a_day_was_never_generated():
    report = (PAGES / "DailyReport.jsx").read_text(encoding="utf-8")
    assert "REPORT NOT GENERATED" in report
    assert "NOT_GENERATED" in report


def test_live_news_renders_evidence_state_and_source_tier_separately():
    news = (SRC / "components" / "LiveNews.jsx").read_text(encoding="utf-8")
    assert "evidence_state" in news and "source_tier" in news
    assert "claim_type" in news, (
        "claim type and source tier are separate axes and both must show")


# ── the consultation is reachable, not merely configured ───────────────────
#
# The four /api/consult routes existed for a week and nothing in the interface
# called any of them. The panel could read AVAILABLE indefinitely while no
# consultation ever happened, because availability is not use — the only way to
# ask SENTINEL anything was to hold a terminal.

SYSTEM_PAGE = PAGES / "System.jsx"


def _system() -> str:
    return SYSTEM_PAGE.read_text(encoding="utf-8")


def test_the_interface_can_actually_ask_sentinel_something():
    page = _strip_comments(_system())
    assert "'/api/consult'" in page or '"/api/consult"' in page, (
        "the SENTINEL panel reports a status and offers no way to consult it")
    assert "red-team" in page, (
        "only the open question is wired; attacking a thesis is the request "
        "worth having and is the one with no button")


def test_the_owner_can_record_whether_the_advice_was_taken():
    """Rejection is a first-class outcome. It is the only signal either system
    gets about whether the bridge earns its cost, and a UI that can ask but not
    answer back leaves that record permanently empty."""
    assert "/api/consult/outcome" in _strip_comments(_system())


def test_every_consult_route_the_ui_calls_exists_in_the_backend():
    """A button wired to a route that does not exist fails at the moment of
    use, which is the worst time to discover it."""
    backend = Path("backend/main.py").read_text(encoding="utf-8-sig")
    for route in re.findall(r"/api/consult[a-z/-]*", _strip_comments(_system())):
        assert f'"{route}"' in backend, f"the UI calls {route}; the backend has no such route"


def test_a_failed_consultation_is_not_drawn_as_agreement():
    """The whole invariant, at the last place it can be broken. If SENTINEL
    does not answer, the panel must say which failure it was — a blank result
    area reads as 'nothing to object to'."""
    page = _system()
    assert "sentinel_status" in page
    assert "not agreement" in page.lower(), (
        "no wording anywhere in the panel stops silence being read as approval")


def test_no_position_is_visually_distinct_from_agreement():
    """NONE means SENTINEL took no position. Rendering it in the same colour
    as AGREE would be the interface telling the lie the backend refuses to."""
    page = _system()
    agree = re.search(r"AGREE:\s*\[([^\]]+)\]", page)
    none = re.search(r"NONE:\s*\[([^\]]+)\]", page)
    assert agree and none, "the position legend is gone"
    assert agree.group(1) != none.group(1)
