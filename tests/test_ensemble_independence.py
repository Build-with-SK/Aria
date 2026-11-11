"""
tests/test_ensemble_independence.py
===================================
Two models are only worth combining if the second adds information. An echo
must be reported as redundant, an independent signal as adding information,
and a pair graded on different horizons as not testable at all.
"""
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from intel_helpers import business_days, row, ticker  # noqa: E402
from src.intelligence import dataset as D  # noqa: E402
from src.intelligence import ensemble as X  # noqa: E402


def _pair_rows(n=200, *, b_mode="echo", horizon_b=10, seed=5):
    rnd = random.Random(seed)
    days = business_days("2026-01-05", 40)
    a, b = [], []
    for i in range(n):
        d = days[i % len(days)]
        sym = ticker(i)
        up = rnd.random() < 0.5
        ret = 0.02 if up else -0.02
        # A is right 60% of the time.
        a_dir = ("bull" if up else "bear") if rnd.random() < 0.6 else ("bear" if up else "bull")
        if b_mode == "echo":
            b_dir = a_dir
        else:                                   # independent, right 75% of the time
            b_dir = ("bull" if up else "bear") if rnd.random() < 0.75 else ("bear" if up else "bull")
        for src, direc, h, out in (("technical", a_dir, 10, a), ("v5", b_dir, horizon_b, b)):
            ok = (ret > 0) == (direc == "bull")
            out.append(row(len(a) + len(b), source=src, subject=sym, direction=direc,
                           created=f"{d}T22:00:00", horizon=h, correct=int(ok),
                           actual_return=ret, information_session=d,
                           external_ref=f"{src}:{i}"))
    return a + b


def _run(rows):
    ds = D.build(rows)
    return X.independence(rows, ds)["pairs"][0]


def test_an_echo_is_redundant():
    p = _run(_pair_rows(b_mode="echo"))
    assert p["agreement"]["rate"] == 1.0
    assert p["incremental"]["status"] == "MEASURED"
    assert p["incremental"]["adds_information"] is False
    assert "redundant" in p["verdict"]


def test_an_independent_informative_model_adds_information():
    p = _run(_pair_rows(b_mode="independent"))
    assert p["incremental"]["adds_information"] is True
    assert p["errors"]["status"] == "MEASURED"
    assert "adds information" in p["verdict"]


def test_different_horizons_are_not_a_common_target():
    p = _run(_pair_rows(b_mode="independent", horizon_b=45))
    assert p["incremental"]["status"] == "NOT_TESTABLE"


def test_too_little_overlap_is_insufficient():
    p = _run(_pair_rows(n=20))
    assert p["agreement"]["status"] == "INSUFFICIENT_DATA"
    assert p["verdict"].startswith("insufficient overlap")


def test_the_lr_test_refuses_a_target_that_never_varies():
    out = X.incremental_information([1] * 60, [1] * 60, [1] * 60)
    assert out["status"] == "INSUFFICIENT_DATA"
