"""
src/research/sources/web.py
===========================
Any public page, as markdown, via Jina Reader (https://r.jina.ai/URL).

Jina is the fallback that handles every URL no other source claims. It is a
legitimate public reader service — no login, no scraping of a logged-in
session, nothing that risks an account.

Two failure modes matter and are handled here rather than by the caller:
a response that is really an anti-bot challenge page (Jina returns HTTP 200
with the challenge as content), and a response large enough to blow up the
process. Both raise SourceError instead of returning junk that would end up
in the store looking like a citation.
"""
from __future__ import annotations

import urllib.error
import urllib.request

from ..base import Document, Source, SourceError
from ..url import normalize_public_url

READER = "https://r.jina.ai/"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 ARIA-Research/1.0"
MAX_BYTES = 5 * 1024 * 1024
TIMEOUT = 30
_SCAN_BYTES = 4096


def _is_challenge_page(body: bytes) -> bool:
    """Recognize a Cloudflare/Jina challenge served with a 200 status."""
    sample = body[:_SCAN_BYTES].decode("utf-8", errors="ignore").casefold()

    jina_captcha = "warning:" in sample and "requiring captcha" in sample
    # These are title/heading lines of interstitials. Upstream only trusts them
    # alongside a Jina captcha warning, which lets a bare "Just a moment..."
    # challenge through as if it were the article. No real page opens this way.
    challenge = any(
        marker in sample
        for marker in (
            "title: just a moment...",
            "## performing security verification",
            "title: attention required! | cloudflare",
            "title: access denied",
        )
    )
    cloudflare_block = "ray id" in sample or "/cdn-cgi/challenge-platform/" in sample
    return jina_captcha or challenge or cloudflare_block


def _title_from_markdown(text: str, fallback: str) -> str:
    """Jina prefixes its output with `Title: ...`; fall back to the first heading."""
    for line in text.splitlines()[:10]:
        line = line.strip()
        if line.lower().startswith("title:"):
            return line[6:].strip() or fallback
        if line.startswith("# "):
            return line[2:].strip() or fallback
    return fallback


class WebSource(Source):
    name = "web"
    description = "Any public web page, rendered to markdown"
    backends = ["jina"]
    zero_config = True

    def can_handle(self, url: str) -> bool:
        return True          # catch-all; the registry tries it last

    def fetch(self, url: str) -> Document:
        clean = normalize_public_url(url)
        req = urllib.request.Request(
            READER + clean,
            headers={"User-Agent": UA, "Accept": "text/plain"},
        )
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                body = resp.read(MAX_BYTES + 1)
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            raise SourceError(f"jina could not read {clean}: {exc}") from exc

        if len(body) > MAX_BYTES:
            raise SourceError(f"jina response for {clean} exceeds {MAX_BYTES} bytes")
        if _is_challenge_page(body):
            raise SourceError(
                f"jina returned an anti-bot challenge for {clean}, not the page"
            )

        text = body.decode("utf-8", errors="replace")
        return Document(
            url=clean,
            title=_title_from_markdown(text, clean),
            text=text,
            source=self.name,
            backend="jina",
        )
