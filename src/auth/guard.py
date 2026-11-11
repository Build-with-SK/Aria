"""
src/auth/guard.py
=================
The per-route half of access control.

WHY A SECOND LAYER
------------------
`_role_guard` in backend/main.py is a middleware: it default-denies by path
prefix and it is genuinely structural — a new route is invisible to non-owners
until somebody names it. But it decides using a *string*, and there is one way
to lose: add an execution route under a prefix that is already allowed
(`/api/v5/execute-now`, say) and it inherits that prefix's permission.

So the routes that can reach a broker also declare it themselves, as a FastAPI
dependency. Then the permission travels with the handler rather than with the
URL it happens to be mounted at, and a route moved or renamed keeps its guard.

Deliberately does NOT trust `request.state.role`. The middleware sets it, and a
dependency that reads it would silently degrade to "allow" the day the
middleware is reordered, removed, or bypassed by a sub-application that never
had it. It re-derives the role from the request instead — the same function,
the same inputs, no shared mutable state.
"""
from __future__ import annotations

import logging

from fastapi import HTTPException, Request

from . import hardening, policy, session as sess

logger = logging.getLogger(__name__)


def resolve(request: Request) -> str:
    """The caller's role, derived from scratch. Never reads request.state."""
    return resolve_with_basis(request)[0]


def resolve_with_basis(request: Request) -> tuple[str, str]:
    """(role, how it was established). Derived from scratch every time."""
    who = sess.read(request.cookies.get(sess.COOKIE))
    return policy.resolve_role_with_basis(
        request.headers,
        hardening.client_ip(request),
        who,
        peer_host=hardening.peer_ip(request),
    )


def require_owner(request: Request) -> str:
    """Dependency: 401 if nobody is signed in, 403 if somebody is but is not
    the owner. Attach to every route that can place, modify or cancel an order.

    PROVEN ownership, not inferred. `resolve_role` will fall back to "this
    connection came from 127.0.0.1, so it must be the owner" — fine on a
    laptop, fatal behind a tunnel, because cloudflared connects to localhost
    and so every request on earth arrives from the loopback address.

    The test is applied to THIS REQUEST, not to the environment. A first
    attempt asked "is an owner identity configured anywhere", which sounds
    equivalent and is not: setting ARIA_OWNER_EMAIL satisfied it while leaving
    the loopback fallback entirely active, so inferred ownership reached
    brokers again. There is no environment variable that turns a loopback
    address into proof, so the question is simply how this caller proved it —
    a token, or a signed session. Nothing else opens a broker.
    """
    role, basis = resolve_with_basis(request)
    if role == policy.OWNER:
        if basis not in policy.PROVEN_BASES:
            logger.error(
                "guard: refusing %s %s — ownership was INFERRED from a %s "
                "connection, not proven. Behind a reverse proxy or tunnel "
                "every caller arrives on loopback, so this cannot reach a "
                "broker. Set ARIA_OWNER_TOKEN, or sign in.",
                request.method, request.url.path, basis)
            raise HTTPException(status_code=403, detail={
                "detail": "Execution requires proven ownership.",
                "reason": "This request was treated as the owner only because "
                          "it arrived from a loopback address, which is what "
                          "every request looks like behind a proxy. Set "
                          "ARIA_OWNER_TOKEN and send it, or sign in with the "
                          "owner account.",
                "basis": basis,
                "role": role,
            })
        return role
    logger.info("guard: %s refused %s %s", role, request.method, request.url.path)
    if role == policy.ANON:
        raise HTTPException(status_code=401, detail={
            "detail": "Sign in to use ARIA.",
            "login": "/api/auth/login",
            "role": role,
        })
    raise HTTPException(status_code=403, detail={
        "detail": "This is owner-only.",
        "reason": policy.denial_reason(request.url.path),
        "role": role,
    })


# ── step-up: authenticated is not the same as allowed right now ──────────────
#
# `require_owner` answers "is this the owner". It cannot answer "is the owner
# holding the device right now", and for most routes it does not need to. For a
# handful it does: approving a trade sends an order to a broker, releasing the
# kill switch re-enables trading, arming auto-execute lets the desk trade
# unattended, and the desk config holds the caps that bound all of it. A session
# that has sat open on a phone for five days is not enough for those.
#
# So those routes additionally need the credential to have been presented
# recently — at sign-in, or via POST /api/auth/confirm. What counts as recent
# lives in ownerauth (STEP_UP_SECONDS), shared with SENTINEL.
#
# What is deliberately NOT here: anything that REDUCES risk. Engaging the kill
# switch, cancelling or rejecting a trade and disarming auto-execute must stay
# one click. A safety control that asks for a password is one that gets used
# too late.

STEP_UP_ERROR = "step_up_required"


def has_stepped_up(request: Request) -> bool:
    """Owner, proven, and the credential presented recently.

    A bearer caller passes outright: it presented the credential on THIS
    request, which is the most recent confirmation there can be.
    """
    role, basis = resolve_with_basis(request)
    if role != policy.OWNER or basis not in policy.PROVEN_BASES:
        return False
    if basis == policy.BASIS_TOKEN:
        return True
    return sess.SESSIONS.recently_confirmed(request.cookies.get(sess.COOKIE))


def step_up_or_403(request: Request) -> None:
    """For a handler whose risk depends on its arguments — call it only on the
    branch that raises risk."""
    if has_stepped_up(request):
        return
    logger.info("guard: step-up required for %s %s",
                request.method, request.url.path)
    raise HTTPException(status_code=403, detail={
        "detail": "Confirm it's you. This action moves money or loosens a "
                  "safety control, so it needs your credential again.",
        "error": STEP_UP_ERROR,
        "confirm": "/api/auth/confirm",
    })


def require_step_up(request: Request) -> str:
    """Dependency form. Runs `require_owner` itself rather than trusting it ran
    first — the same reason `resolve` never reads request.state: an ordering
    assumption is a guard that silently disappears when someone reorders a
    list."""
    role = require_owner(request)
    step_up_or_403(request)
    return role


#: How FastAPI reads a truthy bool from a query string. Mirrored exactly, so this
#: guard and the handler can never disagree about whether a request is arming.
_TRUTHY = frozenset({"1", "true", "on", "yes"})


def require_step_up_to_arm(request: Request) -> None:
    """Step-up only when the request ARMS something (`?enabled=true`).

    Disarming passes straight through. A route-level dependency rather than a
    check inside the handler so the handler's signature — which tests call
    directly to exercise its paper-account gate — stays as it was.
    """
    if (request.query_params.get("enabled") or "").strip().lower() in _TRUTHY:
        step_up_or_403(request)
