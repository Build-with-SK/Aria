"""
metrics.py
==========
Performance metric calculations for backtesting.

All functions take an equity curve (Series of portfolio values)
or a returns Series (daily returns).

Designed to be standalone — no dependencies on other project modules.

Annualisation (review QA-02): the number of periods a year depends on the bar
size and the instrument's calendar — 252 exchange days, ~260 FX days, 365
crypto days, times bars per session day for intraday bars. Callers pass
`periods_per_year`; `None` (unknown frequency or calendar) means CAGR, Sharpe
and Sortino are not annualised and are reported as None, never guessed.
"""

from __future__ import annotations

import re

import numpy as np
import pandas as pd

TRADING_DAYS_PER_YEAR = {"EXCHANGE": 252.0, "FX": 260.0, "CRYPTO": 365.0}
SESSION_HOURS_PER_DAY = {"EXCHANGE": 6.5, "FX": 24.0, "CRYPTO": 24.0}


def calendar_for_symbol(symbol: str) -> str:
    """Calendar from yfinance symbol conventions: "=X" is spot FX, a "-USD"-style
    pair is crypto, and anything else trades on an exchange calendar."""
    s = str(symbol).upper()
    if s.endswith("=X"):
        return "FX"
    if re.search(r"-(USD|USDT|USDC|EUR|GBP|BTC|ETH)$", s):
        return "CRYPTO"
    return "EXCHANGE"


def periods_per_year(index, calendar: str | None) -> float | None:
    """Bars a year for a series with this timestamp index on this calendar.

    None when the calendar is unknown, the index has no timestamps, or the bar
    size is none of intraday (shorter than a session), daily, weekly or monthly.
    """
    days = TRADING_DAYS_PER_YEAR.get(calendar)
    if days is None or not isinstance(index, pd.DatetimeIndex) or len(index) < 2:
        return None
    step = pd.Series(index).diff().dropna().median()
    hours = step / pd.Timedelta(hours=1)
    session = SESSION_HOURS_PER_DAY[calendar]
    if 0 < hours <= session and hours != 24:
        return days * session / hours
    if hours == 24:
        return days
    if 6 * 24 <= hours <= 8 * 24:
        return 52.0
    if 28 * 24 <= hours <= 31 * 24:
        return 12.0
    return None


def total_return(equity_curve: pd.Series) -> float:
    """Total return from first to last portfolio value."""
    if equity_curve.empty or equity_curve.iloc[0] == 0:
        return 0.0
    return float((equity_curve.iloc[-1] / equity_curve.iloc[0]) - 1)


def cagr(equity_curve: pd.Series, *, periods_per_year: float | None) -> float | None:
    """
    Compound Annual Growth Rate over the series' return periods.
    None when the frequency is unknown.
    """
    if equity_curve.empty or equity_curve.iloc[0] == 0:
        return 0.0
    if not periods_per_year:
        return None
    n_years = (len(equity_curve) - 1) / periods_per_year
    if n_years <= 0:
        return 0.0
    return float((equity_curve.iloc[-1] / equity_curve.iloc[0]) ** (1 / n_years) - 1)


def sharpe_ratio(returns: pd.Series, risk_free_rate: float = 0.045, *,
                 periods_per_year: float | None) -> float | None:
    """
    Annualised Sharpe ratio. risk_free_rate is annual, spread over the periods.
    None when the frequency is unknown.
    """
    if not periods_per_year:
        return None
    if returns.empty or returns.std() == 0:
        return 0.0
    period_rf = risk_free_rate / periods_per_year
    excess    = returns - period_rf
    return float(excess.mean() / excess.std() * np.sqrt(periods_per_year))


def sortino_ratio(returns: pd.Series, risk_free_rate: float = 0.045, *,
                  periods_per_year: float | None) -> float | None:
    """
    Sortino ratio — like Sharpe but only penalises downside volatility.
    Better for asymmetric return distributions. None when the frequency is unknown.
    """
    if not periods_per_year:
        return None
    if returns.empty:
        return 0.0
    period_rf      = risk_free_rate / periods_per_year
    excess         = returns - period_rf
    downside_ret   = excess[excess < 0]
    downside_std   = downside_ret.std()
    if downside_std == 0:
        return 0.0
    return float(excess.mean() / downside_std * np.sqrt(periods_per_year))


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
    *,
    periods_per_year: float | None,
) -> dict:
    """
    Compute and return all performance metrics in one dict.

    Parameters
    ----------
    equity_curve  : Portfolio value series (cumulative)
    trade_returns : Series of individual trade returns
    risk_free_rate: Annual risk-free rate
    periods_per_year: Bars a year (see `periods_per_year()`); None leaves
                      CAGR, Sharpe and Sortino unannualised (None)

    Returns
    -------
    Dict with all metrics rounded to 4 decimal places
    """
    period_returns = equity_curve.pct_change().dropna()

    def rounded(value):
        return None if value is None else round(value, 4)

    return {
        "total_return":    round(total_return(equity_curve), 4),
        "cagr":            rounded(cagr(equity_curve, periods_per_year=periods_per_year)),
        "sharpe_ratio":    rounded(sharpe_ratio(period_returns, risk_free_rate,
                                                periods_per_year=periods_per_year)),
        "sortino_ratio":   rounded(sortino_ratio(period_returns, risk_free_rate,
                                                 periods_per_year=periods_per_year)),
        "periods_per_year": periods_per_year,
        "max_drawdown":    round(max_drawdown(equity_curve), 4),
        "win_rate":        round(win_rate(trade_returns), 4),
        "profit_factor":   round(profit_factor(trade_returns), 4),
        "n_trades":        int(len(trade_returns)),
        "avg_trade_return": round(float(trade_returns.mean()), 4) if not trade_returns.empty else 0.0,
        "best_trade":      round(float(trade_returns.max()), 4) if not trade_returns.empty else 0.0,
        "worst_trade":     round(float(trade_returns.min()), 4) if not trade_returns.empty else 0.0,
    }
