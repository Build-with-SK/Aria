"""
ARIA Tool: get_options_greeks
Black-Scholes option pricing and Greeks calculator.
"""

import asyncio
import math
from datetime import datetime


def _norm_cdf(x: float) -> float:
    return (1.0 + math.erf(x / math.sqrt(2.0))) / 2.0


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)


def black_scholes(S: float, K: float, T: float, r: float, sigma: float, option_type: str) -> dict:
    """
    S     = spot price
    K     = strike price
    T     = time to expiry in years
    r     = risk-free rate (decimal)
    sigma = implied volatility (decimal)
    """
    if T <= 0:
        intrinsic = max(0, S - K) if option_type == "call" else max(0, K - S)
        return {"price": round(intrinsic, 4), "intrinsic": round(intrinsic, 4),
                "delta": 1.0 if option_type == "call" else -1.0,
                "gamma": 0, "theta": 0, "vega": 0, "rho": 0,
                "note": "Option has expired or expires today — intrinsic value only."}

    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)

    if option_type == "call":
        price = S * _norm_cdf(d1) - K * math.exp(-r * T) * _norm_cdf(d2)
        delta = _norm_cdf(d1)
        rho   = K * T * math.exp(-r * T) * _norm_cdf(d2) / 100
    else:
        price = K * math.exp(-r * T) * _norm_cdf(-d2) - S * _norm_cdf(-d1)
        delta = _norm_cdf(d1) - 1
        rho   = -K * T * math.exp(-r * T) * _norm_cdf(-d2) / 100

    gamma = _norm_pdf(d1) / (S * sigma * math.sqrt(T))
    vega  = S * _norm_pdf(d1) * math.sqrt(T) / 100    # per 1% vol change
    theta = (
        -(S * _norm_pdf(d1) * sigma) / (2 * math.sqrt(T))
        - r * K * math.exp(-r * T) * (_norm_cdf(d2) if option_type == "call" else _norm_cdf(-d2))
    ) / 365  # per calendar day

    intrinsic = max(0, S - K) if option_type == "call" else max(0, K - S)
    time_value = max(0, price - intrinsic)

    moneyness = "ATM" if abs(S - K) / S < 0.02 else ("ITM" if intrinsic > 0 else "OTM")

    return {
        "price": round(price, 4),
        "intrinsic_value": round(intrinsic, 4),
        "time_value": round(time_value, 4),
        "moneyness": moneyness,
        "greeks": {
            "delta": round(delta, 4),
            "gamma": round(gamma, 6),
            "theta": round(theta, 4),   # daily decay in $
            "vega":  round(vega, 4),    # per 1% vol change
            "rho":   round(rho, 4),     # per 1% rate change
        },
        "inputs": {
            "d1": round(d1, 4),
            "d2": round(d2, 4),
        },
    }


async def get_options_greeks(
    ticker: str,
    strike: float,
    expiry_days: int,
    option_type: str,
    implied_vol: float = None,
) -> dict:
    """
    Calculate option price and Greeks.
    If implied_vol not provided, use a reasonable estimate based on typical vol.
    """
    import yfinance as yf

    loop = asyncio.get_event_loop()

    # Get current spot price
    try:
        info = await loop.run_in_executor(None, lambda: yf.Ticker(ticker).info)
        spot = info.get("regularMarketPrice") or info.get("previousClose")
        if not spot:
            hist = await loop.run_in_executor(
                None, lambda: yf.download(ticker, period="5d", progress=False, auto_adjust=True)
            )
            spot = float(hist["Close"].iloc[-1])
    except Exception as e:
        return {"error": f"Could not fetch spot price for {ticker}: {e}"}

    # Estimate IV from historical vol if not provided
    if implied_vol is None:
        try:
            hist = await loop.run_in_executor(
                None, lambda: yf.download(ticker, period="3mo", progress=False, auto_adjust=True)
            )
            import pandas as pd
            import numpy as np
            returns = hist["Close"].squeeze().pct_change().dropna()
            implied_vol = round(float(returns.std() * np.sqrt(252)), 4)
            iv_source = "estimated from 90d historical volatility"
        except Exception:
            implied_vol = 0.25
            iv_source = "default (0.25) — historical vol unavailable"
    else:
        iv_source = "user-provided"

    # Risk-free rate (approximate — ideally would pull from FRED)
    risk_free_rate = 0.05

    T = expiry_days / 365.0
    result = black_scholes(spot, strike, T, risk_free_rate, implied_vol, option_type)

    # Interpretation
    delta = result["greeks"]["delta"]
    theta = result["greeks"]["theta"]
    vega  = result["greeks"]["vega"]

    interpretations = []
    if abs(delta) > 0.7:
        interpretations.append(f"High delta ({delta:.2f}) — option moves nearly 1:1 with the stock.")
    elif abs(delta) < 0.3:
        interpretations.append(f"Low delta ({delta:.2f}) — lottery ticket character, needs large move to profit.")

    if theta < -0.05:
        interpretations.append(f"Significant time decay: losing ~${abs(theta):.2f}/day.")

    if vega > 0.1:
        interpretations.append(f"High vega ({vega:.2f}) — position is vol-sensitive; an IV crush would hurt.")

    # Break-even
    if option_type == "call":
        breakeven = strike + result["price"]
        be_pct    = (breakeven / spot - 1) * 100
        interpretations.append(f"Break-even at expiry: ${breakeven:.2f} ({be_pct:+.1f}% from current spot ${spot:.2f}).")
    else:
        breakeven = strike - result["price"]
        be_pct    = (breakeven / spot - 1) * 100
        interpretations.append(f"Break-even at expiry: ${breakeven:.2f} ({be_pct:+.1f}% from current spot ${spot:.2f}).")

    return {
        "ticker": ticker,
        "option_type": option_type,
        "spot_price": spot,
        "strike": strike,
        "expiry_days": expiry_days,
        "implied_vol": implied_vol,
        "iv_source": iv_source,
        "risk_free_rate": risk_free_rate,
        **result,
        "interpretations": interpretations,
    }
