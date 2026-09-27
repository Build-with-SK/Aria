"""
tests/test_trading_lifecycle.py
===============================
A strategy has to earn every stage, and can lose them all at once.

The gates are evidence checks, so the attacks are evidence-shaped: promote with
nothing on file, promote with a backtest that modelled no costs, promote with a
deflated Sharpe at zero, skip a stage, come back from suspension straight to
LIVE. The scorecard and drift tests are here too, because they are what decides
whether a strategy keeps what it earned.
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.trading import drift, lifecycle, modes, scorecard  # noqa: E402
from src.trading.lifecycle import LifecycleRefused, Stage   # noqa: E402

pytestmark = pytest.mark.usefixtures("clean_trading_state")

FULL_EVIDENCE = {
    "hypothesis": "overnight gaps in liquid US equities mean-revert by the open",
    "data_sources": ["alpaca minute bars"],
    "backtest": {"trades": 120, "costs_modelled": True, "period": "2024-2026",
                 "sharpe": 1.1},
    "walk_forward": {"folds": 5, "purged": True, "oos_trades": 60,
                     "deflated_sharpe": 0.42},
    "paper": {"trades": 25, "days": 30, "sharpe": 0.8,
              "slippage_over_model_bps": 1.2},
    "capacity": {"max_notional": 400},
    "review": {"by": "owner", "at": "2026-09-22", "note": "read the walk-forward"},
}


def stock(sid="gap-fade", evidence=None) -> str:
    lifecycle.register(sid, name="Gap fade")
    for key, value in (evidence or {}).items():
        lifecycle.record_evidence(sid, key, value)
    return sid


def walk_to(sid: str, target: Stage) -> None:
    for stage in lifecycle.ORDER[1:]:
        lifecycle.promote(sid, stage)
        if stage is target:
            return


# ── registration and the first gate ──────────────────────────────────────────

def test_a_new_strategy_starts_at_draft_and_may_not_trade():
    sid = stock()
    assert lifecycle.stage_of(sid) is Stage.DRAFT
    ok, why = lifecycle.may_trade(sid, modes.ExecutionMode.PAPER)
    assert not ok and "has not reached PAPER" in why


def test_an_unregistered_strategy_may_not_trade_at_all():
    ok, why = lifecycle.may_trade("never-heard-of-it", modes.ExecutionMode.PAPER)
    assert not ok and "registry" in why


def test_promotion_without_evidence_is_refused():
    sid = stock()
    with pytest.raises(LifecycleRefused) as exc:
        lifecycle.promote(sid, Stage.RESEARCH)
    assert "hypothesis" in str(exc.value)
    assert lifecycle.stage_of(sid) is Stage.DRAFT


def test_stages_cannot_be_skipped():
    sid = stock(evidence=FULL_EVIDENCE)
    with pytest.raises(LifecycleRefused) as exc:
        lifecycle.promote(sid, Stage.PAPER)
    assert "single step" in str(exc.value)


# ── the evidence gates, one at a time ────────────────────────────────────────

def test_a_backtest_without_costs_does_not_count():
    evidence = json.loads(json.dumps(FULL_EVIDENCE))
    evidence["backtest"]["costs_modelled"] = False
    sid = stock(evidence=evidence)
    lifecycle.promote(sid, Stage.RESEARCH)
    with pytest.raises(LifecycleRefused) as exc:
        lifecycle.promote(sid, Stage.BACKTEST)
    assert "fees, spread and slippage" in str(exc.value)


def test_an_unpurged_walk_forward_does_not_count():
    evidence = json.loads(json.dumps(FULL_EVIDENCE))
    evidence["walk_forward"]["purged"] = False
    sid = stock(evidence=evidence)
    walk_to(sid, Stage.BACKTEST)
    with pytest.raises(LifecycleRefused) as exc:
        lifecycle.promote(sid, Stage.WALK_FORWARD)
    assert "leakage" in str(exc.value)


def test_paper_trading_requires_a_deflated_sharpe_above_zero():
    evidence = json.loads(json.dumps(FULL_EVIDENCE))
    evidence["walk_forward"]["deflated_sharpe"] = 0.0
    sid = stock(evidence=evidence)
    walk_to(sid, Stage.WALK_FORWARD)
    with pytest.raises(LifecycleRefused) as exc:
        lifecycle.promote(sid, Stage.PAPER)
    assert "variants" in str(exc.value)


def test_validation_requires_enough_paper_trading_and_an_accurate_cost_model():
    evidence = json.loads(json.dumps(FULL_EVIDENCE))
    evidence["paper"] = {"trades": 3, "days": 2, "sharpe": 2.0,
                         "slippage_over_model_bps": 40}
    sid = stock(evidence=evidence)
    walk_to(sid, Stage.PAPER)
    with pytest.raises(LifecycleRefused) as exc:
        lifecycle.promote(sid, Stage.VALIDATED)
    message = str(exc.value)
    assert "paper trades" in message and "cost model is wrong" in message


def test_live_eligibility_requires_a_human_to_have_read_it():
    evidence = json.loads(json.dumps(FULL_EVIDENCE))
    evidence.pop("review")
    sid = stock(evidence=evidence)
    walk_to(sid, Stage.VALIDATED)
    with pytest.raises(LifecycleRefused) as exc:
        lifecycle.promote(sid, Stage.LIVE_ELIGIBLE)
    assert "owner review" in str(exc.value)


def test_the_full_path_reaches_live_eligible_but_not_live():
    sid = stock(evidence=FULL_EVIDENCE)
    walk_to(sid, Stage.LIVE_ELIGIBLE)
    assert lifecycle.stage_of(sid) is Stage.LIVE_ELIGIBLE
    assert lifecycle.any_at_or_beyond(Stage.LIVE_ELIGIBLE)
    with pytest.raises(LifecycleRefused) as exc:
        lifecycle.promote(sid, Stage.LIVE)
    assert "LIVE_AUTONOMOUS" in str(exc.value)
    ok, why = lifecycle.may_trade(sid, modes.ExecutionMode.LIVE_AUTONOMOUS)
    assert not ok and "only a strategy promoted to LIVE" in why


def test_live_eligible_may_still_trade_on_paper():
    sid = stock(evidence=FULL_EVIDENCE)
    walk_to(sid, Stage.LIVE_ELIGIBLE)
    ok, _ = lifecycle.may_trade(sid, modes.ExecutionMode.PAPER)
    assert ok


def test_research_mode_evaluates_a_draft_strategy_without_letting_it_trade():
    sid = stock()
    ok, why = lifecycle.may_trade(sid, modes.ExecutionMode.RESEARCH)
    assert ok and "no orders" in why


# ── losing it ────────────────────────────────────────────────────────────────

def test_suspension_stops_a_strategy_in_every_mode():
    sid = stock(evidence=FULL_EVIDENCE)
    walk_to(sid, Stage.PAPER)
    lifecycle.suspend(sid, reason="slippage tripled")
    for mode in modes.ExecutionMode:
        ok, why = lifecycle.may_trade(sid, mode)
        assert not ok and "suspended" in why, mode


def test_reinstatement_comes_back_at_paper_at_best():
    sid = stock(evidence=FULL_EVIDENCE)
    walk_to(sid, Stage.LIVE_ELIGIBLE)
    lifecycle.suspend(sid, reason="regime change")
    lifecycle.reinstate(sid, actor="owner")
    assert lifecycle.stage_of(sid) is Stage.PAPER
    assert not lifecycle.any_at_or_beyond(Stage.LIVE_ELIGIBLE)


def test_demotion_needs_no_evidence():
    sid = stock(evidence=FULL_EVIDENCE)
    walk_to(sid, Stage.VALIDATED)
    lifecycle.demote(sid, Stage.RESEARCH, reason="the hypothesis was wrong")
    assert lifecycle.stage_of(sid) is Stage.RESEARCH


def test_every_transition_is_recorded():
    sid = stock(evidence=FULL_EVIDENCE)
    walk_to(sid, Stage.BACKTEST)
    lifecycle.suspend(sid, reason="testing")
    rows = [json.loads(line) for line in
            lifecycle.LEDGER.read_text(encoding="utf-8").splitlines()]
    events = [r["event"] for r in rows]
    assert "registered" in events and "promoted" in events and "suspended" in events


# ── the scorecard ────────────────────────────────────────────────────────────

def trades(n: int, *, pnl: float, notional: float = 100.0, fees: float = 0.1,
           oos: bool = True, start: datetime | None = None,
           jitter: float = 0.0) -> list[dict]:
    """`jitter` alternates the P&L up and down, because a strategy whose every
    trade returns exactly the same amount has zero variance and no Sharpe —
    a fixture that would flatter any risk-adjusted measure."""
    start = start or datetime(2026, 7, 1, tzinfo=timezone.utc)
    return [{"strategy_id": "s", "notional": notional,
             "realised_pnl": pnl + (jitter if i % 2 else -jitter),
             "fees": fees, "slippage_bps": 2.0, "holding_period_s": 3600,
             "entry_at": (start + timedelta(days=i)).isoformat(),
             "exit_at": (start + timedelta(days=i, hours=2)).isoformat(),
             "out_of_sample": oos} for i in range(n)]


def test_a_scorecard_reports_costs_and_tails_not_just_profit():
    card = scorecard.score(trades(30, pnl=1.0), strategy_id="s")
    assert card["trades"] == 30
    assert card["fees_bps_mean"] > 0
    assert card["tail_loss_bps_mean"] is not None
    assert card["capacity_note"]


def test_a_short_history_is_not_annualised():
    card = scorecard.score(trades(5, pnl=1.0))
    assert card["sharpe_annualised"] is None
    assert "too little" in card["annualisation_note"]


def test_a_strategy_without_enough_out_of_sample_trades_is_not_ranked():
    card = scorecard.score(trades(5, pnl=5.0), strategy_id="lucky")
    assert card["rankable"] is False
    out = scorecard.rank([card])
    assert out["ranked"] == []
    assert out["insufficient_evidence"][0]["strategy_id"] == "lucky"


def test_ranking_is_not_by_profit():
    # 'lumpy' makes more money in total, in wild swings; 'steady' makes less,
    # consistently. Ranking on return would put lumpy first.
    steady = scorecard.score(trades(30, pnl=1.0, jitter=0.2), strategy_id="steady")
    wild = trades(15, pnl=30.0, jitter=2.0) + trades(
        15, pnl=-25.0, jitter=2.0, start=datetime(2026, 8, 1, tzinfo=timezone.utc))
    lumpy = scorecard.score(wild, strategy_id="lumpy")
    assert lumpy["net_pnl"] > steady["net_pnl"]
    ranked = scorecard.rank([lumpy, steady])["ranked"]
    assert ranked[0]["strategy_id"] == "steady", ranked


# ── drift ────────────────────────────────────────────────────────────────────

def test_drift_needs_enough_recent_trades_before_it_says_anything():
    rep = drift.assess("s", baseline=trades(20, pnl=1.0), recent=trades(2, pnl=-5.0))
    assert rep.verdict == "INSUFFICIENT_DATA"


def test_an_edge_that_has_gone_negative_is_flagged():
    rep = drift.assess("s", baseline=trades(20, pnl=2.0), recent=trades(10, pnl=-3.0))
    assert rep.verdict == "SUSPEND"
    assert "performance" in rep.drifted


def test_slippage_running_over_the_model_is_flagged():
    baseline = trades(20, pnl=1.0)
    recent = trades(10, pnl=1.0)
    for r in recent:
        r["slippage_bps"] = 40.0
    rep = drift.assess("s", baseline=baseline, recent=recent)
    assert "slippage" in rep.drifted and rep.verdict == "SUSPEND"


def test_rejected_orders_count_as_execution_drift():
    rep = drift.assess("s", baseline=trades(20, pnl=1.0), recent=trades(10, pnl=1.0),
                       attempts=10, rejects=8)
    assert "execution" in rep.drifted


def test_a_healthy_strategy_is_left_alone():
    rep = drift.assess("s", baseline=trades(20, pnl=1.0), recent=trades(10, pnl=1.0))
    assert rep.verdict == "OK" and rep.drifted == []


def test_the_monitor_suspends_what_it_finds(monkeypatch):
    from src.trading import outcomes as _out
    sid = stock(evidence=FULL_EVIDENCE)
    walk_to(sid, Stage.PAPER)
    monkeypatch.setattr(_out, "outcomes",
                        lambda strategy_id="", limit=1000:
                        trades(20, pnl=2.0) + trades(10, pnl=-4.0))
    rep = drift.monitor(sid)
    assert rep.verdict == "SUSPEND"
    assert lifecycle.stage_of(sid) is Stage.SUSPENDED
