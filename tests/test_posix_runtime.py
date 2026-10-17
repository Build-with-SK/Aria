"""Real child processes, SIGTERM/restart and POSIX permission failures."""
import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX process/permissions protocol")

CHILD = r'''
import os, sys, socket
from pathlib import Path
import dotenv
dotenv.load_dotenv = lambda *a, **kw: False
real_connect = socket.socket.connect
def local_only(self, address, *args):
    if isinstance(address, tuple) and address[0] in ("127.0.0.1", "::1"):
        return real_connect(self, address, *args)
    raise OSError("runtime verification refuses external connections")
socket.socket.connect = local_only
from backend import main as backend
from src.auth import policy
from src.core import readiness
from src.execution import approval_queue
root = Path(sys.argv[2])
policy.owner_token_hash = lambda: ""
readiness.DATA = root
readiness._last["ready"] = None
approval_queue.QUEUE_FILE = root / "queue.json"
import uvicorn
uvicorn.run(backend.app, host="127.0.0.1", port=int(sys.argv[1]), log_config=None)
'''


def test_real_process_shutdown_and_restart_retains_uncertainty(tmp_path):
    pytest.importorskip("uvicorn")
    from dataclasses import asdict
    from src.execution import approval_queue as aq
    from src.execution.broker_base import OrderRequest, OrderSide
    # Parent fixture redirects application state; only copy synthetic evidence.
    row = asdict(aq.ApprovalQueue().push(OrderRequest("AAPL", OrderSide.BUY, 1)))
    row.update(status="unknown", client_order_id="fixture-no-send", order_status="unknown")
    (tmp_path / "queue.json").write_text(json.dumps([row]))
    env = {k: v for k, v in os.environ.items() if not k.startswith(("ALPACA_", "IBKR_", "ARIA_OWNER_"))}
    env.update(ARIA_TESTING="1", ARIA_OWNER_TOKEN="posix-test-owner",
               ARIA_LOG_FILE=str(tmp_path / "server.log"),
               PYTHONPATH=str(Path(__file__).resolve().parents[1]))
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for iteration in range(2):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        with (tmp_path / "process.log").open("a") as log:
            proc = subprocess.Popen([sys.executable, "-c", CHILD, str(port), str(tmp_path)],
                                    cwd=tmp_path, env=env, stdout=log, stderr=log)
            try:
                deadline = time.monotonic() + 20
                while True:
                    try:
                        with opener.open(f"http://127.0.0.1:{port}/health", timeout=1) as response:
                            assert response.status == 200
                        break
                    except OSError:
                        assert proc.poll() is None, (tmp_path / "process.log").read_text()
                        assert time.monotonic() < deadline, "backend startup exceeded 20s"
                        time.sleep(0.05)
                with opener.open(f"http://127.0.0.1:{port}/ready", timeout=2) as response:
                    assert json.load(response)["ready"] is True
                req = urllib.request.Request(f"http://127.0.0.1:{port}/api/execute/queue",
                        headers={"Authorization": "Bearer posix-test-owner"})
                with opener.open(req, timeout=2) as response:
                    saved = json.load(response)["trades"][0]
                    assert saved["status"] == "unknown" and saved["client_order_id"] == "fixture-no-send"
                proc.send_signal(signal.SIGTERM)
                # Uvicorn restores and re-raises SIGTERM AFTER graceful shutdown.
                # Verify both its signal exit contract and completed lifespan.
                assert proc.wait(timeout=10) == -signal.SIGTERM
                assert (tmp_path / "process.log").read_text().count("Application shutdown complete.") == iteration + 1
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait(timeout=5)
    assert json.loads((tmp_path / "queue.json").read_text())[0]["status"] == "unknown"


def test_database_configuration_unicode_paths_and_permission_failure(tmp_path, monkeypatch):
    from src.database import database
    from src.desk import config
    from src.execution import approval_queue as aq
    from src.execution.broker_base import OrderRequest, OrderSide
    root = tmp_path / "state with spaces — λ"
    root.mkdir()
    monkeypatch.setattr(database, "DB_PATH", root / "signals.sqlite")
    database.initialise_database()
    database.initialise_database()
    with database.get_connection() as conn:
        assert conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    monkeypatch.setattr(config, "CONFIG_FILE", root / "desk.json")
    config.save_config({"auto_execute": False, "focus_tickers": 3})
    assert config.load_config()["focus_tickers"] == 3
    assert config.load_config()["auto_execute"] is False
    monkeypatch.setattr(aq, "QUEUE_FILE", root / "queue.json")
    queue = aq.ApprovalQueue()
    first = queue.push(OrderRequest("AAPL", OrderSide.BUY, 1))
    before = aq.QUEUE_FILE.read_bytes()
    root.chmod(0o500)
    try:
        with pytest.raises(PermissionError):
            queue.push(OrderRequest("MSFT", OrderSide.BUY, 1))
        assert aq.QUEUE_FILE.read_bytes() == before
    finally:
        root.chmod(0o700)
    assert queue.get_by_id(first.id).status == "pending"
