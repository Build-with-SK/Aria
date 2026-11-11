"""
tests/test_trading_pipeline.py
==============================
The whole path end to end, plus the thing that stops it.

RESEARCH runs every stage and sends nothing — that is the property that makes
research meaningful, and the one most easily broken by a refactor, so it is
asserted against the broker's own call counter rather than the result object.

The halt tests check what a kill switch is actually for: not a flag, but new
orders stopped, autonomy off, pending orders cancelled, and positions left
alone unless the owner configured otherwise.
"""
import sys
from datetime import timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

from trading_helpers import OPEN_MARKET, make_quote, make_signal   # noqa: E402
from src.trading import costs as _costs                            # noqa: E402
from src.trading import killswitch, latency, lifecycle, modes      # noqa: E402
from src.trading.broker import OrderSpec, SimulatedBroker          # noqa: E402
from src.trading.pipeline import TradingPipeline                   # noqa: E402

pytestmark = pytest.mark.usefixtures("clean_trading_state")

EVIDENCE = {
    "hypothesis": "h", "data_sources": ["test"],
    "backtest": {"trades": 40, "costs_modelled": True, "period": "2025-2026"},
    "walk_forward": {"folds": 3, "purged": True, "oos_trades": 40,
                     "deflated_sharpe": 0.3},
}


def ready_strategy(sid="test-strategy", stage=lifecycle.Stage.PAPER) -> str:
    lifecycle.register(sid)
    for key, value in EVIDENCE.items():
        lifecycle.record_evidence(sid, key, value)
    for target in lifecycle.ORDER[1:]:
        lifecycle.promote(sid, target)
        if target is stage:
            break
    return sid


def broker(cash: float = 1000.0) -> SimulatedBroker:
    b = SimulatedBroker(cash=cash)
    b.set_quote(make_quote())
    return b


def pipeline(b=None) -> TradingPipeline:
    return TradingPipeline(b or broker(),
                           fee_schedule=_costs.SCHEDULES["simulated"])


def set_mode(mode: str):
    modes.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    modes.STATE_FILE.write_text('{"mode": "%s"}' % mode, encoding="utf-8")


# ── RESEARCH ─────────────────────────────────────────────────────────────────

def test_research_builds_and_validates_an_order_but_sends_nothing():
    ready_strategy(stage=lifecycle.Stage.BACKTEST)
    b = broker()
    result = pipeline(b).run(make_signal(), now=OPEN_MARKET)
    assert result.mode == "RESEARCH" and result.dry_run is True
    assert result.decision["approved"] is True
    assert result.violations == []
    assert result.order["symbol"] == "AAPL" and result.order["qty"] > 0
    assert result.submitted is False
    assert b.submit_calls == 0, "RESEARCH mode reached the broker"
    assert "not sent" in " ".join(result.reasons)


def test_research_times_every_stage():
    ready_strategy(stage=lifecycle.Stage.BACKTEST)
    result = pipeline().run(make_signal(), now=OPEN_MARKET)
    for stage in ("market_data", "decision", "order_generation", "total"):
        assert stage in result.timings_ms, stage
        assert result.timings_ms[stage] >= 0


def test_an_unregistered_strategy_gets_nowhere_even_in_research():
    result = pipeline().run(make_signal(strategy_id="ghost"), now=OPEN_MARKET)
    assert result.decision["approved"] is False
    assert "registry" in " ".join(result.reasons)


# ── PAPER ────────────────────────────────────────────────────────────────────

def test_paper_mode_sends_the_order_and_journals_it():
    ready_strategy()
    set_mode("PAPER")
    b = broker()
    pipe = pipeline(b)
    result = pipe.run(make_signal(), now=OPEN_MARKET)
    assert result.submitted is True, result.reasons
    assert result.ack["status"] == "FILLED"
    assert b.submit_calls == 1
    assert pipe.orders.state_of(result.order["client_order_id"]) == "FILLED"
    assert "broker_ack" in result.timings_ms


def test_the_same_signal_twice_produces_one_order():
    ready_strategy()
    set_mode("PAPER")
    # Room to spare, so the second attempt is stopped by its client order id
    # rather than by the position the first one opened.
    b = broker(cash=10_000)
    pipe = pipeline(b)
    signal = make_signal()
    first = pipe.run(signal, now=OPEN_MARKET)
    second = pipe.run(signal, now=OPEN_MARKET)
    assert first.submitted is True
    assert second.submitted is False
    assert b.submit_calls == 1
    assert any("duplicate client order id" in v for v in second.violations), \
        second.reasons


def test_a_strategy_that_has_not_reached_paper_cannot_trade_on_paper():
    ready_strategy(stage=lifecycle.Stage.BACKTEST)
    set_mode("PAPER")
    b = broker()
    result = pipeline(b).run(make_signal(), now=OPEN_MARKET)
    assert result.decision["approved"] is False
    assert "has not reached PAPER" in " ".join(result.reasons)
    assert b.submit_calls == 0


def test_the_kill_switch_stops_the_pipeline_before_the_risk_engine():
    ready_strategy()
    set_mode("PAPER")
    from src.execution import kill_switch
    kill_switch.engage("testing", actor="test")
    b = broker()
    try:
        result = pipeline(b).run(make_signal(), now=OPEN_MARKET)
        assert result.submitted is False
        assert "kill_switch" in " ".join(result.reasons)
        assert b.submit_calls == 0
    finally:
        kill_switch.release(actor="test")


def test_a_broker_that_cannot_be_read_produces_no_trade_not_a_guess():
    ready_strategy()
    set_mode("PAPER")
    b = broker()
    b.unavailable = True
    result = pipeline(b).run(make_signal(), now=OPEN_MARKET)
    assert result.submitted is False
    assert result.decision["approved"] is False
    assert "account_known" in " ".join(result.reasons)


def test_a_stale_quote_stops_the_pipeline():
    ready_strategy()
    set_mode("PAPER")
    b = broker()
    b.set_quote(make_quote(age_s=600))
    result = pipeline(b).run(make_signal(), now=OPEN_MARKET)
    assert result.submitted is False
    assert "market_data_fresh" in " ".join(result.reasons)


def test_a_closed_market_is_stopped_before_anything_else():
    """SESSION is the first gate (2026-10-06): a closed market is refused
    before the risk engine is asked, not discovered at validation."""
    ready_strategy()
    set_mode("PAPER")
    closed = OPEN_MARKET + timedelta(hours=12)        # 22:00 New York
    b = SimulatedBroker(cash=1000.0)
    b.set_quote(make_quote(now=closed))
    result = pipeline(b).run(make_signal(now=closed), now=closed)
    assert result.submitted is False
    assert result.stage_reached == "session"
    assert result.decision is None                    # risk was never consulted
    assert any("market closed" in r for r in result.reasons), result.reasons
    assert result.session["state"] in ("POST_MARKET", "CLOSED")
    assert result.session["next_open"]
    assert b.submit_calls == 0


def test_the_validator_still_refuses_a_closed_market_on_its_own():
    """Defence in depth: the validator checks the instrument's session too."""
    from src.trading.validator import validate
    from src.trading.account import unknown as unknown_account
    from src.trading import limits as _limits
    closed = OPEN_MARKET + timedelta(hours=12)
    order = OrderSpec(client_order_id="c1", symbol="AAPL", side="buy", qty=1)
    v = validate(order, quote=make_quote(now=closed), account=unknown_account("x"),
                 limits=_limits.load(), now=closed, seen_client_ids=set())
    assert any("market closed" in x for x in v)


# ── latency ──────────────────────────────────────────────────────────────────

def test_latency_is_recorded_and_never_described_as_hft():
    ready_strategy()
    set_mode("PAPER")
    pipeline().run(make_signal(), now=OPEN_MARKET)
    summary = latency.summary()
    assert summary["samples"] >= 1
    text = str(summary).lower()
    assert "hft" not in text or "not_hft_because" in str(summary)
    assert summary["classification"]["label"] != "HFT"
    assert "co-location" in summary["classification"]["not_hft_because"]


def test_an_unmeasured_broker_round_trip_says_so():
    out = latency.classify({"stages": {"broker_ack": None, "decision": None}})
    assert out["label"] == "unmeasured"
    assert "does not characterise" in out["why"]


def test_a_simulated_venue_is_labelled_as_one():
    out = latency.classify({"stages": {"broker_ack": {"p50_ms": 0.4},
                                       "decision": {"p50_ms": 1.0}}})
    assert "simulated" in out["label"]


# ── the halt ─────────────────────────────────────────────────────────────────

def test_halt_stops_new_orders_and_drops_the_mode():
    set_mode("PAPER")
    report = killswitch.halt("testing the halt", actor="owner")
    from src.execution import kill_switch
    try:
        assert kill_switch.is_engaged() is True
        assert modes.stored_mode() is modes.ExecutionMode.RESEARCH
        assert modes.can_place_orders() is False
        assert report["steps"]["desk_auto_execute"] is False
        assert report["steps"]["cancel"]["skipped"]
    finally:
        kill_switch.release(actor="test")


def test_halt_cancels_resting_orders_and_keeps_positions():
    set_mode("PAPER")
    b = broker()
    b.submit_order(OrderSpec(client_order_id="aria-entry", symbol="AAPL",
                             side="buy", qty=1.0))                 # fills
    b.submit_order(OrderSpec(client_order_id="aria-resting", symbol="AAPL",
                             side="buy", qty=0.5, order_type="limit",
                             limit_price=90.0))                    # rests
    from src.execution import kill_switch
    try:
        report = killswitch.halt("stop", broker=b)
        assert report["steps"]["cancel"]["cancelled"] == ["aria-resting"]
        assert b.positions["AAPL"]["qty"] == 1.0, "a position was liquidated"
        assert report["steps"]["positions"] == "preserved"
    finally:
        kill_switch.release(actor="test")


def test_halt_leaves_a_protective_sell_resting():
    set_mode("PAPER")
    b = broker()
    b.submit_order(OrderSpec(client_order_id="aria-entry", symbol="AAPL",
                             side="buy", qty=1.0))
    b.submit_order(OrderSpec(client_order_id="aria-stop", symbol="AAPL",
                             side="sell", qty=1.0, order_type="limit",
                             limit_price=120.0))                   # rests above bid
    from src.execution import kill_switch
    try:
        report = killswitch.halt("stop", broker=b, held={"AAPL": 1.0})
        assert report["steps"]["cancel"]["kept_protective"] == ["aria-stop"]
        assert b.orders["aria-stop"].status == "NEW"
    finally:
        kill_switch.release(actor="test")


def test_flatten_is_off_unless_chosen_and_sells_reduce_only_when_it_is():
    set_mode("PAPER")
    assert killswitch.policy()["positions"] == killswitch.PRESERVE
    killswitch.set_policy(killswitch.FLATTEN, False, actor="owner")
    b = broker()
    b.submit_order(OrderSpec(client_order_id="aria-entry", symbol="AAPL",
                             side="buy", qty=1.0))
    from src.execution import kill_switch
    try:
        report = killswitch.halt("flatten everything", broker=b)
        assert report["steps"]["flatten"]["AAPL"]["status"] == "FILLED"
        assert b.positions["AAPL"]["qty"] == 0.0
    finally:
        kill_switch.release(actor="test")


def test_halt_never_raises_even_when_the_broker_is_broken():
    set_mode("PAPER")

    class Broken:
        def get_open_orders(self):
            raise RuntimeError("no connection")

    from src.execution import kill_switch
    try:
        report = killswitch.halt("broker is down", broker=Broken())
        assert kill_switch.is_engaged() is True
        assert "no connection" in str(report["steps"]["cancel"]["failed"])
    finally:
        kill_switch.release(actor="test")


def test_the_halt_is_recorded():
    from src.execution import kill_switch
    try:
        killswitch.halt("audit trail", actor="owner")
        assert killswitch.LOG.exists()
        assert "audit trail" in killswitch.LOG.read_text(encoding="utf-8")
    finally:
        kill_switch.release(actor="test")
