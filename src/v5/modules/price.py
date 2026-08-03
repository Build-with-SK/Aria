"""
src/v5/modules/price.py
=======================
Price & Trend family — Momentum, Trend Following, Mean Reversion, Technical
Analysis, Relative Strength, Breadth, Sector Rotation.

Each module states a condition, then asks the instrument's own history how often
that condition was followed by a positive forward return. The probability is the
empirical hit rate; the interval is Wilson on the real observation count. No
module here asserts a directional view it cannot count.
"""
from __future__ import annotations

import pandas as pd

from src.v5 import marketdata as md
from src.v5.contract import Evidence, ModuleReport, bootstrap_interval, insufficient
from src.v5.modules._util import (clamp, conditional_hit_rate, effective_interval,
                                  hit_rate_probability, logistic, overlap_weakness,
                                  pct, regime_weakness, rsi, sma, zscore)
from src.v5.registry import module

HORIZON = 21          # one trading month — the house forecast horizon
MIN_BARS = 260        # a year of daily data before any base rate is credible


# ── Momentum ────────────────────────────────────────────────────────────────

@module("momentum", "price",
        "12-1 month price momentum, scored against its own historical base rate.",
        horizon_days=HORIZON)
def momentum(ticker: str) -> ModuleReport:
    c = md.closes(ticker, period="5y")
    if c is None or len(c) < MIN_BARS:
        return insufficient("momentum", "price", ticker,
                            "fewer than 260 daily closes available")
    src = md.source_label(ticker)

    # 12-1: total return over the last 12 months excluding the most recent month
    mom = (c.shift(21) / c.shift(252) - 1.0).dropna()
    if len(mom) < 120:
        return insufficient("momentum", "price", ticker,
                            "not enough history to compute a 12-1 momentum series")
    current = float(mom.iloc[-1])
    rank = float((mom < current).mean())           # percentile within own history

    hi_cut = mom.quantile(0.66)
    lo_cut = mom.quantile(0.33)
    if current >= hi_cut:
        state, mask = "high", mom >= hi_cut
    elif current <= lo_cut:
        state, mask = "low", mom <= lo_cut
    else:
        state, mask = "middle", (mom > lo_cut) & (mom < hi_cut)

    hr = conditional_hit_rate(c, mask, HORIZON)
    est = hit_rate_probability(hr[0], hr[1])
    if est is None:
        return insufficient("momentum", "price", ticker,
                            f"only {hr[1]} historical observations in the {state} momentum state")
    p, ci = est
    lean = "bull" if p > 0.55 else "bear" if p < 0.45 else "neutral"

    evidence = [
        Evidence(f"12-1 momentum is {pct(current)}, the {rank:.0%}th percentile of its own 5y history",
                 round(current, 4), src, lean),
        Evidence(f"In the {state} momentum tercile historically, the next {HORIZON} sessions were "
                 f"positive {hr[0]}/{hr[1]} times ({p:.0%})", round(p, 4), src, lean),
        Evidence(f"Mean forward {HORIZON}-day return from this state: {pct(hr[2], 2)}",
                 round(hr[2], 4), src, "bull" if hr[2] > 0 else "bear"),
        Evidence(f"Last close {float(c.iloc[-1]):.2f}", round(float(c.iloc[-1]), 4), src, "neutral"),
    ]
    return ModuleReport.from_probability(
        "momentum", "price", ticker, p, ci,
        thesis=(f"{ticker} sits in the {state} tercile of its own 12-1 momentum distribution "
                f"({pct(current)}). That state has been followed by a positive {HORIZON}-day "
                f"return {p:.0%} of the time across {hr[1]} observations."),
        evidence=evidence,
        weaknesses=[overlap_weakness(HORIZON), regime_weakness(),
                    "Momentum is a crowded factor; the base rate does not know how crowded it is today.",
                    "Single-name momentum reverses violently around earnings and index events."],
        horizon_days=HORIZON, n_obs=hr[1])


# ── Trend following ─────────────────────────────────────────────────────────

@module("trend_following", "price",
        "50/200-day moving-average trend state and its historical forward base rate.",
        horizon_days=HORIZON)
def trend_following(ticker: str) -> ModuleReport:
    c = md.closes(ticker, period="5y")
    if c is None or len(c) < MIN_BARS:
        return insufficient("trend_following", "price", ticker,
                            "fewer than 260 daily closes available")
    src = md.source_label(ticker)
    ma50, ma200 = sma(c, 50), sma(c, 200)
    above = (c > ma200)
    golden = (ma50 > ma200)
    state_mask = above & golden
    label = "uptrend (price>200dma, 50>200)"
    if not bool(state_mask.iloc[-1]):
        state_mask = ~(above & golden)
        label = "not in confirmed uptrend"

    hr = conditional_hit_rate(c, state_mask, HORIZON)
    est = hit_rate_probability(hr[0], hr[1])
    if est is None:
        return insufficient("trend_following", "price", ticker,
                            f"only {hr[1]} observations in the current trend state")
    p, ci = est
    last, m50, m200 = float(c.iloc[-1]), float(ma50.iloc[-1]), float(ma200.iloc[-1])
    lean = "bull" if p > 0.55 else "bear" if p < 0.45 else "neutral"

    evidence = [
        Evidence(f"Price {last:.2f} vs 50dma {m50:.2f} ({(last/m50-1)*100:+.1f}%) "
                 f"and 200dma {m200:.2f} ({(last/m200-1)*100:+.1f}%)",
                 round(last, 4), src, "bull" if last > m200 else "bear"),
        Evidence(f"Current state: {label}", None, src, lean),
        Evidence(f"That state was followed by a positive {HORIZON}-day return {hr[0]}/{hr[1]} "
                 f"times ({p:.0%})", round(p, 4), src, lean),
        Evidence(f"50dma is {'above' if m50 > m200 else 'below'} the 200dma by "
                 f"{abs(m50/m200-1)*100:.1f}%", round(m50 / m200 - 1, 4), src,
                 "bull" if m50 > m200 else "bear"),
    ]
    return ModuleReport.from_probability(
        "trend_following", "price", ticker, p, ci,
        thesis=(f"{ticker} is in a {label}. Historically this state resolved positively over the "
                f"next {HORIZON} sessions {p:.0%} of the time ({hr[1]} observations)."),
        evidence=evidence,
        weaknesses=[overlap_weakness(HORIZON), regime_weakness(),
                    "Moving-average states whipsaw in range-bound tape; the base rate averages "
                    "over both trending and chopping periods.",
                    "The rule is discrete — it says nothing about how far price has extended."],
        horizon_days=HORIZON, n_obs=hr[1])


# ── Mean reversion ──────────────────────────────────────────────────────────

@module("mean_reversion", "price",
        "Deviation from the 20-day mean plus RSI, tested against its own reversion base rate.",
        horizon_days=10)
def mean_reversion(ticker: str) -> ModuleReport:
    h = 10
    c = md.closes(ticker, period="5y")
    if c is None or len(c) < MIN_BARS:
        return insufficient("mean_reversion", "price", ticker,
                            "fewer than 260 daily closes available")
    src = md.source_label(ticker)
    z = zscore(c / sma(c, 20), 120)
    r = rsi(c, 14)
    cur_z, cur_r = float(z.iloc[-1]), float(r.iloc[-1])

    if cur_z <= -1.0 or cur_r <= 35:
        mask, state = (z <= -1.0) | (r <= 35), "stretched below the mean"
    elif cur_z >= 1.0 or cur_r >= 70:
        mask, state = (z >= 1.0) | (r >= 70), "stretched above the mean"
    else:
        mask, state = (z.abs() < 1.0) & (r.between(35, 70)), "near its mean"

    hr = conditional_hit_rate(c, mask, h)
    est = hit_rate_probability(hr[0], hr[1])
    if est is None:
        return insufficient("mean_reversion", "price", ticker,
                            f"only {hr[1]} observations in the '{state}' state")
    p, ci = est
    lean = "bull" if p > 0.55 else "bear" if p < 0.45 else "neutral"
    evidence = [
        Evidence(f"Price/20dma z-score {cur_z:+.2f}, RSI(14) {cur_r:.0f} — {state}",
                 round(cur_z, 3), src, lean),
        Evidence(f"From this state the next {h} sessions were positive {hr[0]}/{hr[1]} "
                 f"times ({p:.0%})", round(p, 4), src, lean),
        Evidence(f"Mean forward {h}-day return from this state {pct(hr[2], 2)}",
                 round(hr[2], 4), src, "bull" if hr[2] > 0 else "bear"),
    ]
    return ModuleReport.from_probability(
        "mean_reversion", "price", ticker, p, ci,
        thesis=(f"{ticker} is {state} (z {cur_z:+.2f}, RSI {cur_r:.0f}). Historically that "
                f"resolved up over {h} sessions {p:.0%} of the time."),
        evidence=evidence,
        weaknesses=[overlap_weakness(h),
                    "Mean reversion and trend following disagree by construction — when both fire, "
                    "the ensemble's dispersion penalty is doing the real work.",
                    "The estimator cannot distinguish an oversold dip from the start of a repricing "
                    "on news; it has no fundamental input at all."],
        horizon_days=h, n_obs=hr[1])


# ── Technical analysis (cites the existing engines) ─────────────────────────

@module("technical", "price",
        "Classic indicator consensus from ARIA's signal engine and multi-timeframe summary.",
        horizon_days=HORIZON)
def technical(ticker: str) -> ModuleReport:
    sig = md.signal(ticker)
    summary = {}
    try:
        from src.data.technical_summary import summary as tsum
        summary = tsum(ticker, "1d") or {}
    except Exception:
        summary = {}

    if not sig and "error" in summary or (not sig and not summary):
        return insufficient("technical", "price", ticker,
                            "neither signals.json nor the technical summary returned data")

    evidence, votes = [], []
    if sig:
        score = float(sig.get("composite_score") or 0.0)
        bp = float(sig.get("bullish_prob") or 0.5)
        votes.append(clamp(0.5 + score / 200.0, 0.0, 1.0))
        votes.append(bp)
        evidence += [
            Evidence(f"Composite signal score {score:+.1f} ({sig.get('action', '?')}, "
                     f"{sig.get('confidence', '?')} confidence)", round(score, 2),
                     "data/signals.json", "bull" if score > 10 else "bear" if score < -10 else "neutral"),
            Evidence(f"Signal-engine bullish probability {bp:.0%}", round(bp, 3),
                     "data/signals.json", "bull" if bp > 0.55 else "bear" if bp < 0.45 else "neutral"),
            Evidence(f"Trend score {float(sig.get('trend_score') or 0):.0f}/100, momentum "
                     f"{float(sig.get('momentum_score') or 0):.0f}/100",
                     round(float(sig.get("trend_score") or 0), 1), "data/signals.json",
                     "bull" if float(sig.get("trend_score") or 0) > 60 else "neutral"),
        ]
        for r in (sig.get("risks") or [])[:2]:
            evidence.append(Evidence(str(r), None, "data/signals.json", "bear"))

    if summary and "counts" in summary:
        cnt = summary["counts"]
        tot = max(1, cnt.get("buy", 0) + cnt.get("sell", 0))
        votes.append(cnt.get("buy", 0) / tot)
        evidence.append(Evidence(
            f"Indicator tally on the daily: {cnt.get('buy', 0)} buy / {cnt.get('sell', 0)} sell / "
            f"{cnt.get('neutral', 0)} neutral → {summary.get('summary')}",
            cnt.get("buy", 0), "src/data/technical_summary.py (yfinance daily)",
            "bull" if cnt.get("buy", 0) > cnt.get("sell", 0) else "bear"))

    if not votes:
        return insufficient("technical", "price", ticker, "no usable indicator readings")

    p = sum(votes) / len(votes)
    ci = bootstrap_interval(votes) if len(votes) >= 3 else effective_interval(p, len(votes))
    if ci[1] - ci[0] < 0.12:                # indicators are not independent; do not overclaim
        ci = (max(0.0, ci[0] - 0.06), min(1.0, ci[1] + 0.06))

    return ModuleReport.from_probability(
        "technical", "price", ticker, p, ci,
        thesis=(f"Indicator consensus on {ticker} is {p:.0%} bullish across {len(votes)} independent "
                f"readings from the signal engine and the multi-timeframe summary."),
        evidence=evidence,
        weaknesses=["The indicators are highly correlated with one another — the interval is widened "
                    "manually to reflect that, which is a judgment call, not a measurement.",
                    "Classic indicators are lagging by construction.",
                    "No forward-outcome calibration is applied to these specific indicator settings."],
        horizon_days=HORIZON, n_obs=len(votes))


# ── Relative strength ───────────────────────────────────────────────────────

@module("relative_strength", "price",
        "Strength of the instrument against the benchmark, with its own base rate.",
        horizon_days=HORIZON)
def relative_strength(ticker: str) -> ModuleReport:
    c = md.closes(ticker, period="3y")
    b = md.closes(md.BENCH, period="3y")
    if c is None or b is None or len(c) < MIN_BARS:
        return insufficient("relative_strength", "price", ticker,
                            "missing instrument or benchmark history")
    df = pd.concat([c.rename("x"), b.rename("b")], axis=1, sort=True).dropna()
    if len(df) < MIN_BARS:
        return insufficient("relative_strength", "price", ticker,
                            "fewer than 260 overlapping sessions with the benchmark")
    ratio = df["x"] / df["b"]
    rs_ma = sma(ratio, 50)
    strong = ratio > rs_ma
    mask = strong if bool(strong.iloc[-1]) else ~strong
    state = "outperforming" if bool(strong.iloc[-1]) else "underperforming"

    hr = conditional_hit_rate(df["x"], mask, HORIZON)
    est = hit_rate_probability(hr[0], hr[1])
    if est is None:
        return insufficient("relative_strength", "price", ticker,
                            f"only {hr[1]} observations while {state}")
    p, ci = est
    r63 = float(df["x"].iloc[-1] / df["x"].iloc[-64] - 1) if len(df) > 64 else 0.0
    b63 = float(df["b"].iloc[-1] / df["b"].iloc[-64] - 1) if len(df) > 64 else 0.0
    lean = "bull" if p > 0.55 else "bear" if p < 0.45 else "neutral"
    src = md.source_label(ticker)

    return ModuleReport.from_probability(
        "relative_strength", "price", ticker, p, ci,
        thesis=(f"{ticker} is {state} {md.BENCH} on a 50-day relative-strength basis "
                f"({pct(r63)} vs {pct(b63)} over three months). That state historically "
                f"preceded a positive {HORIZON}-day return {p:.0%} of the time."),
        evidence=[
            Evidence(f"3-month return {pct(r63)} vs benchmark {pct(b63)} "
                     f"({(r63 - b63) * 100:+.1f}pp)", round(r63 - b63, 4), src,
                     "bull" if r63 > b63 else "bear"),
            Evidence(f"Relative-strength line is {state} its own 50-day average", None, src, lean),
            Evidence(f"Base rate while {state}: {hr[0]}/{hr[1]} positive ({p:.0%})",
                     round(p, 4), src, lean),
        ],
        weaknesses=[overlap_weakness(HORIZON),
                    f"{md.BENCH} is a US large-cap proxy; for a non-US or non-equity instrument the "
                    f"comparison is only loosely meaningful.",
                    "Relative strength says nothing about absolute direction — a stock can outperform "
                    "all the way down."],
        horizon_days=HORIZON, n_obs=hr[1])


# ── Breadth ─────────────────────────────────────────────────────────────────

@module("breadth", "price",
        "Market breadth from sector-ETF participation above their 50-day averages.",
        horizon_days=HORIZON)
def breadth(ticker: str) -> ModuleReport:
    above, total, detail = 0, 0, []
    for sector, etf in list(md.SECTOR_ETFS.items()):
        c = md.closes(etf, period="1y")
        if c is None or len(c) < 60:
            continue
        m = sma(c, 50)
        ok = float(c.iloc[-1]) > float(m.iloc[-1])
        total += 1
        above += 1 if ok else 0
        detail.append((sector, etf, ok, float(c.iloc[-1] / m.iloc[-1] - 1)))

    if total < 5:
        return insufficient("breadth", "price", ticker,
                            f"only {total} sector ETFs returned usable history")

    frac = above / total
    p = clamp(0.35 + 0.30 * frac, 0.0, 1.0)     # breadth tilts, it does not determine
    ci = effective_interval(p, total, floor=0.18)
    lean = "bull" if frac > 0.6 else "bear" if frac < 0.4 else "neutral"
    src = "Yahoo Finance (sector ETF daily closes)"
    evidence = [Evidence(f"{above}/{total} S&P sector ETFs are above their 50-day average "
                         f"({frac:.0%} participation)", round(frac, 3), src, lean)]
    for sector, etf, ok, dist in sorted(detail, key=lambda d: -d[3])[:3]:
        evidence.append(Evidence(f"{sector} ({etf}) is {dist * 100:+.1f}% vs its 50dma",
                                 round(dist, 4), src, "bull" if ok else "bear"))
    for sector, etf, ok, dist in sorted(detail, key=lambda d: d[3])[:2]:
        evidence.append(Evidence(f"{sector} ({etf}) is {dist * 100:+.1f}% vs its 50dma",
                                 round(dist, 4), src, "bull" if ok else "bear"))

    return ModuleReport.from_probability(
        "breadth", "price", ticker, p, ci,
        thesis=(f"Breadth is {frac:.0%}: {above} of {total} sectors participate. Narrow breadth "
                f"makes any single-name long more fragile; broad breadth is a tailwind, not a signal."),
        evidence=evidence,
        weaknesses=["This is a market-level reading applied to a single name — it is context, not a "
                    "stock-specific forecast, and it is weighted accordingly.",
                    "Sector ETFs are a coarse breadth proxy versus an advance/decline line on full "
                    "index membership.",
                    "US-centric: for an Indian or UK listing this measures the wrong market.",
                    "The probability mapping (0.35 + 0.30 × participation) is a stated prior, not an "
                    "estimated relationship."],
        horizon_days=HORIZON, n_obs=total)


# ── Sector rotation ─────────────────────────────────────────────────────────

@module("sector_rotation", "price",
        "Whether capital is rotating into or out of the instrument's own sector.",
        horizon_days=HORIZON)
def sector_rotation(ticker: str) -> ModuleReport:
    sector = md.sector_of(ticker)
    etf = md.SECTOR_ETFS.get(sector or "")
    if not etf:
        return insufficient("sector_rotation", "price", ticker,
                            f"no sector ETF mapping for sector '{sector or 'unknown'}'")
    s = md.closes(etf, period="2y")
    b = md.closes(md.BENCH, period="2y")
    if s is None or b is None:
        return insufficient("sector_rotation", "price", ticker,
                            f"missing history for {etf} or {md.BENCH}")
    df = pd.concat([s.rename("s"), b.rename("b")], axis=1, sort=True).dropna()
    if len(df) < 130:
        return insufficient("sector_rotation", "price", ticker,
                            "fewer than 130 overlapping sector/benchmark sessions")

    ratio = df["s"] / df["b"]
    r1 = float(ratio.iloc[-1] / ratio.iloc[-22] - 1) if len(ratio) > 22 else 0.0
    r3 = float(ratio.iloc[-1] / ratio.iloc[-64] - 1) if len(ratio) > 64 else 0.0
    trend = float(ratio.iloc[-1] / float(sma(ratio, 50).iloc[-1]) - 1)

    # Coefficients are deliberately gentle: a 4% one-month relative move is real
    # information but it is not a 98% probability, and a steeper mapping produced
    # exactly that kind of false precision.
    score = 12 * r1 + 8 * r3 + 6 * trend
    p = logistic(score, k=1.0)
    ci = effective_interval(p, 3, floor=0.22)   # three overlapping windows, not independent
    lean = "bull" if p > 0.55 else "bear" if p < 0.45 else "neutral"
    src = f"Yahoo Finance ({etf} vs {md.BENCH} daily closes)"

    return ModuleReport.from_probability(
        "sector_rotation", "price", ticker, p, ci,
        thesis=(f"{sector} ({etf}) is {'gaining' if r3 > 0 else 'losing'} relative share versus "
                f"{md.BENCH}: {pct(r1)} over one month, {pct(r3)} over three. Rotation is a tailwind "
                f"or headwind to {ticker}, not a reason to own it."),
        evidence=[
            Evidence(f"{etf}/{md.BENCH} ratio {pct(r1)} over 1 month", round(r1, 4), src,
                     "bull" if r1 > 0 else "bear"),
            Evidence(f"{etf}/{md.BENCH} ratio {pct(r3)} over 3 months", round(r3, 4), src,
                     "bull" if r3 > 0 else "bear"),
            Evidence(f"Ratio is {trend * 100:+.1f}% versus its own 50-day average",
                     round(trend, 4), src, "bull" if trend > 0 else "bear"),
            Evidence(f"Sector classification: {sector}", None,
                     "Yahoo Finance fundamentals via universe dossier", "neutral"),
        ],
        weaknesses=["Sector membership comes from the vendor's classification, which can be stale or "
                    "wrong for conglomerates.",
                    "The score-to-probability mapping is a logistic prior, not a fitted model — it is "
                    "reproducible but not calibrated to outcomes.",
                    "A stock frequently diverges from its sector; this is the weakest possible form of "
                    "single-name evidence."],
        horizon_days=HORIZON, n_obs=3)
