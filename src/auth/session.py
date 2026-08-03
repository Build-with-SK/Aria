"""
src/auth/session.py
===================
Signed session cookies. No server-side session store, no database.

The cookie carries the user's provider identity and nothing else, signed with a
key that never leaves the server. It is tamper-evident rather than encrypted:
a user can read their own email out of their own cookie, which is fine, but
they cannot change the email — or promote themselves to owner — without the
key, because the signature stops verifying.

WHO IS THE OWNER
----------------
Owner is decided HERE, from the verified provider email, and never from
anything the client sends. Set ARIA_OWNER_EMAIL to the address you sign in
with. If it is unset, the first person to sign in is not silently promoted —
nobody is owner over OAuth and you fall back to ARIA_OWNER_TOKEN. Failing
closed matters more than convenience for the one account that can move money.
"""
from __future__ import annotations

import logging
import os
import secrets
from pathlib import Path

logger = logging.getLogger(__name__)

COOKIE = "aria_session"
MAX_AGE = 60 * 60 * 24 * 14          # two weeks
ROOT = Path(__file__).parent.parent.parent
KEY_FILE = ROOT / "data" / ".session_key"


def secret_key() -> str:
    """Signing key: env first, else a persisted random key.

    Persisting matters — a key regenerated on each boot would silently sign
    everyone out every restart, which on a 24/7 box looks like a random bug
    rather than a design choice.
    """
    env = (os.environ.get("ARIA_SESSION_SECRET") or "").strip()
    if env:
        return env
    try:
        if KEY_FILE.exists():
            k = KEY_FILE.read_text(encoding="utf-8").strip()
            if k:
                return k
        KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
        k = secrets.token_urlsafe(48)
        KEY_FILE.write_text(k, encoding="utf-8")
        try:
            os.chmod(KEY_FILE, 0o600)      # no-op on Windows, correct on the mini
        except Exception:
            pass
        logger.info("generated a new session signing key at %s", KEY_FILE)
        return k
    except Exception as e:
        # Ephemeral fallback: sessions die on restart, but the app still runs.
        logger.warning("could not persist a session key (%s) — using an ephemeral one", e)
        return secrets.token_urlsafe(48)


def _serializer():
    from itsdangerous import URLSafeTimedSerializer
    return URLSafeTimedSerializer(secret_key(), salt="aria-session")


def owner_email() -> str:
    return (os.environ.get("ARIA_OWNER_EMAIL") or "").strip().lower()


def is_owner_identity(identity: dict) -> bool:
    """Owner iff the provider-verified email matches ARIA_OWNER_EMAIL."""
    want = owner_email()
    if not want:
        return False
    got = (identity.get("email") or "").strip().lower()
    return bool(got) and got == want


def issue(identity: dict) -> str:
    return _serializer().dumps({
        "sub": identity.get("sub"),
        "provider": identity.get("provider"),
        "email": (identity.get("email") or "").lower(),
        "name": identity.get("name") or "",
        "avatar": identity.get("avatar") or "",
        "owner": is_owner_identity(identity),
    })


def read(cookie_value: str | None) -> dict | None:
    if not cookie_value:
        return None
    try:
        data = _serializer().loads(cookie_value, max_age=MAX_AGE)
    except Exception:
        return None
    if not isinstance(data, dict) or not data.get("sub"):
        return None
    # Re-derive ownership on every read rather than trusting the stored flag.
    # If ARIA_OWNER_EMAIL changes, old cookies must not keep owner rights.
    data["owner"] = is_owner_identity(data)
    return data


def cookie_kwargs() -> dict:
    """Secure only under HTTPS — a Secure cookie on plain http would simply
    never be sent back, which presents as 'login does nothing'."""
    https = (os.environ.get("ARIA_BASE_URL") or "").startswith("https://")
    return {"httponly": True, "samesite": "lax", "secure": https,
            "max_age": MAX_AGE, "path": "/"}
