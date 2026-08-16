"""
src/research/sources/walled.py
==============================
X/Twitter, Instagram and Facebook — through their official APIs, when you
hold a credential, and not otherwise.

Read this before wiring anything here:

X removed free read access in 2023. Instagram and Facebook have never
allowed unauthenticated reading of arbitrary public content. There is no
clever endpoint left; every "free Twitter scraper" works by driving a real
logged-in account against the terms of service, and it fails in three ways
that matter to a trading system — the account gets suspended, the scraper
breaks without warning on a markup change, and the data cannot be
reproduced later when a report is questioned. Meta additionally litigates
scrapers, and has won.

So these adapters use the documented APIs and activate only when the
matching credential is present in data/research_keys.json or the
environment. With no credential they report exactly what is missing and
cost nothing. That is the honest ceiling on these three platforms:

  X_BEARER_TOKEN     X API v2. The $200/mo Basic tier reads; the free tier
                     is write-only, so a free token authenticates and then
                     returns 403 on every search.
  META_ACCESS_TOKEN  Graph API. Reads Instagram Business/Creator accounts
                     and Facebook Pages you have been granted — never
                     arbitrary users' feeds. No tier of this API exposes
                     the whole network.

Public conversation about a ticker is better served by the reddit,
stocktwits, googlenews and hackernews sources, which are free, open and
allowed. Those are already wired in.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from ..base import Document, Source, SourceError
from ..http import get_json, q

ROOT = Path(__file__).parent.parent.parent.parent
KEYS_FILE = ROOT / "data" / "research_keys.json"


def credential(name: str) -> str:
    """Look up a key: environment first, then data/research_keys.json.

    The file is gitignored with the rest of data/research*; never inline a
    token in source.
    """
    value = os.environ.get(name, "")
    if value:
        return value.strip()
    if KEYS_FILE.exists():
        try:
            data = json.loads(KEYS_FILE.read_text(encoding="utf-8"))
            return str(data.get(name, "")).strip()
        except (ValueError, OSError):
            return ""
    return ""


class XSource(Source):
    name = "x"
    description = "X/Twitter via official API v2 (requires paid Basic tier token)"
    backends = ["x-api-v2"]
    zero_config = False

    def can_handle(self, url: str) -> bool:
        from ..url import host_matches
        return host_matches(url, "twitter.com", "x.com")

    def available(self) -> tuple[bool, str]:
        if not credential("X_BEARER_TOKEN"):
            return False, (
                "no X_BEARER_TOKEN — X has no free read tier since 2023; "
                "use reddit/stocktwits/googlenews for public chatter instead"
            )
        return True, "x-api-v2"

    def _headers(self) -> dict:
        token = credential("X_BEARER_TOKEN")
        if not token:
            raise SourceError(
                "X read access needs X_BEARER_TOKEN (paid Basic tier). "
                "Unauthenticated scraping is not implemented: it violates the "
                "terms, risks the account, and cannot be reproduced for audit."
            )
        return {"Authorization": f"Bearer {token}"}

    def fetch(self, url: str) -> Document:
        tweet_id = url.rstrip("/").split("/")[-1].split("?")[0]
        if not tweet_id.isdigit():
            raise SourceError(f"no tweet id in {url}")
        params = q(**{"ids": tweet_id,
                      "tweet.fields": "created_at,public_metrics,author_id,lang",
                      "expansions": "author_id"})
        payload = get_json(f"https://api.twitter.com/2/tweets?{params}",
                           headers=self._headers())
        items = payload.get("data") or []
        if not items:
            raise SourceError(f"X returned no data for {url}")
        tweet = items[0]
        users = {u["id"]: u for u in (payload.get("includes", {}).get("users") or [])}
        author = users.get(tweet.get("author_id", ""), {})
        return Document(
            url=url,
            title=f"@{author.get('username', '?')}: {tweet.get('text', '')[:60]}",
            text=tweet.get("text", ""),
            source=self.name,
            backend="x-api-v2",
            meta={"author": author.get("username", ""),
                  "created_at": tweet.get("created_at", ""),
                  **(tweet.get("public_metrics") or {})},
        )

    def search(self, query: str, limit: int = 25) -> list[Document]:
        params = q(**{"query": query, "max_results": max(10, min(limit, 100)),
                      "tweet.fields": "created_at,public_metrics,author_id,lang"})
        payload = get_json(f"https://api.twitter.com/2/tweets/search/recent?{params}",
                           headers=self._headers())
        return [
            Document(
                url=f"https://x.com/i/status/{t.get('id')}",
                title=(t.get("text") or "")[:80],
                text=t.get("text", ""),
                source=self.name,
                backend="x-api-v2",
                meta={"created_at": t.get("created_at", ""),
                      **(t.get("public_metrics") or {})},
            )
            for t in (payload.get("data") or [])
        ]


class MetaSource(Source):
    name = "meta"
    description = "Instagram Business / Facebook Pages via Graph API (requires token)"
    backends = ["graph-api"]
    zero_config = False

    def can_handle(self, url: str) -> bool:
        from ..url import host_matches
        return host_matches(url, "instagram.com", "facebook.com", "fb.com")

    def available(self) -> tuple[bool, str]:
        if not credential("META_ACCESS_TOKEN"):
            return False, (
                "no META_ACCESS_TOKEN — Graph API reads only IG Business "
                "accounts and FB Pages you are granted; arbitrary public "
                "feeds are not readable by any tier"
            )
        return True, "graph-api"

    def fetch(self, url: str) -> Document:
        raise SourceError(
            "Instagram and Facebook expose no per-URL read endpoint. The Graph "
            "API addresses objects you own or administer — see MetaSource.page() "
            "— and no tier permits reading arbitrary public posts."
        )

    def page(self, page_id: str, limit: int = 25) -> list[Document]:
        """Recent posts from a Facebook Page or IG Business account you manage."""
        token = credential("META_ACCESS_TOKEN")
        if not token:
            raise SourceError("Meta read access needs META_ACCESS_TOKEN")
        params = q(**{"fields": "message,created_time,permalink_url,shares",
                      "limit": limit, "access_token": token})
        payload = get_json(f"https://graph.facebook.com/v21.0/{page_id}/posts?{params}")
        return [
            Document(
                url=post.get("permalink_url", f"https://facebook.com/{post.get('id', '')}"),
                title=(post.get("message") or "")[:80],
                text=post.get("message", ""),
                source=self.name,
                backend="graph-api",
                meta={"created_time": post.get("created_time", ""), "id": post.get("id", "")},
            )
            for post in (payload.get("data") or [])
        ]
