"""
src/intelligence/research/cross_asset.py
========================================
How an instrument is moving relative to other asset classes — measured,
labelled, and never read as cause.

For each relevant pair (chosen by asset class and sector, below), on base-
currency log returns cut at T:

    long-run    correlation and beta over the LONG window that ends where the
                recent window begins (no overlap)
    recent      correlation and beta over the last RECENT periods
    states      INSUFFICIENT_DATA   too few aligned observations
                BREAKDOWN           a meaningful long-run correlation that the
                                    recent window contradicts — the change is
                                    significant (Fisher z, p < 0.01) AND large
                                    (sign flip, or |change| >= 0.4)
                DIVERGING           the last DIVERGE periods' move differs from
                                    what the long-run beta implies by more than
                                    2 residual standard deviations
                CORRELATED          recent |corr| >= 0.3 and significant
                UNCORRELATED        none of the above
                NOT_APPLICABLE      the pair does not describe this instrument
    lead/lag    cross-correlations at lags 1..3, only for pairs on the SAME
                clock (otherwise close-time differences manufacture lags), and
                only reported when one survives a Bonferroni correction across
                the lags tested. Even then it is association, not causation.

Correlation is not causation, and this module never says "because".
Market-wide dispersion (the spread of the eleven sector ETFs' 21-day returns,
against its own past year) is reported alongside.
"""
from __future__ import annotations

import math
from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd

RECENT = {"daily": 63, "weekly": 26}
LONG = {"daily": 252, "weekly": 104}
DIVERGE = {"daily": 21, "weekly": 4}
MIN_RECENT = {"daily": 40, "weekly": 20}
MIN_LONG = {"daily": 120, "weekly": 52}
MEANINGFUL = 0.3
LAGS = (1, 2, 3)
SECTOR_ETFS = ("XLK", "XLF", "XLV", "XLE", "XLI", "XLY", "XLP", "XLU", "XLB", "XLRE", "XLC")

PAIRS = {
    "equity": [("SPY", "equity market"), ("TLT", "long Treasuries"), ("^VIX", "equity volatility"),
               ("UUP", "US dollar"), ("DBC", "commodities"), ("HYG", "high-yield credit")],
    "index": [("TLT", "long Treasuries"), ("UUP", "US dollar"), ("DBC", "commodities")],
    "crypto": [("BTC-USD", "crypto market"), ("QQQ", "risk assets (Nasdaq)"),
               ("UUP", "US dollar"), ("GLD", "gold")],
    "forex": [("UUP", "US dollar"), ("IEF", "US rates"), ("SPY", "risk appetite")],
    "futures": [("SPY", "risk appetite"), ("UUP", "US dollar"), ("IEF", "US rates")],
}
SECTOR_PAIRS = {"energy": [("USO", "crude oil")], "basic materials": [("GLD", "gold")],
                "financial services": [("IEF", "US rates")], "real estate": [("IEF", "US rates")],
                "utilities": [("IEF", "US rates")]}
SPECIAL = {"GLD": [("TIP", "real yields (TIPS)"), ("UUP", "US dollar")],
           "IAU": [("TIP", "real yields (TIPS)"), ("UUP", "US dollar")],
           "GC=F": [("TIP", "real yields (TIPS)"), ("UUP", "US dollar")],
           "USO": [("XLE", "energy equities")], "CL=F": [("XLE", "energy equities")]}


def pairs_for(symbol: str, asset_class: str, sector: Optional[str]) -> list[tuple]:
    if symbol in SPECIAL:
        return SPECIAL[symbol]
    out = list(PAIRS.get(asset_class, []))
    out += SECTOR_PAIRS.get((sector or "").strip().lower(), [])
    seen, uniq = set(), []
    for p, role in out:
        if p != symbol and p not in seen:
            seen.add(p)
            uniq.append((p, role))
    return uniq


def _clock(symbol: str) -> str:
    from src.markets.sessions import resolve
    i = resolve(symbol)
    return "crypto" if i.asset_class == "crypto" else (i.venue or "unknown")


def _returns(symbol, *, at, data, frequency, base="USD"):
    from src.intelligence.research.attribution import _returns as r
    return r(symbol, base=base, at=at, data=data, frequency=frequency)


def _corr(a: np.ndarray, b: np.ndarray) -> Optional[float]:
    if len(a) < 3 or a.std() == 0 or b.std() == 0:
        return None
    return float(np.corrcoef(a, b)[0, 1])


def _beta(y: np.ndarray, x: np.ndarray) -> Optional[float]:
    v = float(np.var(x, ddof=1)) if len(x) > 1 else 0.0
    return None if v == 0 else float(np.cov(x, y, ddof=1)[0, 1] / v)


def _fisher_p(r1: float, n1: int, r2: float, n2: int) -> float:
    z1, z2 = np.arctanh(np.clip([r1, r2], -0.999999, 0.999999))
    se = math.sqrt(1 / max(n1 - 3, 1) + 1 / max(n2 - 3, 1))
    return float(math.erfc(abs(z1 - z2) / se / math.sqrt(2)))


def _significant(r: float, n: int) -> bool:
    if r is None or n < 4 or abs(r) >= 1:
        return False
    t = abs(r) * math.sqrt((n - 2) / (1 - r * r))
    return t > 2.0


def pair_state(y: pd.Series, x: pd.Series, *, frequency: str, same_clock: bool) -> dict:
    j = pd.concat({"y": y, "x": x}, axis=1, join="inner").dropna()
    rn, ln, dn = RECENT[frequency], LONG[frequency], DIVERGE[frequency]
    recent = j.iloc[-rn:]
    longrun = j.iloc[:-rn].iloc[-ln:]
    out = {"n_recent": len(recent), "n_long": len(longrun), "frequency": frequency}
    if len(recent) < MIN_RECENT[frequency] or len(longrun) < MIN_LONG[frequency]:
        return {**out, "state": "INSUFFICIENT_DATA",
                "why": (f"{len(recent)} recent / {len(longrun)} long-run aligned periods; "
                        f"{MIN_RECENT[frequency]} / {MIN_LONG[frequency]} needed")}
    yr, xr = recent["y"].to_numpy(), recent["x"].to_numpy()
    yl, xl = longrun["y"].to_numpy(), longrun["x"].to_numpy()
    c_r, c_l = _corr(yr, xr), _corr(yl, xl)
    b_r, b_l = _beta(yr, xr), _beta(yl, xl)
    out.update(corr_recent=None if c_r is None else round(c_r, 3),
               corr_long=None if c_l is None else round(c_l, 3),
               beta_recent=None if b_r is None else round(b_r, 3),
               beta_long=None if b_l is None else round(b_l, 3))
    if c_r is None or c_l is None:
        return {**out, "state": "INSUFFICIENT_DATA", "why": "a series did not move"}
    p_change = _fisher_p(c_r, len(recent), c_l, len(longrun))
    out["p_change"] = round(p_change, 5)
    tail = j.iloc[-dn:]
    out["pair_move"] = round(float(tail["x"].sum()), 6)
    out["instrument_move"] = round(float(tail["y"].sum()), 6)
    state = "UNCORRELATED"
    if (abs(c_l) >= MEANINGFUL and p_change < 0.01
            and (np.sign(c_r) != np.sign(c_l) or abs(c_r - c_l) >= 0.4)):
        state = "BREAKDOWN"
    else:
        resid_sd = float(np.std(yl - (b_l or 0) * xl, ddof=1))
        implied = (b_l or 0) * float(tail["x"].sum())
        gap = float(tail["y"].sum()) - implied
        dz = gap / (resid_sd * math.sqrt(len(tail))) if resid_sd > 0 else 0.0
        out["divergence_z"] = round(dz, 2)
        if abs(c_l) >= MEANINGFUL and abs(dz) > 2.0:
            state = "DIVERGING"
        elif abs(c_r) >= MEANINGFUL and _significant(c_r, len(recent)):
            state = "CORRELATED"
    out["state"] = state
    out["lead_lag"] = _lead_lag(j.iloc[-ln:], frequency) if same_clock else {
        "state": "NOT_APPLICABLE",
        "why": "the two series close at different times; a lag would be an artefact "
               "of the clocks"}
    return out


def _lead_lag(j: pd.DataFrame, frequency: str) -> dict:
    from statistics import NormalDist
    n = len(j)
    m = 2 * len(LAGS)                                 # both directions, every lag
    crit = NormalDist().inv_cdf(1 - 0.05 / (2 * m))   # two-sided, Bonferroni over m
    found = []
    for k in LAGS:
        for name, a, b in (("pair leads", j["y"].iloc[k:].to_numpy(), j["x"].iloc[:-k].to_numpy()),
                           ("instrument leads", j["x"].iloc[k:].to_numpy(), j["y"].iloc[:-k].to_numpy())):
            c = _corr(a, b)
            if c is not None and abs(c) * math.sqrt(len(a)) > crit:
                found.append({"lag": k, "direction": name, "corr": round(c, 3)})
    if not found:
        return {"state": "NO_DEFENSIBLE_LEAD_LAG", "lags_tested": list(LAGS), "n": n}
    return {"state": "LEAD_LAG_OBSERVED", "lags": found, "n": n,
            "caveat": "a statistical association after a Bonferroni correction — not "
                      "a cause, and not shown to persist out of sample"}


def analyse(symbol: str, at: datetime, data, *, asset_class: str, sector: Optional[str],
            frequency: Optional[str] = None) -> dict:
    """Every relevant pair for `symbol` at `at`."""
    pairs = pairs_for(symbol, asset_class, sector)
    if not pairs:
        return {"status": "NOT_APPLICABLE", "why": f"no cross-asset pairs defined for "
                                                    f"{asset_class or 'this instrument'}",
                "pairs": {}}
    sclock = _clock(symbol)
    out = {"status": "MEASURED", "pairs": {}}
    for p, role in pairs:
        pclock = _clock(p)
        same = sclock == pclock and sclock != "unknown"
        freq = frequency or ("daily" if same else "weekly")
        y, why_y = _returns(symbol, at=at, data=data, frequency=freq)
        x, why_x = _returns(p, at=at, data=data, frequency=freq)
        if y is None or x is None:
            out["pairs"][p] = {"role": role, "state": "INSUFFICIENT_DATA",
                               "why": why_y if y is None else f"{p}: {why_x}"}
            continue
        out["pairs"][p] = {"role": role, **pair_state(y, x, frequency=freq, same_clock=same)}
    states = [v["state"] for v in out["pairs"].values()]
    if all(s == "INSUFFICIENT_DATA" for s in states):
        out["status"] = "INSUFFICIENT_DATA"
    out["caveat"] = "correlation is association; nothing here asserts a cause"
    return out


def dispersion(at: datetime, data) -> dict:
    """Cross-sectional spread of the sector ETFs' 21-day returns, against its
    own trailing year — high dispersion means sectors are moving apart."""
    rets = {}
    for s in SECTOR_ETFS:
        r, _ = _returns(s, at=at, data=data, frequency="daily")
        if r is not None:
            rets[s] = r
    if len(rets) < 6:
        return {"state": "INSUFFICIENT_DATA", "n_sectors": len(rets)}
    panel = pd.concat(rets, axis=1, join="inner").dropna()
    roll = panel.rolling(21).sum().dropna()
    if len(roll) < 120:
        return {"state": "INSUFFICIENT_DATA", "n_sectors": len(rets), "n_days": len(roll)}
    disp = roll.std(axis=1, ddof=1).iloc[-252:]
    now = float(disp.iloc[-1])
    pct = float((disp <= now).mean())
    return {"state": "HIGH" if pct > 0.8 else "LOW" if pct < 0.2 else "NORMAL",
            "dispersion_21d": round(now, 5), "percentile_1y": round(pct, 3),
            "n_sectors": len(rets), "as_of": str(disp.index[-1].date())}
