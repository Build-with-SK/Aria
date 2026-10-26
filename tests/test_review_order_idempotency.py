"""Independent review, 2026-10-26 (Claude, reviewer seat) — order idempotency.

Reproductions for DEFECT_REGISTER entries ARIA-D1..D3 (kept in the atlas
repository's PROJECT_CONTROL/). Each test states the behaviour a release
requires. They are strict xfails: they FAIL on the current code (so the
xfail holds) and will turn into an XPASS error the moment the defect is fixed,
which forces whoever fixes it to remove the marker and keep the test.

No network, no broker, no real data: the queue file is a tmp_path and the
broker is an in-memory fake.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone

import pytest

from src.execution.broker_base import (AccountInfo, AssetClass, OrderRequest,
                                       OrderResult, OrderSide, OrderStatus,
                                       OrderType)

NYSE_OPEN = datetime(2026, 10, 6, 15, 0, tzinfo=timezone.utc)


class SlowBroker:
    """Takes `delay` seconds to acknowledge, like a broker under load."""
    name = "fake"
    paper = True

    def __init__(self, delay=0.4, status=OrderStatus.SUBMITTED, raise_timeout=False):
        self.delay = delay
        self.status = status
        self.raise_timeout = raise_timeout
        self.placed: list[OrderRequest] = []
        self._lock = threading.Lock()

    def is_connected(self):
        return True

    def get_account(self):
        return AccountInfo(broker="fake", cash=1e6, portfolio_value=1e6,
                           buying_power=1e6, currency="USD")

    def get_quote(self, ticker):
        return {"bid": 9.99, "ask": 10.01, "mid": 10.0, "timestamp": NYSE_OPEN.isoformat()}

    def get_positions(self):
        return []

    def get_open_orders(self):
        return []

    def submit_order(self, req):
        with self._lock:
            self.placed.append(req)
        time.sleep(self.delay)
        if self.raise_timeout:
            # What AlpacaBroker.submit_order returns after a read timeout when
            # no client_order_id was supplied (_classify_submit_failure).
            from src.execution.alpaca_broker import _classify_submit_failure
            return OrderResult(broker_order_id="",
                               status=_classify_submit_failure(TimeoutError("read timed out"), req),
                               error_message="read timed out")
        return OrderResult(broker_order_id=f"b-{len(self.placed)}", status=self.status)

    def get_order_status(self, oid):
        return OrderResult(broker_order_id=oid, status=self.status)


def _queue(tmp_path, monkeypatch):
    from src.execution import approval_queue as aq
    monkeypatch.setattr(aq, "QUEUE_FILE", tmp_path / "queue.json")
    req = OrderRequest(ticker="AAPL", side=OrderSide.BUY, qty=10,
                       order_type=OrderType.MARKET, asset_class=AssetClass.EQUITY)
    return aq.ApprovalQueue().push(req, current_price=10.0, broker="alpaca")


def _manager(broker, monkeypatch):
    from src.execution.order_manager import OrderManager
    from src.risk import pretrade

    class _Ok:
        ok = True
        approved_qty = 1e9
        verdict = pretrade.Verdict.APPROVE if hasattr(pretrade.Verdict, "APPROVE") else None
        reasons: list = []
        binding = ""

        def to_dict(self):
            return {"ok": True}

    # Risk gate and session are not under test here; both pass.
    monkeypatch.setattr(OrderManager, "portfolio_risk", lambda self, t, b, now=None: _Ok())
    monkeypatch.setattr(OrderManager, "session_refusal", staticmethod(lambda *a, **k: None))
    return OrderManager(alpaca=broker)


@pytest.mark.xfail(strict=True, reason="ARIA-D1: execute() does not claim the trade atomically")
def test_two_concurrent_approvals_send_one_order(tmp_path, monkeypatch):
    """Owner double-clicks APPROVE (or approves a desk trade the auto-executor
    is already sending). FastAPI runs sync endpoints on a thread pool, so the
    two execute() calls overlap. Exactly one order may reach the broker."""
    broker = SlowBroker(delay=0.4)
    trade = _queue(tmp_path, monkeypatch)
    mgr = _manager(broker, monkeypatch)

    results = []
    t1 = threading.Thread(target=lambda: results.append(mgr.execute(trade.id, now=NYSE_OPEN)))
    t2 = threading.Thread(target=lambda: results.append(mgr.execute(trade.id, now=NYSE_OPEN)))
    t1.start(); time.sleep(0.1); t2.start()
    t1.join(); t2.join()

    assert len(broker.placed) == 1, f"{len(broker.placed)} orders sent for one approved trade"


@pytest.mark.xfail(strict=True, reason="ARIA-D1: a trade left 'approved' can be executed again")
def test_trade_already_approved_is_not_sent_again(tmp_path, monkeypatch):
    """execute() moves the trade to 'approved' BEFORE calling the broker. If the
    process dies (or the request is abandoned) between that write and the
    broker reply, the trade stays 'approved', and execute() accepts 'approved'
    as sendable — so a retry sends a second order with no way to know whether
    the first one exists. 'approved' must mean 'claimed by a sender'."""
    from src.execution.approval_queue import ApprovalQueue
    broker = SlowBroker(delay=0)
    trade = _queue(tmp_path, monkeypatch)
    ApprovalQueue().approve(trade.id)          # the state a crash mid-send leaves
    mgr = _manager(broker, monkeypatch)

    mgr.execute(trade.id, now=NYSE_OPEN)
    assert broker.placed == [], "a trade already claimed by a sender was sent again"


@pytest.mark.xfail(strict=True, reason="ARIA-D2 (= ARIA S1.3): approval-queue orders carry no client_order_id")
def test_queue_orders_carry_an_idempotency_key(tmp_path, monkeypatch):
    """Every order the approval queue sends needs a client_order_id derived
    from the trade, so the broker refuses a duplicate and a timeout can be
    settled by looking the order up."""
    broker = SlowBroker(delay=0)
    trade = _queue(tmp_path, monkeypatch)
    _manager(broker, monkeypatch).execute(trade.id, now=NYSE_OPEN)
    assert broker.placed and broker.placed[0].client_order_id, "order sent without client_order_id"


@pytest.mark.xfail(strict=True, reason="ARIA-D3: a submit timeout is recorded as a rejection")
def test_submit_timeout_is_unknown_not_rejected(tmp_path, monkeypatch):
    """A read timeout after sending means the broker MAY hold the order. The
    queue must not say 'rejected' (which lets the desk propose the same entry
    again and tells the owner 'nothing was executed')."""
    from src.execution.approval_queue import ApprovalQueue
    broker = SlowBroker(delay=0, raise_timeout=True)
    trade = _queue(tmp_path, monkeypatch)
    _manager(broker, monkeypatch).execute(trade.id, now=NYSE_OPEN)
    assert ApprovalQueue().get_by_id(trade.id).status != "rejected"
