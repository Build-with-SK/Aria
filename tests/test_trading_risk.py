"""
tests/test_trading_risk.py
==========================
The risk engine's job is to say no, so these tests are mostly about no.

Each one sets up a signal that a strategy would be happy with and then breaks
exactly one thing — the capital, the freshness of the price, the size already
held, yesterday's drawdown, the arithmetic of the edge against its costs — and
asserts that the trade does not happen AND that the refusal names the reason.
A silent refusal and a wrong approval are both failures here.
"""
import sys
from datetime import timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

from trading_helpers import (ALLOWED, OPEN_MARKET, REFUSED, make_account,  # noqa: E402
                             make_holding, make_quote, make_signal)
from src.trading import costs as _costs               # noqa: E402
from src.trading import limits as _limits             # noqa: E402
from src.trading.risk import RiskEngine               # noqa: E402

pytestmark = pytest.mark.usefixtures("clean_trading_state")


def engine(min_net_edge_bps: float = 10.0) -> RiskEngine:
    return RiskEngine(fee_schedule=_costs.SCHEDULES["simulated"],
                      min_net_edge_bps=min_net_edge_bps)


def evaluate(*, signal=None, quote=None, account=None, recent=None, allowed=ALLOWED,
             kill=False, now=OPEN_MARKET, min_net_edge_bps=10.0):
    return engine(min_net_edge_bps).evaluate(
        signal or make_signal(), quote=quote if quote is not None else make_quote(),
        account=account or make_account(), limits=_limits.load(), now=now,
        kill_switch_engaged=kill, strategy_allowed=allowed, recent_orders=recent or [])


def reasons(d) -> str:
    return " | ".join(d.reasons)


# ── the one that works ───────────────────────────────────────────────────────

def test_a_clean_signal_is_approved_and_sized_by_the_tightest_constraint():
    d = evaluate()
    assert d.approved, reasons(d)
    assert 0 < d.notional <= _limits.load().max_order_value
    assert d.sizing["binding_constraint"] in d.sizing
    assert d.expected_net_edge_bps is not None
    assert d.expected_net_edge_bps < 300, "costs were not subtracted from the edge"


def test_confidence_cannot_buy_a_bigger_position():
    modest = evaluate(signal=make_signal(expected_edge_bps=300))
    certain = evaluate(signal=make_signal(expected_edge_bps=9000))
    assert modest.approved and certain.approved
    assert modest.qty == certain.qty, "a larger claimed edge changed the size"


# ── capital ──────────────────────────────────────────────────────────────────

def test_insufficient_capital_is_no_trade():
    d = evaluate(account=make_account(equity=1000, cash=0.50))
    assert not d.approved
    assert "available_cash" in reasons(d) or "size_viable" in reasons(d)
    assert "minimum trade" in reasons(d)


def test_an_unknown_account_stops_everything():
    d = evaluate(account=make_account(known=False, why_unknown="broker timed out"))
    assert not d.approved
    assert "broker timed out" in reasons(d)
    assert d.checks["account_known"]["ok"] is False


def test_an_old_account_snapshot_is_not_sized_against():
    stale = make_account(now=OPEN_MARKET - timedelta(minutes=10))
    d = evaluate(account=stale)
    assert not d.approved and "account_fresh" in reasons(d)


# ── market data ──────────────────────────────────────────────────────────────

def test_a_stale_quote_is_never_traded_on():
    d = evaluate(quote=make_quote(age_s=40))
    assert not d.approved
    assert "market_data_fresh" in reasons(d) and "old" in reasons(d)


def test_a_quote_without_a_timestamp_is_treated_as_stale():
    d = evaluate(quote=make_quote(timestamp=None))
    assert not d.approved and "no timestamp" in reasons(d)


def test_a_crossed_book_is_refused():
    d = evaluate(quote=make_quote(bid=101.0, ask=100.0))
    assert not d.approved and "crossed" in reasons(d)


def test_a_signal_computed_from_old_data_is_refused():
    d = evaluate(signal=make_signal(data_age_s=600))
    assert not d.approved and "signal_data_fresh" in reasons(d)


# ── position and portfolio limits ────────────────────────────────────────────

def test_an_existing_full_position_leaves_no_room():
    held = make_holding("AAPL", qty=1.0, avg_price=100.0, market_price=100.0)
    d = evaluate(account=make_account(equity=1000, cash=1000, holdings=[held]))
    assert not d.approved
    assert "size_viable" in reasons(d)
    assert d.sizing["instrument_exposure"] == 0


def test_a_breached_drawdown_stops_new_positions():
    d = evaluate(account=make_account(equity=1000, hwm=2000))
    assert not d.approved and "max_drawdown" in reasons(d)


def test_a_bad_day_stops_new_positions():
    d = evaluate(account=make_account(equity=900, day_start=1000))
    assert not d.approved and "max_daily_loss" in reasons(d)


def test_too_many_orders_in_a_minute_stops_trading():
    lim = _limits.load()
    recent = [OPEN_MARKET - timedelta(seconds=5)] * lim.max_orders_per_minute
    d = evaluate(recent=recent)
    assert not d.approved and "order_frequency_minute" in reasons(d)


def test_too_many_orders_in_a_day_stops_trading():
    lim = _limits.load()
    recent = [OPEN_MARKET - timedelta(hours=3)] * lim.max_orders_per_day
    d = evaluate(recent=recent)
    assert not d.approved and "order_frequency_day" in reasons(d)


def test_too_many_open_positions_stops_a_new_one():
    lim = _limits.load()
    holdings = [make_holding(f"SYM{i}", qty=0.01, avg_price=1.0)
                for i in range(lim.max_open_positions)]
    d = evaluate(account=make_account(equity=1000, holdings=holdings))
    assert not d.approved and "max_open_positions" in reasons(d)


# ── the economics ────────────────────────────────────────────────────────────

def test_an_edge_that_does_not_survive_its_costs_is_no_trade():
    d = evaluate(signal=make_signal(expected_edge_bps=5))
    assert not d.approved
    assert "NO TRADE —" in reasons(d)
    assert "round trip costs" in reasons(d)
    assert d.costs["total"] > 0


def test_a_wider_spread_can_turn_a_viable_trade_into_no_trade():
    tight = evaluate(signal=make_signal(expected_edge_bps=60),
                     quote=make_quote(bid=99.99, ask=100.01))
    wide = evaluate(signal=make_signal(expected_edge_bps=60),
                    quote=make_quote(bid=99.0, ask=101.0))
    assert tight.approved
    assert not wide.approved and "NO TRADE —" in reasons(wide)


def test_a_trade_with_no_stop_cannot_be_sized():
    d = evaluate(signal=make_signal(stop_price=None))
    assert not d.approved and "stop_defined" in reasons(d)


def test_a_stop_on_the_wrong_side_is_refused():
    d = evaluate(signal=make_signal(stop_price=100.05))
    assert not d.approved and "stop_sane" in reasons(d)


# ── selling ──────────────────────────────────────────────────────────────────

def test_selling_what_is_not_held_is_refused():
    d = evaluate(signal=make_signal(side="sell", stop_price=105.0))
    assert not d.approved
    assert "no_short_selling" in reasons(d) and "short selling" in reasons(d)


def test_closing_a_position_is_allowed_even_in_a_drawdown():
    held = make_holding("AAPL", qty=2.0, avg_price=120.0, market_price=100.0)
    d = evaluate(signal=make_signal(side="sell", stop_price=105.0),
                 account=make_account(equity=1000, holdings=[held], hwm=5000,
                                      day_start=2000))
    assert d.approved and d.closing
    assert d.qty == 2.0, "a close must be for the whole held quantity"


# ── the switches above the engine ────────────────────────────────────────────

def test_the_kill_switch_refuses_before_anything_else_is_asked():
    d = evaluate(kill=True)
    assert not d.approved
    assert list(d.checks) == ["kill_switch"], "checks continued past the kill switch"


def test_a_suspended_strategy_gets_nowhere():
    d = evaluate(allowed=REFUSED)
    assert not d.approved and "suspended" in reasons(d)


def test_liquidity_caps_the_size():
    thin = make_quote(ask_size=2, bid_size=2)
    d = evaluate(account=make_account(equity=100_000, cash=100_000), quote=thin)
    lim = _limits.load()
    if d.approved:
        assert d.qty <= thin.ask_size * lim.max_participation_pct / 100 + 1e-9
    else:
        assert "size_viable" in reasons(d)
