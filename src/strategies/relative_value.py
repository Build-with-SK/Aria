# src/strategies/relative_value.py
"""
Relative Value / Statistical Arbitrage
Pairs trading via cointegration (Engle-Granger).
Z-score spread entry/exit, dynamic hedge ratio via rolling OLS,
OU half-life filter.
"""

from __future__ import annotations
import logging
from dataclasses import dataclass, field
from itertools import combinations
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class PairsResult:
    ticker_a: str
    ticker_b: str
    hedge_ratio: float          # shares of B per share of A
    spread_zscore: float        # current z-score
    half_life_days: float
    coint_pvalue: float
    signal: str                 # "long_spread", "short_spread", "flat"
    entry_zscore: float
    exit_zscore: float
    backtest_pnl: float
    notes: str

    def _to_json(self) -> dict:
        return {
            "pair": f"{self.ticker_a}/{self.ticker_b}",
            "hedge_ratio": round(self.hedge_ratio, 4),
            "spread_zscore": round(self.spread_zscore, 3),
            "half_life_days": round(self.half_life_days, 1),
            "coint_pvalue": round(self.coint_pvalue, 4),
            "signal": self.signal,
            "entry_zscore": self.entry_zscore,
            "exit_zscore": self.exit_zscore,
            "backtest_pnl": round(self.backtest_pnl, 2),
            "notes": self.notes,
        }


def _safe_import_statsmodels():
    try:
        from statsmodels.tsa.stattools import coint
        from statsmodels.regression.linear_model import OLS
        from statsmodels.tools import add_constant
        return coint, OLS, add_constant
    except ImportError:
        return None, None, None


def _rolling_hedge_ratio(price_a: pd.Series, price_b: pd.Series, window: int = 60) -> pd.Series:
    """Rolling OLS hedge ratio: regress B on A."""
    _, OLS_cls, add_const = _safe_import_statsmodels()
    ratios = pd.Series(index=price_a.index, dtype=float)
    for i in range(window, len(price_a)):
        y = price_b.iloc[i - window:i].values
        x = price_a.iloc[i - window:i].values
        try:
            if OLS_cls is not None:
                X = add_const(x)
                res = OLS_cls(y, X).fit()
                ratios.iloc[i] = res.params[1]
            else:
                # fallback: numpy lstsq
                X = np.column_stack([x, np.ones(len(x))])
                sol, *_ = np.linalg.lstsq(X, y, rcond=None)
                ratios.iloc[i] = sol[0]
        except Exception:
            ratios.iloc[i] = 1.0
    return ratios.ffill().bfill()


def _compute_half_life(spread: pd.Series) -> float:
    """OU half-life via OLS on spread lagged regression."""
    try:
        spread_lag = spread.shift(1).dropna()
        spread_diff = spread.diff().dropna()
        aligned = pd.concat([spread_diff, spread_lag], axis=1).dropna()
        aligned.columns = ["diff", "lag"]
        X = aligned["lag"].values.reshape(-1, 1)
        y = aligned["diff"].values
        X_c = np.column_stack([X, np.ones(len(X))])
        sol, *_ = np.linalg.lstsq(X_c, y, rcond=None)
        lam = sol[0]
        if lam >= 0:
            return 999.0  # not mean-reverting
        return float(-np.log(2) / lam)
    except Exception:
        return 999.0


def _engle_granger_pvalue(price_a: np.ndarray, price_b: np.ndarray) -> float:
    """Returns p-value of EG cointegration test."""
    coint_fn, _, _ = _safe_import_statsmodels()
    if coint_fn is None:
        # fallback: ADF on spread via numpy
        return 0.5  # neutral
    try:
        _, pvalue, _ = coint_fn(price_a, price_b)
        return float(pvalue)
    except Exception:
        return 1.0


def _backtest_pair(
    price_a: pd.Series,
    price_b: pd.Series,
    hedge_ratios: pd.Series,
    entry_z: float = 2.0,
    exit_z: float = 0.5,
) -> float:
    """Simple vectorised backtest. Returns total PnL ($)."""
    spread = price_a - hedge_ratios * price_b
    spread_mean = spread.rolling(60).mean()
    spread_std = spread.rolling(60).std().replace(0, np.nan)
    zscore = (spread - spread_mean) / spread_std

    position = 0  # +1 long spread, -1 short spread
    pnl = 0.0
    entry_price_a = entry_price_b = 0.0

    for i in range(1, len(zscore)):
        z = zscore.iloc[i]
        if np.isnan(z):
            continue
        hr = hedge_ratios.iloc[i]

        if position == 0:
            if z < -entry_z:
                position = 1
                entry_price_a = price_a.iloc[i]
                entry_price_b = price_b.iloc[i]
            elif z > entry_z:
                position = -1
                entry_price_a = price_a.iloc[i]
                entry_price_b = price_b.iloc[i]
        elif position == 1 and abs(z) < exit_z:
            pnl += (price_a.iloc[i] - entry_price_a) - hr * (price_b.iloc[i] - entry_price_b)
            position = 0
        elif position == -1 and abs(z) < exit_z:
            pnl += -(price_a.iloc[i] - entry_price_a) + hr * (price_b.iloc[i] - entry_price_b)
            position = 0

    return float(pnl)


def run_relative_value(
    featured_data: Dict[str, pd.DataFrame],
    config: dict,
) -> List[PairsResult]:
    """
    Scan all within-asset-class pairs for cointegration.
    Returns list of tradeable PairsResult sorted by |z-score|.
    """
    cfg = config.get("strategies", {}).get("relative_value", {})
    entry_z = float(cfg.get("entry_zscore", 2.0))
    exit_z = float(cfg.get("exit_zscore", 0.5))
    min_half_life = float(cfg.get("min_half_life", 5))
    max_half_life = float(cfg.get("max_half_life", 60))
    coint_pvalue_thresh = float(cfg.get("coint_pvalue_thresh", 0.05))
    max_pairs = int(cfg.get("max_pairs_scanned", 50))
    ols_window = int(cfg.get("ols_window", 60))

    # Only use equities (skip FX, crypto, futures)
    skip_suffixes = ["=X", "-USD", "=F", "-F"]
    skip_tickers = {"SPY", "QQQ", "IWM", "DIA", "GLD", "SLV", "USO",
                    "TLT", "IEF", "AGG", "HYG", "LQD", "JNK", "VCIT",
                    "BKLN", "SJNK", "SHYG", "CWB"}

    equity_tickers = [
        t for t, df in featured_data.items()
        if df is not None and not df.empty
        and not any(t.endswith(s) for s in skip_suffixes)
        and t not in skip_tickers
        and len(df) >= 252
    ]

    results: List[PairsResult] = []
    pair_candidates = list(combinations(equity_tickers, 2))[:max_pairs]

    for (t_a, t_b) in pair_candidates:
        try:
            df_a = featured_data[t_a]["Close"].dropna()
            df_b = featured_data[t_b]["Close"].dropna()

            # Align
            combined = pd.concat([df_a, df_b], axis=1).dropna()
            if len(combined) < 252:
                continue
            combined.columns = ["a", "b"]

            # Cointegration test
            pval = _engle_granger_pvalue(combined["a"].values, combined["b"].values)
            if pval > coint_pvalue_thresh:
                continue

            # Hedge ratio (rolling OLS)
            hedge_series = _rolling_hedge_ratio(combined["a"], combined["b"], window=ols_window)

            # Current spread z-score
            spread = combined["a"] - hedge_series * combined["b"]
            lookback = min(252, len(spread))
            spread_recent = spread.iloc[-lookback:]
            std = spread_recent.std()
            mean = spread_recent.mean()
            if std == 0:
                continue
            current_z = float((spread.iloc[-1] - mean) / std)

            # Half-life
            hl = _compute_half_life(spread)
            if not (min_half_life <= hl <= max_half_life):
                continue

            # Signal
            if current_z < -entry_z:
                signal = "long_spread"   # long A, short B
            elif current_z > entry_z:
                signal = "short_spread"  # short A, long B
            elif abs(current_z) < exit_z:
                signal = "flat"
            else:
                signal = "watching"

            # Backtest
            bt_pnl = _backtest_pair(combined["a"], combined["b"], hedge_series, entry_z, exit_z)

            results.append(PairsResult(
                ticker_a=t_a,
                ticker_b=t_b,
                hedge_ratio=round(float(hedge_series.iloc[-1]), 4),
                spread_zscore=round(current_z, 3),
                half_life_days=round(hl, 1),
                coint_pvalue=round(pval, 4),
                signal=signal,
                entry_zscore=entry_z,
                exit_zscore=exit_z,
                backtest_pnl=round(bt_pnl, 2),
                notes=f"EG p={pval:.4f}, HL={hl:.1f}d",
            ))

        except Exception as e:
            logger.debug(f"Pair {t_a}/{t_b} failed: {e}")
            continue

    # Sort by abs z-score descending (most actionable first)
    results.sort(key=lambda r: abs(r.spread_zscore), reverse=True)

    if not results:
        logger.info("No cointegrated pairs found meeting criteria.")

    return results
