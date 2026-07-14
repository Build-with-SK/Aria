"""
position_sizing.py
==================
Standalone position sizing calculators.

These are utility functions that can be called independently
or used by the risk engine. Useful for quick calculations
in the dashboard or Jupyter notebooks.
"""

from __future__ import annotations

import numpy as np


def fixed_fraction_size(
    portfolio_value: float,
    risk_pct:        float,    # e.g. 0.02 for 2%
    entry_price:     float,
    stop_price:      float,
) -> dict:
    """
    Classic 2% rule position sizing.
    Risk a fixed % of portfolio on each trade.

    Returns: units, dollar_size, actual_risk_pct
    """
    if abs(entry_price - stop_price) < 1e-6:
        return {"units": 0, "dollar_size": 0, "actual_risk_pct": 0}

    risk_dollars    = portfolio_value * risk_pct
    risk_per_unit   = abs(entry_price - stop_price)
    units           = risk_dollars / risk_per_unit
    dollar_size     = units * entry_price
    actual_risk_pct = (units * risk_per_unit) / portfolio_value

    return {
        "units":           round(units, 4),
        "dollar_size":     round(dollar_size, 2),
        "actual_risk_pct": round(actual_risk_pct, 6),
    }


def atr_position_size(
    portfolio_value: float,
    risk_pct:        float,
    atr:             float,
    entry_price:     float,
    atr_multiplier:  float = 1.5,
) -> dict:
    """
    ATR-based position sizing (Van Tharp method).
    Stop = entry ± ATR × multiplier.
    Position sized so risk_pct of portfolio is lost at stop.
    """
    stop_distance   = atr * atr_multiplier
    if stop_distance < 1e-6:
        return {"units": 0, "dollar_size": 0, "stop_distance": 0}

    risk_dollars  = portfolio_value * risk_pct
    units         = risk_dollars / stop_distance
    dollar_size   = units * entry_price

    return {
        "units":         round(units, 4),
        "dollar_size":   round(dollar_size, 2),
        "stop_distance": round(stop_distance, 4),
    }


def volatility_scaled_size(
    portfolio_value:  float,
    target_vol:       float,   # Target annual vol contribution (e.g. 0.15)
    asset_vol:        float,   # Asset annual vol (e.g. 0.25)
    entry_price:      float,
    max_pct:          float = 0.10,   # Max 10% per position
) -> dict:
    """
    Volatility targeting: size positions so each contributes equal volatility.
    Common in risk-parity and trend-following funds.
    """
    if asset_vol <= 0:
        return {"units": 0, "dollar_size": 0, "weight_pct": 0}

    weight_pct  = min((target_vol / asset_vol), max_pct)
    dollar_size = portfolio_value * weight_pct
    units       = dollar_size / entry_price if entry_price > 0 else 0

    return {
        "units":       round(units, 4),
        "dollar_size": round(dollar_size, 2),
        "weight_pct":  round(weight_pct, 4),
    }


def kelly_fraction(
    win_rate:    float,
    avg_win:     float,
    avg_loss:    float,
    fraction:    float = 0.25,   # Quarter-Kelly (more conservative)
) -> float:
    """
    Kelly criterion for position sizing.
    Returns the fraction of portfolio to risk.
    fraction=0.25 = quarter-Kelly (recommended conservative version).

    Kelly formula: f = W/L - (1-W)/G
    where W = win rate, L = avg loss, G = avg win
    """
    if avg_loss == 0 or avg_win == 0:
        return 0.0
    full_kelly = win_rate / avg_loss - (1 - win_rate) / avg_win
    kelly = max(0.0, full_kelly * fraction)
    return round(min(kelly, 0.10), 4)   # Hard cap at 10%
