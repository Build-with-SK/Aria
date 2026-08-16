"""
src/research/reach.py
=====================
The one entry point: `read(url)`.

Callers never name a source or a backend. The registry picks the first
source that claims the URL, falls back to the generic web reader when a
specialist fails, and archives whatever comes back before returning it.

Every source here reaches its platform the way the platform publishes it —
public JSON, official RSS, a documented API, a first-party CLI. Nothing
drives a logged-in account against a site's terms. X and Meta are wired to
their official APIs and stay dark until a credential exists; see
sources/walled.py for why that is the ceiling rather than a preference.
"""
from __future__ import annotations

import logging

from .base import Document, Source, SourceError
from .sources.github import GitHubSource
from .sources.markets import EdgarSource, StockTwitsSource
from .sources.news import GoogleNewsSource, HackerNewsSource
from .sources.reddit import RedditSource
from .sources.rss import RSSSource
from .sources.walled import MetaSource, XSource
from .sources.web import WebSource
from .sources.youtube import YouTubeSource
from .store import archive
from .url import normalize_public_url

logger = logging.getLogger(__name__)

# Order matters: specialists first, generic web reader last as catch-all.
SOURCES: list[Source] = [
    YouTubeSource(),
    GitHubSource(),
    RedditSource(),
    StockTwitsSource(),
    EdgarSource(),
    GoogleNewsSource(),
    HackerNewsSource(),
    XSource(),
    MetaSource(),
    RSSSource(),
    WebSource(),
]


def route(url: str) -> Source:
    """The source that claims this URL. WebSource always claims as a last resort."""
    for source in SOURCES:
        if source.can_handle(url):
            return source
    return SOURCES[-1]


def read(url: str, store: bool = True) -> Document:
    """Fetch *url* through the right source, archive it, return the Document.

    A specialist failure falls back to the generic reader once — a broken
    yt-dlp should degrade to reading the page, not lose the item. If both
    fail, SourceError propagates: silently returning empty text would put a
    hole in a report that looks like data.
    """
    clean = normalize_public_url(url)
    source = route(clean)

    try:
        doc = source.fetch(clean)
    except SourceError as exc:
        if isinstance(source, WebSource):
            raise
        logger.warning("%s failed on %s (%s) — falling back to web", source.name, clean, exc)
        doc = WebSource().fetch(clean)

    if store:
        doc.meta["content_id"] = archive(doc)
    return doc


def read_feed(url: str, limit: int = 50, store: bool = True) -> list[Document]:
    """Expand an RSS/Atom feed into its entries without fetching each page."""
    docs = RSSSource().entries(url, limit=limit)
    if store:
        for doc in docs:
            doc.meta["content_id"] = archive(doc)
    return docs


def status() -> list[dict]:
    """Per-source health, for /api/research/status and the doctor script."""
    out = []
    for source in SOURCES:
        ok, detail = source.available()
        out.append({
            "name": source.name,
            "description": source.description,
            "backends": source.backends,
            "available": ok,
            "detail": detail,
            "zero_config": source.zero_config,
        })
    return out
