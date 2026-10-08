"""
tests/test_conviction_portfolio.py
==================================
prediction -> conviction -> portfolio risk -> session -> execution.

A conviction is context, never permission. These tests pin that:

  * it is decomposed — no single confidence number — and labels what must not
    be assumed;
  * at the pre-trade gate it can only REJECT (it does not support the side)
    or DEFER (its data are POOR); a consistent conviction leaves the verdict
    and the size exactly as they were without it, however strong it looks;
  * an exit is never blocked by a prediction;
  * the order path builds it from local files only — no network, no model —
    and the risk / trading modules never import the code that builds it.
"""
import ast
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))

pytest.importorskip("exchange_calendars")

from test_portfolio_risk import LOOSE, T, H, acct, build_frames, order  # noqa: E402
from src.intelligence import conviction as CV  # noqa: E402
from src.markets import calendars, providers  # noqa: E402
from src.risk import pretrade as PT  # noqa: E402
from src.risk.market_data import FrameMarketData  # noqa: E402
from src.risk.pretrade import Verdict  # noqa: E402


@pytest.fixture(autouse=True)
def _chain():
    providers.default_chain()
    calendars.clear_cache()
    yield
    providers.default_chain()


@pytest.fixture
def world():
    frames = build_frames()
    return frames, FrameMarketData(frames)


def transcript(view="bull", at=None, analysts=(("technical", "bull", 70),)):
    return {"id": "dbt-abcdef12", "ticker": "AAPL", "llm": "test",
            "at": (at or (T - timedelta(hours=2))).astimezone().replace(tzinfo=None).isoformat(),
            "opinions": [{"agent": a, "view": v, "conviction": c} for a, v, c in analysts],
            "judge": {"view": view, "conviction": 70, "conviction_bar": 65, "verdict": "BUY",
                      "regime": "Expansion (Goldilocks)"}}


def conv(**kw):
    return CV.from_debate(transcript(**kw), as_of=T, snapshot=None,
                          signals_age_hours=2.0).to_dict()


# ── the object ──────────────────────────────────────────────────────────────

def test_a_conviction_is_decomposed_and_labelled():
    c = conv()
    for k in ("direction", "directional_edge", "probability", "calibrated_probability",
              "calibration_quality", "model_reliability", "disagreement", "data_quality",
              "regime_fit", "routing", "probability_type"):
        assert k in c
    assert "confidence" not in c and "final_score" not in c
    assert c["may_increase_size"] is False
    assert c["probability_type"] == "CONFIDENCE"
    assert {"UNCALIBRATED", "NOT A PROBABILITY (CONFIDENCE)", "RELIABILITY UNKNOWN"} <= set(c["labels"])
    assert c["routing"]["reason_code"] == "ROUTING_INSUFFICIENT_EVIDENCE"


def test_disagreement_is_measured_from_the_voting_analysts():
    assert conv(analysts=(("technical", "bull", 60), ("fundamental", "bear", 40)))[
        "disagreement"]["value"] == "HIGH"
    assert conv(analysts=(("technical", "bull", 60), ("sentiment", "bull", 30)))[
        "disagreement"]["value"] == "LOW"
    single = conv(analysts=(("technical", "bull", 60), ("fundamental", "neutral", 0)))
    assert single["disagreement"]["status"] == "UNKNOWN"


def test_a_debate_past_its_horizon_has_poor_data():
    c = conv(at=T - timedelta(days=3))
    assert c["data_quality"]["value"] == "POOR"
    assert "horizon has elapsed" in c["data_quality"]["basis"]


def test_stale_signals_degrade_but_do_not_block():
    c = CV.from_debate(transcript(), as_of=T, snapshot=None, signals_age_hours=40).to_dict()
    assert c["data_quality"]["value"] == "DEGRADED"
    assert CV.blocking(c, "buy") == []


def test_reliability_comes_from_the_snapshot_and_never_from_the_debate():
    snap = {"built_at": "2026-10-06T10:00:00",
            "evaluations": {"desk": {"directional_accuracy": {
                "status": "MEASURED", "rate": 0.459, "n": 61, "n_clusters": 12,
                "ci95_cluster": [0.32, 0.6], "verdict": "indistinguishable from chance"}}}}
    c = CV.from_debate(transcript(), as_of=T, snapshot=snap, signals_age_hours=1).to_dict()
    assert c["model_reliability"]["value"] == 0.459
    assert "NO DEMONSTRATED EDGE" in c["labels"]


@pytest.mark.parametrize("direction,side,expected", [
    ("LONG", "buy", []), ("SHORT", "sell", []),
    ("LONG", "sell", ["REJECT"]), ("SHORT", "buy", ["REJECT"]), ("FLAT", "buy", ["REJECT"])])
def test_blocking_rule(direction, side, expected):
    assert [v for v, _ in CV.blocking({"direction": direction}, side)] == expected


# ── at the gate ─────────────────────────────────────────────────────────────

def _assess(frames, md, o, a=None, limits=LOOSE):
    return PT.assess(o, a or acct(), at=T, limits=limits, data=md)


def test_a_consistent_conviction_changes_nothing(world):
    frames, md = world
    base = _assess(frames, md, order(frames, "AAPL", 10))
    with_c = _assess(frames, md, order(frames, "AAPL", 10, conviction=conv()))
    assert (with_c.verdict, with_c.approved_qty) == (base.verdict, base.approved_qty)
    assert with_c.data["conviction"]["prediction_ref"] == "dbt-abcdef12"


def test_even_a_maximal_conviction_cannot_enlarge_an_order(world):
    frames, md = world
    tight = PT.PortfolioRiskLimits(**{**LOOSE.to_dict(), "max_name_pct": 2.0})
    base = _assess(frames, md, order(frames, "AAPL", 500), limits=tight)
    loud = conv()
    loud["directional_edge"]["value"] = 100
    loud["model_reliability"] = {"value": 0.99, "status": "KNOWN", "basis": "", "detail": {}}
    with_c = _assess(frames, md, order(frames, "AAPL", 500, conviction=loud), limits=tight)
    assert base.verdict == Verdict.REDUCE_SIZE
    assert (with_c.verdict, with_c.approved_qty) == (base.verdict, base.approved_qty)


def test_a_prediction_that_does_not_support_the_side_rejects(world):
    frames, md = world
    v = _assess(frames, md, order(frames, "AAPL", 10, side="buy", conviction=conv(view="bear")))
    assert v.verdict == Verdict.REJECT
    assert "prediction_supports_side" in v.checks and not v.checks["prediction_supports_side"]["ok"]


def test_poor_prediction_data_defers(world):
    frames, md = world
    v = _assess(frames, md, order(frames, "AAPL", 10, conviction=conv(at=T - timedelta(days=3))))
    assert v.verdict == Verdict.DEFER


def test_an_exit_is_never_blocked_by_a_prediction(world):
    frames, md = world
    a = acct([H(frames, "AAPL", 50)])
    v = _assess(frames, md, order(frames, "AAPL", 20, side="sell", conviction=conv(view="bull")), a)
    assert v.verdict == Verdict.ACCEPT and v.approved_qty == 20


def test_no_conviction_means_the_gate_decides_alone(world):
    frames, md = world
    v = _assess(frames, md, order(frames, "AAPL", 10))
    assert v.checks["prediction_supports_side"]["ok"] is True
    assert "conviction" not in v.data


# ── the order path ──────────────────────────────────────────────────────────

def test_a_manual_trade_carries_no_conviction():
    trade = SimpleNamespace(id="t1", situation="manual proposal from the owner")
    assert CV.for_queued_trade(trade, as_of=T, snapshot={}) is None


def test_a_desk_trade_carries_its_debate(monkeypatch, tmp_path):
    import json
    from src.desk import debate
    monkeypatch.setattr(debate, "DEBATES_DIR", tmp_path)
    (tmp_path / "dbt-abcdef12.json").write_text(json.dumps(transcript()), encoding="utf-8")
    trade = SimpleNamespace(id="t2", situation="ARIA desk cycle — debate dbt-abcdef12")
    c = CV.for_queued_trade(trade, as_of=T, snapshot={})
    assert c["direction"] == "LONG" and c["prediction_ref"] == "dbt-abcdef12"


def test_building_a_conviction_touches_no_network_and_no_model(monkeypatch):
    from src.v5 import marketdata as md

    def boom(*a, **k):
        raise AssertionError("network on the order path")
    monkeypatch.setattr(md, "provider_closes", boom)
    monkeypatch.setattr(md, "history", boom)
    import src.inference.router as router
    monkeypatch.setattr(router, "get_router", boom)
    assert conv()["direction"] == "LONG"


def test_order_manager_falls_back_to_no_conviction_on_any_error(monkeypatch):
    from src.execution.order_manager import OrderManager
    import src.intelligence.conviction as mod
    monkeypatch.setattr(mod, "for_queued_trade", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    assert OrderManager._conviction(SimpleNamespace(id="t3", situation="debate dbt-abcdef12"),
                                    T) is None


def test_the_pipeline_hands_the_conviction_to_the_gate(monkeypatch):
    from src.risk import decisions
    from src.trading.pipeline import TradingPipeline
    seen = {}

    def fake_assess(o, account, **kw):
        seen["conviction"] = o.conviction
        return SimpleNamespace(ok=True)
    monkeypatch.setattr(PT, "assess", fake_assess)
    monkeypatch.setattr(decisions, "record", lambda *a, **k: None)
    pipe = TradingPipeline.__new__(TradingPipeline)
    pipe.portfolio_limits, pipe.market_data = LOOSE, None
    pipe.broker = SimpleNamespace(name="fake")
    sig = SimpleNamespace(symbol="AAPL", side="buy", asset_class="equity", fractionable=True,
                          sector="", strategy_id="s", signal_id="sig-1")
    dec = SimpleNamespace(qty=1.0, price=100.0, closing=False)
    q = SimpleNamespace(timestamp=T, spread_bps=1.0)
    pipe._portfolio_risk(sig, dec, q, acct(), None, T, conviction={"direction": "LONG"})
    assert seen["conviction"] == {"direction": "LONG"}


@pytest.mark.parametrize("module", ["src/risk/pretrade.py", "src/trading/pipeline.py",
                                    "src/execution/order_manager.py"])
def test_the_risk_and_trading_layers_do_not_import_the_intelligence_layer_at_module_level(module):
    tree = ast.parse((ROOT / module).read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
            assert not any(n.startswith("src.intelligence") for n in names), module
    if module != "src/execution/order_manager.py":
        assert "src.intelligence" not in (ROOT / module).read_text(encoding="utf-8").replace(
            "src/intelligence", "")
