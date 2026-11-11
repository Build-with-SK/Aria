"""
Sessions and sign-in.

WHAT THIS FILE USED TO TEST, AND WHY IT DOES NOT ANY MORE
---------------------------------------------------------
It tested the OAuth session: that a provider identity round-tripped through a
signed cookie, that ownership was re-derived from ARIA_OWNER_EMAIL on every read
rather than trusted from the cookie, and that the `next` path could not be used
for an open redirect. Those were the right tests for that design.

Google, GitHub and Apple sign-in were removed at the owner's request, so there
is no provider identity, no email to compare and no redirect to protect. What
replaced them is `src/auth/ownerauth.py`, shared with SENTINEL and covered in
depth by `tests/test_ownerauth.py` — 40 assertions about expiry, revocation,
rotation, forgery and throttling, run against both copies of the module.

What remains here is ARIA's own half: that `session.read()` still produces what
`policy` expects, that the role ladder is unchanged, and that the sign-in routes
are the only thing an anonymous caller can reach.

ON THE FREE TIER
----------------
Gone. It existed for other people signing in with a provider; with the
providers removed nothing could grant it, and it was removed at the owner's
request for an owner-only system. Three roles remain: OWNER, SERVICE, ANON.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.auth import ownerauth, policy  # noqa: E402
from src.auth import session as sess  # noqa: E402

TOKEN = "an-owner-credential-for-tests-only-0123456789"


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    """Its own store and signing key, so the suite never touches the owner's
    real sessions — running the tests used to be able to sign him out."""
    monkeypatch.setenv("ARIA_SESSION_SECRET", "test-signing-key-not-a-real-one")
    monkeypatch.setenv(sess.ENV_TOKEN, TOKEN)
    monkeypatch.delenv(sess.ENV_TOKEN_HASH, raising=False)
    monkeypatch.setattr(sess.SESSIONS, "store", tmp_path / "sessions.json")
    monkeypatch.setattr(sess.SESSIONS, "session_key_file", tmp_path / ".key")
    sess.SESSIONS._attempts.clear()


# ── what policy is handed ────────────────────────────────────────────────────

def test_a_live_session_reads_as_the_owner():
    outcome, cookie = sess.open_session(TOKEN, client="10.0.0.5", label="Phone")
    assert outcome == ownerauth.OK
    who = sess.read(cookie)
    assert who["owner"] is True
    assert who["label"] == "Phone"


def test_ownership_is_not_a_stored_flag_that_could_be_forged():
    """There is no email to re-derive any more, and no flag in the cookie to
    tamper with: the only way to open a session is to present the credential,
    so existing IS the proof."""
    _, cookie = sess.open_session(TOKEN, client="c")
    raw = sess.SESSIONS.store.read_text(encoding="utf-8")
    assert "owner" not in raw, "ownership must not be a field somebody could edit"
    assert sess.read(cookie)["owner"] is True


def test_the_wrong_credential_yields_no_session():
    outcome, cookie = sess.open_session("not-the-credential", client="c")
    assert outcome == ownerauth.BAD_CREDENTIAL
    assert cookie == ""
    assert sess.sessions() == []


def test_garbage_cookies_are_none():
    for junk in (None, "", "not-a-token", "a.b.c"):
        assert sess.read(junk) is None


def test_revoking_ends_the_session():
    _, cookie = sess.open_session(TOKEN, client="c")
    assert sess.revoke(cookie) is True
    assert sess.read(cookie) is None


def test_revoke_all_ends_every_session():
    cookies = [sess.open_session(TOKEN, client=f"c{i}")[1] for i in range(3)]
    assert sess.revoke_all() == 3
    assert all(sess.read(c) is None for c in cookies)


def test_the_cookie_is_httponly_and_strict():
    kw = sess.cookie_kwargs(https=True)
    assert kw["httponly"] is True and kw["samesite"] == "strict"
    assert kw["secure"] is True
    assert sess.cookie_kwargs(https=False)["secure"] is False


def test_status_reports_the_lock_not_the_key():
    st = sess.status()
    assert st["authentication_configured"] is True
    assert TOKEN not in repr(st)


# ── the role ladder is unchanged ─────────────────────────────────────────────

def test_anonymous_when_no_session_and_not_loopback(monkeypatch):
    monkeypatch.setenv("ARIA_OWNER_TOKEN", "tok")
    assert policy.resolve_role({}, "203.0.113.9", None) == policy.ANON


def test_session_without_owner_flag_is_anonymous(monkeypatch):
    """No intermediate tier: a session that is not the owner's grants nothing."""
    monkeypatch.setenv("ARIA_OWNER_TOKEN", "tok")
    assert policy.resolve_role({}, "203.0.113.9", {"owner": False}) == policy.ANON


def test_session_with_owner_flag_is_owner(monkeypatch):
    monkeypatch.setenv("ARIA_OWNER_TOKEN", "tok")
    assert policy.resolve_role({}, "203.0.113.9", {"owner": True}) == policy.OWNER


def test_a_session_resolves_as_proven_ownership():
    """PROVEN, not inferred — the distinction `require_owner` turns on before
    letting anything reach a broker."""
    _, cookie = sess.open_session(TOKEN, client="c")
    role, basis = policy.resolve_role_with_basis(
        {}, "203.0.113.9", sess.read(cookie))
    assert role == policy.OWNER
    assert basis in policy.PROVEN_BASES


@pytest.mark.parametrize("path", [
    "/api/v5/analyse/AAPL", "/api/signals", "/api/chat/local",
    "/api/portfolio", "/api/vault/search", "/api/execute/queue",
])
def test_anonymous_gets_nothing(path):
    assert policy.is_allowed(path, policy.ANON) is False, path


@pytest.mark.parametrize("path", [
    "/health", "/api/auth/login", "/api/auth/logout",
    "/api/auth/status", "/api/auth/me",
])
def test_sign_in_routes_stay_public(path):
    assert policy.is_allowed(path, policy.ANON) is True, path


@pytest.mark.parametrize("gone", [
    "/api/auth/providers", "/api/auth/login/google",
    "/api/auth/callback/github", "/api/auth/users",
])
def test_the_removed_oauth_routes_are_still_covered_by_the_allowlist(gone):
    """They no longer exist. If one is ever re-added under /api/auth/ it would
    be PUBLIC by prefix, so this is a reminder that the prefix is the grant and
    a new route under it needs a reason."""
    assert policy.is_public(gone) is True


def test_oauth_is_gone_from_the_source_tree():
    """The brief asked for Google and GitHub sign-in to be removed, not merely
    hidden. A greyed-out button and an unreachable route are still a code path
    somebody can turn back on by accident."""
    root = Path(__file__).parent.parent
    assert not (root / "src" / "auth" / "oauth.py").exists()
    assert not (root / "frontend" / "src" / "auth" / "ownerToken.js").exists()


# ── the credential may be hashed ─────────────────────────────────────────────

def test_a_hashed_credential_works_on_the_header_path(monkeypatch):
    """`policy.resolve_role_with_basis` compared against the plaintext variable
    directly, so moving the credential to a hash silently stopped
    authenticating anybody on the break-glass path."""
    monkeypatch.delenv(sess.ENV_TOKEN, raising=False)
    monkeypatch.setenv(sess.ENV_TOKEN_HASH, ownerauth.hash_secret(TOKEN))
    role, basis = policy.resolve_role_with_basis(
        {"authorization": f"Bearer {TOKEN}"}, "203.0.113.9", None)
    assert role == policy.OWNER
    assert basis == policy.BASIS_TOKEN


def test_a_hashed_credential_still_counts_as_configured(monkeypatch):
    """`owner_is_configured()` gates the loopback fallback. If a hash did not
    count, setting one would leave inferred ownership fully active."""
    monkeypatch.delenv(sess.ENV_TOKEN, raising=False)
    monkeypatch.setenv(sess.ENV_TOKEN_HASH, ownerauth.hash_secret(TOKEN))
    assert policy.owner_is_configured() is True
    assert policy.resolve_role({}, "127.0.0.1", None) == policy.ANON


def test_owner_email_no_longer_configures_anything(monkeypatch):
    """A stale ARIA_OWNER_EMAIL in a .env must not satisfy the configuration
    check — it would leave the loopback fallback on while nothing could
    actually authenticate."""
    monkeypatch.delenv(sess.ENV_TOKEN, raising=False)
    monkeypatch.delenv(sess.ENV_TOKEN_HASH, raising=False)
    monkeypatch.setenv("ARIA_OWNER_EMAIL", "me@example.com")
    assert policy.owner_is_configured() is False


# ── the static interface ─────────────────────────────────────────────────────

def test_the_sign_in_page_itself_is_reachable_anonymously():
    """The built interface was owner-only, so an anonymous browser asking for
    /app/login got a raw 401 JSON body instead of a sign-in form. Behind a
    tunnel that meant nobody could ever sign in. The shell carries no data."""
    for path in ("/app", "/app/", "/app/login", "/app/assets/index.js",
                 "/app/manifest.webmanifest"):
        assert policy.is_allowed(path, policy.ANON), path


def test_opening_the_shell_opened_nothing_else():
    for path in ("/apple", "/application", "/api/portfolio", "/api/vault/x",
                 "/app-secrets", "/appendix"):
        assert not policy.is_allowed(path, policy.ANON), path


def test_a_refusal_points_at_the_route_that_exists():
    """It pointed at /api/auth/providers, which was removed with OAuth."""
    src = (Path(__file__).parent.parent / "backend" / "main.py").read_text(encoding="utf-8")
    assert "/api/auth/providers" not in src.split("def _role_guard", 1)[1].split("return _harden(await", 1)[0]
