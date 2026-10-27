"""Failure/recovery boundaries from the independent release review; fixtures only."""
import json
from datetime import datetime, timedelta, timezone

import pytest


@pytest.fixture(autouse=True)
def isolated_queue(tmp_path, monkeypatch):
    from src.execution import approval_queue
    monkeypatch.setattr(approval_queue, "QUEUE_FILE", tmp_path / "queue.json")


@pytest.mark.parametrize("row", [
    {}, {"generated_at": "bad"},
    {"generated_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()},
    {"generated_at": datetime.now(timezone.utc).isoformat(), "data_as_of": "2020-01-01"},
    {"generated_at": datetime.now(timezone.utc).isoformat(), "data_as_of": "bad"},
])
def test_signal_rewrite_does_not_refresh_missing_or_invalid_evidence(tmp_path, monkeypatch, row):
    from src.core import world
    from src.desk.opinion import signals_freshness
    monkeypatch.setattr(world, "DATA", tmp_path)
    (tmp_path / "signals.json").write_text(json.dumps({"A": row}), encoding="utf-8")
    assert signals_freshness()["fresh"] is False


def test_signal_oldest_row_and_current_evidence(tmp_path, monkeypatch):
    from src.core import world
    from src.desk.opinion import signals_freshness
    monkeypatch.setattr(world, "DATA", tmp_path)
    now = datetime.now(timezone.utc)
    path = tmp_path / "signals.json"
    current = {"generated_at": now.isoformat(), "data_as_of": now.isoformat()}
    path.write_text(json.dumps({"A": current}), encoding="utf-8")
    assert signals_freshness()["fresh"] is True
    path.write_text(json.dumps({"A": current, "B": {"generated_at": (now - timedelta(days=2)).isoformat()}}), encoding="utf-8")
    assert signals_freshness()["fresh"] is False


@pytest.mark.parametrize("kind", ["future", "invalid"])
def test_chat_context_and_world_reject_bad_ml_dates(tmp_path, monkeypatch, kind):
    import backend.main as backend
    from src.core import world
    monkeypatch.setattr(backend, "ROOT", tmp_path)
    monkeypatch.setattr(world, "DATA", tmp_path / "data")
    world.DATA.mkdir()
    row = {"generated_at": (datetime.now(timezone.utc) + timedelta(minutes=3)).isoformat()
           if kind == "future" else "invalid"}
    (world.DATA / "ml_predictions.json").write_text(json.dumps({"A": row}), encoding="utf-8")
    label, stale = backend._source_age("ml_predictions.json", "ml")
    assert stale and "STALE" in label
    assert world.dataset_freshness("ml_predictions.json")["status"] != "FRESH"


@pytest.mark.usefixtures("clean_trading_state")
def test_halt_refused_cancel_is_a_failure(monkeypatch):
    from src.trading import killswitch
    from src.trading.broker import OrderAck
    from src.execution import kill_switch

    class Book:
        def get_positions(self):
            return []

        def get_open_orders(self):
            return [OrderAck("entry-1", "NEW", "broker-1")]

        def open_order_details(self):
            return [{"client_order_id": "entry-1", "symbol": "AAPL", "side": "buy", "qty": 1}]

        def cancel_order(self, cid):
            return False

    try:
        cancel = killswitch.halt("fixture", broker=Book())["steps"]["cancel"]
        assert "entry-1" in cancel["failed"]
        assert cancel["kept_protective"] == [] and cancel["cancelled"] == []
    finally:
        kill_switch.release(actor="test")


def test_legacy_cancel_reobserves_broker_and_client_identity():
    from src.trading.broker import LegacyBrokerAdapter, OrderAck

    class Book:
        name = "fixture"
        sent = []

        def get_open_orders(self):
            return [{"id": "broker-1", "client_order_id": "client-1"}]

        def cancel_order(self, oid):
            self.sent.append(oid)
            return True

    book = Book()
    adapter = LegacyBrokerAdapter(book, is_live=False)
    assert not adapter.cancel_open_order(OrderAck("wrong-client", "NEW", "broker-1"))
    assert not adapter.cancel_open_order(OrderAck("client-1", "NEW", "wrong-broker"))
    assert book.sent == []
    assert adapter.cancel_open_order(OrderAck("client-1", "NEW", "broker-1"))
    assert book.sent == ["broker-1"]


def test_healing_enlarges_an_existing_partial_stop(monkeypatch):
    from test_claude_final_review_probes import _HaltedBook, _freeze, TUE_OPEN
    from src.desk.position_manager import PositionManager
    from src.execution.order_manager import OrderManager
    _freeze(monkeypatch, TUE_OPEN)
    monkeypatch.setattr("src.execution.live_guard.paper_confirmed", lambda b: True)
    book = _HaltedBook({"AAPL": 10})
    manager = OrderManager(alpaca=book)
    manager.place_protective_orders("AAPL", 4, "buy", 9, 12)
    pos = {"qty": 4, "side": "long", "stop": 9, "target": 12, "asset_class": "equity"}
    pm = PositionManager.__new__(PositionManager)
    pm._heal_brackets("AAPL", pos, manager, book.get_open_orders())
    assert pos["qty"] == 10
    assert sum(o["qty"] for o in book.resting.values() if o["order_type"] == "stop") == 10


def test_healing_keeps_existing_stop_when_cancel_is_refused(monkeypatch):
    from test_claude_final_review_probes import _HaltedBook
    from src.desk.position_manager import PositionManager
    from src.execution.order_manager import OrderManager
    monkeypatch.setattr("src.execution.live_guard.paper_confirmed", lambda b: True)
    book = _HaltedBook({"AAPL": 10})
    book.resting["stop"] = {"id": "stop", "ticker": "AAPL", "side": "sell", "qty": 4, "order_type": "stop"}
    monkeypatch.setattr(book, "cancel_order", lambda oid: False)
    pos = {"qty": 4, "side": "long", "stop": 9, "asset_class": "equity"}
    assert PositionManager.__new__(PositionManager)._heal_brackets("AAPL", pos, OrderManager(alpaca=book), book.get_open_orders()) is None
    assert list(book.resting) == ["stop"] and book.resting["stop"]["qty"] == 4


def test_pipeline_writer_excludes_competing_entry_and_recovers_after_error(tmp_path, monkeypatch):
    from src.core import pipeline_guard as guard
    from src.core.durable import SingleInstance
    monkeypatch.setattr(guard, "LOCK_FILE", tmp_path / "pipeline.lock")
    lock = SingleInstance(lambda: guard.LOCK_FILE)
    assert lock.acquire()
    executed = []

    @guard.single_writer
    def run():
        executed.append(True)
        raise ValueError("fixture interruption")

    try:
        assert guard.busy()
        with pytest.raises(guard.PipelineAlreadyRunning):
            run()
        assert not executed
    finally:
        lock.release()
    with pytest.raises(ValueError, match="fixture interruption"):
        run()
    assert not guard.busy()


def test_api_refuses_pipeline_writer_lock_without_spawning(tmp_path, monkeypatch):
    from src.core import pipeline_guard as guard
    from src.core.durable import SingleInstance
    import backend.main as backend
    from fastapi import BackgroundTasks
    monkeypatch.setattr(guard, "LOCK_FILE", tmp_path / "pipeline.lock")
    monkeypatch.setattr(backend, "_run_in_progress", False)
    lock = SingleInstance(lambda: guard.LOCK_FILE)
    assert lock.acquire()
    try:
        tasks = BackgroundTasks()
        assert backend.trigger_run(tasks)["status"] == "already_running"
        assert tasks.tasks == []
    finally:
        lock.release()


@pytest.mark.parametrize("url", ["http://remote.example", "https://user:secret@example.com", "file:///tmp/local", "https://example.com?token=private"])
def test_consult_token_requires_safe_configured_transport(monkeypatch, url):
    from src.consult import sentinel_client as client
    monkeypatch.setenv("SENTINEL_URL", url)
    monkeypatch.setattr(client, "_token", lambda: "synthetic-token")
    assert client._post("/api/consult", {}, timeout=1)["reason"] == "invalid_endpoint"


def test_consult_token_never_follows_a_redirect(monkeypatch):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread
    from src.consult import sentinel_client as client
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            self.send_response(302)
            self.send_header("Location", "/leak")
            self.end_headers()

        def do_GET(self):
            received.append(self.headers.get("X-Sentinel-Consult-Token"))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("SENTINEL_URL", f"http://127.0.0.1:{server.server_port}")
    monkeypatch.setattr(client, "_token", lambda: "synthetic-token")
    try:
        assert not client._post("/api/consult", {}, timeout=2)["ok"]
        assert received == []
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize("connected,raises", [(False, False), (True, False), (True, True)])
def test_ibkr_unreadable_or_absent_status_stays_unknown(connected, raises):
    from src.execution.ibkr_broker import IBKRBroker
    from src.execution.broker_base import OrderStatus
    from types import SimpleNamespace
    broker = IBKRBroker.__new__(IBKRBroker)
    broker.is_connected = lambda: connected
    def trades():
        if raises:
            raise OSError("fixture gateway unavailable")
        return []
    broker._ib = SimpleNamespace(trades=trades)
    assert broker.get_order_status("unknown-order").status == OrderStatus.UNKNOWN


def test_healing_keeps_residual_stop_while_other_exit_is_working(monkeypatch):
    from test_claude_final_review_probes import _HaltedBook, _freeze, TUE_OPEN
    from src.desk.position_manager import PositionManager
    from src.execution.order_manager import OrderManager
    from src.execution.broker_base import OrderRequest, OrderSide, OrderType
    _freeze(monkeypatch, TUE_OPEN)
    monkeypatch.setattr("src.execution.live_guard.paper_confirmed", lambda b: True)
    book = _HaltedBook({"AAPL": 10})
    manager = OrderManager(alpaca=book)
    manager.place_protective_orders("AAPL", 4, "buy", 9, 12)
    book.submit_order(OrderRequest(ticker="AAPL", qty=6, side=OrderSide.SELL, order_type=OrderType.MARKET))
    before = book.get_open_orders()
    pos = {"qty": 4, "side": "long", "stop": 9, "target": 12, "asset_class": "equity"}
    assert PositionManager.__new__(PositionManager)._heal_brackets("AAPL", pos, manager, before) is None
    assert book.get_open_orders() == before


def test_healing_retains_stop_and_reports_an_invisible_exit_claim(monkeypatch):
    from test_claude_final_review_probes import _HaltedBook
    from src.desk.position_manager import PositionManager
    from src.execution.order_manager import OrderManager
    from src.execution.approval_queue import ApprovalQueue
    from src.execution.broker_base import OrderRequest, OrderSide, OrderType
    monkeypatch.setattr("src.execution.live_guard.paper_confirmed", lambda b: True)
    queue = ApprovalQueue()
    row = queue.push(OrderRequest(ticker="AAPL", qty=6, side=OrderSide.SELL, order_type=OrderType.MARKET, reduce_only=True), broker="alpaca")
    assert queue.claim_for_submit(row.id, "uncertain-exit", exclusive=False)[0]
    queue.mark_unknown(row.id, "fixture lost response")
    book = _HaltedBook({"AAPL": 10})
    book.resting["stop"] = {"id": "stop", "ticker": "AAPL", "side": "sell", "qty": 4, "order_type": "stop"}
    before = book.get_open_orders()
    summary = {"errors": []}
    pos = {"qty": 4, "side": "long", "stop": 9, "asset_class": "equity"}
    assert PositionManager.__new__(PositionManager)._heal_brackets("AAPL", pos, OrderManager(alpaca=book), before, summary=summary) is None
    assert book.get_open_orders() == before
    assert summary["errors"] and "durable exit claim" in summary["errors"][0]


def test_healing_retries_slow_cancel_before_the_management_tick_ends(monkeypatch):
    from test_claude_final_review_probes import _HaltedBook, _freeze, TUE_OPEN
    from src.desk import position_manager as module
    from src.execution.order_manager import OrderManager
    from unittest.mock import Mock
    _freeze(monkeypatch, TUE_OPEN)
    monkeypatch.setattr("src.execution.live_guard.paper_confirmed", lambda b: True)
    book = _HaltedBook({"AAPL": 10})
    manager = OrderManager(alpaca=book)
    manager.place_protective_orders("AAPL", 4, "buy", 9, 12)
    wait = Mock(side_effect=[False, True])
    monkeypatch.setattr(module, "_await_cancelled", wait)
    pos = {"qty": 4, "side": "long", "stop": 9, "target": 12, "asset_class": "equity"}
    module.PositionManager.__new__(module.PositionManager)._heal_brackets("AAPL", pos, manager, book.get_open_orders())
    assert wait.call_count == 2
    assert sum(o["qty"] for o in book.resting.values() if o["order_type"] == "stop") == 10
