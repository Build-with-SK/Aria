"""
Sessions and sign-in.

The load-bearing assertion here is that ownership cannot be claimed by the
client. A cookie is tamper-evident, not secret: the user can read their own
email out of it, and must not be able to change it.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.auth import oauth, policy  # noqa: E402
from src.auth import session as sess  # noqa: E402


@pytest.fixture(autouse=True)
def _fixed_key(monkeypatch):
    monkeypatch.setenv("ARIA_SESSION_SECRET", "test-signing-key-not-a-real-one")


GOOGLE = {"provider": "google", "sub": "1", "email": "Me@Example.com",
          "name": "Me", "avatar": ""}
OTHER = {"provider": "github", "sub": "2", "email": "someone@else.com",
         "name": "Someone", "avatar": ""}


# ── ownership ────────────────────────────────────────────────────────────────

def test_owner_email_match_is_case_insensitive(monkeypatch):
    monkeypatch.setenv("ARIA_OWNER_EMAIL", "me@example.com")
    assert sess.is_owner_identity(GOOGLE) is True


def test_other_users_are_not_owner(monkeypatch):
    monkeypatch.setenv("ARIA_OWNER_EMAIL", "me@example.com")
    assert sess.is_owner_identity(OTHER) is False


def test_nobody_is_owner_when_owner_email_unset(monkeypatch):
    """Fail closed. An unset owner email must not promote the first person to
    sign in — this is the one account that can move money."""
    monkeypatch.delenv("ARIA_OWNER_EMAIL", raising=False)
    assert sess.is_owner_identity(GOOGLE) is False
    assert sess.is_owner_identity(OTHER) is False


def test_roundtrip_preserves_identity(monkeypatch):
    monkeypatch.setenv("ARIA_OWNER_EMAIL", "me@example.com")
    got = sess.read(sess.issue(GOOGLE))
    assert got["email"] == "me@example.com"
    assert got["owner"] is True


def test_tampered_cookie_is_rejected(monkeypatch):
    monkeypatch.setenv("ARIA_OWNER_EMAIL", "me@example.com")
    tok = sess.issue(OTHER)
    # Flip a character in the payload; the signature must stop verifying.
    broken = ("A" if tok[0] != "A" else "B") + tok[1:]
    assert sess.read(broken) is None


def test_cookie_signed_with_another_key_is_rejected(monkeypatch):
    tok = sess.issue(GOOGLE)
    monkeypatch.setenv("ARIA_SESSION_SECRET", "a-completely-different-key")
    assert sess.read(tok) is None


def test_ownership_is_rederived_not_trusted(monkeypatch):
    """A cookie minted while someone was owner must lose owner rights the
    moment ARIA_OWNER_EMAIL changes."""
    monkeypatch.setenv("ARIA_OWNER_EMAIL", "me@example.com")
    tok = sess.issue(GOOGLE)
    assert sess.read(tok)["owner"] is True
    monkeypatch.setenv("ARIA_OWNER_EMAIL", "someone-new@example.com")
    assert sess.read(tok)["owner"] is False


def test_garbage_cookies_are_none():
    for junk in (None, "", "not-a-token", "a.b.c"):
        assert sess.read(junk) is None


# ── anonymous access ─────────────────────────────────────────────────────────

def test_anonymous_when_no_session_and_not_loopback(monkeypatch):
    monkeypatch.setenv("ARIA_OWNER_TOKEN", "tok")
    assert policy.resolve_role({}, "203.0.113.9", None) == policy.ANON


def test_session_without_owner_flag_is_free(monkeypatch):
    monkeypatch.setenv("ARIA_OWNER_TOKEN", "tok")
    assert policy.resolve_role({}, "203.0.113.9", {"owner": False}) == policy.FREE


def test_session_with_owner_flag_is_owner(monkeypatch):
    monkeypatch.setenv("ARIA_OWNER_TOKEN", "tok")
    assert policy.resolve_role({}, "203.0.113.9", {"owner": True}) == policy.OWNER


@pytest.mark.parametrize("path", [
    "/api/v5/analyse/AAPL", "/api/signals", "/api/chat/local",
    "/api/portfolio", "/api/vault/search", "/api/execute/queue",
])
def test_anonymous_gets_nothing(path):
    assert policy.is_allowed(path, policy.ANON) is False, path


@pytest.mark.parametrize("path", [
    "/health", "/api/auth/providers", "/api/auth/login/google",
    "/api/auth/callback/github", "/api/auth/me",
])
def test_sign_in_routes_stay_public(path):
    assert policy.is_allowed(path, policy.ANON) is True, path


# ── open redirect ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("bad", [
    "//evil.com", "https://evil.com", "http://evil.com/x",
    "javascript:alert(1)", "evil.com", "",
])
def test_next_path_cannot_leave_the_site(bad):
    assert oauth.safe_next(bad) == "/", bad


@pytest.mark.parametrize("good", ["/", "/research", "/v5?symbol=AAPL"])
def test_same_site_next_paths_survive(good):
    assert oauth.safe_next(good) == good


# ── state ────────────────────────────────────────────────────────────────────

def test_state_roundtrip_and_rejection():
    st = oauth.make_state("google", "/research")
    got = oauth.read_state(st)
    assert got["p"] == "google" and got["n"] == "/research"
    assert oauth.read_state("forged") is None
    assert oauth.read_state(st[:-4] + "zzzz") is None


def test_unconfigured_providers_report_why(monkeypatch):
    for v in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET",
              "GITHUB_CLIENT_ID", "GITHUB_CLIENT_SECRET",
              "APPLE_CLIENT_ID", "APPLE_CLIENT_SECRET"):
        monkeypatch.delenv(v, raising=False)
    ps = {p["id"]: p for p in oauth.providers()}
    assert set(ps) == {"google", "github", "apple"}
    for p in ps.values():
        assert p["available"] is False
        assert p["reason"], "an unavailable provider must say what is missing"
    assert oauth.any_configured() is False
