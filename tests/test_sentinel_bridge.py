"""
tests/test_sentinel_bridge.py
=============================
ARIA consults SENTINEL. It never depends on it.

The client existed for weeks without a caller — written, tokened, given a port,
and never invoked. A bridge with no traffic is a plan, not an integration, so
these tests exercise the wiring rather than the transport.

THE PROPERTY THAT MATTERS MOST
------------------------------
**Silence is not agreement.** An unreachable consultant must never read as
approval, because that failure looks exactly like success: the trade goes
through, nothing errors, and nobody learns that no second opinion was obtained.
Several tests below exist only to pin that.

TWO SYSTEMS
-----------
ARIA imports no SENTINEL code and reads no SENTINEL state. The whole surface is
HTTP, and a test asserts that too — a future convenience import is exactly how
"separate systems" quietly becomes one.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from src.consult import bridge
from src.consult import sentinel_client as sc


@pytest.fixture(autouse=True)
def _no_auto_consult(monkeypatch):
    """Automatic consultation is off by default; tests opt in explicitly."""
    monkeypatch.delenv(bridge.ENV_FLAG, raising=False)
    yield


# ── the gate: asking is a decision with a cost ─────────────────────────────

def test_a_routine_confident_call_is_not_worth_consulting():
    worth_it, why = sc.should_consult(own_confidence=0.9)
    assert worth_it is False
    assert "itself" in why


@pytest.mark.parametrize("gate", [
    {"outside_domain": True},
    {"conflicting_evidence": True},
    {"anomaly": True},
    {"unfamiliar": True},
    {"own_confidence": 0.2},
    {"high_impact": True, "own_confidence": 0.6},
])
def test_the_cases_that_do_warrant_a_second_opinion(gate):
    worth_it, why = sc.should_consult(**gate)
    assert worth_it is True and why


def test_a_high_impact_call_held_firmly_does_not_need_review():
    """High impact alone is not enough — firm confidence on a known problem is
    exactly what ARIA is for."""
    worth_it, _ = sc.should_consult(high_impact=True, own_confidence=0.9)
    assert worth_it is False


def test_the_gate_short_circuits_before_any_network_call(monkeypatch):
    called = []
    monkeypatch.setattr(sc, "is_available", lambda *a, **k: called.append("live"))
    monkeypatch.setattr(sc, "consult", lambda *a, **k: called.append("consult"))
    out = bridge.ask("routine question", gate={"own_confidence": 0.95})
    assert out["consulted"] is False
    assert out["reason"] == "not_warranted"
    assert not called, "the gate let a network call through"


# ── silence is not agreement ───────────────────────────────────────────────

def test_an_unreachable_sentinel_is_never_reported_as_agreement(monkeypatch):
    monkeypatch.setattr(sc, "is_available", lambda *a, **k: False)
    monkeypatch.setattr(sc, "token_configured", lambda: True)

    out = bridge.ask("does this thesis hold?")
    assert out["ok"] is False
    assert out["consulted"] is False
    assert out["reason"] == "unreachable"
    assert "NOT agreement" in out["guidance"], (
        "an absent consultant must say so — this failure looks exactly like "
        "success if it does not")


def test_a_missing_token_is_never_reported_as_agreement(monkeypatch):
    monkeypatch.setattr(sc, "token_configured", lambda: False)
    out = bridge.ask("anything")
    assert out["ok"] is False and out["consulted"] is False
    assert "NOT agreement" in out["guidance"]


def test_the_client_itself_refuses_to_imply_agreement_on_failure(monkeypatch):
    monkeypatch.setattr(sc, "_post", lambda *a, **k: {
        "ok": False, "reason": "unreachable", "detail": "refused"})
    out = sc.consult("question")
    assert out["ok"] is False
    assert "NOT agreement" in out["guidance"]


def test_summarise_says_no_second_opinion_rather_than_nothing():
    text = sc.summarise({"ok": False, "reason": "unreachable"})
    assert "No second opinion" in text
    assert "own analysis" in text


# ── never blocking, never raising ──────────────────────────────────────────

def test_consultation_is_off_by_default_on_the_trading_path():
    out = bridge.consider_trade(ticker="AAPL", side="buy", thesis="momentum",
                                conviction=70)
    assert out["consulted"] is False
    assert out["reason"] == "disabled"
    assert bridge.ENV_FLAG in out["why"]


def test_an_exploding_consultant_cannot_break_the_trading_path(monkeypatch):
    monkeypatch.setenv(bridge.ENV_FLAG, "true")

    def boom(*a, **k):
        raise RuntimeError("consultant on fire")
    monkeypatch.setattr(bridge, "red_team_thesis", boom)

    out = bridge.consider_trade(ticker="AAPL", side="buy", thesis="t",
                                conviction=90)
    assert out["ok"] is False and out["consulted"] is False
    assert out["reason"] == "error"
    assert "NOT agreement" in out["guidance"]


def test_a_dead_consultant_costs_a_liveness_check_not_a_full_timeout(monkeypatch):
    """Without the cheap check every caller pays the long window to learn
    nothing is there."""
    monkeypatch.setattr(sc, "token_configured", lambda: True)
    monkeypatch.setattr(sc, "is_available", lambda *a, **k: False)
    reached = []
    monkeypatch.setattr(sc, "consult", lambda *a, **k: reached.append(1))

    bridge.ask("question")
    assert not reached, "a full consult was attempted against a dead endpoint"


def test_a_consultation_probes_liveness_exactly_once(monkeypatch):
    """Whether a token exists is answerable locally. Learning it from status(),
    which also probes /health, made every consultation pay two liveness checks
    — and an absent consultant cost twice the timeout to establish the same
    fact twice, on the trading path."""
    probes = []
    monkeypatch.setattr(sc, "is_available",
                        lambda *a, **k: (probes.append(1), False)[1])
    monkeypatch.setattr(sc, "token_configured", lambda: True)

    bridge.ask("does this hold?")
    assert len(probes) == 1, f"SENTINEL was probed {len(probes)} times, not once"


def test_the_trade_timeout_is_shorter_than_the_client_default():
    assert bridge.TRADE_TIMEOUT < sc.TIMEOUT, (
        "a consultant that takes the full window has already missed the "
        "decision it was asked about")


# ── a successful consultation ──────────────────────────────────────────────

def _answer():
    return {"ok": True, "request_id": "req-1", "confidence": 0.6,
            "independent_analysis": "The momentum read ignores the earnings gap.",
            "counter_arguments": [{"kind": "evidence", "point": "volume is thin"}],
            "alternative_hypotheses": [], "uncertainties": ["forward guidance"],
            "recommendation": "size smaller"}


def test_a_successful_consultation_is_summarised_and_flagged(monkeypatch):
    monkeypatch.setattr(sc, "token_configured", lambda: True)
    monkeypatch.setattr(sc, "is_available", lambda *a, **k: True)
    monkeypatch.setattr(sc, "consult", lambda *a, **k: _answer())

    out = bridge.ask("does this hold?")
    assert out["ok"] is True and out["consulted"] is True
    assert "SENTINEL" in out["summary"]
    assert "volume is thin" in out["summary"], "objections were dropped"


def test_objections_survive_into_the_summary_a_human_reads(monkeypatch):
    """The point of a red team is the objections, not the verdict."""
    text = sc.summarise(_answer())
    assert "Objections raised" in text
    assert "size smaller" in text


# ── advisory only ──────────────────────────────────────────────────────────

def test_the_planner_attaches_the_review_without_letting_it_block(monkeypatch):
    """SENTINEL must not gain a veto. A consultant that could stop a trade
    would be a second decision maker, and the approval queue exists so there
    is exactly one."""
    src = Path("src/brain/cognitive/planner.py").read_text(encoding="utf-8")
    block = src[src.index("consider_trade"):]
    block = block[:block.index("planned.append")]
    for veto in ("continue", "return", "raise", "skip"):
        assert f"\n                {veto}" not in block, (
            f"the consultation path can '{veto}' — that is a veto, not advice")
    # The review must end up in the string the approving human reads. Asserted
    # by SHAPE rather than by exact source text, so reformatting the f-string
    # does not fail a test about behaviour.
    import re
    reassigns = re.findall(r"thesis\s*=\s*\(?f?\"", block)
    assert reassigns, "the consultation block never writes back to `thesis`"
    assert "summary" in block or "review" in block, (
        "the consultation result is fetched and then discarded — the human "
        "approving the trade never sees it")


def test_an_unreachable_consultant_is_visible_to_the_approving_human():
    src = Path("src/brain/cognitive/planner.py").read_text(encoding="utf-8")
    assert "not agreement" in src.lower(), (
        "when no review happened, the human approving the trade is not told")


# ── two systems, not one ───────────────────────────────────────────────────

def test_aria_imports_no_sentinel_internals():
    """The entire coupling is HTTP. A convenience import is how two separate
    systems quietly become one."""
    for path in Path("src").rglob("*.py"):
        if path.parts[:2] == ("src", "consult"):
            continue
        # utf-8-sig, not utf-8: thirteen empty package markers in this repo
        # carried a UTF-8 BOM, and `ast.parse` rejects it as a non-printable
        # character even though Python imports the file happily. The BOMs were
        # stripped; reading tolerantly means a re-introduced one is a cosmetic
        # issue rather than a failing test in an unrelated suite.
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            mod = ""
            if isinstance(node, ast.ImportFrom):
                mod = node.module or ""
            elif isinstance(node, ast.Import):
                mod = ",".join(a.name for a in node.names)
            assert "sentinel" not in mod.lower() or "consult" in mod.lower(), (
                f"{path} imports SENTINEL internals ({mod}); the coupling is "
                f"supposed to be one HTTP call")


def test_the_bridge_talks_over_http_and_nothing_else():
    src = Path("src/consult/sentinel_client.py").read_text(encoding="utf-8")
    assert "urllib.request" in src
    for forbidden in ("import sqlite3", "chromadb", "from src.brain"):
        assert forbidden not in src, (
            f"the client reaches into {forbidden} — it is meant to be a "
            f"transport, not a shared runtime")


def test_status_explains_why_consultation_is_unavailable():
    st = bridge.status()
    for key in ("url", "token_configured", "reachable", "operational",
                "auto_consult_enabled", "blocked_by"):
        assert key in st, f"status omits {key}"
    if not st["operational"]:
        assert st["blocked_by"], "unavailable with no reason given"
