# src/models/targets.py
"""
ML Target Engineering
Risk-adjusted return targets, direction labels, volatility targets.
Eliminates lookahead bias. Supports multi-horizon labelling.
"""

from __future__ import annotations
import numpy as np
import pandas as pd
from typing import Dict, List, Optional, Tuple


# ─────────────────────────────────────────────────────────────────────────────
# Core target functions
# ─────────────────────────────────────────────────────────────────────────────

def future_return(close: pd.Series, horizon: int) -> pd.Series:
    """
    Raw forward return over `horizon` days.
    Shift back so index aligns with the day we'd place the trade.
    No lookahead: return at t uses close[t+horizon] / close[t] - 1.
    """
    return close.pct_change(horizon).shift(-horizon)


def risk_adjusted_return(
    close: pd.Series,
    atr: pd.Series,
    horizon: int,
) -> pd.Series:
    """
    Forward return normalised by ATR (volatility-adjusted).
    Teaches the model to find HIGH QUALITY moves, not just direction.
    Target = (close[t+h] - close[t]) / (ATR[t] * sqrt(horizon))
    """
    fwd = future_return(close, horizon)
    norm_atr = (atr * np.sqrt(horizon)).replace(0, np.nan)
    return fwd * close / norm_atr  # scale by price to get R-multiple


def direction_label(
    close: pd.Series,
    horizon: int,
    threshold_pct: float = 0.005,  # 0.5% min move to avoid labelling noise
) -> pd.Series:
    """
    3-class label: 1 (up), -1 (down), 0 (flat/noise).
    Threshold filters out tiny moves that aren't tradeable signals.
    """
    fwd = future_return(close, horizon)
    labels = pd.Series(0, index=close.index, dtype=int)
    labels[fwd >  threshold_pct] =  1
    labels[fwd < -threshold_pct] = -1
    return labels


def binary_direction(close: pd.Series, horizon: int) -> pd.Series:
    """Binary: 1 if up, 0 if down. Used for probability calibration."""
    fwd = future_return(close, horizon)
    return (fwd > 0).astype(int)


def triple_barrier_label(
    close: pd.Series,
    atr: pd.Series,
    horizon: int,
    profit_multiple: float = 2.0,   # TP = entry + profit_multiple * ATR
    stop_multiple: float = 1.0,     # SL = entry - stop_multiple * ATR
) -> pd.Series:
    """
    Lopez de Prado triple-barrier labelling.
    +1: hit profit target before stop or horizon
    -1: hit stop before profit target or horizon
     0: hit horizon without touching either barrier
    Much more realistic than simple forward return.
    """
    labels = pd.Series(0, index=close.index, dtype=int)
    closes = close.values
    atrs   = atr.values
    n = len(closes)

    for i in range(n - horizon):
        entry = closes[i]
        curr_atr = atrs[i] if not np.isnan(atrs[i]) else entry * 0.01
        tp = entry + profit_multiple * curr_atr
        sl = entry - stop_multiple  * curr_atr

        label = 0
        for j in range(i + 1, min(i + horizon + 1, n)):
            price = closes[j]
            if price >= tp:
                label = 1
                break
            elif price <= sl:
                label = -1
                break
        labels.iloc[i] = label

    return labels


def build_targets(
    df: pd.DataFrame,
    horizons: List[int] = [1, 5, 20],
    use_triple_barrier: bool = False,
    use_risk_adjusted: bool = True,
) -> pd.DataFrame:
    """
    Build all target columns for a single ticker DataFrame.
    Returns DataFrame with added target columns (no lookahead).

    Columns added:
      target_dir_{h}d      — 3-class direction label
      target_binary_{h}d   — binary direction
      target_ret_{h}d      — raw forward return
      target_radj_{h}d     — risk-adjusted return (if use_risk_adjusted)
      target_tb_{h}d       — triple barrier label (if use_triple_barrier)
    """
    df = df.copy()
    close = df["Close"]
    atr   = df.get("atr", close.rolling(14).apply(
        lambda x: x.diff().abs().mean(), raw=False
    ))

    for h in horizons:
        df[f"target_dir_{h}d"]    = direction_label(close, h)
        df[f"target_binary_{h}d"] = binary_direction(close, h)
        df[f"target_ret_{h}d"]    = future_return(close, h)

        if use_risk_adjusted:
            df[f"target_radj_{h}d"] = risk_adjusted_return(close, atr, h)

        if use_triple_barrier:
            df[f"target_tb_{h}d"] = triple_barrier_label(close, atr, h)

    return df


def get_target_col(horizon: int, mode: str = "direction") -> str:
    """Return the canonical target column name for a given horizon and mode."""
    mapping = {
        "direction":      f"target_dir_{horizon}d",
        "binary":         f"target_binary_{horizon}d",
        "return":         f"target_ret_{horizon}d",
        "risk_adjusted":  f"target_radj_{horizon}d",
        "triple_barrier": f"target_tb_{horizon}d",
    }
    return mapping.get(mode, f"target_dir_{horizon}d")
