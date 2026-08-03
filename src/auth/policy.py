"""
src/auth/policy.py
==================
Who is asking, and what are they allowed to touch.

THE SHAPE, AND WHY
------------------
This is an ALLOWLIST, not a blocklist. A role starts with nothing and is granted
named path prefixes. Anything not listed is owner-only.

That direction matters more than the list itself. The subtractive version —
"everyone gets access, minus a few sensitive routes" — is how the API ended up
with 112 unauthenticated endpoints including the trade-approval gate: every
route added later was public until somebody remembered to restrict it. Here, a
new endpoint is invisible to free users until it is deliberately named. Forget
about it and the failure is a 403, not a disclosure.

THE THREE THINGS THAT ARE NEVER A TIER
--------------------------------------
The vault, the portfolio and execution are not "premium features". They are the
owner's private data and the owner's money. They are unreachable for every role
except owner, and no plan, flag or upgrade changes that. A misconfigured quota
is an annoyance; a misconfigured vault flag publishes someone's personal notes.

OWNER IDENTIFICATION
--------------------
Set ARIA_OWNER_TOKEN in .env and send it as `Authorization: Bearer <token>` or
`X-ARIA-Token`.

If no token is configured, requests from LOOPBACK are treated as the owner and
everything else is free. That keeps the existing single-user localhost workflow
working untouched while making any remote caller a free user by default. It is
a deliberate compromise for a machine that is not yet exposed — the moment this
is reachable from anywhere else, set the token.
"""
from __future__ import annotations

import hmac
import logging
import os

logger = logging.getLogger(__name__)

OWNER = "owner"
FREE = "free"
ANON = "anon"          # not signed in — gets nothing but the login routes

LOOPBACK = {"127.0.0.1", "::1", "localhost", "testclient"}

# Reachable without a session. Deliberately tiny: the routes needed to sign in,
# and a health check for the deploy script. Everything else requires an account.
PUBLIC_PREFIXES: tuple[str, ...] = (
    "/health",
    "/api/auth/",          # providers, login, callback, me, logout
)


# ── what a free user may reach ───────────────────────────────────────────────
# Research and market data: the actual product. Everything here is public
# knowledge about public markets — none of it is about the owner.
FREE_PREFIXES: tuple[str, ...] = (
    "/health",
    "/api/v5/",            # the research engine
    "/api/universe/",      # symbol lookup, quotes, news, fundamentals
    "/api/quote/",
    "/api/technical/",
    "/api/signals",
    "/api/macro",
    "/api/futures",
    "/api/options",
    "/api/derivatives",
    "/api/sentiment",
    "/api/history",
    "/api/lse",
    "/api/nexus",
    "/api/quant/",         # greeks, payoff curves — maths, not positions
    "/api/backtest",
    "/api/ml",
    "/api/summary",
    "/api/chat/local",     # Ollama only. /api/chat (cloud) is owner-only.
)

# Named so the reason survives someone reading the list in a hurry.
NEVER_SHARED: dict[str, str] = {
    "/api/vault": "the owner's Obsidian notes",
    "/api/execute": "places and approves real orders",
    "/api/portfolio": "the owner's positions",
    "/api/desk": "the autonomous desk and its debate history",
    "/api/brain": "the owner's long-term memory and daemon controls",
    "/api/fx/gbpinr": "the owner's personal remittance monitor",
    "/api/inference": "model and provider management",
    "/api/chat": "the cloud model, billed to the owner's key",
    "/api/run": "triggers the pipeline",
    "/api/report": "the owner's daily report",
    "/api/performance": "the owner's realised track record",
    "/api/stats": "the owner's realised track record",
}


def owner_token() -> str:
    return (os.environ.get("ARIA_OWNER_TOKEN") or "").strip()


def _presented(headers) -> str:
    auth = headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return (headers.get("x-aria-token") or "").strip()


def resolve_role(headers, client_host: str | None, session: dict | None = None) -> str:
    """OWNER, FREE or ANON. Never raises — anything unreadable is simply not owner.

    Order matters. The owner token wins outright because it is the break-glass
    path that must keep working when OAuth or the network is broken. Then a
    signed session, whose `owner` flag was re-derived from ARIA_OWNER_EMAIL on
    read and cannot be forged. Only then the loopback fallback, and only while
    no token is configured, so the existing local workflow survives.
    """
    token = owner_token()
    if token:
        presented = _presented(headers)
        # compare_digest: token checks must not leak length/prefix via timing.
        if presented and hmac.compare_digest(presented, token):
            return OWNER

    if session:
        return OWNER if session.get("owner") else FREE

    if not token and (client_host or "") in LOOPBACK:
        return OWNER

    return ANON


def is_public(path: str) -> bool:
    p = (path or "").rstrip("/") or "/"
    return any(p == q.rstrip("/") or p.startswith(q) for q in PUBLIC_PREFIXES)


def is_allowed(path: str, role: str) -> bool:
    """Default deny. Owner may do anything; signed-in users get the research
    allowlist; anonymous callers get only the sign-in routes."""
    if is_public(path):
        return True
    if role == OWNER:
        return True
    if role != FREE:
        return False
    p = (path or "").rstrip("/") or "/"
    # Longest-match first so /api/chat/local is not shadowed by /api/chat.
    for prefix in sorted(FREE_PREFIXES, key=len, reverse=True):
        if p == prefix.rstrip("/") or p.startswith(prefix):
            return True
    return False


def denial_reason(path: str) -> str:
    p = (path or "")
    for prefix, why in NEVER_SHARED.items():
        if p.startswith(prefix):
            return why
    return "owner-only"


def can_read_vault(role: str) -> bool:
    """The single question the chat path asks before grounding an answer in the
    owner's notes. Structural, not configurable — there is no flag that turns
    this on for anyone else."""
    return role == OWNER


def warn_if_unprotected():
    if not owner_token():
        logger.warning(
            "ARIA_OWNER_TOKEN is not set — owner access is granted by loopback "
            "only. Set it before this API is reachable from anywhere else.")
