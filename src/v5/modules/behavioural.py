"""
src/v5/modules/behavioural.py
=============================
Behavioural family — crowding, sentiment extremes, and bias detection applied to
ARIA's own output.

The third module is the uncomfortable one. It reads ARIA's own prediction log and
asks whether the platform has been systematically leaning one way, and whether
its stated confidence has been earned. When the answer is that ARIA has been
uniformly bullish, this module leans against the house. A system that audits the
market but not itself is only half a system.
"""
from __future__ import annotations

import math

import pandas as pd

from src.v5 import marketdata as md
from src.v5.contract import Evidence, ModuleReport, insufficient
from src.v5.modules._util import (clamp, conditional_hit_rate, effective_interval,
                                  hit_rate_probability, overlap_weakness, pct, rsi, sma)
from src.v5.registry import module

HORIZON = 21


# ── Crowding ────────────────────────────────────────────────────────────────

@module("crowding", "behavioural",
        "How crowded the trade already is: extension, volume, and ownership concentration.",
        horizon_days=HORIZON)
def crowding(ticker: str) -> ModuleReport:
    df = md.history(ticker, period="3y")
    if df is None or len(df) < 260:
        return insufficient("crowding", "behavioural", ticker, "fewer than 260 daily bars")
    c = df["Close"]
    ext = c / sma(c, 200) - 1.0
    cur_ext = float(ext.iloc[-1])
    ext_pct = float((ext.dropna() < cur_ext).mean())
    src = md.source_label(ticker)

    parts = [clamp(1.0 - ext_pct, 0.0, 1.0)]        # extended = crowded = less upside
    ev = [Evidence(f"Price is {pct(cur_ext)} from its 200-day average — the {ext_pct:.0%}th "
                   f"percentile of its own three-year range", round(cur_ext, 4), src,
                   "bear" if ext_pct > 0.85 else "bull" if ext_pct < 0.15 else "neutral")]

    # FX pairs and indices report zero volume; dividing by that mean gives NaN.
    # Absent volume is not "no surge", so the input is dropped entirely.
    if "Volume" in df.columns and len(df) > 252:
        v = pd.to_numeric(df["Volume"], errors="coerce").fillna(0)
        baseline = float(v.tail(252).mean())
        if baseline > 0:
            surge = float(v.tail(21).mean()) / baseline - 1
            parts.append(clamp(0.5 - surge * 0.5, 0.0, 1.0))
            ev.append(Evidence(f"21-day volume is {pct(surge)} versus its one-year average",
                               round(surge, 4), src, "bear" if surge > 0.5 else "neutral"))
        else:
            ev.append(Evidence("No reported volume for this instrument — the volume component of "
                               "crowding is unmeasurable here", None, src, "neutral"))

    f = md.fundamentals(ticker)
    inst = f.get("heldPercentInstitutions")
    if isinstance(inst, (int, float)):
        parts.append(clamp(1.0 - float(inst), 0.0, 1.0))
        ev.append(Evidence(f"Institutional ownership {pct(float(inst))} — the higher it is, the more "
                           f"of the marginal buyer is already in",
                           round(float(inst), 4), "Yahoo Finance fundamentals via universe dossier",
                           "bear" if float(inst) > 0.85 else "neutral"))
    insiders = f.get("heldPercentInsiders")
    if isinstance(insiders, (int, float)):
        ev.append(Evidence(f"Insider ownership {pct(float(insiders))}", round(float(insiders), 4),
                           "Yahoo Finance fundamentals via universe dossier", "neutral"))

    p = sum(parts) / len(parts)
    ci = effective_interval(p, len(parts), floor=0.28)
    return ModuleReport.from_probability(
        "crowding", "behavioural", ticker, p, ci,
        thesis=(f"Crowding reads {p:.0%} favourable across {len(parts)} measures. A trade everyone is "
                f"already in has no marginal buyer left; a trade nobody wants has no catalyst. This "
                f"module penalises extension, it does not fade strength on principle."),
        evidence=ev,
        weaknesses=["No short interest, no fund flow and no positioning survey in this feed — the "
                    "three best crowding measures are all missing.",
                    "Institutional ownership from the vendor is quarter-lagged and mechanically high "
                    "for any large-cap index constituent, which weakens it as a discriminator.",
                    "Extension percentile is measured against the name's own three-year history, so a "
                    "structurally re-rated business looks permanently crowded.",
                    "The mapping from each measure to a probability is a stated prior."],
        horizon_days=HORIZON, n_obs=len(parts))


# ── Sentiment extremes ──────────────────────────────────────────────────────

@module("sentiment_extremes", "behavioural",
        "Contrarian read at RSI and news-sentiment extremes, tested against the base rate.",
        horizon_days=10)
def sentiment_extremes(ticker: str) -> ModuleReport:
    h = 10
    c = md.closes(ticker, period="5y")
    if c is None or len(c) < 300:
        return insufficient("sentiment_extremes", "behavioural", ticker, "fewer than 300 daily closes")
    r = rsi(c, 14)
    cur = float(r.iloc[-1])
    src = md.source_label(ticker)

    if cur >= 75:
        mask, state = r >= 75, "euphoric (RSI ≥ 75)"
    elif cur <= 25:
        mask, state = r <= 25, "capitulating (RSI ≤ 25)"
    else:
        return insufficient("sentiment_extremes", "behavioural", ticker,
                            f"RSI {cur:.0f} is not at an extreme — this module only speaks at the tails")

    hr = conditional_hit_rate(c, mask, h)
    est = hit_rate_probability(hr[0], hr[1], min_n=15)
    if est is None:
        return insufficient("sentiment_extremes", "behavioural", ticker,
                            f"only {hr[1]} historical observations at this extreme")
    p, ci = est
    lean = "bull" if p > 0.55 else "bear" if p < 0.45 else "neutral"
    ev = [
        Evidence(f"RSI(14) at {cur:.0f} — {state}", round(cur, 1), src, lean),
        Evidence(f"From this extreme the next {h} sessions were positive {hr[0]}/{hr[1]} times "
                 f"({p:.0%})", round(p, 4), src, lean),
        Evidence(f"Mean forward {h}-day return from this extreme {pct(hr[2], 2)}",
                 round(hr[2], 4), src, "bull" if hr[2] > 0 else "bear"),
    ]
    s = md.sentiment(ticker)
    if s and s.get("n_articles"):
        sc = float(s.get("recency_weighted_score") or 0)
        ev.append(Evidence(f"News sentiment {sc:+.2f} across {s['n_articles']} articles "
                           f"({s.get('label', '?')}) — {'confirming' if (sc > 0) == (cur >= 75) else 'diverging from'} "
                           f"the price extreme", round(sc, 3), "data/sentiment_data.json", "neutral"))

    return ModuleReport.from_probability(
        "sentiment_extremes", "behavioural", ticker, p, ci,
        thesis=(f"{ticker} is {state}. The contrarian instinct is to fade it; this instrument's own "
                f"history says the next {h} sessions resolved up {p:.0%} of the time from here, which "
                f"is the only version of the contrarian argument worth acting on."),
        evidence=ev,
        weaknesses=[overlap_weakness(h),
                    "Extremes are rare by construction, so the sample is small and the interval wide — "
                    "that is honest, not conservative.",
                    "RSI extremes persist for weeks in a genuine trend; fading them is how people get "
                    "run over in momentum names.",
                    "This module stays silent outside the tails, so it contributes nothing most days "
                    "and should not be read as agreement when absent."],
        horizon_days=h, n_obs=hr[1])


# ── Bias detection on ARIA's own output ─────────────────────────────────────

@module("self_bias_audit", "behavioural",
        "Detects directional bias and overconfidence in ARIA's own recent output.",
        horizon_days=HORIZON)
def self_bias_audit(ticker: str) -> ModuleReport:
    from src.v5.learning import bias_snapshot

    snap = bias_snapshot()
    n = snap.get("n_predictions", 0)
    if n < 10:
        return insufficient("self_bias_audit", "behavioural", ticker,
                            f"only {n} logged predictions — not enough to measure ARIA's own bias")

    bull_share = float(snap.get("bull_share", 0.5))
    resolved = int(snap.get("n_resolved", 0))
    hit = snap.get("hit_rate")
    mean_conf = snap.get("mean_confidence")
    src = "data/v5/predictions.jsonl (ARIA's own prediction log)"

    # Lean against the house's own directional tilt. A platform that has been
    # 90% bullish is not observing a 90% bullish world; it is leaning.
    tilt = bull_share - 0.5
    p = clamp(0.5 - tilt * 0.4, 0.0, 1.0)
    ci = effective_interval(p, 4, floor=0.34)

    ev = [
        Evidence(f"ARIA has called {bull_share:.0%} of its last {n} predictions bullish",
                 round(bull_share, 3), src, "bear" if bull_share > 0.7 else
                 "bull" if bull_share < 0.3 else "neutral"),
    ]
    if resolved >= 10 and hit is not None:
        ev.append(Evidence(f"Resolved calls: {hit:.0%} correct across {resolved} outcomes",
                           round(float(hit), 3), src,
                           "bull" if hit > 0.55 else "bear" if hit < 0.45 else "neutral"))
        if mean_conf is not None:
            gap = float(mean_conf) - float(hit)
            ev.append(Evidence(f"Stated confidence averaged {float(mean_conf):.0%} against a "
                               f"{float(hit):.0%} hit rate — {'overconfident' if gap > 0.05 else 'calibrated' if abs(gap) <= 0.05 else 'underconfident'} "
                               f"by {gap * 100:+.0f}pp", round(gap, 3), src,
                               "bear" if gap > 0.10 else "neutral"))
    else:
        ev.append(Evidence(f"Only {resolved} predictions have resolved so far — calibration is not "
                           f"yet measurable", resolved, src, "neutral"))
    streak = snap.get("recent_streak")
    if streak:
        ev.append(Evidence(f"Most recent resolved run: {streak}", None, src, "neutral"))

    return ModuleReport.from_probability(
        "self_bias_audit", "behavioural", ticker, p, ci,
        thesis=(f"Across its last {n} logged predictions ARIA has been {bull_share:.0%} bullish"
                + (f" and {hit:.0%} correct on {resolved} resolved calls" if resolved >= 10 and hit is not None else "")
                + f". This module leans {abs(tilt) * 40:.0f} points against that tilt so that a "
                  f"house bias has to overcome its own audit before it reaches a recommendation."),
        evidence=ev,
        weaknesses=["This measures ARIA's output, not the market — it is a correction term and would "
                    "be actively harmful if it were ever the loudest voice in the ensemble.",
                    "A genuinely bullish market produces genuinely bullish output; this module cannot "
                    "distinguish justified persistence from bias, so it deliberately applies only a "
                    "small tilt.",
                    "The prediction log is short, so the bias estimate itself is noisy.",
                    "It says nothing whatsoever about this specific ticker."],
        horizon_days=HORIZON, n_obs=n)
