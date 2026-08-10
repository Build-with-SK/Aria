"""
src/v5/modules/volatility.py
============================
Volatility & Derivatives family — realised/implied volatility and the options
surface.

Volatility is where the risk layer gets its sizing input, so these two modules
matter more for how big a position may be than for whether to take it.
"""
from __future__ import annotations

import math
from datetime import datetime

import pandas as pd

from src.v5 import marketdata as md
from src.v5.contract import Evidence, ModuleReport, insufficient
from src.v5.modules._util import (BASE_RATE_LOOKBACK, ann_vol, clamp,
                                  conditional_hit_rate, effective_interval,
                                  hit_rate_probability, overlap_weakness, pct, regime_weakness)
from src.v5.registry import module

HORIZON = 21


@module("volatility", "volatility",
        "Realised volatility regime, term structure and the forward base rate inside it.",
        horizon_days=HORIZON)
def volatility(ticker: str) -> ModuleReport:
    c = md.closes(ticker, period=BASE_RATE_LOOKBACK)
    if c is None or len(c) < 300:
        return insufficient("volatility", "volatility", ticker, "fewer than 300 daily closes")
    r = c.pct_change().dropna()
    rv21 = r.rolling(21).std() * math.sqrt(252)
    rv63 = r.rolling(63).std() * math.sqrt(252)
    cur21, cur63 = float(rv21.iloc[-1]), float(rv63.iloc[-1])
    lo, hi = float(rv21.quantile(0.33)), float(rv21.quantile(0.66))

    if cur21 <= lo:
        mask, label = rv21 <= lo, "low"
    elif cur21 >= hi:
        mask, label = rv21 >= hi, "high"
    else:
        mask, label = (rv21 > lo) & (rv21 < hi), "mid"

    hr = conditional_hit_rate(c, mask.fillna(False), HORIZON)
    est = hit_rate_probability(hr[0], hr[1], min_n=25)
    if est is None:
        return insufficient("volatility", "volatility", ticker,
                            f"only {hr[1]} forward observations in the {label}-vol regime")
    p, ci = est
    vov = float(rv21.pct_change().tail(63).std())
    src = md.source_label(ticker)
    lean = "bull" if p > 0.55 else "bear" if p < 0.45 else "neutral"

    ev = [
        Evidence(f"21-day realised volatility {pct(cur21)} annualised — {label} regime "
                 f"(terciles over {len(rv21.dropna())} sessions: {pct(lo)} / {pct(hi)})", round(cur21, 4), src,
                 "bear" if label == "high" else "neutral"),
        Evidence(f"63-day realised volatility {pct(cur63)}; short-term vol is "
                 f"{'above' if cur21 > cur63 else 'below'} it, so vol is "
                 f"{'expanding' if cur21 > cur63 else 'compressing'}",
                 round(cur63, 4), src, "bear" if cur21 > cur63 else "bull"),
        Evidence(f"Volatility of volatility {pct(vov)} (63-day)", round(vov, 4), src, "neutral"),
        Evidence(f"Forward {HORIZON}-day base rate in the {label}-vol regime: {hr[0]}/{hr[1]} "
                 f"positive ({p:.0%})", round(p, 4), src, lean),
    ]
    vix = md.closes("^VIX", period="1y")
    if vix is not None and len(vix):
        v = float(vix.iloc[-1]) / 100
        ev.append(Evidence(f"Instrument realised vol {pct(cur21)} versus VIX {v:.0%} — the name is "
                           f"{'more' if cur21 > v else 'less'} volatile than the index",
                           round(cur21 - v, 4), "Yahoo Finance (^VIX)",
                           "bear" if cur21 > v * 1.5 else "neutral"))

    return ModuleReport.from_probability(
        "volatility", "volatility", ticker, p, ci,
        thesis=(f"{ticker} runs {pct(cur21)} annualised volatility — the {label} tercile of its own "
                f"own history, and {'expanding' if cur21 > cur63 else 'compressing'}. Position "
                f"size must scale inversely to this number; the risk layer enforces that."),
        evidence=ev,
        weaknesses=[overlap_weakness(HORIZON), regime_weakness(),
                    "Realised volatility is backward-looking; it says nothing about a scheduled "
                    "catalyst that has not happened yet.",
                    "Close-to-close volatility understates true risk versus a range-based estimator, "
                    "particularly for gapping names."],
        horizon_days=HORIZON, n_obs=hr[1])


@module("options_surface", "volatility",
        "Put/call positioning, skew and the variance risk premium from the live chain.",
        horizon_days=HORIZON)
def options_surface(ticker: str) -> ModuleReport:
    chain, chain_src, note = {}, "", ""
    try:
        from src.data.universe import get_universe
        chain = get_universe().options_chain(ticker) or {}
        chain_src = f"Yahoo Finance options chain ({chain.get('nearest_expiry', 'nearest expiry')})"
    except Exception as e:
        note = f"live chain unavailable ({type(e).__name__})"

    calls, puts = chain.get("calls") or [], chain.get("puts") or []
    ev, parts = [], []

    if calls and puts:
        c_oi = sum(float(o.get("openInterest") or 0) for o in calls)
        p_oi = sum(float(o.get("openInterest") or 0) for o in puts)
        c_iv = [float(o["impliedVolatility"]) for o in calls if o.get("impliedVolatility")]
        p_iv = [float(o["impliedVolatility"]) for o in puts if o.get("impliedVolatility")]
        pc = (p_oi / c_oi) if c_oi else None
        if pc is not None:
            # High put/call open interest is a hedged/fearful book — mildly
            # contrarian bullish at extremes, bearish as a demand signal at mid.
            parts.append(clamp(0.5 + (pc - 0.9) * 0.20, 0.15, 0.85))
            ev.append(Evidence(f"Put/call open interest {pc:.2f} ({p_oi:,.0f} puts vs {c_oi:,.0f} calls) "
                               f"near the money", round(pc, 3), chain_src,
                               "bull" if pc > 1.3 else "bear" if pc < 0.6 else "neutral"))
        if c_iv and p_iv:
            avg_c, avg_p = sum(c_iv) / len(c_iv), sum(p_iv) / len(p_iv)
            skew = avg_p - avg_c
            parts.append(clamp(0.5 - skew * 2.0, 0.15, 0.85))
            ev.append(Evidence(f"Implied vol: calls {pct(avg_c)}, puts {pct(avg_p)} → skew "
                               f"{skew * 100:+.1f}pp", round(skew, 4), chain_src,
                               "bear" if skew > 0.02 else "bull" if skew < -0.02 else "neutral"))
            # Variance risk premium: implied versus realised.
            r = md.returns(ticker, period="6mo")
            if r is not None and len(r) > 40:
                rv = ann_vol(r.tail(21))
                atm_iv = (avg_c + avg_p) / 2
                vrp = atm_iv - rv
                parts.append(clamp(0.5 + vrp * 1.5, 0.2, 0.8))
                ev.append(Evidence(f"ATM implied {pct(atm_iv)} versus 21-day realised {pct(rv)} → "
                                   f"variance risk premium {vrp * 100:+.1f}pp",
                                   round(vrp, 4), f"{chain_src} + {md.source_label(ticker)}",
                                   "bull" if vrp > 0 else "bear"))

    if not parts:
        filed = md.options_data(ticker)
        if filed:
            age = None
            try:
                age = (datetime.now() - datetime.fromisoformat(str(filed.get("analysis_date")))).days
            except Exception:
                pass
            if filed.get("pc_oi_ratio") is not None:
                pc = float(filed["pc_oi_ratio"])
                parts.append(clamp(0.5 + (pc - 0.9) * 0.20, 0.15, 0.85))
                ev.append(Evidence(f"Put/call OI {pc:.2f} from the stored options snapshot"
                                   + (f", {age} days old" if age is not None else ""),
                                   round(pc, 3), "data/options_data.json",
                                   "bull" if pc > 1.3 else "bear" if pc < 0.6 else "neutral"))
            if filed.get("iv_skew") is not None:
                sk = float(filed["iv_skew"])
                parts.append(clamp(0.5 - sk * 2.0, 0.15, 0.85))
                ev.append(Evidence(f"IV skew {sk * 100:+.1f}pp from the stored snapshot"
                                   + (f", {age} days old" if age is not None else ""),
                                   round(sk, 4), "data/options_data.json",
                                   "bear" if sk > 0.02 else "bull"))
            if age and age > 7:
                note = (f"All options evidence here is {age} days old — the live chain did not load, "
                        f"so this is a historical snapshot and cannot describe current positioning.")

    if not parts:
        return insufficient("options_surface", "volatility", ticker,
                            note or "no listed options chain and no stored options snapshot")

    p = sum(parts) / len(parts)
    ci = effective_interval(p, len(parts), floor=0.28)
    weaknesses = [
        "Only the near-the-money strikes of the nearest expiry are examined — the tails of the "
        "surface, where the real information about crash risk sits, are not.",
        "Open interest shows position count, not direction: a large put open interest can be a hedge "
        "against a long, protection bought outright, or short puts sold for income.",
        "The contrarian reading of extreme put/call is a stated prior, not a calibrated relationship.",
        "Yahoo's implied volatilities are unreliable on illiquid strikes and can be stale intraday.",
    ]
    if note:
        weaknesses.insert(0, note)

    return ModuleReport.from_probability(
        "options_surface", "volatility", ticker, p, ci,
        thesis=(f"The options surface reads {p:.0%} bullish across {len(parts)} measurements of "
                f"positioning, skew and the variance premium. Options tell you what protection costs, "
                f"which is a better question than what the crowd thinks."),
        evidence=ev, weaknesses=weaknesses,
        horizon_days=HORIZON, n_obs=len(parts))
