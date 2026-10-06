"""
tests/test_ledger_integrity.py
==============================
What a prediction's probability MEANS, where its provenance comes from, and
what its return is measured against.

Found in the 2026-10-06 audit, all in the core ledger:

  * `probability` held three different things — V5's P(bull) (the complement
    of P(correct) on every bear call), the desk's conviction mapped onto
    0.5–0.95 (its own comment: "not a probability"), and nothing for technical
    calls — and calibration scored all of it as P(correct). The Brier of 0.288
    the audit reported was that mixture, not a calibration measurement.
  * V5's carried grades were read under the ledger's key names
    (price_end/actual_return) while V5 writes price_then/realised_return, so
    every carried grade lost its price and return.
  * returns were (adjusted close at horizon - raw recorded price) / raw
    recorded price: dividends inside the window vanished from the return.
"""
import json
import math
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

NY = ZoneInfo("America/New_York")


@pytest.fixture
def spine(tmp_path, monkeypatch):
    monkeypatch.setenv("ARIA_CORE_DB", str(tmp_path / "spine.db"))
    from src.core import bus as bus_mod
    monkeypatch.setattr(bus_mod, "_initialised", False)
    monkeypatch.setattr(bus_mod, "_subscribers", [])
    from src.core import ledger as ledger_mod
    monkeypatch.setattr(ledger_mod, "_ready", False)
    return ledger_mod


# ── migration ───────────────────────────────────────────────────────────────

def test_an_old_ledger_gains_the_columns_and_keeps_its_rows(tmp_path, monkeypatch):
    db = tmp_path / "old.db"
    con = sqlite3.connect(db)
    con.execute("""CREATE TABLE predictions (id TEXT PRIMARY KEY, created_at TEXT NOT NULL,
        source TEXT NOT NULL, subject TEXT NOT NULL, claim TEXT NOT NULL, direction TEXT,
        probability REAL, confidence REAL, horizon_days INTEGER, resolve_after TEXT,
        price_at REAL, regime_at TEXT, strategy TEXT, rationale TEXT, evidence TEXT,
        invalidation TEXT, resolved INTEGER NOT NULL DEFAULT 0, resolved_at TEXT,
        price_end REAL, actual_return REAL, correct INTEGER, error REAL,
        outcome_note TEXT, external_ref TEXT UNIQUE)""")
    con.execute("INSERT INTO predictions (id, created_at, source, subject, claim, probability)"
                " VALUES ('p1', '2026-08-01T22:10:00', 'v5', 'AAPL', 'old', 0.41)")
    con.commit(); con.close()

    monkeypatch.setenv("ARIA_CORE_DB", str(db))
    from src.core import bus as bus_mod
    from src.core import ledger
    monkeypatch.setattr(bus_mod, "_initialised", False)
    monkeypatch.setattr(ledger, "_ready", False)
    rows = ledger.predictions(limit=5)
    assert rows[0]["id"] == "p1" and rows[0]["probability"] == 0.41
    for col in ledger._PROVENANCE_COLUMNS:
        assert col in rows[0] and rows[0][col] is None     # unknown, not invented


# ── probability semantics ───────────────────────────────────────────────────

def test_a_documented_probability_is_taken_as_stated(spine):
    pid = spine.record_prediction(subject="AAPL", claim="c", direction="bull",
                                  probability=0.62, source="t", announce=False)
    row = spine.predictions(limit=1)[0]
    assert row["id"] == pid
    assert row["probability_kind"] == "stated" and row["p_correct"] == 0.62


def test_a_mapped_strength_never_becomes_p_correct(spine):
    spine.record_prediction(subject="AAPL", claim="c", direction="bull",
                            probability=0.86, probability_kind="mapped",
                            source="desk", announce=False)
    row = spine.predictions(limit=1)[0]
    assert row["probability_kind"] == "mapped"
    assert row["p_correct"] is None
    assert row["probability"] == 0.86                  # kept as ingested


@pytest.mark.parametrize("bad", [1.5, -0.1, float("nan"), float("inf"), "x"])
def test_a_p_correct_outside_zero_one_is_not_stored(spine, bad):
    spine.record_prediction(subject="AAPL", claim="c", direction="bull",
                            p_correct=bad, source="t", announce=False)
    assert spine.predictions(limit=1)[0]["p_correct"] is None


def test_calibration_scores_only_stated_directional_calls(spine):
    # 25 stated bear calls at P(correct)=0.8 that were right 80% of the time…
    for i in range(25):
        pid = spine.record_prediction(subject=f"B{i}", claim="c", direction="bear",
                                      probability=0.2, p_correct=0.8,
                                      probability_kind="stated", horizon_days=1,
                                      price_at=1.0, source="v5", announce=False)
        spine.grade(pid, correct=(i < 20), announce=False)
    # …plus rows that must be ignored: mapped strengths and neutral calls.
    for i in range(30):
        pid = spine.record_prediction(subject=f"M{i}", claim="c", direction="bull",
                                      probability=0.95, probability_kind="mapped",
                                      horizon_days=1, price_at=1.0, source="desk",
                                      announce=False)
        spine.grade(pid, correct=False, announce=False)
        pid = spine.record_prediction(subject=f"N{i}", claim="c", direction="neutral",
                                      probability=0.5, probability_kind="none",
                                      horizon_days=1, price_at=1.0, source="v5",
                                      announce=False)
        spine.grade(pid, correct=False, announce=False)
    cal = spine.calibration()
    assert cal["n_resolved"] == 25
    # Scored as P(bull)=0.2 the bears would look wildly miscalibrated; as
    # P(correct)=0.8 they are calibrated.
    assert cal["brier"] == pytest.approx(0.16, abs=1e-6)
    assert cal["skill"] > 0


def test_stats_separate_directional_from_neutral(spine):
    for i, (d, ok) in enumerate([("bull", True), ("bull", False),
                                 ("neutral", False), ("neutral", False)]):
        pid = spine.record_prediction(subject=f"S{i}", claim="c", direction=d,
                                      horizon_days=1, price_at=1.0, source="v5",
                                      announce=False)
        spine.grade(pid, correct=ok, announce=False)
    row = next(r for r in spine.stats()["by_source"] if r["source"] == "v5")
    assert row["hit_rate"] == 0.25
    assert row["directional_scored"] == 2
    assert row["hit_rate_directional"] == 0.5


# ── ingest ──────────────────────────────────────────────────────────────────

def test_v5_bear_call_stores_p_correct_not_p_bull():
    from src.core.ingest import v5_stated_probability
    assert v5_stated_probability({"direction": "bear", "p_bull": 0.31,
                                  "confidence": 0.69}) == 0.69
    assert v5_stated_probability({"direction": "neutral", "p_bull": 0.5,
                                  "confidence": 0.5}) is None
    assert v5_stated_probability({"direction": "bull", "confidence": "junk"}) is None


def test_ingest_v5_carries_grades_and_provenance(spine, tmp_path, monkeypatch):
    from src.core import ingest
    v5 = tmp_path / "v5"; v5.mkdir()
    rows = [
        {"id": "a1", "at": "2026-09-23T22:10:42", "ticker": "AAPL", "direction": "bear",
         "p_bull": 0.35, "confidence": 0.65, "horizon_days": 37,
         "resolve_after": "2026-11-13", "price_at": 250.0, "weights_version": 1,
         "information_session": "2026-09-23", "in_session_window": False,
         "model_version": "v5|code=abc|weights=1", "data_as_of": "2026-09-23",
         "regime_at": "Expansion (Goldilocks)", "regime_basis": "test",
         "resolved": True, "correct": True, "price_then": 240.0,
         "realised_return": -0.04},
        {"id": "a2", "at": "2026-09-24T15:00:00", "ticker": "MSFT", "direction": "bull",
         "p_bull": 0.6, "confidence": 0.6, "horizon_days": 37,
         "information_session": "2026-09-23", "in_session_window": True,
         "price_at": 400.0, "resolved": False},
    ]
    (v5 / "predictions.jsonl").write_text("\n".join(json.dumps(r) for r in rows),
                                          encoding="utf-8")
    monkeypatch.setattr(ingest, "DATA", tmp_path)
    ingest.ingest_v5()
    got = {r["subject"]: r for r in spine.predictions(limit=10)}
    a = got["AAPL"]
    assert a["probability"] == 0.35 and a["p_correct"] == 0.65
    assert a["probability_kind"] == "stated"
    assert a["model_version"] == "v5|code=abc|weights=1"
    assert a["information_session"] == "2026-09-23"
    assert a["regime_at"] == "Expansion (Goldilocks)"
    assert a["price_end"] == 240.0 and a["actual_return"] == -0.04
    # an intraday call has no session close to anchor on
    assert got["MSFT"]["information_session"] is None


# ── grading against one series ──────────────────────────────────────────────

def test_a_dividend_inside_the_window_no_longer_flips_the_grade(spine, monkeypatch):
    """Raw price at the call 102; a 2.00 dividend later; today's adjusted
    series reads 100 at the call and 101 at the horizon. The stock returned
    +1% in total. The old formula said 101/102 - 1 = -1%: a bull call graded
    wrong for a move that did not happen."""
    made = "2026-08-12T22:30:00"            # 17:30 New York, settled
    pid = spine.record_prediction(subject="AAPL", claim="bull", direction="bull",
                                  horizon_days=10, price_at=102.0, source="technical",
                                  created_at=made, announce=False)
    row = spine.predictions(limit=1)[0]
    series = {"2026-08-12": 100.0, row["resolve_after"]: 101.0}
    monkeypatch.setattr(spine, "_price_on", lambda t, on: series.get(on))
    monkeypatch.setattr(spine, "_resolved_price_symbol", lambda s: ("AAPL", ""))
    spine.resolve_due(now=datetime(2026, 9, 30))
    row = next(r for r in spine.predictions(limit=5) if r["id"] == pid)
    assert row["correct"] == 1
    assert row["actual_return"] == pytest.approx(0.01)
    assert "adjusted close 2026-08-12" in row["outcome_note"]


def test_a_pre_open_call_is_anchored_on_the_previous_close(spine):
    # 07:40 London on Monday 17 Aug = 02:40 New York: built on Friday's close.
    row = {"information_session": None, "subject": "AAPL",
           "created_at": datetime(2026, 8, 17, 7, 40, tzinfo=ZoneInfo("Europe/London")).isoformat()}
    anchor, why = spine._grading_anchor(row)
    assert anchor == "2026-08-14"


def test_intraday_and_non_us_calls_keep_the_recorded_price(spine):
    mid = {"information_session": None, "subject": "AAPL",
           "created_at": datetime(2026, 8, 12, 11, 0, tzinfo=NY).isoformat()}
    assert spine._grading_anchor(mid)[0] is None
    crypto = {"information_session": None, "subject": "BTC-USD",
              "created_at": "2026-08-12T22:30:00"}
    assert spine._grading_anchor(crypto)[0] is None


def test_v5_grader_uses_the_series_close_of_the_information_session():
    from src.v5.learning import _series_start
    idx = pd.to_datetime(["2026-08-11", "2026-08-12", "2026-08-13"])
    c = pd.Series([99.0, 100.0, 103.0], index=idx)
    e = {"information_session": "2026-08-12", "in_session_window": False,
         "price_at": 102.0, "at": "2026-08-12T22:30:00"}
    assert _series_start(e, c, idx) == (100.0, "series close 2026-08-12")
    e_intraday = {**e, "in_session_window": True}
    assert _series_start(e_intraday, c, idx)[0] == 102.0


# ── model versions ──────────────────────────────────────────────────────────

def test_code_fingerprint_is_deterministic_and_line_ending_blind(tmp_path, monkeypatch):
    from src.core import versioning
    pkg = tmp_path / "pkg"; pkg.mkdir()
    (pkg / "a.py").write_bytes(b"x = 1\r\ny = 2\r\n")
    monkeypatch.setattr(versioning, "ROOT", tmp_path)
    versioning._fingerprint.cache_clear()
    crlf = versioning.code_fingerprint("pkg")
    (pkg / "a.py").write_bytes(b"x = 1\ny = 2\n")
    versioning._fingerprint.cache_clear()
    assert versioning.code_fingerprint("pkg") == crlf
    (pkg / "a.py").write_bytes(b"x = 1\ny = 3\n")
    versioning._fingerprint.cache_clear()
    assert versioning.code_fingerprint("pkg") != crlf
    versioning._fingerprint.cache_clear()


def test_model_version_string_is_ordered():
    from src.core.versioning import model_version
    a = model_version("v5", "src/v5", weights=3, features=2)
    b = model_version("v5", "src/v5", features=2, weights=3)
    assert a == b and a.startswith("v5|code=") and a.endswith("|features=2|weights=3")


# ── regime stamp ────────────────────────────────────────────────────────────

def test_regime_stamp_says_when_its_inputs_are_stale(monkeypatch):
    from src.macro import regime as R
    monkeypatch.setattr(R, "current", lambda: {"regime": "Stagflation", "as_of": "2026-05-31",
                                               "coverage": 0.75, "stale": True})
    st = R.stamp()
    assert st["regime_at"] == "Stagflation"
    assert "STALE" in st["regime_basis"] and "2026-05-31" in st["regime_basis"]


# ── technical tracker: one batch per session ────────────────────────────────

def test_technical_snapshot_is_once_per_session_and_never_mid_session(tmp_path, monkeypatch):
    from src.data import technical_tracker as tr
    monkeypatch.setattr(tr, "LOG_FILE", tmp_path / "recs.jsonl")
    after = datetime(2026, 8, 12, 18, 0, tzinfo=NY)
    assert tr.snapshot_due(after)["due"] is True
    assert tr.snapshot_due(datetime(2026, 8, 12, 11, 0, tzinfo=NY))["due"] is False
    assert tr.snapshot_due(datetime(2026, 8, 12, 16, 30, tzinfo=NY))["due"] is False
    tr._append({"at": "2026-08-12T23:00:00+01:00", "date": "2026-08-12", "symbol": "X",
                "timeframe": "1d", "summary": "Buy", "entry_price": 1.0})
    assert tr.snapshot_due(after)["due"] is False          # legacy entry maps to 08-12
    sat = datetime(2026, 8, 15, 12, 0, tzinfo=NY)
    assert tr.snapshot_due(sat)["session"] == "2026-08-14"
