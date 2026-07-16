"""
src/data/enrichment.py
======================
On-demand enrichment for a searched symbol: fresh news headlines, a quick
sentiment read, and a political-exposure flag. Nothing here is pre-computed
or stored in bulk — it runs when the user searches a ticker, so the info is
always up to date. Cached briefly (per-symbol, ~30 min) to stay snappy.

Sources: NewsAPI (needs NEWS_API_KEY). Sentiment is a light lexicon read of
the headlines — fast, no model load. Political exposure is a keyword scan.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent.parent.parent
CACHE_DIR = ROOT / "data" / "cache" / "enrichment"
CACHE_TTL_MIN = 30

# tiny finance sentiment lexicon (headline-level, fast)
_POS = {"surge", "soar", "jump", "rally", "beat", "beats", "record", "growth", "profit",
        "gains", "gain", "upgrade", "bullish", "rise", "rises", "strong", "outperform",
        "wins", "win", "boost", "raises", "high", "recovery", "rebound", "expands", "approval"}
_NEG = {"plunge", "slump", "fall", "falls", "drop", "drops", "miss", "misses", "loss",
        "losses", "cut", "cuts", "downgrade", "bearish", "weak", "warning", "warns",
        "probe", "lawsuit", "fraud", "slump", "decline", "fears", "crash", "sinks",
        "layoffs", "recall", "ban", "fine", "sanctions", "default", "scandal"}
_POLITICAL = {"tariff", "sanction", "regulation", "regulator", "policy", "government",
              "election", "parliament", "central bank", "fed", "rbi", "boe", "budget",
              "tax", "nationalis", "subsidy", "trade war", "geopolit", "war", "ban"}


def _cache_path(symbol: str) -> Path:
    safe = symbol.replace("/", "_").replace("=", "_").replace("^", "_").replace(".", "_")
    return CACHE_DIR / f"{safe}.json"


def _newsapi(query: str, n: int = 8) -> list:
    key = os.environ.get("NEWS_API_KEY", "")
    if not key:
        return []
    params = urllib.parse.urlencode({
        "q": query, "sortBy": "publishedAt", "language": "en",
        "pageSize": n, "apiKey": key,
    })
    try:
        with urllib.request.urlopen(f"https://newsapi.org/v2/everything?{params}", timeout=12) as r:
            data = json.loads(r.read())
        out = []
        for a in data.get("articles", [])[:n]:
            out.append({
                "title": a.get("title", ""),
                "source": (a.get("source") or {}).get("name", ""),
                "url": a.get("url", ""),
                "at": a.get("publishedAt", ""),
            })
        return out
    except Exception as e:
        logger.warning(f"NewsAPI fetch failed for '{query}': {e}")
        return []


def _score_headlines(headlines: list) -> dict:
    pos = neg = pol = 0
    for h in headlines:
        words = set(h.get("title", "").lower().replace(",", " ").replace(".", " ").split())
        pos += len(words & _POS)
        neg += len(words & _NEG)
        low = h.get("title", "").lower()
        pol += any(p in low for p in _POLITICAL)
    total = pos + neg
    score = 0.0 if total == 0 else round((pos - neg) / total, 2)   # -1 .. +1
    label = ("Bullish" if score > 0.2 else "Bearish" if score < -0.2 else "Neutral")
    return {"score": score, "label": label, "positive_hits": pos,
            "negative_hits": neg, "political_hits": pol}


def enrich(symbol: str, name: str = "", force: bool = False) -> dict:
    """Fresh news + sentiment + political flag for one symbol. Cached ~30 min."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cp = _cache_path(symbol)
    if cp.exists() and not force:
        try:
            cached = json.loads(cp.read_text(encoding="utf-8"))
            if datetime.now() - datetime.fromisoformat(cached["fetched_at"]) < timedelta(minutes=CACHE_TTL_MIN):
                cached["cache"] = "hit"
                return cached
        except Exception:
            pass

    query = name.strip() or symbol
    # for a company, "Name stock" narrows to market news
    headlines = _newsapi(f'"{query}" stock OR shares OR earnings', n=8) or _newsapi(query, n=8)
    sentiment = _score_headlines(headlines)
    result = {
        "symbol": symbol,
        "name": name,
        "fetched_at": datetime.now().isoformat(),
        "news": headlines,
        "sentiment": sentiment,
        "political_exposure": "High" if sentiment["political_hits"] >= 2 else
                              "Some" if sentiment["political_hits"] == 1 else "Low",
        "cache": "miss",
        "source": "NewsAPI" if headlines else "none (set NEWS_API_KEY for live news)",
    }
    try:
        cp.write_text(json.dumps(result), encoding="utf-8")
    except Exception:
        pass
    return result
