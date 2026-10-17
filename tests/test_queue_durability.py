"""The approval queue survives a crash mid-write, fails closed when damaged,
and is serialised across processes (Codex 09f2158, verified at integration).

Before: write_text (truncate, then write) and `except: return []`. A torn file
became an EMPTY queue and the next push saved that over it — erasing every
"submitting"/"unknown" row, the records that block a second order for an
instrument whose first may already be at the broker.
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from test_order_settlement import (FakeAlpaca, FakeClient, OPEN_NY,  # noqa: F401
                                   manager, queue)

ROOT = Path(__file__).resolve().parent.parent


def _unknown_row(queue):
    """A queue whose AAPL order timed out and was never found: UNKNOWN."""
    q = queue()
    client = FakeClient(lookups=[None, None, None])
    res = manager(FakeAlpaca(client)).execute(q.id, now=OPEN_NY)
    assert res.get("unknown") is True
    return q, client


def test_a_torn_queue_sends_nothing_and_is_never_overwritten(queue):
    from src.execution import approval_queue as aq
    q, client = _unknown_row(queue)
    raw = aq.QUEUE_FILE.read_text(encoding="utf-8")
    torn = raw[: len(raw) // 2]                     # what a crash mid-write_text left
    aq.QUEUE_FILE.write_text(torn, encoding="utf-8")

    with pytest.raises(aq.QueueStateError):
        queue()                                     # a new proposal is refused
    with pytest.raises(aq.QueueStateError):
        manager(FakeAlpaca(client)).execute(q.id, now=OPEN_NY)
    assert len(client.sent) == 1, "nothing further was sent"
    assert aq.QUEUE_FILE.read_text(encoding="utf-8") == torn, "left exactly as found"


def test_a_crash_during_save_keeps_the_previous_queue_and_its_block(queue, monkeypatch):
    from src.execution import approval_queue as aq
    q, client = _unknown_row(queue)
    before = aq.QUEUE_FILE.read_text(encoding="utf-8")

    def crash(src, dst):
        raise OSError("power cut")

    with monkeypatch.context() as m:
        m.setattr(os, "replace", crash)
        with pytest.raises(OSError):
            queue("MSFT")

    assert aq.QUEUE_FILE.read_text(encoding="utf-8") == before
    assert [t.status for t in aq.ApprovalQueue().unresolved()] == ["unknown"]
    assert not list(aq.QUEUE_FILE.parent.glob("*.tmp")), "no temp file left behind"
    # The block on a second AAPL order survived the crash.
    again = queue("AAPL")
    client._accept = object()                       # would "succeed" if it were sent
    res = manager(FakeAlpaca(client)).execute(again.id, now=OPEN_NY)
    assert res.get("blocked") is True and len(client.sent) == 1


_HOLD_QUEUE = textwrap.dedent("""
    import sys, time
    sys.path.insert(0, sys.argv[1])
    from pathlib import Path
    from src.execution import approval_queue as aq
    aq.QUEUE_FILE = Path(sys.argv[2])
    with aq._queue_guard():
        print("held", flush=True)
        time.sleep(float(sys.argv[3]))
        print(repr(time.time()), flush=True)        # released just after this
""")


def test_another_process_holding_the_queue_serialises_the_write(queue, tmp_path):
    """Two backends (an orphaned uvicorn beside a fresh one) used to hold only
    their own thread lock; the claim's exclusivity check could pass in both."""
    from src.execution import approval_queue as aq
    q = queue()
    script = tmp_path / "hold.py"
    script.write_text(_HOLD_QUEUE, encoding="utf-8")
    child = subprocess.Popen([sys.executable, str(script), str(ROOT), str(aq.QUEUE_FILE), "2"],
                             stdout=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == "held"
        aq.ApprovalQueue().approve(q.id)            # must wait for the other process
        done = time.time()
        released = float(child.stdout.readline())
    finally:
        child.wait(30)
    assert done >= released - 0.05, "the write ran while another process held the queue"
    assert aq.ApprovalQueue().get_by_id(q.id).status == "approved"


TOKEN = "test-owner-token-for-queue-durability"


def test_queue_lock_timeout_preserves_state_and_recovers(queue, tmp_path, monkeypatch):
    from src.execution import approval_queue as aq
    from src.core.durable import StateLockTimeout
    q = queue()
    before = aq.QUEUE_FILE.read_bytes()
    script = tmp_path / "hold-timeout.py"
    script.write_text(_HOLD_QUEUE, encoding="utf-8")
    child = subprocess.Popen([sys.executable, str(script), str(ROOT), str(aq.QUEUE_FILE), "1"],
                             stdout=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == "held"
        monkeypatch.setattr(aq._QUEUE_LOCK, "timeout_s", 0.1)
        with pytest.raises(StateLockTimeout):
            aq.ApprovalQueue().approve(q.id)
        assert aq.QUEUE_FILE.read_bytes() == before
    finally:
        child.wait(10)
    assert aq.ApprovalQueue().approve(q.id).status == "approved"


def test_the_api_reports_a_damaged_queue_as_503_not_500(queue, monkeypatch, tmp_path):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from backend.main import app
    from src.auth import hardening
    from src.auth import session as sess
    from src.execution import approval_queue as aq
    monkeypatch.setenv("ARIA_OWNER_TOKEN", TOKEN)
    monkeypatch.delenv("ARIA_OWNER_TOKEN_HASH", raising=False)
    monkeypatch.delenv("ARIA_TRUST_PROXY", raising=False)
    monkeypatch.setattr(sess.SESSIONS, "store", tmp_path / "sessions.json")
    monkeypatch.setattr(sess.SESSIONS, "session_key_file", tmp_path / ".key")
    sess.SESSIONS._attempts.clear()
    hardening._limiter._hits.clear()
    owner = TestClient(app, client=("203.0.113.44", 54100))
    assert owner.post("/api/auth/login", json={"token": TOKEN}).status_code == 200

    queue()
    aq.QUEUE_FILE.write_text('[{"id": "x", "sta', encoding="utf-8")
    r = owner.get("/api/execute/unresolved")
    assert r.status_code == 503
    body = r.json()
    assert body["state_unreadable"] == "approval_queue.json"
    assert "Nothing was sent" in body["detail"]
    assert str(tmp_path) not in body["detail"], "no local paths in the answer"
