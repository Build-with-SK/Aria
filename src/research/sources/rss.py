"""
src/research/sources/rss.py
===========================
RSS and Atom feeds, parsed with the standard library.

feedparser is the usual choice and is not installed; adding it would buy
tolerance for malformed feeds we do not need, since the feeds worth watching
(exchange filings, central banks, company IR, wire services) emit valid XML.
xml.etree handles both RSS 2.0 and Atom with one pass.

Entry links are normalized through the SSRF guard exactly like any other
untrusted URL — a feed is a list of links written by someone else.
"""
from __future__ import annotations

import urllib.error
import urllib.request
from xml.etree import ElementTree

from ..base import Document, Source, SourceError
from ..url import normalize_public_url

UA = "ARIA-Research/1.0 (+feed reader)"
TIMEOUT = 20
MAX_BYTES = 8 * 1024 * 1024

ATOM = "{http://www.w3.org/2005/Atom}"


def _text(node, *paths: str) -> str:
    """First non-empty child text among *paths*."""
    for path in paths:
        found = node.find(path)
        if found is not None:
            value = "".join(found.itertext()).strip()
            if value:
                return value
    return ""


def _entry_link(node) -> str:
    """RSS puts the link in <link> text; Atom puts it in a href attribute."""
    link = _text(node, "link")
    if link:
        return link
    for candidate in node.findall(f"{ATOM}link"):
        rel = candidate.get("rel", "alternate")
        href = candidate.get("href", "").strip()
        if href and rel == "alternate":
            return href
    return ""


class RSSSource(Source):
    name = "rss"
    description = "RSS 2.0 and Atom feeds"
    backends = ["stdlib-xml"]
    zero_config = True

    def can_handle(self, url: str) -> bool:
        lowered = url.lower()
        return any(
            token in lowered
            for token in ("/rss", "/feed", ".rss", ".atom", "feed=rss", "format=atom")
        )

    def fetch(self, url: str) -> Document:
        """Return the feed itself as one Document; use entries() for the items."""
        items = self.entries(url)
        clean = normalize_public_url(url)
        lines = [f"- {e.title} — {e.url}" for e in items]
        return Document(
            url=clean,
            title=f"feed: {clean}",
            text="\n".join(lines),
            source=self.name,
            backend="stdlib-xml",
            meta={"entry_count": len(items)},
        )

    def entries(self, url: str, limit: int = 50) -> list[Document]:
        """Parse a feed into one Document per item, newest first as published."""
        clean = normalize_public_url(url)
        req = urllib.request.Request(clean, headers={"User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                body = resp.read(MAX_BYTES + 1)
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            raise SourceError(f"could not fetch feed {clean}: {exc}") from exc

        if len(body) > MAX_BYTES:
            raise SourceError(f"feed {clean} exceeds {MAX_BYTES} bytes")

        try:
            root = ElementTree.fromstring(body)
        except ElementTree.ParseError as exc:
            raise SourceError(f"feed {clean} is not valid XML: {exc}") from exc

        nodes = root.findall(".//item") or root.findall(f".//{ATOM}entry")
        out: list[Document] = []
        for node in nodes[:limit]:
            link = _entry_link(node)
            try:
                link = normalize_public_url(link) if link else clean
            except ValueError:
                # An entry pointing somewhere non-public is dropped, not fatal:
                # one poisoned item must not kill the whole feed.
                continue

            summary = _text(
                node, "description", f"{ATOM}summary", f"{ATOM}content", "content"
            )
            published = _text(
                node, "pubDate", f"{ATOM}published", f"{ATOM}updated", "date"
            )
            out.append(
                Document(
                    url=link,
                    title=_text(node, "title", f"{ATOM}title") or link,
                    text=summary,
                    source=self.name,
                    backend="stdlib-xml",
                    meta={"published": published, "feed": clean},
                )
            )
        return out
