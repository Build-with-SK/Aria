"""
tests/test_research_integration.py
==================================
Research reaches the trade path only as recorded context, and cannot reach
execution at all.

    MARKET DATA -> SNAPSHOT -> EVIDENCE -> THESIS -> MODEL PREDICTIONS
      -> ROUTING -> CONVICTION -> PORTFOLIO RISK -> SESSION -> EXECUTION

  * the conviction for a desk trade cites the cached thesis as AGREES /
    CONTRADICTS / NEUTRAL / UNKNOWN, and a thesis built after the instant,
    or too old, is UNKNOWN;
  * a CONTRADICTING thesis changes neither the verdict nor the size;
  * the research package imports nothing that executes, sizes or reaches a
    language model, and runs with the pre-trade gate disabled.
"""
import ast
import json
import sys
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

pytest.importorskip("exchange_calendars")

import research_fixtures as F  # noqa: E402
from src.intelligence import conviction as CV  # noqa: E402
from src.intelligence.research import engine  # noqa: E402


def thesis(direction="BULLISH", built=None):
    return {"symbol": "AAPL", "built_at": (built or F.T - timedelta(hours=1)).isoformat(),
            "direction": direction, "basis": "MULTIPLE_INDEPENDENT_GROUPS",
            "summary": "s", "scorecard": {"EVENT_RISK": "HIGH"}}


@pytest.mark.parametrize("direction,th,state", [
    ("LONG", thesis("BULLISH"), "AGREES"),
    ("LONG", thesis("BEARISH"), "CONTRADICTS"),
    ("SHORT", thesis("BEARISH"), "AGREES"),
    ("LONG", thesis("NEUTRAL"), "NEUTRAL"),
    ("LONG", None, "UNKNOWN"),
    ("LONG", thesis("BULLISH", built=F.T + timedelta(minutes=1)), "UNKNOWN"),
    ("LONG", thesis("BULLISH", built=F.T - timedelta(hours=30)), "UNKNOWN"),
])
def test_research_context_states(direction, th, state):
    rc = CV.research_context(direction, th, F.T)
    assert rc["state"] == state
    if state in ("AGREES", "CONTRADICTS", "NEUTRAL"):
        assert "context only" in rc["effect"]


def _transcript(view="bull"):
    return {"id": "dbt-0000beef", "ticker": "AAPL", "llm": "t",
            "at": (F.T - timedelta(hours=1)).astimezone().replace(tzinfo=None).isoformat(),
            "opinions": [{"agent": "technical", "view": view, "conviction": 70}],
            "judge": {"view": view, "conviction": 70, "conviction_bar": 65, "verdict": "BUY",
                      "regime": "x"}}


def test_a_contradicting_thesis_is_labelled_but_changes_no_verdict_or_size():
    from test_portfolio_risk import LOOSE, T as PT_T, acct, build_frames, order
    from src.markets import calendars, providers
    from src.risk import pretrade
    from src.risk.market_data import FrameMarketData
    providers.default_chain()
    calendars.clear_cache()
    frames = build_frames()
    md = FrameMarketData(frames)
    tr = dict(_transcript(), at=(PT_T - timedelta(hours=1)).astimezone().replace(tzinfo=None)
              .isoformat())
    agree = CV.from_debate(tr, as_of=PT_T, snapshot=None, signals_age_hours=1,
                           research=dict(thesis("BULLISH"), built_at=(PT_T - timedelta(hours=1))
                                         .isoformat())).to_dict()
    contra = CV.from_debate(tr, as_of=PT_T, snapshot=None, signals_age_hours=1,
                            research=dict(thesis("BEARISH"), built_at=(PT_T - timedelta(hours=1))
                                          .isoformat())).to_dict()
    assert contra["research_context"]["state"] == "CONTRADICTS"
    assert "RESEARCH CONTRADICTS" in contra["labels"]
    a = pretrade.assess(order(frames, "AAPL", 10, conviction=agree), acct(), at=PT_T,
                        limits=LOOSE, data=md)
    b = pretrade.assess(order(frames, "AAPL", 10, conviction=contra), acct(), at=PT_T,
                        limits=LOOSE, data=md)
    assert (a.verdict, a.approved_qty) == (b.verdict, b.approved_qty)
    assert b.data["conviction"]["research_context"]["state"] == "CONTRADICTS"
    providers.default_chain()


def test_a_desk_trade_reads_the_cached_thesis_without_any_network(monkeypatch, tmp_path):
    from src.desk import debate
    from src.v5 import marketdata as md
    monkeypatch.setattr(debate, "DEBATES_DIR", tmp_path / "debates")
    (tmp_path / "debates").mkdir()
    (tmp_path / "debates" / "dbt-0000beef.json").write_text(json.dumps(_transcript()),
                                                            encoding="utf-8")
    monkeypatch.setattr(engine, "LATEST_DIR", tmp_path / "latest")
    (tmp_path / "latest").mkdir()
    (tmp_path / "latest" / "AAPL.json").write_text(json.dumps(thesis("BEARISH")),
                                                   encoding="utf-8")

    def boom(*a, **k):
        raise AssertionError("network on the order path")
    monkeypatch.setattr(md, "history", boom)
    monkeypatch.setattr(md, "provider_closes", boom)
    trade = SimpleNamespace(id="t", situation="ARIA desk cycle — debate dbt-0000beef")
    c = CV.for_queued_trade(trade, as_of=F.T, snapshot={})
    assert c["research_context"]["state"] == "CONTRADICTS"


def test_a_frame_report_is_never_saved_for_the_order_path(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "LATEST_DIR", tmp_path / "latest")
    rep = engine.research("AAPL", at=F.T, data=F.world(), horizon_days=21, analogues=False)
    assert engine.save_latest(rep) is None
    assert not (tmp_path / "latest").exists()


def test_research_runs_with_the_trade_gate_disabled(monkeypatch):
    from src.risk import pretrade

    def forbidden(*a, **k):
        raise AssertionError("research called the pre-trade gate")
    monkeypatch.setattr(pretrade, "assess", forbidden)
    rep = engine.research("AAPL", at=F.T, data=F.world(), horizon_days=21, analogues=False)
    assert rep["thesis"]["executable"] is False


FORBIDDEN = ("src.trading", "src.execution", "src.risk.pretrade", "src.brain",
             "src.inference", "src.consult", "ollama", "openai", "anthropic")


@pytest.mark.parametrize("path", sorted((ROOT / "src" / "intelligence" / "research").glob("*.py")),
                         ids=lambda p: p.name)
def test_the_research_package_cannot_reach_execution_or_a_model(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
        for n in names:
            assert not any(n == f or n.startswith(f + ".") for f in FORBIDDEN), (path.name, n)
