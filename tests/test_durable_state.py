"""src/core/durable — atomic writes, fail-closed reads, cross-process locks.

The stores the order path relies on (approval queue, reflex playbooks, desk
positions, day state) used write_text + `except: return []`. A truncated file
became an empty store and the next save made that permanent. These tests pin
the replacement primitives; the stores' own tests are in
test_state_store_durability.py.
"""
from __future__ import annotations

import json
import subprocess
import sys
import textwrap
import threading
from pathlib import Path

import pytest

from src.core import durable as D

ROOT = Path(__file__).resolve().parent.parent


# ── atomic writes ───────────────────────────────────────────────────────────

def test_atomic_write_replaces_and_leaves_no_temp(tmp_path):
    p = tmp_path / "s.json"
    D.atomic_write_json(p, [{"id": 1}])
    D.atomic_write_json(p, [{"id": 2}])
    assert json.loads(p.read_text()) == [{"id": 2}]
    assert [x.name for x in tmp_path.iterdir()] == ["s.json"]


def test_failed_write_keeps_the_old_content(tmp_path):
    p = tmp_path / "s.json"
    D.atomic_write_json(p, {"ok": True})

    with pytest.raises(Exception):
        D.atomic_write_json(p, {"bad": float("nan")}, allow_nan=False)
    assert json.loads(p.read_text()) == {"ok": True}
    assert [x.name for x in tmp_path.iterdir()] == ["s.json"]


def test_fsync_failure_is_not_reported_as_a_durable_save(tmp_path, monkeypatch):
    p = tmp_path / "s.json"
    D.atomic_write_json(p, {"before": True})
    def failed(fd):
        raise OSError("storage could not commit")
    monkeypatch.setattr(D.os, "fsync", failed)
    with pytest.raises(OSError, match="storage could not commit"):
        D.atomic_write_json(p, {"after": True})
    assert json.loads(p.read_text()) == {"before": True}
    assert [x.name for x in tmp_path.iterdir()] == ["s.json"]


# ── fail-closed reads ───────────────────────────────────────────────────────

def test_missing_file_is_the_default(tmp_path):
    assert D.read_json_state(tmp_path / "none.json", list) == []


@pytest.mark.parametrize("content,why", [
    ('[{"id": "a", "status": "unkn', "not valid JSON"),       # truncated mid-write
    ("", "empty file"),
    ("   \n", "empty file"),
    ('{"id": "a"}', "expected list"),
    ('[1, 2]', "row 0 is int"),
    ('[{"id": "a"}]', "lacks status"),
])
def test_damaged_file_raises_quarantines_and_stays(tmp_path, content, why):
    p = tmp_path / "approval_queue.json"
    p.write_text(content, encoding="utf-8")
    with pytest.raises(D.StateUnreadable) as e:
        D.read_json_state(p, list, expect=list, validate=D.rows_with("id", "status"))
    assert why in str(e.value)
    # The damaged original is left exactly as it was — nothing may write an
    # empty store over it — and a copy is kept for the owner.
    assert p.read_text(encoding="utf-8") == content
    copies = list(tmp_path.glob("approval_queue.json.corrupt-*"))
    assert len(copies) == 1 and copies[0].read_text(encoding="utf-8") == content
    assert e.value.quarantined == copies[0]


def test_valid_file_reads(tmp_path):
    p = tmp_path / "q.json"
    p.write_text('[{"id": "a", "status": "unknown"}]', encoding="utf-8")
    assert D.read_json_state(p, list, expect=list,
                             validate=D.rows_with("id", "status"))[0]["status"] == "unknown"
    assert not list(tmp_path.glob("*.corrupt-*"))


def test_a_sharing_violation_is_retried_not_called_damage(tmp_path, monkeypatch):
    p = tmp_path / "q.json"
    p.write_text("[]", encoding="utf-8")
    calls = {"n": 0}
    real = Path.read_text

    def flaky(self, *a, **k):
        if self == p and calls["n"] < 3:
            calls["n"] += 1
            raise PermissionError(13, "sharing violation")
        return real(self, *a, **k)

    monkeypatch.setattr(Path, "read_text", flaky)
    assert D.read_json_state(p, list) == []
    assert calls["n"] == 3
    assert not list(tmp_path.glob("*.corrupt-*"))


def test_a_persistent_sharing_violation_fails_closed_without_quarantine(tmp_path, monkeypatch):
    p = tmp_path / "q.json"
    p.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(D, "READ_RETRY_S", 0.05)

    def locked(self, *a, **k):
        raise PermissionError(13, "sharing violation")

    monkeypatch.setattr(Path, "read_text", locked)
    with pytest.raises(D.StateUnreadable):
        D.read_json_state(p, list)
    assert not list(tmp_path.glob("*.corrupt-*"))


# ── locks ───────────────────────────────────────────────────────────────────

def test_process_shared_lock_is_reentrant_and_serialises_threads(tmp_path):
    lock = D.ProcessSharedLock(lambda: tmp_path / "x.lock", timeout_s=5)
    order = []
    with lock:
        with lock:                      # re-entrant in the same thread
            order.append("outer")

            def other():
                with lock:
                    order.append("other")

            t = threading.Thread(target=other)
            t.start()
            t.join(0.3)
            assert t.is_alive()         # blocked while we hold it
    t.join(5)
    assert order == ["outer", "other"]


_HOLD = textwrap.dedent("""
    import sys, time
    sys.path.insert(0, sys.argv[1])
    from pathlib import Path
    from src.core import durable as D
    lock = D.ProcessSharedLock(lambda: Path(sys.argv[2]), timeout_s=5)
    with lock:
        print("held", flush=True)
        time.sleep(float(sys.argv[3]))
""")


def test_process_shared_lock_excludes_another_process(tmp_path):
    lock_path = tmp_path / "q.lock"
    script = tmp_path / "hold.py"
    script.write_text(_HOLD, encoding="utf-8")
    child = subprocess.Popen([sys.executable, str(script), str(ROOT), str(lock_path), "3"],
                             stdout=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == "held"
        mine = D.ProcessSharedLock(lambda: lock_path, timeout_s=0.3)
        with pytest.raises(D.StateLockTimeout):
            mine.acquire()
    finally:
        child.wait(10)
    # Released when the other process let go.
    mine = D.ProcessSharedLock(lambda: lock_path, timeout_s=2)
    with mine:
        pass


def test_a_dead_holder_does_not_wedge_the_lock(tmp_path):
    lock_path = tmp_path / "q.lock"
    script = tmp_path / "hold.py"
    script.write_text(_HOLD, encoding="utf-8")
    child = subprocess.Popen([sys.executable, str(script), str(ROOT), str(lock_path), "60"],
                             stdout=subprocess.PIPE, text=True)
    assert child.stdout.readline().strip() == "held"
    child.kill()                         # a crash while holding the lock
    child.wait(10)
    with D.ProcessSharedLock(lambda: lock_path, timeout_s=3):
        pass


_SINGLE = textwrap.dedent("""
    import os, sys, time
    sys.path.insert(0, sys.argv[1])
    from pathlib import Path
    from src.core import durable as D
    one = D.SingleInstance(lambda: Path(sys.argv[2]))
    print(("got " if one.acquire() else "refused ") + str(os.getpid()), flush=True)
    time.sleep(float(sys.argv[3]))
""")


def test_single_instance_refuses_a_second_process_and_names_the_holder(tmp_path):
    lock_path = tmp_path / "desk.lock"
    script = tmp_path / "single.py"
    script.write_text(_SINGLE, encoding="utf-8")
    child = subprocess.Popen([sys.executable, str(script), str(ROOT), str(lock_path), "3"],
                             stdout=subprocess.PIPE, text=True)
    try:
        got, pid = child.stdout.readline().split()
        assert got == "got"
        mine = D.SingleInstance(lambda: lock_path)
        assert mine.acquire() is False
        # (the venv's python.exe is a launcher on Windows, so Popen.pid is
        # not the interpreter's pid — compare with what the holder reported)
        assert mine.holder().startswith(pid + " ")
    finally:
        child.wait(10)
    assert mine.acquire() is True and mine.acquire() is True   # idempotent
    mine.release()
