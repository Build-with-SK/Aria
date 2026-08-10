"""
tests/test_security_ibkr_status.py
==================================
AUDIT FINDING 2 — "IBKR adapter order-status misreporting".

The adapter's job is to report what IBKR says, not what the adapter assumed.
Every test here builds an ib_insync-shaped trade object in a state IBKR really
produces, and asserts the reported status matches that state — the three the
audit named (partial fill, rejection, cancel) plus the transitions around them
where "reasonable-looking" code quietly invents a status.

The fakes deliberately mirror ib_insync's shape: orderStatus is a running
summary, trade.fills are the execution events, trade.log carries the reason a
rejected order was rejected. Where the summary and the events disagree, the
events are the record of what happened.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.execution.broker_base import OrderStatus     # noqa: E402


# ── ib_insync-shaped fakes ───────────────────────────────────────────────────

class FakeExecution:
    def __init__(self, shares, price):
        self.shares = shares
        self.price = price


class FakeFill:
    def __init__(self, shares, price):
        self.execution = FakeExecution(shares, price)


class FakeOrderStatus:
    def __init__(self, status, filled=0.0, remaining=0.0, avg=0.0):
        self.status = status
        self.filled = filled
        self.remaining = remaining
        self.avgFillPrice = avg


class FakeLogEntry:
    def __init__(self, message):
        self.message = message


class FakeOrder:
    def __init__(self, order_id):
        self.orderId = order_id
        self.totalQuantity = 0.0


class FakeTrade:
    def __init__(self, order_id, status, filled=0.0, remaining=0.0, avg=0.0,
                 fills=(), log=(), total=None):
        self.order = FakeOrder(order_id)
        # The order size, which is what makes filled/remaining checkable.
        self.order.totalQuantity = (total if total is not None
                                    else (filled or 0) + (remaining or 0))
        self.orderStatus = FakeOrderStatus(status, filled, remaining, avg)
        self.fills = list(fills)
        self.log = [FakeLogEntry(m) for m in log]


class FakeIB:
    def __init__(self, trades=()):
        self._trades = list(trades)
        self.cancelled = []

    def trades(self):
        return self._trades

    def openTrades(self):
        return [t for t in self._trades
                if t.orderStatus.status in ("Submitted", "PreSubmitted",
                                            "PendingSubmit")]

    def cancelOrder(self, order):
        self.cancelled.append(order.orderId)


def broker_with(fake_ib):
    from src.execution.ibkr_broker import IBKRBroker
    b = IBKRBroker.__new__(IBKRBroker)          # no __init__, no TWS connection
    b._ib = fake_ib
    b.is_connected = lambda: True
    return b


class _FakeOrderObject:
    """Whatever ib_insync would have built. Only `tif` is ever touched."""
    def __init__(self, *args, **kwargs):
        self.tif = "DAY"


@pytest.fixture
def no_ib_insync_needed(monkeypatch):
    """Building contracts and order objects is ib_insync's job, and ib_insync
    is not installed in CI (nor on any machine without TWS). Stub both so what
    these tests exercise is the adapter's status handling, not the presence of
    a broker SDK — the whole point is that they run everywhere."""
    from src.execution import ibkr_broker

    class FakeIbi:
        Stock = Future = Forex = Crypto = _FakeOrderObject
        MarketOrder = LimitOrder = StopOrder = StopLimitOrder = _FakeOrderObject

    monkeypatch.setattr(ibkr_broker, "ibi", FakeIbi, raising=False)
    monkeypatch.setattr(ibkr_broker, "_make_contract",
                        lambda ticker, asset_class: object())


# ── 1. PARTIAL FILL ──────────────────────────────────────────────────────────

def test_partial_fill_is_reported_as_partial_not_submitted():
    """IBKR reports a half-filled order as "Submitted" with filled > 0 — the
    same label as an order that has not traded a share. Believing the label
    means owning 40 shares while reporting a position of nothing."""
    ib = FakeIB([FakeTrade(101, "Submitted", filled=40, remaining=60, avg=10.5,
                           fills=[FakeFill(40, 10.5)])])
    r = broker_with(ib).get_order_status("101")
    assert r.status == OrderStatus.PARTIAL
    assert r.filled_qty == 40
    assert r.avg_fill_price == 10.5


def test_partial_fill_is_not_reported_as_filled():
    """The other direction: PARTIAL must never round up to FILLED, or the
    caller places protective orders for shares it does not own."""
    ib = FakeIB([FakeTrade(102, "Submitted", filled=40, remaining=60,
                           fills=[FakeFill(40, 10.0)])])
    assert broker_with(ib).get_order_status("102").status != OrderStatus.FILLED


def test_filled_label_with_shares_remaining_is_partial():
    """A "Filled" label with remaining > 0 is a contradiction. The counter wins."""
    ib = FakeIB([FakeTrade(103, "Filled", filled=50, remaining=50,
                           fills=[FakeFill(50, 9.0)])])
    assert broker_with(ib).get_order_status("103").status == OrderStatus.PARTIAL


def test_cancelled_after_partial_fill_still_reports_the_position():
    """Cancelling the unfilled remainder does not undo the shares already
    bought. CANCELLED here would report a flat book over a real position."""
    ib = FakeIB([FakeTrade(104, "Cancelled", filled=30, remaining=0,
                           fills=[FakeFill(30, 12.0)])])
    r = broker_with(ib).get_order_status("104")
    assert r.status == OrderStatus.PARTIAL
    assert r.filled_qty == 30


def test_a_truncated_execution_stream_does_not_shrink_a_known_fill():
    """orderStatus.filled says 100, the visible executions add up to 40.

    This originally asserted the adapter must report 40 — "never claim more
    than the executions prove". That reasoning was wrong in a way an
    independent review had to point out: `trade.fills` holds only executions
    seen on THIS connection and clientId, so a reconnect mid-fill or a table
    rebuilt from reqAllOpenOrders leaves it truncated, while orderStatus is
    server-side and complete. Reporting 40 for a filled 100-share order leaves
    60 shares unhedged and unrecorded.

    Neither source ever invents shares, so the evidenced maximum is the honest
    number — and the disagreement is surfaced either way.
    """
    ib = FakeIB([FakeTrade(105, "Submitted", filled=100, remaining=0, avg=10.0,
                           fills=[FakeFill(40, 10.0)])])
    r = broker_with(ib).get_order_status("105")
    assert r.filled_qty == 100
    assert r.raw["fill_discrepancy"] is True
    assert r.raw["executions_filled"] == 40      # both numbers stay visible
    assert r.raw["order_status_filled"] == 100


def test_a_completed_order_is_not_downgraded_by_a_partial_execution_view():
    """The same truncation on a terminal order. 'Filled, remaining 0' with a
    partial execution view is a complete order seen incompletely, not a
    partial fill."""
    ib = FakeIB([FakeTrade(109, "Filled", filled=100, remaining=0, avg=10.0,
                           fills=[FakeFill(40, 10.0)])])
    r = broker_with(ib).get_order_status("109")
    assert r.status == OrderStatus.FILLED
    assert r.filled_qty == 100
    # The price must follow the count it belongs to: a VWAP over 40 observed
    # shares does not describe a 100-share fill.
    assert r.avg_fill_price == 10.0


def test_executions_ahead_of_the_summary_do_not_produce_a_zero_fill():
    """execDetails routinely arrives before the final orderStatus, so
    orderStatus.filled=0 with a 100-share execution on the books is normal.
    Taking the smaller number reported "FILLED, 0 shares" — a state that reads
    downstream as "entry complete, now place brackets" over a real position."""
    ib = FakeIB([FakeTrade(107, "Filled", filled=0, remaining=0,
                           fills=[FakeFill(100, 10.0)])])
    r = broker_with(ib).get_order_status("107")
    assert r.filled_qty == 100
    assert r.status == OrderStatus.FILLED


def test_filled_with_no_quantity_anywhere_is_not_a_fill():
    """If neither the summary nor the executions show a share, "Filled" is a
    contradiction and must not be reported as a completed order."""
    ib = FakeIB([FakeTrade(108, "Filled", filled=0, remaining=0, fills=[])])
    r = broker_with(ib).get_order_status("108")
    assert r.status != OrderStatus.FILLED
    assert r.status == OrderStatus.SUBMITTED
    assert "no executed quantity" in r.error_message


def test_a_rejection_after_a_partial_fill_keeps_its_reason():
    """"Inactive" with shares already filled maps to PARTIAL, and the reason
    used to be attached only when the final status was REJECTED — so IBKR's
    explanation was dropped in exactly the case that most needed it."""
    ib = FakeIB([FakeTrade(203, "Inactive", filled=40, remaining=60,
                           fills=[FakeFill(40, 10.0)],
                           log=["rejected: no shares available to borrow"])])
    r = broker_with(ib).get_order_status("203")
    assert r.status == OrderStatus.PARTIAL
    assert "no shares available to borrow" in r.error_message


def test_open_orders_are_reported_for_ibkr():
    """BrokerBase returns [] by default and IBKR never overrode it, so bracket
    healing, stale-order cleanup and the "is an exit already in flight" check
    all silently did nothing for every IBKR position."""
    from src.execution.ibkr_broker import IBKRBroker
    from src.execution.broker_base import BrokerBase
    assert IBKRBroker.get_open_orders is not BrokerBase.get_open_orders

    class Contract:
        symbol = "AAPL"

    class Order:
        orderId, action, orderType = 501, "SELL", "STP"
        totalQuantity, auxPrice, lmtPrice = 10, 95.0, 0.0

    trade = FakeTrade(501, "Submitted")
    trade.order = Order()
    trade.contract = Contract()
    orders = broker_with(FakeIB([trade])).get_open_orders()
    assert len(orders) == 1
    assert orders[0]["ticker"] == "AAPL"
    assert orders[0]["side"] == "sell"
    assert orders[0]["order_type"] == "stop"
    assert orders[0]["stop_price"] == 95.0


def test_true_full_fill_is_still_filled():
    """The guard against over-reporting must not under-report a real fill."""
    ib = FakeIB([FakeTrade(106, "Filled", filled=100, remaining=0, avg=10.25,
                           fills=[FakeFill(60, 10.0), FakeFill(40, 10.625)])])
    r = broker_with(ib).get_order_status("106")
    assert r.status == OrderStatus.FILLED
    assert r.filled_qty == 100
    assert round(r.avg_fill_price, 4) == 10.25      # volume weighted, from fills


def test_a_fill_count_larger_than_the_order_is_clamped_and_flagged():
    """Three passes produced three tiebreak rules between the two fill
    counters — min, then executions-only, then max — each justified by naming
    the previous one's failure, and each with a counter-case. The invariant was
    sitting unused the whole time: filled + remaining must equal the order
    size, and a count that breaks it is not evidence to prefer, it is a number
    to refuse.

    A stale-high orderStatus.filled (a bust or correction re-reported) lands
    here."""
    ib = FakeIB([FakeTrade(110, "Submitted", filled=150, remaining=100,
                           total=100)])
    r = broker_with(ib).get_order_status("110")
    assert r.filled_qty == 100                  # clamped to the order size
    assert r.raw["reconciled"] is False
    assert "reconcile" in r.error_message


def test_counts_that_do_not_add_up_are_reported_unreconciled():
    ib = FakeIB([FakeTrade(111, "Submitted", filled=40, remaining=40,
                           total=100, fills=[FakeFill(40, 10.0)])])
    r = broker_with(ib).get_order_status("111")
    assert r.raw["reconciled"] is False
    assert r.raw["total_quantity"] == 100


def test_a_consistent_partial_fill_reconciles_cleanly():
    """The check must not cry wolf on the ordinary case."""
    ib = FakeIB([FakeTrade(112, "Submitted", filled=40, remaining=60,
                           total=100, fills=[FakeFill(40, 10.0)])])
    r = broker_with(ib).get_order_status("112")
    assert r.raw["reconciled"] is True
    assert r.status == OrderStatus.PARTIAL
    assert not r.error_message


def test_an_unknown_price_is_reported_as_unknown_not_as_a_partial_vwap():
    """The price fallback defeated its own comment: with avgFillPrice at 0 —
    the normal state while the summary leads the executions — a 100-share fill
    was priced at the VWAP of the 40 executions that happened to be visible."""
    ib = FakeIB([FakeTrade(113, "Filled", filled=100, remaining=0, avg=0.0,
                           total=100, fills=[FakeFill(40, 10.0)])])
    r = broker_with(ib).get_order_status("113")
    assert r.filled_qty == 100
    assert r.avg_fill_price == 0.0              # unknown, not 10.0
    assert "average price" in r.error_message


# ── 2. REJECTION ─────────────────────────────────────────────────────────────

def test_rejection_is_reported_as_rejected_with_ibkrs_reason():
    """IBKR says "Inactive" and puts the reason in the event log. Without
    reading the log every rejection is an unexplained dead order."""
    ib = FakeIB([FakeTrade(201, "Inactive", filled=0, remaining=100,
                           log=["Order rejected - reason:201 insufficient buying power"])])
    r = broker_with(ib).get_order_status("201")
    assert r.status == OrderStatus.REJECTED
    assert "insufficient buying power" in r.error_message


def test_rejection_never_looks_like_a_fill():
    ib = FakeIB([FakeTrade(202, "Inactive", filled=0, remaining=100)])
    r = broker_with(ib).get_order_status("202")
    assert r.status == OrderStatus.REJECTED
    assert r.filled_qty == 0
    assert r.error_message                       # never silently blank


def test_unknown_order_is_not_reported_filled_or_dead():
    """Absence proves nothing. It must not read as FILLED (audit C2) and must
    not read as REJECTED either — the order may be working right now."""
    r = broker_with(FakeIB([])).get_order_status("999")
    assert r.status == OrderStatus.SUBMITTED
    assert "cannot confirm" in r.error_message


def test_disconnection_is_not_a_rejection():
    """TWS dropping does not cancel anything at IBKR. Reporting REJECTED
    retires a live order from the caller's books and abandons the position."""
    from src.execution.ibkr_broker import IBKRBroker
    b = IBKRBroker.__new__(IBKRBroker)
    b._ib = None
    b.is_connected = lambda: False
    r = b.get_order_status("303")
    assert r.status != OrderStatus.REJECTED
    assert r.status == OrderStatus.SUBMITTED
    assert "not connected" in r.error_message


def test_unrecognised_ibkr_status_is_treated_as_still_working():
    """A status the adapter has never seen must fail towards "keep polling",
    never towards filled or dead."""
    ib = FakeIB([FakeTrade(204, "SomeNewIBKRStatus", filled=0, remaining=100)])
    r = broker_with(ib).get_order_status("204")
    assert r.status == OrderStatus.SUBMITTED


def test_pending_states_are_live_not_terminal():
    for label in ("ApiPending", "PendingSubmit", "PreSubmitted", "PendingCancel"):
        ib = FakeIB([FakeTrade(205, label, filled=0, remaining=100)])
        r = broker_with(ib).get_order_status("205")
        assert r.status == OrderStatus.SUBMITTED, label


# ── 3. CANCEL ────────────────────────────────────────────────────────────────

def test_cancel_reports_cancelled():
    ib = FakeIB([FakeTrade(301, "Cancelled", filled=0, remaining=100)])
    r = broker_with(ib).get_order_status("301")
    assert r.status == OrderStatus.CANCELLED
    assert r.filled_qty == 0


def test_api_cancelled_is_cancelled():
    ib = FakeIB([FakeTrade(302, "ApiCancelled", filled=0, remaining=100)])
    assert broker_with(ib).get_order_status("302").status == OrderStatus.CANCELLED


def test_cancelling_a_live_order_sends_the_cancel():
    ib = FakeIB([FakeTrade(304, "Submitted", filled=0, remaining=100)])
    assert broker_with(ib).cancel_order("304") is True
    assert ib.cancelled == [304]


def test_cancelling_an_already_filled_order_returns_false():
    """The caller cancels protective orders before replacing them. Being told
    "cancelled" about an order that already executed hides a real position."""
    ib = FakeIB([FakeTrade(305, "Filled", filled=100, remaining=0,
                           fills=[FakeFill(100, 10.0)])])
    assert broker_with(ib).cancel_order("305") is False
    assert ib.cancelled == []


def test_cancelling_an_already_cancelled_order_returns_true():
    """Idempotent: the caller's goal — this will not fill — already holds."""
    ib = FakeIB([FakeTrade(306, "Cancelled", filled=0, remaining=100)])
    assert broker_with(ib).cancel_order("306") is True


def test_cancelling_an_unknown_order_returns_false():
    assert broker_with(FakeIB([])).cancel_order("999") is False


# ── submit_order: placement vs. reading the placement ────────────────────────

def test_placement_that_cannot_be_read_is_not_reported_rejected(no_ib_insync_needed):
    """The order reached IBKR and then the status read blew up. REJECTED here
    means nobody polls it, nobody brackets it, and the fill arrives unowned."""
    from src.execution.broker_base import (AssetClass, OrderRequest, OrderSide,
                                           OrderType)

    class HalfBrokenIB(FakeIB):
        def qualifyContracts(self, c):
            return [c]

        def placeOrder(self, contract, order):
            return FakeTrade(401, "PreSubmitted")

        def sleep(self, s):
            raise RuntimeError("socket died while waiting for the ack")

    b = broker_with(HalfBrokenIB())
    r = b.submit_order(OrderRequest(ticker="AAPL", side=OrderSide.BUY, qty=10,
                                    order_type=OrderType.MARKET,
                                    asset_class=AssetClass.EQUITY))
    assert r.status != OrderStatus.REJECTED
    assert r.status == OrderStatus.SUBMITTED
    assert r.broker_order_id == "401"           # the id survives, so it is pollable
    assert "unreadable" in r.error_message


def test_failure_to_place_is_still_rejected(no_ib_insync_needed):
    """The honest REJECTED must survive: nothing reached IBKR here."""
    from src.execution.broker_base import (AssetClass, OrderRequest, OrderSide,
                                           OrderType)

    class DeadIB(FakeIB):
        def qualifyContracts(self, c):
            raise RuntimeError("no connection to TWS")

    b = broker_with(DeadIB())
    r = b.submit_order(OrderRequest(ticker="AAPL", side=OrderSide.BUY, qty=10,
                                    order_type=OrderType.MARKET,
                                    asset_class=AssetClass.EQUITY))
    assert r.status == OrderStatus.REJECTED
    assert r.broker_order_id == ""


def test_submit_and_status_agree_on_the_same_trade(no_ib_insync_needed):
    """Both paths reconcile through one function, so they cannot drift into
    describing the same order differently."""
    from src.execution.broker_base import (AssetClass, OrderRequest, OrderSide,
                                           OrderType)
    trade = FakeTrade(402, "Submitted", filled=25, remaining=75,
                      fills=[FakeFill(25, 8.0)])

    class LiveIB(FakeIB):
        def qualifyContracts(self, c):
            return [c]

        def placeOrder(self, contract, order):
            return trade

        def sleep(self, s):
            return None

    ib = LiveIB([trade])
    b = broker_with(ib)
    submitted = b.submit_order(OrderRequest(ticker="AAPL", side=OrderSide.BUY,
                                            qty=100, order_type=OrderType.MARKET,
                                            asset_class=AssetClass.EQUITY))
    polled = b.get_order_status("402")
    assert submitted.status == polled.status == OrderStatus.PARTIAL
    assert submitted.filled_qty == polled.filled_qty == 25


# ── what the order manager does with those statuses ──────────────────────────

class _RecordingBroker:
    """Records every order it is asked to place, and answers with one status."""
    name = "fake"
    paper = True

    def __init__(self, status, filled=0.0, price=10.0):
        self._status = status
        self._filled = filled
        self._price = price
        self.placed = []

    def is_connected(self):
        return True

    def submit_order(self, req):
        from src.execution.broker_base import OrderResult
        self.placed.append(req)
        return OrderResult(broker_order_id=f"o{len(self.placed)}",
                           status=self._status, filled_qty=self._filled,
                           avg_fill_price=self._price)

    def get_order_status(self, oid):
        from src.execution.broker_base import OrderResult
        return OrderResult(broker_order_id=oid, status=self._status,
                           filled_qty=self._filled, avg_fill_price=self._price)

    def cancel_order(self, oid):
        return True

    def get_open_orders(self):
        return []

    def get_positions(self):
        return []


def _queue_a_trade(tmp_path, monkeypatch, **overrides):
    """A pending trade in an isolated queue file — never the real one."""
    from src.execution import approval_queue as aq
    from src.execution.broker_base import (AssetClass, OrderRequest, OrderSide,
                                           OrderType)
    monkeypatch.setattr(aq, "QUEUE_FILE", tmp_path / "queue.json")
    req = OrderRequest(ticker="AAPL", side=OrderSide.BUY, qty=100,
                       order_type=OrderType.MARKET,
                       asset_class=AssetClass.EQUITY,
                       stop_loss=overrides.get("stop_loss", 9.0),
                       take_profit=overrides.get("take_profit", 12.0))
    return aq.ApprovalQueue().push(req, current_price=10.0, broker="alpaca")


def test_order_manager_does_not_bracket_a_partial_fill(tmp_path, monkeypatch):
    """A PARTIAL entry must not get brackets sized for the full order — a stop
    for 100 shares against a 40-share position is a naked short on the rest."""
    from src.execution.order_manager import OrderManager
    from src.execution.broker_base import OrderType

    broker = _RecordingBroker(OrderStatus.PARTIAL, filled=40)
    queued = _queue_a_trade(tmp_path, monkeypatch)
    mgr = OrderManager(alpaca=broker)
    # The order stays partial for the whole poll window; skip the 30s of real
    # waiting, keep the outcome the code would actually see.
    monkeypatch.setattr(OrderManager, "_await_fill",
                        lambda self, b, r, **kw: b.get_order_status(r.broker_order_id))
    result = mgr.execute(queued.id)

    assert result["ok"] is True
    assert result["status"] == OrderStatus.PARTIAL.value
    bracket_types = [r.order_type for r in broker.placed[1:]]
    assert bracket_types == [], f"brackets placed on a partial fill: {bracket_types}"
    assert result["brackets_pending"] is True
    assert not any(r.order_type in (OrderType.STOP, OrderType.LIMIT)
                   for r in broker.placed)


def test_order_manager_brackets_a_real_fill(tmp_path, monkeypatch):
    """The counterpart: a genuine fill must still get its protection."""
    from src.execution.order_manager import OrderManager
    from src.execution.broker_base import OrderType

    broker = _RecordingBroker(OrderStatus.FILLED, filled=100)
    queued = _queue_a_trade(tmp_path, monkeypatch)
    mgr = OrderManager(alpaca=broker)
    result = mgr.execute(queued.id)

    assert result["ok"] is True
    types = {r.order_type for r in broker.placed[1:]}
    assert OrderType.STOP in types and OrderType.LIMIT in types
    assert result["brackets_pending"] is False


def test_order_manager_marks_a_rejection_rejected(tmp_path, monkeypatch):
    """A rejected entry must not be recorded as executed."""
    from src.execution import approval_queue as aq
    from src.execution.order_manager import OrderManager

    broker = _RecordingBroker(OrderStatus.REJECTED)
    queued = _queue_a_trade(tmp_path, monkeypatch)
    mgr = OrderManager(alpaca=broker)
    result = mgr.execute(queued.id)

    assert result["ok"] is False
    # The queue is the audit trail. execute() marks a trade approved before it
    # calls the broker, so a rejection arriving afterwards has to be able to
    # move it out of "approved" — otherwise the record claims a trade that the
    # market refused.
    assert aq.ApprovalQueue().get_by_id(queued.id).status == "rejected"
    assert len(broker.placed) == 1              # no brackets for a dead order


def test_await_fill_stops_polling_on_a_terminal_status():
    """_await_fill must not burn its whole window on an order that is already
    dead, and must not report the stale pre-poll status."""
    import time
    from src.execution.order_manager import OrderManager
    from src.execution.broker_base import OrderResult

    broker = _RecordingBroker(OrderStatus.REJECTED)
    mgr = OrderManager(alpaca=broker)
    started = time.monotonic()
    out = mgr._await_fill(broker,
                          OrderResult(broker_order_id="x",
                                      status=OrderStatus.SUBMITTED),
                          timeout_s=10.0, poll_s=0.01)
    assert out.status == OrderStatus.REJECTED
    assert time.monotonic() - started < 5.0
