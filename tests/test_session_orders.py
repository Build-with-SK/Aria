"""
tests/test_session_orders.py
============================
A closed market changes what a thesis becomes; it never turns it into a blind
order at the open (src/trading/session_orders.py).
"""
import math
import random
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.markets import sessions as S
from src.trading.session_orders import (OpenAction, OpenObservation, PlanKind,
                                        ProxyMove, SessionPolicy, TradeIntent,
                                        adapt_at_open, estimate_open, plan_order)

UTC = timezone.utc
OPEN_T = datetime(2026, 10, 6, 15, 0, tzinfo=UTC)          # Tue 11:00 NY
CLOSED_T = datetime(2026, 10, 6, 22, 0, tzinfo=UTC)        # Tue 18:00 NY
FRI_NIGHT = datetime(2026, 10, 9, 23, 0, tzinfo=UTC)


def intent(**kw):
    base = dict(symbol="AAPL", side="buy", qty=10, reference_price=100.0, atr=2.0,
                stop_price=96.0)
    return TradeIntent(**{**base, **kw})


# ── planning ────────────────────────────────────────────────────────────────

def test_an_open_market_executes_through_the_gates():
    p = plan_order(intent(), S.session("AAPL", OPEN_T), now=OPEN_T)
    assert p.kind == PlanKind.EXECUTE_NOW


def test_a_closed_market_schedules_after_the_next_open_not_at_it():
    p = plan_order(intent(), S.session("AAPL", CLOSED_T), now=CLOSED_T)
    assert p.kind == PlanKind.SCHEDULED
    # next open Wed 09:30 NY = 13:30 UTC, plus the observation wait
    assert p.valid_from == datetime(2026, 10, 7, 13, 35, tzinfo=UTC).isoformat()
    assert "never sent as a blind order" in p.reasons[0]


def test_the_weekend_schedules_for_monday():
    p = plan_order(intent(), S.session("AAPL", FRI_NIGHT), now=FRI_NIGHT)
    assert p.valid_from.startswith("2026-10-12T13:35")


def test_a_thesis_that_expires_before_the_open_is_rejected():
    p = plan_order(intent(thesis_expires=datetime(2026, 10, 7, 12, 0, tzinfo=UTC)),
                   S.session("AAPL", CLOSED_T), now=CLOSED_T)
    assert p.kind == PlanKind.REJECT


def test_a_trigger_becomes_a_conditional_order():
    p = plan_order(intent(trigger_price=101.5), S.session("AAPL", CLOSED_T), now=CLOSED_T)
    assert p.kind == PlanKind.CONDITIONAL and p.trigger_price == 101.5


def test_standing_orders_are_off_unless_the_policy_allows_them():
    i = intent(order_type="limit", limit_price=99.0)
    sess = S.session("AAPL", CLOSED_T)
    assert plan_order(i, sess, now=CLOSED_T).kind == PlanKind.SCHEDULED
    allowed = SessionPolicy(allow_standing_orders=True)
    p = plan_order(i, sess, allowed, now=CLOSED_T)
    assert p.kind == PlanKind.STANDING and p.time_in_force == "gtc"
    far = intent(order_type="limit", limit_price=90.0)        # 5 ATR away
    assert plan_order(far, sess, allowed, now=CLOSED_T).kind == PlanKind.SCHEDULED


def test_an_event_thesis_is_deferred_for_re_derivation():
    p = plan_order(intent(style="event"), S.session("AAPL", CLOSED_T), now=CLOSED_T)
    assert p.kind == PlanKind.DEFERRED


def test_unknown_halted_and_expiring_sessions():
    unknown = S.session("RELIANCE.NS", datetime(2026, 10, 6, 5, 0, tzinfo=UTC))
    assert plan_order(intent(symbol="RELIANCE.NS"), unknown).kind == PlanKind.DEFERRED
    halted = S.session("AAPL", OPEN_T, halted=True)
    assert plan_order(intent(), halted).kind == PlanKind.DEFERRED
    expiring = S.session("AAPL261218C00200000", datetime(2026, 12, 18, 15, 0, tzinfo=UTC))
    assert plan_order(intent(symbol="AAPL261218C00200000"), expiring).kind == PlanKind.REJECT
    assert plan_order(intent(symbol="AAPL261218C00200000", reduces_position=True),
                      expiring).kind != PlanKind.REJECT


def test_holiday_schedules_for_the_next_trading_day():
    thanksgiving = datetime(2026, 11, 26, 15, 0, tzinfo=UTC)
    p = plan_order(intent(), S.session("AAPL", thanksgiving), now=thanksgiving)
    assert p.kind == PlanKind.SCHEDULED and p.valid_from.startswith("2026-11-27T14:35")


def test_a_london_thesis_schedules_on_london_time():
    sat = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)
    p = plan_order(intent(symbol="BARC.L"), S.session("BARC.L", sat), now=sat)
    assert p.valid_from == datetime(2026, 10, 12, 7, 5, tzinfo=UTC).isoformat()   # 08:05 BST


# ── pre-open estimate ───────────────────────────────────────────────────────

def _gaps(n=250, sd=0.01, seed=0):
    rng = random.Random(seed)
    return [rng.gauss(0, sd) for _ in range(n)]


def test_estimate_states_an_interval_with_its_basis():
    e = estimate_open(100.0, _gaps())
    assert e.low < e.expected_open < e.high
    assert e.confidence == pytest.approx(0.6827, abs=1e-3)
    assert 0.55 < e.coverage_in_sample < 0.8
    assert any("not an out-of-sample validation" in r for r in e.reasons)


def test_too_little_history_states_no_interval():
    e = estimate_open(100.0, _gaps(n=10))
    assert e.expected_open == 100.0 and e.low is None and e.confidence is None


def test_a_fresh_overnight_proxy_moves_the_estimate():
    last_close = datetime(2026, 10, 6, 20, 0, tzinfo=UTC)
    now = datetime(2026, 10, 7, 13, 0, tzinfo=UTC)
    proxy = ProxyMove("ES=F", log_return=-0.02, beta=1.0,
                      observed_at=now - timedelta(minutes=2))
    e = estimate_open(100.0, _gaps(), proxy=proxy, target_last_close=last_close, now=now)
    assert e.expected_open == pytest.approx(100.0 * math.exp(-0.02), rel=1e-6)
    assert "ES=F" in e.method


def test_a_stale_or_pre_close_proxy_is_excluded_and_named():
    last_close = datetime(2026, 10, 6, 20, 0, tzinfo=UTC)
    now = datetime(2026, 10, 7, 13, 0, tzinfo=UTC)
    stale = ProxyMove("ES=F", -0.02, 1.0, observed_at=now - timedelta(hours=3))
    before = ProxyMove("ES=F", -0.02, 1.0, observed_at=last_close - timedelta(minutes=5))
    for p in (stale, before):
        e = estimate_open(100.0, _gaps(), proxy=p, target_last_close=last_close, now=now)
        assert "ES=F" not in e.method
        assert any("excluded" in r for r in e.reasons)


# ── adapting to the actual open ─────────────────────────────────────────────

def _plan():
    return plan_order(intent(), S.session("AAPL", CLOSED_T), now=CLOSED_T)


def obs(open_price, **kw):
    base = dict(observed_minutes=10, spread_bps=5, liquidity_ok=True)
    return OpenObservation(open_price, **{**base, **kw})


def test_nothing_is_decided_on_the_opening_print():
    assert adapt_at_open(_plan(), obs(100.2, observed_minutes=1)).action == OpenAction.WAIT


@pytest.mark.parametrize("open_price,action,mult", [
    (99.5, OpenAction.NORMAL, 1.0),        # slightly favourable
    (100.6, OpenAction.NORMAL, 1.0),       # 0.3 ATR adverse
    (101.6, OpenAction.REDUCE, 0.5),       # 0.8 ATR adverse
    (103.5, OpenAction.REASSESS, 0.0),     # 1.75 ATR — extreme gap
    (95.0, OpenAction.REASSESS, 0.0),      # opened through the stop
])
def test_opening_gap_rules(open_price, action, mult):
    dec = adapt_at_open(_plan(), obs(open_price))
    assert dec.action == action and dec.size_multiplier == mult


def test_news_liquidity_and_risk_override_a_good_open():
    assert adapt_at_open(_plan(), obs(100.0, material_news=True)).action == OpenAction.SUSPEND
    assert adapt_at_open(_plan(), obs(100.0, spread_bps=120)).action == OpenAction.DEFER
    assert adapt_at_open(_plan(), obs(100.0, liquidity_ok=None)).action == OpenAction.DEFER
    assert adapt_at_open(_plan(), obs(100.0, risk_ok=False)).action == OpenAction.REJECT


def test_a_short_side_gap_is_judged_from_its_own_side():
    plan = plan_order(intent(side="sell", stop_price=104.0), S.session("AAPL", CLOSED_T),
                      now=CLOSED_T)
    assert adapt_at_open(plan, obs(98.4)).action == OpenAction.REDUCE     # selling 0.8 ATR lower
    assert adapt_at_open(plan, obs(104.5)).action == OpenAction.REASSESS  # through the stop


def test_session_orders_cannot_reach_a_broker_or_a_model():
    """Planning is pure: its only imports are the standard library, so it
    cannot place an order, read a clock it was not given, or ask a model."""
    import ast
    tree = ast.parse(Path("src/trading/session_orders.py").read_text(encoding="utf-8"))
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module.split(".")[0])
    assert mods <= {"__future__", "math", "statistics", "dataclasses", "datetime",
                    "enum", "typing"}, mods
