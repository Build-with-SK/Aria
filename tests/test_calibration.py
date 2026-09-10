"""
tests/test_calibration.py
=========================
The statistics that stopped ARIA over-claiming about itself — Phase 18.

Every test here pins a REFUSAL. The module's value is not that it computes a
Brier score; `ledger.calibration()` already did that, and the number it
produced supported a conclusion the data did not. The value is that this
version declines to draw a curve through non-independent events, declines to
pool populations with different mean confidence, and declines to call a
direction when the interval spans chance.

The concrete regression being guarded: 306 desk rows that are 61 distinct
events produced "skill -0.222, confidence inverted". Corrected, the same data
says 45.9% over 61 events, CI 34.0-58.3%, indistinguishable from chance.
"""
from __future__ import annotations

import pytest

from src.core import calibration as cal


# ── the interval ────────────────────────────────────────────────────────────

def test_wilson_interval_stays_inside_zero_and_one():
    """The normal approximation produces bounds outside [0,1] at these sample
    sizes, which would render as a probability range that cannot exist."""
    lo, hi = cal.wilson(0, 10)
    assert lo == 0.0 and 0 < hi < 1
    lo, hi = cal.wilson(10, 10)
    assert hi == 1.0 and 0 < lo < 1


def test_interval_narrows_as_n_grows():
    small = cal.wilson(5, 10)
    large = cal.wilson(500, 1000)
    assert (large[1] - large[0]) < (small[1] - small[0])


def test_rate_refuses_to_call_a_direction_on_a_small_sample():
    """20 of 30 is 67% — but at n=30 the module must say it cannot call it."""
    r = cal.rate(20, 30)
    assert r["rate"] == pytest.approx(0.6667, abs=1e-3)
    assert r["verdict"].startswith("undecided")
    assert str(cal.MIN_EVENTS) in r["verdict"]


def test_rate_calls_a_direction_once_the_sample_supports_it():
    r = cal.rate(400, 600)          # 66.7% over 600
    assert r["verdict"] == "better than chance"
    assert r["ci95"][0] > 0.5


def test_a_coin_flip_is_reported_as_a_coin_flip():
    r = cal.rate(300, 600)
    assert r["verdict"] == "indistinguishable from chance"


def test_worse_than_chance_is_reportable_when_real():
    """The module must be able to deliver bad news — a metric that can only
    say 'fine' or 'unknown' is not measuring anything."""
    r = cal.rate(200, 600)          # 33% over 600
    assert r["verdict"] == "worse than chance"
    assert r["ci95"][1] < 0.5


# ── deduplication ───────────────────────────────────────────────────────────

def _pred(subject, direction, price_at, price_end, correct, prob=0.7, rationale=""):
    return {"subject": subject, "direction": direction, "price_at": price_at,
            "price_end": price_end, "correct": correct, "probability": prob,
            "rationale": rationale}


def test_repeated_assertions_of_one_event_collapse_to_one():
    """BONK-USD bear @ 3e-06 → 3e-06 appeared eighteen times in the real file.
    Eighteen assertions of one fact are one observation."""
    rows = [_pred("BONK-USD", "bear", 3e-06, 3e-06, False) for _ in range(18)]
    rows.append(_pred("BTC-USD", "bear", 66505.0, 66100.0, True))
    out = cal._dedupe(rows)
    assert len(out) == 2


def test_different_moves_on_one_ticker_stay_separate():
    """Dedup must not collapse genuinely distinct events on the same name."""
    rows = [_pred("ETH-USD", "bear", 100.0, 97.0, True),
            _pred("ETH-USD", "bear", 100.0, 101.0, False)]
    assert len(cal._dedupe(rows)) == 2


def test_duplication_inflation_factor_is_reported(monkeypatch):
    """A caller must be told how much narrower the intervals WOULD have been,
    because that is the size of the error being avoided."""
    rows = [_pred("X", "bull", 1.0, 2.0, True) for _ in range(20)]
    rows += [_pred(f"T{i}", "bull", 1.0, 2.0, True) for i in range(4)]
    monkeypatch.setattr(cal, "_dedupe", cal._dedupe)   # real impl

    from src.core import ledger
    monkeypatch.setattr(ledger, "predictions", lambda **kw: rows)
    out = cal.investigate()
    assert out["rows_in_ledger"] == 24
    assert out["distinct_events"] == 5
    assert "repeat assertions" in out["duplication"]["note"]


# ── population mixing ───────────────────────────────────────────────────────

def test_populations_are_kept_apart_by_default(monkeypatch):
    traded = [_pred(f"A{i}", "bull", 1.0, 1.1 + i, True, prob=0.85,
                    rationale="Desk verdict BUY at conviction 80") for i in range(10)]
    not_traded = [_pred(f"B{i}", "bull", 1.0, 0.9 - i * 0.01, False, prob=0.55,
                        rationale="Desk verdict NO_TRADE at conviction 20") for i in range(10)]
    from src.core import ledger
    monkeypatch.setattr(ledger, "predictions", lambda **kw: traded + not_traded)
    out = cal.investigate()
    assert set(out["by_population"]) >= {"traded", "not traded"}
    assert out["by_population"]["traded"]["n"] == 10
    assert out["by_population"]["not traded"]["n"] == 10


def test_confidence_spread_between_populations_raises_a_warning(monkeypatch):
    """The specific mistake: a conviction curve drawn across populations whose
    mean confidence differs by 0.24 measures the populations, not calibration."""
    traded = [_pred(f"A{i}", "bull", 1.0, 1.1 + i, True, prob=0.85,
                    rationale="Desk verdict BUY at conviction 80") for i in range(12)]
    not_traded = [_pred(f"B{i}", "bull", 1.0, 0.9 - i * 0.01, False, prob=0.55,
                        rationale="Desk verdict NO_TRADE at conviction 20") for i in range(12)]
    from src.core import ledger
    monkeypatch.setattr(ledger, "predictions", lambda **kw: traded + not_traded)
    out = cal.investigate()
    warn = out["duplication"].get("population_warning")
    assert warn and "between populations" in warn


# ── the curve ───────────────────────────────────────────────────────────────

def test_curve_refuses_below_the_event_minimum():
    rows = [_pred(f"T{i}", "bull", 1.0, 2.0 + i, True) for i in range(20)]
    out = cal._calibration_curve(rows)
    assert out["measurable"] is False
    assert out["buckets"] == []
    assert str(cal.MIN_EVENTS) in out["note"]


def test_a_bucket_is_only_miscalibrated_outside_its_own_interval():
    """Noise inside the error bar must not be reported as miscalibration —
    that is how a random sample becomes a 'finding'."""
    # 80 events, stated 0.70, realised 0.70 → well calibrated.
    rows = [_pred(f"T{i}", "bull", 1.0, 2.0 + i, i < 56, prob=0.70) for i in range(80)]
    out = cal._calibration_curve(rows, min_bucket=10)
    measured = [b for b in out["buckets"] if b["measurable"]]
    assert measured and all(b["miscalibrated"] is False for b in measured)
    assert out["buckets_miscalibrated"] == 0


def test_real_miscalibration_is_caught():
    # stated 0.80, realised 0.20, n=100 → far outside the interval.
    rows = [_pred(f"T{i}", "bull", 1.0, 2.0 + i, i < 20, prob=0.80) for i in range(100)]
    out = cal._calibration_curve(rows, min_bucket=10)
    assert out["buckets_miscalibrated"] >= 1
    assert out["skill"] < 0


# ── probability vs ranking ──────────────────────────────────────────────────

def test_overlapping_halves_are_reported_as_undetermined():
    """The corrected desk result: 50.0% vs 42.3% with overlapping intervals is
    suggestive, and suggestive is not a finding."""
    rows = [_pred(f"L{i}", "bull", 1.0, 2.0 + i, i % 2 == 0, prob=0.55) for i in range(26)]
    rows += [_pred(f"H{i}", "bull", 1.0, 2.0 + i, i < 11, prob=0.85) for i in range(26)]
    out = cal._semantics(rows)
    assert out["measurable"] is True
    assert out["intervals_separated"] is False
    assert out["reading"].startswith("undetermined")


def test_clear_inversion_is_named_when_intervals_separate():
    rows = [_pred(f"L{i}", "bull", 1.0, 2.0 + i, True, prob=0.55) for i in range(120)]
    rows += [_pred(f"H{i}", "bull", 1.0, 2.0 + i, False, prob=0.85) for i in range(120)]
    out = cal._semantics(rows)
    assert out["intervals_separated"] is True
    assert "INVERTED" in out["reading"]


def test_semantics_declines_under_twenty_events():
    rows = [_pred(f"T{i}", "bull", 1.0, 2.0 + i, True) for i in range(10)]
    assert cal._semantics(rows)["measurable"] is False
