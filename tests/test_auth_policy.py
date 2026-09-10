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
    assert policy.resolve_role({}, "127.0.0.1") == policy.OWNER


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
def test_free_user_is_denied(path):
    assert policy.is_allowed(path, policy.FREE) is False, path


@pytest.mark.parametrize("path", OWNER_ONLY)
def test_owner_is_allowed(path):
    assert policy.is_allowed(path, policy.OWNER) is True, path


# ── what free users legitimately get ─────────────────────────────────────────

FREE_OK = [
    "/health",
    "/api/v5/analyse/AAPL", "/api/v5/modules",
    "/api/universe/search", "/api/universe/news/AAPL",
    "/api/quote/live/AAPL",
    "/api/technical/AAPL", "/api/signals", "/api/macro",
    "/api/quant/strategy/iron_condor", "/api/summary",
    "/api/chat/local",
]


@pytest.mark.parametrize("path", FREE_OK)
def test_free_user_gets_research(path):
    assert policy.is_allowed(path, policy.FREE) is True, path


def test_chat_local_is_not_shadowed_by_chat():
    """/api/chat is owner-only and /api/chat/local is public. Prefix matching
    must not let the shorter, denied path swallow the longer, allowed one."""
    assert policy.is_allowed("/api/chat", policy.FREE) is False
    assert policy.is_allowed("/api/chat/local", policy.FREE) is True


def test_unknown_paths_default_to_deny():
    """The whole point of an allowlist: a route added tomorrow is invisible to
    free users until someone names it."""
    for path in ("/api/brand-new-feature", "/api/admin", "/", "/api/v6/secret"):
        assert policy.is_allowed(path, policy.FREE) is False, path


# ── the vault gate ───────────────────────────────────────────────────────────

def test_only_owner_reads_the_vault():
    assert policy.can_read_vault(policy.OWNER) is True
    assert policy.can_read_vault(policy.FREE) is False
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
    sign_in_flow = {"/api/auth/providers", "/api/auth/me", "/api/auth/logout",
                    "/api/auth/login/{provider}", "/api/auth/callback/{provider}"}
    for path, route in anon_reachable:
        if path in sign_in_flow:
            continue
        assert _is_owner_guarded(route), (
            f"{path} sits under the open /api/auth/ prefix and carries no "
            f"require_owner — the prefix is the door, not the lock")


def test_the_user_record_is_owner_only_despite_the_open_auth_prefix():
    """Named explicitly because it is the one that would hurt: the user list is
    personal data, and it lives under the prefix that has to stay open."""
    import backend.main as m
    for path in ("/api/auth/users", "/api/auth/users/{key:path}"):
        routes = [r for r in m.app.routes if getattr(r, "path", "") == path]
        assert routes, f"{path} is no longer registered"
        for r in routes:
            assert _is_owner_guarded(r), f"{path} lost its owner guard"


def test_every_free_reachable_endpoint_is_deliberately_public():
    """A route becoming free-readable must be a decision, not a side effect.

    This list is the record of that decision. Adding an endpoint under an
    existing FREE prefix silently widens public access — which is exactly how
    an API ends up exposing something it did not mean to — so the new path has
    to be named here before the suite goes green again.
    """
    deliberately_public = {
        "/api/v5/", "/api/universe/", "/api/quote/", "/api/technical/",
        "/api/signals", "/api/macro", "/api/futures", "/api/options",
        "/api/derivatives", "/api/sentiment", "/api/history", "/api/lse",
        "/api/nexus", "/api/quant/", "/api/backtest", "/api/ml",
        "/api/summary", "/api/chat/local", "/api/brain/pulse",
        "/api/system/health", "/api/auth/",
    }
    surprises = []
    for path, concrete, _ in _app_routes():
        if not policy.is_allowed(concrete, policy.FREE):
            continue
        if not any(path.startswith(p) for p in deliberately_public):
            surprises.append(path)
    assert not surprises, (
        f"these endpoints are readable by any signed-in user and are not on "
        f"the deliberate list: {surprises}")


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
        for role in (policy.ANON, policy.FREE):
            assert policy.is_allowed(concrete, role) is False, (
                f"{path} is reachable by {role}")


def test_the_brain_aggregate_never_leaks_below_owner():
    """/api/brain/pulse is deliberately public; /api/brain is not.

    They differ by everything that matters — the aggregate carries the world
    model, memory counts and the vault-backed cognition summary.
    """
    assert policy.is_allowed("/api/brain/pulse", policy.FREE) is True
    for path in ("/api/brain", "/api/brain/activity", "/api/brain/memory"):
        assert policy.is_allowed(path, policy.FREE) is False, path
