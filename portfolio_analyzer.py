"""
portfolio_analyzer.py
=====================
Portfolio-level risk and exposure analysis.

Takes all current signals and computes:
  - Exposure by asset class
  - Portfolio volatility (weighted)
  - Correlation matrix across all assets
  - Diversification score
  - Risk contribution by asset
  - Concentration warnings

This module assumes equal-weight allocation adjusted by position_size_pct.
In Phase 4 this will be replaced by mean-variance optimisation.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def compute_exposure(signals: Dict[str, dict]) -> dict:
    """
    Compute portfolio exposure by asset class.
    Assumes position_size_pct reflects the actual allocation.

    Returns dict: { "equities": 45.2, "crypto": 12.3, ... }
    """
    exposure: Dict[str, float] = {}
    total_allocated = 0.0

    for ticker, sig in signals.items():
        asset_class = sig.get("asset_class", "unknown")
        pct = sig.get("position_size_pct", 0.0)
        # Only count if signal is bullish (positive score)
        if sig.get("composite_score", 0) > 10:
            exposure[asset_class] = exposure.get(asset_class, 0.0) + pct
            total_allocated += pct

    return {
        "by_class":        {k: round(v, 2) for k, v in exposure.items()},
        "total_allocated": round(total_allocated, 2),
        "cash_pct":        round(max(0, 100 - total_allocated), 2),
    }


def compute_correlation_matrix(
    featured_data: Dict[str, pd.DataFrame],
    window:        int = 60,
) -> Optional[pd.DataFrame]:
    """
    Compute rolling correlation matrix across all assets.

    Uses 60-day rolling returns for correlation.
    Returns DataFrame or None if insufficient data.
    """
    returns_dict = {}

    for ticker, df in featured_data.items():
        if df is None or df.empty or "Close" not in df.columns:
            continue
        ret = df["Close"].squeeze().pct_change().dropna()
        if len(ret) < window:
            continue
        returns_dict[ticker] = ret.tail(window)

    if len(returns_dict) < 2:
        return None

    try:
        returns_df = pd.DataFrame(returns_dict).dropna(axis=1, how="all")
        return returns_df.corr().round(3)
    except Exception as e:
        logger.warning(f"Correlation matrix failed: {e}")
        return None


def diversification_score(corr_matrix: Optional[pd.DataFrame]) -> float:
    """
    Diversification score from 0 (all correlated) to 100 (fully diversified).

    Computed as: 1 - avg(|off-diagonal correlation|)
    High score = assets move independently = good diversification.
    """
    if corr_matrix is None or corr_matrix.empty:
        return 50.0   # Unknown — return neutral

    n = len(corr_matrix)
    if n < 2:
        return 0.0

    # Extract upper triangle (exclude diagonal)
    upper = corr_matrix.values[np.triu_indices(n, k=1)]
    avg_abs_corr = float(np.mean(np.abs(upper)))

    score = (1 - avg_abs_corr) * 100
    return round(max(0, min(100, score)), 1)


def portfolio_volatility(
    signals:       Dict[str, dict],
    featured_data: Dict[str, pd.DataFrame],
) -> float:
    """
    Estimate overall portfolio volatility.
    Weighted average of individual asset vols, adjusted by position size.

    Returns annualised portfolio vol estimate (simple weighted, ignores correlations).
    For a proper calculation including correlations use the matrix approach in Phase 4.
    """
    weighted_vol = 0.0
    total_weight = 0.0

    for ticker, sig in signals.items():
        pct = sig.get("position_size_pct", 0.0) / 100
        vol = sig.get("realised_vol", 0.0)  # already annualised
        if pct > 0 and vol > 0 and sig.get("composite_score", 0) > 10:
            weighted_vol += pct * vol
            total_weight += pct

    if total_weight == 0:
        return 0.0

    # Normalise by total weight so it's a true weighted average (annualised %)
    return round(weighted_vol / total_weight, 4)


def risk_contribution(
    signals:       Dict[str, dict],
    corr_matrix:   Optional[pd.DataFrame],
) -> Dict[str, float]:
    """
    Estimate each asset's contribution to total portfolio risk (simplified).
    Returns dict: { ticker: contribution_pct }
    """
    if corr_matrix is None:
        # Fallback: weight by position size × vol
        total = 0.0
        contributions = {}
        for ticker, sig in signals.items():
            pct = sig.get("position_size_pct", 0.0) / 100
            vol = sig.get("realised_vol", 0.0)
            contrib = pct * vol
            contributions[ticker] = contrib
            total += contrib

        if total > 0:
            return {t: round(v / total * 100, 2) for t, v in contributions.items()}
        return {}

    # With correlation matrix: marginal risk contribution approximation
    tickers = [t for t in signals if t in corr_matrix.columns]
    weights = np.array([signals[t].get("position_size_pct", 0.0) / 100 for t in tickers])
    vols    = np.array([signals[t].get("realised_vol", 0.0)            for t in tickers])

    if len(tickers) < 2 or weights.sum() == 0:
        return {}

    # Build covariance matrix (simplified: diag(vols) × corr × diag(vols))
    try:
        sub_corr = corr_matrix.loc[tickers, tickers].values
        cov      = np.outer(vols, vols) * sub_corr
        port_var = float(weights @ cov @ weights)
        if port_var <= 0:
            return {}

        # Marginal contribution = weight × (cov @ weights) / port_vol
        marginal = (weights * (cov @ weights)) / np.sqrt(port_var)
        total    = marginal.sum()

        if total == 0:
            return {}

        return {t: round(float(m / total * 100), 2) for t, m in zip(tickers, marginal)}
    except Exception as e:
        logger.warning(f"Risk contribution calculation failed: {e}")
        return {}


def run_portfolio_analysis(
    signals:       Dict[str, dict],
    featured_data: Dict[str, pd.DataFrame],
) -> dict:
    """
    Full portfolio analysis in one call.

    Returns a dict suitable for JSON serialisation and dashboard display.
    """
    exposure  = compute_exposure(signals)
    corr_mat  = compute_correlation_matrix(featured_data)
    div_score = diversification_score(corr_mat)
    port_vol  = portfolio_volatility(signals, featured_data)
    risk_cont = risk_contribution(signals, corr_mat)

    # Warnings
    warnings = []
    if exposure["total_allocated"] > 80:
        warnings.append(f"High allocation: {exposure['total_allocated']:.1f}% deployed")
    if div_score < 30:
        warnings.append("Low diversification — assets are highly correlated")
    if port_vol > 0.30:
        warnings.append(f"High portfolio volatility: {port_vol:.0%} annualised")

    # Top 3 risk contributors
    top_risk = sorted(risk_cont.items(), key=lambda x: x[1], reverse=True)[:5]

    return {
        "exposure":           exposure,
        "diversification_score": div_score,
        "portfolio_vol":      port_vol,
        "risk_contribution":  risk_cont,
        "top_risk_assets":    [{"ticker": t, "contribution_pct": c} for t, c in top_risk],
        "warnings":           warnings,
        "n_assets_active":    sum(1 for s in signals.values() if s.get("composite_score", 0) > 10),
        "corr_matrix":        corr_mat.to_dict() if corr_mat is not None else {},
    }
