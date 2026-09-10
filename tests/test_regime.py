"""
tests/test_regime.py
====================
The regime classifier is REAL, and it says how much of its answer it knew.

Two separate claims are pinned here, and they are separate on purpose:

  1. "Expansion (Goldilocks)" is NOT hardcoded. Feed the classifier different
     macro inputs and it returns a different regime — every quadrant is
     reachable, and each threshold moves it on its own.

  2. It cannot fake certainty. Every missing input falls back to a default that
     sits on the BENIGN side of its own threshold (3.0 < 3.5, 0.0 is not < 0,
     20 < 25, 4.5 < 5.5), so a completely empty snapshot classifies as
     Expansion. That is unavoidable — a quadrant model must answer somewhere —
     and it is only dangerous if the answer looks confident. So the empty case
     must report 0% coverage.
"""
from __future__ import annotations

import pytest

from src.macro import regime as R


def _snap(**kw):
    base = dict(cpi_yoy=2.0, yield_spread_10y2y=0.8, vix=15.0,
                unemployment_rate=4.0)
    base.update(kw)
    return base


# ── 1: the classifier moves ─────────────────────────────────────────────────

def test_every_quadrant_is_reachable():
    """A classifier that can only return one label is a constant."""
    assert R.classify(_snap())["regime"] == R.EXPANSION
    assert R.classify(_snap(cpi_yoy=6.0))["regime"] == R.STAGFLATION
    assert R.classify(_snap(yield_spread_10y2y=-0.5))["regime"] == R.SLOWDOWN
    assert R.classify(_snap(cpi_yoy=6.0, vix=40.0))["regime"] == R.RECESSION

    seen = {R.classify(_snap(cpi_yoy=c, vix=v))["regime"]
            for c in (1.0, 9.0) for v in (10.0, 45.0)}
    assert seen == set(R.REGIMES) - {R.SLOWDOWN} or len(seen) >= 3


@pytest.mark.parametrize("field,benign,stressed", [
    ("yield_spread_10y2y", 0.5, -0.1),
    ("vix", 15.0, 30.0),
    ("unemployment_rate", 4.0, 6.0),
])
def test_each_growth_input_can_flip_it_alone(field, benign, stressed):
    """Every stress test must be load-bearing on its own, or it is decoration."""
    assert R.classify(_snap(**{field: benign}))["regime"] == R.EXPANSION
    assert R.classify(_snap(**{field: stressed}))["regime"] == R.SLOWDOWN


def test_inflation_threshold_is_load_bearing():
    assert R.classify(_snap(cpi_yoy=3.49))["regime"] == R.EXPANSION
    assert R.classify(_snap(cpi_yoy=3.51))["regime"] == R.STAGFLATION


def test_goldilocks_is_not_a_string_in_the_data_path():
    """It is a CONCLUSION, so it must be derivable from inputs alone."""
    hot = R.classify(_snap(cpi_yoy=8.0, yield_spread_10y2y=-1.0,
                           vix=45.0, unemployment_rate=9.0))
    assert hot["regime"] == R.RECESSION
    assert hot["inflation"] == "high"
    assert hot["growth"] == "stressed"


# ── 2: it cannot fake certainty ─────────────────────────────────────────────

def test_a_snapshot_with_nothing_in_it_reports_zero_confidence():
    """THE point of this module.

    An empty snapshot still classifies as Expansion — every default sits on the
    benign side of its threshold, and a quadrant model has to answer somewhere.
    What must never happen is that answer looking as solid as a measured one.
    """
    blind = R.classify({})
    assert blind["regime"] == R.EXPANSION
    assert blind["confidence"] == 0.0
    assert blind["observed_inputs"] == 0
    assert len(blind["assumed_inputs"]) == len(R.INPUTS)
    assert blind["caveats"], "a fully-assumed regime reported no caveat"
    assert any("default" in c for c in blind["caveats"])


def test_confidence_is_coverage_and_rises_with_observation():
    assert R.classify({})["confidence"] == 0.0
    partial = R.classify({"vix": 15.0})
    full = R.classify(_snap())
    assert 0.0 < partial["confidence"] < full["confidence"] == 1.0


def test_every_input_is_labelled_observed_or_assumed():
    got = R.classify({"vix": 15.0, "cpi_yoy": 2.0})
    assert got["inputs"]["vix"]["status"] == "OBSERVED"
    assert got["inputs"]["cpi_yoy"]["status"] == "OBSERVED"
    assert got["inputs"]["unemployment_rate"]["status"] == "ASSUMED"
    assert got["inputs"]["unemployment_rate"]["assumed_default"] is not None
    # An observed value never advertises a default it did not use.
    assert got["inputs"]["vix"]["assumed_default"] is None


def test_confidence_never_comes_from_how_strong_the_reading_looks():
    """A screamingly obvious recession on ONE input is still one input."""
    obvious = R.classify({"cpi_yoy": 15.0, "vix": 80.0})
    assert obvious["regime"] == R.RECESSION
    assert obvious["confidence"] < 1.0, (
        "a two-input reading reported full confidence because the numbers "
        "were dramatic — confidence is coverage, not conviction")


# ── evidence and invalidation ───────────────────────────────────────────────

def test_the_evidence_names_every_threshold_test():
    got = R.classify(_snap())
    assert len(got["evidence"]) == len(R.INPUTS)
    joined = " ".join(got["evidence"])
    for token in ("CPI", "10Y-2Y", "VIX", "Unemployment"):
        assert token in joined


def test_it_states_what_would_change_its_mind():
    got = R.classify(_snap())
    assert got["would_flip_to"], "a regime with no invalidation condition"
    for f in got["would_flip_to"]:
        assert f["distance"] is not None and f["if"]


def test_the_live_reading_reports_its_own_age():
    got = R.current()
    assert got["regime"] in R.REGIMES
    assert "coverage" in got and "stale" in got
    if got.get("stale"):
        assert any("old" in c for c in got["caveats"]), (
            "a stale macro snapshot produced no staleness caveat")


def test_a_stale_snapshot_says_so_rather_than_reading_as_today():
    got = R.current({"cpi_yoy": 2.0, "yield_spread_10y2y": 0.5, "vix": 15.0,
                     "unemployment_rate": 4.0, "data_date": "2020-01-01"})
    assert got["stale"] is True
    assert got["age_days"] > 1000
    assert any("old" in c for c in got["caveats"])


def test_a_cached_regime_label_that_no_longer_follows_is_flagged():
    """The failure this system already had: a stored string rendered under a
    heading that implied it was live."""
    got = R.current({"cpi_yoy": 9.0, "yield_spread_10y2y": -1.0, "vix": 40.0,
                     "unemployment_rate": 8.0, "data_date": "2026-08-29",
                     "regime": "Expansion (Goldilocks)"})
    assert got["regime"] == R.RECESSION
    assert any("stale" in c for c in got["caveats"])
