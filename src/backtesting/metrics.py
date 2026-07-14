"""
metrics.py
==========
Performance metric calculations for backtesting.

All functions take an equity curve (Series of portfolio values)
or a returns Series (daily returns).

Designed to be standalone — no dependencies on other project modules.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def total_return(equity_curve: pd.Series) -> float:
    """Total return from first to last portfolio value."""
    if equity_curve.empty or equity_curve.iloc[0] == 0:
        return 0.0
    return float((equity_curve.iloc[-1] / equity_curve.iloc[0]) - 1)


def cagr(equity_curve: pd.Series) -> float:
    """
    Compound Annual Growth Rate.
    Assumes daily frequency (252 trading days per year).
    """
    if equity_curve.empty or equity_curve.iloc[0] == 0:
        return 0.0
    n_years = len(equity_curve) / 252
    if n_years <= 0:
        return 0.0
    return float((equity_curve.iloc[-1] / equity_curve.iloc[0]) ** (1 / n_years) - 1)


def sharpe_ratio(returns: pd.Series, risk_free_rate: float = 0.045) -> float:
    """
    Annualised Sharpe ratio.
    risk_free_rate is annual — divided by 252 for daily.
    """
    if returns.empty or returns.std() == 0:
        return 0.0
    daily_rf = risk_free_rate / 252
    excess   = returns - daily_rf
    return float(excess.mean() / excess.std() * np.sqrt(252))


def sortino_ratio(returns: pd.Series, risk_free_rate: float = 0.045) -> float:
    """
    Sortino ratio — like Sharpe but only penalises downside volatility.
    Better for asymmetric return distributions.
    """
    if returns.empty:
        return 0.0
    daily_rf       = risk_free_rate / 252
    excess         = returns - daily_rf
    downside_ret   = excess[excess < 0]
    downside_std   = downside_ret.std()
    if downside_std == 0:
        return 0.0
    return float(excess.mean() / downside_std * np.sqrt(252))


def max_drawdown(equity_curve: pd.Series) -> float:
    """
    Maximum peak-to-trough drawdown as a negative percentage.
    Example: -0.25 means a 25% drawdown from peak.
    """
    if equity_curve.empty:
        return 0.0
    rolling_peak = equity_curve.cummax()
    drawdowns    = (equity_curve - rolling_peak) / rolling_peak
    return float(drawdowns.min())


def win_rate(trade_returns: pd.Series) -> float:
    """Fraction of trades that were profitable (return > 0)."""
    if trade_returns.empty:
        return 0.0
    return float((trade_returns > 0).sum() / len(trade_returns))


def profit_factor(trade_returns: pd.Series) -> float:
    """
    Profit factor = gross profit / gross loss.
    > 1 = strategy is net profitable.
    """
    gains  = trade_returns[trade_returns > 0].sum()
    losses = trade_returns[trade_returns < 0].abs().sum()
    if losses == 0:
        return float("inf") if gains > 0 else 0.0
    return float(gains / losses)


def compute_all_metrics(
    equity_curve:  pd.Series,
    trade_returns: pd.Series,
    risk_free_rate: float = 0.045,
) -> dict:
    """
    Compute and return all performance metrics in one dict.

    Parameters
    ----------
    equity_curve  : Portfolio value series (cumulative)
    trade_returns : Series of individual trade returns
    risk_free_rate: Annual risk-free rate

    Returns
    -------
    Dict with all metrics rounded to 4 decimal places
    """
    daily_returns = equity_curve.pct_change().dropna()

    return {
        "total_return":    round(total_return(equity_curve), 4),
        "cagr":            round(cagr(equity_curve), 4),
        "sharpe_ratio":    round(sharpe_ratio(daily_returns, risk_free_rate), 4),
        "sortino_ratio":   round(sortino_ratio(daily_returns, risk_free_rate), 4),
        "max_drawdown":    round(max_drawdown(equity_curve), 4),
        "win_rate":        round(win_rate(trade_returns), 4),
        "profit_factor":   round(profit_factor(trade_returns), 4),
        "n_trades":        int(len(trade_returns)),
        "avg_trade_return": round(float(trade_returns.mean()), 4) if not trade_returns.empty else 0.0,
        "best_trade":      round(float(trade_returns.max()), 4) if not trade_returns.empty else 0.0,
        "worst_trade":     round(float(trade_returns.min()), 4) if not trade_returns.empty else 0.0,
    }
