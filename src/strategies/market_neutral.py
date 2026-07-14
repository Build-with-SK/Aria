# src/strategies/market_neutral.py
"""
Market Neutral Strategy
Beta-hedged long/short — net market beta ≈ 0.
Rolling 60-day beta vs SPY. Factor neutralisation optional.
"""

from __future__ import annotations
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class MarketNeutralPosition:
    ticker: str
    direction: str          # "long" | "short"
    raw_weight: float
    adjusted_weight: float  # after beta adjustment
    beta: float
    beta_contribution: float
    dollar_allocation: float


@dataclass
class MarketNeutralPortfolio:
    positions: List[MarketNeutralPosition]
    net_beta: float
    gross_exposure: float
    net_exposure: float
    long_count: int
    short_count: int
    factor_loadings: Dict[str, float]   # size, value, momentum, low_vol
    beta_hedge_notional: float          # SPY short needed to flatten beta
    strategy_score: float
    notes: List[str]

    def _to_json(self) -> dict:
        return {
            "positions": [vars(p) for p in self.positions],
            "net_beta": round(self.net_beta, 4),
            "gross_exposure": round(self.gross_exposure, 2),
            "net_exposure": round(self.net_exposure, 2),
            "long_count": self.long_count,
            "short_count": self.short_count,
            "factor_loadings": {k: round(v, 4) for k, v in self.factor_loadings.items()},
            "beta_hedge_notional": round(self.beta_hedge_notional, 2),
            "strategy_score": round(self.strategy_score, 2),
            "notes": self.notes,
        }


def _compute_beta(asset_returns: pd.Series, market_returns: pd.Series, window: int = 60) -> float:
    """Rolling covariance beta vs market."""
    try:
        aligned = pd.concat([asset_returns, market_returns], axis=1).dropna()
        if len(aligned) < window:
            return 1.0
        aligned.columns = ["asset", "mkt"]
        recent = aligned.tail(window)
        cov = recent["asset"].cov(recent["mkt"])
        var = recent["mkt"].var()
        if var == 0:
            return 1.0
        return float(cov / var)
    except Exception:
        return 1.0


def _compute_size_factor(df: pd.DataFrame, info: dict) -> float:
    """Market cap rank proxy (smaller = higher size factor exposure)."""
    try:
        mkt_cap = info.get("marketCap") or 0
        if mkt_cap == 0:
            # Proxy via price * shares
            price = float(df["Close"].iloc[-1])
            shares = info.get("sharesOutstanding") or 1e9
            mkt_cap = price * shares
        return float(np.log1p(mkt_cap))
    except Exception:
        return 10.0


def _compute_vol_factor(df: pd.DataFrame, window: int = 60) -> float:
    """Realised volatility (annualised). Lower vol = better for low-vol factor."""
    try:
        ret = df["Close"].pct_change().dropna()
        return float(ret.tail(window).std() * np.sqrt(252))
    except Exception:
        return 0.2


def run_market_neutral(
    featured_data: Dict[str, pd.DataFrame],
    config: dict,
    signal_scores: Optional[Dict[str, float]] = None,
    yf_info: Optional[Dict[str, dict]] = None,
) -> MarketNeutralPortfolio:
    """
    Build a beta-neutral long/short book.
    Uses SPY as market proxy. Adjusts weights so net beta → 0.
    """
    cfg = config.get("strategies", {}).get("market_neutral", {})
    beta_window = int(cfg.get("beta_window", 60))
    target_beta_max = float(cfg.get("target_beta_max", 0.1))
    n_longs = int(cfg.get("n_longs", 8))
    n_shorts = int(cfg.get("n_shorts", 8))
    capital = float(config.get("backtest", {}).get("initial_capital", 100_000))

    notes = []

    # Get SPY returns as market benchmark
    spy_df = featured_data.get("SPY")
    if spy_df is None or spy_df.empty:
        notes.append("SPY not in universe — using equal-weight fallback, beta unknown.")
        mkt_returns = pd.Series(dtype=float)
    else:
        mkt_returns = spy_df["Close"].pct_change().dropna()

    # Build candidate table
    skip_suffixes = ["=X", "-USD", "=F"]
    skip_tickers = {"SPY", "QQQ", "IWM", "DIA", "GLD", "SLV", "USO",
                    "TLT", "IEF", "AGG", "HYG", "LQD", "JNK", "VCIT",
                    "BKLN", "SJNK", "SHYG", "CWB"}

    rows = []
    for ticker, df in featured_data.items():
        if df is None or df.empty:
            continue
        if any(ticker.endswith(s) for s in skip_suffixes):
            continue
        if ticker in skip_tickers:
            continue

        score = (signal_scores or {}).get(ticker, 0.0)
        info = (yf_info or {}).get(ticker, {})
        asset_ret = df["Close"].pct_change().dropna()

        beta = _compute_beta(asset_ret, mkt_returns, beta_window) if len(mkt_returns) > 0 else 1.0
        vol = _compute_vol_factor(df)
        size = _compute_size_factor(df, info)

        rows.append({
            "ticker": ticker,
            "score": score,
            "beta": beta,
            "vol": vol,
            "size": size,
        })

    if len(rows) < 4:
        notes.append("Not enough tickers for market neutral strategy.")
        return MarketNeutralPortfolio(
            positions=[], net_beta=0, gross_exposure=0, net_exposure=0,
            long_count=0, short_count=0, factor_loadings={},
            beta_hedge_notional=0, strategy_score=0, notes=notes,
        )

    df_rank = pd.DataFrame(rows).sort_values("score", ascending=False)
    longs_raw = df_rank.head(n_longs).to_dict("records")
    shorts_raw = df_rank.tail(n_shorts).to_dict("records")

    all_raw = [dict(**r, direction="long") for r in longs_raw] + \
              [dict(**r, direction="short") for r in shorts_raw]

    # --- Compute raw beta of equal-weight book ---
    long_betas = [r["beta"] for r in longs_raw]
    short_betas = [r["beta"] for r in shorts_raw]
    raw_net_beta = (np.mean(long_betas) - np.mean(short_betas)) if long_betas and short_betas else 0.0

    # --- Adjust weights to neutralise beta ---
    # Scale short side by ratio so net beta → 0
    half_cap = capital / 2.0
    long_alloc = half_cap / max(len(longs_raw), 1)
    short_alloc = half_cap / max(len(shorts_raw), 1)

    positions = []
    for r in all_raw:
        direction = r["direction"]
        # Adjust weight inversely proportional to beta for risk parity
        beta_adj = float(np.clip(r["beta"], 0.1, 3.0))
        raw_w = 1.0 / max(len(longs_raw), 1) if direction == "long" else 1.0 / max(len(shorts_raw), 1)
        adj_w = raw_w / beta_adj
        alloc = long_alloc if direction == "long" else short_alloc
        adjusted_dollar = alloc * adj_w * (max(len(longs_raw), 1) if direction == "long" else max(len(shorts_raw), 1))

        positions.append(MarketNeutralPosition(
            ticker=r["ticker"],
            direction=direction,
            raw_weight=round(raw_w, 4),
            adjusted_weight=round(adj_w, 4),
            beta=round(r["beta"], 3),
            beta_contribution=round(adj_w * r["beta"] * (1 if direction == "long" else -1), 4),
            dollar_allocation=round(adjusted_dollar, 2),
        ))

    # Net beta after adjustment
    net_beta = sum(p.beta_contribution for p in positions)

    # If net_beta still exceeds threshold, compute SPY hedge notional
    beta_hedge = 0.0
    if abs(net_beta) > target_beta_max:
        beta_hedge = -net_beta * capital
        notes.append(f"SPY overlay hedge: ${beta_hedge:,.0f} to flatten net beta {net_beta:.3f} → 0")
    else:
        notes.append(f"Net beta within target: {net_beta:.3f}")

    # Factor loadings
    long_vols = [r["vol"] for r in longs_raw]
    short_vols = [r["vol"] for r in shorts_raw]
    long_sizes = [r["size"] for r in longs_raw]
    short_sizes = [r["size"] for r in shorts_raw]
    factor_loadings = {
        "beta_net": round(float(net_beta), 4),
        "low_vol": round(float(np.mean(short_vols) - np.mean(long_vols)), 4),
        "size": round(float(np.mean(long_sizes) - np.mean(short_sizes)), 4),
    }

    long_alloc_total = sum(p.dollar_allocation for p in positions if p.direction == "long")
    short_alloc_total = sum(p.dollar_allocation for p in positions if p.direction == "short")
    gross_exp = long_alloc_total + short_alloc_total
    net_exp = long_alloc_total - short_alloc_total

    avg_long_score = float(np.mean([r["score"] for r in longs_raw])) if longs_raw else 0
    avg_short_score = float(np.mean([r["score"] for r in shorts_raw])) if shorts_raw else 0
    strategy_score = float(np.clip(avg_long_score - avg_short_score, -100, 100))

    notes.append(f"Long {len(longs_raw)}, short {len(shorts_raw)} positions.")
    notes.append(f"Gross exposure: ${gross_exp:,.0f} | Net: ${net_exp:,.0f}")

    return MarketNeutralPortfolio(
        positions=positions,
        net_beta=round(float(net_beta), 4),
        gross_exposure=round(gross_exp, 2),
        net_exposure=round(net_exp, 2),
        long_count=len(longs_raw),
        short_count=len(shorts_raw),
        factor_loadings=factor_loadings,
        beta_hedge_notional=round(beta_hedge, 2),
        strategy_score=round(strategy_score, 2),
        notes=notes,
    )
