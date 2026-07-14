"""
black_scholes.py
================
Manual implementation of the Black-Scholes options pricing model.

Covers: price, delta, gamma, vega, theta, rho for calls and puts.

Inputs follow market conventions:
  S     = Current underlying price
  K     = Strike price
  T     = Time to expiry in years (e.g. 30 days = 30/365)
  r     = Risk-free rate (annualised, e.g. 0.05 for 5%)
  sigma = Implied volatility (annualised, e.g. 0.25 for 25%)

DISCLAIMER: Black-Scholes assumes constant volatility, no dividends,
and European-style exercise. Real-world options deviate from these
assumptions. Use as an approximation only.
"""

from __future__ import annotations

import logging
import math

import numpy as np
from scipy.stats import norm

logger = logging.getLogger(__name__)


def _d1_d2(S: float, K: float, T: float, r: float, sigma: float):
    """
    Compute d1 and d2 — the core intermediate values in Black-Scholes.
    Returns (nan, nan) if inputs are invalid (e.g. T=0 or sigma=0).
    """
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return np.nan, np.nan

    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    return d1, d2


# ── Prices ────────────────────────────────────────────────────────────────────

def call_price(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """
    Black-Scholes European call price.
    Returns intrinsic value max(S-K, 0) if T <= 0.
    """
    if T <= 0:
        return max(S - K, 0.0)
    d1, d2 = _d1_d2(S, K, T, r, sigma)
    if np.isnan(d1):
        return np.nan
    return S * norm.cdf(d1) - K * math.exp(-r * T) * norm.cdf(d2)


def put_price(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """
    Black-Scholes European put price.
    Uses put-call parity: P = C - S + K*e^(-rT)
    """
    if T <= 0:
        return max(K - S, 0.0)
    d1, d2 = _d1_d2(S, K, T, r, sigma)
    if np.isnan(d1):
        return np.nan
    return K * math.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)


# ── Delta ─────────────────────────────────────────────────────────────────────

def call_delta(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """
    Call delta: sensitivity of call price to underlying price movement.
    Range: 0 to 1. At-the-money ≈ 0.5.
    """
    d1, _ = _d1_d2(S, K, T, r, sigma)
    if np.isnan(d1):
        return np.nan
    return norm.cdf(d1)


def put_delta(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """
    Put delta: negative (puts lose value when underlying rises).
    Range: -1 to 0. At-the-money ≈ -0.5.
    """
    d1, _ = _d1_d2(S, K, T, r, sigma)
    if np.isnan(d1):
        return np.nan
    return norm.cdf(d1) - 1


# ── Gamma ─────────────────────────────────────────────────────────────────────

def gamma(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """
    Gamma: rate of change of delta with respect to underlying price.
    Same for calls and puts. High gamma near ATM at expiry.
    """
    d1, _ = _d1_d2(S, K, T, r, sigma)
    if np.isnan(d1) or T <= 0 or sigma <= 0:
        return np.nan
    return norm.pdf(d1) / (S * sigma * math.sqrt(T))


# ── Vega ──────────────────────────────────────────────────────────────────────

def vega(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """
    Vega: sensitivity to a 1% change in implied volatility.
    Same for calls and puts. Expressed per 1% vol change.
    """
    d1, _ = _d1_d2(S, K, T, r, sigma)
    if np.isnan(d1) or T <= 0:
        return np.nan
    return S * norm.pdf(d1) * math.sqrt(T) * 0.01  # Per 1% move in vol


# ── Theta ─────────────────────────────────────────────────────────────────────

def call_theta(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """
    Theta: daily time decay of a call option.
    Negative — options lose value as time passes (all else equal).
    """
    d1, d2 = _d1_d2(S, K, T, r, sigma)
    if np.isnan(d1) or T <= 0:
        return np.nan
    term1 = -(S * norm.pdf(d1) * sigma) / (2 * math.sqrt(T))
    term2 = -r * K * math.exp(-r * T) * norm.cdf(d2)
    return (term1 + term2) / 365  # Per calendar day


def put_theta(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """
    Theta for a put option. Usually negative, can be positive deep ITM.
    """
    d1, d2 = _d1_d2(S, K, T, r, sigma)
    if np.isnan(d1) or T <= 0:
        return np.nan
    term1 = -(S * norm.pdf(d1) * sigma) / (2 * math.sqrt(T))
    term2 = r * K * math.exp(-r * T) * norm.cdf(-d2)
    return (term1 + term2) / 365


# ── Rho ───────────────────────────────────────────────────────────────────────

def call_rho(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """
    Call rho: sensitivity to a 1% change in interest rates.
    Positive — calls benefit from higher rates.
    """
    _, d2 = _d1_d2(S, K, T, r, sigma)
    if np.isnan(d2) or T <= 0:
        return np.nan
    return K * T * math.exp(-r * T) * norm.cdf(d2) * 0.01


def put_rho(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """
    Put rho: negative — puts are hurt by higher rates.
    """
    _, d2 = _d1_d2(S, K, T, r, sigma)
    if np.isnan(d2) or T <= 0:
        return np.nan
    return -K * T * math.exp(-r * T) * norm.cdf(-d2) * 0.01


# ── Implied Volatility (Newton-Raphson) ───────────────────────────────────────

def implied_volatility(
    market_price: float,
    S: float,
    K: float,
    T: float,
    r: float,
    option_type: str = "call",
    max_iterations: int = 100,
    tolerance: float = 1e-6,
) -> float:
    """
    Estimate implied volatility from a market option price using
    Newton-Raphson iteration.

    Returns nan if no solution found within max_iterations.
    """
    if T <= 0 or market_price <= 0:
        return np.nan

    sigma = 0.3  # Starting guess

    for _ in range(max_iterations):
        if option_type == "call":
            price = call_price(S, K, T, r, sigma)
        else:
            price = put_price(S, K, T, r, sigma)

        if np.isnan(price):
            return np.nan

        v = vega(S, K, T, r, sigma)
        if np.isnan(v) or abs(v) < 1e-12:
            return np.nan

        diff = price - market_price
        if abs(diff) < tolerance:
            return sigma

        sigma -= diff / (v * 100)  # Vega is per 1% so scale back
        sigma = max(min(sigma, 5.0), 0.001)  # Bound between 0.1% and 500%

    return np.nan  # Did not converge


# ── Quick summary ──────────────────────────────────────────────────────────────

def full_greeks(
    S: float, K: float, T: float, r: float, sigma: float, option_type: str = "call"
) -> dict:
    """
    Return all greeks and price for one option in a dict.
    Useful for display in the dashboard.
    """
    if option_type == "call":
        price  = call_price(S, K, T, r, sigma)
        delta  = call_delta(S, K, T, r, sigma)
        theta_ = call_theta(S, K, T, r, sigma)
        rho_   = call_rho(S, K, T, r, sigma)
    else:
        price  = put_price(S, K, T, r, sigma)
        delta  = put_delta(S, K, T, r, sigma)
        theta_ = put_theta(S, K, T, r, sigma)
        rho_   = put_rho(S, K, T, r, sigma)

    return {
        "type":    option_type,
        "price":   round(price,   4) if not np.isnan(price)  else None,
        "delta":   round(delta,   4) if not np.isnan(delta)  else None,
        "gamma":   round(gamma(S, K, T, r, sigma), 6),
        "vega":    round(vega(S, K, T, r, sigma),  4),
        "theta":   round(theta_,  4) if not np.isnan(theta_) else None,
        "rho":     round(rho_,    4) if not np.isnan(rho_)   else None,
    }
