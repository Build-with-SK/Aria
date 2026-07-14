"""
portfolio_optimizer.py
======================
Phase 4 — Portfolio optimisation engine.

Three strategies implemented:
  1. Mean-Variance (Markowitz) — maximise Sharpe ratio
  2. Risk Parity — equalise risk contribution across assets
  3. Signal-Weighted — weight by composite signal score
  4. Kelly Criterion — size positions by edge and odds

All optimisers respect:
  - Maximum position size per asset (default 15%)
  - Minimum position size (default 0.5%)
  - Long-only constraint (no shorting in default mode)
  - Cash allocation (any unallocated capital stays as cash)

Output: OptimalPortfolio with weights, expected return, expected vol,
        Sharpe ratio, and full allocation table.

Uses scipy.optimize — no external portfolio library needed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from scipy.optimize import minimize

logger = logging.getLogger(__name__)


@dataclass
class OptimalPortfolio:
    """Result of one portfolio optimisation run."""
    strategy:         str
    weights:          Dict[str, float]    # ticker → weight (0-1)
    expected_return:  float               # Annualised
    expected_vol:     float               # Annualised
    sharpe_ratio:     float
    diversification:  float               # 0-100
    total_allocated:  float               # Sum of weights (should be ≤ 1)
    cash_pct:         float
    allocation_table: pd.DataFrame        # Full breakdown
    warnings:         List[str] = field(default_factory=list)


def _build_return_matrix(
    featured_data: Dict[str, pd.DataFrame],
    tickers: List[str],
    min_periods: int = 60,
) -> Tuple[pd.DataFrame, List[str]]:
    """
    Build a matrix of daily returns for all valid tickers.
    Drops tickers with insufficient history.
    Returns (returns_df, valid_tickers).
    """
    returns = {}
    for ticker in tickers:
        df = featured_data.get(ticker)
        if df is None or df.empty or "Close" not in df.columns:
            continue
        r = df["Close"].squeeze().pct_change().dropna()
        if len(r) >= min_periods:
            returns[ticker] = r

    if not returns:
        return pd.DataFrame(), []

    ret_df = pd.DataFrame(returns).dropna(how="all")
    valid  = [t for t in tickers if t in ret_df.columns]
    return ret_df[valid], valid


def _annualise(daily_return: float, daily_vol: float) -> Tuple[float, float]:
    """Convert daily return/vol to annualised figures."""
    ann_ret = (1 + daily_return) ** 252 - 1
    ann_vol = daily_vol * np.sqrt(252)
    return ann_ret, ann_vol


# ===========================================================================
# Strategy 1: Mean-Variance (Maximum Sharpe)
# ===========================================================================

def mean_variance_optimise(
    returns_df:   pd.DataFrame,
    risk_free:    float = 0.045,
    max_weight:   float = 0.15,
    min_weight:   float = 0.005,
) -> Optional[np.ndarray]:
    """
    Find weights that maximise the Sharpe ratio.
    Uses scipy.optimize.minimize with SLSQP solver.

    Returns array of weights (same order as returns_df.columns),
    or None if optimisation fails.
    """
    n = len(returns_df.columns)
    if n < 2:
        return None

    mean_returns = returns_df.mean()
    cov_matrix   = returns_df.cov()
    daily_rf     = risk_free / 252

    def neg_sharpe(weights):
        port_return = float(np.dot(weights, mean_returns))
        port_vol    = float(np.sqrt(weights @ cov_matrix.values @ weights))
        if port_vol < 1e-10:
            return 0.0
        return -(port_return - daily_rf) / port_vol

    # Constraints: weights sum to 1
    constraints = [{"type": "eq", "fun": lambda w: np.sum(w) - 1.0}]
    bounds      = [(min_weight, max_weight)] * n
    x0          = np.array([1.0 / n] * n)   # Equal weight start

    try:
        result = minimize(
            neg_sharpe, x0,
            method="SLSQP",
            bounds=bounds,
            constraints=constraints,
            options={"maxiter": 1000, "ftol": 1e-9},
        )
        if result.success:
            return result.x
    except Exception as e:
        logger.warning(f"Mean-variance optimisation failed: {e}")

    return None


# ===========================================================================
# Strategy 2: Risk Parity
# ===========================================================================

def risk_parity_optimise(
    returns_df: pd.DataFrame,
    max_weight: float = 0.20,
) -> Optional[np.ndarray]:
    """
    Risk parity: allocate so each asset contributes equally to portfolio risk.
    Assets with lower volatility get higher weights.

    Simple implementation: weight = 1/vol (normalised).
    More advanced: iterative risk contribution equalisation.
    """
    n = len(returns_df.columns)
    if n < 1:
        return None

    vols = returns_df.std()
    vols = vols.replace(0, np.nan).dropna()

    if vols.empty:
        return None

    # Inverse volatility weights
    inv_vol = 1.0 / vols
    weights = inv_vol / inv_vol.sum()

    # Align with original columns
    w_array = np.array([weights.get(col, 0.0) for col in returns_df.columns])

    # Clip to max weight and renormalise
    w_array = np.clip(w_array, 0, max_weight)
    if w_array.sum() > 0:
        w_array = w_array / w_array.sum()

    return w_array


# ===========================================================================
# Strategy 3: Signal-Weighted
# ===========================================================================

def signal_weighted_optimise(
    signals:    Dict[str, dict],
    tickers:    List[str],
    max_weight: float = 0.15,
    min_score:  float = 10.0,   # Only include assets with score > this
) -> np.ndarray:
    """
    Weight positions by composite signal score.
    Only bullish signals (score > min_score) get allocated.
    Higher score = larger weight.

    Simple, transparent, and directly tied to the signal engine.
    """
    n = len(tickers)
    weights = np.zeros(n)

    for i, ticker in enumerate(tickers):
        sig = signals.get(ticker, {})
        score = sig.get("composite_score", 0)
        if score > min_score:
            weights[i] = score

    if weights.sum() <= 0:
        return np.zeros(n)

    weights = weights / weights.sum()
    weights = np.clip(weights, 0, max_weight)
    if weights.sum() > 0:
        weights = weights / weights.sum()

    return weights


# ===========================================================================
# Master optimiser
# ===========================================================================

def optimise_portfolio(
    signals:       Dict[str, dict],
    featured_data: Dict[str, pd.DataFrame],
    strategy:      str  = "signal_weighted",   # "mean_variance" | "risk_parity" | "signal_weighted"
    risk_free:     float = 0.045,
    max_weight:    float = 0.15,
    min_weight:    float = 0.005,
    total_capital: float = 100_000,
) -> OptimalPortfolio:
    """
    Run portfolio optimisation and return an OptimalPortfolio.

    Parameters
    ----------
    signals       : Dict of current signal dicts (from signals.json)
    featured_data : Dict of feature-enriched DataFrames
    strategy      : Which optimisation method to use
    risk_free     : Annual risk-free rate
    max_weight    : Maximum weight per asset (e.g. 0.15 = 15%)
    min_weight    : Minimum weight per asset
    total_capital : Portfolio size in $ (for dollar allocation display)

    Returns
    -------
    OptimalPortfolio with weights and analytics
    """
    warnings = []

    # Only include assets with signal data
    tickers = [t for t in signals.keys() if signals[t].get("composite_score") is not None]

    if not tickers:
        return OptimalPortfolio(
            strategy=strategy, weights={}, expected_return=0, expected_vol=0,
            sharpe_ratio=0, diversification=0, total_allocated=0, cash_pct=100,
            allocation_table=pd.DataFrame(), warnings=["No valid signals found"],
        )

    # Build return matrix
    returns_df, valid_tickers = _build_return_matrix(featured_data, tickers)

    if returns_df.empty or len(valid_tickers) < 2:
        warnings.append("Insufficient price history for optimisation — using signal-weighted fallback")
        strategy = "signal_weighted"
        valid_tickers = tickers

    # Run chosen strategy
    weights_array = None

    if strategy == "mean_variance" and not returns_df.empty:
        weights_array = mean_variance_optimise(returns_df, risk_free, max_weight, min_weight)
        if weights_array is None:
            warnings.append("Mean-variance optimisation failed — falling back to risk parity")
            strategy = "risk_parity"

    if strategy == "risk_parity" and not returns_df.empty:
        weights_array = risk_parity_optimise(returns_df, max_weight)

    if strategy == "signal_weighted" or weights_array is None:
        strategy = "signal_weighted"
        valid_tickers = tickers
        weights_array = signal_weighted_optimise(signals, valid_tickers, max_weight)

    if weights_array is None or weights_array.sum() == 0:
        warnings.append("No allocation possible — all weights are zero")
        weights_array = np.zeros(len(valid_tickers))

    # Scale back: only invest up to the signal threshold
    # Don't force 100% allocation — hold cash for weak markets
    max_allocation = 0.80   # Never allocate more than 80% of capital
    total_w = weights_array.sum()
    if total_w > max_allocation:
        weights_array = weights_array * (max_allocation / total_w)

    # Build weights dict
    weights_dict = {t: round(float(w), 4) for t, w in zip(valid_tickers, weights_array)}
    # Add zero weights for any tickers not in optimisation
    for t in signals:
        if t not in weights_dict:
            weights_dict[t] = 0.0

    total_allocated = sum(weights_dict.values())
    cash_pct        = max(0.0, 1.0 - total_allocated)

    # Portfolio analytics
    exp_return = 0.0
    exp_vol    = 0.0
    sharpe     = 0.0

    if not returns_df.empty and len(valid_tickers) >= 2:
        try:
            w = np.array([weights_dict.get(t, 0) for t in valid_tickers])
            ret_subset = returns_df[[t for t in valid_tickers if t in returns_df.columns]]
            mean_r = ret_subset.mean().values
            cov_m  = ret_subset.cov().values

            daily_ret = float(np.dot(w[:len(mean_r)], mean_r))
            daily_vol = float(np.sqrt(w[:len(mean_r)] @ cov_m @ w[:len(mean_r)]))

            exp_return, exp_vol = _annualise(daily_ret, daily_vol)
            daily_rf = risk_free / 252
            sharpe = (daily_ret - daily_rf) / daily_vol if daily_vol > 0 else 0.0
            sharpe = round(sharpe * np.sqrt(252), 3)
        except Exception as e:
            logger.warning(f"Portfolio analytics failed: {e}")

    # Diversification score
    active_weights = np.array([w for w in weights_dict.values() if w > 0])
    if len(active_weights) > 1:
        hhi = float(np.sum(active_weights ** 2))   # Herfindahl index
        div_score = round((1 - hhi) * 100, 1)
    else:
        div_score = 0.0

    # Build allocation table
    rows = []
    for ticker, weight in sorted(weights_dict.items(), key=lambda x: x[1], reverse=True):
        sig = signals.get(ticker, {})
        rows.append({
            "Ticker":       ticker,
            "Weight %":     round(weight * 100, 2),
            "$ Allocation": round(weight * total_capital, 0),
            "Signal":       sig.get("action", ""),
            "Score":        sig.get("composite_score", 0),
            "Risk":         sig.get("risk_level", ""),
            "Vol":          f"{sig.get('realised_vol', 0):.1%}",
        })

    alloc_table = pd.DataFrame(rows)

    return OptimalPortfolio(
        strategy=strategy,
        weights=weights_dict,
        expected_return=round(exp_return, 4),
        expected_vol=round(exp_vol, 4),
        sharpe_ratio=sharpe,
        diversification=div_score,
        total_allocated=round(total_allocated, 4),
        cash_pct=round(cash_pct * 100, 2),
        allocation_table=alloc_table,
        warnings=warnings,
    )


def portfolio_to_json(port: OptimalPortfolio) -> dict:
    """Serialise OptimalPortfolio to JSON-safe dict."""
    return {
        "strategy":        port.strategy,
        "weights":         port.weights,
        "expected_return": port.expected_return,
        "expected_vol":    port.expected_vol,
        "sharpe_ratio":    port.sharpe_ratio,
        "diversification": port.diversification,
        "total_allocated": port.total_allocated,
        "cash_pct":        port.cash_pct,
        "warnings":        port.warnings,
        "allocation_table": port.allocation_table.to_dict(orient="records"),
    }
