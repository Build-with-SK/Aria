"""
src/intelligence/research/attribution.py
========================================
"AAPL rose 4%" versus "AAPL rose 4%, 3.1% of it explained by market, sector and
factor moves, leaving about 0.9% unexplained".

METHOD
------
Returns are log returns of the instrument and of tradable proxies, all in the
base currency (src/risk/covariance.base_prices: point in time, each price
converted at its own date's FX rate), all cut at T by the session engine.

For a window W (the last 1, 5 or 21 trading days up to the last bar known at
T) the exposures are estimated by one OLS regression on the E periods
STRICTLY BEFORE the window:

    r = a + b_mkt*MKT + b_sec*(SECTOR - SPY) + b_ctry*(COUNTRY - ACWI)
          + b_ccy*FX + sum_k b_k*FACTOR_k + e

so nothing inside the window being explained informs its own explanation.
Then, over the window:

    contribution_f = b_f * (sum of factor f's returns in W)
    explained      = sum of contributions
    residual       = r_W - explained           -> labelled UNEXPLAINED_RESIDUAL

The intercept is not projected into the window (an in-sample drift is not a
forecast). The residual is never called alpha: it is what this model does not
explain, which includes estimation error, missing factors and luck. Its size
relative to the regression's own residual volatility (a z-score) says whether
it is UNUSUAL — descriptively, not as a forecast.

Market proxy: SPY for US-listed equities (the style spreads are SPY-relative),
ACWI otherwise. Sector: the SPDR sector ETF for the symbol master's sector
(today's classification — not point in time; stated). Country: the iShares
country ETF for a non-US equity's venue country. Currency: the FX move of a
non-base instrument. Other factors: src/risk/factors.applicable.

Instruments that do not trade on the factor proxies' clock (non-US venues,
crypto, FX) are estimated on WEEKLY returns, and a 1-day window is
NOT_APPLICABLE for them: a Tokyo close and a New York close are not the same
day's information.
"""
from __future__ import annotations

import math
from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd

UNEXPLAINED = "UNEXPLAINED_RESIDUAL"
WINDOWS = (1, 5, 21)
EST_PERIODS = {"daily": 252, "weekly": 104}
MIN_OBS = {"daily": 120, "weekly": 52}
LOOKBACK_DAYS = {"daily": 560, "weekly": 1000}
UNUSUAL_Z = 2.0

SECTOR_ETF = {
    "technology": "XLK", "information technology": "XLK",
    "financial services": "XLF", "financials": "XLF", "financial": "XLF",
    "healthcare": "XLV", "health care": "XLV",
    "energy": "XLE", "industrials": "XLI",
    "consumer cyclical": "XLY", "consumer discretionary": "XLY",
    "consumer defensive": "XLP", "consumer staples": "XLP",
    "utilities": "XLU", "basic materials": "XLB", "materials": "XLB",
    "real estate": "XLRE", "communication services": "XLC",
}
COUNTRY_ETF = {"JP": "EWJ", "GB": "EWU", "IN": "INDA", "DE": "EWG", "FR": "EWQ",
               "HK": "EWH", "CN": "MCHI", "CA": "EWC", "AU": "EWA", "CH": "EWL",
               "KR": "EWY", "TW": "EWT", "BR": "EWZ", "SG": "EWS", "NL": "EWN",
               "ES": "EWP", "IT": "EWI", "SE": "EWD"}
US_VENUES = ("XNYS",)


def plan(symbol: str, asset_class: str = "") -> dict:
    """Which proxies apply to `symbol`, and on what clock."""
    from src.risk import factors as F
    from src.risk.portfolio_risk import describe, sector_of
    d = describe(symbol, asset_class)
    applies, note = F.applicable(symbol, asset_class)
    us = d["venue"] in US_VENUES and d["asset_class"] in ("equity", "etf", "fund", "index")
    legs: dict[str, tuple] = {}
    for f in applies:
        if f == "market":
            legs["market"] = ("SPY", None) if us else F.FACTORS["market"]
        else:
            legs[f] = F.FACTORS[f]
    sector = None
    if d["asset_class"] in ("equity",) and us:
        sector = sector_of(symbol, d["asset_class"])
        etf = SECTOR_ETF.get((sector or "").strip().lower())
        if etf and etf != symbol:
            legs["sector"] = (etf, "SPY")
    ctry = d.get("country")
    if d["asset_class"] == "equity" and not us and ctry in COUNTRY_ETF:
        legs["country"] = (COUNTRY_ETF[ctry], "ACWI")
    return {"venue": d["venue"], "country": ctry, "asset_class": d["asset_class"],
            "sector": sector, "legs": legs, "note": note,
            "frequency": "daily" if us else "weekly"}


def _index_currency(symbol: str) -> Optional[str]:
    """A US index LEVEL (^VIX, ^TNX) has no quote currency in the resolver;
    it needs no conversion either, so it is read as a USD series, explicitly.
    Anything else returns None and the normal currency rules apply."""
    if not symbol.startswith("^"):
        return None
    from src.markets.sessions import resolve
    return "USD" if resolve(symbol).venue in US_VENUES else None


def _returns(symbol: str, *, base: str, at: datetime, data, frequency: str):
    from src.risk import covariance as cov
    p, why = cov.base_prices(symbol, base=base, at=at, data=data,
                             currency=_index_currency(symbol))
    if p is None:
        return None, why
    p = p[p.index >= pd.Timestamp((at - pd.Timedelta(days=LOOKBACK_DAYS[frequency])).date())]
    if frequency == "weekly":
        p = p.resample("W-FRI").last().dropna()
    r = np.log(p).diff().dropna()
    r = r[np.isfinite(r)]
    return (r if len(r) else None), ("" if len(r) else "no returns")


def _fx_returns(ccy: str, base: str, *, at, data, frequency):
    from src.risk import fx as _fx
    s = _fx.series(ccy, base, at=at, data=data)
    if s is None or isinstance(s, (int, float)):
        return None
    s = s[s.index >= pd.Timestamp((at - pd.Timedelta(days=LOOKBACK_DAYS[frequency])).date())]
    if frequency == "weekly":
        s = s.resample("W-FRI").last().dropna()
    r = np.log(s.where(s > 0)).diff().dropna()
    return r if len(r) else None


def attribute(symbol: str, at: datetime, data, *, base: str = "USD",
              asset_class: str = "", windows=WINDOWS) -> dict:
    """Absolute, explained and UNEXPLAINED_RESIDUAL returns per window."""
    from src.risk import factors as F
    from src.risk import fx as _fx
    if at.tzinfo is None:
        raise ValueError("attribution needs an aware instant")
    pl = plan(symbol, asset_class)
    freq = pl["frequency"]
    out = {"symbol": symbol, "base_currency": base, "frequency": freq,
           "market_proxy": pl["legs"].get("market", (None,))[0],
           "sector": pl["sector"], "country": pl["country"],
           "plan_note": pl["note"], "label": UNEXPLAINED, "windows": {},
           "missing_factors": {}}
    y, why = _returns(symbol, base=base, at=at, data=data, frequency=freq)
    if y is None:
        out["status"] = "INSUFFICIENT_DATA"
        out["why"] = f"instrument: {why}"
        return out

    cache: dict = {}

    def leg(sym):
        if sym not in cache:
            cache[sym] = _returns(sym, base=base, at=at, data=data, frequency=freq)
        return cache[sym]

    fac: dict[str, pd.Series] = {}
    raw_proxy: dict[str, pd.Series] = {}
    for name, (long_sym, short_sym) in pl["legs"].items():
        a, why_a = leg(long_sym)
        if a is None:
            out["missing_factors"][name] = f"proxy {long_sym}: {why_a}"
            continue
        raw_proxy[name] = a
        if short_sym:
            b, why_b = leg(short_sym)
            if b is None:
                out["missing_factors"][name] = f"proxy {short_sym}: {why_b}"
                continue
            j = pd.concat([a, b], axis=1, join="inner").dropna()
            fac[name] = j.iloc[:, 0] - j.iloc[:, 1]
        else:
            fac[name] = a
    ccy = _fx.native_currency(symbol, pl["asset_class"])
    if ccy and _fx.major(ccy)[0] != _fx.major(base)[0]:
        fx_r = _fx_returns(ccy, base, at=at, data=data, frequency=freq)
        if fx_r is None:
            out["missing_factors"]["currency"] = f"no {ccy}->{base} history known at T"
        else:
            fac["currency"] = fx_r
            raw_proxy["currency"] = fx_r
    if "market" not in fac:
        out["status"] = "INSUFFICIENT_DATA"
        out["why"] = "no market proxy history known at T"
        return out

    names = sorted(fac, key=lambda n: (n != "market", n))
    panel = pd.concat({"y": y, **{n: fac[n] for n in names}}, axis=1, join="inner").dropna()
    est_n, min_n = EST_PERIODS[freq], MIN_OBS[freq]
    for w in windows:
        wp = w if freq == "daily" else max(1, round(w / 5))
        if freq == "weekly" and w < 5:
            out["windows"][f"{w}d"] = {"status": "NOT_APPLICABLE",
                                       "why": "the instrument does not trade on the factor "
                                              "proxies' clock; a 1-day comparison would mix "
                                              "two different days' information"}
            continue
        if len(panel) < wp + min_n:
            out["windows"][f"{w}d"] = {"status": "INSUFFICIENT_DATA", "n": len(panel),
                                       "why": f"{len(panel)} aligned periods; "
                                              f"{wp + min_n} needed"}
            continue
        window = panel.iloc[-wp:]
        est = panel.iloc[:-wp].iloc[-est_n:]
        if len(est) < min_n:
            out["windows"][f"{w}d"] = {"status": "INSUFFICIENT_DATA", "n": len(est),
                                       "why": f"{len(est)} estimation periods; {min_n} needed"}
            continue
        X = est[names].to_numpy()
        fit = F._ols(est["y"].to_numpy(), X)
        if fit is None:
            out["windows"][f"{w}d"] = {"status": "INSUFFICIENT_DATA", "why": "regression singular"}
            continue
        coef, t, r2, resid = fit
        resid_sd = float(np.std(resid, ddof=len(names) + 1))
        y_w = float(window["y"].sum())
        contrib = {n: float(coef[i] * window[n].sum()) for i, n in enumerate(names)}
        explained = float(sum(contrib.values()))
        residual = y_w - explained
        z = residual / (resid_sd * math.sqrt(wp)) if resid_sd > 0 else None
        tot_sd = float(est["y"].std(ddof=1))
        z_total = y_w / (tot_sd * math.sqrt(wp)) if tot_sd > 0 else None
        proxies = {n: float(np.expm1(raw_proxy[n].reindex(window.index).dropna().sum()))
                   for n in ("market", "sector", "country") if n in raw_proxy}
        if "sector" in raw_proxy:
            proxies["sector_etf"] = pl["legs"]["sector"][0]
        res = {
            "status": "MEASURED", "periods": wp,
            "window": [str(window.index[0].date()), str(window.index[-1].date())],
            "estimation_window": [str(est.index[0].date()), str(est.index[-1].date())],
            "n_estimation": len(est), "r2": round(float(r2), 4),
            "absolute_return": round(float(np.expm1(y_w)), 6),
            "log_return": round(y_w, 6),
            "market_return": round(proxies.get("market"), 6) if "market" in proxies else None,
            "sector_return": round(proxies.get("sector"), 6) if "sector" in proxies else None,
            "country_return": round(proxies.get("country"), 6) if "country" in proxies else None,
            "contributions": {n: {"beta": round(float(coef[i]), 4),
                                  "t_stat": None if not np.isfinite(t[i]) else round(float(t[i]), 2),
                                  "factor_return": round(float(window[n].sum()), 6),
                                  "contribution": round(contrib[n], 6)}
                              for i, n in enumerate(names)},
            "explained": round(explained, 6),
            "residual": {"label": UNEXPLAINED, "log": round(residual, 6),
                         "approx_pct": round(float(np.expm1(residual)), 6),
                         "z": None if z is None else round(z, 2),
                         "unusual": bool(z is not None and abs(z) >= UNUSUAL_Z)},
            "move_z": None if z_total is None else round(z_total, 2),
            "move_unusual": bool(z_total is not None and abs(z_total) >= UNUSUAL_Z),
        }
        res["sentence"] = _sentence(symbol, w, res)
        out["windows"][f"{w}d"] = res
    measured = [v for v in out["windows"].values() if v.get("status") == "MEASURED"]
    out["status"] = "MEASURED" if measured else "INSUFFICIENT_DATA"
    return out


def _sentence(symbol: str, w: int, r: dict) -> str:
    move = r["absolute_return"] * 100
    exp = float(np.expm1(r["explained"])) * 100
    res = r["residual"]["approx_pct"] * 100
    verb = "rose" if move >= 0 else "fell"
    tail = (f" — {abs(r['residual']['z']):.1f} residual standard deviations, unusual"
            if r["residual"]["unusual"] else "")
    return (f"{symbol} {verb} {abs(move):.1f}% over {w} day(s); {exp:+.1f}% is explained by "
            f"market, sector and factor moves at their estimated exposures, leaving "
            f"{res:+.1f}% unexplained (UNEXPLAINED_RESIDUAL){tail}.")
