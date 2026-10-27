"""Only one process runs the desk.

Two backends at once — an orphaned uvicorn beside a fresh one — each ran the
hunt cycle, the management tick and the reflex loop against the same broker
account, and every lock the desk had was per process. The desk daemon now
takes a process-lifetime lock (src/core/durable.SingleInstance) before it
starts anything; a second process stays idle, says so in /api/desk/status,
and retries, so it takes over when the first one dies.
"""
from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

_HOLD = textwrap.dedent("""
    import sys, time
    sys.path.insert(0, sys.argv[1])
    from pathlib import Path
    from src.core import durable as D
    lock = D.SingleInstance(lambda: Path(sys.argv[2]))
    print("got" if lock.acquire() else "refused", flush=True)
    time.sleep(float(sys.argv[3]))
""")


class _Scheduler:
    def __init__(self, *a, **k):
        self.jobs, self.running = [], False

    def add_job(self, fn, *a, **k):
        self.jobs.append(k.get("id"))

    def start(self):
        self.running = True

    def shutdown(self, wait=False):
        self.running = False


class _Reflex:
    started = stopped = 0

    def start(self):
        _Reflex.started += 1

    def stop(self):
        _Reflex.stopped += 1


@pytest.fixture
def desk(tmp_path, monkeypatch):
    import apscheduler.schedulers.background as bg
    from src.desk import desk_daemon as dd
    from src.desk import reflex
    monkeypatch.setattr(dd, "DESK_DIR", tmp_path / "desk")
    monkeypatch.setattr(dd, "STATE_FILE", tmp_path / "desk" / "last_cycle.json")
    monkeypatch.setattr(dd, "_INSTANCE", None)
    monkeypatch.setattr(dd, "INSTANCE_RETRY_S", 3600)
    monkeypatch.setattr(bg, "BackgroundScheduler", _Scheduler)
    monkeypatch.setattr(reflex, "get_reflex", lambda: _Reflex())
    monkeypatch.setattr("src.desk.config.load_config",
                        lambda: {"interval_minutes": 30, "management_tick_minutes": 5})
    _Reflex.started = _Reflex.stopped = 0
    yield dd
    if dd._INSTANCE is not None:
        dd._INSTANCE.release()


def test_a_second_process_does_not_start_a_second_desk(desk, tmp_path):
    lock = tmp_path / "desk" / "desk_daemon.lock"
    lock.parent.mkdir(parents=True)
    script = tmp_path / "hold.py"
    script.write_text(_HOLD, encoding="utf-8")
    child = subprocess.Popen([sys.executable, str(script), str(ROOT), str(lock), "3"],
                             stdout=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == "got"
        d = desk.DeskDaemon()
        d.start()
        assert d.running is False and d.scheduler is None
        assert "another process already runs the desk" in d.instance_conflict
        assert _Reflex.started == 0, "no second reflex loop"
    finally:
        child.wait(15)
    # The other process is gone: the next attempt takes the desk over.
    d.start()
    assert d.running is True and d.instance_conflict == ""
    assert _Reflex.started == 1


def test_stop_releases_the_desk_and_a_restart_in_the_same_process_works(desk):
    d = desk.DeskDaemon()
    d.start()
    assert d.running and desk._instance().held
    d.stop()
    assert not desk._instance().held and _Reflex.stopped == 1
    d.start()
    assert d.running and desk._instance().held


def test_the_nightly_backup_includes_the_order_records(tmp_path, monkeypatch):
    """The queue is what blocks a second order for an UNKNOWN one; a damaged
    queue now stops the lane (fail closed), so it must be restorable."""
    import zipfile
    from src.desk import desk_daemon as dd
    desk_dir, exec_dir = tmp_path / "data" / "desk", tmp_path / "data" / "execution"
    desk_dir.mkdir(parents=True)
    exec_dir.mkdir(parents=True)
    (desk_dir / "playbooks.json").write_text("[]", encoding="utf-8")
    (exec_dir / "approval_queue.json").write_text("[]", encoding="utf-8")
    (exec_dir / "approval_queue.audit.jsonl").write_text("{}\n", encoding="utf-8")
    (exec_dir / "approval_queue.json.lock").write_bytes(b"")
    monkeypatch.setattr(dd, "ROOT", tmp_path)
    monkeypatch.setattr(dd, "DESK_DIR", desk_dir)
    monkeypatch.setattr(dd, "EXECUTION_DIR", exec_dir)
    dd._backup_desk_state()
    (zip_path,) = (tmp_path / "data" / "backups").glob("desk_*.zip")
    names = set(zipfile.ZipFile(zip_path).namelist())
    assert {"playbooks.json", "execution/approval_queue.json",
            "execution/approval_queue.audit.jsonl"} <= names
    assert "execution/approval_queue.json.lock" not in names
