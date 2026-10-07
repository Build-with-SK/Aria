"""
src/risk/factors.py
===================
Factor exposures, MEASURED from returns — or reported as unavailable.

THE MODEL
---------
Returns-based (time-series) exposures to tradable proxy portfolios, the
standard way to measure factor risk without a vendor model:

  market        ACWI                  global equity market (USD)
  size          IWM  - SPY            small minus large (US)
  value         IWD  - IWF            value minus growth (US)
  growth        IWF  - SPY            growth minus market (US)
  momentum      MTUM - SPY            (US)
  quality       QUAL - SPY            (US)
  volatility    USMV - SPY            low-volatility minus market (US):
                                      a NEGATIVE exposure is a high-vol tilt
  rates         IEF                   7-10y US Treasuries
  credit        HYG  - IEF            high yield minus Treasuries
  fx            UUP                   the US dollar index
  commodities   DBC                   broad commodities
  crypto        BTC-USD               (crypto only)

Each exposure is the slope of the instrument's base-currency return on the
factor's return, with the market return as a control for every non-market
factor (r = a + b_mkt*mkt + b_f*f + e). One factor at a time keeps the
estimates stable — the style spreads are correlated with each other, and a
regression on all of them at once trades interpretability for noise.

WHERE A FACTOR DOES NOT APPLY, IT IS NOT APPLIED
------------------------------------------------
  US-listed equity     market, styles, macro
  other equity         market, macro — the style proxies are US portfolios;
                       a Tokyo stock's "US value beta" is not its value
                       exposure, so it is UNAVAILABLE, not computed
  futures              by contract group: equity index -> market, rates ->
                       rates, energy/metals/grains/softs -> commodities,
                       FX -> fx, crypto -> crypto
  FX pairs             fx
  crypto               market, crypto
  options              nothing: without a delta model there is no honest
                       factor exposure, so every factor is UNAVAILABLE

A proxy whose history is missing, or an instrument without enough
overlapping observations, gives an UNAVAILABLE exposure with the reason.
Nothing is filled with a sector average, a prior, or 1.0.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd

from src.risk import covariance as _cov

FACTORS: dict[str, tuple[str, Optional[str]]] = {
    "market": ("ACWI", None),
    "size": ("IWM", "SPY"),
    "value": ("IWD", "IWF"),
    "growth": ("IWF", "SPY"),
    "momentum": ("MTUM", "SPY"),
    "quality": ("QUAL", "SPY"),
    "volatility": ("USMV", "SPY"),
    "rates": ("IEF", None),
    "credit": ("HYG", "IEF"),
    "fx": ("UUP", None),
    "commodities": ("DBC", None),
    "crypto": ("BTC-USD", None),
}
STYLE = ("size", "value", "growth", "momentum", "quality", "volatility")
MACRO = ("rates", "credit", "fx", "commodities")
FUTURES_GROUP_FACTOR = {
    "CME_EQUITY": "market", "CME_RATES": "rates", "CME_ENERGY": "commodities",
    "CME_METALS": "commodities", "CBOT_GRAINS": "commodities", "ICE_KC": "commodities",
    "ICE_SB": "commodities", "ICE_CC": "commodities", "ICE_CT": "commodities",
    "CME_FX": "fx", "CME_CRYPTO": "crypto",
}


def applicable(symbol: str, asset_class: str = "") -> tuple[tuple, str]:
    """(factors that apply, why) for one instrument."""
    from src.markets.sessions import resolve
    inst = resolve(symbol, asset_class=asset_class or None)
    ac = (inst.asset_class or asset_class or "").lower()
    if ac in ("option", "options"):
        return (), "option: no delta model, so no factor exposure is computed"
    if ac in ("futures", "future"):
        f = FUTURES_GROUP_FACTOR.get(inst.calendar or "")
        return ((f,) if f else ()), (f"futures group {inst.calendar}" if f
                                     else "futures contract with no mapped risk driver")
    if ac in ("forex", "fx"):
        return ("fx",), "FX pair"
    if ac == "crypto":
        return ("market", "crypto"), "crypto"
    if inst.venue in ("XNYS",):
        return ("market",) + STYLE + MACRO, "US-listed equity"
    if inst.venue:
        return ("market",) + MACRO, (f"equity on {inst.venue}: US style proxies do "
                                     f"not describe it, so styles are unavailable")
    return (), "instrument has no resolved venue"


@dataclass
class Exposure:
    factor: str
    beta: Optional[float] = None
    t_stat: Optional[float] = None
    r2: Optional[float] = None
    n: int = 0
    why: str = ""

    @property
    def ok(self) -> bool:
        return self.beta is not None and math.isfinite(self.beta)

    def to_dict(self) -> dict:
        return {"beta": None if self.beta is None else round(self.beta, 6),
                "t_stat": None if self.t_stat is None else round(self.t_stat, 3),
                "r2": None if self.r2 is None else round(self.r2, 4),
                "n": self.n, "ok": self.ok, "why": self.why}


@dataclass
class InstrumentFactors:
    symbol: str
    applies: tuple
    note: str
    exposures: dict = field(default_factory=dict)      # factor -> Exposure
    residual_vol: Optional[float] = None               # annualised, market model

    def to_dict(self) -> dict:
        return {"applies": list(self.applies), "note": self.note,
                "residual_vol": None if self.residual_vol is None else round(self.residual_vol, 6),
                "exposures": {k: v.to_dict() for k, v in self.exposures.items()}}


def _series(symbol: str, *, base, at, data, frequency, lookback_days):
    p, why = _cov.base_prices(symbol, base=base, at=at, data=data)
    if p is None:
        return None, why
    p = p[p.index >= pd.Timestamp((at - pd.Timedelta(days=lookback_days)).date())]
    if frequency == "weekly":
        p = p.resample("W-FRI").last().dropna()
    r = np.log(p).diff().dropna()
    return r[np.isfinite(r)], ""


def factor_returns(names, *, base: str, at: datetime, data, frequency: str,
                   lookback_days: int = _cov.LOOKBACK_DAYS) -> tuple[dict, dict]:
    """({factor: return series}, {factor: why unavailable})."""
    out, missing, cache = {}, {}, {}

    def leg(sym):
        if sym not in cache:
            cache[sym] = _series(sym, base=base, at=at, data=data, frequency=frequency,
                                 lookback_days=lookback_days)
        return cache[sym]
    for f in names:
        long_sym, short_sym = FACTORS[f]
        a, why = leg(long_sym)
        if a is None:
            missing[f] = f"proxy {long_sym}: {why}"
            continue
        if short_sym:
            b, why_b = leg(short_sym)
            if b is None:
                missing[f] = f"proxy {short_sym}: {why_b}"
                continue
            j = pd.concat([a, b], axis=1, join="inner").dropna()
            out[f] = j.iloc[:, 0] - j.iloc[:, 1]
        else:
            out[f] = a
    return out, missing


def _ols(y: np.ndarray, x: np.ndarray):
    """OLS with intercept: (coefs excluding intercept, t-stats, r2, residuals)."""
    X = np.column_stack([np.ones(len(y)), x])
    coef, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ coef
    dof = len(y) - X.shape[1]
    if dof <= 0:
        return None
    s2 = float(resid @ resid) / dof
    try:
        cov = s2 * np.linalg.inv(X.T @ X)
    except np.linalg.LinAlgError:
        return None
    se = np.sqrt(np.clip(np.diag(cov), 0, None))
    tss = float(((y - y.mean()) ** 2).sum())
    r2 = 1 - float(resid @ resid) / tss if tss > 0 else 0.0
    with np.errstate(divide="ignore", invalid="ignore"):
        t = np.where(se > 0, coef / se, np.nan)
    return coef[1:], t[1:], r2, resid


def exposures(symbols: list[str], *, base: str, at: datetime, data,
              asset_classes: Optional[dict] = None, frequency: str = "auto",
              lookback_days: int = _cov.LOOKBACK_DAYS) -> dict[str, InstrumentFactors]:
    """Measured factor exposures for each instrument, point in time at `at`."""
    asset_classes = asset_classes or {}
    plans = {s: applicable(s, asset_classes.get(s, "")) for s in symbols}
    if frequency not in _cov.PERIODS:
        frequency = "weekly"          # factor proxies are US-listed; the book may not be
    need = _cov.MIN_OBS[frequency]
    names = sorted({f for fs, _ in plans.values() for f in fs} | {"market"})
    fret, fmiss = factor_returns(names, base=base, at=at, data=data, frequency=frequency,
                                 lookback_days=lookback_days)
    ann = math.sqrt(_cov.PERIODS[frequency])
    result = {}
    for s in symbols:
        fs, note = plans[s]
        inf = InstrumentFactors(s, fs, note)
        result[s] = inf
        if not fs:
            continue
        r, why = _series(s, base=base, at=at, data=data, frequency=frequency,
                         lookback_days=lookback_days)
        for f in fs:
            if r is None:
                inf.exposures[f] = Exposure(f, why=f"instrument: {why}")
                continue
            if f not in fret:
                inf.exposures[f] = Exposure(f, why=fmiss.get(f, "factor unavailable"))
                continue
            cols = {"y": r, "f": fret[f]}
            control = f != "market" and "market" in fret and f in STYLE + MACRO
            if control:
                cols["m"] = fret["market"]
            j = pd.concat(cols, axis=1, join="inner").dropna()
            if len(j) < need:
                inf.exposures[f] = Exposure(f, n=len(j),
                                            why=f"{len(j)} overlapping {frequency} returns, {need} needed")
                continue
            x = j[["f", "m"]].to_numpy() if control else j[["f"]].to_numpy()
            fit = _ols(j["y"].to_numpy(), x)
            if fit is None:
                inf.exposures[f] = Exposure(f, n=len(j), why="regression singular")
                continue
            coef, t, r2, resid = fit
            inf.exposures[f] = Exposure(f, float(coef[0]), float(t[0]), float(r2), len(j))
            if f == "market":
                inf.residual_vol = float(np.std(resid, ddof=2) * ann)
    return result


def portfolio_exposures(weights: dict[str, float],
                        per_instrument: dict[str, InstrumentFactors]) -> dict:
    """Weighted factor exposures of a book: sum of weight x beta over the
    positions where the beta is MEASURED, with the coverage stated. A partly
    covered exposure is reported as partial, never scaled up to look whole."""
    gross = sum(abs(w) for w in weights.values()) or 1.0
    factors = sorted({f for i in per_instrument.values() for f in i.exposures})
    out = {}
    for f in factors:
        covered, total = 0.0, 0.0
        for s, w in weights.items():
            e = per_instrument.get(s)
            ex = e.exposures.get(f) if e else None
            if ex is not None and ex.ok:
                total += w * ex.beta
                covered += abs(w)
        out[f] = {"exposure": round(total, 6), "coverage": round(covered / gross, 4),
                  "complete": covered >= gross - 1e-9}
    return out
