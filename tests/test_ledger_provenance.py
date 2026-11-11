"""
tests/test_ledger_provenance.py
===============================
Every new prediction carries what its evaluation needs, unknown fields stay
unknown, and the Phase 1 baseline counts what is there.
"""
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from intel_helpers import row  # noqa: E402
from src.intelligence import baseline, provenance  # noqa: E402


@pytest.fixture
def spine(tmp_path, monkeypatch):
    monkeypatch.setenv("ARIA_CORE_DB", str(tmp_path / "spine.db"))
    from src.core import bus as bus_mod
    monkeypatch.setattr(bus_mod, "_initialised", False)
    monkeypatch.setattr(bus_mod, "_subscribers", [])
    from src.core import ledger as ledger_mod
    monkeypatch.setattr(ledger_mod, "_ready", False)
    return ledger_mod


NEW = ("probability_type", "venue", "market_session", "feature_version",
       "routing_decision", "calibration_version", "reference_basis", "model_hash")


def test_a_fresh_ledger_has_every_provenance_column(spine):
    with spine.connect() as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(predictions)")}
    assert set(NEW) <= cols


def test_an_old_ledger_gains_them_and_keeps_its_rows(tmp_path, monkeypatch):
    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    # The 2026-10-06 schema: identity and the first provenance columns, none
    # of the 2026-10-08 ones.
    conn.execute("CREATE TABLE predictions (id TEXT PRIMARY KEY, created_at TEXT NOT NULL, "
                 "source TEXT NOT NULL, subject TEXT NOT NULL, claim TEXT NOT NULL, "
                 "direction TEXT, probability REAL, confidence REAL, horizon_days INTEGER, "
                 "resolve_after TEXT, price_at REAL, regime_at TEXT, strategy TEXT, "
                 "rationale TEXT, evidence TEXT, invalidation TEXT, "
                 "resolved INTEGER NOT NULL DEFAULT 0, resolved_at TEXT, price_end REAL, "
                 "actual_return REAL, correct INTEGER, error REAL, outcome_note TEXT, "
                 "external_ref TEXT UNIQUE, provider_symbol TEXT, identity_basis TEXT, "
                 "probability_kind TEXT, p_correct REAL, model_version TEXT, data_as_of TEXT, "
                 "information_session TEXT, regime_basis TEXT)")
    conn.execute("INSERT INTO predictions (id, created_at, source, subject, claim) "
                 "VALUES ('x', '2026-01-01', 'v5', 'AAPL', 'old')")
    conn.commit()
    conn.close()
    monkeypatch.setenv("ARIA_CORE_DB", str(db))
    from src.core import bus as bus_mod
    monkeypatch.setattr(bus_mod, "_initialised", False)
    from src.core import ledger
    monkeypatch.setattr(ledger, "_ready", False)
    with ledger.connect() as c:
        cols = {r[1] for r in c.execute("PRAGMA table_info(predictions)")}
        assert c.execute("SELECT claim FROM predictions").fetchone()[0] == "old"
    assert set(NEW) <= cols


def test_record_stamps_type_hash_venue_and_session(spine):
    pid = spine.record_prediction(
        subject="AAPL", claim="t", direction="bull", source="v5", p_correct=0.6,
        probability_kind="stated", model_version="v5|code=0123456789ab|weights=1",
        created_at="2026-08-04T22:30:00", horizon_days=45, price_at=100.0,
        routing_decision="DIRECT", calibration_version="none", announce=False)
    with spine.connect() as conn:
        r = dict(conn.execute("SELECT * FROM predictions WHERE id=?", (pid,)).fetchone())
    assert r["probability_type"] == "STATED_PROBABILITY"
    assert r["model_hash"] == "0123456789ab"
    assert r["venue"] == "XNYS"
    assert r["market_session"]                      # the engine's state, recorded as given
    assert r["reference_basis"] is None and r["feature_version"] is None   # not invented


def test_an_unknown_reference_basis_is_refused(spine):
    assert spine.record_prediction(subject="AAPL", claim="t", source="t",
                                   probability_kind="none", reference_basis="whenever",
                                   announce=False) is None


def test_a_known_reference_returns_the_same_id_without_rewriting(spine):
    kw = dict(subject="AAPL", claim="t", source="t", probability_kind="none",
              external_ref="same", announce=False)
    a = spine.record_prediction(**kw)
    b = spine.record_prediction(**{**kw, "claim": "changed"})
    assert a == b
    assert spine.predictions(limit=5)[0]["claim"] == "t"


def test_completeness_lists_what_is_missing_and_fills_nothing():
    r = row(1)
    rep = provenance.completeness(r)
    assert rep["complete"] is False
    assert {"venue", "market_session", "feature_version", "routing_decision",
            "calibration_version"} <= set(rep["missing"])
    assert "probability" not in rep["missing"]          # NONE states no number: complete
    full = dict(r, venue="XNYS", market_session="CLOSED", feature_version="f",
                routing_decision="DIRECT", calibration_version="none")
    assert provenance.completeness(full) == {"complete": True, "missing": []}
    assert "probability_type" in provenance.completeness(dict(full, probability_kind=None))["missing"]


def test_the_baseline_audit_counts_without_inferring():
    rows = [row(1), row(2, resolved=False),
            row(3, source="v5", kind="stated", p_correct=0.6, horizon=45),
            row(4, source="v5", kind=None, p_correct=None, horizon=45, subject="MSFT"),
            row(5, source="desk", kind="mapped", horizon=1, subject="BTC-USD")]
    a = baseline.audit(rows)
    o = a["overall"]
    assert o["total"] == 5 and o["graded"] == 4 and o["ungraded"] == 1
    assert o["valid_probabilities"] == 1
    assert o["probability_types"] == {"NONE": 2, "STATED_PROBABILITY": 1, "UNKNOWN": 1,
                                      "CONFIDENCE": 1}
    assert o["by_asset_class"] == {"equity": 4, "crypto": 1}
    assert a["by_model"]["v5"]["field_presence"]["probability_kind"] == 1
    assert o["provenance_complete"] == 0
    assert o["field_presence"]["venue"] == 0


def test_ingest_declares_only_provable_provenance(spine, tmp_path, monkeypatch):
    import json
    from src.core import ingest
    v5 = tmp_path / "v5"
    v5.mkdir()
    (v5 / "predictions.jsonl").write_text(json.dumps({
        "id": "v5-aa", "ticker": "AAPL", "at": "2026-08-04T22:30:00", "direction": "bull",
        "p_bull": 0.56, "confidence": 0.56, "horizon_days": 45, "price_at": 100.0,
        "model_version": "v5|code=0123456789ab|weights=1", "weights_version": 1}) + "\n",
        encoding="utf-8")
    monkeypatch.setattr(ingest, "DATA", tmp_path)
    ingest.ingest_v5()
    r = spine.predictions(limit=1, source="v5")[0]
    assert r["routing_decision"] == ingest.DIRECT_ROUTE
    assert r["calibration_version"] == "none"
    assert r["feature_version"] == "src/v5@0123456789ab"
