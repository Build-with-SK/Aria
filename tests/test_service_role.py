"""
tests/test_service_role.py
==========================
SENTINEL's scoped identity, and the privileges it must never inherit.

THE DEFECT BEING PINNED
-----------------------
SENTINEL held `ARIA_OWNER_TOKEN`. That credential opens every owner-only route
in this application, including `POST /api/execute/approve/{trade_id}` and
`POST /api/execute/kill-switch/release`. SENTINEL did not call them — but "does
not" is a fact about today's code, and a credential's reach is a fact about the
system. These tests assert the boundary rather than the habit.

The sweep at the bottom is the one that matters most. Listing routes by hand
proves what somebody remembered; walking the registered routes proves that a
route added next year is refused by default.
"""
from __future__ import annotations

import pytest

from src.auth import policy

OWNER_TOKEN = "owner-token-for-tests-only"
SERVICE_TOKEN = "service-token-for-tests-only"


class _Headers(dict):
    """Case-insensitive, like the real header mapping."""
    def get(self, key, default=None):
        for k, v in self.items():
            if k.lower() == str(key).lower():
                return v
        return default


@pytest.fixture
def tokens(monkeypatch):
    monkeypatch.setenv("ARIA_OWNER_TOKEN", OWNER_TOKEN)
    monkeypatch.setenv("ARIA_SERVICE_TOKEN", SERVICE_TOKEN)
    return None


def _role(token: str):
    return policy.resolve_role_with_basis(
        _Headers({"Authorization": f"Bearer {token}"}), "10.0.0.9",
        peer_host="10.0.0.9")


# ── identity ─────────────────────────────────────────────────────────────

def test_the_service_token_resolves_to_the_service_role(tokens):
    role, basis = _role(SERVICE_TOKEN)
    assert role == policy.SERVICE
    assert basis == policy.BASIS_SERVICE


def test_the_owner_token_still_resolves_to_owner(tokens):
    assert _role(OWNER_TOKEN) == (policy.OWNER, policy.BASIS_TOKEN)


def test_the_service_basis_is_not_proof_of_ownership(tokens):
    """`require_owner` admits only PROVEN_BASES. The service basis must not be
    in it — belt and braces alongside the role check itself."""
    assert policy.BASIS_SERVICE not in policy.PROVEN_BASES


def test_a_service_token_equal_to_the_owner_token_is_refused(monkeypatch):
    """A scoped credential that equals the owner's is not scoped. Rather than
    silently granting owner rights under a service name, the service role is
    disabled and the caller resolves as owner (which is what the token IS)."""
    monkeypatch.setenv("ARIA_OWNER_TOKEN", OWNER_TOKEN)
    monkeypatch.setenv("ARIA_SERVICE_TOKEN", OWNER_TOKEN)
    assert policy.service_token_is_distinct() is False
    assert _role(OWNER_TOKEN)[0] == policy.OWNER


def test_no_service_token_configured_means_no_service_role(monkeypatch):
    monkeypatch.setenv("ARIA_OWNER_TOKEN", OWNER_TOKEN)
    monkeypatch.delenv("ARIA_SERVICE_TOKEN", raising=False)
    assert _role(SERVICE_TOKEN)[0] == policy.ANON


# ── the scope ────────────────────────────────────────────────────────────

def test_the_granted_scope_is_exactly_what_sentinel_calls():
    """If this fails, someone widened SENTINEL's reach. That should be a
    deliberate act with a reason, not a diff nobody noticed."""
    # Widened ONCE, deliberately, in the intelligence work: the structured
    # specialist consultation, which runs on the same no-vault, no-intent path
    # as /api/chat/local and returns the data's age and sources with the answer.
    assert policy.SERVICE_ROUTES == frozenset({
        ("GET", "/health"),
        ("GET", "/api/summary"),
        ("GET", "/api/brain/status"),
        ("POST", "/api/chat/local"),
        ("POST", "/api/specialist/analysis"),
    })


@pytest.mark.parametrize("method,path", sorted(policy.SERVICE_ROUTES))
def test_every_granted_route_is_allowed(method, path):
    assert policy.is_allowed(path, policy.SERVICE, method) is True


def test_the_scope_is_method_qualified():
    """Reading the brain's status and posting to it are different grants."""
    assert policy.is_allowed("/api/brain/status", policy.SERVICE, "GET") is True
    assert policy.is_allowed("/api/brain/status", policy.SERVICE, "POST") is False


def test_the_scope_does_not_match_by_prefix():
    """`/api/execute/queue` and `/api/execute/approve/x` share a prefix; one is
    a read and the other moves money."""
    assert policy.is_allowed("/api/summary/secret", policy.SERVICE, "GET") is False
    assert policy.is_allowed("/api/chat/local/x", policy.SERVICE, "POST") is False


def test_the_cloud_chat_route_is_not_granted():
    """`/api/chat` records a held trade intent when the message parses as an
    order. A consultation peer must not be able to put an intent in the owner's
    book by phrasing a question as 'should I buy 20 AAPL'."""
    assert policy.is_allowed("/api/chat", policy.SERVICE, "POST") is False


# ── what it must never reach ─────────────────────────────────────────────

FORBIDDEN = [
    ("POST", "/api/execute/approve/abc123"),
    ("POST", "/api/execute/reject/abc123"),
    ("POST", "/api/execute/cancel/abc123"),
    ("POST", "/api/execute/propose"),
    ("GET", "/api/execute/queue"),
    ("GET", "/api/execute/positions"),
    ("POST", "/api/execute/kill-switch/release"),
    ("POST", "/api/execute/kill-switch/engage"),
    ("GET", "/api/vault/search"),
    ("GET", "/api/vault/note"),
    ("POST", "/api/vault/reindex"),
    ("GET", "/api/portfolio"),
    ("GET", "/api/portfolio/holdings"),
    ("GET", "/api/desk/intents"),
    ("POST", "/api/desk/auto-execute"),
    ("GET", "/api/brain/memories"),
    ("GET", "/api/brain/recall"),
    ("GET", "/api/fx/gbpinr"),
    ("POST", "/api/run"),
    ("GET", "/api/auth/users"),
]


@pytest.mark.parametrize("method,path", FORBIDDEN)
def test_the_service_role_cannot_reach_sensitive_routes(method, path):
    assert policy.is_allowed(path, policy.SERVICE, method) is False, path


def test_the_service_role_cannot_reach_any_execution_path():
    for method in ("GET", "POST", "DELETE"):
        for path in ("/api/execute", "/api/execute/queue", "/api/desk",
                     "/api/desk/run-now"):
            assert policy.is_allowed(path, policy.SERVICE, method) is False


def test_the_service_role_is_not_granted_the_free_research_surface():
    """Scoped means scoped. The service peer is not 'a free user plus extras' —
    it is four pairs, so reading its grant means reading one list."""
    assert policy.is_allowed("/api/v5/modules", policy.SERVICE, "GET") is False
    assert policy.is_allowed("/api/universe/search", policy.SERVICE, "GET") is False


def test_every_registered_execution_route_is_refused_to_the_service_role():
    """The sweep. Walks the real app rather than a list somebody maintained."""
    from backend.main import app
    checked = 0
    for route in app.routes:
        path = getattr(route, "path", "")
        if not policy.is_execution_path(path):
            continue
        for method in (getattr(route, "methods", set()) or {"GET"}):
            assert policy.is_allowed(path, policy.SERVICE, method) is False, (
                f"service role reaches execution route {method} {path}")
            checked += 1
    assert checked >= 10, f"swept only {checked} execution routes"


def test_the_service_role_reaches_almost_nothing_of_the_whole_app():
    """A blast-radius measurement, not a style check. The point of the change is
    that the credential's reach is small and countable."""
    from backend.main import app
    reachable = set()
    for route in app.routes:
        path = getattr(route, "path", "")
        for method in (getattr(route, "methods", set()) or {"GET"}):
            if policy.is_allowed(path, policy.SERVICE, method):
                reachable.add((method, path))
    assert reachable <= policy.SERVICE_ROUTES, (
        f"service role reaches routes outside its declared scope: "
        f"{reachable - policy.SERVICE_ROUTES}")


# ── existing roles are unchanged ─────────────────────────────────────────

def test_adding_the_service_role_did_not_widen_the_free_role():
    assert policy.is_allowed("/api/execute/queue", policy.ANON, "GET") is False
    assert policy.is_allowed("/api/vault/search", policy.ANON, "GET") is False
    assert policy.is_allowed("/api/chat", policy.ANON, "POST") is False
    assert policy.is_allowed("/api/chat/local", policy.ANON, "POST") is False


def test_adding_the_service_role_did_not_widen_the_anonymous_role():
    for path in ("/api/summary", "/api/brain/status", "/api/chat/local"):
        assert policy.is_allowed(path, policy.ANON, "GET") is False


def test_the_owner_is_still_allowed_everything():
    for method, path in FORBIDDEN:
        assert policy.is_allowed(path, policy.OWNER, method) is True


# ── over HTTP, through the real middleware and the real guard ────────────
#
# The tests above assert the policy. These assert the APPLICATION, because a
# correct policy consulted by nobody protects nothing — and the money routes
# carry a second, independent control (`require_owner`) whose interaction with
# a new role is exactly the kind of thing that is assumed rather than checked.

@pytest.fixture
def http(tokens):
    from fastapi.testclient import TestClient

    from backend.main import app
    return TestClient(app)


def _svc() -> dict:
    return {"Authorization": f"Bearer {SERVICE_TOKEN}"}


@pytest.mark.parametrize("path", [
    "/api/execute/approve/abc123",
    "/api/execute/cancel/abc123",
    "/api/execute/kill-switch/release",
])
def test_over_http_the_service_token_cannot_touch_a_broker(http, path):
    r = http.post(path, headers=_svc())
    assert r.status_code in (401, 403), f"{path} answered {r.status_code}"


def test_over_http_the_service_token_cannot_read_the_vault(http):
    r = http.get("/api/vault/search?q=anything", headers=_svc())
    assert r.status_code in (401, 403)


def test_over_http_the_service_token_cannot_read_the_trade_queue(http):
    """Documented consequence: SENTINEL loses the 'trades waiting' signal.

    Kept as an assertion rather than a comment because the tempting fix — let
    the service role past `require_owner` for this one read — would weaken the
    guard that stands on every order-placing route.
    """
    r = http.get("/api/execute/queue", headers=_svc())
    assert r.status_code in (401, 403)


def test_over_http_a_granted_route_is_reachable(http):
    """The scope has to actually work, or the migration just breaks SENTINEL."""
    r = http.get("/api/brain/status", headers=_svc())
    assert r.status_code == 200


def test_over_http_no_credential_is_still_refused(http):
    assert http.get("/api/brain/status").status_code in (401, 403)


# ── the four credentials are four values ─────────────────────────────────────

def test_a_service_token_equal_to_sentinels_is_refused(monkeypatch):
    """One value for both directions of the bridge is one leak for two doors."""
    monkeypatch.setenv("ARIA_OWNER_TOKEN", "owner-value-0123456789")
    monkeypatch.setenv("ARIA_SERVICE_TOKEN", "shared-value-0123456789")
    monkeypatch.setenv("SENTINEL_SERVICE_TOKEN", "shared-value-0123456789")
    assert policy.service_token_is_distinct() is False
    role = policy.resolve_role({"authorization": "Bearer shared-value-0123456789"},
                               "203.0.113.9", None)
    assert role == policy.ANON


def test_the_client_refuses_a_sentinel_token_equal_to_arias_own(monkeypatch):
    from src.consult import sentinel_client
    monkeypatch.setenv("ARIA_SERVICE_TOKEN", "same-value-0123456789")
    monkeypatch.setenv("SENTINEL_SERVICE_TOKEN", "same-value-0123456789")
    assert sentinel_client._token() == ""


def test_the_new_name_is_read_first_and_the_old_still_works(monkeypatch):
    from src.consult import sentinel_client
    monkeypatch.delenv("ARIA_SERVICE_TOKEN", raising=False)
    monkeypatch.setenv("SENTINEL_CONSULT_TOKEN", "old-name-value-0123456789")
    monkeypatch.delenv("SENTINEL_SERVICE_TOKEN", raising=False)
    assert sentinel_client._token() == "old-name-value-0123456789"
    monkeypatch.setenv("SENTINEL_SERVICE_TOKEN", "new-name-value-0123456789")
    assert sentinel_client._token() == "new-name-value-0123456789"
