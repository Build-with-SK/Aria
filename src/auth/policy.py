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
Two ways, and both count as PROVEN:

  * `ARIA_OWNER_TOKEN` (or `ARIA_OWNER_TOKEN_HASH`) sent as
    `Authorization: Bearer <token>` or `X-ARIA-Token`. The break-glass path —
    scripts, the deploy check, a terminal, and the way back in when a session
    store is lost.
  * A session cookie issued by `POST /api/auth/login` and backed by a record in
    `ownerauth`. This is what a browser uses. It expires, it can be revoked, and
    it is not a credential: it cannot be replayed as one.

Google, GitHub and Apple sign-in were removed at the owner's request. There is
no third-party identity in this system any more, so ownership is no longer
derived from an email address and ARIA_OWNER_EMAIL is no longer consulted.

If NOTHING is configured, requests from LOOPBACK are treated as the owner and
everything else is anonymous. That keeps the existing single-user localhost
workflow working untouched while making any remote caller anonymous by default. It is
a deliberate compromise for a machine that is not yet exposed — the moment this
is reachable from anywhere else, set the credential. Routes that can reach a
broker refuse inferred ownership outright (see PROVEN_BASES).
"""
from __future__ import annotations

import hmac
import logging
import os

logger = logging.getLogger(__name__)

# THREE ROLES, AND EACH EXISTS BECAUSE SOMETHING NEEDS IT.
#
#   OWNER    the human. The only person who signs in. Everything is theirs.
#   SERVICE  SENTINEL, calling ARIA with ARIA_SERVICE_TOKEN — five routes.
#   ANON     not signed in: the login routes, liveness and the UI shell.
#
# There used to be a FREE role: signed in, not the owner, research-only. It
# existed for Google/GitHub sign-in, which let other people in. Both went
# together — this is a private, owner-only system, and a role nothing can
# ever be granted is not a safety margin, it is a branch nobody tests against
# reality. A session that is somehow not the owner's is ANON.
OWNER = "owner"
ANON = "anon"          # not signed in — gets nothing but the login routes

# ── the service role ─────────────────────────────────────────────────────────
#
# One named peer — SENTINEL — with a method-and-route allowlist and nothing else.
#
# WHY IT EXISTS. SENTINEL held ARIA_OWNER_TOKEN, because that was the only
# credential ARIA had that opened /api/brain/status. That token also opens
# /api/execute/approve/{id} and /api/execute/kill-switch/release. The rule that
# SENTINEL does not touch those was enforced by nobody having written the call —
# which is not a permission boundary, it is an absence. A credential's blast
# radius is every route it opens, not the routes its holder currently chooses.
#
# WHAT IT IS NOT. It is not a tier, not an upgrade path and not a second owner.
# It cannot be granted anything by editing a config file: the allowlist is a
# literal below, method-qualified, and every entry had to be justified against
# a call SENTINEL actually makes.
SERVICE = "service"

#: Every role there is. Enumerated so that checks like "no role other than the
#: owner may read the vault" can be written against the list rather than
#: against a hand-written set of names that goes stale — which is exactly what
#: happened when FREE was removed and a preflight check kept naming it.
ROLES: tuple[str, ...] = (OWNER, SERVICE, ANON)

#: Exactly what the service peer may call, as (METHOD, path). Method-qualified
#: because "may read the brain's status" and "may post to the brain" are not the
#: same grant, and a path-only allowlist cannot tell them apart.
#:
#: Derived from an inventory of every request sentinel/aria.py actually issues.
#: Four of its five calls are here. The fifth is GET /api/execute/queue, and it
#: is deliberately ABSENT: that route carries `Depends(require_owner)`, so
#: admitting the service role would have meant weakening the guard that stands
#: on every order-placing route. Losing the "ARIA has trades waiting" line in
#: SENTINEL's morning brief is the correct price. sentinel/aria.py already
#: renders a refused probe as refused rather than as "nothing pending".
SERVICE_ROUTES: frozenset[tuple[str, str]] = frozenset({
    ("GET", "/health"),             # liveness
    ("GET", "/api/summary"),        # the signal summary
    ("GET", "/api/brain/status"),   # is the local brain up, and on which model
    # The ONLY write the service peer may make, and it writes nothing that a
    # later request can read back. Deliberately /api/chat/local and never
    # /api/chat: the cloud path RECORDS A HELD TRADE INTENT when the message
    # parses as an order (backend/main.py, `intents.record`). A consultation
    # peer must not be able to put an intent in the owner's book by phrasing a
    # question as "should I buy 20 AAPL".
    ("POST", "/api/chat/local"),
    # The STRUCTURED form of the same consultation: analysis plus the data's
    # age, its sources, whether it needs checking, and a confidence only when
    # the signal engine actually produced one. Same model and same no-vault,
    # no-intent path as /api/chat/local; it exists so SENTINEL can tell the
    # owner how old ARIA's numbers are instead of relaying free text.
    ("POST", "/api/specialist/analysis"),
})


def service_token() -> str:
    return (os.environ.get("ARIA_SERVICE_TOKEN") or "").strip()


def service_token_is_distinct() -> bool:
    """A service token equal to the owner token is not a scoped credential; it
    is the owner token wearing a different name. Refuse rather than pretend."""
    svc = service_token()
    if not svc:
        return False
    # Asked as "does this value authenticate AS THE OWNER" rather than "is it
    # the same string as ARIA_OWNER_TOKEN". The string comparison was right
    # while the credential was always plaintext, and became blind the moment it
    # could be stored as a hash: `owner_token()` is then empty, the comparison
    # is skipped, and a service token identical to the owner's would have been
    # accepted as correctly scoped.
    from . import ownerauth
    stored = owner_token_hash() or owner_token()
    if stored and ownerauth.verify_secret(svc, stored):
        logger.error(
            "ARIA_SERVICE_TOKEN is identical to ARIA_OWNER_TOKEN. The service "
            "role is DISABLED: a scoped credential that equals the owner's is "
            "not scoped. Issue a different value.")
        return False
    # And not the other direction's credential either: one value that opened
    # SENTINEL -> ARIA and ARIA -> SENTINEL would be one leak for two doors.
    for other in ("SENTINEL_SERVICE_TOKEN", "SENTINEL_CONSULT_TOKEN"):
        v = (os.environ.get(other) or "").strip()
        if v and hmac.compare_digest(svc, v):
            logger.error("ARIA_SERVICE_TOKEN equals %s — the service role is "
                         "DISABLED until they differ.", other)
            return False
    return True


def service_may(method: str, path: str) -> bool:
    """Exact match on (METHOD, path). No prefixes, deliberately.

    Prefix matching is how `/api/execute/queue` would have quietly become
    `/api/execute/approve/{id}` — one of those is a read and the other moves
    money, and they share six characters of path.
    """
    p = (path or "").rstrip("/") or "/"
    return ((method or "").upper(), p) in SERVICE_ROUTES

LOOPBACK = {"127.0.0.1", "::1", "localhost"}
# "testclient" is deliberately NOT here. It was, and it meant every request made
# through a FastAPI TestClient authenticated as the owner — so a test could not
# prove that an execution endpoint refuses an anonymous caller, because the test
# itself was the owner. Tests now present real credentials or a real remote peer.

# Reachable without a session. Deliberately tiny: the routes needed to sign in,
# and a health check for the deploy script. Everything else requires an account.
PUBLIC_PREFIXES: tuple[str, ...] = (
    "/health",
    # Readiness: a bare true/false for the watchdog and the deploy check. The
    # reasons are logged and shown only to signed-in callers.
    "/ready",
    "/api/auth/",          # login, logout, status, me — sessions + logout-everywhere are guarded per route
    # THE STATIC INTERFACE: the built SPA's HTML shell, scripts, styles and
    # icons. No data, no credential — every byte of it is fetched from the API
    # afterwards, and the API is what is guarded.
    #
    # It was owner-only, which meant the backend-served build could not load
    # its own sign-in page: an anonymous browser asked for /app/login and got a
    # raw 401 JSON body. Locally that was hidden by the Vite dev server, which
    # serves the interface itself; behind a tunnel on the Mac mini it would
    # have meant nobody could ever sign in. SENTINEL made the same call for the
    # same reason (sentinel/auth.py: "paths outside /api are the static
    # interface").
    #
    # Checked before being opened: the bundle bakes in no environment values
    # (no import.meta.env.VITE_* anywhere), and the only secret-shaped strings
    # in it are UI text naming variables. `/app/` with its slash, so "/apple"
    # is not caught by a bare "/app" prefix.
    "/app/",
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
    # Sign-in flow. Writes a session record and a cookie, and nothing else.
    #
    # `/api/auth/login` REPLACED `/api/auth/login/{provider}` when Google,
    # GitHub and Apple were removed. It cannot be owner-guarded for the obvious
    # reason — it is how you become the owner — so it carries its own defence
    # instead: the failed-attempt throttle in ownerauth, which gets slower and
    # then stops, plus the per-IP limiter in the middleware above it.
    "/api/auth/login",
    # Ends a session. Revokes only the session presented, so an anonymous caller
    # has nothing to revoke. `/api/auth/logout-everywhere` is a different thing
    # and IS owner-guarded: signing the owner out of every device is an owner
    # action, not part of the sign-in flow.
    "/api/auth/logout",
    # A local model completion — the SERVICE peer's consultation route. It is
    # exempt because it changes no state a later request can read back — NOT because it is cheap: it builds market context first,
    # which fetches a dossier over the network, and then spends minutes of a 4B
    # model. (An earlier version of this comment claimed it "changes nothing",
    # which was untrue about the work it does.) The protection is the rate
    # limit — 20/hour, shared with /api/chat — not this exemption.
    "/api/chat/local",
    # Black-Scholes on numbers in the request body. Pure maths, no state.
    "/api/quant/greeks",
    # ARIA answering as the specialist SENTINEL consults. Not owner-guarded by
    # dependency because the SERVICE peer must reach it; the middleware still
    # admits only OWNER and SERVICE. Writes nothing a later request can read —
    # deliberately built on the /api/chat/local path, never /api/chat, which
    # records trade intents.
    "/api/specialist/analysis",
})


def owner_token() -> str:
    return (os.environ.get("ARIA_OWNER_TOKEN") or "").strip()


def owner_token_hash() -> str:
    """The same credential stored as an scrypt hash (see ownerauth). A hash is
    not comparable here — `resolve_role_with_basis` needs a constant-time match
    against a presented value and delegates that to ownerauth — but its
    PRESENCE is what says a credential is configured at all."""
    return (os.environ.get("ARIA_OWNER_TOKEN_HASH") or "").strip()


def owner_is_configured() -> bool:
    """True when ownership can be PROVEN rather than merely inferred from where
    the connection came from.

    This used to also accept ARIA_OWNER_EMAIL, because an OAuth identity was a
    second way to prove ownership. Google, GitHub and Apple sign-in were removed
    at the owner's request, so that variable no longer proves anything and is no
    longer consulted — leaving it in would have meant a stale value in a .env
    silently satisfying this check while nothing could actually authenticate.
    """
    return bool(owner_token() or owner_token_hash())


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
BASIS_SERVICE = "service_token"      # a named peer, NOT the owner
BASIS_NONE = "none"

#: Bases that prove OWNERSHIP. `BASIS_SERVICE` is deliberately absent: the
#: service token proves "I am SENTINEL", which is a different claim from "I am
#: the owner". `require_owner` tests the role first and would refuse the service
#: role anyway, so this is the second of two independent reasons a service
#: credential cannot reach a broker.
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
    presented = _presented(headers)
    if owner_is_configured() and presented:
        # Delegated so that a credential stored as an scrypt hash works on this
        # path too. Comparing against `owner_token()` directly — as this did —
        # silently stopped authenticating anybody the moment an operator moved
        # the credential to ARIA_OWNER_TOKEN_HASH, because the plaintext
        # variable is then empty and the branch was never entered.
        from . import ownerauth
        stored = owner_token_hash() or owner_token()
        if ownerauth.verify_secret(presented, stored):
            return OWNER, BASIS_TOKEN

    # The service peer, checked AFTER the owner so a misconfiguration that set
    # both to the same value resolves to owner and is caught by
    # `service_token_is_distinct()` rather than silently downgrading the owner.
    if presented and service_token_is_distinct() and hmac.compare_digest(
            presented, service_token()):
        return SERVICE, BASIS_SERVICE

    if session:
        if session.get("owner"):
            return OWNER, BASIS_SESSION
        return ANON, BASIS_NONE

    host = peer_host if peer_host is not None else client_host
    if (not owner_is_configured() and loopback_owner_enabled()
            and (host or "") in LOOPBACK):
        return OWNER, BASIS_LOOPBACK

    return ANON, BASIS_NONE


def resolve_role(headers, client_host: str | None, session: dict | None = None,
                 *, peer_host: str | None = None) -> str:
    """OWNER, SERVICE or ANON. Never raises — anything unreadable is simply not owner.

    Order matters. The owner token wins outright because it is the break-glass
    path that must keep working when OAuth or the network is broken. Then a
    session record from ownerauth, which exists only because the credential
    was presented and so has no owner flag to forge. Only then the loopback fallback, and only while
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


def is_allowed(path: str, role: str, method: str = "GET") -> bool:
    """Default deny. The owner may do anything; the service peer gets its five
    (METHOD, path) pairs; anyone else gets only the public routes.

    `method` matters for one role only. The service peer is granted
    (METHOD, path) pairs rather than prefixes, so it defaults to GET here and
    every existing caller keeps its meaning unchanged.
    """
    if role == SERVICE:
        # Checked BEFORE is_public so the service role is described entirely by
        # its own allowlist — one list to read when asking what SENTINEL can do,
        # rather than that list plus whatever happens to be public today.
        return service_may(method, path)
    if is_public(path):
        return True
    return role == OWNER


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
        "SERVER NOT INITIALIZED: no ARIA_OWNER_TOKEN and no "
        "ARIA_OWNER_TOKEN_HASH is set, so nobody can sign in. Ownership is "
        "being INFERRED from loopback connections, which is fine on a laptop "
        "and wrong behind any proxy or tunnel — a proxy connects from "
        "127.0.0.1, so every caller would look like you. Routes that can reach "
        "a broker therefore REFUSE inferred ownership: execution, the desk and "
        "the approval queue will answer 403 until you set one of these. Set "
        "ARIA_TRUST_PROXY=1 as well if anything sits in front of this app.")
