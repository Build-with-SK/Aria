"""
tests/test_walkforward_multiple_testing.py
==========================================
Time order is never broken, overlapping labels are purged, every trial is
counted, and selection among many is charged for the search.
"""
import math
import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from intel_helpers import business_days, row  # noqa: E402
from src.intelligence import multiple_testing as MT  # noqa: E402
from src.intelligence import walkforward as WF  # noqa: E402


def _rows(n=300, horizon=10, shuffle=True):
    days = business_days("2025-01-02", n)
    rows = [row(i, created=f"{d}T22:00:00", horizon=horizon) for i, d in enumerate(days)]
    if shuffle:
        random.Random(1).shuffle(rows)
    return rows


def test_windows_are_chronological_whatever_the_input_order():
    for w, train, val, test in WF.chronological_splits(_rows(), n_folds=3):
        assert max(r["created_at"] for r in train) < min(r["created_at"] for r in val)
        assert max(r["created_at"] for r in val) < min(r["created_at"] for r in test)
        assert w.n_train == len(train) and w.n_test == len(test)


def test_open_labels_are_purged_before_each_later_window():
    splits = WF.chronological_splits(_rows(horizon=20), n_folds=2)
    assert all(w.purged > 0 for w, *_ in splits)
    for w, train, val, test in splits:
        assert all(r["resolve_after"] < val[0]["created_at"][:10] for r in train)
        assert all(r["resolve_after"] < test[0]["created_at"][:10] for r in val)


def test_test_blocks_are_consecutive_and_cover_the_tail():
    rows = _rows(shuffle=False)
    splits = WF.chronological_splits(rows, n_folds=3, initial_fraction=0.5)
    tested = [r["id"] for *_, test in splits for r in test]
    assert tested == [r["id"] for r in rows[150:]]


def test_holdout_is_the_most_recent_block_with_open_labels_dropped():
    earlier, later = WF.holdout_split(_rows(horizon=15), holdout_fraction=0.3)
    assert len(later) == 90
    assert max(r["created_at"] for r in earlier) < min(r["created_at"] for r in later)
    assert all(r["resolve_after"] < later[0]["created_at"][:10] for r in earlier)


def test_too_few_rows_gives_no_windows():
    assert WF.chronological_splits(_rows(n=3), n_folds=5) == []


def test_every_trial_is_recorded_and_counted(tmp_path, monkeypatch):
    monkeypatch.setattr(WF, "TRIALS_FILE", tmp_path / "trials.jsonl")
    w = WF.chronological_splits(_rows(), n_folds=2)[0][0]
    for k in range(3):
        WF.record_trial(model_id="m", purpose="search", hyperparameters={"k": k},
                        windows=[w], n=100, metric="brier", value=0.2 + k / 100)
    WF.record_trial(model_id="other", purpose="search")
    assert WF.trial_count("m", "search") == 3
    assert WF.trial_count() == 4
    t = WF.trials("m")[0]
    assert t["hyperparameters"] == {"k": 0} and t["windows"][0]["fold"] == 0


# ── multiple testing ────────────────────────────────────────────────────────

def test_holm_known_answer():
    adj = MT.holm({"a": 0.01, "b": 0.04, "c": 0.03})
    assert adj == {"a": 0.03, "c": 0.06, "b": 0.06}
    assert MT.significant({"a": 0.01, "b": 0.04, "c": 0.03}) == {"a": True, "b": False,
                                                                 "c": False}


def test_bh_known_answer():
    q = MT.bh({"a": 0.01, "b": 0.04, "c": 0.03})
    assert q == {"a": 0.03, "b": 0.04, "c": 0.04}


def test_one_lucky_result_among_twenty_does_not_survive():
    p = {f"t{i}": 0.5 for i in range(19)}
    p["lucky"] = 0.03
    assert MT.significant(p)["lucky"] is False


def test_the_deflated_sharpe_correction_is_preserved():
    """The 2026-10-06 unit fix is untouched by this package: annualised SR 1.0
    over 252 daily returns, one trial -> PSR ~0.84, not ~1.0."""
    from src.evolution.statistics import deflated_sharpe
    out = deflated_sharpe(1.0, [0.0], n_observations=252, n_trials=1)
    assert 0.83 < out["probability"] < 0.85


def test_more_trials_raise_the_bar():
    from src.evolution.statistics import expected_max_sharpe
    spread = [0.2 * ((i % 7) - 3) for i in range(200)]
    assert expected_max_sharpe(spread, n_trials=1500) > expected_max_sharpe(spread, n_trials=20) > 0
