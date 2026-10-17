"""Real ASGI listener, ephemeral loopback port, isolated state and fake broker."""
import json
import socket
import threading
import time
import urllib.request
from contextlib import contextmanager

import pytest


@contextmanager
def listening(app):
    uvicorn = pytest.importorskip("uvicorn")
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    # Do not alter global uvicorn loggers: later security tests must still see
    # access records and prove credential redaction before any handler runs.
    server = uvicorn.Server(uvicorn.Config(app, log_config=None, log_level=None, lifespan="on"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started, "ASGI server failed to start"
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(10)
        sock.close()
        assert not thread.is_alive(), "ASGI server failed to stop"


def test_health_approval_timeout_reconciliation_and_restart(monkeypatch, tmp_path):
    from backend import main as backend
    from src.auth import policy
    from src.core import readiness
    from src.execution import approval_queue as aq
    from src.execution import order_manager as om
    from src.execution.broker_base import OrderRequest, OrderResult, OrderSide, OrderStatus
    from test_final_execution_reliability import Broker, manager

    monkeypatch.setenv("ARIA_TESTING", "1")
    monkeypatch.setenv("ARIA_OWNER_TOKEN", "fixture-runtime-owner")
    monkeypatch.setenv("ALPACA_PAPER", "true")
    monkeypatch.setenv("IBKR_PAPER", "true")
    monkeypatch.setattr(policy, "owner_token_hash", lambda: "")
    monkeypatch.setattr(readiness, "DATA", tmp_path)
    readiness._last["ready"] = None
    monkeypatch.setattr(aq, "QUEUE_FILE", tmp_path / "queue.json")
    monkeypatch.setattr(om, "RECONCILE_LOG", tmp_path / "reconciliation.jsonl")
    broker = Broker()
    mgr = manager(broker)
    monkeypatch.setattr(backend, "_get_order_manager", lambda: mgr)
    trade = aq.ApprovalQueue().push(OrderRequest("AAPL", OrderSide.BUY, 1))
    # Explicit fixture approval: no real adapter, credential or order endpoint.
    assert mgr.execute(trade.id)["unknown"] is True
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def get(base, path):
        req = urllib.request.Request(base + path, headers={"Authorization": "Bearer fixture-runtime-owner"})
        with opener.open(req, timeout=5) as response:
            assert response.status == 200
            return json.load(response)

    with listening(backend.app) as base:
        assert get(base, "/health")["status"] == "ok"
        assert get(base, "/ready")["ready"] is True
        assert get(base, "/api/execute/unresolved")["orders"][0]["status"] == "unknown"
    # New manager and a restarted listener read the persisted claim; never POST again.
    mgr = manager(broker)
    assert mgr.execute(trade.id)["ok"] is False
    broker.get_order_by_client_id = lambda cid: OrderResult(
        "fixture-venue-order", OrderStatus.FILLED, filled_qty=1, avg_fill_price=101)
    assert mgr.reconcile_unresolved()["settled"][trade.id] == "filled"
    with listening(backend.app) as base:
        body = get(base, "/api/execute/queue")
        rows = body["trades"]
        saved = next(t for t in rows if t["id"] == trade.id)
        assert saved["order_status"] == "filled"
        assert saved["filled_qty"] == 1 and saved["fill_price"] == 101
        assert get(base, "/api/execute/unresolved")["count"] == 0
    assert len(broker.sent) == 1
