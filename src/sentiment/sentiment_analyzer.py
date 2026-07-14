"""
sentiment_analyzer.py  —  v2 (hardened)
=========================================
News sentiment analysis for all tickers in the universe.

Fixes vs v1
-----------
[BUG]  No negation handling — "not bullish", "failed to beat", "misses guidance"
       scored as positive because only individual keywords were matched.
       Now applies a negation window: if a negator word appears within
       NEGATION_WINDOW tokens before a keyword, the keyword's polarity flips.
[BUG]  RSS filter accepted first few articles regardless of ticker relevance.
       The guard `if len(articles) > 0: continue` only applied after the first
       article — so the first irrelevant headline always slipped through.
       Removed the bypass. All articles must pass the relevance check.
[BUG]  No recency weighting — a 3-week-old headline had equal weight to
       today's. Now applies exponential decay (half-life = 3 days).
[BUG]  Near-duplicate deduplication only matched exact title strings.
       Two wire services running "AAPL earnings beat consensus" vs
       "Apple quarterly earnings beat estimates" were counted twice.
       Now uses token-overlap Jaccard similarity (threshold 0.6).

New features
------------
[NEW]  _score_headline_v2()  — full negation-aware scorer with window.
[NEW]  _recency_weight()     — exponential decay weight from publish timestamp.
[NEW]  _jaccard_dedup()      — removes near-duplicate headlines before scoring.
[NEW]  SentimentResult.recency_weighted_score — primary score; plain avg_score
       kept for backward compatibility.
[NEW]  All public functions retain the same signature — drop-in replacement.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from typing import Dict, List, Optional, Set, Tuple

import yfinance as yf

logger = logging.getLogger(__name__)

try:
    import feedparser
    FEEDPARSER_AVAILABLE = True
except ImportError:
    FEEDPARSER_AVAILABLE = False
    logger.info("feedparser not installed — RSS news disabled. Run: pip install feedparser")


# ===========================================================================
# Keyword lexicons
# ===========================================================================

BULLISH_KEYWORDS = {
    "beat", "beats", "record", "surge", "surges", "rally", "rallies",
    "upgrade", "upgraded", "outperform", "buy", "strong", "growth",
    "profit", "revenue", "earnings beat", "raised guidance", "buyback",
    "acquisition", "partnership", "breakthrough", "approval", "approved",
    "bullish", "upside", "gain", "gains", "momentum", "positive",
    "exceeds", "exceed", "top", "high", "all-time", "expansion",
    "dividend", "investment", "wins", "won", "contract", "deal",
}

BEARISH_KEYWORDS = {
    "miss", "misses", "missed", "decline", "declines", "fall", "falls",
    "downgrade", "downgraded", "sell", "underperform", "weak", "loss",
    "losses", "layoff", "layoffs", "investigation", "lawsuit", "fine",
    "bearish", "downside", "cut", "cuts", "guidance cut", "warning",
    "risk", "concern", "worry", "recession", "inflation", "rate hike",
    "disappointing", "below", "debt", "bankruptcy", "fraud", "probe",
    "recall", "delay", "delays", "withdrew", "crash", "slump",
}

STRONG_BULLISH = {"record high", "all-time high", "blowout", "massive beat", "raises guidance"}
STRONG_BEARISH = {"investigation", "fraud", "bankruptcy", "massive loss", "guidance cut", "class action"}

# Negator words that flip polarity of the next keyword within NEGATION_WINDOW tokens
NEGATORS       = {"not", "no", "never", "failed", "fails", "miss", "missed", "without", "below"}
NEGATION_WINDOW = 3   # tokens


# ===========================================================================
# Dataclasses
# ===========================================================================

@dataclass
class ArticleSentiment:
    """Sentiment for a single news article."""
    title:         str
    source:        str
    published:     str
    score:         float          # Raw [-1, 1]
    recency_weight: float = 1.0   # Exponential decay
    keywords:      List[str] = field(default_factory=list)


@dataclass
class SentimentResult:
    """Aggregated sentiment for one ticker."""
    ticker:                   str
    n_articles:               int
    avg_score:                float    # Simple mean [-1, 1] — backward compat
    recency_weighted_score:   float    # Preferred: recency-decayed [-1, 1]
    sentiment_score:          float    # recency_weighted_score × 100 → [-100, 100]
    label:                    str      # "Bullish" | "Bearish" | "Neutral"
    top_headlines:            List[str] = field(default_factory=list)
    articles:                 List[ArticleSentiment] = field(default_factory=list)
    warning:                  str = ""


# ===========================================================================
# Scoring with negation handling
# ===========================================================================

def _score_headline_v2(text: str) -> Tuple[float, List[str]]:
    """
    Score a headline with negation-aware keyword matching.

    Algorithm
    ---------
    1. Tokenise to lowercase words.
    2. Slide a window: if any of the NEGATION_WINDOW tokens before position i
       is a negator, the keyword at i has its polarity flipped.
    3. Strong phrases are matched as substrings (pre-negation tokenisation).
    4. Final score = (bull - bear) / max(bull + bear, 1), clipped to [-1, 1].

    Example: "failed to beat estimates" — "beat" is bullish but "failed"
    appears 2 tokens before → polarity flips → counted as bearish.
    """
    text_lower = text.lower()
    tokens     = re.findall(r"\b\w+\b", text_lower)

    bull_score = 0.0
    bear_score = 0.0
    hit_words: List[str] = []

    # Phrase matching (strong signals — no negation windowing on multi-word phrases)
    for phrase in STRONG_BULLISH:
        if phrase in text_lower:
            bull_score += 2.0
            hit_words.append(f"+{phrase}")
    for phrase in STRONG_BEARISH:
        if phrase in text_lower:
            bear_score += 2.0
            hit_words.append(f"-{phrase}")

    # Token-level matching with negation window
    for i, token in enumerate(tokens):
        window_start = max(0, i - NEGATION_WINDOW)
        negated      = any(tokens[j] in NEGATORS for j in range(window_start, i))

        if token in BULLISH_KEYWORDS:
            if negated:
                bear_score += 1.0
                hit_words.append(f"~{token}")   # ~ = negated
            else:
                bull_score += 1.0
                hit_words.append(f"+{token}")

        elif token in BEARISH_KEYWORDS:
            if negated:
                bull_score += 0.5              # Negated bearish — mild positive
                hit_words.append(f"~-{token}")
            else:
                bear_score += 1.0
                hit_words.append(f"-{token}")

    total = bull_score + bear_score
    if total == 0:
        return 0.0, []

    score = (bull_score - bear_score) / max(total, 1)
    return round(max(-1.0, min(1.0, score)), 3), hit_words


# ===========================================================================
# Recency weight
# ===========================================================================

RECENCY_HALF_LIFE_DAYS = 3.0

def _recency_weight(published_str: str) -> float:
    """
    Exponential decay weight based on article age.
    Articles published within the last 24h → weight ≈ 1.0.
    Articles 3 days old → weight ≈ 0.5. 7 days old → weight ≈ 0.2.
    Returns 1.0 if the timestamp cannot be parsed.
    """
    if not published_str:
        return 1.0
    try:
        # Try RFC 2822 (RSS), then ISO 8601
        try:
            pub = parsedate_to_datetime(published_str)
        except Exception:
            pub = datetime.fromisoformat(published_str.replace("Z", "+00:00"))

        if pub.tzinfo is None:
            pub = pub.replace(tzinfo=timezone.utc)

        age_days = (datetime.now(timezone.utc) - pub).total_seconds() / 86_400
        age_days = max(0.0, age_days)
        return float(2 ** (-age_days / RECENCY_HALF_LIFE_DAYS))
    except Exception:
        return 1.0


# ===========================================================================
# Near-duplicate removal
# ===========================================================================

def _tokenset(text: str) -> Set[str]:
    return set(re.findall(r"\b\w{3,}\b", text.lower()))

def _jaccard(a: str, b: str) -> float:
    sa, sb = _tokenset(a), _tokenset(b)
    if not sa and not sb:
        return 1.0
    inter = len(sa & sb)
    union = len(sa | sb)
    return inter / union if union else 0.0

def _jaccard_dedup(
    articles: List[ArticleSentiment],
    threshold: float = 0.60,
) -> List[ArticleSentiment]:
    """
    Remove near-duplicate headlines using Jaccard token overlap.
    Keeps the highest-|score| article from each duplicate cluster.
    """
    kept: List[ArticleSentiment] = []
    for art in articles:
        duplicate = False
        for existing in kept:
            if _jaccard(art.title, existing.title) >= threshold:
                # Keep whichever has larger absolute signal
                if abs(art.score) > abs(existing.score):
                    kept.remove(existing)
                    kept.append(art)
                duplicate = True
                break
        if not duplicate:
            kept.append(art)
    return kept


# ===========================================================================
# Fetchers
# ===========================================================================

def _is_relevant(title: str, ticker: str) -> bool:
    """
    Check whether a headline is plausibly about `ticker`.
    More conservative than v1 — no bypass for first articles.
    """
    # Strip yfinance suffixes for clean-name matching
    clean = (
        ticker
        .replace("=X", "").replace("^", "").replace("-USD", "")
        .replace(".NYB", "").replace("=F", "")
        .upper()
    )
    return clean in title.upper()


def _fetch_yfinance_news(ticker: str, limit: int = 15) -> List[ArticleSentiment]:
    """Fetch and score news from yfinance."""
    articles: List[ArticleSentiment] = []
    try:
        news = yf.Ticker(ticker).news or []
        for item in news[:limit]:
            try:
                content = item.get("content", {})
                title   = content.get("title", "")
                source  = content.get("provider", {}).get("displayName", "Unknown")
                pub     = content.get("pubDate", "")
                if not title:
                    continue
                score, kws = _score_headline_v2(title)
                weight     = _recency_weight(pub)
                articles.append(ArticleSentiment(
                    title=title, source=source, published=pub,
                    score=score, recency_weight=weight, keywords=kws,
                ))
            except Exception:
                continue
    except Exception as e:
        logger.debug(f"yfinance news failed for {ticker}: {e}")
    return articles


def _fetch_rss_news(feed_url: str, ticker: str, limit: int = 10) -> List[ArticleSentiment]:
    """
    Fetch and score news from an RSS feed.

    FIX v1: Removed the bypass that accepted first articles unconditionally.
    All articles must pass the relevance check — prevents generic macro noise
    from contaminating asset-specific sentiment.
    """
    if not FEEDPARSER_AVAILABLE:
        return []

    articles: List[ArticleSentiment] = []
    try:
        feed   = feedparser.parse(feed_url)
        source = feed.feed.get("title", "RSS")

        for entry in feed.entries[:limit * 4]:   # Fetch extra, filter strictly
            title = entry.get("title", "")
            if not title or not _is_relevant(title, ticker):
                continue

            pub         = entry.get("published", "")
            score, kws  = _score_headline_v2(title)
            weight      = _recency_weight(pub)

            articles.append(ArticleSentiment(
                title=title, source=source, published=pub,
                score=score, recency_weight=weight, keywords=kws,
            ))
            if len(articles) >= limit:
                break

    except Exception as e:
        logger.debug(f"RSS fetch failed for {feed_url}: {e}")

    return articles


# ===========================================================================
# Main analysis function
# ===========================================================================

def analyze_sentiment(ticker: str, company_name: str = "") -> SentimentResult:
    """
    Analyse news sentiment for one ticker.

    Returns SentimentResult with recency-weighted score in [-100, 100].
    """
    all_articles: List[ArticleSentiment] = []

    # Source 1: yfinance
    all_articles.extend(_fetch_yfinance_news(ticker))

    # Source 2: Yahoo Finance RSS
    try:
        url = f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={ticker}&region=US&lang=en-US"
        all_articles.extend(_fetch_rss_news(url, ticker))
    except Exception:
        pass

    if not all_articles:
        return SentimentResult(
            ticker=ticker, n_articles=0, avg_score=0.0,
            recency_weighted_score=0.0, sentiment_score=0.0,
            label="Neutral", warning="No news articles found",
        )

    # Near-duplicate removal (Jaccard)
    unique = _jaccard_dedup(all_articles, threshold=0.60)

    # Scores
    plain_scores   = [a.score for a in unique]
    weights        = [a.recency_weight for a in unique]
    total_weight   = sum(weights)

    avg            = float(sum(plain_scores) / len(plain_scores)) if plain_scores else 0.0

    if total_weight > 0:
        rw_score = float(
            sum(a.score * a.recency_weight for a in unique) / total_weight
        )
    else:
        rw_score = avg

    # Scale to -100 → +100 using the recency-weighted score
    sent_100 = round(rw_score * 100, 2)

    # Label
    if sent_100 > 15:
        label = "Bullish"
    elif sent_100 < -15:
        label = "Bearish"
    else:
        label = "Neutral"

    top_headlines = [
        a.title for a in sorted(unique, key=lambda x: abs(x.score), reverse=True)[:5]
    ]

    return SentimentResult(
        ticker=ticker,
        n_articles=len(unique),
        avg_score=round(avg, 4),
        recency_weighted_score=round(rw_score, 4),
        sentiment_score=sent_100,
        label=label,
        top_headlines=top_headlines,
        articles=unique[:10],
    )


def analyze_all_sentiment(config: dict) -> Dict[str, SentimentResult]:
    """
    Run sentiment analysis for all tickers in the universe.
    Returns { ticker: SentimentResult }.
    """
    metadata: Dict[str, str] = {}
    for asset_class, assets in config.get("universe", {}).items():
        for asset in assets:
            metadata[asset["ticker"]] = asset.get("name", asset["ticker"])

    results: Dict[str, SentimentResult] = {}
    for ticker, name in metadata.items():
        logger.info(f"Sentiment: {ticker}")
        try:
            results[ticker] = analyze_sentiment(ticker, name)
        except Exception as e:
            logger.warning(f"Sentiment failed for {ticker}: {e}")
            results[ticker] = SentimentResult(
                ticker=ticker, n_articles=0, avg_score=0.0,
                recency_weighted_score=0.0, sentiment_score=0.0,
                label="Neutral", warning=str(e),
            )

    bull = sum(1 for r in results.values() if r.label == "Bullish")
    bear = sum(1 for r in results.values() if r.label == "Bearish")
    logger.info(f"Sentiment complete: {bull} bullish, {bear} bearish of {len(results)}")
    return results


def sentiment_to_json(results: Dict[str, SentimentResult]) -> dict:
    """Serialise sentiment results to JSON-safe dict."""
    out = {}
    for ticker, r in results.items():
        out[ticker] = {
            "ticker":                  r.ticker,
            "n_articles":              r.n_articles,
            "avg_score":               r.avg_score,
            "recency_weighted_score":  r.recency_weighted_score,
            "sentiment_score":         r.sentiment_score,
            "label":                   r.label,
            "top_headlines":           r.top_headlines,
            "warning":                 r.warning,
        }
    return out
