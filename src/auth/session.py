"""
src/auth/session.py
===================
ARIA's owner sessions. One credential, one short-lived session, one cookie.

WHAT THIS USED TO BE, AND WHY IT CHANGED
----------------------------------------
This module used to carry an OAuth identity — provider, subject, email, avatar —
in a signed cookie, and decide ownership by comparing that email to
`ARIA_OWNER_EMAIL`. That design was sound for what it was asked to do: let other
people sign in with an account they already had, and identify exactly one of them
as the owner.

Two things retired it.

1. **The owner asked for Google and GitHub sign-in to be removed.** With no
   provider, there is no provider identity, and a cookie shaped to carry one has
   nothing to carry.

2. **The browser's real credential was never the cookie.** It was the raw
   `ARIA_OWNER_TOKEN`, kept in `localStorage` by `frontend/src/auth/ownerToken.js`
   and attached to every request. One XSS on this origin handed an attacker a
   permanent credential that opens the broker routes — and "permanent" was
   literal: there was no expiry, no revocation and no way to end a session
   short of rotating the token and signing every device out at once.

So the shape is now the one SENTINEL already used, and both applications share
the implementation in `ownerauth.py`:

    owner credential  ──►  open_session()  ──►  a session record on disk
                                                      │
                                                      ▼
                                       HttpOnly cookie naming that record

The cookie is not a credential. It cannot be replayed as one, it names a record
that can be revoked, and it dies of age and of idleness whether or not the
browser co-operates.

WHAT DID NOT CHANGE
-------------------
`read()` still returns a dict with an `owner` key, and `COOKIE` is still the
cookie's name — so `policy.resolve_role_with_basis`, `guard.resolve` and the
`_role_guard` middleware are untouched. The proven-versus-inferred basis and the
allowlist were deliberately left alone.

THERE IS NO FREE TIER
---------------------
It went with the providers: it existed so other people could sign in, and this
is an owner-only system. `read()` returns a session only for the owner, and
`policy` has three roles — OWNER, SERVICE (the SENTINEL peer), ANON.

THE BREAK-GLASS PATH SURVIVES
-----------------------------
`ARIA_OWNER_TOKEN` sent as `Authorization: Bearer` or `X-ARIA-Token` is still
PROVEN ownership (`policy.BASIS_TOKEN`). It has to be: it is what the deploy
check, the watchdog and a terminal use, and it is the way back in when a session
store is lost. What changed is that the BROWSER no longer holds it.
"""
from __future__ import annotations

import logging
from pathlib import Path

from . import ownerauth

logger = logging.getLogger(__name__)

COOKIE = "aria_session"
ROOT = Path(__file__).parent.parent.parent

#: Where sessions and the signing key live. Both are gitignored under data/auth/.
STORE = ROOT / "data" / "auth" / "sessions.json"
KEY_FILE = ROOT / "data" / ".session_key"

#: Environment names. `ARIA_OWNER_TOKEN` is the documented one and is unchanged;
#: `ARIA_OWNER_TOKEN_HASH` is the alternative for a credential stored as an
#: scrypt hash rather than in the clear, and wins when both are present so a
#: stale plaintext variable cannot quietly re-open an old credential.
ENV_TOKEN = "ARIA_OWNER_TOKEN"
ENV_TOKEN_HASH = "ARIA_OWNER_TOKEN_HASH"
ENV_SESSION_SECRET = "ARIA_SESSION_SECRET"

#: The sessions themselves. Module-level and shared, because the failed-attempt
#: counters live inside it — a limiter rebuilt per request has forgotten every
#: attempt and limits nothing. The test suite redirects `.store` in conftest.
SESSIONS = ownerauth.OwnerAuth(
    app="aria",
    store=STORE,
    secret_envs=(ENV_TOKEN,),
    hash_envs=(ENV_TOKEN_HASH,),
    session_secret_envs=(ENV_SESSION_SECRET,),
    session_key_file=KEY_FILE,
)


def configured() -> bool:
    """Is an owner credential installed, in either storage form?"""
    return SESSIONS.configured()


def open_session(presented: str, *, client: str = "unknown",
                 label: str = "") -> tuple[str, str]:
    """Credential -> (outcome, cookie value). The sign-in route's whole job.

    Throttling happens inside this call, before the credential is compared, so a
    locked-out caller cannot use the comparison for timing and a correct guess
    arriving mid-campaign does not win.
    """
    return SESSIONS.begin(presented, client=client, label=label)


def read(cookie_value: str | None) -> dict | None:
    """The live session behind this cookie, or None.

    Shaped for `policy.resolve_role_with_basis`, which asks only whether the
    session's `owner` flag is set. It is set from the fact that a session
    exists at all: the only way to open one is to present the owner credential,
    so there is no stored flag to forge and no email to re-derive.

    None covers every way of not being signed in — no cookie, a forged MAC, a
    revoked session, an expired one, one left idle too long. Callers must not
    branch on the difference.
    """
    s = SESSIONS.read(cookie_value)
    if s is None:
        return None
    return {
        "owner": True,
        "sid": s["sid"],
        "client": s.get("client"),
        "label": s.get("label"),
        "issued_at": s.get("issued_at"),
    }


def revoke(cookie_value: str | None) -> bool:
    """End this session server-side. What logout must call.

    Deleting the browser's copy of a cookie is housekeeping, not revocation: a
    copy captured beforehand keeps working. Marking the record is what ends it.
    """
    return SESSIONS.revoke(cookie_value)


def revoke_all() -> int:
    """Sign out everywhere. For a lost device, and for credential rotation —
    changing the credential must not leave sessions minted under the old one
    alive."""
    return SESSIONS.revoke_all()


def sessions() -> list[dict]:
    """Live sessions, newest first. Truncated ids, no MACs: the listing cannot
    be turned back into a working cookie."""
    return SESSIONS.sessions()


def cookie_kwargs(*, https: bool = False) -> dict:
    """What to pass to `set_cookie`.

    `https` decides the Secure flag and must describe the BROWSER's leg of the
    connection, which behind a tunnel is not the same as this process's socket
    scheme — use `hardening.is_https(request)`, which knows that.
    """
    return SESSIONS.cookie_kwargs(https=https)


def status() -> dict:
    """What a status endpoint may report: that a lock exists, never the key."""
    return SESSIONS.status()
