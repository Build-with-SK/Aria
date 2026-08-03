"""
src/auth/oauth.py
=================
Sign-in with Google, GitHub or Apple. Authorization Code flow, server-side.

WHY IT IS SHAPED LIKE THIS
--------------------------
ARIA never sees a password. The user authenticates at the provider, the provider
hands us a short-lived code on the redirect, and we exchange that code for an
identity over a back channel using a secret the browser never holds. Nothing
here stores or transports a credential — which is the point of using OAuth
rather than rolling a login.

The client secrets live in .env and are read at call time, so a provider you
have not configured is simply absent from the login page rather than a broken
button.

CSRF: the `state` parameter is signed with itsdangerous and carries a short
TTL. A callback whose state does not verify is rejected before any code is
exchanged, so a forged redirect cannot start a session.

APPLE
-----
Apple is implemented but inert unless configured, and it costs more than the
others to turn on: it needs a paid Apple Developer account, and its client
"secret" is not a string but an ES256-signed JWT you must generate from a .p8
key — which needs the `cryptography` package this project does not install.
`providers()` reports it unavailable with the reason, so the UI can show it
greyed rather than failing at the redirect.
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import time
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)

STATE_MAX_AGE = 600           # 10 minutes is plenty to complete a redirect


# ── provider definitions ─────────────────────────────────────────────────────

PROVIDERS = {
    "google": {
        "label": "Google",
        "authorize": "https://accounts.google.com/o/oauth2/v2/auth",
        "token": "https://oauth2.googleapis.com/token",
        "userinfo": "https://openidconnect.googleapis.com/v1/userinfo",
        "scope": "openid email profile",
        "id_env": "GOOGLE_CLIENT_ID",
        "secret_env": "GOOGLE_CLIENT_SECRET",
    },
    "github": {
        "label": "GitHub",
        "authorize": "https://github.com/login/oauth/authorize",
        "token": "https://github.com/login/oauth/access_token",
        "userinfo": "https://api.github.com/user",
        "emails": "https://api.github.com/user/emails",
        "scope": "read:user user:email",
        "id_env": "GITHUB_CLIENT_ID",
        "secret_env": "GITHUB_CLIENT_SECRET",
    },
    "apple": {
        "label": "Apple",
        "authorize": "https://appleid.apple.com/auth/authorize",
        "token": "https://appleid.apple.com/auth/token",
        "userinfo": None,             # identity arrives inside the id_token
        "scope": "name email",
        "id_env": "APPLE_CLIENT_ID",
        "secret_env": "APPLE_CLIENT_SECRET",
    },
}


def base_url() -> str:
    return (os.environ.get("ARIA_BASE_URL") or "http://localhost:8000").rstrip("/")


def redirect_uri(provider: str) -> str:
    return f"{base_url()}/api/auth/callback/{provider}"


def _creds(provider: str) -> tuple[str, str]:
    p = PROVIDERS[provider]
    return (os.environ.get(p["id_env"], "").strip(),
            os.environ.get(p["secret_env"], "").strip())


def _apple_ready() -> tuple[bool, str]:
    try:
        import cryptography  # noqa: F401
    except Exception:
        return False, "needs the 'cryptography' package and an Apple Developer account"
    return True, ""


def providers() -> list[dict]:
    """What the login page should offer, and why anything is missing."""
    out = []
    for key, p in PROVIDERS.items():
        cid, sec = _creds(key)
        available, reason = bool(cid and sec), ""
        if not available:
            reason = f"set {p['id_env']} and {p['secret_env']} in .env"
        if key == "apple" and available:
            available, why = _apple_ready()
            if not available:
                reason = why
        out.append({"id": key, "label": p["label"],
                    "available": available, "reason": reason})
    return out


def any_configured() -> bool:
    return any(p["available"] for p in providers())


# ── state signing ────────────────────────────────────────────────────────────

def _serializer():
    from itsdangerous import URLSafeTimedSerializer
    from src.auth.session import secret_key
    return URLSafeTimedSerializer(secret_key(), salt="aria-oauth-state")


def make_state(provider: str, next_path: str = "/") -> str:
    # next_path is validated on the way back out; never trust it as a full URL.
    return _serializer().dumps({"p": provider, "n": next_path,
                                "r": secrets.token_urlsafe(8)})


def read_state(state: str) -> dict | None:
    try:
        return _serializer().loads(state, max_age=STATE_MAX_AGE)
    except Exception as e:
        logger.warning("oauth state rejected: %s", e)
        return None


def safe_next(path: str | None) -> str:
    """Only same-site absolute paths. Blocks open-redirect via `//evil.com`
    and any scheme-bearing value."""
    p = (path or "/").strip()
    if not p.startswith("/") or p.startswith("//") or ":" in p.split("/")[0]:
        return "/"
    return p


# ── the flow ─────────────────────────────────────────────────────────────────

def authorize_url(provider: str, next_path: str = "/") -> str:
    p = PROVIDERS[provider]
    cid, _ = _creds(provider)
    params = {
        "client_id": cid,
        "redirect_uri": redirect_uri(provider),
        "response_type": "code",
        "scope": p["scope"],
        "state": make_state(provider, next_path),
    }
    if provider == "google":
        params["access_type"] = "online"
        params["prompt"] = "select_account"
    if provider == "apple":
        # Apple returns the profile as a form POST when name/email are asked for.
        params["response_mode"] = "form_post"
    return f"{p['authorize']}?{urllib.parse.urlencode(params)}"


def _post_form(url: str, data: dict, headers: dict | None = None) -> dict:
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", "ARIA")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode() or "{}")


def _get_json(url: str, token: str) -> dict:
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", "ARIA")
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode() or "{}")


def _decode_id_token(id_token: str) -> dict:
    """Read the claims out of an id_token WITHOUT verifying its signature.

    Safe only because of where it is used: the token came back over TLS on a
    direct back-channel call to the provider's token endpoint, authenticated
    with our client secret. It was never in the browser's hands. Verifying the
    signature here would need JWKS fetching and a crypto dependency for no
    additional guarantee in this specific flow. Do NOT reuse this on a token
    that arrived from a client.
    """
    import base64
    try:
        payload = id_token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except Exception:
        return {}


def exchange(provider: str, code: str) -> dict | None:
    """Code → identity dict {sub, email, name, avatar, provider}. None on failure."""
    p = PROVIDERS[provider]
    cid, sec = _creds(provider)
    if not (cid and sec):
        return None
    try:
        tok = _post_form(p["token"], {
            "client_id": cid,
            "client_secret": sec,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri(provider),
        })
    except Exception as e:
        logger.warning("oauth token exchange failed for %s: %s", provider, e)
        return None

    access = tok.get("access_token")

    if provider == "apple":
        claims = _decode_id_token(tok.get("id_token", ""))
        if not claims.get("sub"):
            return None
        return {"provider": "apple", "sub": claims["sub"],
                "email": claims.get("email", ""), "name": "", "avatar": ""}

    if not access:
        logger.warning("oauth: no access_token from %s (%s)", provider,
                       tok.get("error") or list(tok)[:4])
        return None

    try:
        info = _get_json(p["userinfo"], access)
    except Exception as e:
        logger.warning("oauth userinfo failed for %s: %s", provider, e)
        return None

    if provider == "google":
        if not info.get("sub"):
            return None
        return {"provider": "google", "sub": info["sub"],
                "email": (info.get("email") or "").lower(),
                "name": info.get("name", ""), "avatar": info.get("picture", "")}

    if provider == "github":
        if not info.get("id"):
            return None
        email = (info.get("email") or "").lower()
        if not email:
            # A GitHub user with a private email needs the extra call, and only
            # a verified primary address is worth trusting for identity.
            try:
                for e in _get_json(p["emails"], access):
                    if e.get("primary") and e.get("verified"):
                        email = (e.get("email") or "").lower()
                        break
            except Exception:
                pass
        return {"provider": "github", "sub": str(info["id"]), "email": email,
                "name": info.get("name") or info.get("login", ""),
                "avatar": info.get("avatar_url", "")}

    return None
