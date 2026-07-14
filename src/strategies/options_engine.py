# src/strategies/options_engine.py
"""
Options Strategy Engine
Full Black-Scholes Greeks, IV Rank, strategy selection:
Covered call, CSP, vertical spreads, iron condor, protective put, calendar spread.
"""

from __future__ import annotations
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class Greeks:
    delta: float
    gamma: float
    theta: float
    vega: float
    rho: float


@dataclass
class OptionsSignal:
    ticker: str
    spot_price: float
    strategy: str           # "covered_call" | "csp" | "bull_spread" | "bear_spread" |
                            # "iron_condor" | "protective_put" | "calendar_spread"
    iv_rank: float          # 0-100
    iv_current: float
    iv_52w_high: float
    iv_52w_low: float
    realised_vol: float
    recommended_expiry: str
    recommended_strikes: List[float]
    estimated_premium: float
    max_loss: float
    max_gain: float
    breakeven: List[float]
    net_greeks: Greeks
    conviction: float
    rationale: str

    def _to_json(self) -> dict:
        return {
            "ticker": self.ticker,
            "spot_price": round(self.spot_price, 4),
            "strategy": self.strategy,
            "iv_rank": round(self.iv_rank, 1),
            "iv_current": round(self.iv_current, 4),
            "iv_52w_high": round(self.iv_52w_high, 4),
            "iv_52w_low": round(self.iv_52w_low, 4),
            "realised_vol": round(self.realised_vol, 4),
            "recommended_expiry": self.recommended_expiry,
            "recommended_strikes": [round(k, 2) for k in self.recommended_strikes],
            "estimated_premium": round(self.estimated_premium, 4),
            "max_loss": round(self.max_loss, 4),
            "max_gain": round(self.max_gain, 4),
            "breakeven": [round(b, 4) for b in self.breakeven],
            "net_greeks": vars(self.net_greeks),
            "conviction": round(self.conviction, 1),
            "rationale": self.rationale,
        }


# ---------------------------------------------------------------------------
# Black-Scholes core
# ---------------------------------------------------------------------------

def _d1(S: float, K: float, T: float, r: float, sigma: float) -> float:
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return 0.0
    return (np.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * np.sqrt(T))


def _d2(S: float, K: float, T: float, r: float, sigma: float) -> float:
    return _d1(S, K, T, r, sigma) - sigma * np.sqrt(T)


def _bs_price(S: float, K: float, T: float, r: float, sigma: float, option_type: str = "call") -> float:
    try:
        from scipy.stats import norm
    except ImportError:
        return max(S - K, 0) if option_type == "call" else max(K - S, 0)
    try:
        if T <= 0:
            return max(S - K, 0) if option_type == "call" else max(K - S, 0)
        d1 = _d1(S, K, T, r, sigma)
        d2 = _d2(S, K, T, r, sigma)
        if option_type == "call":
            return float(S * norm.cdf(d1) - K * np.exp(-r * T) * norm.cdf(d2))
        else:
            return float(K * np.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1))
    except Exception:
        return 0.0


def _compute_greeks(S: float, K: float, T: float, r: float, sigma: float, option_type: str = "call") -> Greeks:
    try:
        from scipy.stats import norm
        if T <= 0 or sigma <= 0:
            return Greeks(delta=1.0 if option_type == "call" else -1.0, gamma=0, theta=0, vega=0, rho=0)
        d1 = _d1(S, K, T, r, sigma)
        d2 = _d2(S, K, T, r, sigma)
        delta = norm.cdf(d1) if option_type == "call" else norm.cdf(d1) - 1
        gamma = norm.pdf(d1) / (S * sigma * np.sqrt(T))
        # Theta: per-day
        theta_call = (
            -(S * norm.pdf(d1) * sigma) / (2 * np.sqrt(T))
            - r * K * np.exp(-r * T) * norm.cdf(d2)
        ) / 365
        theta = theta_call if option_type == "call" else (theta_call + r * K * np.exp(-r * T) / 365)
        vega = S * norm.pdf(d1) * np.sqrt(T) / 100   # per 1% move in vol
        rho_call = K * T * np.exp(-r * T) * norm.cdf(d2) / 100
        rho = rho_call if option_type == "call" else -K * T * np.exp(-r * T) * norm.cdf(-d2) / 100

        return Greeks(
            delta=round(float(delta), 4),
            gamma=round(float(gamma), 6),
            theta=round(float(theta), 4),
            vega=round(float(vega), 4),
            rho=round(float(rho), 4),
        )
    except Exception:
        return Greeks(delta=0.5, gamma=0, theta=0, vega=0, rho=0)


# ---------------------------------------------------------------------------
# IV Rank
# ---------------------------------------------------------------------------

def _compute_iv_rank(iv_current: float, iv_52w_high: float, iv_52w_low: float) -> float:
    """IV Rank: 0 = at 52w low, 100 = at 52w high."""
    rng = iv_52w_high - iv_52w_low
    if rng <= 0:
        return 50.0
    return float(np.clip((iv_current - iv_52w_low) / rng * 100, 0, 100))


def _get_options_iv(ticker: str, df: pd.DataFrame) -> Tuple[float, float, float]:
    """
    Returns (iv_current, iv_52w_high, iv_52w_low).
    Tries yfinance options chain, falls back to realised vol estimate.
    """
    rv = float(df["Close"].pct_change().dropna().std() * np.sqrt(252))
    rv_high = rv * 1.5
    rv_low = rv * 0.7

    try:
        import yfinance as yf
        stock = yf.Ticker(ticker)
        exps = stock.options
        if not exps:
            return rv * 1.1, rv_high, rv_low

        # Nearest expiry
        chain = stock.option_chain(exps[0])
        calls = chain.calls
        if calls.empty:
            return rv * 1.1, rv_high, rv_low

        spot = float(df["Close"].iloc[-1])
        atm_row = calls.iloc[(calls["strike"] - spot).abs().argsort().iloc[0]]
        iv = float(atm_row.get("impliedVolatility", rv * 1.1))

        if not (0.01 < iv < 5.0):
            iv = rv * 1.1

        # Historical IV proxied by vol of returns over past year
        rets = df["Close"].pct_change().dropna()
        rolling_vol = rets.rolling(21).std() * np.sqrt(252)
        iv_hist_high = float(rolling_vol.tail(252).max()) if len(rolling_vol) >= 252 else rv_high
        iv_hist_low = float(rolling_vol.tail(252).min()) if len(rolling_vol) >= 252 else rv_low

        return iv, max(iv, iv_hist_high), min(iv, iv_hist_low)

    except Exception:
        return rv * 1.1, rv_high, rv_low


def _select_strikes(spot: float, iv: float, T: float, strategy: str) -> Tuple[List[float], str]:
    """Return recommended strikes and expiry label for each strategy."""
    otm_move = spot * iv * np.sqrt(T) * 0.5   # ~0.5-sigma OTM

    if strategy == "covered_call":
        strikes = [round(spot * 1.05, 2)]   # 5% OTM call
        expiry = "~30 DTE"
    elif strategy == "csp":
        strikes = [round(spot * 0.95, 2)]   # 5% OTM put
        expiry = "~30 DTE"
    elif strategy == "bull_spread":
        strikes = [round(spot * 0.98, 2), round(spot * 1.05, 2)]
        expiry = "~45 DTE"
    elif strategy == "bear_spread":
        strikes = [round(spot * 1.02, 2), round(spot * 0.95, 2)]
        expiry = "~45 DTE"
    elif strategy == "iron_condor":
        strikes = [
            round(spot * 0.90, 2),  # long put wing
            round(spot * 0.95, 2),  # short put
            round(spot * 1.05, 2),  # short call
            round(spot * 1.10, 2),  # long call wing
        ]
        expiry = "~30 DTE"
    elif strategy == "protective_put":
        strikes = [round(spot * 0.95, 2)]
        expiry = "~60 DTE"
    elif strategy == "calendar_spread":
        strikes = [round(spot, 2)]
        expiry = "~30 DTE (short) / ~60 DTE (long)"
    else:
        strikes = [round(spot, 2)]
        expiry = "~30 DTE"

    return strikes, expiry


def _strategy_payoff(spot: float, strikes: List[float], premium: float, strategy: str) -> Tuple[float, float, List[float]]:
    """Compute max_gain, max_loss, breakeven for each strategy."""
    if strategy == "covered_call":
        max_gain = (strikes[0] - spot) + premium
        max_loss = spot - premium    # downside = stock loss - premium received
        breakeven = [round(spot - premium, 2)]
    elif strategy == "csp":
        max_gain = premium
        max_loss = strikes[0] - premium
        breakeven = [round(strikes[0] - premium, 2)]
    elif strategy == "bull_spread":
        width = strikes[1] - strikes[0] if len(strikes) > 1 else spot * 0.07
        max_gain = width - premium
        max_loss = premium
        breakeven = [round(strikes[0] + premium, 2)]
    elif strategy == "bear_spread":
        width = strikes[0] - strikes[1] if len(strikes) > 1 else spot * 0.07
        max_gain = width - premium
        max_loss = premium
        breakeven = [round(strikes[0] - premium, 2)]
    elif strategy == "iron_condor":
        if len(strikes) >= 4:
            width = strikes[1] - strikes[0]    # put spread width
            max_gain = premium
            max_loss = width - premium
            breakeven = [round(strikes[1] - premium, 2), round(strikes[2] + premium, 2)]
        else:
            max_gain = premium
            max_loss = spot * 0.10
            breakeven = [spot * 0.95, spot * 1.05]
    elif strategy == "protective_put":
        max_gain = float("inf")
        max_loss = (spot - strikes[0]) + premium
        breakeven = [round(spot + premium, 2)]
    elif strategy == "calendar_spread":
        max_gain = premium * 2   # approximate
        max_loss = premium
        breakeven = [round(strikes[0] * 0.95, 2), round(strikes[0] * 1.05, 2)]
    else:
        max_gain = premium
        max_loss = premium
        breakeven = [spot]

    return round(max_gain, 4), round(abs(max_loss), 4), [round(b, 4) for b in breakeven]


def _analyse_ticker_options(
    ticker: str,
    df: pd.DataFrame,
    cfg: dict,
    signal_score: float = 0.0,
) -> Optional[OptionsSignal]:
    """Build OptionsSignal for a single ticker."""
    try:
        if df is None or df.empty or len(df) < 60:
            return None

        spot = float(df["Close"].iloc[-1])
        rv = float(df["Close"].pct_change().dropna().tail(60).std() * np.sqrt(252))

        iv_current, iv_high, iv_low = _get_options_iv(ticker, df)
        iv_rank = _compute_iv_rank(iv_current, iv_high, iv_low)

        T = 30.0 / 365.0   # ~30 DTE
        r = 0.05

        iv_sell_thresh = float(cfg.get("iv_rank_sell_threshold", 50))
        iv_buy_thresh = float(cfg.get("iv_rank_buy_threshold", 30))

        # Strategy selection
        if iv_rank > iv_sell_thresh:
            # Sell premium
            if signal_score > 20:
                strategy = "covered_call"
                rationale = f"IV rank {iv_rank:.0f} > {iv_sell_thresh} + bullish signal → covered call"
            else:
                strategy = "iron_condor"
                rationale = f"IV rank {iv_rank:.0f} > {iv_sell_thresh} + neutral signal → iron condor"
        elif iv_rank < iv_buy_thresh:
            # Buy premium
            if signal_score > 20:
                strategy = "bull_spread"
                rationale = f"IV rank {iv_rank:.0f} < {iv_buy_thresh} + bullish signal → bull spread"
            elif signal_score < -20:
                strategy = "bear_spread"
                rationale = f"IV rank {iv_rank:.0f} < {iv_buy_thresh} + bearish signal → bear spread"
            else:
                strategy = "calendar_spread"
                rationale = f"IV rank {iv_rank:.0f} < {iv_buy_thresh} + neutral → calendar spread"
        else:
            # Mid IV rank
            if signal_score < -30:
                strategy = "protective_put"
                rationale = f"Bearish signal {signal_score:.0f} + mid IV rank → protective put"
            else:
                strategy = "csp"
                rationale = f"Mid IV rank {iv_rank:.0f} → cash-secured put for premium"

        strikes, expiry = _select_strikes(spot, iv_current, T, strategy)

        # Premium estimate (simplified)
        if strategy in ("covered_call", "csp", "iron_condor"):
            primary_k = strikes[1] if strategy == "iron_condor" and len(strikes) > 1 else strikes[0]
            ptype = "call" if strategy == "covered_call" else "put"
            premium = _bs_price(spot, primary_k, T, r, iv_current, ptype)
        elif strategy in ("bull_spread", "bear_spread"):
            p1 = _bs_price(spot, strikes[0], T, r, iv_current, "call" if strategy == "bull_spread" else "put")
            p2 = _bs_price(spot, strikes[1], T, r, iv_current, "call" if strategy == "bull_spread" else "put")
            premium = abs(p1 - p2)
        elif strategy == "calendar_spread":
            p_short = _bs_price(spot, strikes[0], T, r, iv_current, "call")
            p_long = _bs_price(spot, strikes[0], T * 2, r, iv_current, "call")
            premium = p_long - p_short
        elif strategy == "protective_put":
            premium = _bs_price(spot, strikes[0], T * 2, r, iv_current, "put")
        else:
            premium = _bs_price(spot, strikes[0], T, r, iv_current, "call")

        max_gain, max_loss, breakeven = _strategy_payoff(spot, strikes, premium, strategy)

        # Greeks for primary leg
        k_main = strikes[1] if strategy == "iron_condor" and len(strikes) > 1 else strikes[0]
        ptype_main = "put" if strategy in ("csp", "protective_put", "bear_spread") else "call"
        greeks = _compute_greeks(spot, k_main, T, r, iv_current, ptype_main)

        conviction = float(np.clip(abs(iv_rank - 50) * 1.5, 10, 85))

        return OptionsSignal(
            ticker=ticker,
            spot_price=round(spot, 4),
            strategy=strategy,
            iv_rank=round(iv_rank, 1),
            iv_current=round(iv_current, 4),
            iv_52w_high=round(iv_high, 4),
            iv_52w_low=round(iv_low, 4),
            realised_vol=round(rv, 4),
            recommended_expiry=expiry,
            recommended_strikes=strikes,
            estimated_premium=round(premium, 4),
            max_loss=max_loss,
            max_gain=max_gain,
            breakeven=breakeven,
            net_greeks=greeks,
            conviction=round(conviction, 1),
            rationale=rationale,
        )

    except Exception as e:
        logger.debug(f"Options analysis failed for {ticker}: {e}")
        return None


def run_options_engine(
    featured_data: Dict[str, pd.DataFrame],
    config: dict,
    signal_scores: Optional[Dict[str, float]] = None,
) -> List[OptionsSignal]:
    """
    Run options strategy analysis across equity universe.
    Returns list of OptionsSignal sorted by conviction.
    """
    cfg = config.get("strategies", {}).get("options", {})
    notes = []

    skip_suffixes = ["=X", "-USD", "=F"]
    skip_etfs = {"SPY", "QQQ", "IWM", "GLD", "SLV", "USO",
                 "TLT", "IEF", "AGG", "HYG", "LQD", "JNK",
                 "VCIT", "BKLN", "SJNK", "SHYG", "CWB"}

    signals: List[OptionsSignal] = []

    for ticker, df in featured_data.items():
        if any(ticker.endswith(s) for s in skip_suffixes):
            continue
        if ticker in skip_etfs:
            continue
        score = (signal_scores or {}).get(ticker, 0.0)
        sig = _analyse_ticker_options(ticker, df, cfg, score)
        if sig:
            signals.append(sig)

    signals.sort(key=lambda s: s.conviction, reverse=True)

    if not signals:
        logger.info("No options signals generated (check universe has optionable equities).")

    return signals
