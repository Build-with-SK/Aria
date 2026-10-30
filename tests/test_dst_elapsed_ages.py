"""
tests/test_dst_elapsed_ages.py
==============================
Data ages are elapsed time, including across a daylight-saving change.

On 2026-10-27 `world.dataset_freshness("signals.json")` reported a file
written 56 hours earlier as 55.0 hours old. The interval crossed the
2026-10-25 01:00 UTC UK clock change, and the age was taken between two naive
local wall-clock readings, which measures how far the clock moved, not how
much time passed. The execution gate and the UI read these ages against
FRESH/STALE budgets, so near a budget the hour flips the verdict.

Every test pins the clock (`src.core.clock.now`) and writes its stamp or
mtime from that pinned instant, so nothing here depends on today's date.
Naive stamps are written the way ARIA writes them, as this machine's local
wall time. On a machine whose zone does not change clocks in these windows the
old arithmetic happened to agree. `test_the_windows_cross_a_clock_change_here`
says whether this machine is one where these tests have teeth.
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from src.core import clock, world         # noqa: E402

UTC = timezone.utc
#: When UK clocks went back (BST -> GMT), and when they next go forward.
AUTUMN_CHANGE = datetime(2026, 10, 25, 1, 0, tzinfo=UTC)
SPRING_CHANGE = datetime(2027, 3, 28, 1, 0, tzinfo=UTC)


def _wall(at: datetime) -> str:
    """`at` stamped the way ARIA stamps it: datetime.now().isoformat()."""
    return at.astimezone().replace(tzinfo=None).isoformat(timespec="seconds")


@pytest.fixture
def pin(monkeypatch):
    def set_now(at: datetime) -> datetime:
        monkeypatch.setattr(clock, "now", lambda: at)
        return at
    return set_now


@pytest.fixture
def data(monkeypatch, tmp_path):
    """The world model reads a fixture directory, never the live data/."""
    d = tmp_path / "data"
    d.mkdir()
    monkeypatch.setattr(world, "DATA", d)

    def write(name: str, body, *, written_at: datetime) -> Path:
        p = d / name
        p.write_text(json.dumps(body), encoding="utf-8")
        t = written_at.timestamp()
        os.utime(p, (t, t))
        return p
    return write


def test_the_windows_cross_a_clock_change_here():
    """Guards the guards. On UK time the wall clock moves 55 h over the 56 h
    before 2026-10-26 12:00 UTC, so a reader subtracting naive local times
    would fail every test below."""
    now = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)
    then = now - timedelta(hours=56)
    assert then < AUTUMN_CHANGE < now
    wall = (datetime.fromisoformat(_wall(now)) - datetime.fromisoformat(_wall(then)))
    if wall == timedelta(hours=56):
        pytest.skip("this machine's zone does not change clocks on 2026-10-25; "
                    "the tests below pass here even with naive arithmetic")
    assert wall == timedelta(hours=55)


# ── the clock ───────────────────────────────────────────────────────────────

def test_a_naive_local_stamp_reads_back_as_the_instant_that_wrote_it(pin):
    now = pin(datetime(2026, 10, 26, 12, 0, tzinfo=UTC))
    stamp = datetime.fromisoformat(_wall(now - timedelta(hours=56)))
    assert clock.instant(stamp) == now - timedelta(hours=56)
    assert clock.hours_since(stamp) == 56.0


def test_an_aware_stamp_is_kept_as_it_is(pin):
    pin(datetime(2026, 10, 26, 12, 0, tzinfo=UTC))
    stamp = datetime(2026, 10, 24, 5, 0, tzinfo=timezone(timedelta(hours=1)))
    assert clock.instant(stamp) is stamp
    assert clock.hours_since(stamp) == 56.0


def test_a_stamp_before_1970_does_not_raise():
    """Windows cannot convert a naive local time before 1970. An absurd stated
    date must still age (as very old), not take the world model down."""
    assert clock.hours_since(datetime(1969, 12, 31)) > 24 * 365 * 50


# ── the world model: dataset_freshness, the gate, the pipeline marker ──────

@pytest.mark.parametrize("change", [AUTUMN_CHANGE, SPRING_CHANGE],
                         ids=["clocks-back-2026-10-25", "clocks-forward-2027-03-28"])
def test_a_file_age_is_elapsed_time_across_the_change(pin, data, change):
    """The reported failure: signals.json written 56 h earlier read 55.0 h."""
    now = pin(change + timedelta(hours=35))
    written = now - timedelta(hours=56)
    p = data("signals.json", {"AAPL": {"composite_score": 12}}, written_at=written)
    f = world.dataset_freshness("signals.json")
    assert f["age_hours"] == 56.0
    assert f["basis"] == "file_mtime" and f["status"] == "STALE"
    # as_of keeps its old form: the write time as a naive local reading.
    assert f["as_of"] == datetime.fromtimestamp(p.stat().st_mtime).isoformat(
        timespec="seconds")
    assert f["stale_threshold_hours"] == 30


def test_the_execution_gate_does_not_read_a_stale_file_as_fresh(pin, data):
    """30.5 h against the 30 h signals budget. A wall-clock age across the
    autumn change is 29.5 h, which opened the auto-execution gate on a file
    past its budget."""
    now = pin(datetime(2026, 10, 25, 20, 0, tzinfo=UTC))
    written = now - timedelta(hours=30.5)
    assert written < AUTUMN_CHANGE < now
    data("signals.json", {"AAPL": {"composite_score": 12}}, written_at=written)
    from src.desk import opinion
    gate = opinion.signals_freshness()
    assert gate["fresh"] is False
    assert gate["age_hours"] == 30.5


def test_a_naive_stated_timestamp_is_aged_in_elapsed_time(pin, data):
    """brain_state.json states its own `timestamp`, a naive local stamp
    (brain_daemon: datetime.now().isoformat()). 6.5 h against the 6 h brain
    budget read 5.5 h, FRESH, across the autumn change."""
    now = pin(datetime(2026, 10, 25, 4, 0, tzinfo=UTC))
    stamp = _wall(now - timedelta(hours=6.5))
    data("brain_state.json", {"timestamp": stamp}, written_at=now)
    f = world.dataset_freshness("brain_state.json")
    assert f["basis"] == "stated_date"
    assert f["age_hours"] == 6.5 and f["status"] == "STALE"
    assert f["as_of"] == stamp                  # reported exactly as stated


def test_an_unfinished_pipeline_run_is_aged_in_elapsed_time(pin, data):
    """pipeline_run.json's naive `started_at` (main.py _mark_run). 8.5 h
    against PIPELINE_MAX_HOURS = 8 read 7.5 h, IN_PROGRESS, across the change."""
    now = pin(datetime(2026, 10, 25, 6, 0, tzinfo=UTC))
    started = _wall(now - timedelta(hours=8.5))
    data("pipeline_run.json", {"started_at": started, "step": "run_ml",
                               "completed": False}, written_at=now)
    run = world.pipeline_run()
    assert run["state"] == "DID_NOT_FINISH"
    assert "started 8.5h ago" in run["why"]


# ── the sibling readers ─────────────────────────────────────────────────────

def test_the_brain_cycle_age_is_elapsed_time(pin, monkeypatch):
    """self_state reads the worker registry's naive `last_ok`."""
    from src.brain import self_state
    from src.core import workers
    now = pin(datetime(2026, 10, 25, 1, 30, tzinfo=UTC))
    last = _wall(now - timedelta(hours=2.5))
    monkeypatch.setattr(workers, "status", lambda: {"workers": [
        {"name": "brain", "state": "ok", "last_ok": last}]})
    cycle = self_state.brain_cycle()
    assert cycle["age_hours"] == 2.5
    assert "(2.5h ago)" in cycle["detail"]


def test_the_model_card_file_age_is_elapsed_time(pin, tmp_path):
    """2.06 days is 2.1 rounded; an hour short, 2.02 days, rounds to 2.0."""
    from src.intelligence import registry
    now = pin(datetime(2026, 10, 25, 20, 0, tzinfo=UTC))
    p = tmp_path / "ml_predictions.json"
    p.write_text("{}", encoding="utf-8")
    t = (now - timedelta(days=2.06)).timestamp()
    os.utime(p, (t, t))
    card = registry._file_age(p)
    assert card["age_days"] == 2.1
    assert card["modified"] == datetime.fromtimestamp(t).isoformat(timespec="seconds")


def test_the_evidence_snapshot_age_is_elapsed_time(pin):
    """The snapshot the order path reads is rebuilt past 6 h; 6.5 h read 5.5 h."""
    from src.intelligence import evidence
    now = pin(datetime(2026, 10, 25, 4, 0, tzinfo=UTC))
    assert evidence.age_hours({"built_at": _wall(now - timedelta(hours=6.5))}) == 6.5


def test_the_brain_cycle_age_reads_a_repeated_hour_tick_as_a_heartbeat(pin, monkeypatch):
    """A tick at 01:15 GMT, in the hour that repeats, is 15 minutes old at
    01:30 GMT. Read as 01:15 BST it would be 1 h 15 min."""
    from src.brain import self_state
    from src.core import workers
    now = pin(AUTUMN_CHANGE + timedelta(minutes=30))
    last = _wall(AUTUMN_CHANGE + timedelta(minutes=15))
    monkeypatch.setattr(workers, "status", lambda: {"workers": [
        {"name": "brain", "state": "ok", "last_ok": last}]})
    assert self_state.brain_cycle()["age_hours"] == 0.25


# ── the worker registry: late / stalled / failing ──────────────────────────

def _row(last_ok: str, interval_s: int = 900, last_error_at: str | None = None) -> dict:
    return {"enabled": 1, "last_ok": last_ok, "interval_s": interval_s,
            "last_error_at": last_error_at}


def test_a_worker_ticking_up_to_clocks_going_forward_is_not_stalled():
    """15-minute worker, last tick 00:55 GMT, read at 02:00 BST: 5 minutes.
    As wall-clock readings that is 65 minutes, past 3 x 900 s: stalled."""
    from src.core import workers
    now = SPRING_CHANGE
    state, age = workers._state_of(_row(_wall(now - timedelta(minutes=5))), now)
    assert (state, age) == ("ok", 300)


def test_a_worker_ticking_through_the_repeated_hour_is_not_stalled():
    """Ticks at 01:15 GMT, the second 01:15 of the night. Read as the first
    (BST) it is an hour older, and every fast worker would show stalled for
    the whole repeated hour."""
    from src.core import workers
    now = AUTUMN_CHANGE + timedelta(minutes=20)
    state, age = workers._state_of(_row(_wall(now - timedelta(minutes=5))), now)
    assert (state, age) == ("ok", 300)


def test_a_worker_that_stopped_before_clocks_went_back_is_stalled():
    """Last tick 01:20 BST, read at 01:10 GMT: 50 minutes. As wall-clock
    readings that is minus 10 minutes, so the stall went unseen for an hour."""
    from src.core import workers
    now = AUTUMN_CHANGE + timedelta(minutes=10)
    state, age = workers._state_of(_row(_wall(now - timedelta(minutes=50))), now)
    assert (state, age) == ("stalled", 3000)


def test_a_failure_after_clocks_went_back_is_later_than_the_success_before():
    """Success 01:50 BST, failure 01:10 GMT, 20 minutes later. As strings
    the failure looks 40 minutes earlier, so the worker read `ok`."""
    from src.core import workers
    now = AUTUMN_CHANGE + timedelta(minutes=12)
    ok_at = _wall(AUTUMN_CHANGE - timedelta(minutes=10))
    failed_at = _wall(AUTUMN_CHANGE + timedelta(minutes=10))
    state, _ = workers._state_of(_row(ok_at, last_error_at=failed_at), now)
    assert state == "failing"


@pytest.fixture
def registry(tmp_path, monkeypatch):
    """A throwaway worker registry (see test_core_spine.spine on why the
    environment variable, not bus.DB_PATH, is what isolates it)."""
    monkeypatch.setenv("ARIA_CORE_DB", str(tmp_path / "spine.db"))
    from src.core import bus, workers
    monkeypatch.setattr(bus, "_initialised", False)
    monkeypatch.setattr(bus, "_subscribers", [])
    monkeypatch.setattr(workers, "_ready", False)
    return workers


def test_next_expected_is_one_interval_of_elapsed_time_later(pin, registry):
    """30-minute worker, last tick 01:50 BST: next due 01:20 GMT, not 02:20."""
    now = pin(AUTUMN_CHANGE + timedelta(minutes=5))
    last = AUTUMN_CHANGE - timedelta(minutes=10)
    registry.register("w", "Worker", interval_s=1800)
    with registry._db() as conn:
        conn.execute("UPDATE workers SET last_ok=? WHERE name='w'", (_wall(last),))
    row = registry.status()["workers"][0]
    assert row["state"] == "ok" and row["age_seconds"] == 900
    assert row["next_expected"] == _wall(last + timedelta(minutes=30))
    assert row["last_ok"] == _wall(last)              # stored stamp unchanged


def test_a_v5_prediction_past_four_days_is_not_a_current_view(pin):
    """focus drops predictions older than 4 days; 4 days and 30 minutes read
    3.98 days across the change and stayed on the desk's list."""
    from src.desk import focus
    now = pin(datetime(2026, 10, 26, 0, 0, tzinfo=UTC))
    age = focus._age_days(_wall(now - timedelta(hours=96.5)))
    assert age == pytest.approx(96.5 / 24)
    assert age > focus.MAX_PREDICTION_AGE_DAYS
