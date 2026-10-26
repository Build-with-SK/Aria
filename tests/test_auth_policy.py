"""
Access policy: who may reach what.

These tests are deliberately blunt about the three things that are never a
tier. If one of them ever starts passing for a free user, that is a disclosure
of the owner's notes, positions or broker — not a failing assertion.
"""
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.auth import policy  # noqa: E402


# ── role resolution ──────────────────────────────────────────────────────────

def test_loopback_is_owner_when_no_token_configured(monkeypatch):
    monkeypatch.delenv("ARIA_OWNER_TOKEN", raising=False)
    assert policy.resolve_role({"host": "127.0.0.1"}, "127.0.0.1") == policy.OWNER


def test_remote_without_a_session_is_anonymous(monkeypatch):
    """No session and not loopback means not signed in — and anonymous callers
    get nothing but the sign-in routes."""
    monkeypatch.delenv("ARIA_OWNER_TOKEN", raising=False)
    assert policy.resolve_role({}, "192.168.1.55") == policy.ANON


def test_token_beats_loopback(monkeypatch):
    """Once a token exists, being on localhost is not enough — otherwise
    anything running on the mini itself would inherit owner rights."""
    monkeypatch.setenv("ARIA_OWNER_TOKEN", "s3cret")
    assert policy.resolve_role({}, "127.0.0.1") == policy.ANON
    hdr = {"authorization": "Bearer s3cret"}
    assert policy.resolve_role(hdr, "10.0.0.9") == policy.OWNER


def test_wrong_and_partial_tokens_get_no_access(monkeypatch):
    monkeypatch.setenv("ARIA_OWNER_TOKEN", "s3cret")
    # A prefix, a case variant and a mangled scheme must all fail. Case matters
    # especially: tokens are compared as bytes, not case-folded. With no session
    # to fall back on, a failed token is anonymous rather than demoted to free.
    for bad in ("", "s3cre", "s3crett", "S3CRET", "bearer s3cret"):
        assert policy.resolve_role({"x-aria-token": bad}, "1.2.3.4") == policy.ANON


def test_surrounding_whitespace_is_trimmed_not_rejected(monkeypatch):
    """HTTP permits optional whitespace around a header value (RFC 7230 OWS),
    so " s3cret " IS the token s3cret. Rejecting it would fail legitimate
    clients without excluding any attacker — anyone who has the padded string
    already has the token."""
    monkeypatch.setenv("ARIA_OWNER_TOKEN", "s3cret")
    assert policy.resolve_role({"x-aria-token": "  s3cret  "}, "1.2.3.4") == policy.OWNER


def test_x_aria_token_header_works(monkeypatch):
    monkeypatch.setenv("ARIA_OWNER_TOKEN", "s3cret")
    assert policy.resolve_role({"x-aria-token": "s3cret"}, "1.2.3.4") == policy.OWNER


# ── the three that are never a tier ──────────────────────────────────────────

OWNER_ONLY = [
    "/api/vault/search", "/api/vault/note", "/api/vault/reindex",
    "/api/execute/approve/abc123", "/api/execute/propose", "/api/execute/queue",
    "/api/portfolio", "/api/portfolio/summary",
    "/api/desk/auto-execute", "/api/desk/state", "/api/desk/debates",
    "/api/brain/status", "/api/brain/consult", "/api/brain/start",
    "/api/fx/gbpinr", "/api/fx/gbpinr/targets",
    "/api/chat",
    "/api/report", "/api/performance", "/api/stats", "/api/run",
    "/api/inference/models",
]


@pytest.mark.parametrize("path", OWNER_ONLY)
def test_nobody_but_the_owner_reaches_owner_routes(path):
    """There is no second human role any more (the FREE tier went with OAuth),
    so the only non-owner callers are anonymous ones and the SENTINEL peer."""
    assert policy.is_allowed(path, policy.ANON) is False, path
    if ("GET", path) not in policy.SERVICE_ROUTES:     # SENTINEL's five, by name
        assert policy.is_allowed(path, policy.SERVICE, "GET") is False, path


@pytest.mark.parametrize("path", OWNER_ONLY)
def test_owner_is_allowed(path):
    assert policy.is_allowed(path, policy.OWNER) is True, path


# ── the roles that exist ─────────────────────────────────────────────────────

def test_there_are_exactly_three_roles():
    """Owner, the SENTINEL service peer, and anonymous. The FREE role existed
    for other people signing in with Google or GitHub; both were removed at the
    owner's request, and a role nothing can be granted is dead code with a
    security-shaped name."""
    assert not hasattr(policy, "FREE")
    assert not hasattr(policy, "FREE_PREFIXES")
    assert {policy.OWNER, policy.SERVICE, policy.ANON} == {"owner", "service", "anon"}


def test_a_session_that_is_not_the_owners_is_anonymous():
    """Nothing mints one, but if a record ever read as non-owner it must fall
    to the least privilege, not to some intermediate tier."""
    assert policy.resolve_role({}, "203.0.113.9", {"owner": False}) == policy.ANON


def test_unknown_paths_default_to_deny():
    """The whole point of an allowlist: a route added tomorrow is invisible to
    anybody but the owner until someone names it."""
    for path in ("/api/brand-new-feature", "/api/admin", "/api/v6/secret"):
        assert policy.is_allowed(path, policy.ANON) is False, path
        assert policy.is_allowed(path, policy.OWNER) is True, path


# ── the vault gate ───────────────────────────────────────────────────────────

def test_only_owner_reads_the_vault():
    assert policy.can_read_vault(policy.OWNER) is True
    assert policy.can_read_vault(policy.SERVICE) is False
    assert policy.can_read_vault(policy.ANON) is False
    for junk in ("", None, "admin", "OWNER", "owner "):
        assert policy.can_read_vault(junk) is False, junk


# ── every route the app actually exposes, checked against the allowlist ─────
#
# The tests above check a hand-written list of paths, which protects the paths
# somebody remembered to add. This one enumerates the ROUTER, so a new endpoint
# is covered the moment it is registered — including one added by a future
# session that never reads this file.

def _app_routes():
    import backend.main as m
    out = []
    for r in m.app.routes:
        path = getattr(r, "path", "")
        if not path.startswith("/api/"):
            continue
        # Path params never match a literal prefix; substitute something inert.
        concrete = re.sub(r"\{[^}]+\}", "X", path)
        out.append((path, concrete, r))
    return out


def _is_owner_guarded(route) -> bool:
    names = {getattr(getattr(d, "call", None), "__name__", "")
             for d in getattr(getattr(route, "dependant", None), "dependencies", [])}
    return "require_owner" in names


def test_the_only_anon_reachable_api_is_the_sign_in_flow():
    """Anon must reach the login endpoints and nothing else.

    `/api/auth/` has to be open or nobody could ever sign in — you cannot put
    the door behind the lock. So the allowlist opens the whole prefix, and the
    sensitive routes INSIDE it are protected at the route level instead. Both
    halves of that arrangement are asserted here, because the prefix on its own
    would happily expose the user list.
    """
    anon_reachable = [(p, r) for p, concrete, r in _app_routes()
                      if policy.is_allowed(concrete, policy.ANON)]
    stray = [p for p, _ in anon_reachable if not p.startswith("/api/auth/")]
    assert not stray, f"anonymous callers can reach {stray}"

    # Of the auth routes, only the sign-in flow itself may be unguarded.
    #
    # The list shrank when Google, GitHub and Apple were removed: the provider
    # redirect and its callback are gone, and one credential endpoint replaced
    # them. `/api/auth/status` joined it so an uninitialised server can say so
    # rather than rejecting every attempt as a wrong credential.
    sign_in_flow = {"/api/auth/login", "/api/auth/logout",
                    "/api/auth/me", "/api/auth/status"}
    for path, route in anon_reachable:
        if path in sign_in_flow:
            continue
        assert _is_owner_guarded(route), (
            f"{path} sits under the open /api/auth/ prefix and carries no "
            f"require_owner — the prefix is the door, not the lock")


def test_the_sensitive_auth_routes_are_owner_only_despite_the_open_prefix():
    """Named explicitly because these are the ones that would hurt.

    This used to be about `/api/auth/users`, the record of everybody who had
    signed in via a provider. Both the route and `src/auth/users.py` were
    removed with OAuth: with no provider there are no other users, so the file
    was an orphan holding personal data and nothing wrote to it.

    What replaced it under the same open prefix is the session list, which says
    which devices are signed in, and the sign-out-everywhere control. Neither
    may be reachable without the owner guard: one is operational detail about
    the owner's machines, and the other could sign him out of it.
    """
    import backend.main as m
    for path in ("/api/auth/sessions", "/api/auth/logout-everywhere"):
        routes = [r for r in m.app.routes if getattr(r, "path", "") == path]
        assert routes, f"{path} is no longer registered"
        for r in routes:
            assert _is_owner_guarded(r), f"{path} lost its owner guard"


def test_the_removed_user_record_is_really_gone():
    """A module nothing imports is still a module somebody can import."""
    root = Path(__file__).parent.parent
    assert not (root / "src" / "auth" / "users.py").exists()
    import backend.main as m
    paths = {getattr(r, "path", "") for r in m.app.routes}
    assert not any(p.startswith("/api/auth/users") for p in paths)


def test_every_anonymously_reachable_route_is_deliberate():
    """With no second human role, the only non-owner surface is the anonymous
    one — and it must stay exactly: sign-in, liveness, readiness, the UI shell.
    Adding to it has to be a decision recorded here."""
    deliberately_public = {"/api/auth/", "/health", "/ready", "/app"}
    surprises = []
    for path, concrete, _ in _app_routes():
        if not policy.is_allowed(concrete, policy.ANON):
            continue
        if not any(path.startswith(p) for p in deliberately_public):
            surprises.append(path)
    assert not surprises, f"reachable without signing in: {surprises}"


def test_the_owners_private_surfaces_are_owner_only():
    """Vault, portfolio, execution, ledger and brain internals are not a tier.

    A misconfigured quota is an annoyance. A misconfigured vault flag publishes
    somebody's personal notes.
    """
    private = ("/api/vault/", "/api/portfolio", "/api/execute/", "/api/desk/",
               "/api/ledger/", "/api/aria/", "/api/brain/memory",
               "/api/brain/memories", "/api/brain/recall",
               "/api/brain/last-cycle", "/api/daily-report", "/api/world",
               "/api/news/", "/api/research/")
    for path, concrete, _ in _app_routes():
        if not any(path.startswith(p) for p in private):
            continue
        for role in (policy.ANON, policy.SERVICE):
            assert policy.is_allowed(concrete, role) is False, (
                f"{path} is reachable by {role}")


def test_the_brain_is_owner_only():
    """/api/brain/pulse was public to signed-in non-owners. There are none now,
    so the brain — pulse included — is the owner's, and SENTINEL reaches only
    /api/brain/status through its own allowlist."""
    for path in ("/api/brain/pulse", "/api/brain", "/api/brain/activity",
                 "/api/brain/memory"):
        assert policy.is_allowed(path, policy.ANON) is False, path
    assert policy.is_allowed("/api/brain/status", policy.SERVICE, "GET") is True
    assert policy.is_allowed("/api/brain/pulse", policy.SERVICE, "GET") is False

