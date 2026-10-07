"""
tests/test_send_time.py
=======================
Planning-time validity is not execution-time validity. Whatever was true
when an order was planned, queued, armed or held, the state at the moment it
is SENT is the one that authorizes it.

Each case plans in one state and sends in another. Instants are written by
hand; the engine is never asked what they should be.
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))

pytest.importorskip("exchange_calendars")

from src.execution.broker_base import (AssetClass, OrderRequest, OrderResult,   # noqa: E402
                                       OrderSide, OrderStatus, OrderType)
from src.markets import calendars, providers, sessions as S                    # noqa: E402
from src.markets.sessions import SessionState as St                           # noqa: E402

UTC = timezone.utc


def u(*a):
    return datetime(*a, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _chain():
    providers.default_chain()
    calendars.clear_cache()
    yield
    providers.default_chain()
    calendars.clear_cache()


class Recorder:
    name = "recorder"
    paper = True

    def __init__(self):
        self.sent = []

    def is_connected(self):
        return True

    def get_positions(self):
        return []

    def get_open_orders(self):
        return []

    def submit_order(self, req):
        self.sent.append(req)
        return OrderResult(broker_order_id="x", status=OrderStatus.FILLED,
                           filled_qty=req.qty, avg_fill_price=10.0)


def queue(tmp_path, monkeypatch, ticker="AAPL", ac=AssetClass.EQUITY):
    from src.execution import approval_queue as aq
    monkeypatch.setattr(aq, "QUEUE_FILE", tmp_path / "q.json")
    return aq.ApprovalQueue().push(OrderRequest(ticker=ticker, side=OrderSide.BUY, qty=1,
                                                order_type=OrderType.MARKET, asset_class=ac),
                                   broker="alpaca")


# ═══ A2: the approval path (owner approve, desk auto-execute, reflex) ══════

TRANSITIONS = [
    # name, symbol, planned at (state), sent at, halted at send, expect sent
    ("OPEN -> CLOSED (Shanghai 14:50 -> 15:01)", "600519.SS",
     u(2026, 10, 9, 6, 50), St.OPEN, u(2026, 10, 9, 7, 1), None, False),
    ("OPEN -> POST_MARKET (NY 15:59 -> 16:01)", "AAPL",
     u(2026, 10, 6, 19, 59), St.OPEN, u(2026, 10, 6, 20, 1), None, False),
    ("OPEN -> CLOSED (NY 15:59 -> 20:01)", "AAPL",
     u(2026, 10, 6, 19, 59), St.OPEN, u(2026, 10, 7, 0, 1), None, False),
    ("OPEN -> HALTED", "AAPL",
     u(2026, 10, 6, 15, 0), St.OPEN, u(2026, 10, 6, 15, 2), True, False),
    ("OPEN -> UNKNOWN (past the calendar horizon)", "AAPL",
     u(2027, 12, 31, 15, 0), St.OPEN, u(2028, 1, 3, 15, 0), None, False),
    ("OPEN -> HOLIDAY (Wednesday -> Thanksgiving)", "AAPL",
     u(2026, 11, 25, 16, 0), St.OPEN, u(2026, 11, 26, 16, 0), None, False),
    ("PRE_OPEN -> OPEN (09:00 -> 09:31 New York)", "AAPL",
     u(2026, 10, 6, 13, 0), St.PRE_OPEN, u(2026, 10, 6, 13, 31), None, True),
]


@pytest.mark.parametrize("name,sym,planned,planned_state,sent_at,halted,expect",
                         TRANSITIONS, ids=[t[0] for t in TRANSITIONS])
def test_the_send_time_state_decides(tmp_path, monkeypatch, name, sym, planned,
                                     planned_state, sent_at, halted, expect):
    from src.execution.order_manager import OrderManager
    assert S.session(sym, planned).state == planned_state, name
    q = queue(tmp_path, monkeypatch, sym)
    rec = Recorder()
    res = OrderManager(alpaca=rec).execute(q.id, now=sent_at, halted=halted)
    assert bool(rec.sent) is expect, (name, res)
    if not expect:
        assert res["session_closed"] is True


def test_a_provider_that_fails_between_plan_and_send_refuses(tmp_path, monkeypatch):
    from src.execution.order_manager import OrderManager

    class Boom(providers.CalendarProvider):
        name = "boom"

        def facts(self, code, d, tz):
            raise RuntimeError("calendar backend died")
    t = u(2026, 10, 6, 2, 0)                                   # 10:00 Hong Kong
    assert S.session("0005.HK", t).tradable
    q = queue(tmp_path, monkeypatch, "0005.HK")
    providers.set_chain([Boom()])
    rec = Recorder()
    assert OrderManager(alpaca=rec).execute(q.id, now=t)["ok"] is False
    assert rec.sent == []


# ═══ A2: the pipeline's own send boundary ═══════════════════════════════════

@pytest.mark.usefixtures("clean_trading_state")
@pytest.mark.parametrize("send_at,expect", [
    (u(2026, 10, 6, 19, 59, 30), True),        # still inside the session
    (u(2026, 10, 6, 20, 1), False),            # the close passed before the send
    (u(2031, 3, 4, 15, 0), False),             # UNKNOWN by the time it leaves
])
def test_the_pipeline_rechecks_the_session_at_the_send_boundary(send_at, expect):
    from test_trading_pipeline import broker, pipeline, ready_strategy, set_mode
    from trading_helpers import make_quote, make_signal
    planned = u(2026, 10, 6, 19, 59)                           # 15:59 New York
    ready_strategy()
    set_mode("PAPER")
    b = broker()
    b.set_quote(make_quote(now=planned))
    res = pipeline(b).run(make_signal(now=planned), now=planned, send_at=send_at)
    assert res.session["state"] == "OPEN"                      # valid when planned
    assert res.submitted is expect, res.reasons
    if not expect:
        assert res.stage_reached == "session" and b.submit_calls == 0
        assert res.decision["approved"] is True                # the plan itself was fine
        assert "session changed before send" in " ".join(res.reasons)
        assert res.session_at_send["tradable"] is False


# ═══ A2/A3: plans, conditional orders, held intents ═════════════════════════

def _plan(sym, at, **kw):
    from src.trading.session_orders import SessionPolicy, TradeIntent, plan_order
    intent = TradeIntent(symbol=sym, side="buy", qty=1, **kw)
    return plan_order(intent, S.session(sym, at), SessionPolicy(), now=at)


def test_a_plan_made_while_open_does_not_authorize_a_send_after_the_close(tmp_path,
                                                                           monkeypatch):
    from src.execution.order_manager import OrderManager
    from src.trading.session_orders import PlanKind
    plan = _plan("AAPL", u(2026, 10, 6, 19, 59))
    assert plan.kind == PlanKind.EXECUTE_NOW
    assert not hasattr(plan, "execute") and not hasattr(plan, "submit")   # plans are inert
    q = queue(tmp_path, monkeypatch, plan.symbol)
    rec = Recorder()
    assert OrderManager(alpaca=rec).execute(q.id, now=u(2026, 10, 6, 20, 1))["ok"] is False
    assert rec.sent == []
    replanned = _plan("AAPL", u(2026, 10, 6, 20, 1))
    assert replanned.kind == PlanKind.SCHEDULED


def test_a_conditional_plan_does_not_wake_on_a_holiday(tmp_path, monkeypatch):
    from src.execution.order_manager import OrderManager
    from src.trading.session_orders import PlanKind
    wed_close = u(2026, 11, 25, 21, 30)                         # after Wednesday's close
    plan = _plan("AAPL", wed_close, trigger_price=150.0, reference_price=148.0, atr=2.0)
    assert plan.kind == PlanKind.CONDITIONAL
    assert plan.valid_from.startswith("2026-11-27T14:35")       # Friday 09:35 ET, not Thursday
    q = queue(tmp_path, monkeypatch)
    rec = Recorder()
    thanksgiving = u(2026, 11, 26, 16, 0)
    assert OrderManager(alpaca=rec).execute(q.id, now=thanksgiving)["ok"] is False
    assert rec.sent == []


def test_a_plan_made_while_unknown_stays_deferred_until_replanned():
    from src.trading.session_orders import PlanKind
    unknown = _plan("RELIANCE.NS", u(2026, 10, 6, 5, 30))       # no NSE calendar here
    assert unknown.kind == PlanKind.DEFERRED and unknown.valid_from is None
    assert any("UNKNOWN" in r for r in unknown.reasons)


def test_a_held_intent_crossing_a_session_boundary_is_held():
    from src.desk import intents as I
    intent = {"id": "i", "status": I.OPEN, "ticker": "AAPL", "side": "buy", "qty": 1,
              "expires_at": "2099-01-01T00:00:00"}
    open_ = S.session("AAPL", u(2026, 10, 6, 19, 59)).to_dict()
    closed = S.session("AAPL", u(2026, 10, 6, 20, 1)).to_dict()
    assert I.evaluate(intent, price=100.0, session=open_)["ready"] is True
    held = I.evaluate(intent, price=100.0, session=closed)
    assert held["ready"] is False and held["session_closed"] is True
    # and an intent has no execution path of its own: without an injected
    # fill it goes nowhere near a broker
    assert I.execute(intent, 100.0)["ok"] is False


def test_an_instrument_that_resolves_differently_at_send_is_refused(tmp_path, monkeypatch):
    """Queued while NSE's calendar answered OPEN; by the time it is sent the
    calendar is gone (or the listing maps nowhere) and the venue is UNKNOWN."""
    from src.execution.order_manager import OrderManager
    from src.markets import venues
    t = u(2026, 10, 6, 5, 30)                                   # 11:00 IST
    monkeypatch.setattr(calendars, "CALENDAR_DIR", ROOT / "data" / "calendars")
    calendars.clear_cache()
    assert S.session("RELIANCE.NS", t).tradable
    q = queue(tmp_path, monkeypatch, "RELIANCE.NS")
    monkeypatch.setattr(calendars, "CALENDAR_DIR", tmp_path / "gone")
    calendars.clear_cache()
    rec = Recorder()
    assert OrderManager(alpaca=rec).execute(q.id, now=t)["ok"] is False
    monkeypatch.setattr(calendars, "CALENDAR_DIR", ROOT / "data" / "calendars")
    calendars.clear_cache()
    monkeypatch.delitem(venues.EXCHANGE_ALIASES, "NSE")
    assert OrderManager(alpaca=rec).execute(q.id, now=t)["ok"] is False
    assert rec.sent == []


@pytest.mark.usefixtures("clean_trading_state")
def test_mode_and_kill_switch_are_read_at_send_time_on_the_approval_path(tmp_path,
                                                                        monkeypatch):
    """Queued in PAPER; the mode drops to RESEARCH before the owner approves.
    The gate re-reads the mode on the call that sends — nothing reaches the
    raw broker. Then the kill switch: the adapter refuses below every gate."""
    from src.execution.order_manager import OrderManager
    from src.execution import kill_switch
    from src.execution.alpaca_broker import AlpacaBroker
    from src.trading import access, modes
    from trading_helpers import FakeLegacyBroker
    monkeypatch.setenv("ALPACA_PAPER", "true")
    t = u(2026, 10, 6, 15, 0)
    modes.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    modes.STATE_FILE.write_text('{"mode": "PAPER"}', encoding="utf-8")
    raw = FakeLegacyBroker(paper=True)
    raw.is_connected = lambda: True
    q = queue(tmp_path, monkeypatch)
    modes.STATE_FILE.write_text('{"mode": "RESEARCH"}', encoding="utf-8")
    res = OrderManager(alpaca=access.gate_for_approvals(raw)).execute(q.id, now=t)
    assert res["ok"] is False and raw.submitted == []

    class Connected(AlpacaBroker):
        def __init__(self):
            self._client = None

        def is_connected(self):
            return True
    modes.STATE_FILE.write_text('{"mode": "PAPER"}', encoding="utf-8")
    q2 = queue(tmp_path, monkeypatch)
    kill_switch.engage("test", actor="test")
    try:
        res = OrderManager(alpaca=Connected()).execute(q2.id, now=t)
        assert res["ok"] is False and "KILL SWITCH" in res["error"]
    finally:
        kill_switch.release(actor="test")
