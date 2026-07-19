"""
src/desk/analysts/technical_agent.py
====================================
Technical analyst — grounds its opinion in signals.json (composite score,
trend, momentum, volatility, ML ensemble horizons). No LLM: the signal
engine already did the thinking; this agent cites it.
"""
from __future__ import annotations

from src.desk.opinion import Opinion, ev, load_data_json

SRC = "data/signals.json"
SRC_ML = "data/ml_predictions.json"


def opine(ticker: str) -> Opinion:
    sig = load_data_json("signals.json").get(ticker) or {}
    ml = load_data_json("ml_predictions.json").get(ticker) or {}

    if not sig:
        return Opinion(agent="technical", ticker=ticker, view="neutral", conviction=0,
                       thesis=f"No signal data for {ticker} — cannot form a technical view.",
                       evidence=[])

    score = sig.get("composite_score") or 0.0
    bull_p = sig.get("bullish_prob") or 0.5
    trend = sig.get("trend_score") or 0.0
    mom = sig.get("momentum_score") or 0.0
    vol = sig.get("realised_vol") or 0.0
    price = sig.get("current_price") or 0.0

    evidence = [
        ev(f"Composite signal score is {score:+.1f} ({sig.get('action', '?')})", round(score, 1), SRC,
           "bull" if score > 10 else "bear" if score < -10 else "neutral"),
        ev(f"Heuristic bullish probability is {bull_p:.0%}", round(bull_p, 3), SRC,
           "bull" if bull_p > 0.55 else "bear" if bull_p < 0.45 else "neutral"),
        ev(f"Trend score {trend:.0f}/100", round(trend, 1), SRC,
           "bull" if trend > 60 else "bear" if trend < 40 else "neutral"),
        ev(f"Momentum score {mom:.0f}/100", round(mom, 1), SRC,
           "bull" if mom > 60 else "bear" if mom < 40 else "neutral"),
        ev(f"Realised volatility {vol:.0%}", round(vol, 4), SRC,
           "bear" if vol > 0.45 else "neutral"),
        ev(f"Price ${price:.2f}, regime {sig.get('regime', '?')}", round(price, 2), SRC,
           "bull" if sig.get("regime") == "Bull" else "bear" if sig.get("regime") == "Bear" else "neutral"),
    ]

    # ML ensemble horizons — cite each horizon's directional probability
    horizons = (ml.get("horizons") or {})
    for h, hd in sorted(horizons.items(), key=lambda kv: int(kv[0])):
        if not isinstance(hd, dict):
            continue
        bp = hd.get("bullish_prob")
        if bp is None:
            continue
        evidence.append(ev(
            f"ML ensemble {h}d horizon: {hd.get('direction', '?')} ({bp:.0%} bullish, "
            f"agreement {hd.get('model_agreement', 0):.0%})",
            round(bp, 3), SRC_ML,
            "bull" if bp > 0.55 else "bear" if bp < 0.45 else "neutral"))

    # First signal driver / risk verbatim — real engine output, cited
    for d in (sig.get("drivers") or [])[:2]:
        if not str(d).startswith("[Phase"):
            evidence.append(ev(d, None, SRC, "bull" if score >= 0 else "neutral"))
    for r in (sig.get("risks") or [])[:2]:
        evidence.append(ev(r, None, SRC, "bear"))

    view = "bull" if score > 10 else "bear" if score < -10 else "neutral"
    # Conviction: distance from neutral in both the composite and the prob
    conviction = int(min(100, abs(score) + abs(bull_p - 0.5) * 100))
    conf = sig.get("confidence", "Low")
    if conf == "High":
        conviction = min(100, conviction + 15)
    elif conf == "Low":
        conviction = max(0, conviction - 15)

    thesis = (
        f"{ticker} scores {score:+.1f} composite ({sig.get('action', '?')}, {conf} confidence) with "
        f"{bull_p:.0%} bullish probability. Trend {trend:.0f} and momentum {mom:.0f} in a "
        f"{sig.get('regime', 'unknown')} regime; realised vol {vol:.0%}. "
        f"Engine stop ${sig.get('stop_loss', 0):.2f}, target ${sig.get('take_profit', 0):.2f}, "
        f"invalidation ${sig.get('invalidation', 0):.2f}."
    )
    return Opinion(agent="technical", ticker=ticker, view=view,
                   conviction=conviction, thesis=thesis, evidence=evidence)
