"""
tests/test_predict_catchup.py
=============================
The missed-slot fix in `src/v5/loop.py`, keyed on NYSE sessions.

History. `resolve` ran on startup; `predict` was a plain 22:10 cron. The first
fix added a startup catch-up that declined "before 22:00 local" — and from
23 Sep to 6 Oct 2026 every backend start was before 22:00 London and the box
was never up at 22:10, so no prediction was made for thirteen days while the
resolve leg kept turning. "22:00 London" was a proxy for "after the US close";
the rule is now stated in sessions.

What has to hold — the last three are what stop the fix from quietly
corrupting the sample:

  - a missed session is made up, including from a morning restart
  - nothing runs inside the session window, because a call on a partial daily
    bar is a different measurement
  - one call per completed session, so a restart loop or a weekend cannot
    manufacture rows
  - without a calendar it refuses rather than guessing
"""
import json
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from src.v5 import loop as L

NY = ZoneInfo("America/New_York")
LDN = ZoneInfo("Europe/London")


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


def beat(path: Path, session: str, at: str = "2026-01-01T00:00:00"):
    path.write_text(json.dumps({"predict": {"at": at, "session": session}}),
                    encoding="utf-8")


AFTER = datetime(2026, 8, 12, 18, 30, tzinfo=NY)     # Wed, settled
MID = datetime(2026, 8, 12, 11, 0, tzinfo=NY)        # Wed, mid-session
SETTLING = datetime(2026, 8, 12, 16, 30, tzinfo=NY)  # closed, bar not final


def test_a_missed_session_is_made_up(heartbeat, ran):
    beat(heartbeat, "2026-08-07")                    # last Friday
    out = L.predict_catchup(now=AFTER)
    assert out["ran"] is True and out["catchup"] is True
    assert out["session"] == "2026-08-12"
    assert len(ran) == 1


def test_a_loop_that_has_never_run_is_started(heartbeat, ran):
    out = L.predict_catchup(now=AFTER)
    assert out["ran"] is True
    assert len(ran) == 1


def test_the_thirteen_day_stall_cannot_recur(heartbeat, ran):
    """The real failure: last calls on 23 Sep, the box restarted at 07:38
    London on Monday 5 Oct. That is 02:38 in New York — the market is shut and
    Friday 2 Oct is the latest settled close, so the call is due now."""
    beat(heartbeat, "2026-09-23")
    out = L.predict_catchup(now=datetime(2026, 10, 5, 7, 38, tzinfo=LDN))
    assert out["ran"] is True
    assert out["session"] == "2026-10-02"
    assert len(ran) == 1


def test_inside_the_session_it_waits(heartbeat, ran):
    """A call made mid-session is not the same measurement as one made after
    the close. Mixing them would put a seam through the sample that nobody
    would find later."""
    beat(heartbeat, "2026-08-10")
    out = L.predict_catchup(now=MID)
    assert out["ran"] is False
    assert "not the same measurement" in out["why"]
    assert ran == []


def test_between_the_close_and_the_settle_it_waits(heartbeat, ran):
    """At 16:30 New York today's bar exists but is not final; a call built on
    it would be labelled with yesterday's session while reading today's."""
    beat(heartbeat, "2026-08-10")
    assert L.predict_catchup(now=SETTLING)["ran"] is False
    assert ran == []


def test_it_never_runs_twice_for_one_session(heartbeat, ran):
    """Otherwise every restart adds a session's worth of predictions, and the
    track record inflates without a single new market day."""
    beat(heartbeat, "2026-08-12")
    out = L.predict_catchup(now=AFTER)
    assert out["ran"] is False
    assert "already run for the 2026-08-12 session" in out["why"]
    assert ran == []


def test_a_weekend_adds_nothing(heartbeat, ran):
    """The old daily cron fired on Saturday and Sunday too, logging Friday's
    close twice more as if it were new evidence (70 such rows exist)."""
    beat(heartbeat, "2026-08-14")                    # Friday done
    for day in (15, 16):                             # Sat, Sun
        out = L.predict_catchup(now=datetime(2026, 8, day, 18, 0, tzinfo=NY))
        assert out["ran"] is False
    assert ran == []


def test_a_holiday_is_not_a_session(heartbeat, ran):
    beat(heartbeat, "2026-09-04")                    # Fri before Labor Day
    out = L.predict_catchup(now=datetime(2026, 9, 7, 20, 0, tzinfo=NY))
    assert out["ran"] is False
    assert ran == []


def test_without_a_calendar_it_refuses_rather_than_guesses(heartbeat, ran):
    out = L.predict_catchup(now=datetime(2031, 3, 4, 20, 0, tzinfo=NY))
    assert out["ran"] is False
    assert "calendar" in out["why"]
    assert ran == []


def test_a_restart_loop_cannot_manufacture_rows(heartbeat, monkeypatch):
    """The real predict leg writes the heartbeat, so the second call declines
    on its own rather than relying on the caller to remember."""
    calls = []

    def fake_predict(*a, **kw):
        calls.append(1)
        beat(heartbeat, "2026-08-12")
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


def test_a_legacy_heartbeat_maps_to_its_session(heartbeat):
    """Heartbeats written before sessions were recorded carry only a time.
    22:11 London on 23 Sep is 17:11 New York — the 23 Sep close had settled."""
    heartbeat.write_text(json.dumps({"predict": {"at": "2026-09-23T22:11:16+01:00"}}),
                         encoding="utf-8")
    assert L.predict_last_session() == date(2026, 9, 23)


# ── health: the missed-run state is observable ───────────────────────────────

def test_health_counts_the_missed_sessions(heartbeat):
    beat(heartbeat, "2026-09-23")
    h = L.predict_health(now=datetime(2026, 10, 5, 7, 38, tzinfo=LDN))
    assert h["ok"] is False
    assert h["expected_session"] == "2026-10-02"
    # 24, 25, 28, 29, 30 Sep and 1, 2 Oct
    assert h["missed_sessions"] == 7


def test_health_allows_the_hour_after_the_settle(heartbeat):
    beat(heartbeat, "2026-08-11")
    assert L.predict_health(now=datetime(2026, 8, 12, 18, 0, tzinfo=NY))["ok"] is True
    assert L.predict_health(now=datetime(2026, 8, 12, 23, 30, tzinfo=NY))["ok"] is False


def test_health_is_not_fooled_by_a_weekend(heartbeat):
    """The old 36-hour rule reported every Monday morning as a stall."""
    beat(heartbeat, "2026-08-14")
    h = L.predict_health(now=datetime(2026, 8, 17, 8, 0, tzinfo=NY))
    assert h["ok"] is True and h["missed_sessions"] == 0


# ── the dedupe key matches the session key ──────────────────────────────────

def test_a_saturday_call_is_fridays_information():
    from src.v5.learning import information_key
    fri = information_key(datetime(2026, 8, 14, 18, 0, tzinfo=NY))
    sat = information_key(datetime(2026, 8, 15, 12, 0, tzinfo=NY))
    assert fri == sat == ("2026-08-14", False)


def test_monday_morning_and_monday_evening_are_different_calls():
    from src.v5.learning import information_key
    pre_open = information_key(datetime(2026, 8, 17, 7, 0, tzinfo=NY))
    evening = information_key(datetime(2026, 8, 17, 19, 0, tzinfo=NY))
    mid = information_key(datetime(2026, 8, 17, 11, 0, tzinfo=NY))
    assert pre_open == ("2026-08-14", False)
    assert evening == ("2026-08-17", False)
    assert mid == ("2026-08-14", True)          # partial bar: its own key
    assert len({pre_open, evening, mid}) == 3


# ── wiring ──────────────────────────────────────────────────────────────────

def test_the_daemon_checks_hourly_and_at_startup():
    """The fix only works if it is wired in: the cron job must run the guarded
    catch-up (never the raw leg) and the startup job must exist."""
    import inspect
    from src.desk import desk_daemon
    source = inspect.getsource(desk_daemon.DeskDaemon.start)
    assert "v5_predict_catchup" in source
    assert "v5_loop.predict_once" not in source
    assert source.count("v5_loop.predict_catchup") >= 2
