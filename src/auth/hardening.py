"""
src/auth/hardening.py
=====================
The defences that are not about *who* you are.

Access control answers "may this person touch this route". This file answers a
different set of questions: what a hostile page in the same browser can do,
what an attacker who can send unlimited requests can do, and what the app
accidentally tells people about itself.

None of this replaces the policy layer — it is the belt to its braces.

A NOTE ON HONESTY
-----------------
This makes the app meaningfully harder to attack. It does not make it
unhackable, and nothing does. The things it does NOT solve are written down in
docs/SECURITY.md rather than left implied.
"""
from __future__ import annotations

import logging
import os
import time
from collections import defaultdict, deque

logger = logging.getLogger(__name__)


# ── security headers ─────────────────────────────────────────────────────────

def security_headers(is_https: bool) -> dict:
    """Headers applied to every response.

    The CSP is deliberately strict, with one honest compromise: the frontend
    is built with inline styles throughout (a project convention), so
    'unsafe-inline' is required for style-src. Script-src is NOT relaxed, which
    is the half that actually stops injected code from running.
    """
    csp = "; ".join([
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data: blob: https:",
        "font-src 'self' data:",
        # The API and the frontend are same-origin behind the tunnel; in dev
        # Vite proxies, so 'self' covers both without listing localhost ports.
        "connect-src 'self'",
        "frame-ancestors 'none'",      # clickjacking: nothing may embed us
        "base-uri 'self'",
        "form-action 'self'",
        "object-src 'none'",
    ])
    h = {
        "Content-Security-Policy": csp,
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "strict-origin-when-cross-origin",
        # Deny hardware the app never uses, so a compromised page cannot ask.
        "Permissions-Policy": "geolocation=(), camera=(), microphone=(), "
                              "payment=(), usb=(), interest-cohort=()",
        "Cross-Origin-Opener-Policy": "same-origin",
        "Cross-Origin-Resource-Policy": "same-origin",
    }
    if is_https:
        # Only over HTTPS. Sending HSTS on plain http is ignored by browsers,
        # and setting it during local dev would pin localhost to https and
        # break the next project that uses the port.
        h["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return h


# ── rate limiting ────────────────────────────────────────────────────────────

class RateLimiter:
    """Fixed-window counters, in memory.

    In memory is a real limitation and worth naming: it resets on restart and
    does not span processes. For a single-process app behind one tunnel that is
    exactly the deployment, so it is honest rather than lazy. Put anything
    bigger behind a real gateway.

    The buckets matter more than the numbers. Sign-in is limited far harder
    than reading a chart, because a login endpoint is where credential-stuffing
    and token-guessing land, and because each chat costs the owner a minute of
    a 4B model on a 2014 Mac mini — an unlimited chat endpoint IS the denial of
    service, no packet flood required.
    """

    BUCKETS = {
        "/api/auth/": (20, 300),        # 20 sign-in attempts per 5 min
        "/api/chat": (20, 3600),        # 20 chats/hour — the mini is the bottleneck
        "/api/v5/": (60, 3600),         # analysis is ~7s of CPU each
        "": (300, 60),                  # everything else: 300/min
    }

    def __init__(self):
        self._hits: dict[tuple[str, str], deque] = defaultdict(deque)
        self._last_sweep = time.time()

    def _bucket(self, path: str) -> tuple[str, int, int]:
        for prefix, (limit, window) in self.BUCKETS.items():
            if prefix and path.startswith(prefix):
                return prefix, limit, window
        limit, window = self.BUCKETS[""]
        return "", limit, window

    def _sweep(self, now: float):
        # Without this the dict grows forever behind a NAT or a scanner, which
        # turns a defence into a memory leak.
        if now - self._last_sweep < 120:
            return
        self._last_sweep = now
        for key in list(self._hits):
            if not self._hits[key] or now - self._hits[key][-1] > 3600:
                del self._hits[key]

    def check(self, client: str, path: str) -> tuple[bool, int]:
        """(allowed, retry_after_seconds)."""
        now = time.time()
        self._sweep(now)
        prefix, limit, window = self._bucket(path)
        q = self._hits[(client, prefix)]
        while q and now - q[0] > window:
            q.popleft()
        if len(q) >= limit:
            return False, max(1, int(window - (now - q[0])))
        q.append(now)
        return True, 0


_limiter = RateLimiter()


def rate_limit(client: str, path: str) -> tuple[bool, int]:
    return _limiter.check(client, path)


# ── client identity behind a proxy ───────────────────────────────────────────

def client_ip(request) -> str:
    """The caller's address, correct both directly and behind Cloudflare.

    X-Forwarded-For is attacker-controlled unless something trusted overwrites
    it, so it is only honoured when ARIA_TRUST_PROXY is set — which you do only
    when the app genuinely sits behind a proxy that rewrites the header. Trust
    it unconditionally and every rate limit becomes bypassable by sending a
    fresh fake IP with each request.
    """
    if os.environ.get("ARIA_TRUST_PROXY", "").strip().lower() in ("1", "true", "yes"):
        cf = request.headers.get("cf-connecting-ip")
        if cf:
            return cf.strip()
        xff = request.headers.get("x-forwarded-for")
        if xff:
            return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def peer_ip(request) -> str:
    """The address of the socket that actually connected — never a header.

    client_ip() above answers "who is the human", which behind a proxy means
    reading a header the human controls. This answers "who opened the
    connection", which nobody can forge, and is the only address an
    authorisation decision may be based on.
    """
    return request.client.host if request.client else "unknown"


def is_https(request) -> bool:
    if request.url.scheme == "https":
        return True
    if os.environ.get("ARIA_TRUST_PROXY", "").strip().lower() in ("1", "true", "yes"):
        return (request.headers.get("x-forwarded-proto") or "").lower() == "https"
    return False
