"""
ARIA Tool: get_news_sentiment
Fetches recent news headlines and runs NLP sentiment analysis.
Falls back to RSS feeds if NewsAPI key is not set.
"""

import asyncio
import re
from datetime import datetime, timedelta
from typing import Optional


# Simple lexicon-based sentiment (no heavy NLP dependency required)
POSITIVE_WORDS = {
    "beat", "beats", "surge", "surges", "rally", "rallies", "gain", "gains",
    "upgrade", "upgrades", "outperform", "record", "strong", "growth", "profit",
    "revenue", "exceed", "exceeds", "raise", "raises", "bullish", "upside",
    "buy", "overweight", "positive", "improved", "improving", "recovery",
    "deal", "acquisition", "partnership", "innovation", "breakthrough",
}

NEGATIVE_WORDS = {
    "miss", "misses", "drop", "drops", "fall", "falls", "cut", "cuts",
    "downgrade", "downgrades", "underperform", "loss", "losses", "decline",
    "weak", "warning", "layoff", "layoffs", "fraud", "investigation",
    "lawsuit", "fine", "penalty", "recall", "selloff", "bearish", "downside",
    "sell", "underweight", "negative", "worsen", "worsening", "concern",
    "debt", "bankruptcy", "default", "crash", "plunge", "plunges",
}

NEGATORS = {"not", "no", "never", "neither", "without", "despite", "fails", "fail"}


def _score_headline(text: str) -> float:
    """Simple negation-aware sentiment scorer. Returns -1.0 to +1.0."""
    words = re.findall(r"\b\w+\b", text.lower())
    score = 0
    for i, word in enumerate(words):
        context_words = words[max(0, i-3):i]
        negated = any(n in context_words for n in NEGATORS)
        if word in POSITIVE_WORDS:
            score += -0.5 if negated else 1.0
        elif word in NEGATIVE_WORDS:
            score += 0.5 if negated else -1.0

    # Normalise
    if abs(score) > 5:
        score = 5 * (score / abs(score))
    return round(score / 5, 3)


def _label(score: float) -> str:
    if score > 0.3:
        return "positive"
    elif score < -0.3:
        return "negative"
    return "neutral"


async def _fetch_from_newsapi(query: str, days: int, api_key: str) -> list:
    """Fetch from NewsAPI."""
    try:
        import httpx
        from_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        url = "https://newsapi.org/v2/everything"
        params = {
            "q": query,
            "from": from_date,
            "sortBy": "publishedAt",
            "pageSize": 20,
            "language": "en",
            "apiKey": api_key,
        }
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(url, params=params)
            data = r.json()
            return data.get("articles", [])
    except Exception as e:
        return []


async def _fetch_from_rss(query: str) -> list:
    """Fallback: fetch from Yahoo Finance RSS."""
    try:
        import httpx
        ticker = query.split()[0].upper()
        url = f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={ticker}&region=US&lang=en-US"
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(url)
            text = r.text

        # Simple XML parse
        titles = re.findall(r"<title><!\[CDATA\[(.+?)\]\]></title>", text)
        dates  = re.findall(r"<pubDate>(.+?)</pubDate>", text)

        articles = []
        for i, title in enumerate(titles[:15]):
            articles.append({
                "title": title,
                "publishedAt": dates[i] if i < len(dates) else "",
                "source": {"name": "Yahoo Finance"},
                "description": "",
            })
        return articles
    except Exception:
        return []


async def get_news_sentiment(query: str, days: int = 3, api_key: str = "") -> dict:
    """Fetch news and return headlines with sentiment scores."""
    if api_key:
        articles = await _fetch_from_newsapi(query, days, api_key)
    else:
        articles = await _fetch_from_rss(query)

    if not articles:
        return {
            "query": query,
            "status": "no_results",
            "message": "No news articles found. If NewsAPI key is set, check if key is valid.",
            "articles": [],
            "sentiment_summary": None,
        }

    processed = []
    for a in articles[:15]:
        title = a.get("title", "")
        desc  = a.get("description", "") or ""
        combined = f"{title} {desc}"
        score = _score_headline(combined)
        processed.append({
            "headline": title,
            "source": a.get("source", {}).get("name", "Unknown"),
            "published": a.get("publishedAt", ""),
            "sentiment_score": score,
            "sentiment_label": _label(score),
            "snippet": desc[:150] if desc else "",
        })

    # Sort by absolute score (most signal first)
    processed.sort(key=lambda x: abs(x["sentiment_score"]), reverse=True)

    # Aggregate
    scores  = [p["sentiment_score"] for p in processed]
    avg     = round(sum(scores) / len(scores), 3) if scores else 0
    pos_cnt = sum(1 for s in scores if s > 0.3)
    neg_cnt = sum(1 for s in scores if s < -0.3)
    neu_cnt = len(scores) - pos_cnt - neg_cnt

    overall_label = _label(avg)

    # Detect sentiment divergence
    divergence = None
    if pos_cnt > 0 and neg_cnt > 0:
        ratio = max(pos_cnt, neg_cnt) / (pos_cnt + neg_cnt)
        if ratio < 0.7:
            divergence = f"Mixed sentiment: {pos_cnt} positive, {neg_cnt} negative headlines — narrative is contested."

    return {
        "query": query,
        "days_covered": days,
        "total_articles": len(processed),
        "articles": processed[:10],
        "sentiment_summary": {
            "average_score": avg,
            "overall_label": overall_label,
            "positive_count": pos_cnt,
            "negative_count": neg_cnt,
            "neutral_count": neu_cnt,
            "divergence_note": divergence,
        },
        "data_source": "NewsAPI" if api_key else "Yahoo Finance RSS (no API key set)",
    }
