"""
src/risk/covariance.py
======================
Returns and covariance for portfolio risk — point in time, in the base
currency, on a frequency that the instruments' calendars can support.

CALENDARS
---------
Tokyo closes before New York opens. Same-DATE daily returns of a Tokyo and a
New York stock are measured over different 24-hour windows, so their daily
correlation is biased towards zero — the classic asynchronous-close problem,
and it would make a global book look more diversified than it is. So:

  every instrument on ONE venue   daily close-to-close returns
  instruments on several venues   WEEKLY (Friday) returns, which overlap
                                  almost entirely across time zones

The choice is recorded on the result (`frequency`). Bars are never filled in
on a day an instrument did not trade: each series' returns are computed on its
own trading days and then aligned.

CURRENCY
--------
Prices are converted into the base currency date by date with each date's
own FX close (src/risk/fx.series), before returns are taken — a JPY stock held
by a USD account carries JPY risk, and that is part of its volatility.

ESTIMATION
----------
Ledoit-Wolf shrinkage towards a scaled identity (2004), then a positive-
semidefinite repair. Sample covariance from a year of weekly data with ten
names is noisy and can be singular; shrinkage makes it invertible and stable
without inventing structure. Too few observations is INSUFFICIENT, not a
smaller sample quietly used.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

import numpy as np
import pandas as pd

from src.risk import fx as _fx

LOOKBACK_DAYS = 365
MIN_OBS = {"daily": 60, "weekly": 26}
PERIODS = {"daily": 252, "weekly": 52}
#: An FX close may be carried forward this many days to a price date (a
#: market open on the other centre's holiday); never further.
FX_FILL_DAYS = 3


@dataclass
class ReturnsPanel:
    returns: pd.DataFrame
    frequency: str
    periods_per_year: int
    at: str
    base: str
    excluded: dict = field(default_factory=dict)       # symbol -> why
    last_bar: dict = field(default_factory=dict)       # symbol -> date used

    @property
    def symbols(self) -> list:
        return list(self.returns.columns)


@dataclass
class CovEstimate:
    matrix: Optional[pd.DataFrame]          # annualised, base currency
    frequency: str
    n_obs: int
    shrinkage: Optional[float]
    condition_number: Optional[float]
    psd_repaired: bool
    method: str
    excluded: dict = field(default_factory=dict)
    why: str = ""

    @property
    def ok(self) -> bool:
        return self.matrix is not None and len(self.matrix) > 0

    def vol(self, symbol: str) -> Optional[float]:
        if not self.ok or symbol not in self.matrix.index:
            return None
        return float(math.sqrt(max(self.matrix.loc[symbol, symbol], 0.0)))

    def to_dict(self) -> dict:
        return {"ok": self.ok, "frequency": self.frequency, "n_obs": self.n_obs,
                "shrinkage": self.shrinkage, "condition_number": self.condition_number,
                "psd_repaired": self.psd_repaired, "method": self.method,
                "symbols": list(self.matrix.index) if self.ok else [],
                "excluded": self.excluded, "why": self.why}


def base_prices(symbol: str, *, base: str, at: datetime, data,
                currency: Optional[str] = None) -> tuple[Optional[pd.Series], str]:
    """Closes of `symbol` in `base`, each converted at its own date's rate."""
    h = data.history(symbol, at)
    if h is None or "Close" not in h:
        return None, "no price history known at this instant"
    c = h["Close"].where(h["Close"] > 0).dropna()
    if c.empty:
        return None, "no positive closes"
    ccy = currency or _fx.native_currency(symbol)
    if not ccy:
        return None, "quote currency unknown — not converted, not guessed"
    rates = _fx.series(ccy, base, at=at, data=data)
    if rates is None:
        return None, f"no {ccy}->{base} FX history known at this instant"
    if isinstance(rates, (int, float)):
        return c * float(rates), ""
    r = rates.reindex(c.index.union(rates.index)).sort_index().ffill(limit=FX_FILL_DAYS)
    out = (c * r.reindex(c.index)).dropna()
    if out.empty:
        return None, f"no {ccy}->{base} rate on any price date"
    return out, ""


def _frequency(symbols: list[str], requested: str) -> str:
    if requested in PERIODS:
        return requested
    from src.markets.sessions import resolve
    venues = {resolve(s).venue for s in symbols}
    return "daily" if len(venues) == 1 and None not in venues else "weekly"


def panel(symbols: list[str], *, base: str, at: datetime, data,
          lookback_days: int = LOOKBACK_DAYS, frequency: str = "auto",
          currencies: Optional[dict] = None, min_obs: Optional[int] = None) -> ReturnsPanel:
    """Aligned base-currency log returns known at `at`."""
    if at.tzinfo is None:
        raise ValueError("returns need an aware instant")
    freq = _frequency(list(symbols), frequency)
    need = min_obs or MIN_OBS[freq]
    start = pd.Timestamp((at - timedelta(days=lookback_days)).date())
    excluded, series, last = {}, {}, {}
    for s in dict.fromkeys(symbols):
        p, why = base_prices(s, base=base, at=at, data=data,
                             currency=(currencies or {}).get(s))
        if p is None:
            excluded[s] = why
            continue
        p = p[p.index >= start]
        if freq == "weekly":
            p = p.resample("W-FRI").last().dropna()
        r = np.log(p).diff().dropna()
        r = r[np.isfinite(r)]
        if len(r) < need:
            excluded[s] = f"{len(r)} {freq} returns, {need} needed"
            continue
        series[s] = r
        last[s] = p.index[-1].date().isoformat()
    df = pd.concat(series, axis=1, join="inner").dropna() if series else pd.DataFrame()
    # Too little overlap: drop the shortest history until what remains is enough.
    while len(df.columns) > 1 and len(df) < need:
        shortest = min(df.columns, key=lambda s: len(series[s]))
        excluded[shortest] = f"too little history overlapping the others ({len(df)} < {need})"
        series.pop(shortest)
        last.pop(shortest, None)
        df = pd.concat(series, axis=1, join="inner").dropna()
    if len(df) < need:
        for s in list(df.columns):
            excluded[s] = f"{len(df)} overlapping {freq} returns, {need} needed"
        df = pd.DataFrame()
        last = {}
    return ReturnsPanel(df, freq, PERIODS[freq], at.isoformat(), base, excluded, last)


def ledoit_wolf(x: np.ndarray) -> tuple[np.ndarray, float]:
    """Ledoit & Wolf (2004) shrinkage of the sample covariance towards
    mean-variance * I. `x` is T x N. Returns (covariance, shrinkage)."""
    t, n = x.shape
    x = x - x.mean(axis=0)
    s = x.T @ x / t
    mu = np.trace(s) / n
    target = mu * np.eye(n)
    d2 = np.sum((s - target) ** 2) / n
    if d2 <= 0:
        return s, 0.0
    # sum_t ||x_t x_t' - S||^2 = sum_t (x_t'x_t)^2 - T ||S||^2   (since sum_t x_t x_t' = T S)
    b_bar2 = (np.sum(np.sum(x * x, axis=1) ** 2) - t * np.sum(s * s)) / n / t ** 2
    b2 = min(b_bar2, d2)
    shrink = b2 / d2
    return shrink * target + (1 - shrink) * s, float(shrink)


def _psd(m: np.ndarray) -> tuple[np.ndarray, bool]:
    m = (m + m.T) / 2
    w, v = np.linalg.eigh(m)
    floor = max(float(np.trace(m)) / len(m), 1e-18) * 1e-10
    if w.min() >= floor:
        return m, False
    w = np.clip(w, floor, None)
    return (v * w) @ v.T, True


def covariance(p: ReturnsPanel) -> CovEstimate:
    """Annualised shrinkage covariance of a returns panel."""
    if p.returns.empty:
        return CovEstimate(None, p.frequency, 0, None, None, False, "none",
                           p.excluded, "not enough history to estimate anything")
    x = p.returns.to_numpy(dtype=float)
    if x.shape[1] == 1:
        var = float(np.var(x, ddof=1))
        m = pd.DataFrame([[var * p.periods_per_year]], index=p.symbols, columns=p.symbols)
        return CovEstimate(m, p.frequency, x.shape[0], 0.0, 1.0, False, "sample variance",
                           p.excluded)
    cov, shrink = ledoit_wolf(x)
    cov, repaired = _psd(cov * p.periods_per_year)
    w = np.linalg.eigvalsh(cov)
    cond = float(w.max() / w.min()) if w.min() > 0 else math.inf
    return CovEstimate(pd.DataFrame(cov, index=p.symbols, columns=p.symbols), p.frequency,
                       x.shape[0], round(shrink, 6), cond, repaired,
                       "Ledoit-Wolf shrinkage to scaled identity", p.excluded)


def correlation(c: CovEstimate) -> Optional[pd.DataFrame]:
    if not c.ok:
        return None
    d = np.sqrt(np.clip(np.diag(c.matrix.to_numpy()), 1e-30, None))
    corr = c.matrix.to_numpy() / np.outer(d, d)
    return pd.DataFrame(np.clip(corr, -1, 1), index=c.matrix.index, columns=c.matrix.columns)
