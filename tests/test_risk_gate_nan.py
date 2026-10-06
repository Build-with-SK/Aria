"""
tests/test_risk_gate_nan.py
===========================
One gappy bar must not poison the V5 risk gate.

`_atr` returned float(NaN) whenever a missing High/Low fell inside the last
fourteen bars, and the callers tested it with `if (atr and px)` — NaN is
truthy. The NaN reached the stop distance and the portfolio-heat sum, where
every `<=` against a cap is False: one bad bar on any holding vetoed every new
trade, with a "nan%" reason.
"""
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.v5 import risk_gate as RG


def _frame(n=80, gap_at=None, seed=7):
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    high = close * (1 + rng.uniform(0.002, 0.02, n))
    low = close * (1 - rng.uniform(0.002, 0.02, n))
    df = pd.DataFrame({"High": high, "Low": low, "Close": close},
                      index=pd.bdate_range("2026-01-01", periods=n))
    if gap_at is not None:
        df.iloc[gap_at, df.columns.get_loc("High")] = np.nan
    return df


def _old_atr(df, n=14):
    hl = df["High"] - df["Low"]
    hc = (df["High"] - df["Close"].shift()).abs()
    lc = (df["Low"] - df["Close"].shift()).abs()
    tr = hl.combine(hc, max).combine(lc, max)
    return float(tr.rolling(n).mean().iloc[-1])


def test_clean_data_gives_the_same_atr_as_before(monkeypatch):
    df = _frame()
    monkeypatch.setattr(RG.md, "history", lambda t, period="6mo": df)
    assert math.isclose(RG._atr("X"), _old_atr(df), rel_tol=1e-12)


def test_a_gap_inside_the_window_yields_a_finite_atr(monkeypatch):
    df = _frame(gap_at=-3)
    monkeypatch.setattr(RG.md, "history", lambda t, period="6mo": df)
    atr = RG._atr("X")
    assert atr is not None and math.isfinite(atr) and atr > 0


def test_too_few_usable_bars_is_no_atr_not_a_guess(monkeypatch):
    df = _frame(n=25)
    df.loc[df.index[-15:], "High"] = np.nan
    monkeypatch.setattr(RG.md, "history", lambda t, period="6mo": df)
    assert RG._atr("X") is None


def test_finite_positive_rejects_nan_inf_zero_and_junk():
    for bad in (float("nan"), float("inf"), -1.0, 0.0, None, "abc"):
        assert RG._finite_positive(bad) is None
    assert RG._finite_positive("2.5") == 2.5


def test_a_holding_with_unmeasurable_atr_is_charged_the_flat_rate(monkeypatch):
    """The documented behaviour for an unmeasurable position: 2% of its
    value — never NaN."""
    monkeypatch.setattr(RG, "_atr", lambda t: None)
    monkeypatch.setattr(RG.md, "closes",
                        lambda t, period="3mo": pd.Series([float("nan")]))
    heat = RG._open_heat([{"symbol": "X", "market_value": 10_000}], 100_000)
    assert math.isfinite(heat)
    assert math.isclose(heat, 0.2)          # 10k * 2% / 100k * 100


def test_open_heat_is_finite_when_history_has_a_gap(monkeypatch):
    df = _frame(gap_at=-2)
    monkeypatch.setattr(RG.md, "history", lambda t, period="6mo": df)
    monkeypatch.setattr(RG.md, "closes", lambda t, period="3mo": df["Close"])
    heat = RG._open_heat([{"symbol": "X", "market_value": 10_000}], 100_000)
    assert math.isfinite(heat) and heat > 0
