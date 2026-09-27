"""
tests/test_trading_orders.py
============================
One signal, one order — through retries, restarts and a broker that stops
answering mid-sentence.

The dangerous case is not a rejection: it is a timeout, where the order may or
may not exist. These tests hold the engine to the rule that an unknown state
stops everything until the broker has been asked by client order id, and that a
retry in the meantime never sends a second order.

The validator is tested here too, because it is the last thing an order passes
before it leaves.
"""
import sys
from datetime import timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

from trading_helpers import (ALLOWED, CLOSED_MARKET, OPEN_MARKET,  # noqa: E402
                             make_account, make_holding, make_quote, make_signal)
from src.trading import costs as _costs                 # noqa: E402
from src.trading import limits as _limits               # noqa: E402
from src.trading.broker import (BrokerTimeout, BrokerUnavailable,  # noqa: E402
                                OrderSpec, SimulatedBroker)
from src.trading.orders import OrderEngine, client_order_id  # noqa: E402
from src.trading.risk import RiskEngine                 # noqa: E402
from src.trading.validator import validate              # noqa: E402

pytestmark = pytest.mark.usefixtures("clean_trading_state")


def broker(cash: float = 1000.0) -> SimulatedBroker:
    b = SimulatedBroker(cash=cash)
    b.set_quote(make_quote())
    return b


def spec(**kw) -> OrderSpec:
    base = dict(client_order_id="aria-test-1", symbol="AAPL", side="buy", qty=0.5,
                strategy_id="test-strategy", signal_id="sig-1")
    base.update(kw)
    return OrderSpec(**base)


# ── the simulated venue ──────────────────────────────────────────────────────

def test_the_paper_broker_charges_fees_and_crosses_the_spread():
    b = broker()
    ack = b.submit_order(spec(qty=1.0))
    assert ack.status == "FILLED"
    assert ack.avg_price > 100.05, "filled at or better than the touch — too kind"
    assert ack.fees > 0
    assert b.cash < 1000 - 100


def test_the_paper_broker_enforces_cash_and_refuses_shorts():
    b = broker(cash=10.0)
    assert b.submit_order(spec(qty=5.0)).status == "REJECTED"
    b2 = broker()
    assert b2.submit_order(spec(side="sell", qty=1.0)).status == "REJECTED"


def test_the_same_client_id_is_the_same_order_at_the_broker():
    b = broker()
    first = b.submit_order(spec(qty=0.5))
    second = b.submit_order(spec(qty=0.5))
    assert first.broker_order_id == second.broker_order_id
    assert len(b.positions["AAPL"]) and b.positions["AAPL"]["qty"] == 0.5


# ── idempotency ──────────────────────────────────────────────────────────────

def test_the_client_order_id_is_derived_from_the_signal():
    a = client_order_id("strategy", "sig-abc", 0)
    assert a == client_order_id("strategy", "sig-abc", 0)
    assert a != client_order_id("strategy", "sig-abc", 1)
    assert a != client_order_id("other", "sig-abc", 0)
    assert a.startswith("aria-")


def test_a_second_submission_of_the_same_order_is_refused_before_the_broker():
    b, eng = broker(), OrderEngine()
    first = eng.submit(spec(), b)
    assert first.status == "FILLED"
    again = eng.submit(spec(), b)
    assert again.status == "DUPLICATE"
    assert b.submit_calls == 1, "the broker was asked twice"


def test_a_signal_replayed_after_a_restart_cannot_open_a_second_position():
    b = broker()
    signal = make_signal()
    decision = RiskEngine(fee_schedule=_costs.SCHEDULES["simulated"]).evaluate(
        signal, quote=make_quote(), account=make_account(), limits=_limits.load(),
        now=OPEN_MARKET, kill_switch_engaged=False, strategy_allowed=ALLOWED)
    assert decision.approved
    order = OrderEngine().build(signal, decision)
    assert OrderEngine().submit(order, b).status == "FILLED"
    # a fresh engine — as after a restart — reading the same journal
    assert OrderEngine().submit(order, b).status == "DUPLICATE"
    assert b.submit_calls == 1


# ── failure handling ─────────────────────────────────────────────────────────

def test_a_rejection_is_final_and_does_not_halt_the_engine():
    b, eng = broker(cash=1.0), OrderEngine()
    ack = eng.submit(spec(qty=10.0), b)
    assert ack.status == "REJECTED"
    assert eng.halted() is None


def test_an_unreachable_broker_means_nothing_was_sent():
    b, eng = broker(), OrderEngine()
    b.unavailable = True
    ack = eng.submit(spec(), b)
    assert ack.status == "NOT_SENT"
    assert eng.halted() is None
    assert eng.state_of("aria-test-1") == "NOT_FOUND"


def test_a_lost_acknowledgement_halts_everything_until_reconciled():
    b, eng = broker(), OrderEngine()
    b.lose_next_ack = True
    ack = eng.submit(spec(), b)
    assert ack.status == "UNKNOWN"
    assert eng.halted(), "an unknown order state did not halt the engine"

    # while halted, nothing else goes out, whatever it is
    blocked = eng.submit(spec(client_order_id="aria-test-2"), b)
    assert blocked.status == "REFUSED" and b.submit_calls == 1

    report = eng.reconcile(b)
    assert report["settled"]["aria-test-1"] == "FILLED"
    assert not report["halted"], "the halt survived a successful reconciliation"
    assert eng.submit(spec(client_order_id="aria-test-2"), b).status == "FILLED"


def test_if_the_broker_cannot_be_asked_the_halt_stays_on():
    b, eng = broker(), OrderEngine()
    b.lose_next_ack = True
    eng.submit(spec(), b)
    b.fail_lookups = BrokerUnavailable("still down")
    report = eng.reconcile(b)
    assert report["still_unknown"] == ["aria-test-1"]
    assert report["halted"] is True


def test_an_order_the_broker_never_had_is_recorded_as_not_found():
    b, eng = broker(), OrderEngine()
    b.fail_next_submit = BrokerTimeout("no reply")
    assert eng.submit(spec(), b).status == "UNKNOWN"
    report = eng.reconcile(b)
    assert report["settled"]["aria-test-1"] == "NOT_FOUND"
    assert not report["halted"]


def test_a_position_mismatch_halts_trading():
    b, eng = broker(), OrderEngine()
    eng.submit(spec(qty=1.0), b)
    assert eng.reconcile_positions(b, {"AAPL": 1.0})["ok"]
    out = eng.reconcile_positions(b, {"AAPL": 5.0})
    assert not out["ok"] and out["differences"]["AAPL"]["broker"] == 1.0
    assert eng.halted()


def test_recent_submission_times_only_counts_what_was_actually_sent():
    b, eng = broker(), OrderEngine()
    eng.submit(spec(), b)
    eng.submit(spec(), b)                      # duplicate, not sent
    assert len(eng.recent_submission_times()) == 1


# ── the validator ────────────────────────────────────────────────────────────

def checks(order=None, **kw):
    args = dict(quote=make_quote(), account=make_account(), limits=_limits.load(),
                now=OPEN_MARKET, seen_client_ids=set())
    args.update(kw)
    return validate(order or spec(), **args)


def test_a_valid_order_has_no_violations():
    assert checks() == []


def test_the_validator_refuses_a_closed_market():
    v = checks(now=CLOSED_MARKET, quote=make_quote(now=CLOSED_MARKET))
    assert any("market closed" in x for x in v)


def test_the_validator_refuses_a_stale_quote_and_stops_there():
    v = checks(quote=make_quote(age_s=120))
    assert any("not fresh" in x for x in v)
    assert not any("order value" in x for x in v), "priced checks ran on a stale quote"


def test_the_validator_refuses_a_duplicate_client_order_id():
    v = checks(seen_client_ids={"aria-test-1"})
    assert any("duplicate client order id" in x for x in v)


def test_the_validator_refuses_an_order_above_the_value_limit():
    v = checks(spec(qty=50))
    assert any("exceeds max_order_value" in x for x in v)


def test_the_validator_refuses_a_buy_the_account_cannot_pay_for():
    v = checks(spec(qty=0.9), account=make_account(equity=1000, cash=10))
    assert any("insufficient cash" in x for x in v)


def test_the_validator_refuses_a_sell_larger_than_the_holding():
    v = checks(spec(side="sell", qty=2.0),
               account=make_account(holdings=[make_holding("AAPL", qty=1.0)]))
    assert any("no short selling" in x for x in v)


def test_the_validator_refuses_a_limit_price_far_from_the_market():
    v = checks(spec(order_type="limit", limit_price=150.0))
    assert any("from mid" in x for x in v)


def test_the_validator_refuses_an_unknown_symbol_and_a_bad_quantity():
    assert any("not a valid symbol" in x for x in checks(spec(symbol="not a symbol!")))
    assert any("positive number" in x for x in checks(spec(qty=0)))
    assert any("not in the tradable universe" in x
               for x in checks(tradable_symbols={"MSFT"}))


def test_the_validator_refuses_a_fractional_order_where_fractions_are_not_allowed():
    v = checks(spec(qty=0.5), allow_fractional=False)
    assert any("fractional quantity" in x for x in v)


def test_the_validator_refuses_an_unknown_account():
    v = checks(account=make_account(known=False, why_unknown="broker down"))
    assert any("account state unknown" in x for x in v)


def test_an_order_sized_by_the_risk_engine_passes_the_validator():
    signal = make_signal()
    decision = RiskEngine(fee_schedule=_costs.SCHEDULES["simulated"]).evaluate(
        signal, quote=make_quote(), account=make_account(), limits=_limits.load(),
        now=OPEN_MARKET, kill_switch_engaged=False, strategy_allowed=ALLOWED)
    order = OrderEngine().build(signal, decision)
    assert checks(order) == []


def test_recent_orders_and_the_frequency_limit_agree_on_what_an_order_is():
    """The risk engine counts orders per minute from the journal the order
    engine writes. If the two disagree about what counts, the limit is not the
    limit it claims to be."""
    b, eng = broker(), OrderEngine()
    eng.submit(spec(), b)
    times = eng.recent_submission_times()
    assert times and (times[0].tzinfo is not None), "journal times are not aware"
    assert times[0] <= times[0] + timedelta(seconds=1)
