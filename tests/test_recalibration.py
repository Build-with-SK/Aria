"""
tests/test_recalibration.py
===========================
Recalibration is judged out of sample, or not at all (src/core/recalibration).
Synthetic histories with known truth: an overconfident model that SHOULD be
recalibrated, a calibrated one that should not, and a sample too small to say.
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core import recalibration as R


def _history(n, truth, seed=0):
    rng = np.random.default_rng(seed)
    t0 = datetime(2026, 1, 1)
    rows = []
    for i in range(n):
        p = float(rng.uniform(0.5, 0.95))
        rows.append({"created_at": (t0 + timedelta(hours=i)).isoformat(),
                     "p_correct": p, "correct": bool(rng.random() < truth(p))})
    return rows


def test_small_samples_keep_raw_and_say_why():
    out = R.evaluate(_history(60, lambda p: p))
    assert out["recommendation"] == "keep raw"
    assert "insufficient data" in out["why"]


def test_an_overconfident_model_is_recalibrated_out_of_sample():
    """Stated 90% that come true 70% of the time: shrink towards 0.5."""
    out = R.evaluate(_history(2000, lambda p: 0.5 + (p - 0.5) * 0.4, seed=1))
    assert out["recommendation"] in ("isotonic", "platt")
    best = out["held_out"][out["recommendation"]]
    assert best["brier"] < out["held_out"]["raw"]["brier"]
    assert best["significant"] is True


def test_a_calibrated_model_is_left_alone():
    out = R.evaluate(_history(2000, lambda p: p, seed=2))
    assert out["recommendation"] == "keep raw"


def test_the_split_is_chronological():
    rows = _history(400, lambda p: p, seed=3)
    out = R.evaluate(rows)
    assert out["fit_period"][1] < out["eval_period"][0]


def test_a_monotone_map_cannot_buy_discrimination():
    """AUC is a ranking measure; a monotone recalibration preserves ranks, so
    a held-out AUC that 'improves' would mean the map is not monotone."""
    out = R.evaluate(_history(2000, lambda p: 0.5 + (p - 0.5) * 0.4, seed=4))
    raw_auc = out["held_out"]["raw"]["auc"]
    platt_auc = out["held_out"]["platt"]["auc"]
    assert abs(platt_auc - raw_auc) < 1e-9
