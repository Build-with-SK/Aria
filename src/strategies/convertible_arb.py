# src/strategies/convertible_arb.py
"""
Convertible Arbitrage Strategy
Models convertible bond theoretical value, delta-hedge equity component.
Uses CWB ETF and preferred proxy instruments.
"""

from __future__ import annotations
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class ConvertiblePosition:
    ticker: str
    instrument_type: str        # "etf_proxy" | "preferred" | "convert"
    underlying_ticker: str
    market_price: float
    theoretical_value: float
    conversion_value: float
    time_value: float
    implied_vol: float
    realised_vol: float
    delta: float
    gamma: float
    equity_hedge_shares: float  # shares of underlying to short
    signal: str                 # "long_convert" | "short_convert" | "neutral"
    mispricing_pct: float       # (theoretical - market) / theoretical * 100
    conviction: float
    notes: str

    def _to_json(self) -> dict:
        return {
            "ticker": self.ticker,
            "instrument_type": self.instrument_type,
            "underlying_ticker": self.underlying_ticker,
            "market_price": round(self.market_price, 4),
            "theoretical_value": round(self.theoretical_value, 4),
            "conversion_value": round(self.conversion_value, 4),
            "time_value": round(self.time_value, 4),
            "implied_vol": round(self.implied_vol, 4),
            "realised_vol": round(self.realised_vol, 4),
            "delta": round(self.delta, 4),
            "gamma": round(self.gamma, 6),
            "equity_hedge_shares": round(self.equity_hedge_shares, 4),
            "signal": self.signal,
            "mispricing_pct": round(self.mispricing_pct, 3),
            "conviction": round(self.conviction, 1),
            "notes": self.notes,
        }


# Convertible proxies in universe
_CONVERT_PROXIES: Dict[str, dict] = {
    "CWB": {
        "name": "SPDR Bloomberg Convertible Securities ETF",
        "underlying": "SPY",
        "conversion_ratio": 1.0,   # ETF — treat as basket
        "par_value": 100.0,
        "coupon": 0.035,
        "maturity_years": 5.0,
    },
}

# Preferred/hybrid securities with convertible-like characteristics
_PREFERRED_PROXIES: Dict[str, dict] = {}


def _black_scholes_call(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """Standard Black-Scholes call option price."""
    try:
        from scipy.stats import norm
    except ImportError:
        # Fallback: simplified
        return max(S - K, 0)

    try:
        if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
            return max(S - K, 0)
        d1 = (np.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * np.sqrt(T))
        d2 = d1 - sigma * np.sqrt(T)
        return float(S * norm.cdf(d1) - K * np.exp(-r * T) * norm.cdf(d2))
    except Exception:
        return max(S - K, 0)


def _bs_delta(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """Black-Scholes delta."""
    try:
        from scipy.stats import norm
        if T <= 0 or sigma <= 0:
            return 1.0 if S > K else 0.0
        d1 = (np.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * np.sqrt(T))
        return float(norm.cdf(d1))
    except Exception:
        return 0.5


def _bs_gamma(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """Black-Scholes gamma."""
    try:
        from scipy.stats import norm
        if T <= 0 or sigma <= 0 or S <= 0:
            return 0.0
        d1 = (np.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * np.sqrt(T))
        return float(norm.pdf(d1) / (S * sigma * np.sqrt(T)))
    except Exception:
        return 0.0


def _compute_realised_vol(df: pd.DataFrame, window: int = 60) -> float:
    """Annualised realised vol from daily returns."""
    try:
        ret = df["Close"].pct_change().dropna()
        return float(ret.tail(window).std() * np.sqrt(252))
    except Exception:
        return 0.25


def _get_implied_vol(ticker: str, df: pd.DataFrame) -> float:
    """Try to get ATM IV from yfinance options chain, fallback to realised vol."""
    rv = _compute_realised_vol(df)
    try:
        import yfinance as yf
        stock = yf.Ticker(ticker)
        exps = stock.options
        if not exps:
            return rv * 1.1  # IV typically slightly above RV
        # Use first expiry with options
        chain = stock.option_chain(exps[0])
        calls = chain.calls
        if calls.empty:
            return rv * 1.1
        # ATM strike
        spot = float(df["Close"].iloc[-1])
        atm = calls.iloc[(calls["strike"] - spot).abs().argsort()[:1]]
        iv = float(atm["impliedVolatility"].iloc[0])
        return iv if 0.01 < iv < 5.0 else rv * 1.1
    except Exception:
        return rv * 1.1


def _theoretical_convertible_value(
    spot: float,
    conversion_ratio: float,
    par: float,
    coupon: float,
    T: float,
    r: float,
    sigma: float,
) -> tuple:
    """
    Convertible bond theoretical value:
    TV = Bond floor (DCF of coupons + par) + Call option (equity upside)
    """
    # Bond floor: PV of coupons + par
    periods = int(T * 2)   # semi-annual coupons
    period_rate = r / 2
    coupon_pmt = par * coupon / 2

    if period_rate > 0 and periods > 0:
        bond_floor = coupon_pmt * (1 - (1 + period_rate) ** (-periods)) / period_rate + \
                     par * (1 + period_rate) ** (-periods)
    else:
        bond_floor = par

    # Conversion value: spot * ratio
    conversion_value = spot * conversion_ratio

    # Equity option value (call on conversion value)
    K = par  # convert if equity value > par
    call_val = _black_scholes_call(conversion_value, K, T, r, sigma)

    theoretical = bond_floor + call_val
    time_value = call_val
    return float(theoretical), float(conversion_value), float(time_value)


def _analyse_cwb_etf(df_cwb: pd.DataFrame, df_spy: pd.DataFrame, config: dict) -> ConvertiblePosition:
    """Analyse CWB as convertible proxy vs SPY."""
    meta = _CONVERT_PROXIES["CWB"]
    r = 0.05    # risk-free rate proxy
    T = meta["maturity_years"]
    par = meta["par_value"]
    coupon = meta["coupon"]
    cr = meta["conversion_ratio"]

    spy_price = float(df_spy["Close"].iloc[-1]) if not df_spy.empty else 500.0
    cwb_price = float(df_cwb["Close"].iloc[-1]) if not df_cwb.empty else 70.0

    sigma = _compute_realised_vol(df_spy)
    iv = sigma * 1.15  # approximate

    theoretical, conv_val, time_val = _theoretical_convertible_value(
        spy_price, cr * (cwb_price / spy_price), par, coupon, T, r, sigma
    )

    delta = _bs_delta(spy_price * cr, par, T, r, sigma)
    gamma = _bs_gamma(spy_price * cr, par, T, r, sigma)
    mispricing = (theoretical - cwb_price) / max(theoretical, 1) * 100

    if mispricing > 5:
        signal = "long_convert"
        conviction = min(80, abs(mispricing) * 2)
    elif mispricing < -5:
        signal = "short_convert"
        conviction = min(70, abs(mispricing) * 2)
    else:
        signal = "neutral"
        conviction = 20

    hedge_shares = delta   # shares of SPY to short per CWB unit held

    return ConvertiblePosition(
        ticker="CWB",
        instrument_type="etf_proxy",
        underlying_ticker="SPY",
        market_price=round(cwb_price, 4),
        theoretical_value=round(theoretical, 4),
        conversion_value=round(conv_val, 4),
        time_value=round(time_val, 4),
        implied_vol=round(iv, 4),
        realised_vol=round(sigma, 4),
        delta=round(delta, 4),
        gamma=round(gamma, 6),
        equity_hedge_shares=round(hedge_shares, 4),
        signal=signal,
        mispricing_pct=round(mispricing, 3),
        conviction=round(conviction, 1),
        notes=f"Theoretical={theoretical:.2f} vs Market={cwb_price:.2f}, mispricing={mispricing:.1f}%",
    )


def run_convertible_arb(
    featured_data: Dict[str, pd.DataFrame],
    config: dict,
) -> List[ConvertiblePosition]:
    """
    Identify convertible arb opportunities.
    Returns list of ConvertiblePosition with delta-hedge and signal.
    """
    notes = []
    positions: List[ConvertiblePosition] = []

    df_spy = featured_data.get("SPY", pd.DataFrame())
    df_cwb = featured_data.get("CWB", pd.DataFrame())

    if df_cwb is not None and not df_cwb.empty and df_spy is not None and not df_spy.empty:
        try:
            cwb_pos = _analyse_cwb_etf(df_cwb, df_spy, config)
            positions.append(cwb_pos)
        except Exception as e:
            logger.warning(f"CWB analysis failed: {e}")

    if not positions:
        logger.info("No convertible arb instruments in universe (need CWB + SPY).")

    return positions
