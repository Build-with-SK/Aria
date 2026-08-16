"""
tests/test_brain_eye_wiring.py
==============================
The eye runs on its own clock inside the brain daemon.

What must hold:
  1. Blinking is a SEPARATE scheduled job. If it ever moves onto the
     cognitive cycle's path, a throttled news feed becomes a stalled brain.
  2. A failing blink never touches the cognitive cycle.
  3. Two looks never overlap.
  4. The eye can be switched off entirely.

No network, no real scheduler.
"""
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.brain.brain_daemon import BrainDaemon  # noqa: E402


class FakeScheduler:
    """Records add_job calls instead of running anything."""

    def __init__(self):
        self.jobs = {}
        self.started = False

    def add_job(self, fn, trigger, **kwargs):
        job_id = kwargs.get("id") or f"anon-{len(self.jobs)}"
        self.jobs[job_id] = {"fn": fn, "trigger": trigger, **kwargs}

    def start(self):
        self.started = True

    def shutdown(self, wait=False):
        self.started = False

    def reschedule_job(self, job_id, trigger, **kwargs):
        if job_id not in self.jobs:
            raise KeyError(job_id)
        self.jobs[job_id].update(kwargs)


@pytest.fixture
def daemon(tmp_path, monkeypatch):
    monkeypatch.setattr(BrainDaemon, "STATE_FILE", tmp_path / "brain_state.json")
    d = BrainDaemon()
    fake = FakeScheduler()
    monkeypatch.setattr(
        "apscheduler.schedulers.background.BackgroundScheduler", lambda: fake
    )
    d._fake = fake
    return d


def test_eye_is_a_separate_job_from_the_cognitive_cycle(daemon, monkeypatch):
    monkeypatch.setattr(daemon, "_config", lambda: {})
    daemon.start()

    jobs = daemon._fake.jobs
    assert "brain_cycle" in jobs and "eye_blink" in jobs
    assert jobs["eye_blink"]["fn"] != jobs["brain_cycle"]["fn"], (
        "blinking must not share the cognitive cycle's job"
    )
    assert jobs["eye_blink"]["minutes"] == daemon.eye_interval_minutes


def test_eye_can_be_switched_off(daemon, monkeypatch):
    monkeypatch.setattr(daemon, "_config", lambda: {"eye_blink": False})
    daemon.start()
    assert "eye_blink" not in daemon._fake.jobs
    assert "brain_cycle" in daemon._fake.jobs, "the brain still thinks"


def test_eye_is_on_by_default(daemon, monkeypatch):
    monkeypatch.setattr(daemon, "_config", lambda: {})
    assert daemon._eye_enabled() is True


def test_blink_records_what_it_saw(daemon, monkeypatch):
    summary = {
        "at": "2026-08-13T10:00:00+00:00",
        "watches_total": 3, "watches_looked": 2, "suppressed": 4,
        "primed": [], "failed": {"ticker:nvda/reddit": "HTTP 429"},
        "observations": [
            {"title": "Nvidia cuts guidance", "source": "googlenews",
             "why": "3 independent sources carrying it", "url": "https://ex.com/a"},
        ],
    }
    import src.research.eye as eye_module
    monkeypatch.setattr(eye_module, "blink", lambda **k: summary)
    monkeypatch.setattr(eye_module, "attend_to_portfolio", lambda: [])

    daemon._run_blink()

    assert daemon.last_blink["observations"] == 1
    assert daemon.last_blink["suppressed"] == 4
    assert daemon.last_blink["failed"]["ticker:nvda/reddit"] == "HTTP 429"
    assert daemon.last_blink["top"][0]["title"] == "Nvidia cuts guidance"


def test_a_failing_eye_does_not_raise(daemon, monkeypatch):
    """A daemon that dies because a news feed timed out has traded a working
    brain for a working eye."""
    import src.research.eye as eye_module
    monkeypatch.setattr(eye_module, "attend_to_portfolio", lambda: [])
    monkeypatch.setattr(
        eye_module, "blink",
        lambda **k: (_ for _ in ()).throw(RuntimeError("every feed is down")))

    daemon._run_blink()          # must not raise

    assert "every feed is down" in daemon.last_blink["error"]
    assert daemon._blink_lock.locked() is False, "the lock must be released"


def test_broken_attention_still_lets_the_eye_look(daemon, monkeypatch):
    """A desk position file that cannot be read costs attention, not sight."""
    import src.research.eye as eye_module
    monkeypatch.setattr(
        eye_module, "attend_to_portfolio",
        lambda: (_ for _ in ()).throw(OSError("positions file locked")))
    monkeypatch.setattr(eye_module, "blink", lambda **k: {
        "at": "x", "watches_total": 1, "watches_looked": 1, "observations": []})

    daemon._run_blink()
    assert daemon.last_blink.get("error") is None
    assert daemon.last_blink["looked"] == 1


def test_two_looks_never_overlap(daemon, monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    calls = []

    def slow_blink(**kwargs):
        calls.append(1)
        entered.set()
        release.wait(timeout=5)
        return {"at": "x", "watches_total": 1, "watches_looked": 1,
                "observations": []}

    import src.research.eye as eye_module
    monkeypatch.setattr(eye_module, "attend_to_portfolio", lambda: [])
    monkeypatch.setattr(eye_module, "blink", slow_blink)

    first = threading.Thread(target=daemon._run_blink)
    first.start()
    assert entered.wait(timeout=5)

    daemon._run_blink()          # second look while the first is in flight
    assert len(calls) == 1, "overlapping looks must be skipped"

    release.set()
    first.join(timeout=5)


def test_status_reports_the_eye(daemon, monkeypatch):
    monkeypatch.setattr(daemon, "_config", lambda: {})
    status = daemon.status()
    assert status["eye"]["enabled"] is True
    assert status["eye"]["interval_minutes"] == daemon.eye_interval_minutes
    assert status["eye"]["looking"] is False
    assert status["eye"]["last"] is None


def test_set_eye_interval_reschedules(daemon, monkeypatch):
    monkeypatch.setattr(daemon, "_config", lambda: {})
    daemon.start()
    daemon.set_eye_interval(45)
    assert daemon.eye_interval_minutes == 45
    assert daemon._fake.jobs["eye_blink"]["minutes"] == 45


def test_set_eye_interval_is_safe_when_the_eye_is_off(daemon, monkeypatch):
    monkeypatch.setattr(daemon, "_config", lambda: {"eye_blink": False})
    daemon.start()
    daemon.set_eye_interval(45)          # no eye_blink job exists
    assert daemon.eye_interval_minutes == 45
