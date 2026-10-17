"""
tests/test_launch_get_sweep.py
==============================
Launch gate L3 (docs/collab/WORK_PLAN.md §5): every GET route answers without
a server error — under test isolation, as the owner, with outbound network
refused, so a provider outage shows up as a degraded answer, not a 500.

The sweep takes about a minute, so it runs only when asked:
    ARIA_LAUNCH_SWEEP=1 venv\\Scripts\\python.exe -m pytest tests/test_launch_get_sweep.py -q
The legacy-database regression below always runs.
"""
import json
import os
import socket
import threading
import time

import pytest

SAMPLE = {"symbol": "AAPL", "ticker": "AAPL", "name": "momentum", "strategy": "momentum",
          "scenario_type": "rate_shock", "content_id": "x", "debate_id": "x", "prediction_id": "x"}
# Semantic recall may cold-load its model. Listing must never do so.
SLOW_OK = {"/api/brain/recall"}


def _owner_client(monkeypatch):
    from fastapi.testclient import TestClient
    import backend.main as backend
    from src.auth import policy
    real_connect = socket.socket.connect

    def guarded(self, address, *a):
        host = address[0] if isinstance(address, tuple) else str(address)
        # asyncio's socketpair is loopback; Ollama (11434) is refused too so the
        # sweep never waits on a model.
        if host in ("127.0.0.1", "::1", "localhost") and not (isinstance(address, tuple) and address[1] == 11434):
            return real_connect(self, address, *a)
        raise OSError("network disabled for the launch sweep")
    monkeypatch.setattr(socket.socket, "connect", guarded)
    monkeypatch.setenv("ARIA_OWNER_TOKEN", "sweep-owner")
    monkeypatch.delenv("ARIA_OWNER_TOKEN_HASH", raising=False)
    monkeypatch.setattr(policy, "owner_token_hash", lambda: "")
    return backend, TestClient(backend.app, raise_server_exceptions=False), {"Authorization": "Bearer sweep-owner"}


@pytest.mark.parametrize("path", ["/api/history/AAPL", "/api/performance/AAPL"])
def test_legacy_signal_db_without_tables_is_empty_not_a_server_error(monkeypatch, path):
    _, client, headers = _owner_client(monkeypatch)
    from src.database import database
    import sqlite3

    def no_tables():
        return sqlite3.connect(":memory:")
    monkeypatch.setattr(database, "get_connection", no_tables)
    r = client.get(path, headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["records"] == [] and "legacy signal database" in body["message"]


@pytest.mark.skipif(os.environ.get("ARIA_LAUNCH_SWEEP") != "1", reason="set ARIA_LAUNCH_SWEEP=1 to run the full GET sweep")
def test_every_get_route_answers_without_a_server_error(monkeypatch):
    from fastapi.routing import APIRoute
    backend, client, headers = _owner_client(monkeypatch)
    results = []
    for route in [r for r in backend.app.routes if isinstance(r, APIRoute) and "GET" in r.methods]:
        path = route.path
        for key, value in SAMPLE.items():
            path = path.replace("{" + key + "}", value)
        box = {}

        def call():
            try:
                box["r"] = client.get(path, headers=headers)
            except Exception as exc:  # noqa: BLE001
                box["e"] = f"{type(exc).__name__}: {exc}"[:300]
        start = time.time()
        worker = threading.Thread(target=call, daemon=True)
        worker.start()
        worker.join(25)
        took = round(time.time() - start, 2)
        status = "TIMEOUT" if worker.is_alive() else "EXC" if "e" in box else box["r"].status_code
        detail = box.get("e") or (box["r"].text[:300] if isinstance(status, int) and status >= 500 else "")
        results.append({"path": route.path, "status": status, "s": took, "detail": detail})
    if os.environ.get("SWEEP_OUT"):
        with open(os.environ["SWEEP_OUT"], "w", encoding="utf-8") as fh:
            json.dump(results, fh, indent=1)
    bad = [r for r in results if r["status"] == "EXC" or (isinstance(r["status"], int) and r["status"] >= 500)
           or (r["status"] == "TIMEOUT" and r["path"] not in SLOW_OK)]
    assert not bad, json.dumps(bad, indent=1)
    assert len(results) >= 150
