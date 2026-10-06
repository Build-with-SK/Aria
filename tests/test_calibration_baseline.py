"""
tests/test_calibration_baseline.py
==================================
"Calibration improved" may only be said out of sample, with enough data, and
outside sampling noise — src/core/baseline.py. Synthetic rows with known
answers, plus a check that the committed baseline is well-formed.
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from src.core import baseline as B


def _rows(n, hits, *, source="v5", p=None, direction="bull", start=0, day="2026-08"):
    out = []
    for i in range(n):
        out.append({"source": source, "direction": direction, "resolved": 1,
                    "correct": int(i < hits),
                    "created_at": f"{day}-{(start + i) % 28 + 1:02d}T22:10:{i % 60:02d}",
                    "probability_kind": "stated" if p is not None else "none",
                    "p_correct": p, "regime_at": "Expansion (Goldilocks)"})
    return out


def test_directional_and_neutral_are_reported_apart():
    rows = _rows(10, 6) + _rows(10, 1, direction="neutral")
    m = B.measure(rows)
    v = m["by_source"]["v5"]
    assert v["directional"]["rate"] == 0.6 and v["directional"]["n"] == 10
    assert v["neutral"]["rate"] == 0.1


def test_brier_reads_only_stated_probabilities():
    stated = _rows(20, 16, p=0.8)
    mapped = [dict(r, probability_kind="mapped", p_correct=None, probability=0.95)
              for r in _rows(20, 0, source="v5")]
    m = B.measure(stated + mapped)
    sp = m["by_source"]["v5"]["stated_probability"]
    assert sp["n"] == 20
    assert sp["brier"] == pytest.approx(0.16, abs=1e-9)
    assert sp["ece"] == pytest.approx(0.0, abs=1e-9)


def test_a_comparison_on_in_sample_rows_is_refused():
    base = B.measure(_rows(40, 20, p=0.6))
    cur = B.measure(_rows(40, 30, p=0.6))               # no created_after
    assert B.compare(base, cur)["verdict"] == "refused"


def test_small_samples_are_insufficient_whatever_the_point_estimate():
    base = B.measure(_rows(40, 20, p=0.6, day="2026-08"))
    cur = B.measure(_rows(10, 10, p=0.6, day="2026-09"), created_after=base["cutoff"])
    v = B.compare(base, cur)["by_source"]["v5"]
    assert v["hit_rate"] == "insufficient data"


def test_noise_is_not_improvement():
    base = B.measure(_rows(60, 30, p=0.6, day="2026-08"))
    cur = B.measure(_rows(60, 34, p=0.6, day="2026-09"), created_after=base["cutoff"])
    assert B.compare(base, cur)["by_source"]["v5"]["hit_rate"] == "no detectable change"


def test_a_real_difference_is_detected_both_ways():
    base = B.measure(_rows(200, 100, p=0.6, day="2026-08"))
    better = B.measure(_rows(200, 140, p=0.6, day="2026-09"), created_after=base["cutoff"])
    worse = B.measure(_rows(200, 60, p=0.6, day="2026-09"), created_after=base["cutoff"])
    assert B.compare(base, better)["by_source"]["v5"]["hit_rate"] == "improved"
    assert B.compare(base, worse)["by_source"]["v5"]["hit_rate"] == "worse"


def test_rows_before_the_cutoff_do_not_count_as_current():
    base = B.measure(_rows(40, 20, day="2026-08"))
    cur = B.measure(_rows(40, 20, day="2026-08") + _rows(5, 5, day="2026-09"),
                    created_after=base["cutoff"])
    assert cur["by_source"]["v5"]["directional"]["n"] == 5


def test_the_committed_baseline_is_aggregate_only_and_well_formed():
    path = ROOT / "docs" / "baselines" / "calibration_baseline.json"
    base = json.loads(path.read_text(encoding="utf-8"))
    assert base["cutoff"] and "by_source" in base and "overall" in base
    text = path.read_text(encoding="utf-8")
    for leak in ("subject", "ticker", "rationale", "external_ref"):
        assert f'"{leak}"' not in text, f"baseline must not carry per-prediction field {leak}"
    for src in base["by_source"].values():
        assert {"directional", "neutral", "stated_probability", "kinds"} <= set(src)
