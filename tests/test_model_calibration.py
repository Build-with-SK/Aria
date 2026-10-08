"""
tests/test_model_calibration.py
===============================
Calibration is never fitted on too little data, never judged on the data it
was fitted on, and never applied by this code.
"""
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from intel_helpers import business_days, row  # noqa: E402
from src.intelligence import calibration as C  # noqa: E402
from src.intelligence import dataset as D  # noqa: E402


def _prob_rows(n, stated, truth, seed=7, horizon=1):
    rnd = random.Random(seed)
    days = business_days("2025-01-02", n)
    out = []
    for i, d in enumerate(days):
        p = stated(i)
        ok = rnd.random() < truth(p)
        out.append(row(i, source="v5", kind="stated", p_correct=p, horizon=horizon,
                       created=f"{d}T22:00:00", correct=int(ok), information_session=d,
                       actual_return=0.01 if ok else -0.01, subject="AAPL"))
    return D.build(out).rows


def test_a_model_that_states_no_probability_is_not_applicable():
    rows = [row(1)]
    s = C.status(D.build(rows).rows, model_id="technical", declared_probability=False)
    assert s["status"] == C.NOT_APPLICABLE and s["calibration_version"] == "none"


def test_sixteen_probabilities_are_insufficient_and_say_how_many_are_needed():
    rows = _prob_rows(16, lambda i: 0.52, lambda p: 0.5)
    s = C.status(rows, model_id="v5")
    assert s["status"] == C.INSUFFICIENT
    assert s["n_probability_observations"] == 16
    assert s["candidate_methods"] == []
    assert "100 / 50 / 50" in s["why"]


def test_method_choice_depends_on_the_training_window_only():
    assert C.methods_for(99) == []
    assert C.methods_for(100) == ["platt"]
    assert C.methods_for(999) == ["platt"]
    assert C.methods_for(1000) == ["platt", "isotonic"]


def test_the_windows_are_chronological_and_purged():
    rows = _prob_rows(400, lambda i: 0.6, lambda p: 0.6, horizon=10)
    train, val, test, purged = C._split(rows)
    assert max(r["created_at"] for r in train) < min(r["created_at"] for r in val)
    assert max(r["created_at"] for r in val) < min(r["created_at"] for r in test)
    assert purged > 0
    assert all(r["resolve_after"] < val[0]["created_at"][:10] for r in train)


def test_a_well_calibrated_source_keeps_its_raw_probabilities():
    rows = _prob_rows(600, lambda i: 0.55 + 0.3 * ((i * 37) % 100) / 100, lambda p: p)
    s = C.status(rows, model_id="v5")
    assert s["status"] in (C.RAW_RETAINED,) and s["calibration_version"] == "none"


def test_a_badly_overconfident_source_yields_a_candidate_that_is_not_applied():
    """States 0.55-0.95 while being right half the time."""
    rows = _prob_rows(800, lambda i: 0.55 + 0.4 * ((i * 37) % 100) / 100, lambda p: 0.5,
                      seed=11)
    s = C.status(rows, model_id="v5")
    assert s["status"] == C.CANDIDATE
    assert s["chosen_on_validation"] == "platt"
    assert s["test_brier_gain"] > 1.96 * s["test_brier_gain_se"]
    assert s["calibration_version"] == "none"          # NOT applied
    assert "owner decision" in s["why"]


def test_recording_a_calibration_trial(tmp_path, monkeypatch):
    from src.intelligence import walkforward
    monkeypatch.setattr(walkforward, "TRIALS_FILE", tmp_path / "trials.jsonl")
    rows = _prob_rows(800, lambda i: 0.55 + 0.4 * ((i * 37) % 100) / 100, lambda p: 0.5,
                      seed=11)
    C.status(rows, model_id="v5", record=True)
    assert walkforward.trial_count("v5", "calibration_selection") == 1
