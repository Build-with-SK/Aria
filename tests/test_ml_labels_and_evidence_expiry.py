"""
tests/test_ml_labels_and_evidence_expiry.py
===========================================
Two defects found while auditing the ML path on 2026-10-08:

  * training labels: the last `horizon` rows have no future close, but were
    labelled 0 ("not up") because `NaN > x` is False — and the trainer's
    `y.notna()` filter, written for exactly those rows, kept them. Every model
    was trained to say "not up" on the most recent conditions it had seen.
  * expired evidence: data/ml_predictions.json dated from 2026-05-31 and every
    desk debate still cited its 1/5/20-day calls as current. A call whose
    horizon has elapsed is not evidence about today.

Plus: ML calls are now recorded in the ledger as SCORES, forward only.
"""
import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.models import targets as TG  # noqa: E402


def closes(n=80):
    return pd.Series(100 + np.cumsum(np.random.default_rng(0).standard_normal(n)),
                     index=pd.bdate_range("2026-01-05", periods=n))


@pytest.mark.parametrize("h", [1, 5, 20])
def test_the_unknown_future_is_nan_not_down(h):
    c = closes()
    for lab in (TG.direction_label(c, h), TG.binary_direction(c, h)):
        assert lab.iloc[-h:].isna().all()
        assert lab.iloc[:-h].notna().all()


def test_triple_barrier_leaves_unfinished_windows_unknown():
    c = closes()
    lab = TG.triple_barrier_label(c, c.diff().abs().rolling(5).mean(), 5)
    assert lab.iloc[-5:].isna().all() and lab.iloc[:-5].notna().all()


def test_build_targets_lets_the_trainer_drop_unknown_rows():
    df = pd.DataFrame({"Close": closes()})
    out = TG.build_targets(df, horizons=[5], use_risk_adjusted=False)
    y = out["target_dir_5d"]
    assert y.notna().sum() == len(df) - 5            # what model_trainer keeps


def test_known_labels_do_not_change():
    c = closes()
    fwd = c.pct_change(5).shift(-5)
    lab = TG.direction_label(c, 5)
    known = fwd.notna()
    assert ((lab[known] == 1) == (fwd[known] > 0.005)).all()
    assert ((lab[known] == -1) == (fwd[known] < -0.005)).all()


def test_ml_model_prepare_features_drops_the_tail():
    from src.models import ml_model as M
    n = 120
    rng = np.random.default_rng(1)
    df = pd.DataFrame({c: rng.standard_normal(n) for c in M.ML_FEATURE_COLS},
                      index=pd.bdate_range("2026-01-05", periods=n))
    df["Close"] = 100 + np.cumsum(rng.standard_normal(n))
    X, y = M._prepare_features(df, horizon=5)
    assert len(y) == n - 5 and y.index[-1] == df.index[-6]
    assert set(y.unique()) <= {0, 1}


# ── expired ML evidence is not cited ───────────────────────────────────────

def test_an_elapsed_horizon_is_not_live():
    from src.desk.analysts import technical_agent as TA
    made = datetime(2026, 5, 31, 1, 18)
    assert TA.ml_horizon_live(20, made, now=made + timedelta(days=27)) is True
    assert TA.ml_horizon_live(20, made, now=made + timedelta(days=29)) is False
    assert TA.ml_horizon_live(1, made, now=datetime(2026, 10, 8)) is False
    assert TA.ml_horizon_live(5, None) is False


def _write_inputs(data_dir: Path, ml_age_days: float):
    (data_dir / "signals.json").write_text(json.dumps({"AAPL": {
        "composite_score": 25.0, "bullish_prob": 0.6, "trend_score": 70, "momentum_score": 65,
        "realised_vol": 0.2, "current_price": 150.0, "action": "Buy", "regime": "Bull",
        "confidence": "Medium"}}), encoding="utf-8")
    ml = data_dir / "ml_predictions.json"
    ml.write_text(json.dumps({"AAPL": {"horizons": {
        "1": {"horizon_days": 1, "bullish_prob": 0.3, "direction": "Bearish", "model_agreement": 0.7},
        "20": {"horizon_days": 20, "bullish_prob": 0.2, "direction": "Bearish",
               "model_agreement": 0.5}}}}), encoding="utf-8")
    t = time.time() - ml_age_days * 86400
    os.utime(ml, (t, t))


def test_the_technical_analyst_stops_citing_a_may_ml_file(tmp_path, monkeypatch):
    from src.desk import opinion
    from src.desk.analysts import technical_agent as TA
    monkeypatch.setattr(opinion, "DATA", tmp_path)
    monkeypatch.setattr(TA, "DATA", tmp_path)
    _write_inputs(tmp_path, ml_age_days=130)
    op = TA.opine("AAPL")
    assert not any("ML ensemble" in e["claim"] for e in op.evidence)


def test_a_fresh_ml_file_is_cited_only_for_unexpired_horizons(tmp_path, monkeypatch):
    from src.desk import opinion
    from src.desk.analysts import technical_agent as TA
    monkeypatch.setattr(opinion, "DATA", tmp_path)
    monkeypatch.setattr(TA, "DATA", tmp_path)
    _write_inputs(tmp_path, ml_age_days=3)
    claims = [e["claim"] for e in TA.opine("AAPL").evidence if "ML ensemble" in e["claim"]]
    assert claims == ["ML ensemble 20d horizon: Bearish (20% bullish, agreement 50%)"]


# ── ML calls reach the ledger as scores ────────────────────────────────────

@pytest.fixture
def spine(tmp_path, monkeypatch):
    monkeypatch.setenv("ARIA_CORE_DB", str(tmp_path / "spine.db"))
    from src.core import bus as bus_mod
    monkeypatch.setattr(bus_mod, "_initialised", False)
    monkeypatch.setattr(bus_mod, "_subscribers", [])
    from src.core import ledger as ledger_mod
    monkeypatch.setattr(ledger_mod, "_ready", False)
    return ledger_mod


def test_ml_calls_are_recorded_as_scores_inside_the_models_own_dead_zone(spine):
    from src.intelligence.recorders import record_ml_predictions
    payload = {"AAPL": {"horizons": {
        "1": {"horizon_days": 1, "bullish_prob": 0.70},
        "5": {"horizon_days": 5, "bullish_prob": 0.50},          # dead zone: no call
        "20": {"horizon_days": 20, "bullish_prob": 0.30}}}}
    out = record_ml_predictions(payload, data_as_of={"AAPL": "2026-10-07"},
                                last_close={"AAPL": 150.0}, trainer_version="v6.0",
                                created_at="2026-10-08T07:00:00")
    assert out == {"recorded": 2, "no_call_dead_zone": 1}
    rows = {r["horizon_days"]: r for r in spine.predictions(limit=10, source="ml-ensemble")}
    assert set(rows) == {1, 20}
    assert rows[1]["direction"] == "bull" and rows[20]["direction"] == "bear"
    for r in rows.values():
        assert (r["probability_type"], r["p_correct"]) == ("SCORE", None)
        assert r["information_session"] == "2026-10-07" and r["data_as_of"] == "2026-10-07"
        assert r["model_hash"] and r["calibration_version"] == "none"
    # Re-running the same pipeline output is one claim, not two.
    record_ml_predictions(payload, data_as_of={"AAPL": "2026-10-07"},
                          created_at="2026-10-08T09:00:00")
    assert len(spine.predictions(limit=10, source="ml-ensemble")) == 2
