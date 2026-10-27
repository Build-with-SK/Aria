"""
One sizing base — min(broker equity, declared capital) — for the planner, the
risk officer and the reflex lane.

Before: the planner sized off the broker's figure (or a $100k fallback) while
the risk officer capped against the declared capital, so the two disagreed by
up to three orders of magnitude.
"""
from __future__ import annotations

import pytest

from src.desk import capital as C


@pytest.fixture
def fx(monkeypatch):
    monkeypatch.setattr("src.data.currency.convert", lambda amt, a, b: amt * 1.35)


def test_base_is_the_minimum_of_broker_and_declared(fx):
    assert C.sizing_base({"equity": 10_000.0},
                         {"capital_base": 1000.0, "capital_currency": "GBP"}
                         )["equity"] == pytest.approx(1350.0)
    assert C.sizing_base({"equity": 500.0},
                         {"capital_base": 1000.0, "capital_currency": "GBP"}
                         )["equity"] == pytest.approx(500.0)


def test_unknown_broker_equity_sizes_nothing(fx):
    b = C.sizing_base({"equity": 0.0},
                      {"capital_base": 1000.0, "capital_currency": "GBP"})
    assert b["equity"] == 0.0 and b["source"] == "unavailable"


class _Cond:
    regime = "Expansion (Goldilocks)"
    risk_multiplier = 1.0
    conviction_bar = 60


def _transcript(ticker="AAPL"):
    return {"id": "dbt-1", "ticker": ticker,
            "judge": {"verdict": "BUY", "conviction": 80, "reasoning": "x"}}


def _pm(monkeypatch, captured):
    from src.desk import portfolio_manager as P

    class Planner:
        def plan(self, decisions, perception, account_equity):
            captured["planner"] = account_equity
            return []

    class Perception:
        def perceive(self):
            return None

    monkeypatch.setattr("src.brain.cognitive.planner.TradePlanner", Planner)
    monkeypatch.setattr("src.brain.cognitive.perception.MarketPerception", Perception)

    class Alloc:
        size_multiplier, shorts_allowed, mode, max_positions = 1.0, True, "x", 10
        reason = ""

        def to_dict(self):
            return {}
    monkeypatch.setattr("src.desk.capital_allocator.allocate_for_account",
                        lambda *a: Alloc())

    real_review = P.RiskOfficer.review

    def review(self, candidates, account):
        captured["officer"] = getattr(self, "sizing", {}).get("equity")
        return real_review(self, candidates, account)
    monkeypatch.setattr(P.RiskOfficer, "review", review)
    from src.desk.config import DEFAULTS
    return P.PortfolioManager({**DEFAULTS, "capital_base": 1000.0,
                               "capital_currency": "GBP"}, _Cond())


def test_planner_and_officer_get_the_same_base(fx, monkeypatch):
    captured = {}
    pm = _pm(monkeypatch, captured)
    pm.build_slate([_transcript()], {"equity": 10_000.0, "positions": []})
    assert captured["planner"] == pytest.approx(1350.0), \
        "the planner sized off the broker's figure, not the shared base"
    assert captured["officer"] == captured["planner"]


def test_no_100k_fallback_when_the_broker_is_silent(fx, monkeypatch):
    captured = {}
    pm = _pm(monkeypatch, captured)
    out = pm.build_slate([_transcript()], {"equity": 0.0, "positions": []})
    assert "planner" not in captured, "planned against a fictional equity"
    assert out["slate"] == []
    assert out["rejected"][0]["reason"].startswith("SIZING BASE UNAVAILABLE")
    assert out["rejected"][0]["trade"]["debate_id"] == "dbt-1"


def test_risk_officer_has_no_fictional_sizing_fallback(monkeypatch, tmp_path):
    from src.desk import day_state as D
    from src.desk.risk_officer import RiskOfficer
    monkeypatch.setattr(D, "STATE_FILE", tmp_path / "day.json")
    officer = RiskOfficer({"max_name_pct": 5, "max_sector_pct": 25,
                           "heat_cap_pct": 10, "max_correlated_positions": 3,
                           "drawdown_halt_pct": 2, "max_trades_per_day": 10},
                          _Cond())
    officer.sizing = {"equity": 0.0, "note": "broker silent"}
    D.load_day_state(current_equity=10_000.0)        # breaker is measurable
    approved, rejected, checks = officer.review(
        [{"ticker": "AAPL", "side": "buy", "qty": 1, "price": 100.0,
          "notional": 100.0, "risk_amount": 3.0, "conviction": 90}],
        {"equity": 10_000.0, "positions": []})
    assert approved == []
    assert rejected[0]["reason"].startswith("SIZING BASE UNAVAILABLE")


def test_reflex_sizes_from_the_same_base(fx, monkeypatch):
    from src.desk import reflex
    seen = {}

    class Alloc:
        risk_per_trade_pct, size_multiplier = 1.0, 1.0
    monkeypatch.setattr("src.desk.capital_allocator.allocate_for_account",
                        lambda *a: Alloc())
    real = C.sizing_base

    def spy(account, cfg=None):
        out = real(account, cfg)
        seen["equity"] = out["equity"]
        return out
    monkeypatch.setattr(C, "sizing_base", spy)
    eng = reflex.ReflexEngine.__new__(reflex.ReflexEngine)
    eng.cfg = {"capital_base": 1000.0, "capital_currency": "GBP"}
    eng._size({"side": "buy", "ticker": "AAPL", "asset_class": "equity",
               "stop": 95.0, "target": 110.0, "conviction": 80,
               "source": "debate"},
              100.0, {"regime": ""}, {"equity": 10_000.0})
    assert seen["equity"] == pytest.approx(1350.0)

    assert eng._size({"side": "buy", "ticker": "AAPL", "asset_class": "equity",
                      "stop": 95.0, "source": "debate"}, 100.0, {"regime": ""},
                     {"equity": 0.0}) is None
