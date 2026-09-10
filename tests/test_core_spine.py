"""
tests/test_core_spine.py
========================
The spine: event bus, prediction ledger, worker registry, world model.

These tests pin the properties that make the spine trustworthy rather than
merely present. The ones that matter most are the negative cases — a system
whose whole claim is "it does not pretend" has to be tested on what it
REFUSES to say:

  * calibration refuses to report below its minimum sample
  * a hit rate carries the sample size that would invalidate it
  * a stale source is reported stale rather than carried forward
  * an unknown quantity appears in `unknowns` rather than defaulting to zero
  * an unknown event kind is dropped, not silently invented

Every test runs against a temporary database, so nothing here touches
data/aria_core.db.
"""
from __future__ import annotations

import importlib
from datetime import datetime, timedelta

import pytest


@pytest.fixture
def spine(tmp_path, monkeypatch):
    """A fresh bus + ledger + registry on a per-test throwaway database.

    Sets ARIA_CORE_DB rather than patching bus.DB_PATH. `_db_path()` checks the
    environment FIRST, so patching the module attribute alone is silently
    ineffective once conftest.py has set the variable session-wide — every test
    here would quietly share one database and see each other's rows. That is
    exactly what happened on the first run: ten of these tests failed only when
    the full suite ran, and passed in isolation.

    The modules cache a "schema is present" flag at module level, so that is
    reset too — otherwise the second test in a session skips CREATE TABLE
    against a file that does not have the tables yet.
    """
    monkeypatch.setenv("ARIA_CORE_DB", str(tmp_path / "spine.db"))

    from src.core import bus as bus_mod
    monkeypatch.setattr(bus_mod, "_initialised", False)
    monkeypatch.setattr(bus_mod, "_subscribers", [])

    from src.core import ledger as ledger_mod
    from src.core import workers as workers_mod
    monkeypatch.setattr(ledger_mod, "_ready", False)
    monkeypatch.setattr(workers_mod, "_ready", False)
    return bus_mod, ledger_mod, workers_mod


# ── the bus ─────────────────────────────────────────────────────────────────

def test_publish_persists_and_reads_back(spine):
    bus, _, _ = spine
    eid = bus.publish("MARKET_UPDATE", "regime moved", source="test", subject="SPY")
    assert eid
    got = bus.recent(limit=5)
    assert len(got) == 1
    assert got[0]["summary"] == "regime moved"
    assert got[0]["subject"] == "SPY"


def test_unknown_kind_is_dropped_not_invented(spine):
    """A typo must not become a new event type nobody can filter on."""
    bus, _, _ = spine
    assert bus.publish("TOTALLY_MADE_UP", "nope") is None
    assert bus.recent(limit=5) == []


def test_subscriber_exception_cannot_break_the_publisher(spine):
    """A broken listener must never take down the component that emitted."""
    bus, _, _ = spine
    bus.subscribe(lambda e: 1 / 0)
    seen = []
    bus.subscribe(lambda e: seen.append(e["kind"]))
    assert bus.publish("SYSTEM_NOTE", "still fine") is not None
    assert seen == ["SYSTEM_NOTE"]          # the good subscriber still ran
    assert len(bus.recent(limit=5)) == 1    # and the event was persisted


def test_provenance_survives_the_round_trip(spine):
    """§45 — where a claim came from has to travel with it."""
    bus, _, _ = spine
    bus.publish("OBSERVATION_RECORDED", "a headline", source="research_eye",
                provenance={"source": "reddit", "url": "https://example.test/x"})
    ev = bus.recent(limit=1)[0]
    assert ev["provenance"]["source"] == "reddit"
    assert ev["provenance"]["url"].startswith("https://")


def test_severity_filter_is_inclusive_upwards(spine):
    bus, _, _ = spine
    bus.publish("SYSTEM_NOTE", "chatter", severity="info")
    bus.publish("REGIME_CHANGED", "regime", severity="notable")
    bus.publish("APPROVAL_REQUIRED", "decide", severity="action")
    assert len(bus.recent(limit=20, min_severity="notable")) == 2
    assert len(bus.recent(limit=20, min_severity="action")) == 1


# ── the ledger ──────────────────────────────────────────────────────────────

def test_prediction_is_recorded_with_a_resolve_date(spine):
    _, ledger, _ = spine
    pid = ledger.record_prediction(
        subject="aapl", claim="bull over 10 days", direction="bull",
        probability=0.7, horizon_days=10, price_at=100.0, source="test")
    assert pid
    rows = ledger.predictions(limit=5)
    assert len(rows) == 1
    assert rows[0]["subject"] == "AAPL"          # normalised
    assert rows[0]["resolved"] == 0
    # 10 trading days is 14 calendar days.
    due = datetime.fromisoformat(rows[0]["resolve_after"])
    made = datetime.fromisoformat(rows[0]["created_at"])
    assert 13 <= (due.date() - made.date()).days <= 15


def test_external_ref_deduplicates_reingestion(spine):
    """Ingest runs on every boot and every half hour; it must be a no-op."""
    _, ledger, _ = spine
    for _ in range(3):
        ledger.record_prediction(subject="MSFT", claim="c", source="test",
                                 horizon_days=5, external_ref="fixed-key")
    assert len(ledger.predictions(limit=10)) == 1


def test_grading_marks_resolved_and_is_idempotent(spine):
    _, ledger, _ = spine
    pid = ledger.record_prediction(subject="NVDA", claim="c", direction="bull",
                                   horizon_days=5, price_at=10.0, source="test")
    assert ledger.grade(pid, correct=True, actual_return=0.05) is True
    # A second grade must not overwrite the first.
    assert ledger.grade(pid, correct=False) is False
    row = ledger.predictions(limit=5)[0]
    assert row["resolved"] == 1 and row["correct"] == 1


def test_backfill_can_grade_without_announcing(spine):
    """Importing measured history must not fake today's activity (§48)."""
    bus, ledger, _ = spine
    pid = ledger.record_prediction(subject="XOM", claim="c", direction="bull",
                                   horizon_days=1, price_at=5.0, source="test",
                                   announce=False)
    ledger.grade(pid, correct=True, announce=False)
    assert bus.recent(limit=10, kinds=["PREDICTION_RESOLVED"]) == []


def test_hit_rate_carries_its_own_sample_warning(spine):
    """A rate over four calls is not a rate, and must say so."""
    _, ledger, _ = spine
    for i in range(4):
        pid = ledger.record_prediction(subject=f"T{i}", claim="c", direction="bull",
                                       horizon_days=1, price_at=1.0, source="test",
                                       announce=False)
        ledger.grade(pid, correct=(i % 2 == 0), announce=False)
    st = ledger.stats()
    assert st["scored"] == 4
    assert st["hit_rate"] is not None            # computed
    assert st["hit_rate_note"]                   # but flagged as unreliable
    assert "20" in st["hit_rate_note"]


def test_calibration_refuses_below_its_minimum(spine):
    """§32 — a curve through three points is decoration, not evidence."""
    _, ledger, _ = spine
    for i in range(5):
        pid = ledger.record_prediction(subject=f"C{i}", claim="c", direction="bull",
                                       probability=0.7, horizon_days=1,
                                       price_at=1.0, source="test", announce=False)
        ledger.grade(pid, correct=True, announce=False)
    cal = ledger.calibration()
    assert cal["measurable"] is False
    assert cal["n_resolved"] == 5
    assert cal["buckets"] == []
    assert "not reported below" in cal["note"]


def test_calibration_detects_inverted_confidence(spine):
    """The real finding this system produced, pinned as a regression test.

    Stated 80% on calls that resolve 20% of the time must come back as
    NEGATIVE skill — worse than always saying 50%. If this ever reports
    positive skill for an inverted signal, the metric is broken.
    """
    _, ledger, _ = spine
    for i in range(25):
        pid = ledger.record_prediction(subject=f"I{i}", claim="c", direction="bull",
                                       probability=0.8, horizon_days=1,
                                       price_at=1.0, source="test", announce=False)
        ledger.grade(pid, correct=(i < 5), announce=False)   # 20% realised
    cal = ledger.calibration()
    assert cal["measurable"] is True
    assert cal["skill"] < 0
    bucket = next(b for b in cal["buckets"] if b.get("measurable"))
    assert bucket["realised"] < bucket["stated"]


def test_decisions_record_and_close(spine):
    """§11 — approval behaviour is information and has to persist."""
    _, ledger, _ = spine
    ledger.record_decision(kind="approval", subject="TSLA", action="buy 10",
                           external_ref="approval:t1", status="pending",
                           announce=False)
    assert ledger.close_decision("approval:t1", status="rejected",
                                 outcome="too concentrated") is True
    row = ledger.decisions(limit=5)[0]
    assert row["status"] == "rejected"
    assert row["outcome"] == "too concentrated"


def test_resolve_due_leaves_a_priceless_prediction_unscorable(spine):
    """No reference price means the claim cannot be scored — and saying so is
    the correct answer, not guessing one."""
    _, ledger, _ = spine
    past = (datetime.now() - timedelta(days=40)).isoformat(timespec="seconds")
    ledger.record_prediction(subject="NOPRICE", claim="c", direction="bull",
                             horizon_days=1, price_at=None, source="test",
                             created_at=past, announce=False)
    out = ledger.resolve_due()
    assert out["unresolvable"] == 1
    row = ledger.predictions(limit=5)[0]
    assert row["resolved"] == 1 and row["correct"] is None
    assert "cannot be scored" in row["outcome_note"]


# ── the worker registry ─────────────────────────────────────────────────────

def test_worker_states_track_the_clock(spine):
    _, _, workers = spine
    workers.register("w1", "Worker one", loop="fast", interval_s=60)
    st = workers.status()
    assert st["workers"][0]["state"] == "unknown"     # never reported

    workers.tick_ok("w1", {"did": "something"})
    assert workers.status()["workers"][0]["state"] == "ok"


def test_a_failing_worker_is_visible_not_swallowed(spine):
    """The whole point: a loop that catches its own exceptions still shows red."""
    _, _, workers = spine
    workers.register("w2", "Worker two", interval_s=60)
    workers.tick_ok("w2")
    with pytest.raises(ValueError):
        with workers.heartbeat("w2"):
            raise ValueError("vendor timeout")
    row = workers.status()["workers"][0]
    assert row["state"] == "failing"
    assert "vendor timeout" in row["last_error"]
    assert workers.status()["healthy"] is False


def test_disabled_is_not_the_same_as_dead(spine):
    """ARIA_RUN_BRAIN=false is a configuration, not an outage."""
    _, _, workers = spine
    workers.register("brain", "Cognitive brain", interval_s=900)
    workers.disable("brain", "ARIA_RUN_BRAIN=false")
    st = workers.status()
    assert st["workers"][0]["state"] == "disabled"
    assert st["healthy"] is not False     # a switched-off worker is not a fault


def test_heartbeat_records_success_without_an_explicit_call(spine):
    _, _, workers = spine
    workers.register("w3", "Worker three", interval_s=60)
    with workers.heartbeat("w3"):
        pass
    assert workers.status()["workers"][0]["state"] == "ok"


# ── the world model ─────────────────────────────────────────────────────────

def test_stale_source_is_reported_not_carried_forward(tmp_path, monkeypatch):
    """The exact bug this module exists for: macro_data.json said 2026-05-31
    while the deck rendered its VIX under the heading LIVE MARKET STATE."""
    from src.core import world
    monkeypatch.setattr(world, "DATA", tmp_path)
    (tmp_path / "macro_data.json").write_text(
        '{"regime": "Expansion", "vix": 15.3, "macro_score": 10.0,'
        ' "data_date": "2020-01-01"}', encoding="utf-8")
    (tmp_path / "signals.json").write_text("{}", encoding="utf-8")

    block = world._market()
    assert block["macro"]["stale"] is True
    assert block["volatility"]["vix_stale"] is True
    assert "old" in (block["macro"]["why"] or "")


def test_stated_date_beats_file_mtime(tmp_path, monkeypatch):
    """A file rewritten today whose contents are from May is stale data in a
    fresh file — and the contents are what matter."""
    from src.core import world
    monkeypatch.setattr(world, "DATA", tmp_path)
    p = tmp_path / "macro_data.json"
    p.write_text('{"data_date": "2020-01-01"}', encoding="utf-8")   # just written
    block = world._age_block("macro_data.json", "macro", "2020-01-01")
    assert block["stale"] is True


def test_unknowns_names_what_cannot_be_seen(tmp_path, monkeypatch):
    """§20 — a world model that omits its blind spots invites over-trust."""
    from src.core import world
    monkeypatch.setattr(world, "DATA", tmp_path)
    (tmp_path / "macro_data.json").write_text(
        '{"regime": "Expansion", "data_date": "2020-01-01"}', encoding="utf-8")
    (tmp_path / "signals.json").write_text("{}", encoding="utf-8")

    market = world._market()
    portfolio = world._portfolio()
    unknowns = world._unknowns(market, portfolio, {}, {})
    joined = " ".join(unknowns)
    assert "Macro is stale" in joined
    assert "not measurable" in joined      # no positions → no concentration


def test_regime_disagreement_is_surfaced(tmp_path, monkeypatch):
    """When two independent estimates disagree, the disagreement IS the finding."""
    from src.core import world
    monkeypatch.setattr(world, "DATA", tmp_path)
    (tmp_path / "macro_data.json").write_text('{"regime": "Expansion"}', encoding="utf-8")
    (tmp_path / "brain_state.json").write_text('{"regime": "Contraction"}', encoding="utf-8")
    (tmp_path / "signals.json").write_text("{}", encoding="utf-8")

    market = world._market()
    assert market["regime"]["agree"] is False
    unknowns = world._unknowns(market, world._portfolio(), {}, {})
    assert any("disagree" in u for u in unknowns)


def test_digest_only_moves_on_material_change(tmp_path, monkeypatch):
    """If every field entered the digest, 'what changed?' would answer
    'everything', which is the same as answering nothing."""
    from src.core import world
    monkeypatch.setattr(world, "DATA", tmp_path)
    (tmp_path / "signals.json").write_text("{}", encoding="utf-8")

    snap = world.snapshot()
    a = world._digest(snap)
    snap2 = dict(snap)
    snap2["at"] = "2099-01-01T00:00:00"          # only the clock moved
    assert world._digest(snap2) == a


# ── instrument identity (Phase 40) ──────────────────────────────────────────

def test_a_venue_mismatch_is_refused_not_graded(spine, monkeypatch):
    """The live bug: the technical tracker logs BARE tickers, and universe.db
    carries ('BA','Boeing','LSE','GBP'). A recommendation recorded at BAE
    Systems' LSE price (2200p) was graded against Boeing's NYSE close ($215) —
    a -90% 'return' that is not a market move and not the same company.

    Six of 116 resolved predictions were corrupted this way. Withdrawing them
    moved the headline record from 46.6% to 49.1%, because every one was a
    forced loss.
    """
    _, ledger, _ = spine
    from datetime import datetime, timedelta
    made = (datetime.now() - timedelta(days=40)).isoformat(timespec="seconds")
    pid = ledger.record_prediction(subject="BA", claim="bull", direction="bull",
                                   horizon_days=10, price_at=2200.0, source="technical",
                                   created_at=made, announce=False)
    # The price source knows only the US listing.
    monkeypatch.setattr(ledger, "_price_on", lambda t, on: 215.10)
    out = ledger.resolve_due()
    assert out["unresolvable"] == 1
    assert out["resolved"] == 0
    row = ledger.predictions(limit=5)[0]
    assert row["resolved"] == 1 and row["correct"] is None
    assert "different instruments" in row["outcome_note"]
    assert "9.5" in row["outcome_note"] or "10.2" in row["outcome_note"]


def test_a_matching_reference_grades_normally(spine, monkeypatch):
    """The guard must not block ordinary grading."""
    _, ledger, _ = spine
    from datetime import datetime, timedelta
    made = (datetime.now() - timedelta(days=40)).isoformat(timespec="seconds")
    pid = ledger.record_prediction(subject="AAPL", claim="bull", direction="bull",
                                   horizon_days=10, price_at=200.0, source="technical",
                                   created_at=made, announce=False)
    calls = {"n": 0}

    def prices(ticker, on):
        calls["n"] += 1
        return 202.0 if calls["n"] == 1 else 220.0     # reference, then exit
    monkeypatch.setattr(ledger, "_price_on", prices)
    out = ledger.resolve_due()
    assert out["resolved"] == 1
    row = ledger.predictions(limit=5)[0]
    assert row["correct"] == 1


def test_an_unverifiable_reference_still_grades(spine, monkeypatch):
    """Unverifiable is not the same as mismatched. If the check cannot run, the
    prediction is still graded and the note says the check was skipped —
    refusing here would silently stop the whole flywheel on a vendor outage."""
    _, ledger, _ = spine
    ok, why = ledger._reference_matches("AAPL", "2026-01-01", 200.0)
    monkeypatch.setattr(ledger, "_price_on", lambda t, on: None)
    ok, why = ledger._reference_matches("AAPL", "2026-01-01", 200.0)
    assert ok is True
    assert "could not be re-checked" in why


def test_a_genuine_crypto_move_is_not_mistaken_for_a_mismatch(spine, monkeypatch):
    """A magnitude-of-return rule would reject real crypto moves. Testing
    instrument identity instead means a 3x rally grades normally."""
    _, ledger, _ = spine
    from datetime import datetime, timedelta
    made = (datetime.now() - timedelta(days=40)).isoformat(timespec="seconds")
    ledger.record_prediction(subject="SOL-USD", claim="bull", direction="bull",
                             horizon_days=10, price_at=100.0, source="desk",
                             created_at=made, announce=False)
    calls = {"n": 0}

    def prices(ticker, on):
        calls["n"] += 1
        return 100.0 if calls["n"] == 1 else 300.0     # reference matches; +200%
    monkeypatch.setattr(ledger, "_price_on", prices)
    out = ledger.resolve_due()
    assert out["resolved"] == 1
    assert ledger.predictions(limit=5)[0]["actual_return"] == pytest.approx(2.0)


# ── worker failure AND recovery ────────────────────────────────────────────
#
# A health surface that can go red is only half of one. If it cannot go green
# again, the first transient error poisons the dashboard forever and everybody
# learns to ignore it — which is worse than having no dashboard, because it
# looks like monitoring.

def test_a_failed_tick_is_recorded_as_failing(spine):
    _, _, workers = spine
    workers.register("flaky", "Flaky worker", interval_s=60)
    with pytest.raises(RuntimeError):
        with workers.heartbeat("flaky"):
            raise RuntimeError("upstream went away")

    row = {w["name"]: w for w in workers.status()["workers"]}["flaky"]
    assert row["state"] == "failing"
    assert "upstream went away" in (row.get("last_error") or "")


def test_a_worker_recovers_on_its_next_successful_tick(spine):
    """THE property. `failing` is derived from `last_error_at > last_ok`, so a
    success clears it — the state is a comparison, not a latch."""
    _, _, workers = spine
    workers.register("flaky", "Flaky worker", interval_s=60)
    with pytest.raises(RuntimeError):
        with workers.heartbeat("flaky"):
            raise RuntimeError("transient")
    assert {w["name"]: w for w in workers.status()["workers"]}["flaky"]["state"] == "failing"

    with workers.heartbeat("flaky"):
        pass                                  # the next tick succeeds

    row = {w["name"]: w for w in workers.status()["workers"]}["flaky"]
    assert row["state"] == "ok", (
        "a worker that recovered is still reported as failing — the first "
        "transient error would poison the health surface permanently")


def test_the_error_text_is_kept_after_recovery(spine):
    """Recovered is not the same as never broken. The last error stays readable
    so somebody can still ask what happened."""
    _, _, workers = spine
    workers.register("flaky", "Flaky worker", interval_s=60)
    with pytest.raises(ValueError):
        with workers.heartbeat("flaky"):
            raise ValueError("model was not loaded")
    with workers.heartbeat("flaky"):
        pass
    row = {w["name"]: w for w in workers.status()["workers"]}["flaky"]
    assert row["state"] == "ok"
    assert "model was not loaded" in (row.get("last_error") or "")


def test_overall_health_goes_false_on_a_failure_and_true_again_after(spine):
    _, _, workers = spine
    workers.register("w1", "Worker one", interval_s=60)
    with workers.heartbeat("w1"):
        pass
    assert workers.status()["healthy"] is True

    with pytest.raises(RuntimeError):
        with workers.heartbeat("w1"):
            raise RuntimeError("boom")
    assert workers.status()["healthy"] is False

    with workers.heartbeat("w1"):
        pass
    assert workers.status()["healthy"] is True


def test_a_worker_that_never_reports_is_unknown_not_ok(spine):
    """Silence must never read as health. This is the exact failure the module
    exists to catch: a dead prediction loop behind a green dot."""
    _, _, workers = spine
    workers.register("never", "Never runs", interval_s=60)
    row = {w["name"]: w for w in workers.status()["workers"]}["never"]
    assert row["state"] in ("unknown", "stalled")
    assert row["state"] != "ok"
