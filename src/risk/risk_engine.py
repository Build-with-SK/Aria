"""
risk_engine.py
==============
Comprehensive risk parameter calculator for individual signals.

For every AssetSignal this module produces:
  - Suggested position size (multiple methods)
  - Stop-loss and take-profit levels
  - Maximum capital at risk in dollar terms
  - Drawdown warning
  - Correlation warning (if provided)
  - Portfolio concentration warning

Risk methodology:
  1. Fixed-fraction (2% of portfolio risk per trade)
  2. ATR-based sizing (size inversely proportional to ATR)
  3. Volatility-adjusted (Kelly-inspired, capped at 5% of portfolio)
  4. Final recommendation = min of all three (most conservative)

IMPORTANT: Risk management is the most important layer of any trading system.
A good signal with bad risk management will still lose money.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, Optional

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class RiskParameters:
    """Full risk analysis for one signal."""
    ticker:               str

    # Position sizing (as % of portfolio)
    fixed_fraction_pct:   float    # 2% rule
    atr_based_pct:        float    # ATR sizing
    vol_adjusted_pct:     float    # Volatility sizing
    recommended_pct:      float    # Most conservative of the three

    # Dollar amounts (assuming portfolio_value input)
    max_dollar_risk:      float    # Max $ to risk on this trade
    position_dollar_size: float    # Total position in $

    # Stop and target levels
    stop_loss:            float
    take_profit:          float
    invalidation:         float
    risk_reward_ratio:    float    # (target - entry) / (entry - stop)

    # Warnings
    drawdown_warning:     bool
    drawdown_pct:         float
    high_vol_warning:     bool
    vol_pct:              float
    correlation_warning:  bool     # True if asset is highly correlated to rest of portfolio

    # Summary
    risk_level:           str      # "Low" / "Medium" / "High" / "Very High"
    summary:              str


def compute_risk_parameters(
    ticker:           str,
    current_price:    float,
    atr:              float,           # Average True Range (price units)
    realised_vol:     float,           # Annualised vol (e.g. 0.25 for 25%)
    drawdown:         float,           # Current drawdown from 52w high (negative)
    signal_score:     float,
    portfolio_value:  float = 100_000,
    max_risk_pct:     float = 0.02,    # 2% per trade
    sma_50:           Optional[float] = None,
    correlation:      float = 0.0,     # Correlation to portfolio (from cross-asset features)
    portfolio_vol:    float = 0.0,     # Overall portfolio volatility
) -> RiskParameters:
    """
    Compute all risk parameters for one signal.

    Parameters
    ----------
    ticker          : Asset ticker
    current_price   : Latest close price
    atr             : ATR in price units
    realised_vol    : Annualised realised volatility
    drawdown        : Drawdown from 52-week high (negative fraction)
    signal_score    : Composite signal score (-100 to +100)
    portfolio_value : Total portfolio $ value
    max_risk_pct    : Max % of portfolio to risk per trade
    sma_50          : 50-day SMA (used for invalidation level)
    correlation     : Correlation of asset with portfolio
    portfolio_vol   : Portfolio-level volatility

    Returns
    -------
    RiskParameters
    """
    direction = 1 if signal_score > 0 else -1
    atr = max(atr, current_price * 0.005)  # Floor at 0.5% of price

    # --- Stop loss (1.5 × ATR from entry) ---
    stop_loss = current_price - direction * 1.5 * atr

    # --- Take profit (2.5 × ATR from entry) ---
    take_profit = current_price + direction * 2.5 * atr

    # --- Invalidation level ---
    if sma_50 is not None and not np.isnan(sma_50):
        invalidation = sma_50 * (0.99 if direction > 0 else 1.01)
    else:
        invalidation = stop_loss  # Fall back to stop-loss

    # --- Risk/reward ratio ---
    entry_to_stop   = abs(current_price - stop_loss)
    entry_to_target = abs(take_profit - current_price)
    rr_ratio = entry_to_target / entry_to_stop if entry_to_stop > 0 else 0.0

    # --- Method 1: Fixed-fraction (2% rule) ---
    # Risk $ = portfolio_value * max_risk_pct
    # Position size = Risk $ / (entry - stop) per unit
    risk_dollars      = portfolio_value * max_risk_pct
    if entry_to_stop > 0:
        fixed_units   = risk_dollars / entry_to_stop
        fixed_fraction_pct = (fixed_units * current_price / portfolio_value) * 100
    else:
        fixed_fraction_pct = max_risk_pct * 100

    # --- Method 2: ATR-based sizing ---
    # Position size inversely proportional to ATR as % of price
    atr_pct    = atr / current_price
    vol_scalar = min(0.02 / max(atr_pct, 0.005), 1.0)   # Normalise to 2% ATR benchmark
    atr_based_pct = vol_scalar * max_risk_pct * 100 * 1.5

    # --- Method 3: Volatility-adjusted ---
    # Inspired by Kelly criterion but capped conservatively
    if realised_vol > 0:
        target_vol = 0.15   # Target 15% annual vol contribution
        vol_adjusted_pct = (target_vol / realised_vol) * 100 * max_risk_pct * 5
    else:
        vol_adjusted_pct = max_risk_pct * 100

    # --- Final recommendation: most conservative (min of all three) ---
    all_sizes = [
        max(0.1, min(fixed_fraction_pct, 10.0)),
        max(0.1, min(atr_based_pct,      10.0)),
        max(0.1, min(vol_adjusted_pct,   10.0)),
    ]
    recommended_pct = min(all_sizes)

    # Reduce by signal strength (only go full size on strong signals)
    abs_score = abs(signal_score)
    if abs_score < 20:
        recommended_pct *= 0.3
    elif abs_score < 40:
        recommended_pct *= 0.6
    elif abs_score < 60:
        recommended_pct *= 0.8

    recommended_pct = round(recommended_pct, 2)

    # --- Dollar amounts ---
    position_dollar = portfolio_value * recommended_pct / 100
    max_dollar_risk = position_dollar * (entry_to_stop / current_price)

    # --- Warnings ---
    drawdown_warning   = drawdown < -0.20   # In drawdown > 20% from peak
    high_vol_warning   = realised_vol > 0.40
    corr_warning       = correlation > 0.80  # Highly correlated to portfolio

    # --- Risk level ---
    if realised_vol > 0.50 or atr_pct > 0.04:
        risk_level = "Very High"
    elif realised_vol > 0.30 or atr_pct > 0.02:
        risk_level = "High"
    elif realised_vol > 0.15 or atr_pct > 0.01:
        risk_level = "Medium"
    else:
        risk_level = "Low"

    # --- Summary ---
    warnings = []
    if drawdown_warning:
        warnings.append(f"Asset in {abs(drawdown):.0%} drawdown from 52w high")
    if high_vol_warning:
        warnings.append(f"High volatility: {realised_vol:.0%} annualised")
    if corr_warning:
        warnings.append("High correlation to portfolio — concentration risk")
    if rr_ratio < 1.5:
        warnings.append(f"Risk/reward ratio is low: {rr_ratio:.1f}x")

    summary = (
        f"Recommended position: {recommended_pct:.1f}% of portfolio (${position_dollar:,.0f}). "
        f"Risk/reward: {rr_ratio:.1f}x. Risk level: {risk_level}."
    )
    if warnings:
        summary += " WARNINGS: " + "; ".join(warnings)

    return RiskParameters(
        ticker=ticker,
        fixed_fraction_pct=round(fixed_fraction_pct, 2),
        atr_based_pct=round(atr_based_pct, 2),
        vol_adjusted_pct=round(vol_adjusted_pct, 2),
        recommended_pct=recommended_pct,
        max_dollar_risk=round(max_dollar_risk, 2),
        position_dollar_size=round(position_dollar, 2),
        stop_loss=round(stop_loss, 4),
        take_profit=round(take_profit, 4),
        invalidation=round(invalidation, 4),
        risk_reward_ratio=round(rr_ratio, 2),
        drawdown_warning=drawdown_warning,
        drawdown_pct=round(float(drawdown), 4),
        high_vol_warning=high_vol_warning,
        vol_pct=round(realised_vol, 4),
        correlation_warning=corr_warning,
        risk_level=risk_level,
        summary=summary,
    )


def risk_to_json(risk: RiskParameters) -> dict:
    """Serialise RiskParameters to JSON-safe dict."""
    return {
        "ticker":               risk.ticker,
        "fixed_fraction_pct":   risk.fixed_fraction_pct,
        "atr_based_pct":        risk.atr_based_pct,
        "vol_adjusted_pct":     risk.vol_adjusted_pct,
        "recommended_pct":      risk.recommended_pct,
        "max_dollar_risk":      risk.max_dollar_risk,
        "position_dollar_size": risk.position_dollar_size,
        "stop_loss":            risk.stop_loss,
        "take_profit":          risk.take_profit,
        "invalidation":         risk.invalidation,
        "risk_reward_ratio":    risk.risk_reward_ratio,
        "drawdown_warning":     risk.drawdown_warning,
        "drawdown_pct":         risk.drawdown_pct,
        "high_vol_warning":     risk.high_vol_warning,
        "vol_pct":              risk.vol_pct,
        "correlation_warning":  risk.correlation_warning,
        "risk_level":           risk.risk_level,
        "summary":              risk.summary,
    }
