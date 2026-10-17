"""
tests/test_session_bypass.py
============================
No path may place or arm an order without the authoritative session check
(src/markets/sessions), and UNKNOWN never authorizes execution.

Found and fixed while writing these (2026-10-07):

* OrderManager.execute — the owner's approve button, the desk's
  auto-executor and the reflex lane all send through it — had no session
  check. The desk checked New York's session when a cycle STARTED; a cycle
  that ran past the close sent a market order that would fill blind at the
  next open. execute() now checks the instrument's own session at the moment
  of sending, and a closed or UNKNOWN market leaves the trade pending.
* The exit engine resolved instruments by symbol shape only, so a broker
  position Alpaca reports as "BTCUSD" was gated on NYSE hours.

The structural test at the bottom pins every place in the code that sends
an order, so a new one cannot appear without being reviewed against this.

Time is frozen by replacing the session engine's clock, never by stubbing
the calendar logic under test.
"""
import ast
import inspect
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))

pytest.importorskip("exchange_calendars")

from src.execution.broker_base import (AssetClass, OrderRequest, OrderResult,   # noqa: E402
                                       OrderSide, OrderStatus, OrderType)
from src.markets import calendars, providers, sessions as S                    # noqa: E402

UTC = timezone.utc
TUE_OPEN = datetime(2026, 10, 6, 15, 0, tzinfo=UTC)          # 11:00 New York
SATURDAY = datetime(2026, 10, 10, 15, 0, tzinfo=UTC)
THANKSGIVING = datetime(2026, 11, 26, 16, 0, tzinfo=UTC)
AFTER_HOURS = datetime(2026, 10, 6, 21, 0, tzinfo=UTC)       # 17:00 New York, post-market
UNCOVERED = datetime(2031, 3, 4, 15, 0, tzinfo=UTC)          # no calendar for the year


@pytest.fixture(autouse=True)
def _chain():
    providers.default_chain()
    calendars.clear_cache()
    yield
    providers.default_chain()


def freeze(monkeypatch, instant):
    """Make the session engine's 'now' a fixed instant."""
    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return instant if tz is None else instant.astimezone(tz)
    monkeypatch.setattr(S, "datetime", Frozen)


class RecordingBroker:
    name = "Recording"
    paper = True

    # The send path now values the book and prices the order before it
    # sends (src/risk/pretrade): an account to measure against and a timed
    # quote. `quote_time` is set by each test to the instant it sends at.
    quote_time = None

    def get_account(self):
        from src.execution.broker_base import AccountInfo
        return AccountInfo(broker=self.name, cash=1e6, portfolio_value=1e6,
                           buying_power=1e6, currency="USD")

    def get_quote(self, ticker):
        return {"bid": 9.99, "ask": 10.01, "mid": 10.0,
                "timestamp": self.quote_time.isoformat() if self.quote_time else None}

    def __init__(self):
        self.submitted = []

    def is_connected(self):
        return True

    def submit_order(self, req):
        self.submitted.append(req)
        return OrderResult(broker_order_id=f"o{len(self.submitted)}",
                           status=OrderStatus.FILLED, filled_qty=req.qty, avg_fill_price=10.0)

    def get_order_status(self, oid):
        return OrderResult(broker_order_id=oid, status=OrderStatus.FILLED)

    def cancel_order(self, oid):
        return True

    def get_open_orders(self):
        return []

    def get_positions(self):
        return []


def queue_trade(tmp_path, monkeypatch, ticker="AAPL", asset_class=AssetClass.EQUITY):
    from src.execution import approval_queue as aq
    monkeypatch.setattr(aq, "QUEUE_FILE", tmp_path / "queue.json")
    req = OrderRequest(ticker=ticker, side=OrderSide.BUY, qty=1, order_type=OrderType.MARKET,
                       asset_class=asset_class)
    return aq.ApprovalQueue().push(req, current_price=10.0, broker="alpaca")


# ═══ the approval-queue path: owner approve, desk auto-execute, reflex ═════

@pytest.mark.parametrize("ticker,ac,when,sent", [
    ("AAPL", AssetClass.EQUITY, TUE_OPEN, True),
    ("AAPL", AssetClass.EQUITY, SATURDAY, False),
    ("AAPL", AssetClass.EQUITY, THANKSGIVING, False),
    ("AAPL", AssetClass.EQUITY, AFTER_HOURS, False),           # POST_MARKET is not OPEN
    ("AAPL", AssetClass.EQUITY, UNCOVERED, False),             # UNKNOWN date
    ("ENI.MI", AssetClass.EQUITY, TUE_OPEN, False),            # UNKNOWN venue
    ("RELIANCE.NS", AssetClass.EQUITY, datetime(2026, 10, 6, 5, 30, tzinfo=UTC), False),
    ("BTC-USD", AssetClass.CRYPTO, SATURDAY, True),
    ("BTCUSD", AssetClass.CRYPTO, SATURDAY, True),             # Alpaca's crypto format
    ("SPY", AssetClass.CRYPTO, SATURDAY, False),               # a label is not a venue
    ("ES", AssetClass.FUTURES, THANKSGIVING, False),           # US holiday: UNKNOWN for futures
    ("ESZ6", AssetClass.FUTURES, TUE_OPEN, False),             # unmodelled contract
    ("AAPL", AssetClass.OPTION, TUE_OPEN, False),              # an option needs its OCC symbol
])
def test_execute_checks_the_instruments_session_at_the_moment_of_sending(
        tmp_path, monkeypatch, ticker, ac, when, sent):
    from src.execution import approval_queue as aq
    from src.execution.order_manager import OrderManager
    broker = RecordingBroker()
    broker.quote_time = when
    queued = queue_trade(tmp_path, monkeypatch, ticker, ac)
    result = OrderManager(alpaca=broker, ibkr=broker).execute(queued.id, now=when)
    assert (len(broker.submitted) == 1) is sent, (ticker, when, result)
    assert result["ok"] is sent
    if not sent:
        assert result["session_closed"] is True
        assert "market not in continuous trading" in result["error"]
        assert aq.ApprovalQueue().get_by_id(queued.id).status == "pending"   # unsent, not lost


def test_execute_without_now_reads_the_engines_clock(tmp_path, monkeypatch):
    from src.execution.order_manager import OrderManager
    freeze(monkeypatch, SATURDAY)
    broker = RecordingBroker()
    queued = queue_trade(tmp_path, monkeypatch)
    assert OrderManager(alpaca=broker).execute(queued.id)["session_closed"] is True
    assert broker.submitted == []


class _Boom(providers.CalendarProvider):
    name = "boom"

    def facts(self, code, d, tz):
        raise RuntimeError("calendar backend exploded")


def test_a_calendar_provider_failure_never_authorizes_an_order(tmp_path, monkeypatch):
    from src.execution.order_manager import OrderManager
    providers.set_chain([_Boom()])
    broker = RecordingBroker()
    queued = queue_trade(tmp_path, monkeypatch, "0005.HK")
    hk_mid_session = datetime(2026, 10, 6, 2, 0, tzinfo=UTC)      # 10:00 HKT
    result = OrderManager(alpaca=broker).execute(queued.id, now=hk_mid_session)
    assert result["ok"] is False and broker.submitted == []


def test_a_session_engine_exception_is_a_refusal(tmp_path, monkeypatch):
    from src.execution.order_manager import OrderManager

    def explode(*a, **k):
        raise RuntimeError("engine fault")
    monkeypatch.setattr(S, "session", explode)
    broker = RecordingBroker()
    queued = queue_trade(tmp_path, monkeypatch)
    result = OrderManager(alpaca=broker).execute(queued.id, now=TUE_OPEN)
    assert result["ok"] is False and broker.submitted == []
    assert "unknown is never open" in result["error"]


def test_the_desk_queues_a_trade_whose_market_closed_mid_cycle(tmp_path, monkeypatch):
    """All of the desk's gates green, but the market has closed by the time
    the slate executes: the trade is queued for the owner, nothing is sent."""
    from src.desk import auto_executor as ae
    from src.execution.order_manager import OrderManager
    from src.execution import approval_queue as aq
    monkeypatch.setattr(aq, "QUEUE_FILE", tmp_path / "queue.json")
    broker = RecordingBroker()
    monkeypatch.setattr(ae, "get_order_manager", lambda *a, **k: OrderManager(alpaca=broker))
    monkeypatch.setattr(ae, "account_snapshot", lambda: {"connected": True, "equity": 1e5})
    monkeypatch.setattr(ae.AutoExecutor, "gates",
                        lambda self, account=None: {"auto_allowed": True, "budget_left": 1e9})
    monkeypatch.setattr(ae.AutoExecutor, "_notify", lambda self, *a: None)
    freeze(monkeypatch, AFTER_HOURS)
    entry = {"ticker": "AAPL", "side": "buy", "qty": 1, "asset_class": "equity",
             "price": 10.0, "notional": 10.0, "conviction": 80, "thesis": "t"}
    record = ae.AutoExecutor({"auto_execute": True})._handle(entry, {})
    assert record["mode"] == "queued" and broker.submitted == []
    assert "POST_MARKET" in record["reason"]


# ═══ the exit engine ════════════════════════════════════════════════════════

def _close(monkeypatch, ticker, asset_class):
    from test_kill_switch_and_profiles import _FakeBroker, _FakeMgr
    from src.desk import position_manager as pm
    broker = _FakeBroker({ticker: 10})
    pmgr = pm.PositionManager.__new__(pm.PositionManager)
    pmgr.cfg = {}
    monkeypatch.setattr(pmgr, "_log_execution", lambda rec: None, raising=False)
    monkeypatch.setattr(pmgr, "_log_closed", lambda rec: None, raising=False)
    monkeypatch.setattr("src.desk.notify.push", lambda msg, cfg: None)
    monkeypatch.setattr("src.execution.live_guard.paper_confirmed", lambda broker: True)
    tracked = {ticker: {"asset_class": asset_class, "side": "long",
                        "entry_price": 100.0, "qty": 10}}
    out = pmgr._close(tracked, ticker, {"qty": 10, "side": "long"}, _FakeMgr(broker),
                      reason="stop hit")
    return out, broker


@pytest.mark.parametrize("when,ticker,ac,sent", [
    (TUE_OPEN, "AAPL", "equity", True),
    (SATURDAY, "AAPL", "equity", False),
    (THANKSGIVING, "AAPL", "equity", False),
    (UNCOVERED, "AAPL", "equity", False),
    (SATURDAY, "BTCUSD", "crypto", True),
    (SATURDAY, "SPY", "crypto", False),
])
def test_exits_wait_for_continuous_trading(monkeypatch, when, ticker, ac, sent):
    freeze(monkeypatch, when)
    out, broker = _close(monkeypatch, ticker, ac)
    assert (len(broker.submitted) == 1) is sent, (when, ticker, out)


# ═══ the other paths ════════════════════════════════════════════════════════

def test_held_intents_are_held_on_unknown():
    from src.desk import intents as I
    intent = {"id": "i1", "status": I.OPEN, "ticker": "RELIANCE.NS", "side": "buy",
              "qty": 1, "expires_at": "2099-01-01T00:00:00"}
    sess = S.session("RELIANCE.NS", datetime(2026, 10, 6, 5, 30, tzinfo=UTC)).to_dict()
    assert sess["state"] == "UNKNOWN"
    d = I.evaluate(intent, price=100.0, session=sess)
    assert d["ready"] is False and d.get("session_closed") is True


@pytest.mark.usefixtures("clean_trading_state")
@pytest.mark.parametrize("symbol,when", [("ENI.MI", TUE_OPEN), ("AAPL", THANKSGIVING),
                                         ("AAPL", UNCOVERED)])
def test_the_pipeline_stops_unknown_and_holidays_at_the_session_stage(symbol, when):
    from test_trading_pipeline import broker, pipeline, ready_strategy, set_mode
    from trading_helpers import make_quote, make_signal
    ready_strategy()
    set_mode("PAPER")
    b = broker()
    b.set_quote(make_quote(symbol=symbol, now=when))
    res = pipeline(b).run(make_signal(symbol=symbol, now=when), now=when)
    assert res.submitted is False and res.stage_reached == "session"
    assert res.decision is None and b.submit_calls == 0


def test_the_validator_refuses_unknown_on_its_own():
    from src.trading.validator import validate
    from src.trading.account import unknown as unknown_account
    from src.trading import limits as _limits
    from src.trading.broker import OrderSpec
    from trading_helpers import make_quote
    for symbol, when in (("ENI.MI", TUE_OPEN), ("AAPL", UNCOVERED)):
        order = OrderSpec(client_order_id="c", symbol=symbol, side="buy", qty=1)
        v = validate(order, quote=make_quote(symbol=symbol, now=when),
                     account=unknown_account("x"), limits=_limits.load(), now=when,
                     seen_client_ids=set())
        assert any("market closed" in x for x in v), (symbol, v)


def test_market_open_reads_a_known_asset_class():
    from src.trading.market import market_open
    assert market_open("crypto", SATURDAY, symbol="BTCUSD")[0] is True
    assert market_open("crypto", SATURDAY, symbol="SPY")[0] is False
    assert market_open("equity", SATURDAY, symbol="BTCUSD")[0] is False


def test_nothing_asks_for_a_guessed_close():
    """src/trading/market.session_close used to fall back to 16:00 for a day
    the calendar could not answer — three hours late on a half day."""
    from src.trading import market
    assert market.session_close(date(2031, 3, 4)) is None
    assert market.session_close(date(2026, 11, 27)).hour == 13


# ═══ structure: every place that sends an order is accounted for ═══════════

#: path -> (call sites, why each is safe). A new call site fails this test
#: until someone states how it passes the session check.
SENDERS = {
    "src/trading/orders.py": (1, "OrderEngine.submit: reached only from TradingPipeline.run, "
                                 "after its SESSION stage and the validator's session check"),
    "src/trading/broker.py": (1, "LegacyBrokerAdapter forwarding to the wrapped broker"),
    "src/execution/order_manager.py": (3, "execute(): session_refusal() before the entry, "
                                          "reduce_only.at_broker() for a queued exit; protective "
                                          "OCO, or ONE stop-or-target order (Codex 09f2158 merged "
                                          "the two): reduce_only.at_broker() first"),
    "src/execution/alpaca_broker.py": (2, "the Alpaca adapter itself (order + OCO exit)"),
    "src/execution/ibkr_broker.py": (1, "the IBKR adapter itself"),
    "src/desk/position_manager.py": (1, "exit engine: instrument_open(ticker, asset_class) first"),
    "src/trading/killswitch.py": (1, "halt flatten: reduce-only closes on a halt, deliberately "
                                     "below the normal path; the kill switch is not session-gated"),
}
_SEND = re.compile(r"\.(submit_order|submit_oco_exit|placeOrder|place_order|send_order|"
                   r"close_position|close_all_positions|replace_order|liquidate)\(")


def test_every_order_sending_call_site_is_accounted_for():
    found = {}
    for base in ("src", "backend"):
        for p in (ROOT / base).rglob("*.py"):
            if "__pycache__" in p.parts:
                continue
            n = sum(1 for line in p.read_text(encoding="utf-8", errors="ignore").splitlines()
                    if _SEND.search(line) and not line.lstrip().startswith(("#", "def ")))
            if n:
                found[p.relative_to(ROOT).as_posix()] = n
    assert found == {k: v[0] for k, v in SENDERS.items()}, found


def _source(fn) -> str:
    return inspect.getsource(fn)


def test_the_session_check_precedes_the_send_in_each_gated_path():
    from src.desk.position_manager import PositionManager
    from src.execution.order_manager import OrderManager
    from src.trading.pipeline import TradingPipeline
    from src.trading import killswitch
    for fn, check, send in (
            (OrderManager.execute, "session_refusal(", "broker.submit_order(req)"),
            (OrderManager.execute, "reduce_only.at_broker(", "broker.submit_order(req)"),
            (OrderManager.place_protective_orders, "reduce_only.at_broker(",
             "broker.submit_oco_exit("),
            (PositionManager._close, "instrument_open(", "broker.submit_order(req)"),
            (PositionManager._close, "reduce_only.at_broker(", "broker.submit_order(req)"),
            (TradingPipeline.run, "_sessions.session(", "self.orders.submit("),
            (TradingPipeline.run, "at_send = _sessions.session(", "self.orders.submit("),
            (TradingPipeline.run, "self._portfolio_risk(", "self.orders.submit("),
            (OrderManager.execute, "self.portfolio_risk(", "broker.submit_order(req)"),
            (killswitch._flatten, "reduce_only.check(", "broker.submit_order(spec)"),
    ):
        src = _source(fn)
        assert check in src and send in src, fn.__qualname__
        assert src.index(check) < src.index(send), fn.__qualname__


#: Modules allowed to BUILD a plan: they produce an inert preview and import
#: nothing that can send an order (checked below). Refined 2026-10-07 when the
#: portfolio-risk layer began attaching a deferral plan to a closed-market
#: decision; the invariant was always "no execution path", not "no module".
PLAN_PREVIEWERS = {"src/risk/pretrade.py"}
SENDING_MODULES = ("src.execution.order_manager", "src.execution.alpaca_broker",
                   "src.execution.ibkr_broker", "src.trading.orders", "src.trading.broker",
                   "src.trading.pipeline", "src.trading.access", "src.desk")


def test_planned_orders_cannot_reach_a_broker():
    """session_orders plans are previews: no execution path imports them,
    and the modules that build one cannot send anything."""
    for rel in PLAN_PREVIEWERS:
        tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
        imported = {(n.module or "") for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        imported |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        assert not any(m.startswith(SENDING_MODULES) for m in imported), (rel, imported)
    importers = []
    for p in (ROOT / "src").rglob("*.py"):
        if "__pycache__" in p.parts or p.name == "session_orders.py":
            continue
        tree = ast.parse(p.read_text(encoding="utf-8", errors="ignore"))
        for node in ast.walk(tree):
            names = ([a.name for a in node.names] if isinstance(node, ast.Import) else
                     [node.module or ""] if isinstance(node, ast.ImportFrom) else [])
            if any("session_orders" in n for n in names) or (
                    isinstance(node, ast.ImportFrom) and
                    any(a.name == "session_orders" for a in node.names)):
                importers.append(p.relative_to(ROOT).as_posix())
    assert set(importers) <= PLAN_PREVIEWERS, importers
