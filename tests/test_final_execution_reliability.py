"""Independent failure probes for the final reliability mission. No network."""
from types import SimpleNamespace

import pytest

from src.execution import approval_queue as aq
from src.execution.broker_base import OrderRequest, OrderResult, OrderSide, OrderStatus
from src.execution.order_manager import OrderManager


@pytest.fixture
def queue(tmp_path, monkeypatch):
    monkeypatch.setattr(aq, "QUEUE_FILE", tmp_path / "queue.json")
    return aq.ApprovalQueue()


def proposal(queue, symbol="AAPL"):
    return queue.push(OrderRequest(symbol, OrderSide.BUY, 1), broker="alpaca")


def manager(broker):
    mgr = OrderManager(alpaca=broker)
    mgr.session_refusal = lambda *a, **k: None
    mgr.portfolio_risk = lambda *a, **k: SimpleNamespace(
        ok=True, approved_qty=1, to_dict=lambda: {"ok": True})
    mgr.SETTLE_WAIT_S = 0
    return mgr


class Broker:
    name = "fixture"

    def __init__(self):
        self.sent = []

    def is_connected(self):
        return True

    def submit_order(self, req):
        self.sent.append(req)
        raise TimeoutError("accepted at venue; reply lost")

    def get_order_by_client_id(self, cid):
        return None  # delayed visibility is NOT proof of rejection


def test_lost_reply_stays_unknown_and_never_resubmits(queue):
    broker = Broker()
    trade = proposal(queue)
    mgr = manager(broker)
    result = mgr.execute(trade.id)
    assert result.get("unknown") is True
    assert queue.get_by_id(trade.id).status == "unknown"
    assert broker.sent[0].client_order_id
    assert mgr.execute(trade.id)["ok"] is False
    assert len(broker.sent) == 1


@pytest.mark.parametrize("contents", ['[{"status":', '{}', '[7]', '[{"id":"missing-fields"}]'])
def test_damaged_queue_is_never_silently_overwritten(queue, contents):
    aq.QUEUE_FILE.write_text(contents, encoding="utf-8")
    with pytest.raises((ValueError, RuntimeError)):
        proposal(queue)
    assert aq.QUEUE_FILE.read_text(encoding="utf-8") == contents


def test_failed_atomic_replace_preserves_last_committed_queue(queue, monkeypatch):
    import os
    trade = proposal(queue)
    previous = aq.QUEUE_FILE.read_bytes()

    def fail(*a, **k):
        raise OSError("injected disk failure before replace")

    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(OSError):
        queue.approve(trade.id)
    assert aq.QUEUE_FILE.read_bytes() == previous


def test_oco_submission_obeys_kill_switch(monkeypatch):
    from src.execution import alpaca_broker as ab
    client = SimpleNamespace(submit_order=lambda **k: pytest.fail("kill switch bypassed"))
    broker = ab.AlpacaBroker.__new__(ab.AlpacaBroker)
    broker._client = client
    monkeypatch.setattr(ab.kill_switch, "is_engaged", lambda: True)
    # A known refusal, not None ("outcome unknown") — see test_protective_order_outcomes.
    with pytest.raises(ab.OrderRefused):
        broker.submit_oco_exit("AAPL", 1, "buy", 95, 110)


def test_ibkr_place_timeout_is_unknown_and_keeps_client_reference(monkeypatch):
    from src.execution import ibkr_broker as ib
    sent = []

    def place(contract, order):
        sent.append(order)
        raise TimeoutError("socket lost after write")

    fake_ib = SimpleNamespace(qualifyContracts=lambda c: None, placeOrder=place)
    broker = ib.IBKRBroker.__new__(ib.IBKRBroker)
    broker._ib = fake_ib
    broker.is_connected = lambda: True
    monkeypatch.setattr(ib, "_make_contract", lambda *a: object())
    monkeypatch.setattr(ib, "ibi", SimpleNamespace(MarketOrder=lambda *a: SimpleNamespace()), raising=False)
    monkeypatch.setattr(ib.kill_switch, "is_engaged", lambda: False)
    result = broker.submit_order(OrderRequest("AAPL", OrderSide.BUY, 1, client_order_id="aria-fixture"))
    assert result.status is OrderStatus.UNKNOWN
    assert sent[0].orderRef == "aria-fixture"


@pytest.mark.parametrize("method", ["get_positions", "get_open_orders"])
def test_alpaca_read_failure_is_not_an_empty_account(monkeypatch, method):
    from src.execution import alpaca_broker as ab

    def fail(*a, **k):
        raise TimeoutError("broker state unreadable")

    broker = ab.AlpacaBroker.__new__(ab.AlpacaBroker)
    broker._client = SimpleNamespace(get_all_positions=fail, get_orders=fail)
    with pytest.raises(ConnectionError):
        getattr(broker, method)()


def test_concurrent_process_writes_preserve_all_proposals(queue, tmp_path):
    import subprocess
    import sys
    from pathlib import Path

    worker = """
import sys
from pathlib import Path
from src.execution import approval_queue as aq
from src.execution.broker_base import OrderRequest, OrderSide
aq.QUEUE_FILE = Path(sys.argv[1])
for i in range(12):
    aq.ApprovalQueue().push(OrderRequest('AAPL', OrderSide.BUY, 1))
"""
    children = [subprocess.Popen([sys.executable, "-c", worker, str(aq.QUEUE_FILE)],
                                 cwd=Path(__file__).resolve().parents[1],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                for _ in range(3)]
    for child in children:
        out, err = child.communicate(timeout=45)
        assert child.returncode == 0, (out, err)
    rows = queue.get_all(limit=100)
    assert len(rows) == 36
    assert len({t.id for t in rows}) == 36


def test_pending_market_entry_cannot_be_ignored_in_risk():
    from datetime import datetime, timezone
    from src.risk.pretrade import assess, ProposedOrder, Verdict, PortfolioRiskLimits
    from src.trading.account import AccountState
    at = datetime(2026, 10, 6, 15, tzinfo=timezone.utc)
    account = AccountState(known=True, as_of=at, currency="USD", equity=10000,
                           cash=10000, available_cash=10000,
                           pending_orders=({"symbol": "AAPL", "side": "buy", "qty": 100,
                                            "notional": 0},))
    verdict = assess(ProposedOrder("AAPL", "buy", 1, 100, at), account, at=at,
                     limits=PortfolioRiskLimits())
    assert verdict.verdict is Verdict.DEFER
    assert verdict.checks["pending_entries"]["ok"] is False


def test_future_dated_quote_is_not_fresh():
    from datetime import datetime, timedelta, timezone
    from src.risk.pretrade import assess, ProposedOrder, Verdict, PortfolioRiskLimits
    from src.trading.account import AccountState
    at = datetime(2026, 10, 6, 15, tzinfo=timezone.utc)
    account = AccountState(known=True, as_of=at, currency="USD", equity=10000,
                           cash=10000, available_cash=10000)
    verdict = assess(ProposedOrder("AAPL", "buy", 1, 100, at + timedelta(days=1)), account, at=at,
                     limits=PortfolioRiskLimits())
    assert verdict.verdict is Verdict.DEFER
    assert verdict.checks["price_fresh"]["ok"] is False


def test_chat_with_unreachable_brain_returns_controlled_503(monkeypatch):
    from backend import main as backend
    from fastapi import HTTPException
    from src.inference import router
    from src.inference.policy import BrainUnreachable

    def unreachable(*a, **k):
        raise BrainUnreachable("DEEP", ["fixture model offline"], tried=[])

    monkeypatch.setattr(backend, "_prepare_aria_chat", lambda *a: ("", [], None))
    monkeypatch.setattr(router, "get_router", lambda: SimpleNamespace(complete=unreachable))
    with pytest.raises(HTTPException) as error:
        backend.aria_chat(None, None)
    assert error.value.status_code == 503
    assert error.value.detail


def test_unknown_oco_never_falls_back_or_retries_after_restart(queue):
    from test_reduce_only import Book
    broker = Book({"AAPL": 10})
    broker.name = "Alpaca"
    attempts = []

    def lost(*a, **k):
        attempts.append(k["client_order_id"])
        return None  # broker may have accepted the OCO

    broker.submit_oco_exit = lost
    first = OrderManager(alpaca=broker).place_protective_orders("AAPL", 10, "buy", 95, 110)
    assert first.get("unknown") is True
    assert first["stop_loss_order"] is None and broker.sent == []
    second = OrderManager(alpaca=broker).place_protective_orders("AAPL", 10, "buy", 95, 110)
    assert second["stop_loss_order"] is None
    assert len(attempts) == 1 and queue.unresolved()[0].client_order_id == attempts[0]
    # A manual market exit cannot race the hidden OCO and reverse the position.
    from src.execution.reduce_only import at_broker
    assert not at_broker(broker, "AAPL", "sell", 10).ok


def test_crypto_alias_cannot_bypass_inflight_claim(queue):
    one, two = proposal(queue, "BTC-USD"), proposal(queue, "BTC/USD")
    assert queue.claim_for_submit(one.id, "one")[0] is not None
    assert queue.claim_for_submit(two.id, "two")[0] is None


def test_ibkr_reconnect_finds_completed_order_by_reference(monkeypatch):
    from src.execution.ibkr_broker import IBKRBroker
    from test_security_ibkr_status import FakeTrade
    completed = FakeTrade(19, "Filled", filled=2, remaining=0, avg=101, total=2)
    completed.order.orderRef = "persisted-reference"
    broker = IBKRBroker.__new__(IBKRBroker)
    broker._ib = SimpleNamespace(trades=lambda: [], reqAllOpenOrders=lambda: [],
                                 reqCompletedOrders=lambda **k: [completed])
    broker.is_connected = lambda: True
    found = broker.get_order_by_client_id("persisted-reference")
    assert found.status is OrderStatus.FILLED and found.filled_qty == 2
    assert found.broker_order_id == "19"


def test_unknown_exit_is_persistent_and_never_resubmitted(queue, monkeypatch):
    from test_reduce_only import Book, _close
    broker = Book({"AAPL": 10})
    attempts = []

    def lost(req):
        attempts.append(req.client_order_id)
        raise TimeoutError("exit accepted; reply lost")

    broker.submit_order = lost
    monkeypatch.setattr(OrderManager, "SETTLE_WAIT_S", 0)
    assert _close(monkeypatch, broker) is None
    assert queue.unresolved()[0].status == "unknown"
    assert _close(monkeypatch, broker) is None
    assert len(attempts) == 1 and attempts[0]


def test_remote_negative_lookup_keeps_order_engine_halted(clean_trading_state):
    from test_trading_orders import broker, spec
    from src.trading.orders import OrderEngine
    from src.trading.broker import BrokerTimeout
    venue = broker()
    venue.authoritative_absence = False  # model a remote API, not synchronous memory
    venue.fail_next_submit = BrokerTimeout("reply lost")
    assert OrderEngine().submit(spec(), venue).status == "UNKNOWN"
    report = OrderEngine().reconcile(venue)
    assert report["halted"] and report["still_unknown"] == ["aria-test-1"]
    assert OrderEngine().submit(spec(client_order_id="second"), venue).status == "REFUSED"
    assert venue.submit_calls == 1


def test_submitting_journal_claim_survives_crash_before_halt_file(clean_trading_state):
    from test_trading_orders import broker, spec
    from src.trading.orders import OrderEngine
    OrderEngine()._append("crashed", "SUBMITTING")
    venue = broker()
    assert OrderEngine().submit(spec(), venue).status == "REFUSED"
    assert venue.submit_calls == 0


def test_corrupt_order_journal_never_allows_a_new_order(clean_trading_state):
    from test_trading_orders import broker, spec
    from src.trading import orders
    orders.JOURNAL.parent.mkdir(parents=True, exist_ok=True)
    orders.JOURNAL.write_text('{"truncated":', encoding="utf-8")
    venue = broker()
    assert orders.OrderEngine().submit(spec(), venue).status == "REFUSED"
    assert venue.submit_calls == 0


def test_estimated_pnl_is_not_written_to_learning_memory(monkeypatch):
    from src.desk.position_manager import PositionManager
    from src.brain.cognitive import long_term_memory
    writes = []
    monkeypatch.setattr(long_term_memory, "LongTermMemory", lambda: SimpleNamespace(
        record_outcome=lambda *a: writes.append(a)))
    manager_ = PositionManager.__new__(PositionManager)
    manager_._record_outcome_memory({"debate_id": "fixture", "estimated": True})
    assert writes == []


def test_cancelled_partial_fill_preserves_position_but_releases_working_remainder(queue):
    trade = proposal(queue)
    from src.execution import alpaca_broker as ab
    client = SimpleNamespace(get_order_by_client_id=lambda cid: SimpleNamespace(
        id="partial-terminal", status="canceled", filled_qty="0.5", filled_avg_price="101", filled_at=None))
    adapter = ab.AlpacaBroker.__new__(ab.AlpacaBroker)
    adapter._client = client
    result = adapter.get_order_by_client_id("partial-key")
    assert result.status is OrderStatus.PARTIAL and result.terminal
    queue.mark_executed(trade.id, result.broker_order_id, result.avg_fill_price,
                        order_status=result.status.value, filled_qty=result.filled_qty,
                        order_terminal=result.terminal)
    saved = queue.get_by_id(trade.id)
    assert saved.filled_qty == 0.5 and saved.fill_price == 101
    assert saved.order_terminal and queue.in_flight() == []


def test_legacy_timeout_is_unknown_even_without_lookup_key():
    from src.execution.alpaca_broker import _classify_submit_failure
    request = OrderRequest("AAPL", OrderSide.BUY, 1)
    assert _classify_submit_failure(TimeoutError("lost reply"), request) is OrderStatus.UNKNOWN
