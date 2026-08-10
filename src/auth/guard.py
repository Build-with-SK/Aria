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
            "login": "/api/auth/providers",
            "role": role,
        })
    raise HTTPException(status_code=403, detail={
        "detail": "This is owner-only.",
        "reason": policy.denial_reason(request.url.path),
        "role": role,
    })
