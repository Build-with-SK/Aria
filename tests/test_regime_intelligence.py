"""
tests/test_regime_intelligence.py
=================================
The market regime is computed only from bars known when the call was made,
and the macro label's limits are reported rather than evaluated around.
"""
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from intel_helpers import row  # noqa: E402
from src.intelligence import regime as R  # noqa: E402

NY = ZoneInfo("America/New_York")


def series(n=400, drift=0.001, vol=0.01, end="2026-06-30", seed=3, vol_path=None):
    idx = pd.bdate_range(end=end, periods=n)
    rnd = np.random.default_rng(seed)
    v = vol_path if vol_path is not None else np.full(n, vol)
    r = drift + v * rnd.standard_normal(n)
    return pd.Series(100 * np.exp(np.cumsum(r)), index=idx)


def test_trend_labels():
    assert R.classify_market(series(drift=0.002))["trend"] == "BULL"
    assert R.classify_market(series(drift=-0.002))["trend"] == "BEAR"


def test_fewer_than_two_hundred_bars_is_unknown_not_a_default():
    out = R.classify_market(series(n=150))
    assert out["trend"] == "UNKNOWN" and out["label"] is None


def test_volatility_relative_to_its_own_history():
    calm_then_wild = np.r_[np.full(380, 0.005), np.full(20, 0.04)]
    assert R.classify_market(series(vol_path=calm_then_wild))["vol"] == "HIGH_VOL"
    wild_then_calm = np.r_[np.full(380, 0.03), np.full(20, 0.003)]
    out = R.classify_market(series(vol_path=wild_then_calm, drift=0.002))
    assert out["vol"] == "LOW_VOL"


def test_the_label_is_scale_invariant_so_later_adjustments_cannot_change_it():
    s = series()
    assert R.classify_market(s)["label"] == R.classify_market(s * 0.97)["label"]


def test_the_labeller_uses_only_bars_known_at_the_call():
    s = series(end="2026-06-30")
    lab = R.MarketRegimeLabeler(s)
    at = datetime(2026, 6, 15, 14, 0, tzinfo=NY)            # inside the session
    base = lab.at(at)
    assert base["last_bar"] == "2026-06-12"                  # today's bar is not final
    mutated = s.copy()
    mutated[mutated.index >= "2026-06-15"] *= 0.5            # today and after
    assert R.MarketRegimeLabeler(mutated).at(at) == base
    after_close = datetime(2026, 6, 15, 16, 30, tzinfo=NY)
    assert lab.at(after_close)["last_bar"] == "2026-06-15"


def test_a_change_before_the_call_can_change_the_label():
    s = series(drift=0.002)
    at = datetime(2026, 6, 15, 17, 0, tzinfo=NY)
    crashed = s.copy()
    crashed[(crashed.index > "2026-01-01") & (crashed.index <= "2026-06-15")] *= np.linspace(
        1, 0.3, int(((crashed.index > "2026-01-01") & (crashed.index <= "2026-06-15")).sum()))
    assert R.MarketRegimeLabeler(s).at(at)["trend"] == "BULL"
    assert R.MarketRegimeLabeler(crashed).at(at)["trend"] == "BEAR"


def test_the_labeller_reads_naive_ledger_times_as_local():
    lab = R.MarketRegimeLabeler(series())
    out = lab({"created_at": "2026-06-15T23:00:00"})
    assert out["last_bar"] in ("2026-06-15", "2026-06-12")


def test_a_single_macro_label_is_reported_as_unmeasurable():
    rows = [row(i, created=f"2026-08-0{i}T22:00:00") for i in range(3, 8)]
    a = R.audit_macro(rows)
    assert a["graded_labels"] == {"Expansion (Goldilocks)": 5}
    assert "NOT measurable" in a["verdict"]
    assert a["transitions_in_creation_order"] == 0 and a["lookahead_rows"] == 0


def test_the_audit_counts_lookahead_labels():
    rows = [row(1, created="2026-09-02T20:00:00",
                regime_basis="world_snapshot 2026-09-02T20:24:47 (taken before the call)")]
    assert R.audit_macro(rows)["lookahead_rows"] == 1
