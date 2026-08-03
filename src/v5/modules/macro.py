"""
src/v5/modules/macro.py
=======================
Macro & Cross-Asset family — Market Regime, Global Macro, Yield Curve, Credit,
Currency Strength, Carry, Commodity Cycle, Seasonality, Cross-Asset Correlation,
Liquidity.

Most of these are *conditioning* engines: they rarely produce a strong
single-name view, and they are weighted accordingly by the ensemble. Their real
job is to tell the risk layer what environment the trade would be entered into.

data/macro_data.json is used where it is fresh and ignored where it is not —
every macro module checks its `data_date` and states the age of what it cited.
Stale macro presented as current would be a Law 2 violation.
"""
from __future__ import annotations

import math
from datetime import datetime

import pandas as pd

from src.v5 import marketdata as md
from src.v5.contract import Evidence, ModuleReport, insufficient
from src.v5.modules._util import (ann_vol, clamp, conditional_hit_rate, effective_interval,
                                  hit_rate_probability, logistic, overlap_weakness, pct,
                                  regime_weakness, sma)
from src.v5.registry import module

HORIZON = 21
MSRC = "data/macro_data.json"


def _macro_age_days() -> float | None:
    m = md.macro()
    d = m.get("data_date")
    if not d:
        return None
    try:
        return (datetime.now() - datetime.fromisoformat(str(d))).days
    except Exception:
        return None


def _stale_note() -> str | None:
    age = _macro_age_days()
    if age is None:
        return "data/macro_data.json carries no data_date — its freshness cannot be verified."
    if age > 7:
        return (f"data/macro_data.json is {age} days old (dated {md.macro().get('data_date')}); "
                f"live market-derived values are preferred where available and any figure quoted "
                f"from that file is a snapshot, not a current reading.")
    return None


def _trend(sym: str, days: int, period: str = "1y") -> tuple[float, float] | None:
    """(last value, fractional change over `days` sessions) for a market series."""
    c = md.closes(sym, period=period)
    if c is None or len(c) <= days:
        return None
    return (float(c.iloc[-1]), float(c.iloc[-1] / c.iloc[-days - 1] - 1))


# ── Market regime ───────────────────────────────────────────────────────────

@module("market_regime", "macro",
        "Volatility regime from VIX terciles, with the instrument's own base rate inside it.",
        horizon_days=HORIZON)
def market_regime(ticker: str) -> ModuleReport:
    vix = md.closes("^VIX", period="5y")
    c = md.closes(ticker, period="5y")
    if vix is None or c is None or len(vix) < 300 or len(c) < 300:
        return insufficient("market_regime", "macro", ticker,
                            "VIX or instrument history unavailable")
    df = pd.concat([c.rename("p"), vix.rename("v")], axis=1, sort=True).dropna()
    if len(df) < 300:
        return insufficient("market_regime", "macro", ticker,
                            f"only {len(df)} sessions align with the VIX series")

    v_now = float(df["v"].iloc[-1])
    lo, hi = float(df["v"].quantile(0.33)), float(df["v"].quantile(0.66))
    if v_now <= lo:
        mask, label = df["v"] <= lo, "low-volatility"
    elif v_now >= hi:
        mask, label = df["v"] >= hi, "high-volatility"
    else:
        mask, label = (df["v"] > lo) & (df["v"] < hi), "mid-volatility"

    hr = conditional_hit_rate(df["p"], mask, HORIZON)
    est = hit_rate_probability(hr[0], hr[1], min_n=25)
    if est is None:
        return insufficient("market_regime", "macro", ticker,
                            f"only {hr[1]} forward observations in the {label} regime")
    p, ci = est
    spy = md.closes(md.BENCH, period="1y")
    spy_state = ""
    if spy is not None and len(spy) > 200:
        above = float(spy.iloc[-1] / float(sma(spy, 200).iloc[-1]) - 1)
        spy_state = f"{md.BENCH} is {above * 100:+.1f}% versus its 200-day average"
    lean = "bull" if p > 0.55 else "bear" if p < 0.45 else "neutral"
    src = "Yahoo Finance (^VIX and instrument daily closes, 5y)"

    ev = [
        Evidence(f"VIX {v_now:.1f} — {label} regime (5y terciles {lo:.1f} / {hi:.1f})",
                 round(v_now, 2), src, "bear" if label == "high-volatility" else
                 "bull" if label == "low-volatility" else "neutral"),
        Evidence(f"{ticker} forward {HORIZON}-day returns in the {label} regime: {hr[0]}/{hr[1]} "
                 f"positive ({p:.0%})", round(p, 4), src, lean),
        Evidence(f"Mean forward return in this regime {pct(hr[2], 2)}", round(hr[2], 4), src,
                 "bull" if hr[2] > 0 else "bear"),
    ]
    if spy_state:
        ev.append(Evidence(spy_state, None, f"Yahoo Finance ({md.BENCH})", "neutral"))
    stale = _stale_note()
    filed = md.macro()
    if filed.get("regime"):
        ev.append(Evidence(f"ARIA macro engine labels the regime '{filed['regime']}' "
                           f"(as of {filed.get('data_date', 'unknown date')})",
                           None, MSRC, "neutral"))

    return ModuleReport.from_probability(
        "market_regime", "macro", ticker, p, ci,
        thesis=(f"The market is in a {label} regime at VIX {v_now:.1f}. Inside that regime "
                f"{ticker} has closed the next {HORIZON} sessions higher {p:.0%} of the time "
                f"across {hr[1]} observations."),
        evidence=ev,
        weaknesses=[overlap_weakness(HORIZON), regime_weakness(),
                    "VIX terciles are computed on a five-year window, so the definition of 'high "
                    "volatility' drifts with the sample.",
                    "The VIX is an S&P instrument — for a non-US name it measures a correlated but "
                    "different risk environment."] + ([stale] if stale else []),
        horizon_days=HORIZON, n_obs=hr[1])


# ── Global macro ────────────────────────────────────────────────────────────

@module("global_macro", "macro",
        "Rates, dollar, growth and inflation proxies read as a single risk backdrop.",
        horizon_days=63)
def global_macro(ticker: str) -> ModuleReport:
    parts, ev = [], []
    src = "Yahoo Finance (macro proxy series, daily closes)"

    tnx = _trend("^TNX", 21)
    if tnx:
        # ^TNX quotes the yield directly in percent (4.75 = 4.75%), cross-checked
        # against data/macro_data.json's treasury_10y. No /10 scaling.
        level, chg = tnx[0], tnx[1]
        parts.append(clamp(0.5 - chg * 4, 0.0, 1.0))
        ev.append(Evidence(f"US 10-year yield {level:.2f}%, {chg * 100:+.1f}% over the last month",
                           round(level, 3), src, "bear" if chg > 0.03 else "bull" if chg < -0.03 else "neutral"))
    dxy = _trend("DX-Y.NYB", 21)
    if dxy:
        parts.append(clamp(0.5 - dxy[1] * 6, 0.0, 1.0))
        ev.append(Evidence(f"Dollar index {dxy[0]:.1f}, {pct(dxy[1])} over the last month",
                           round(dxy[0], 2), src, "bear" if dxy[1] > 0.01 else "bull"))
    oil = _trend("CL=F", 21)
    if oil:
        parts.append(clamp(0.5 + oil[1] * 2, 0.0, 1.0))
        ev.append(Evidence(f"Crude {oil[0]:.1f}, {pct(oil[1])} over the last month — growth proxy",
                           round(oil[0], 2), src, "bull" if oil[1] > 0 else "bear"))
    hyg = _trend("HYG", 21)
    if hyg:
        parts.append(clamp(0.5 + hyg[1] * 12, 0.0, 1.0))
        ev.append(Evidence(f"High-yield credit (HYG) {pct(hyg[1])} over the last month — risk appetite",
                           round(hyg[1], 4), src, "bull" if hyg[1] > 0 else "bear"))

    if len(parts) < 3:
        return insufficient("global_macro", "macro", ticker,
                            f"only {len(parts)} macro proxy series loaded")

    m = md.macro()
    stale = _stale_note()
    for k, label in [("macro_score", "ARIA macro score"), ("regime", "ARIA macro regime")]:
        if m.get(k) is not None:
            ev.append(Evidence(f"{label}: {m[k]} (as of {m.get('data_date', '?')})",
                               m[k] if isinstance(m[k], (int, float)) else None, MSRC, "neutral"))
    missing = [w for w in (m.get("warnings") or []) if "Missing" in str(w)]
    if missing:
        ev.append(Evidence(f"ARIA macro feed reports: {missing[0]}", None, MSRC, "neutral"))

    p = sum(parts) / len(parts)
    ci = effective_interval(p, len(parts), floor=0.30)
    return ModuleReport.from_probability(
        "global_macro", "macro", ticker, p, ci,
        thesis=(f"The macro backdrop scores {p:.0%} risk-positive across {len(parts)} proxies "
                f"(rates, dollar, growth, credit). This is background conditioning for {ticker}, "
                f"not a view on the name."),
        evidence=ev,
        weaknesses=["The mapping from each proxy to a probability is a stated prior, not a fitted "
                    "relationship — reproducible but uncalibrated.",
                    "No CPI, no payrolls, no policy path: the FRED feed is unavailable in this "
                    "environment, so the hardest macro inputs are simply missing.",
                    "Macro moves slowly and this is applied to a 21-day single-name decision; its "
                    "ensemble weight is deliberately low.",
                    "US-centric proxies applied regardless of the instrument's listing venue."]
        + ([stale] if stale else []),
        horizon_days=63, n_obs=len(parts))


# ── Yield curve ─────────────────────────────────────────────────────────────

@module("yield_curve", "macro",
        "Curve level, slope and direction from live Treasury series.",
        horizon_days=63)
def yield_curve(ticker: str) -> ModuleReport:
    ten = md.closes("^TNX", period="2y")
    short = md.closes("^IRX", period="2y")
    if ten is None or len(ten) < 120:
        return insufficient("yield_curve", "macro", ticker, "10-year yield series unavailable")

    y10 = float(ten.iloc[-1])          # ^TNX is already a percentage yield
    chg10 = float(ten.iloc[-1] / ten.iloc[-22] - 1) if len(ten) > 22 else 0.0
    ev = [Evidence(f"US 10-year {y10:.2f}%, {chg10 * 100:+.1f}% over the last month",
                   round(y10, 3), "Yahoo Finance (^TNX)",
                   "bear" if chg10 > 0.05 else "bull" if chg10 < -0.05 else "neutral")]

    slope = None
    if short is not None and len(short) > 22:
        y3m = float(short.iloc[-1])
        slope = y10 - y3m
        ev.append(Evidence(f"3-month bill {y3m:.2f}% → 10y-3m slope {slope:+.2f}pp",
                           round(slope, 3), "Yahoo Finance (^TNX, ^IRX)",
                           "bear" if slope < 0 else "bull"))
    m = md.macro()
    if m.get("yield_spread_10y2y") is not None:
        ev.append(Evidence(f"ARIA feed 10y-2y spread {m['yield_spread_10y2y']:+.2f}pp "
                           f"(as of {m.get('data_date', '?')})",
                           m["yield_spread_10y2y"], MSRC,
                           "bear" if m["yield_spread_10y2y"] < 0 else "bull"))

    score = 0.0
    score += clamp(slope, -1.5, 1.5) * 0.5 if slope is not None else 0.0
    score += -chg10 * 6
    p = logistic(score, k=1.0)
    ci = effective_interval(p, 2 if slope is not None else 1, floor=0.32)
    stale = _stale_note()

    return ModuleReport.from_probability(
        "yield_curve", "macro", ticker, p, ci,
        thesis=(f"The curve is {'inverted' if (slope or 0) < 0 else 'positively sloped'} "
                + (f"at {slope:+.2f}pp (10y-3m)" if slope is not None else "(slope unavailable)")
                + f" with the 10-year at {y10:.2f}% and {'rising' if chg10 > 0 else 'falling'}. "
                  f"Rising long rates compress equity multiples; an inverted curve is a recession "
                  f"signal with a long and variable lead."),
        evidence=ev,
        weaknesses=["The 3-month bill is a proxy for the front end; the canonical 10y-2y spread is not "
                    "directly available from this feed.",
                    "Curve inversion leads recessions by 6-24 months — it carries almost no information "
                    "about a 21-day equity horizon.",
                    "The score-to-probability map is a stated prior, not an estimated relationship."]
        + ([stale] if stale else []),
        horizon_days=63, n_obs=2 if slope is not None else 1)


# ── Credit ──────────────────────────────────────────────────────────────────

@module("credit", "macro",
        "Credit risk appetite from high-yield versus investment-grade and duration.",
        horizon_days=63)
def credit(ticker: str) -> ModuleReport:
    hyg, lqd, tlt = (md.closes(s, period="2y") for s in ("HYG", "LQD", "TLT"))
    if hyg is None or lqd is None:
        return insufficient("credit", "macro", ticker, "HYG or LQD history unavailable")
    df = pd.concat([hyg.rename("hy"), lqd.rename("ig")], axis=1, sort=True).dropna()
    if len(df) < 130:
        return insufficient("credit", "macro", ticker, "fewer than 130 aligned credit sessions")

    ratio = df["hy"] / df["ig"]
    r21 = float(ratio.iloc[-1] / ratio.iloc[-22] - 1)
    r63 = float(ratio.iloc[-1] / ratio.iloc[-64] - 1) if len(ratio) > 64 else 0.0
    vs_ma = float(ratio.iloc[-1] / float(sma(ratio, 100).iloc[-1]) - 1)
    src = "Yahoo Finance (HYG, LQD, TLT daily closes)"

    ev = [
        Evidence(f"HY/IG ratio {pct(r21)} over one month — credit risk appetite",
                 round(r21, 4), src, "bull" if r21 > 0 else "bear"),
        Evidence(f"HY/IG ratio {pct(r63)} over three months", round(r63, 4), src,
                 "bull" if r63 > 0 else "bear"),
        Evidence(f"HY/IG is {vs_ma * 100:+.1f}% versus its 100-day average", round(vs_ma, 4), src,
                 "bull" if vs_ma > 0 else "bear"),
    ]
    if tlt is not None and len(tlt) > 22:
        t21 = float(tlt.iloc[-1] / tlt.iloc[-22] - 1)
        ev.append(Evidence(f"Long duration (TLT) {pct(t21)} over one month", round(t21, 4), src,
                           "neutral"))

    p = logistic(60 * r21 + 25 * r63 + 20 * vs_ma, k=1.0)
    ci = effective_interval(p, 3, floor=0.28)
    return ModuleReport.from_probability(
        "credit", "macro", ticker, p, ci,
        thesis=(f"Credit is {'leading risk assets higher' if r21 > 0 else 'lagging — a warning sign'}: "
                f"high yield versus investment grade is {pct(r21)} on the month. Credit usually cracks "
                f"before equities do."),
        evidence=ev,
        weaknesses=["ETF price ratios are a coarse proxy for option-adjusted credit spreads; duration "
                    "and composition differences contaminate the reading.",
                    "This is market-level context applied to one name, weighted low by the ensemble.",
                    "The logistic mapping is a stated prior with no outcome calibration."],
        horizon_days=63, n_obs=3)


# ── Currency strength ───────────────────────────────────────────────────────

@module("currency_strength", "macro",
        "Dollar direction and the instrument's currency exposure.",
        horizon_days=63)
def currency_strength(ticker: str) -> ModuleReport:
    dxy = md.closes("DX-Y.NYB", period="2y")
    if dxy is None or len(dxy) < 130:
        return insufficient("currency_strength", "macro", ticker, "dollar index history unavailable")
    d21 = float(dxy.iloc[-1] / dxy.iloc[-22] - 1)
    d63 = float(dxy.iloc[-1] / dxy.iloc[-64] - 1)
    vs_ma = float(dxy.iloc[-1] / float(sma(dxy, 200).iloc[-1]) - 1)

    f = md.fundamentals(ticker)
    ccy = f.get("currency") or md.dossier(ticker).get("currency") or "unknown"
    src = "Yahoo Finance (DX-Y.NYB daily closes)"

    # A stronger dollar is a headwind for US multinationals and for most non-USD
    # assets priced in dollars; the effect is real but second-order for one name.
    p = clamp(0.5 - d21 * 5 - d63 * 2, 0.0, 1.0)
    ci = effective_interval(p, 3, floor=0.30)
    return ModuleReport.from_probability(
        "currency_strength", "macro", ticker, p, ci,
        thesis=(f"The dollar is {pct(d21)} on the month, {pct(d63)} on the quarter and "
                f"{vs_ma * 100:+.1f}% versus its 200-day average. {ticker} reports in {ccy}. "
                f"Dollar strength is a translation headwind for USD-reporting multinationals and "
                f"a direct headwind for dollar-priced commodities."),
        evidence=[
            Evidence(f"Dollar index {pct(d21)} over one month", round(d21, 4), src,
                     "bear" if d21 > 0 else "bull"),
            Evidence(f"Dollar index {pct(d63)} over three months", round(d63, 4), src,
                     "bear" if d63 > 0 else "bull"),
            Evidence(f"Dollar index {vs_ma * 100:+.1f}% versus its 200-day average",
                     round(vs_ma, 4), src, "bear" if vs_ma > 0 else "bull"),
            Evidence(f"Instrument reporting currency: {ccy}", None,
                     "Yahoo Finance fundamentals via universe dossier", "neutral"),
        ],
        weaknesses=["The revenue geography that would make this specific is not in the feed — the "
                    "currency sensitivity is assumed, not measured.",
                    "The sign of the effect flips for exporters versus importers, and this module "
                    "cannot tell which the instrument is.",
                    "Coefficients on the dollar move are stated priors."],
        horizon_days=63, n_obs=3)


# ── Carry ───────────────────────────────────────────────────────────────────

@module("carry", "macro",
        "Cost of carry: funding rate against the yield the position actually pays.",
        horizon_days=63)
def carry(ticker: str) -> ModuleReport:
    short = md.closes("^IRX", period="1y")
    if short is None or len(short) < 30:
        return insufficient("carry", "macro", ticker, "short-rate series unavailable")
    funding = float(short.iloc[-1])
    dy_pct = md.dividend_yield_pct(ticker)
    if dy_pct is None:
        return insufficient("carry", "macro", ticker,
                            "no unambiguous dividend yield available (the vendor field's units are "
                            "version-dependent, and guessing them would be a 100× error) — carry "
                            "cannot be computed for this instrument")

    net = dy_pct - funding
    p = clamp(0.5 + net / 12.0, 0.0, 1.0)
    ci = effective_interval(p, 2, floor=0.34)
    src = "Yahoo Finance (^IRX) and universe fundamentals"
    return ModuleReport.from_probability(
        "carry", "macro", ticker, p, ci,
        thesis=(f"Holding {ticker} pays {dy_pct:.2f}% against a {funding:.2f}% risk-free funding rate: "
                f"net carry {net:+.2f}pp. Negative carry means the position must earn its return from "
                f"price alone, with the clock running against it."),
        evidence=[
            Evidence(f"Dividend yield {dy_pct:.2f}%", round(dy_pct, 3), src,
                     "bull" if dy_pct > 2 else "neutral"),
            Evidence(f"3-month bill (funding proxy) {funding:.2f}%", round(funding, 3), src, "neutral"),
            Evidence(f"Net carry {net:+.2f}pp per year", round(net, 3), src,
                     "bull" if net > 0 else "bear"),
        ],
        weaknesses=["Retail financing costs exceed the bill rate; the real net carry on a levered "
                    "position is worse than this.",
                    "Ignores borrow costs, dividend timing, and tax entirely.",
                    "Carry is an annual concept applied to a 21-day decision; its weight should be "
                    "and is very small."],
        horizon_days=63, n_obs=2)


# ── Commodity cycle ─────────────────────────────────────────────────────────

@module("commodity_cycle", "macro",
        "Copper/gold, oil and gold as a read on where the industrial cycle is.",
        horizon_days=63)
def commodity_cycle(ticker: str) -> ModuleReport:
    cop, gold, oil = (md.closes(s, period="2y") for s in ("HG=F", "GC=F", "CL=F"))
    ev, parts = [], []
    src = "Yahoo Finance (HG=F, GC=F, CL=F daily closes)"

    if cop is not None and gold is not None:
        df = pd.concat([cop.rename("c"), gold.rename("g")], axis=1, sort=True).dropna()
        if len(df) > 70:
            ratio = df["c"] / df["g"]
            r63 = float(ratio.iloc[-1] / ratio.iloc[-64] - 1)
            parts.append(clamp(0.5 + r63 * 3, 0.0, 1.0))
            ev.append(Evidence(f"Copper/gold ratio {pct(r63)} over three months — the classic "
                               f"growth-versus-fear read", round(r63, 4), src,
                               "bull" if r63 > 0 else "bear"))
    for sym, label, k in (("CL=F", "Crude", 2.0), ("GC=F", "Gold", -1.5)):
        t = _trend(sym, 63, "2y")
        if t:
            parts.append(clamp(0.5 + t[1] * k, 0.0, 1.0))
            ev.append(Evidence(f"{label} {pct(t[1])} over three months (level {t[0]:.1f})",
                               round(t[1], 4), src,
                               "bull" if t[1] * k > 0 else "bear"))
    if len(parts) < 2:
        return insufficient("commodity_cycle", "macro", ticker,
                            f"only {len(parts)} commodity series loaded")

    p = sum(parts) / len(parts)
    ci = effective_interval(p, len(parts), floor=0.32)
    return ModuleReport.from_probability(
        "commodity_cycle", "macro", ticker, p, ci,
        thesis=(f"The commodity complex reads {p:.0%} cycle-positive across {len(parts)} inputs. "
                f"Copper leading gold indicates industrial demand; gold leading indicates fear."),
        evidence=ev,
        weaknesses=["Gold is treated as a fear proxy with a negative sign, which is wrong whenever "
                    "gold is rising on real-rate or currency dynamics instead.",
                    "Commodity futures front months carry roll and storage effects that contaminate "
                    "the trend reading.",
                    "Unless the instrument is a producer or heavy consumer, this is weak evidence "
                    "about the name."],
        horizon_days=63, n_obs=len(parts))


# ── Seasonality ─────────────────────────────────────────────────────────────

@module("seasonality", "macro",
        "The instrument's own calendar-month base rate over its full history.",
        horizon_days=HORIZON)
def seasonality(ticker: str) -> ModuleReport:
    c = md.closes(ticker, period="10y")
    if c is None or len(c) < 750:
        return insufficient("seasonality", "macro", ticker,
                            "fewer than three years of history — seasonality would be noise")
    month = datetime.now().month
    mask = pd.Series(c.index.month == month, index=c.index)
    hr = conditional_hit_rate(c, mask, HORIZON)
    est = hit_rate_probability(hr[0], hr[1], min_n=40)
    if est is None:
        return insufficient("seasonality", "macro", ticker,
                            f"only {hr[1]} observations for calendar month {month}")
    p, ci = est
    years = round(hr[1] / 21, 1)
    name = datetime.now().strftime("%B")
    lean = "bull" if p > 0.55 else "bear" if p < 0.45 else "neutral"
    src = md.source_label(ticker)

    return ModuleReport.from_probability(
        "seasonality", "macro", ticker, p, ci,
        thesis=(f"Entering in {name}, {ticker} closed the following {HORIZON} sessions higher "
                f"{hr[0]}/{hr[1]} times ({p:.0%}), mean {pct(hr[2], 2)}. That is roughly {years:.0f} "
                f"distinct Junes-equivalent of evidence — treat it as the weakest input in the stack."),
        evidence=[
            Evidence(f"{name} base rate {hr[0]}/{hr[1]} positive ({p:.0%})", round(p, 4), src, lean),
            Evidence(f"Mean forward {HORIZON}-day return entering in {name}: {pct(hr[2], 2)}",
                     round(hr[2], 4), src, "bull" if hr[2] > 0 else "bear"),
            Evidence(f"Sample spans roughly {years:.0f} independent {name} periods",
                     years, src, "neutral"),
        ],
        weaknesses=["Calendar effects are the most over-fitted phenomenon in finance; with ~10 "
                    "independent observations per month, this is close to noise and the Wilson "
                    "interval on overlapping daily windows overstates the evidence badly.",
                    overlap_weakness(HORIZON),
                    "Twelve months tested implicitly means multiple-comparison bias: one of them will "
                    "look significant by chance.",
                    "No adjustment for the drift of the underlying over the sample."],
        horizon_days=HORIZON, n_obs=hr[1])


# ── Cross-asset correlation ─────────────────────────────────────────────────

@module("cross_asset_correlation", "macro",
        "What the position is really exposed to once correlations are measured.",
        horizon_days=HORIZON)
def cross_asset_correlation(ticker: str) -> ModuleReport:
    r = md.returns(ticker, period="1y")
    if r is None or len(r) < 120:
        return insufficient("cross_asset_correlation", "macro", ticker,
                            "fewer than 120 return observations")
    rows, ev = [], []
    src = "Yahoo Finance (1y daily returns)"
    for label, sym in md.CROSS_ASSET.items():
        if sym == ticker:
            continue
        o = md.returns(sym, period="1y")
        if o is None:
            continue
        df = pd.concat([r.rename("a"), o.rename("b")], axis=1, sort=True).dropna()
        if len(df) < 100:
            continue
        full = float(df["a"].corr(df["b"]))
        recent = float(df.tail(63)["a"].corr(df.tail(63)["b"]))
        if math.isnan(full):
            continue
        rows.append((label, sym, full, recent))

    if len(rows) < 4:
        return insufficient("cross_asset_correlation", "macro", ticker,
                            f"only {len(rows)} cross-asset series aligned")

    rows.sort(key=lambda x: -abs(x[2]))
    for label, sym, full, recent in rows[:4]:
        drift = recent - full
        ev.append(Evidence(f"Correlation to {label} ({sym}): {full:+.2f} over a year, {recent:+.2f} "
                           f"over three months ({drift:+.2f} drift)", round(full, 3), src,
                           "neutral"))
    equity_corr = next((x[2] for x in rows if x[1] == md.BENCH), None)
    avg_abs = sum(abs(x[2]) for x in rows) / len(rows)
    rising = sum(1 for x in rows if x[3] > x[2] + 0.10)
    ev.append(Evidence(f"Average absolute cross-asset correlation {avg_abs:.2f}; {rising} of "
                       f"{len(rows)} correlations have risen more than 0.10 recently",
                       round(avg_abs, 3), src, "bear" if avg_abs > 0.6 else "neutral"))

    # Rising correlation means diversification is failing — a risk statement, so
    # this module stays close to neutral by design and hands the finding to risk.
    p = clamp(0.5 - 0.15 * (avg_abs - 0.4) - 0.03 * rising, 0.0, 1.0)
    ci = effective_interval(p, len(rows), floor=0.30)
    return ModuleReport.from_probability(
        "cross_asset_correlation", "macro", ticker, p, ci,
        thesis=(f"{ticker} carries an average absolute correlation of {avg_abs:.2f} across "
                f"{len(rows)} cross-asset proxies"
                + (f", including {equity_corr:+.2f} to {md.BENCH}" if equity_corr is not None else "")
                + f". {rising} of those correlations are rising, which means a position here "
                  f"diversifies less than it did."),
        evidence=ev,
        weaknesses=["Correlation is not exposure — a high correlation to oil does not establish that "
                    "oil drives this name.",
                    "Pearson correlation on daily returns understates tail co-movement, which is "
                    "exactly when it matters.",
                    "This module's directional output is near-neutral by construction; its real "
                    "contribution is to the risk layer, not to the view."],
        horizon_days=HORIZON, n_obs=len(rows))


# ── Liquidity ───────────────────────────────────────────────────────────────

@module("liquidity", "macro",
        "Tradability: dollar volume, Amihud impact and range — what an exit would cost.",
        horizon_days=HORIZON)
def liquidity(ticker: str) -> ModuleReport:
    df = md.history(ticker, period="1y")
    if df is None or "Volume" not in df.columns or len(df) < 60:
        return insufficient("liquidity", "macro", ticker,
                            "no volume history available for this instrument")
    df = df.dropna(subset=["Close"])
    vol = pd.to_numeric(df["Volume"], errors="coerce").fillna(0)
    dollar = (vol * df["Close"]).tail(21)
    adv = float(dollar.mean())
    if adv <= 0:
        return insufficient("liquidity", "macro", ticker, "reported volume is zero")

    ret = df["Close"].pct_change().abs()
    amihud = float((ret / (vol * df["Close"]).replace(0, float("nan"))).tail(63).mean() * 1e9)
    rng = float(((df["High"] - df["Low"]) / df["Close"]).tail(21).mean()) if "High" in df else float("nan")
    # Guard the denominator: a zero six-month volume mean would make this NaN.
    base_vol = float(vol.tail(126).mean()) if len(vol) > 126 else 0.0
    vol_trend = (float(vol.tail(21).mean()) / base_vol - 1) if base_vol > 0 else 0.0
    src = md.source_label(ticker)

    tier = ("deep" if adv > 5e8 else "adequate" if adv > 5e7 else
            "thin" if adv > 5e6 else "illiquid")
    p = {"deep": 0.52, "adequate": 0.50, "thin": 0.46, "illiquid": 0.40}[tier]
    ci = effective_interval(p, 3, floor=0.34)

    ev = [
        Evidence(f"21-day average daily dollar volume ${adv / 1e6:.1f}M — {tier}",
                 round(adv, 0), src, "bear" if tier in ("thin", "illiquid") else "neutral"),
        Evidence(f"Amihud illiquidity {amihud:.4f} (price impact per $1bn traded)",
                 round(amihud, 6), src, "bear" if amihud > 1 else "neutral"),
        Evidence(f"Average daily high-low range {pct(rng)}" if rng == rng else
                 "High/low range unavailable", round(rng, 4) if rng == rng else None, src, "neutral"),
        Evidence(f"Volume is {pct(vol_trend)} versus its six-month average", round(vol_trend, 4),
                 src, "neutral"),
    ]
    return ModuleReport.from_probability(
        "liquidity", "macro", ticker, p, ci,
        thesis=(f"{ticker} trades ${adv / 1e6:.1f}M a day — {tier}. Liquidity is not a directional "
                f"signal; it sets the maximum size that can be entered and exited without paying for "
                f"the privilege, and the risk layer uses it as a hard constraint."),
        evidence=ev,
        weaknesses=["No bid-ask spread or order-book depth in this feed — the true cost of exit is "
                    "estimated from daily bars only.",
                    "Amihud on daily data misses intraday impact entirely.",
                    "Deliberately near-neutral directionally: this module exists to constrain size, "
                    "and reading it as a view would be a misuse."],
        horizon_days=HORIZON, n_obs=3)
