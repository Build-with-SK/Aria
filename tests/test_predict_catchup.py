"""
tests/test_predict_catchup.py
=============================
The missed-slot fix in `src/v5/loop.py`.

`resolve` ran on startup; `predict` was a plain daily cron. A machine that was
not awake at 22:10 skipped that day's predictions entirely, and nothing said
so — the resolve leg went on turning over an input that had stopped arriving.
Sixteen predictions exist in total and none have resolved.

Three things have to hold, and the last two are what stop this fix from
becoming a quiet corruption of the sample:

  - a missed slot is made up
  - a slot that has not arrived yet is left alone, because a call made at 11am
    against an unsettled session is a different measurement
  - it never runs twice in a day, so a restart loop cannot manufacture rows
"""
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from src.v5 import loop as L


@pytest.fixture
def heartbeat(tmp_path, monkeypatch):
    path = tmp_path / "loop_heartbeat.json"
    monkeypatch.setattr(L, "HEARTBEAT", path)
    return path


@pytest.fixture
def ran(monkeypatch):
    """Record whether the real predict leg was invoked."""
    calls = []
    monkeypatch.setattr(L, "predict_once",
                        lambda *a, **kw: calls.append(1) or {"logged": 3})
    return calls


def beat(path: Path, when: str):
    path.write_text(json.dumps({"predict": {"at": when}}), encoding="utf-8")


AFTER = datetime(2026, 8, 12, 23, 30)      # past the 22:10 slot
BEFORE = datetime(2026, 8, 12, 11, 0)      # before it


def test_a_missed_slot_is_made_up(heartbeat, ran):
    beat(heartbeat, "2026-08-09T22:10:00")          # three days ago
    out = L.predict_catchup(now=AFTER)
    assert out["ran"] is True and out["catchup"] is True
    assert len(ran) == 1


def test_a_loop_that_has_never_run_is_started(heartbeat, ran):
    out = L.predict_catchup(now=AFTER)
    assert out["ran"] is True
    assert len(ran) == 1


def test_before_the_slot_it_waits_for_the_cron(heartbeat, ran):
    """A call made mid-session is not the same measurement as one made after
    the close. Mixing them would put a seam through the sample that nobody
    would find later."""
    beat(heartbeat, "2026-08-09T22:10:00")
    out = L.predict_catchup(now=BEFORE)
    assert out["ran"] is False
    assert "not the same measurement" in out["why"]
    assert ran == []


def test_it_never_runs_twice_in_one_day(heartbeat, ran):
    """Otherwise every restart adds a day's worth of predictions, and the
    track record inflates without a single new market day."""
    beat(heartbeat, "2026-08-12T22:10:00")
    out = L.predict_catchup(now=AFTER)
    assert out["ran"] is False
    assert "already run today" in out["why"]
    assert ran == []


def test_a_restart_loop_cannot_manufacture_rows(heartbeat, monkeypatch):
    """The real predict leg writes the heartbeat, so the second call declines
    on its own rather than relying on the caller to remember."""
    calls = []

    def fake_predict(*a, **kw):
        calls.append(1)
        beat(heartbeat, AFTER.isoformat())
        return {"logged": 3}

    monkeypatch.setattr(L, "predict_once", fake_predict)
    for _ in range(5):
        L.predict_catchup(now=AFTER)
    assert len(calls) == 1


def test_an_unreadable_heartbeat_does_not_block_the_loop(heartbeat, ran):
    """Failing closed here would mean a corrupt file silently stops her
    learning — the exact failure this is fixing."""
    heartbeat.write_text("{ not json", encoding="utf-8")
    assert L.predict_catchup(now=AFTER)["ran"] is True
    assert len(ran) == 1


def test_the_daemon_registers_the_catchup():
    """The fix only works if it is wired in beside resolve."""
    import inspect
    from src.desk import desk_daemon
    source = inspect.getsource(desk_daemon.DeskDaemon.start)
    assert "predict_catchup" in source
    assert "v5_predict_catchup" in source
