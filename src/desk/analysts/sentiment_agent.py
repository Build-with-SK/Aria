"""
src/desk/analysts/sentiment_agent.py
====================================
Sentiment analyst — grounds its opinion in the on-demand enrichment layer
(fresh NewsAPI headlines + lexicon sentiment + political-exposure flag).
Cited headlines are real; if there is no news key or no news, it abstains.
"""
from __future__ import annotations

import logging

from src.desk.opinion import Opinion, ev, load_data_json

logger = logging.getLogger(__name__)

SRC = "enrichment (NewsAPI, cached 30min)"

MIN_HEADLINES = 5      # below this the agent abstains — thin news is noise
MAX_CONVICTION = 60    # a keyword lexicon never outvotes the signal engine


def opine(ticker: str) -> Opinion:
    name = (load_data_json("signals.json").get(ticker) or {}).get("name", "")
    try:
        from src.data.enrichment import enrich
        data = enrich(ticker, name=name)
    except Exception as e:
        logger.warning(f"sentiment_agent: enrichment failed for {ticker}: {e}")
        data = {}

    news = data.get("news") or []
    senti = data.get("sentiment") or {}
    if not news:
        return Opinion(agent="sentiment", ticker=ticker, view="neutral", conviction=0,
                       thesis=f"No fresh news available for {ticker} "
                              f"({data.get('source', 'enrichment unavailable')}). Abstaining.",
                       evidence=[])
    # Calibration: a keyword lexicon over a handful of headlines is a weak
    # instrument — below MIN_HEADLINES it abstains rather than opines.
    if len(news) < MIN_HEADLINES:
        return Opinion(agent="sentiment", ticker=ticker, view="neutral", conviction=0,
                       thesis=f"Only {len(news)} fresh headlines for {ticker} "
                              f"(need ≥{MIN_HEADLINES} for a view). Abstaining.",
                       evidence=[])

    score = senti.get("score") or 0.0
    label = senti.get("label", "Neutral")
    pol = data.get("political_exposure", "Low")

    evidence = [
        ev(f"Headline sentiment {score:+.2f} ({label}) over {len(news)} fresh stories — "
           f"{senti.get('positive_hits', 0)} positive vs {senti.get('negative_hits', 0)} negative hits",
           score, SRC, "bull" if score > 0.2 else "bear" if score < -0.2 else "neutral"),
        ev(f"Political exposure flagged {pol} ({senti.get('political_hits', 0)} political headlines)",
           senti.get("political_hits", 0), SRC, "bear" if pol == "High" else "neutral"),
    ]
    # Cite the two most recent real headlines verbatim
    for h in news[:2]:
        evidence.append(ev(f"Headline: \"{h.get('title', '')[:110]}\" ({h.get('source', '?')})",
                           None, SRC, "neutral"))

    view = "bull" if score > 0.2 else "bear" if score < -0.2 else "neutral"
    conviction = int(min(MAX_CONVICTION, abs(score) * 100 + min(len(news), 8) * 3))
    if pol == "High":
        conviction = max(0, conviction - 10)

    thesis = (
        f"News flow on {name or ticker} reads {label} ({score:+.2f}) across {len(news)} "
        f"headlines in the last day; political exposure {pol}. "
        f"Sentiment is a fast-decaying signal — weight accordingly."
    )
    return Opinion(agent="sentiment", ticker=ticker, view=view,
                   conviction=conviction, thesis=thesis, evidence=evidence)
