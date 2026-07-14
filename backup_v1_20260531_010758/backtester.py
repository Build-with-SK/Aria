"""
backtester.py  —  v2 (hardened)
================================
Vectorised backtester for the signal engine.

Fixes vs v1
-----------
[BUG]  "Close" column guard fixed — operator-precedence error made it always False.
         was:  "Close" in df.columns is False   →  Python chains: always evaluates False
         now:  "Close" not in df.columns
[BUG]  Short PnL completely rewritten. v1's margin simulation inflated returns.
         Now uses clean notional-based cash mechanics (same model as long side).
[BUG]  Entry uses Open[i] when available instead of same-bar Close[i].
         Executing on the same bar the signal fires is a mild lookahead bias.

New defences
------------
[NEW]  Fractional position sizing  — risk_per_trade controls capital per trade (default 50%).
       Prevents the original all-in allocation that wiped the portfolio on a single stop.
[NEW]  Per-trade stop-loss         — hard exit when unrealised loss > stop_loss_pct (default 5%).
[NEW]  Max drawdown circuit-breaker — halts new entries when portfolio DD > max_drawdown_limit.
[NEW]  Trade.exit_reason           — "signal" | "stop_loss" | "max_drawdown" | "end_of_data"
[NEW]  Open positions force-closed at end of simulation so metrics include all trades.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from src.backtesting.metrics import compute_all_metrics

logger = logging.getLogger(__name__)


# ===========================================================================
# Output structures
# ===========================================================================

@dataclass
class Trade:
    """Single trade record."""
    ticker:       str
    entry_date:   str
    exit_date:    str
    entry_price:  float
    exit_price:   float
    direction:    str        # "long" | "short"
    pnl_pct:      float      # Net return after costs
    signal_score: float      # Score at entry
    exit_reason:  str = "signal"  # "signal" | "stop_loss" | "max_drawdown" | "end_of_data"


@dataclass
class BacktestResult:
    """Full backtest output for one asset."""
    ticker:          str
    strategy_name:   str

    equity_curve:    pd.Series
    benchmark_curve: pd.Series

    trades:          List[Trade]
    trade_returns:   pd.Series

    metrics:         dict

    bull_metrics:    Optional[dict] = None
    bear_metrics:    Optional[dict] = None

    n_data_points:   int = 0
    warning:         str = ""


# ===========================================================================
# Signal recalculation — vectorised, no lookahead
# ===========================================================================

def _compute_historical_scores(df: pd.DataFrame) -> pd.Series:
    """
    Compute composite signal score for every historical row.
    Same logic as the live engine, applied vectorised to avoid lookahead.
    Returns a Series of scores in [-100, 100].
    """
    scores = pd.Series(0.0, index=df.index)

    # Trend (30%)
    if all(c in df.columns for c in ["dist_sma_20", "dist_sma_50", "dist_sma_200"]):
        trend = (
            df["dist_sma_20"].clip(-0.1, 0.1)  * 300 * 0.30 +
            df["dist_sma_50"].clip(-0.1, 0.1)  * 200 * 0.30 +
            df["dist_sma_200"].clip(-0.1, 0.1) * 150 * 0.30
        ).clip(-30, 30)
        scores += trend

    # Momentum (25%)
    if "rsi" in df.columns:
        scores += ((df["rsi"].fillna(50) - 50) * 1.2).clip(-35, 35) * 0.25

    if "macd_histogram" in df.columns:
        scores += np.sign(df["macd_histogram"].fillna(0)) * 5 * 0.25

    # Regime (20%)
    if "bull_regime" in df.columns:
        regime_score = (
            df["bull_regime"].fillna(0) * 40
            + df.get("risk_on",  pd.Series(0, index=df.index)).fillna(0) * 20
            - df["bear_regime"].fillna(0) * 40
            - df.get("risk_off", pd.Series(0, index=df.index)).fillna(0) * 20
        ) * 0.20
        scores += regime_score

    return scores.clip(-100, 100)


# ===========================================================================
# Main backtester
# ===========================================================================

def run_backtest(
    ticker:              str,
    df:                  pd.DataFrame,
    initial_capital:     float = 100_000,
    commission_pct:      float = 0.001,
    slippage_pct:        float = 0.0005,
    signal_threshold:    float = 20.0,
    allow_short:         bool  = False,
    risk_per_trade:      float = 0.50,    # Fraction of free cash committed per trade
    stop_loss_pct:       float = 0.05,    # Hard stop: exit if unrealised loss > this
    max_drawdown_limit:  float = 0.20,    # Circuit-breaker: halt entries if DD > this
    strategy_name:       str   = "Signal Strategy",
) -> Optional[BacktestResult]:
    """
    Run a signal-based vectorised backtest on one asset.

    Position mechanics
    ------------------
    Both long and short positions use a notional-based model:
      - At entry:  cash decreases by notional = cash * risk_per_trade
      - Mark-to-market: cash + notional * (1 ± unrealised_pct)
      - At exit:   cash increases by notional * (1 + realised_pct)

    This keeps the maths identical for longs and shorts and avoids
    the double-counting bug in v1's short margin simulation.
    """

    # ── Guard ────────────────────────────────────────────────────────────────
    # BUG FIX v1: was `"Close" in df.columns is False` which is always False.
    if df is None or df.empty or "Close" not in df.columns:
        return None

    if len(df) < 100:
        return BacktestResult(
            ticker=ticker, strategy_name=strategy_name,
            equity_curve=pd.Series([initial_capital]),
            benchmark_curve=pd.Series([initial_capital]),
            trades=[], trade_returns=pd.Series(dtype=float),
            metrics={"n_trades": 0},
            warning=f"Insufficient data: only {len(df)} rows",
        )

    scores = _compute_historical_scores(df)

    close = df["Close"].squeeze()
    if isinstance(close, pd.DataFrame):
        close = close.iloc[:, 0]

    # Use Open for execution if available — more realistic than same-bar Close.
    if "Open" in df.columns:
        opens = df["Open"].squeeze()
        if isinstance(opens, pd.DataFrame):
            opens = opens.iloc[:, 0]
    else:
        opens = close

    cost = commission_pct + slippage_pct   # one-way

    # ── State ─────────────────────────────────────────────────────────────────
    cash         = initial_capital
    peak_equity  = initial_capital
    in_trade     = False
    direction: Optional[str] = None
    entry_price  = 0.0
    notional     = 0.0     # Capital committed to current trade
    entry_date   = None
    entry_score  = 0.0
    halted       = False   # max-drawdown circuit-breaker

    equity_vals: List[float] = []
    trades:      List[Trade] = []
    trade_rets:  List[float] = []

    dates = close.index

    def _date_str(d) -> str:
        return str(d.date()) if hasattr(d, "date") else str(d)

    def _record_trade(exit_px: float, reason: str) -> None:
        """Close the current position, update cash, append trade record."""
        nonlocal cash, in_trade, notional, direction, entry_price

        if direction == "long":
            final_px = exit_px * (1 - cost)
            pnl_pct  = (final_px - entry_price) / entry_price
        else:
            final_px = exit_px * (1 + cost)
            pnl_pct  = (entry_price - final_px) / entry_price

        cash += notional * (1 + pnl_pct)
        trades.append(Trade(
            ticker=ticker,
            entry_date=_date_str(entry_date),
            exit_date=_date_str(today),
            entry_price=round(entry_price, 4),
            exit_price=round(final_px, 4),
            direction=direction,
            pnl_pct=round(pnl_pct, 6),
            signal_score=round(entry_score, 2),
            exit_reason=reason,
        ))
        trade_rets.append(pnl_pct)
        in_trade = False; notional = 0.0; direction = None

    # ── Main loop ─────────────────────────────────────────────────────────────
    for i in range(1, len(dates)):
        today   = dates[i]
        px      = float(close.iloc[i])
        exec_px = float(opens.iloc[i])
        if np.isnan(exec_px):
            exec_px = px

        score = float(scores.iloc[i - 1])   # Yesterday's signal → today's decision

        if np.isnan(px) or np.isnan(score):
            equity_vals.append(cash + notional)
            continue

        # ── Unrealised PnL for current position ───────────────────────────────
        if in_trade:
            if direction == "long":
                unreal_pct = (px - entry_price) / entry_price
            else:
                unreal_pct = (entry_price - px) / entry_price
            portfolio_val = cash + notional * (1 + unreal_pct)
        else:
            portfolio_val = cash

        # ── Max drawdown circuit-breaker ──────────────────────────────────────
        if portfolio_val > peak_equity:
            peak_equity = portfolio_val
        if peak_equity > 0:
            current_dd = (peak_equity - portfolio_val) / peak_equity
        else:
            current_dd = 0.0

        if current_dd >= max_drawdown_limit and not halted:
            logger.warning(
                f"{ticker}: drawdown {current_dd:.1%} ≥ limit {max_drawdown_limit:.0%} — "
                "halting new entries"
            )
            halted = True

        # ── Exit logic ────────────────────────────────────────────────────────
        if in_trade:
            if direction == "long":
                unreal_pct  = (px - entry_price) / entry_price
                signal_flip = score < 0
            else:
                unreal_pct  = (entry_price - px) / entry_price
                signal_flip = score > 0

            stop_hit = unreal_pct <= -stop_loss_pct

            if stop_hit:
                # Stop triggered at close price — most conservative fill
                _record_trade(px, "stop_loss")
            elif signal_flip:
                # Signal reversed — execute at next-bar open (exec_px)
                _record_trade(exec_px, "signal")
            elif halted:
                _record_trade(exec_px, "max_drawdown")

        # ── Entry logic ───────────────────────────────────────────────────────
        if not in_trade and not halted:
            if score >= signal_threshold:
                entry_price = exec_px * (1 + cost)
                notional    = cash * risk_per_trade
                cash       -= notional
                in_trade    = True
                direction   = "long"
                entry_date  = today
                entry_score = score

            elif allow_short and score <= -signal_threshold:
                entry_price = exec_px * (1 - cost)
                notional    = cash * risk_per_trade
                cash       -= notional
                in_trade    = True
                direction   = "short"
                entry_date  = today
                entry_score = score

        # ── Mark-to-market ────────────────────────────────────────────────────
        if in_trade:
            if direction == "long":
                unreal_pct = (px - entry_price) / entry_price
            else:
                unreal_pct = (entry_price - px) / entry_price
            equity_vals.append(cash + notional * (1 + unreal_pct))
        else:
            equity_vals.append(cash)

    # ── Force-close any open position at simulation end ───────────────────────
    if in_trade and len(close) > 1:
        last_px = float(close.iloc[-1])
        today   = dates[-1]
        if not np.isnan(last_px):
            _record_trade(last_px, "end_of_data")

    # ── Build outputs ─────────────────────────────────────────────────────────
    equity_curve    = pd.Series(equity_vals, index=dates[1:])
    first_valid     = close.dropna().iloc[0]
    benchmark_curve = (close / first_valid * initial_capital).iloc[1:]
    trade_returns   = pd.Series(trade_rets)
    metrics         = compute_all_metrics(equity_curve, trade_returns)

    # ── Regime breakdown ──────────────────────────────────────────────────────
    bull_metrics = bear_metrics = None
    if "bull_regime" in df.columns:
        bull_dates  = df[df["bull_regime"] == 1].index
        bear_dates  = df[df["bear_regime"] == 1].index
        bull_equity = equity_curve.reindex(bull_dates).dropna()
        bear_equity = equity_curve.reindex(bear_dates).dropna()
        bull_d_str  = {_date_str(d) for d in bull_dates}
        bear_d_str  = {_date_str(d) for d in bear_dates}
        bull_tr     = [t for t in trades if t.entry_date in bull_d_str]
        bear_tr     = [t for t in trades if t.entry_date in bear_d_str]
        if len(bull_equity) > 10:
            bull_metrics = compute_all_metrics(bull_equity, pd.Series([t.pnl_pct for t in bull_tr]))
        if len(bear_equity) > 10:
            bear_metrics = compute_all_metrics(bear_equity, pd.Series([t.pnl_pct for t in bear_tr]))

    return BacktestResult(
        ticker=ticker, strategy_name=strategy_name,
        equity_curve=equity_curve, benchmark_curve=benchmark_curve,
        trades=trades, trade_returns=trade_returns,
        metrics=metrics, bull_metrics=bull_metrics, bear_metrics=bear_metrics,
        n_data_points=len(df),
    )


# ===========================================================================
# Batch runner
# ===========================================================================

def run_all_backtests(
    featured_data: Dict[str, pd.DataFrame],
    config:        dict,
) -> Dict[str, BacktestResult]:
    """Run backtests for every asset in featured_data."""
    bt_cfg  = config.get("backtest", {})
    results: Dict[str, BacktestResult] = {}

    for ticker, df in featured_data.items():
        if df is None or df.empty:
            continue
        try:
            result = run_backtest(
                ticker=ticker,
                df=df,
                initial_capital=    bt_cfg.get("initial_capital",    100_000),
                commission_pct=     bt_cfg.get("commission_pct",     0.001),
                slippage_pct=       bt_cfg.get("slippage_pct",       0.0005),
                signal_threshold=   bt_cfg.get("signal_threshold",   15),
                risk_per_trade=     bt_cfg.get("risk_per_trade",     0.50),
                stop_loss_pct=      bt_cfg.get("stop_loss_pct",      0.05),
                max_drawdown_limit= bt_cfg.get("max_drawdown_limit", 0.20),
            )
            if result:
                results[ticker] = result
        except Exception as e:
            logger.error(f"Backtest failed for {ticker}: {e}", exc_info=True)

    logger.info(f"Backtests complete for {len(results)} assets")
    return results


def backtests_to_json(results: Dict[str, BacktestResult]) -> dict:
    """Serialise backtest results to JSON-safe dict."""
    out = {}
    for ticker, r in results.items():
        date_str = lambda d: str(d.date()) if hasattr(d, "date") else str(d)
        out[ticker] = {
            "ticker":        r.ticker,
            "strategy_name": r.strategy_name,
            "metrics":       r.metrics,
            "bull_metrics":  r.bull_metrics,
            "bear_metrics":  r.bear_metrics,
            "n_trades":      r.metrics.get("n_trades", 0),
            "n_data_points": r.n_data_points,
            "warning":       r.warning,
            "trades": [
                {
                    "entry_date":   t.entry_date,
                    "exit_date":    t.exit_date,
                    "entry_price":  t.entry_price,
                    "exit_price":   t.exit_price,
                    "direction":    t.direction,
                    "pnl_pct":      t.pnl_pct,
                    "signal_score": t.signal_score,
                    "exit_reason":  t.exit_reason,
                }
                for t in r.trades[-50:]
            ],
            "equity_curve":  [round(v, 2) for v in r.equity_curve.tolist()],
            "equity_dates":  [date_str(d) for d in r.equity_curve.index],
        }
    return out
