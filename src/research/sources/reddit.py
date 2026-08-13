"""
src/research/sources/reddit.py
==============================
Reddit, by whichever official door is open.

The old trick — appending `.json` to any URL — is dead: as of 2026 Reddit
answers 403 Blocked to unauthenticated JSON regardless of User-Agent. Two
legitimate doors remain, and this source tries them in order:

  oauth       The free registered-app tier. Set REDDIT_CLIENT_ID and
              REDDIT_CLIENT_SECRET (create an app at reddit.com/prefs/apps,
              type "script") and you get ~100 requests/minute, full search,
              scores, and comment trees. This is the door Reddit wants you
              to use and the only one that returns vote counts.
  public-rss  No credential at all. Reddit still serves Atom at
              `/.rss` and `/search.rss`, which is enough for titles, links
              and timestamps — but carries no scores. Rate limits are
              tight; back off and do not crawl.

No account is driven, no HTML is scraped, nothing here risks a ban.
"""
from __future__ import annotations

import base64
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from xml.etree import ElementTree

from ..base import Document, Source, SourceError
from ..http import UA, get, get_json, q
from ..url import host_matches, normalize_public_url
from .walled import credential

logger = logging.getLogger(__name__)

API = "https://www.reddit.com"
OAUTH = "https://oauth.reddit.com"
ATOM = "{http://www.w3.org/2005/Atom}"

# Where a bare ticker means a ticker. Searched as one multireddit so a symbol
# lookup costs one request rather than six.
FINANCE_SUBS = (
    "stocks+investing+wallstreetbets+StockMarket+options+SecurityAnalysis"
    "+ValueInvesting+Daytrading+thetagang"
)

_token_cache: dict[str, tuple[str, float]] = {}


def _oauth_token() -> str | None:
    """App-only bearer token, cached until shortly before it expires."""
    cid = credential("REDDIT_CLIENT_ID")
    secret = credential("REDDIT_CLIENT_SECRET")
    if not (cid and secret):
        return None

    cached = _token_cache.get("token")
    if cached and cached[1] > time.time() + 60:
        return cached[0]

    basic = base64.b64encode(f"{cid}:{secret}".encode()).decode()
    req = urllib.request.Request(
        "https://www.reddit.com/api/v1/access_token",
        data=urllib.parse.urlencode({"grant_type": "client_credentials"}).encode(),
        headers={"Authorization": f"Basic {basic}", "User-Agent": UA},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            payload = json.loads(resp.read(64_000))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        logger.warning("reddit oauth failed, falling back to rss: %s", exc)
        return None

    token = payload.get("access_token", "")
    if not token:
        return None
    _token_cache["token"] = (token, time.time() + float(payload.get("expires_in", 3600)))
    return token


def _post_doc(child: dict) -> Document:
    data = child.get("data", {}) or {}
    permalink = data.get("permalink", "")
    url = f"{API}{permalink}" if permalink else data.get("url", API)
    return Document(
        url=url,
        title=data.get("title", "") or url,
        text=data.get("selftext") or data.get("url") or "",
        source="reddit",
        backend="oauth",
        meta={
            "subreddit": data.get("subreddit", ""),
            "author": data.get("author", ""),
            "score": data.get("score"),
            "num_comments": data.get("num_comments"),
            "created_utc": data.get("created_utc"),
            "upvote_ratio": data.get("upvote_ratio"),
            "flair": data.get("link_flair_text") or "",
        },
    )


def _atom_docs(body: bytes) -> list[Document]:
    """Parse Reddit's Atom output. No scores exist in this format."""
    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError as exc:
        raise SourceError(f"reddit returned invalid Atom: {exc}") from exc

    out = []
    for entry in root.findall(f".//{ATOM}entry"):
        link = ""
        for node in entry.findall(f"{ATOM}link"):
            if node.get("href"):
                link = node.get("href", "")
                break
        try:
            link = normalize_public_url(link)
        except ValueError:
            continue
        author = entry.find(f"{ATOM}author/{ATOM}name")
        content = entry.find(f"{ATOM}content")
        out.append(Document(
            url=link,
            title=(entry.findtext(f"{ATOM}title") or link).strip(),
            text="".join(content.itertext()).strip() if content is not None else "",
            source="reddit",
            backend="public-rss",
            meta={
                # removeprefix, not lstrip: lstrip takes a character set, so
                # "/u/u1" would come back as "1".
                "author": (author.text or "").removeprefix("/u/") if author is not None else "",
                "updated": entry.findtext(f"{ATOM}updated") or "",
                # Absent, not zero: RSS carries no score, and a real 0 means
                # something different from "this backend cannot tell you".
                "score": None,
            },
        ))
    return out


class RedditSource(Source):
    name = "reddit"
    description = "Reddit posts, subreddits and search (OAuth app tier, else public Atom)"
    backends = ["oauth", "public-rss"]
    zero_config = True          # the RSS fallback needs nothing

    def can_handle(self, url: str) -> bool:
        return host_matches(url, "reddit.com", "old.reddit.com", "redd.it")

    def available(self) -> tuple[bool, str]:
        if _oauth_token():
            return True, "oauth (full search, scores, comments)"
        return True, (
            "public-rss — no scores or comment trees; set REDDIT_CLIENT_ID and "
            "REDDIT_CLIENT_SECRET (reddit.com/prefs/apps, type 'script') for the "
            "free API tier"
        )

    def _oauth_get(self, path: str, token: str):
        return get_json(f"{OAUTH}{path}", headers={"Authorization": f"Bearer {token}"})

    def fetch(self, url: str) -> Document:
        clean = normalize_public_url(url).split("?")[0].rstrip("/")
        token = _oauth_token()

        if token:
            path = urllib.parse.urlsplit(clean).path.rstrip("/")
            payload = self._oauth_get(f"{path}.json?raw_json=1", token)

            if isinstance(payload, list) and payload:
                children = (payload[0].get("data", {}) or {}).get("children", [])
                if not children:
                    raise SourceError(f"no post found at {clean}")
                doc = _post_doc(children[0])
                comments = []
                if len(payload) > 1:
                    for child in (payload[1].get("data", {}) or {}).get("children", [])[:50]:
                        data = child.get("data", {}) or {}
                        text = (data.get("body") or "").strip()
                        if text and text not in ("[deleted]", "[removed]"):
                            comments.append(
                                f"**u/{data.get('author', '?')}** ({data.get('score')}): {text}"
                            )
                doc.text = f"# {doc.title}\n\n{doc.text}\n\n---\n\n" + "\n\n".join(comments)
                doc.meta["comments_read"] = len(comments)
                return doc

            children = (payload.get("data", {}) or {}).get("children", [])
            docs = [_post_doc(c) for c in children]
        else:
            docs = _atom_docs(get(f"{clean}/.rss"))

        return Document(
            url=clean,
            title=f"reddit: {clean}",
            text="\n".join(f"- {d.title} — {d.url}" for d in docs),
            source=self.name,
            backend="oauth" if token else "public-rss",
            meta={"post_count": len(docs)},
        )

    def search(self, query: str, subreddit: str | None = None, limit: int = 25,
               sort: str = "relevance", time_filter: str = "week") -> list[Document]:
        """Search Reddit. `subreddit=None` searches all of it."""
        token = _oauth_token()
        if token:
            path = f"/r/{subreddit}/search" if subreddit else "/search"
            params = q(q=query, limit=limit, sort=sort, t=time_filter, raw_json=1,
                       restrict_sr="on" if subreddit else None)
            payload = self._oauth_get(f"{path}?{params}", token)
            children = (payload.get("data", {}) or {}).get("children", [])
            return [_post_doc(c) for c in children]

        base = f"{API}/r/{subreddit}/search.rss" if subreddit else f"{API}/search.rss"
        params = q(q=query, sort=sort, t=time_filter,
                   restrict_sr="on" if subreddit else None)
        return _atom_docs(get(f"{base}?{params}"))[:limit]

    def search_ticker(self, ticker: str, limit: int = 15) -> list[Document]:
        """Search only where people discuss tickers.

        Site-wide, a symbol collides with everything: ERX returned a Fallout
        mod, and NET returns the English word. Restricting to a finance
        multireddit is what makes the result about the position.
        """
        return self.search(ticker, subreddit=FINANCE_SUBS, limit=limit,
                           sort="new")
