"""
tests/test_track_record_threshold.py
====================================
PHASE 4 — track record infrastructure.

Two questions, both of which were previously answered by "presumably":

  1. Does the track record actually start reporting once 20 calls resolve, or
     does it stay silent because the threshold is checked in one place and the
     numbers are computed in another?
  2. Is anything actually running that would ever resolve a call?

The second turned out to matter more than the first. `resolve_pending()` was
reachable only from a manual POST endpoint — no scheduler, no loop — so the
counter this file tests could never have moved on its own no matter how long
the machine stayed up. src/v5/loop.py is that missing loop, and the tests below
pin down that it exists, that it is scheduled, and that it is honest about
whether it has run.

Every test writes to a temporary prediction log. Synthetic outcomes must never
touch the real calibration record — a track record with test data in it is
worse than no track record.
"""
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture
def fake_log(tmp_path, monkeypatch):
    """Point the prediction log at a temp file and return a seeder."""
    from src.v5 import learning
    log = tmp_path / "predictions.jsonl"
    monkeypatch.setattr(learning, "V5_DIR", tmp_path)
    monkeypatch.setattr(learning, "PRED_LOG", log)
    monkeypatch.setattr(learning, "WEIGHTS_FILE", tmp_path / "weights.json")

    def seed(n, *, resolved=True, hit_rate=0.6, confidence=0.62):
        rows = []
        for i in range(n):
            correct = i < int(round(n * hit_rate))
            at = datetime.now() - timedelta(days=60 - i)
            rows.append({
                "id": f"v5-test{i:04d}",
                "at": at.isoformat(timespec="seconds"),
                "ticker": "AAPL", "direction": "bull",
                "p_bull": 0.62, "confidence": confidence,
                "horizon_days": 21,
                "resolve_after": (at + timedelta(days=29)).date().isoformat(),
                "price_at": 100.0,
                "modules": [{"module": "momentum", "family": "price", "net": 40,
                             "view": "bull", "abstained": False}],
                "risk": {}, "weights_version": 1,
                "resolved": resolved,
                **({"correct": bool(correct), "realised_return": 0.03 if correct else -0.03}
                   if resolved else {}),
            })
        log.write_text("\n".join(json.dumps(r) for r in rows) + "\n",
                       encoding="utf-8")
        return rows

    return seed


# ── 1. the threshold ─────────────────────────────────────────────────────────

def test_below_the_threshold_it_refuses_to_report(fake_log):
    from src.v5 import track_record
    fake_log(19)
    out = track_record.build()
    assert out["calibration"]["measurable"] is False
    assert out["calibration"]["n_resolved"] == 19
    assert out["calibration"]["n_required"] == 20
    assert out["calibration"]["buckets"] == []
    # And it says why, in words, rather than showing a number with a caveat
    # nobody reads.
    assert "too small a sample" in out["headline"]["text"]


def test_at_exactly_twenty_it_starts_reporting(fake_log):
    """The flip. One more resolved call than the previous test."""
    from src.v5 import track_record
    fake_log(20)
    out = track_record.build()
    cal = out["calibration"]
    assert cal["measurable"] is True
    assert cal["n_resolved"] == 20
    assert isinstance(cal["brier"], float)
    assert cal["buckets"], "measurable but no buckets — the flip is cosmetic"
    assert cal["verdict"]


def test_the_headline_changes_with_the_flip(fake_log):
    """A stranger reading the top line must be able to tell the difference."""
    from src.v5 import track_record
    fake_log(19)
    before = track_record.build()["headline"]["text"]
    fake_log(20)
    after = track_record.build()["headline"]["text"]
    assert before != after
    assert "too small a sample" in before
    assert "too small a sample" not in after
    assert "resolved calls" in after


def test_the_numbers_are_real_not_placeholders(fake_log):
    """Reporting must mean reporting: a hit rate that matches the seeded
    outcomes, not a constant that appears once the gate opens."""
    from src.v5 import track_record
    fake_log(40, hit_rate=0.75, confidence=0.62)
    out = track_record.build()
    assert out["performance"]["resolved"] == 40
    assert out["performance"]["hit_rate"] == pytest.approx(0.75, abs=0.02)
    assert out["performance"]["mean_confidence"] == pytest.approx(0.62, abs=0.01)
    # Stated 62%, realised 75% → under-confident, and the gap is reported signed.
    assert out["performance"]["calibration_gap"] == pytest.approx(-0.13, abs=0.02)


def test_unresolved_predictions_do_not_count_towards_the_threshold(fake_log):
    """Otherwise the gate opens on opinions rather than on outcomes."""
    from src.v5 import track_record
    fake_log(50, resolved=False)
    out = track_record.build()
    assert out["calibration"]["measurable"] is False
    assert out["calibration"]["n_resolved"] == 0
    assert "Nothing has resolved yet" in out["headline"]["text"]


def test_an_empty_record_says_it_has_no_track_record(fake_log):
    from src.v5 import track_record
    fake_log(0)
    out = track_record.build()
    assert out["calibration"]["measurable"] is False
    assert "no track record" in out["headline"]["text"]


def test_a_bucket_below_its_own_minimum_stays_unreported(fake_log):
    """The overall gate opening does not open every sub-gate. Five calls in a
    bucket is still five calls."""
    from src.v5 import track_record
    fake_log(20)
    cal = track_record.build()["calibration"]
    for b in cal["buckets"]:
        if b["n"] < track_record.MIN_PER_BUCKET:
            assert b["reportable"] is False
            assert b["realised_frequency"] is None


# ── 2. the loop that makes resolution happen ─────────────────────────────────

def test_the_research_loop_exists_and_has_both_legs():
    from src.v5 import loop
    assert callable(loop.predict_once)
    assert callable(loop.resolve_once)
    assert loop.watchlist(), "a track record needs a universe fixed in advance"


def test_the_loop_is_actually_scheduled():
    """The finding this phase turned up: resolve_pending() had no caller except
    a manual endpoint, so the counter could never move unattended."""
    import inspect
    from src.desk import desk_daemon
    src = inspect.getsource(desk_daemon.DeskDaemon.start)
    assert "v5_predict" in src and "v5_resolve" in src
    assert "predict_once" in src and "resolve_once" in src


def test_the_scheduler_will_not_silently_drop_a_late_job():
    """APScheduler's default misfire_grace_time is one second. On a loaded box
    that means jobs are skipped with a log line and the loop quietly stops."""
    import inspect
    from src.desk import desk_daemon
    src = inspect.getsource(desk_daemon.DeskDaemon.start)
    assert "misfire_grace_time" in src
    assert "coalesce" in src


def test_a_watchdog_checks_the_scheduler_is_still_alive():
    from src.desk import desk_daemon
    assert hasattr(desk_daemon.DeskDaemon, "_watchdog")
    import inspect
    src = inspect.getsource(desk_daemon.DeskDaemon._watchdog)
    assert "self.start()" in src            # it repairs, not just complains


def test_loop_health_reports_never_run_honestly(tmp_path, monkeypatch, fake_log):
    from src.v5 import loop
    monkeypatch.setattr(loop, "HEARTBEAT", tmp_path / "hb.json")
    fake_log(0)
    h = loop.health()
    assert h["healthy"] is False
    assert h["note"] == "The loop has never run."
    assert h["predict"]["last_run"] is None


def test_loop_health_reports_a_stall(tmp_path, monkeypatch, fake_log):
    """Running-but-stalled is the dangerous state: the app answers requests
    normally while the flywheel has stopped."""
    from src.v5 import loop
    hb = tmp_path / "hb.json"
    monkeypatch.setattr(loop, "HEARTBEAT", hb)
    monkeypatch.setattr(loop, "STATE_DIR", tmp_path)
    old = (datetime.now() - timedelta(days=5)).isoformat(timespec="seconds")
    hb.write_text(json.dumps({"predict": {"at": old}, "resolve": {"at": old}}),
                  encoding="utf-8")
    fake_log(5)
    h = loop.health()
    assert h["healthy"] is False
    assert "stalled" in h["note"]
    assert h["predict"]["hours_ago"] > 36


def test_loop_health_reports_healthy_when_both_legs_are_fresh(tmp_path, monkeypatch,
                                                              fake_log):
    from src.v5 import loop
    hb = tmp_path / "hb.json"
    monkeypatch.setattr(loop, "HEARTBEAT", hb)
    monkeypatch.setattr(loop, "STATE_DIR", tmp_path)
    now = datetime.now().isoformat(timespec="seconds")
    hb.write_text(json.dumps({"predict": {"at": now, "logged": 10},
                              "resolve": {"at": now, "resolved": 2}}),
                  encoding="utf-8")
    fake_log(25)
    h = loop.health()
    assert h["healthy"] is True
    assert h["note"] == "Running."
    assert h["predictions_resolved"] == 25


def test_one_bad_ticker_does_not_stop_the_days_run(monkeypatch, tmp_path):
    """A delisted name or a vendor hiccup should cost one row, not the sample."""
    from src.v5 import loop
    monkeypatch.setattr(loop, "HEARTBEAT", tmp_path / "hb.json")
    monkeypatch.setattr(loop, "STATE_DIR", tmp_path)

    def fake_analyze(ticker, log=True):
        if ticker == "BOOM":
            raise RuntimeError("vendor exploded")
        if ticker == "GONE":
            return {"error": "No price history could be loaded"}
        return {"ticker": ticker, "prediction_id": f"p-{ticker}",
                "ensemble": {"direction": "bull"},
                "recommendation": {"confidence": 0.6},
                "data": {"stale": False}}

    monkeypatch.setattr("src.v5.pipeline.analyze", fake_analyze)
    out = loop.predict_once(["AAPL", "BOOM", "GONE", "MSFT"])
    assert out["logged"] == 2
    assert out["errors"] == 1 and out["skipped"] == 1
    assert {d["ticker"] for d in out["detail"]} == {"AAPL", "MSFT"}
