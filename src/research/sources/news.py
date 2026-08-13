"""
src/research/sources/news.py
============================
Google News and Hacker News — the two widest free nets available.

Google News RSS is the closest legitimate thing to "search Google for it":
`news.google.com/rss/search?q=` is an official feed, needs no key, and
indexes essentially every publication that matters, including small
regional outlets. It returns headlines and links, not article bodies; feed
the links to `read()` when you want the text.

Hacker News is searched through Algolia's public API, which is free,
documented and rate-limit generous. Worth having because tech and crypto
stories surface there hours before the wires.
"""
from __future__ import annotations

from datetime import datetime, timezone
from xml.etree import ElementTree

from ..base import Document, Source, SourceError
from ..http import get, get_json, q
from ..url import host_matches, normalize_public_url

GOOGLE_NEWS = "https://news.google.com/rss"
HN_API = "https://hn.algolia.com/api/v1"


class GoogleNewsSource(Source):
    name = "googlenews"
    description = "Google News search and topic feeds (official RSS, no key)"
    backends = ["gnews-rss"]
    zero_config = True

    def can_handle(self, url: str) -> bool:
        return host_matches(url, "news.google.com")

    def fetch(self, url: str) -> Document:
        docs = self._parse(get(normalize_public_url(url)))
        return Document(
            url=normalize_public_url(url),
            title=f"google news: {url}",
            text="\n".join(f"- {d.title} — {d.url}" for d in docs),
            source=self.name,
            backend="gnews-rss",
            meta={"item_count": len(docs)},
        )

    def _parse(self, body: bytes) -> list[Document]:
        try:
            root = ElementTree.fromstring(body)
        except ElementTree.ParseError as exc:
            raise SourceError(f"google news returned invalid XML: {exc}") from exc

        out = []
        for item in root.findall(".//item"):
            link = (item.findtext("link") or "").strip()
            try:
                link = normalize_public_url(link)
            except ValueError:
                continue
            out.append(Document(
                url=link,
                title=(item.findtext("title") or link).strip(),
                text=(item.findtext("description") or "").strip(),
                source=self.name,
                backend="gnews-rss",
                meta={
                    "published": (item.findtext("pubDate") or "").strip(),
                    "publisher": (item.findtext("source") or "").strip(),
                },
            ))
        return out

    def search(self, query: str, limit: int = 50, lang: str = "en-US",
               country: str = "US", when: str | None = "7d") -> list[Document]:
        """Search all of Google News. `when` accepts 1h, 1d, 7d, 1y."""
        term = f"{query} when:{when}" if when else query
        params = q(**{"q": term, "hl": lang, "gl": country,
                      "ceid": f"{country}:{lang.split('-')[0]}"})
        return self._parse(get(f"{GOOGLE_NEWS}/search?{params}"))[:limit]

    def search_ticker(self, ticker: str, limit: int = 15) -> list[Document]:
        """Qualify the symbol so the index knows it is a stock, not a word."""
        return self.search(f'"{ticker}" stock', limit=limit)


class HackerNewsSource(Source):
    name = "hackernews"
    description = "Hacker News stories and comments via Algolia public API"
    backends = ["algolia"]
    zero_config = True

    def can_handle(self, url: str) -> bool:
        return host_matches(url, "news.ycombinator.com")

    def fetch(self, url: str) -> Document:
        clean = normalize_public_url(url)
        item_id = ""
        if "id=" in clean:
            item_id = clean.split("id=")[1].split("&")[0]
        if not item_id.isdigit():
            raise SourceError(f"no HN item id in {clean}")

        data = get_json(f"{HN_API}/items/{item_id}")
        parts = [f"# {data.get('title') or ''}", data.get("text") or ""]

        def walk(node, depth=1):
            for child in node.get("children") or []:
                text = (child.get("text") or "").strip()
                if text:
                    parts.append(f"{'  ' * depth}- **{child.get('author', '?')}**: {text}")
                walk(child, depth + 1)
        walk(data)

        return Document(
            url=clean,
            title=data.get("title") or f"HN item {item_id}",
            text="\n\n".join(p for p in parts if p),
            source=self.name,
            backend="algolia",
            meta={"item_id": item_id, "points": data.get("points"),
                  "author": data.get("author", "")},
        )

    def search_ticker(self, ticker: str, limit: int = 15) -> list[Document]:
        """Nothing. HN has no ticker context, and pretending otherwise is worse
        than silence: searching it for NET returns Netflix and Netscape, and
        ERX returns a Fallout mod. It stays valuable for topic sweeps."""
        return []

    def search(self, query: str, limit: int = 25, days: int = 7) -> list[Document]:
        cutoff = int(datetime.now(timezone.utc).timestamp()) - days * 86400
        params = q(query=query, tags="story",
                   numericFilters=f"created_at_i>{cutoff}", hitsPerPage=limit)
        payload = get_json(f"{HN_API}/search?{params}")
        out = []
        for hit in payload.get("hits", []):
            hn_url = f"https://news.ycombinator.com/item?id={hit.get('objectID')}"
            out.append(Document(
                url=hit.get("url") or hn_url,
                title=hit.get("title") or "",
                text=hit.get("story_text") or "",
                source=self.name,
                backend="algolia",
                meta={"points": hit.get("points"), "author": hit.get("author", ""),
                      "num_comments": hit.get("num_comments"),
                      "created_at": hit.get("created_at", ""), "discussion": hn_url},
            ))
        return out
