"""
tests/test_model_evaluation.py
==============================
Per-model metrics are reported only where the sample supports them, with the
dependence between calls made from the same day's data accounted for, and
compared with always-long rather than only with a coin.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from intel_helpers import clustered_record, row, ticker  # noqa: E402
from src.intelligence import dataset as D  # noqa: E402
from src.intelligence import evaluation as E  # noqa: E402


def _graded(rows):
    return D.build(rows).rows


def test_below_thirty_calls_there_is_no_rate_to_quote():
    rows = _graded(clustered_record(4, 7, lambda k: 0.6))          # 28 calls
    b = E.rate_block(rows)
    assert b["status"] == "INSUFFICIENT_DATA" and "rate" not in b


def test_many_calls_on_few_dates_are_not_enough():
    rows = _graded(clustered_record(3, 20, lambda k: 0.6))          # 60 calls, 3 dates
    b = E.rate_block(rows)
    assert b["status"] == "INSUFFICIENT_DATA" and b["n_clusters"] == 3


def test_clustered_outcomes_widen_the_interval():
    """Whole dates right or wrong together: the cluster interval must be much
    wider than the one that pretends every call is independent."""
    rows = _graded(clustered_record(10, 10, lambda k: 1.0 if k % 2 else 0.0))
    b = E.rate_block(rows)
    assert b["status"] == "MEASURED" and b["rate"] == 0.5
    wl, wh = b["ci95_wilson"]
    cl, ch = b["ci95_cluster"]
    assert (ch - cl) > 2 * (wh - wl)
    assert b["verdict"] == "indistinguishable from chance"


def test_a_genuinely_strong_record_is_measured_as_such():
    rows = _graded(clustered_record(30, 10, lambda k: 0.8))
    b = E.rate_block(rows)
    assert b["rate"] == 0.8 and b["verdict"] == "better than chance"
    assert b["cluster_df"] == 29


def test_always_long_is_the_baseline_not_only_a_coin():
    """All-bull calls in a rising market beat a coin and carry no edge."""
    rows = _graded(clustered_record(30, 10, lambda k: 0.7))
    v = E.vs_always_long(rows)
    assert v["model_right_long_wrong"] == 0 and v["model_wrong_long_right"] == 0
    assert v["status"] == "INSUFFICIENT_DATA" and v["n"] == 300
    assert v["always_long_hit_rate"] == 0.7


def test_bear_calls_that_beat_always_long_are_detected():
    rows = _graded(clustered_record(
        30, 6, lambda k: 1.0, direction_fn=lambda k, j: "bear" if j % 2 else "bull"))
    v = E.vs_always_long(rows)
    assert v["status"] == "MEASURED" and v["verdict"] == "beats always-long"


def test_probability_metrics_need_stated_probabilities():
    rows = _graded(clustered_record(30, 10, lambda k: 0.7))          # kind none
    assert E.probability_block(rows)["status"] == "INSUFFICIENT_DATA"


def test_probability_metrics_on_stated_probabilities():
    rows = _graded(clustered_record(30, 10, lambda k: 0.7, source="v5", kind="stated",
                                    p_fn=lambda k, j, ok: 0.7, horizon=45))
    p = E.probability_block(rows)
    assert p["status"] == "MEASURED" and p["n"] == 300
    assert p["brier"] == pytest.approx(0.21, abs=1e-6)
    assert p["mean_stated_probability"] == 0.7 and p["realised_frequency"] == 0.7
    assert p["calibration_error"]["status"] == "MEASURED"
    assert p["calibration_error"]["ece"] == pytest.approx(0.0, abs=1e-9)


def test_one_regime_label_is_not_a_regime_comparison():
    rows = _graded(clustered_record(30, 10, lambda k: 0.6))
    r = E.regime_slices(rows, "regime_macro")
    assert r["status"] == "INSUFFICIENT_DATA" and "needs at least two" in r["why"]


def test_two_measurable_regimes_are_compared():
    rows = _graded(clustered_record(40, 10, lambda k: 0.6))
    for i, r in enumerate(rows):
        r["regime_market"] = "BULL/LOW_VOL" if i % 2 else "BULL/NORMAL_VOL"
    out = E.regime_slices(rows, "regime_market")
    assert out["status"] == "MEASURED" and set(out["slices"]) == {"BULL/LOW_VOL",
                                                                  "BULL/NORMAL_VOL"}


def test_a_lucky_slice_does_not_survive_the_slice_correction():
    """Ten asset-class slices of a coin-flip record: whatever clears p<0.05 raw
    must be re-read after Holm."""
    rows = []
    for c in range(10):
        rows += clustered_record(8, 5, lambda k, c=c: 0.75 if c == 3 and k % 2 else 0.5,
                                 subject_fn=lambda k, j, c=c: f"{ticker(c * 50 + j)}")
    ds = D.build(rows)
    for r in ds.rows:
        r["asset_class"] = f"class{int(r['symbol'][1:2] < 'B')}{r['symbol'][2]}"
    ev = E.evaluate_model(ds.rows)
    assert ev["slice_tests"] >= 1
    for k, b in ev["by_asset_class"].items():
        if b.get("status") == "MEASURED" and b["holm_p"] >= 0.05:
            assert "after the slice correction" in b["verdict"] or \
                   b["verdict"] == "indistinguishable from chance"


def test_evaluate_model_reports_everything_with_its_status():
    rows = _graded(clustered_record(30, 10, lambda k: 0.6))
    ev = E.evaluate_model(rows)
    for key in ("hit_rate_all", "directional_accuracy", "neutral_band_hit_rate",
                "vs_always_long", "probability", "by_horizon", "by_asset_class",
                "by_model_version", "by_regime_macro", "by_regime_market"):
        assert key in ev
    assert ev["sample"]["graded_events"] == 300
    assert ev["neutral_band_hit_rate"]["status"] == "INSUFFICIENT_DATA"
    assert E.headline(ev).startswith("60.0% directional")


def test_cluster_se_known_answer():
    # Two clusters, all-right and all-wrong: p = 0.5, every residual 0.5.
    se = E.cluster_se([1, 1, 0, 0], ["a", "a", "b", "b"])
    assert se == pytest.approx(((2 / 1) * (1.0 + 1.0) / 16) ** 0.5)
    assert E.cluster_se([1], ["a"]) is None
