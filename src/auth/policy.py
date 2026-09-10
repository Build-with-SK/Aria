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

LOOPBACK = {"127.0.0.1", "::1", "localhost"}
# "testclient" is deliberately NOT here. It was, and it meant every request made
# through a FastAPI TestClient authenticated as the owner — so a test could not
# prove that an execution endpoint refuses an anonymous caller, because the test
# itself was the owner. Tests now present real credentials or a real remote peer.

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
    # Vital signs ONLY — no reasoning text, no memories, no controls. Watching
    # ARIA think is the best thing this system does and should not need a
    # login; but the brain reasons with the owner's vault in context, so
    # /api/brain/memories, /recall and /last-cycle stay owner-only and every
    # POST under /api/brain stays owner-only with them.
    "/api/brain/pulse",
    # Coarse system health: worker states and a 24h event count. No holdings,
    # no tickers, no track record — it answers "is ARIA actually working", which
    # is the honest version of the status dot in the rail. Deliberately NOT in
    # PUBLIC_PREFIXES: the anonymous surface stays exactly as small as it was.
    "/api/system/health",
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


# Everything that can move money or reach a broker. Named once, here, so the
# route guard, the middleware and the tests all read the SAME list — a new
# execution route cannot be protected in one of the three and missed in the
# other two.
EXECUTION_PREFIXES: tuple[str, ...] = (
    "/api/execute",   # the approval queue and the broker itself
    "/api/desk",      # the autonomous desk, which can queue and fire trades
)


def is_execution_path(path: str) -> bool:
    p = (path or "").rstrip("/") or "/"
    return any(p == q.rstrip("/") or p.startswith(q) for q in EXECUTION_PREFIXES)


# Mutating routes that are NOT under an execution prefix but still change
# something the system trades or reports on. Naming them individually is the
# point: backend/main.py asserts that every POST/PATCH/PUT/DELETE route in the
# app is either owner-guarded or listed here as a deliberate exception, so a new
# write endpoint cannot be added without someone answering the question.
#
# An independent review found four of these reachable by any signed-in free
# user, including one that resolves predictions and moves the ensemble weights
# the desk sizes trades from. Path prefixes alone could never have caught them:
# they live under /api/v5 and /api/universe, which free users are meant to read.
PUBLIC_WRITE_ROUTES: frozenset[str] = frozenset({
    # Sign-in flow. Writes a session cookie and nothing else.
    "/api/auth/login/{provider}",
    "/api/auth/logout",
    # A local model completion. The policy allowlist routes free users here on
    # purpose. It is exempt because it changes no state a later request can
    # read back — NOT because it is cheap: it builds market context first,
    # which fetches a dossier over the network, and then spends minutes of a 4B
    # model. (An earlier version of this comment claimed it "changes nothing",
    # which was untrue about the work it does.) The protection is the rate
    # limit — 20/hour, shared with /api/chat — not this exemption.
    "/api/chat/local",
    # Black-Scholes on numbers in the request body. Pure maths, no state.
    "/api/quant/greeks",
})


def owner_token() -> str:
    return (os.environ.get("ARIA_OWNER_TOKEN") or "").strip()


def owner_is_configured() -> bool:
    """True when ownership can be PROVEN — by a token or by an OAuth identity —
    rather than merely inferred from where the connection came from."""
    return bool(owner_token()
                or (os.environ.get("ARIA_OWNER_EMAIL") or "").strip())


# Evidence, gathered from live traffic, that something is in front of this app.
# Latched: once seen it stays seen for the life of the process.
_PROXY_SEEN: dict = {"detected": False, "how": ""}

# Headers only a proxy adds. A browser talking directly to the app never sends
# any of these.
_PROXY_HEADERS = ("cf-connecting-ip", "x-forwarded-for", "x-forwarded-host",
                  "forwarded", "x-real-ip")


def note_proxy_evidence(headers) -> bool:
    """Notice a reverse proxy without being told about one.

    ARIA_TRUST_PROXY exists to say "there is a proxy in front of me", and every
    protection keyed to it inherits the same weakness: it depends on an
    operator remembering a variable. Two rounds of review found holes that all
    reduced to that. A cloudflared tunnel forwards to localhost, so every
    caller arrives on loopback and inherits inferred ownership.

    But a tunnelled request is not silent about being tunnelled — it carries
    CF-Connecting-IP or X-Forwarded-For, which a direct caller has no reason to
    send. So the app can observe the thing it was waiting to be told: the first
    proxied request retires the loopback fallback for the rest of the process.

    The failure direction is deliberate. A hostile caller can send a fake
    X-Forwarded-For to a directly-exposed instance and lock the owner out of
    the loopback fallback until restart — which costs the owner a token, while
    the alternative costs them their notes.
    """
    if _PROXY_SEEN["detected"]:
        return True
    try:
        for h in _PROXY_HEADERS:
            if headers.get(h):
                _PROXY_SEEN.update(detected=True, how=h)
                logger.warning(
                    "proxy evidence: a request carried %s, so something is in "
                    "front of this app. The loopback owner fallback is now "
                    "OFF — every caller behind a proxy arrives on loopback, so "
                    "it cannot stand in for proof. Sign in, or set "
                    "ARIA_OWNER_TOKEN.", h)
                return True
    except Exception:                                       # pragma: no cover
        pass
    return False


def proxy_detected() -> dict:
    return dict(_PROXY_SEEN)


def loopback_owner_enabled() -> bool:
    """Whether "the request came from this machine" may still stand in for
    "the owner is asking".

    Only when nothing sits in front of the app. Behind a reverse proxy every
    request arrives from 127.0.0.1 — the proxy's own socket — so the loopback
    fallback would hand OWNER to the entire internet. ARIA_TRUST_PROXY is
    already the flag that says "there is a proxy in front of me", so it is also
    the flag that retires this fallback.
    """
    if _PROXY_SEEN["detected"]:
        return False
    return os.environ.get("ARIA_TRUST_PROXY", "").strip().lower() not in (
        "1", "true", "yes")


def _presented(headers) -> str:
    auth = headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return (headers.get("x-aria-token") or "").strip()


# How a role was arrived at. The distinction that matters is PROVEN versus
# INFERRED: a token match or a signed session is proof that survives being
# behind a proxy; "the connection came from 127.0.0.1" is not, because a proxy
# connects from 127.0.0.1 on behalf of the entire internet.
BASIS_TOKEN = "token"
BASIS_SESSION = "session"
BASIS_LOOPBACK = "loopback"          # inferred — never sufficient for a broker
BASIS_NONE = "none"

PROVEN_BASES = frozenset({BASIS_TOKEN, BASIS_SESSION})


def resolve_role_with_basis(headers, client_host: str | None,
                            session: dict | None = None,
                            *, peer_host: str | None = None) -> tuple[str, str]:
    """(role, basis). The basis is the security-relevant half.

    An earlier attempt to close the loopback-behind-a-tunnel hole asked "is an
    owner identity configured at all", which is a question about the
    environment rather than about this request. It had an obvious hole: setting
    ARIA_OWNER_EMAIL (which the deploy guide tells you to do, for OAuth)
    satisfied the configuration check while leaving the loopback fallback fully
    active — so inferred ownership reached brokers again, and a test I had
    written pinned that behaviour in place as if it were intended.

    Asking how THIS request proved ownership has no such gap. There is no
    environment variable that turns a loopback address into proof.
    """
    token = owner_token()
    if token:
        presented = _presented(headers)
        if presented and hmac.compare_digest(presented, token):
            return OWNER, BASIS_TOKEN

    if session:
        return (OWNER if session.get("owner") else FREE), BASIS_SESSION

    host = peer_host if peer_host is not None else client_host
    if not token and loopback_owner_enabled() and (host or "") in LOOPBACK:
        return OWNER, BASIS_LOOPBACK

    return ANON, BASIS_NONE


def resolve_role(headers, client_host: str | None, session: dict | None = None,
                 *, peer_host: str | None = None) -> str:
    """OWNER, FREE or ANON. Never raises — anything unreadable is simply not owner.

    Order matters. The owner token wins outright because it is the break-glass
    path that must keep working when OAuth or the network is broken. Then a
    signed session, whose `owner` flag was re-derived from ARIA_OWNER_EMAIL on
    read and cannot be forged. Only then the loopback fallback, and only while
    no token is configured, so the existing local workflow survives.

    `peer_host` is the address of the socket that actually connected. It exists
    because `client_host` may have come from X-Forwarded-For, which the caller
    writes: trusting it here would let anyone claim to be 127.0.0.1 and inherit
    the loopback fallback. Rate limiting still wants the forwarded address (it
    should count the real human, not the proxy); ownership must not.

    Callers that are about to touch a broker want `resolve_role_with_basis`
    instead — the role alone cannot tell them whether it was proven or guessed.
    """
    return resolve_role_with_basis(headers, client_host, session,
                                   peer_host=peer_host)[0]


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
    """Say plainly, at startup, what the current configuration means.

    This function existed for a while and was never called from anywhere — the
    warning was in the source and nowhere else, which is the same failure as a
    guard nobody invokes. It runs from the startup event now.
    """
    if owner_is_configured():
        return
    logger.warning(
        "No ARIA_OWNER_TOKEN and no ARIA_OWNER_EMAIL is set. Ownership is "
        "being INFERRED from loopback connections, which is fine on a laptop "
        "and wrong behind any proxy or tunnel — a proxy connects from "
        "127.0.0.1, so every caller would look like you. Routes that can reach "
        "a broker therefore REFUSE inferred ownership: execution, the desk and "
        "the approval queue will answer 403 until you set one of these. Set "
        "ARIA_TRUST_PROXY=1 as well if anything sits in front of this app.")
