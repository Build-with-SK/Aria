"""
sentiment_analyzer.py
=====================
Phase 3 — News sentiment analysis.

Sources used (all free, no API key required):
  1. yfinance .news — returns recent headlines per ticker
  2. RSS feeds from Reuters, Yahoo Finance, MarketWatch (via feedparser)
  3. Simple keyword-based sentiment scoring (no paid NLP API needed)

In Phase 4 this can be upgraded to:
  - OpenAI API for GPT-based sentiment classification
  - NewsAPI.org (free tier: 100 calls/day)
  - Benzinga / Unusual Whales APIs

Output: SentimentResult per ticker with bullish/bearish score -100 to +100
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

import yfinance as yf

logger = logging.getLogger(__name__)

# Try feedparser for RSS
try:
    import feedparser
    FEEDPARSER_AVAILABLE = True
except ImportError:
    FEEDPARSER_AVAILABLE = False
    logger.info("feedparser not installed — RSS news disabled. Run: pip install feedparser")


# ===========================================================================
# Keyword dictionaries for simple lexicon-based sentiment
# ===========================================================================

BULLISH_KEYWORDS = [
    "beat", "beats", "record", "surge", "surges", "rally", "rallies",
    "upgrade", "upgraded", "outperform", "buy", "strong", "growth",
    "profit", "revenue", "earnings beat", "raised guidance", "buyback",
    "acquisition", "partnership", "breakthrough", "approval", "approved",
    "bullish", "upside", "gain", "gains", "momentum", "positive",
    "exceeds", "exceed", "top", "high", "all-time", "expansion",
    "dividend", "investment", "wins", "won", "contract", "deal",
]

BEARISH_KEYWORDS = [
    "miss", "misses", "missed", "decline", "declines", "fall", "falls",
    "downgrade", "downgraded", "sell", "underperform", "weak", "loss",
    "losses", "layoff", "layoffs", "investigation", "lawsuit", "fine",
    "bearish", "downside", "cut", "cuts", "guidance cut", "warning",
    "risk", "concern", "worry", "recession", "inflation", "rate hike",
    "disappointing", "below", "debt", "bankruptcy", "fraud", "probe",
    "recall", "delay", "delays", "withdrew", "crash", "slump",
]

STRONG_BULLISH = ["record high", "all-time high", "blowout", "massive beat", "raises guidance"]
STRONG_BEARISH = ["investigation", "fraud", "bankruptcy", "massive loss", "guidance cut", "class action"]


@dataclass
class ArticleSentiment:
    """Sentiment for a single news article."""
    title:       str
    source:      str
    published:   str
    score:       float    # -1.0 to +1.0
    keywords:    List[str] = field(default_factory=list)


@dataclass
class SentimentResult:
    """Aggregated sentiment for one ticker."""
    ticker:          str
    n_articles:      int
    avg_score:       float         # -1.0 to +1.0
    sentiment_score: float         # -100 to +100 (for signal engine)
    label:           str           # "Bullish" / "Bearish" / "Neutral"
    top_headlines:   List[str] = field(default_factory=list)
    articles:        List[ArticleSentiment] = field(default_factory=list)
    warning:         str = ""


def _score_headline(text: str) -> tuple:
    """
    Score a headline using keyword matching.
    Returns (score, matched_keywords) where score is -1.0 to +1.0.
    """
    text_lower = text.lower()
    bull_hits  = [kw for kw in BULLISH_KEYWORDS if kw in text_lower]
    bear_hits  = [kw for kw in BEARISH_KEYWORDS if kw in text_lower]

    # Strong phrases override
    strong_bull = sum(1 for p in STRONG_BULLISH if p in text_lower)
    strong_bear = sum(1 for p in STRONG_BEARISH if p in text_lower)

    bull_score = len(bull_hits) + strong_bull * 2
    bear_score = len(bear_hits) + strong_bear * 2

    total = bull_score + bear_score
    if total == 0:
        return 0.0, []

    score = (bull_score - bear_score) / max(total, 1)
    score = max(-1.0, min(1.0, score))

    return round(score, 3), bull_hits + [f"-{b}" for b in bear_hits]


def _fetch_yfinance_news(ticker: str, limit: int = 15) -> List[ArticleSentiment]:
    """Fetch and score news from yfinance."""
    articles = []
    try:
        tk   = yf.Ticker(ticker)
        news = tk.news or []

        for item in news[:limit]:
            try:
                content = item.get("content", {})
                title   = content.get("title", "")
                source  = content.get("provider", {}).get("displayName", "Unknown")
                pub     = content.get("pubDate", "")

                if not title:
                    continue

                score, keywords = _score_headline(title)
                articles.append(ArticleSentiment(
                    title=title, source=source, published=pub,
                    score=score, keywords=keywords,
                ))
            except Exception:
                continue
    except Exception as e:
        logger.debug(f"yfinance news failed for {ticker}: {e}")

    return articles


def _fetch_rss_news(feed_url: str, ticker: str, limit: int = 10) -> List[ArticleSentiment]:
    """Fetch and score news from an RSS feed, filtering for ticker mentions."""
    if not FEEDPARSER_AVAILABLE:
        return []

    articles = []
    try:
        feed = feedparser.parse(feed_url)
        for entry in feed.entries[:limit * 3]:   # Fetch more, filter down
            title = entry.get("title", "")
            if not title:
                continue

            # Only include if ticker or company name is mentioned
            # (rough filter — avoids completely unrelated news)
            if ticker.replace("=X","").replace("^","").replace("-USD","").upper() not in title.upper():
                if len(articles) > 0:   # Accept first few regardless
                    continue

            source    = feed.feed.get("title", "RSS")
            published = entry.get("published", "")
            score, kw = _score_headline(title)
            articles.append(ArticleSentiment(
                title=title, source=source, published=published,
                score=score, keywords=kw,
            ))

            if len(articles) >= limit:
                break
    except Exception as e:
        logger.debug(f"RSS fetch failed for {feed_url}: {e}")

    return articles


# General market RSS feeds (used for indices and macro assets)
GENERAL_RSS_FEEDS = [
    "https://feeds.finance.yahoo.com/rss/2.0/headline?s={ticker}&region=US&lang=en-US",
]


def analyze_sentiment(ticker: str, company_name: str = "") -> SentimentResult:
    """
    Analyse news sentiment for one ticker.

    Returns SentimentResult with score from -100 to +100.
    """
    all_articles: List[ArticleSentiment] = []

    # Source 1: yfinance news
    yf_articles = _fetch_yfinance_news(ticker)
    all_articles.extend(yf_articles)

    # Source 2: Yahoo Finance RSS
    try:
        url = f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={ticker}&region=US&lang=en-US"
        rss_articles = _fetch_rss_news(url, ticker)
        all_articles.extend(rss_articles)
    except Exception:
        pass

    if not all_articles:
        return SentimentResult(
            ticker=ticker, n_articles=0, avg_score=0.0,
            sentiment_score=0.0, label="Neutral",
            warning="No news articles found",
        )

    # Remove duplicates by title
    seen   = set()
    unique = []
    for a in all_articles:
        if a.title not in seen:
            seen.add(a.title)
            unique.append(a)

    # Compute average score
    scores   = [a.score for a in unique]
    avg      = float(sum(scores) / len(scores)) if scores else 0.0
    sent_100 = round(avg * 100, 2)   # Scale to -100 → +100

    # Label
    if sent_100 > 15:
        label = "Bullish"
    elif sent_100 < -15:
        label = "Bearish"
    else:
        label = "Neutral"

    top_headlines = [a.title for a in sorted(unique, key=lambda x: abs(x.score), reverse=True)[:5]]

    return SentimentResult(
        ticker=ticker,
        n_articles=len(unique),
        avg_score=round(avg, 4),
        sentiment_score=sent_100,
        label=label,
        top_headlines=top_headlines,
        articles=unique[:10],
    )


def analyze_all_sentiment(config: dict) -> Dict[str, SentimentResult]:
    """
    Run sentiment analysis for all tickers in the universe.
    Returns dict: { ticker: SentimentResult }
    """
    results: Dict[str, SentimentResult] = {}
    metadata = {}

    for asset_class, assets in config.get("universe", {}).items():
        for asset in assets:
            ticker = asset["ticker"]
            name   = asset.get("name", ticker)
            metadata[ticker] = name

    for ticker, name in metadata.items():
        logger.info(f"Sentiment: {ticker}")
        try:
            result = analyze_sentiment(ticker, name)
            results[ticker] = result
        except Exception as e:
            logger.warning(f"Sentiment failed for {ticker}: {e}")
            results[ticker] = SentimentResult(
                ticker=ticker, n_articles=0, avg_score=0.0,
                sentiment_score=0.0, label="Neutral",
                warning=str(e),
            )

    bullish_count = sum(1 for r in results.values() if r.label == "Bullish")
    bearish_count = sum(1 for r in results.values() if r.label == "Bearish")
    logger.info(f"Sentiment complete: {bullish_count} bullish, {bearish_count} bearish of {len(results)}")
    return results


def sentiment_to_json(results: Dict[str, SentimentResult]) -> dict:
    """Serialise sentiment results to JSON-safe dict."""
    out = {}
    for ticker, r in results.items():
        out[ticker] = {
            "ticker":          r.ticker,
            "n_articles":      r.n_articles,
            "avg_score":       r.avg_score,
            "sentiment_score": r.sentiment_score,
            "label":           r.label,
            "top_headlines":   r.top_headlines,
            "warning":         r.warning,
        }
    return out
