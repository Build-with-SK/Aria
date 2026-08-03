"""
src/v5/modules/machine.py
=========================
Machine Intelligence family — ML ensemble, NLP/news, event detection, the
relationship graph, and two engines that exist but must not vote.

Reinforcement learning is registered `research_only` and always abstains from a
live recommendation. That is a deliberate architectural constraint, not a gap:
an RL policy that has not been validated out of sample has no business sizing
real capital, and making it structurally unable to vote is stronger than a
promise not to listen to it.

Alternative data is registered and honestly reports that no alternative dataset
is connected. A framework with nothing in it is worth naming so that it is
obvious what is missing, rather than quietly absent.
"""
from __future__ import annotations

import math
from datetime import datetime

import pandas as pd

from src.v5 import marketdata as md
from src.v5.contract import (Evidence, ModuleReport, bootstrap_interval, insufficient,
                             wilson_interval)
from src.v5.modules._util import (clamp, conditional_hit_rate, effective_interval,
                                  hit_rate_probability, overlap_weakness, pct)
from src.v5.registry import module

HORIZON = 21


def _age_days(iso: str | None) -> int | None:
    if not iso:
        return None
    try:
        return (datetime.now() - datetime.fromisoformat(str(iso)[:19])).days
    except Exception:
        return None


# ── ML ensemble ─────────────────────────────────────────────────────────────

@module("ml_ensemble", "machine",
        "ARIA's trained ensemble across forecast horizons, discounted for model agreement.",
        horizon_days=HORIZON)
def ml_ensemble(ticker: str) -> ModuleReport:
    pred = md.ml_prediction(ticker)
    horizons = (pred.get("horizons") or {}) if isinstance(pred, dict) else {}
    if not horizons:
        return insufficient("ml_ensemble", "machine", ticker,
                            "no trained ML prediction exists for this instrument")

    probs, agrees, ev = [], [], []
    src = "data/ml_predictions.json"
    for h, hd in sorted(horizons.items(), key=lambda kv: int(kv[0])):
        if not isinstance(hd, dict) or hd.get("bullish_prob") is None:
            continue
        bp = float(hd["bullish_prob"])
        ag = float(hd.get("model_agreement") or 0.0)
        probs.append(bp)
        agrees.append(ag)
        ev.append(Evidence(f"{h}-day horizon: {hd.get('direction', '?')} at {bp:.0%} bullish, "
                           f"model agreement {ag:.0%}", round(bp, 4), src,
                           "bull" if bp > 0.55 else "bear" if bp < 0.45 else "neutral"))
    if not probs:
        return insufficient("ml_ensemble", "machine", ticker,
                            "prediction record contains no usable horizon probabilities")

    p = sum(probs) / len(probs)
    ci = bootstrap_interval(probs) if len(probs) >= 3 else effective_interval(p, len(probs))
    # Disagreement between the underlying models must widen the interval.
    mean_ag = sum(agrees) / len(agrees) if agrees else 0.0
    widen = clamp(0.35 * (1 - mean_ag), 0.0, 0.35)
    ci = (max(0.0, ci[0] - widen / 2), min(1.0, ci[1] + widen / 2))

    if pred.get("models_trained"):
        ev.append(Evidence(f"{pred['models_trained']} models in the ensemble",
                           pred["models_trained"], src, "neutral"))
    if pred.get("warning"):
        ev.append(Evidence(f"Model warning: {pred['warning']}", None, src, "neutral"))

    weaknesses = [
        "The stored predictions carry no out-of-sample hit rate in this file, so their historical "
        "accuracy is unverified from here — the interval is widened for model disagreement instead.",
        "Financial ML is prone to look-ahead leakage through feature construction; this module cannot "
        "verify how the upstream features were built.",
        "Horizon probabilities from one ensemble are highly correlated with each other, so averaging "
        "them does not reduce error as much as the arithmetic suggests.",
    ]
    age = _age_days(pred.get("as_of") or pred.get("generated_at"))
    if age is not None and age > 3:
        weaknesses.insert(0, f"The stored prediction is {age} days old.")

    return ModuleReport.from_probability(
        "ml_ensemble", "machine", ticker, p, ci,
        thesis=(f"ARIA's ensemble averages {p:.0%} bullish across {len(probs)} horizons with "
                f"{mean_ag:.0%} mean agreement between its models. Agreement below 100% widens the "
                f"interval mechanically — disagreeing models are not a consensus."),
        evidence=ev, weaknesses=weaknesses,
        horizon_days=HORIZON, n_obs=len(probs))


# ── News / NLP ──────────────────────────────────────────────────────────────

@module("news_nlp", "machine",
        "Headline sentiment, recency-weighted, with the article count that produced it.",
        horizon_days=10)
def news_nlp(ticker: str) -> ModuleReport:
    s = md.sentiment(ticker)
    src = "data/sentiment_data.json"
    if not s:
        try:
            from src.data.enrichment import enrich
            live = enrich(ticker) or {}
            if live.get("sentiment") or live.get("headlines"):
                s = {"n_articles": len(live.get("headlines") or []),
                     "recency_weighted_score": (live.get("sentiment") or {}).get("score"),
                     "label": (live.get("sentiment") or {}).get("label"),
                     "top_headlines": [h.get("title") for h in (live.get("headlines") or [])[:5]]}
                src = "src/data/enrichment.py (NewsAPI)"
        except Exception:
            pass
    if not s:
        return insufficient("news_nlp", "machine", ticker, "no news coverage found for this instrument")

    n = int(s.get("n_articles") or 0)
    if n < 3:
        return insufficient("news_nlp", "machine", ticker,
                            f"only {n} articles — too thin for a sentiment estimate")

    raw = s.get("recency_weighted_score")
    if raw is None:
        raw = (s.get("sentiment_score") or 0) / 100.0
    score = float(raw)
    if abs(score) > 1:
        score = score / 100.0
    p = clamp(0.5 + score * 0.5, 0.0, 1.0)
    # Interval from article count: a handful of headlines cannot support a tight claim.
    lo, hi = wilson_interval(p * n, n)
    ci = (max(0.0, lo - 0.05), min(1.0, hi + 0.05))

    ev = [Evidence(f"Recency-weighted sentiment {score:+.2f} across {n} articles "
                   f"({s.get('label', 'unlabelled')})", round(score, 3), src,
                   "bull" if score > 0.1 else "bear" if score < -0.1 else "neutral")]
    for h in (s.get("top_headlines") or [])[:4]:
        if h:
            ev.append(Evidence(f"Headline: {h}", None, src, "neutral"))
    if s.get("warning"):
        ev.append(Evidence(f"Feed warning: {s['warning']}", None, src, "neutral"))

    return ModuleReport.from_probability(
        "news_nlp", "machine", ticker, p, ci,
        thesis=(f"News sentiment on {ticker} is {score:+.2f} across {n} recency-weighted articles. "
                f"Headline sentiment is mostly already in the price by the time it is measurable; "
                f"this is a check on positioning, not an edge."),
        evidence=ev,
        weaknesses=["Lexicon scoring of headlines misses sarcasm, negation and context, and it cannot "
                    "distinguish a company's news from the market's reaction to it.",
                    f"{n} articles is a small sample and the mix is whatever the news API surfaced — "
                    f"there is a real selection bias in coverage.",
                    "Sentiment is priced within minutes; a daily-frequency read of it carries little "
                    "forward information.",
                    "Headlines about ETFs holding the name are counted as if they were news about the "
                    "name itself."],
        horizon_days=10, n_obs=n)


# ── Event detection ─────────────────────────────────────────────────────────

@module("event_detection", "machine",
        "Abnormal price/volume events and the instrument's historical drift after them.",
        horizon_days=10)
def event_detection(ticker: str) -> ModuleReport:
    h = 10
    df = md.history(ticker, period="5y")
    if df is None or len(df) < 300:
        return insufficient("event_detection", "machine", ticker, "fewer than 300 daily bars")
    c = df["Close"]
    r = c.pct_change()
    sigma = r.rolling(63).std()
    shock = (r.abs() > 2.5 * sigma)

    vol_spike = pd.Series(False, index=df.index)
    if "Volume" in df.columns:
        v = pd.to_numeric(df["Volume"], errors="coerce")
        vol_spike = v > 2.0 * v.rolling(63).mean()

    event = (shock | (shock & vol_spike)).fillna(False)
    recent = bool(event.tail(5).any())
    if not recent:
        # No event: the module has nothing to say, and says so rather than
        # manufacturing a view from an absence.
        last = event[event].index[-1] if event.any() else None
        return insufficient("event_detection", "machine", ticker,
                            f"no abnormal price or volume event in the last five sessions"
                            + (f" (most recent: {last.date()})" if last is not None else ""))

    day = event.tail(5)[event.tail(5)].index[-1]
    move = float(r.loc[day])
    direction_mask = event & (r > 0) if move > 0 else event & (r < 0)
    hr = conditional_hit_rate(c, direction_mask, h)
    est = hit_rate_probability(hr[0], hr[1], min_n=15)
    if est is None:
        return insufficient("event_detection", "machine", ticker,
                            f"only {hr[1]} comparable past events to estimate drift from")
    p, ci = est
    src = md.source_label(ticker)
    lean = "bull" if p > 0.55 else "bear" if p < 0.45 else "neutral"

    return ModuleReport.from_probability(
        "event_detection", "machine", ticker, p, ci,
        thesis=(f"{ticker} moved {pct(move)} on {day.date()}, beyond 2.5 standard deviations of its "
                f"recent range. After comparable {'up' if move > 0 else 'down'} shocks, the next "
                f"{h} sessions were positive {hr[0]}/{hr[1]} times ({p:.0%})."),
        evidence=[
            Evidence(f"{pct(move)} move on {day.date()} — beyond 2.5σ of trailing 63-day volatility",
                     round(move, 4), src, "bull" if move > 0 else "bear"),
            Evidence(f"Post-event drift base rate: {hr[0]}/{hr[1]} positive over {h} sessions ({p:.0%})",
                     round(p, 4), src, lean),
            Evidence(f"Mean post-event {h}-day return {pct(hr[2], 2)}", round(hr[2], 4), src,
                     "bull" if hr[2] > 0 else "bear"),
            Evidence(f"Accompanied by a volume spike: {'yes' if bool(vol_spike.loc[day]) else 'no'}",
                     None, src, "neutral"),
        ],
        weaknesses=[overlap_weakness(h),
                    "The module detects that something happened, not what — an earnings gap, an index "
                    "rebalance and a fraud allegation all look identical here and drift very differently.",
                    "Post-event drift is one of the most heavily arbitraged effects in equities; the "
                    "historical base rate likely overstates what remains.",
                    "Threshold (2.5σ, 63-day window) is chosen, not optimised."],
        horizon_days=h, n_obs=hr[1])


# ── Relationship graph ──────────────────────────────────────────────────────

@module("relationship_graph", "machine",
        "Signal propagated from curated competitors and suppliers — a one-hop graph read.",
        horizon_days=HORIZON)
def relationship_graph(ticker: str) -> ModuleReport:
    rel = md.data_json("relationships.json")
    node = rel.get(ticker) if isinstance(rel, dict) else None
    if not isinstance(node, dict):
        return insufficient("relationship_graph", "machine", ticker,
                            "no curated competitor/supplier graph entry for this instrument")

    # Suppliers lead (their order books move first); competitors are read as a
    # sector read-across, not as a zero-sum inverse — both weights are stated.
    groups = {"suppliers": (node.get("suppliers") or [], 0.6),
              "competitors": (node.get("competitors") or [], 0.4)}
    contribs, ev = [], []
    src = "data/relationships.json + Yahoo Finance daily closes"
    for group, (symbols, weight) in groups.items():
        rets = []
        for sym in symbols[:6]:
            c = md.closes(sym, period="6mo")
            if c is None or len(c) < 30:
                continue
            m = float(c.iloc[-1] / c.iloc[-22] - 1)
            rets.append((sym, m))
        if not rets:
            continue
        avg = sum(m for _, m in rets) / len(rets)
        contribs.append((weight, clamp(0.5 + avg * 3, 0.0, 1.0), len(rets)))
        best = max(rets, key=lambda x: x[1])
        worst = min(rets, key=lambda x: x[1])
        ev.append(Evidence(f"{group.capitalize()} ({len(rets)} names) averaged {pct(avg)} over the "
                           f"last month — best {best[0]} {pct(best[1])}, worst {worst[0]} {pct(worst[1])}",
                           round(avg, 4), src, "bull" if avg > 0 else "bear"))

    if not contribs:
        return insufficient("relationship_graph", "machine", ticker,
                            "no price history loaded for any graph neighbour")

    wsum = sum(w for w, _, _ in contribs)
    p = sum(w * v for w, v, _ in contribs) / wsum
    n_names = sum(n for _, _, n in contribs)
    ci = effective_interval(p, max(2, n_names // 2), floor=0.26)

    own = md.closes(ticker, period="6mo")
    if own is not None and len(own) > 22:
        own_m = float(own.iloc[-1] / own.iloc[-22] - 1)
        ev.append(Evidence(f"{ticker} itself is {pct(own_m)} over the same month — "
                           f"{'lagging' if own_m < 0 else 'leading'} its network",
                           round(own_m, 4), md.source_label(ticker), "neutral"))

    return ModuleReport.from_probability(
        "relationship_graph", "machine", ticker, p, ci,
        thesis=(f"Across {n_names} curated suppliers and competitors, the network reads {p:.0%} "
                f"positive. Supply chains transmit demand information before it reaches the "
                f"end-market name."),
        evidence=ev,
        weaknesses=["The graph is hand-curated and static — it does not know about a supplier that was "
                    "replaced last quarter, and it has no edge weights beyond the two stated here.",
                    "One hop only: second-order effects and shared exposures are invisible.",
                    "Competitor strength is read as a positive read-across, which is exactly backwards "
                    "when the competitor is taking this company's share.",
                    "This is a graph *heuristic*, not a graph neural network — no learned message "
                    "passing is involved and calling it one would be a misdescription."],
        horizon_days=HORIZON, n_obs=n_names)


# ── Engines that exist but must not vote ────────────────────────────────────

@module("reinforcement_learning", "machine",
        "RL policy research — structurally barred from live recommendations.",
        horizon_days=HORIZON, research_only=True)
def reinforcement_learning(ticker: str) -> ModuleReport:
    return insufficient(
        "reinforcement_learning", "machine", ticker,
        "no validated RL policy is deployed, and by design an RL agent may not contribute to a live "
        "recommendation — it is registered research-only so that it is structurally unable to size "
        "capital, however good its backtest looks")


@module("alternative_data", "machine",
        "Alternative data framework — no alternative dataset is currently connected.",
        horizon_days=HORIZON, research_only=True)
def alternative_data(ticker: str) -> ModuleReport:
    return insufficient(
        "alternative_data", "machine", ticker,
        "no alternative dataset (card spend, satellite, web traffic, app downloads, job postings) is "
        "wired into this system. The framework is registered so the absence is visible in every module "
        "listing rather than silently missing")
