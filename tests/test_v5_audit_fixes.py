"""
Regressions for the four defects found by the V5 stress audit.

Each of these shipped a number a user would have acted on. They are pinned here
because all four are the kind of bug that reads as correct arithmetic — nothing
crashed, nothing logged a warning, the output just was not true.
"""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.v5 import ensemble  # noqa: E402
from src.v5.contract import DIRECTION_BAND, ModuleReport  # noqa: E402
from src.v5.modules._util import conditional_hit_rate  # noqa: E402
from src.v5.modules.fundamental import _ratio_is_meaningful  # noqa: E402


def _report(p, ci, i=0):
    return ModuleReport.from_probability(
        module=f"m{i}", family="price", ticker="T",
        p_bull=p, ci=ci, evidence=[], weaknesses=["w"], thesis="t")


# ── 1. neutral mass must survive into the ensemble ───────────────────────────

def test_ten_modules_saying_nothing_do_not_become_a_bull_call():
    """The headline defect: `bull/(bull+bear)` renormalised the neutral mass
    away, handing the ensemble back each module's raw point estimate. Ten
    engines that each individually read "neutral" produced BULL at 65%
    confidence — above the 55% trading bar, with the full risk budget released.
    """
    reps = [_report(0.80, (0.05, 0.90), i) for i in range(10)]
    assert all(r.view == "neutral" for r in reps), "precondition: each says neutral"

    res = ensemble.synthesise(reps, "T")
    assert res.direction == "neutral"
    assert res.confidence == 0.5
    assert res.confidence <= 0.55, "must not clear the trading bar"


def test_genuine_confidence_still_gets_through():
    """The fix must not simply mute everything — a tight interval on a strong
    estimate should still produce a tradeable call."""
    reps = [_report(0.80, (0.75, 0.85), i) for i in range(10)]
    res = ensemble.synthesise(reps, "T")
    assert res.direction == "bull"
    assert res.confidence > 0.55


def test_module_p_keeps_neutral_mass():
    wide, tight = _report(0.80, (0.05, 0.90)), _report(0.80, (0.75, 0.85))
    p_wide, p_tight = ensemble._module_p(wide), ensemble._module_p(tight)
    # Both had p_bull=0.80; only the tight one may still look like 0.80.
    assert p_wide < 0.60, "a maximally uncertain module must read near a coin flip"
    assert p_tight > 0.70
    assert p_wide < p_tight


def test_confidence_never_escapes_its_range():
    for p in (0.0, 0.25, 0.5, 0.75, 1.0):
        for ci in ((0.0, 1.0), (0.4, 0.6), (0.49, 0.51)):
            res = ensemble.synthesise([_report(p, ci, i) for i in range(3)], "T")
            assert 0.5 <= res.confidence <= 1.0, (p, ci, res.confidence)


def test_neutral_direction_carries_no_confidence():
    """Confidence is P(the stated direction is correct); with no direction
    stated it is undefined. It used to ship ~0.62 alongside "no position",
    and the learning log averaged that into its calibration statistics."""
    res = ensemble.synthesise([_report(0.5, (0.4, 0.6), i) for i in range(3)], "T")
    assert res.direction == "neutral"
    assert res.confidence == 0.5
    assert res.edge == 0.0


def test_ensemble_and_modules_share_one_direction_band():
    """They disagreed — modules banded at ±10, the ensemble at ±5 — so a net of
    9 was 'neutral' on every constituent and 'bull' on the aggregate."""
    assert DIRECTION_BAND == 10.0
    src = Path(__file__).parent.parent / "src" / "v5" / "ensemble.py"
    text = src.read_text(encoding="utf-8")
    assert "net > 5" not in text, "ensemble must not carry its own hardcoded band"


# ── 2. overlapping windows are not independent observations ──────────────────

def _series(n=525):
    return pd.Series(range(100, 100 + n), dtype=float,
                     index=pd.date_range("2024-01-01", periods=n, freq="D"))


def test_daily_sampling_of_a_21_day_horizon_is_discounted():
    """Daily sampling of a 21-day forward return inflates the sample ~21x.
    Wilson is correct arithmetic on a wrong n, so the interval came out ~4.6x
    too narrow — and `from_probability`'s 0.10 neutral floor then granted the
    result maximum conviction."""
    c = _series()
    mask = pd.Series(True, index=c.index)
    succ, n_eff, _mean, n_raw = conditional_hit_rate(c, mask, horizon=21)

    assert n_raw > 400, "precondition: plenty of raw daily observations"
    assert n_eff == round(n_raw / 21)
    assert n_eff < n_raw / 10, "the discount must actually bite"
    assert succ <= n_eff, "successes cannot exceed the sample they came from"


def test_the_hit_rate_itself_is_preserved():
    c = _series()
    mask = pd.Series(True, index=c.index)
    succ, n_eff, _m, n_raw = conditional_hit_rate(c, mask, horizon=21)
    # This series rises monotonically, so every forward return is positive.
    assert n_raw > 0 and succ == n_eff


def test_horizon_of_one_is_left_alone():
    """A 1-day horizon sampled daily genuinely IS independent."""
    c = _series(n=60)
    mask = pd.Series(True, index=c.index)
    _s, n_eff, _m, n_raw = conditional_hit_rate(c, mask, horizon=1)
    assert n_eff == n_raw


def test_empty_selection_is_still_safe():
    c = _series(n=60)
    mask = pd.Series(False, index=c.index)
    assert conditional_hit_rate(c, mask, horizon=21) == (0, 0, 0.0, 0)


# ── 3. undefined ratios are not attractive ratios ────────────────────────────

@pytest.mark.parametrize("key,value", [
    ("trailingPE", -8.0),      # loss-making
    ("forwardPE", -5.0),       # expected to swing to a loss
    ("priceToBook", -6.0),     # negative book equity: HD, MCD, SBUX, BA today
    ("debtToEquity", -50.0),
    ("trailingPE", 0.0),
])
def test_undefined_ratios_are_excluded(key, value):
    """Both scorers rank by raw magnitude, so a negative value landed at the
    'cheap' end and scored 1.0 — maximally attractive."""
    assert _ratio_is_meaningful(key, value) is False


@pytest.mark.parametrize("key,value", [
    ("trailingPE", 18.0),
    ("priceToBook", 2.4),
    ("debtToEquity", 45.0),
    ("profitMargins", -0.05),   # a real negative margin IS information
    ("returnOnEquity", -0.12),  # ditto — undefined is not the same as bad
])
def test_meaningful_values_still_score(key, value):
    assert _ratio_is_meaningful(key, value) is True
