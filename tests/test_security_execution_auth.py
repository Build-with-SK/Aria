"""
tests/test_security_execution_auth.py
=====================================
AUDIT FINDING 1 — "unauthenticated execution endpoints".

The claim under test is not "the endpoints I remembered to list are guarded".
It is "no route that can place, modify or cancel an order is reachable without
credentials" — so the suite discovers the routes from the running app's own
routing table rather than from a hand-written list. An execution endpoint added
next month is covered by these tests the day it is written.

Two callers are simulated, because they fail differently:

  * ANON over the network — a remote socket, no token, no cookie. Must get 401.
  * FREE — a signed-in non-owner. Must get 403. This is the one a middleware
    written as a blocklist gets wrong.
"""
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient          # noqa: E402

REMOTE = "203.0.113.9"       # TEST-NET-3: never this machine
TOKEN = "test-owner-token-do-not-ship"


@pytest.fixture(scope="module")
def app():
    # Import FIRST, then set the token: backend.main loads .env with
    # override=True, so anything set beforehand is overwritten by the real
    # deployment's values — including, on the owner's own machine, the real
    # owner token. Tests must never depend on (or be rescued by) that file.
    from backend.main import app as fastapi_app
    saved = {k: os.environ.get(k) for k in ("ARIA_OWNER_TOKEN", "ARIA_TRUST_PROXY")}
    os.environ["ARIA_OWNER_TOKEN"] = TOKEN
    os.environ.pop("ARIA_TRUST_PROXY", None)
    yield fastapi_app
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


@pytest.fixture(scope="module")
def anon(app):
    """A caller from the public internet with no credentials at all."""
    return TestClient(app, client=(REMOTE, 51234))


def execution_routes(app):
    """(method, path) for every route that can touch a broker, from the app."""
    from src.auth import policy
    out = []
    for route in app.routes:
        path = getattr(route, "path", "")
        if not policy.is_execution_path(path):
            continue
        for method in getattr(route, "methods", set()) or set():
            if method in ("HEAD", "OPTIONS"):
                continue
            out.append((method, path))
    return sorted(set(out))


def _concrete(path: str) -> str:
    """Fill path params with a value that cannot exist, so a leak shows up as a
    2xx/404 from the handler instead of a routing miss."""
    return path.replace("{trade_id}", "no-such-trade").replace(
        "{debate_id}", "no-such-debate")


# ── the inventory itself ─────────────────────────────────────────────────────

def test_execution_routes_are_discovered(app):
    """A guard test that silently matches zero routes proves nothing."""
    routes = execution_routes(app)
    assert len(routes) >= 20, routes
    paths = {p for _, p in routes}
    # The ones that actually move money, named so a rename cannot quietly empty
    # the inventory while the count still passes.
    for must in ("/api/execute/approve/{trade_id}",
                 "/api/execute/propose",
                 "/api/execute/cancel/{trade_id}",
                 "/api/desk/auto-execute",
                 "/api/desk/run-now"):
        assert must in paths, f"{must} missing from execution inventory"


def test_every_execution_route_declares_its_own_guard(app):
    """Structural: the guard is on the handler, not on a URL prefix. main.py
    refuses to import if this is violated — this asserts it directly too, so
    the failure names the route instead of a startup traceback."""
    from src.auth import policy
    unguarded = []
    for route in app.routes:
        path = getattr(route, "path", "")
        if not policy.is_execution_path(path):
            continue
        names = {getattr(getattr(d, "call", None), "__name__", "")
                 for d in getattr(getattr(route, "dependant", None),
                                  "dependencies", [])}
        if "require_owner" not in names:
            unguarded.append(path)
    assert not unguarded, f"execution routes without require_owner: {unguarded}"


# ── no credentials ───────────────────────────────────────────────────────────

def test_no_execution_endpoint_answers_an_anonymous_caller(app, anon):
    """THE finding. Every execution route, no credentials, from a remote IP."""
    leaked = []
    for method, path in execution_routes(app):
        r = anon.request(method, _concrete(path))
        if r.status_code not in (401, 403):
            leaked.append((method, path, r.status_code))
    assert not leaked, f"reachable without credentials: {leaked}"


def test_anonymous_caller_gets_401_not_403(app, anon):
    r = anon.post("/api/execute/approve/no-such-trade")
    assert r.status_code == 401


def test_signed_in_non_owner_is_refused(app):
    """A FREE user is authenticated but must still never reach execution.
    Blocklist-shaped auth passes the anonymous test and fails this one."""
    from src.auth import session as sess
    # A real signed cookie for somebody who is not ARIA_OWNER_EMAIL. `read()`
    # re-derives the owner flag from the email, so this cannot self-promote.
    cookie = sess.issue({"sub": "free-user-1", "provider": "google",
                         "email": "someone.else@example.com"})
    assert sess.read(cookie)["owner"] is False
    client = TestClient(app, client=(REMOTE, 51235))
    client.cookies.set(sess.COOKIE, cookie)
    leaked = []
    for method, path in execution_routes(app):
        r = client.request(method, _concrete(path))
        if r.status_code != 403:
            leaked.append((method, path, r.status_code))
    assert not leaked, f"free user reached execution: {leaked}"


# ── the ways in that are not the front door ──────────────────────────────────

def test_forwarded_for_cannot_forge_loopback_ownership(app):
    """X-Forwarded-For is written by the caller. When the app trusts proxy
    headers, claiming to be 127.0.0.1 must not confer ownership — and behind a
    proxy the loopback fallback is retired entirely, because the proxy's own
    socket IS loopback for every request it forwards."""
    from src.auth import policy
    token_backup = os.environ.pop("ARIA_OWNER_TOKEN", None)
    os.environ["ARIA_TRUST_PROXY"] = "1"
    try:
        # forged forwarded address + real remote socket
        role = policy.resolve_role({"x-forwarded-for": "127.0.0.1"},
                                   "127.0.0.1", None, peer_host=REMOTE)
        assert role == policy.ANON
        # the proxy itself connects from loopback, forwarding a remote user
        role = policy.resolve_role({"x-forwarded-for": REMOTE},
                                   REMOTE, None, peer_host="127.0.0.1")
        assert role == policy.ANON, "reverse proxy turns every caller into owner"
    finally:
        os.environ.pop("ARIA_TRUST_PROXY", None)
        if token_backup is not None:
            os.environ["ARIA_OWNER_TOKEN"] = token_backup


def test_testclient_is_not_a_privileged_hostname(app):
    """The default TestClient host used to sit in the loopback allowlist, which
    made every test an owner and made this whole file impossible to write."""
    from src.auth import policy
    assert "testclient" not in policy.LOOPBACK
    token_backup = os.environ.pop("ARIA_OWNER_TOKEN", None)
    try:
        assert policy.resolve_role({}, "testclient", None,
                                   peer_host="testclient") == policy.ANON
    finally:
        if token_backup is not None:
            os.environ["ARIA_OWNER_TOKEN"] = token_backup


def test_bad_token_is_not_a_partial_match(app):
    """Prefix of the real token, and the empty string, are both nobody."""
    from src.auth import policy
    for bad in ("", TOKEN[:-1], TOKEN + "x", TOKEN.upper()):
        assert policy.resolve_role({"x-aria-token": bad}, REMOTE, None,
                                   peer_host=REMOTE) == policy.ANON


def test_owner_token_still_works(app):
    """The guard must refuse strangers without locking the owner out."""
    client = TestClient(app, client=(REMOTE, 51236))
    r = client.get("/api/execute/queue", headers={"x-aria-token": TOKEN})
    assert r.status_code == 200


def test_every_state_changing_route_is_guarded_or_declared_public(app):
    """The generalised claim, checked over METHOD rather than path prefix.

    The first version of this inventory only examined routes already under
    /api/execute or /api/desk — the same path-prefix reasoning the guard exists
    to escape. An independent review pointed out that a write endpoint added
    under /api/v5 or /api/universe was therefore invisible to it, and found
    four such endpoints reachable by any signed-in free user, one of which
    moves the ensemble weights the desk sizes trades from.
    """
    from src.auth import policy
    from backend.main import _is_guarded
    unguarded = []
    for route in app.routes:
        methods = set(getattr(route, "methods", set()) or set())
        if not (methods & {"POST", "PATCH", "PUT", "DELETE"}):
            continue
        path = getattr(route, "path", "")
        if _is_guarded(route) or path in policy.PUBLIC_WRITE_ROUTES:
            continue
        unguarded.append(f"{sorted(methods)} {path}")
    assert not unguarded, (
        f"state-changing routes that are neither guarded nor declared public: "
        f"{unguarded}")


def test_a_broker_touching_get_under_a_free_prefix_is_caught(app):
    """A read-only broker endpoint added under /api/v5 would be covered by
    neither the method rule (it is a GET) nor the path rule (it is not under
    an execution prefix). The handler's own source is the third signal.

    Negative control: the check must FAIL on a route it should catch, or it is
    only asserting that today's routes happen to be fine.
    """
    from backend.main import _assert_execution_routes_guarded, app as real_app
    import backend.main as main_mod

    @real_app.get("/api/v5/__test_positions__")
    def _leaky():                                   # pragma: no cover
        mgr = main_mod._get_order_manager()
        return {"positions": mgr.get_all_positions() if mgr else []}

    try:
        with pytest.raises(RuntimeError) as e:
            _assert_execution_routes_guarded()
        assert "__test_positions__" in str(e.value)
    finally:
        real_app.router.routes = [r for r in real_app.router.routes
                                  if getattr(r, "path", "") != "/api/v5/__test_positions__"]
    # And the app is clean again once it is removed.
    _assert_execution_routes_guarded()


def test_the_four_writes_the_review_found_are_closed(app, anon):
    """Named individually so a regression names itself. None of these place an
    order, but all four mutate state the system trades or reports on."""
    for method, path in (("POST", "/api/v5/learning/resolve"),
                         ("POST", "/api/v5/learning/revert/1"),
                         ("POST", "/api/universe/refresh"),
                         ("POST", "/api/technical/snapshot"),
                         ("POST", "/api/quant/run-now")):
        r = anon.request(method, path)
        assert r.status_code in (401, 403), f"{method} {path} → {r.status_code}"


@pytest.mark.parametrize("env", [
    {},                                                  # nothing configured
    {"ARIA_OWNER_EMAIL": "owner@example.com"},           # OAuth configured
])
def test_loopback_ownership_cannot_reach_a_broker(app, monkeypatch, env):
    """The fail-open an independent review constructed: deploy behind a tunnel,
    and cloudflared connects from 127.0.0.1 — so every caller on earth looks
    like loopback and inherits OWNER.

    Parametrised because the first fix only covered the empty case. It asked
    "is an owner identity configured", which ARIA_OWNER_EMAIL satisfies while
    leaving the loopback fallback fully active — and the test written alongside
    it asserted that combination returns 200, pinning the bypass open as
    intended behaviour. Both rows must refuse: what matters is how THIS request
    proved ownership, not what exists in the environment.
    """
    monkeypatch.delenv("ARIA_OWNER_TOKEN", raising=False)
    monkeypatch.delenv("ARIA_OWNER_EMAIL", raising=False)
    monkeypatch.delenv("ARIA_TRUST_PROXY", raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    local = TestClient(app, client=("127.0.0.1", 51999))

    from src.auth import policy
    role, basis = policy.resolve_role_with_basis({}, "127.0.0.1", None,
                                                 peer_host="127.0.0.1")
    assert (role, basis) == (policy.OWNER, policy.BASIS_LOOPBACK)

    for method, path in (("POST", "/api/execute/propose"),
                         ("GET", "/api/execute/queue"),
                         ("POST", "/api/desk/auto-execute?enabled=true")):
        r = local.request(method, path, json={"ticker": "AAPL", "side": "buy",
                                              "qty": 1})
        assert r.status_code == 403, f"{method} {path} → {r.status_code}"
        assert "proven ownership" in r.text


def test_a_proven_owner_on_loopback_still_works(app, monkeypatch):
    """The local single-user workflow must survive — via proof, not address.
    A token (or a signed owner session) works from anywhere, loopback
    included."""
    monkeypatch.setenv("ARIA_OWNER_TOKEN", TOKEN)
    monkeypatch.delenv("ARIA_TRUST_PROXY", raising=False)
    local = TestClient(app, client=("127.0.0.1", 51998))
    # With a token configured the loopback fallback is off entirely, so an
    # unauthenticated local caller is anonymous — 401, "sign in", not 403.
    assert local.get("/api/execute/queue").status_code == 401
    assert local.get("/api/execute/queue",
                     headers={"x-aria-token": TOKEN}).status_code == 200


def test_a_signed_owner_session_is_proof(app, monkeypatch):
    """OAuth sign-in as the owner is the other proven basis, so configuring
    ARIA_OWNER_EMAIL and signing in genuinely does restore execution."""
    from src.auth import session as sess
    monkeypatch.setenv("ARIA_OWNER_EMAIL", "owner@example.com")
    monkeypatch.delenv("ARIA_OWNER_TOKEN", raising=False)
    cookie = sess.issue({"sub": "owner-1", "provider": "google",
                         "email": "owner@example.com"})
    assert sess.read(cookie)["owner"] is True
    client = TestClient(app, client=(REMOTE, 51996))
    client.cookies.set(sess.COOKIE, cookie)
    assert client.get("/api/execute/queue").status_code == 200


def test_a_proxied_request_retires_the_loopback_fallback(app):
    """The self-configuring half of the tunnel fix.

    Every protection keyed to ARIA_TRUST_PROXY inherits the same weakness: it
    waits to be told. A tunnelled request is not silent about being tunnelled —
    it carries CF-Connecting-IP or X-Forwarded-For, which a direct caller has
    no reason to send. So the app observes the thing it was waiting for.

    This is what closes the vault, the portfolio and the track record on a
    misconfigured tunnel, none of which the broker guard covers.
    """
    from src.auth import policy
    saved = dict(policy._PROXY_SEEN)
    token_backup = os.environ.pop("ARIA_OWNER_TOKEN", None)
    policy._PROXY_SEEN.update(detected=False, how="")
    try:
        local = TestClient(app, client=("127.0.0.1", 51994))
        # Direct localhost: the documented single-user workflow still works.
        assert policy.loopback_owner_enabled() is True
        assert local.get("/api/vault/status").status_code == 200

        # One request arrives with a proxy header. Something is in front of us.
        local.get("/health", headers={"CF-Connecting-IP": "203.0.113.5"})
        assert policy.proxy_detected()["detected"] is True
        assert policy.loopback_owner_enabled() is False

        # From here the vault and the owner's data are closed to a caller who
        # only *looks* local — which, behind a tunnel, is everyone.
        for path in ("/api/vault/status", "/api/portfolio", "/api/stats"):
            assert local.get(path).status_code in (401, 403), path
    finally:
        policy._PROXY_SEEN.update(saved)
        if token_backup is not None:
            os.environ["ARIA_OWNER_TOKEN"] = token_backup


def test_proxy_detection_latches(app):
    """One proxied request is enough — it does not re-open when the next
    request happens to arrive without the header."""
    from src.auth import policy
    saved = dict(policy._PROXY_SEEN)
    policy._PROXY_SEEN.update(detected=False, how="")
    try:
        assert policy.note_proxy_evidence({"x-forwarded-for": "1.2.3.4"}) is True
        assert policy.note_proxy_evidence({}) is True
        assert policy.loopback_owner_enabled() is False
    finally:
        policy._PROXY_SEEN.update(saved)


def test_the_startup_recheck_actually_runs(app):
    """Every other test builds a TestClient without a context manager, so
    Starlette never fires lifespan events and the startup re-check was never
    exercised. Entering the context manager runs it for real."""
    with TestClient(app, client=(REMOTE, 51995)) as client:
        assert client.get("/health").status_code in (200, 401, 403)


def test_the_guard_denies_without_the_middleware():
    """The two layers must be genuinely separable. This mounts the dependency
    on a bare app with no middleware at all — if it only worked because
    _role_guard ran first, the fix is one layer wearing two hats."""
    from fastapi import Depends, FastAPI
    from src.auth.guard import require_owner

    bare = FastAPI()

    @bare.post("/danger", dependencies=[Depends(require_owner)])
    def danger():
        return {"ok": True}

    saved = os.environ.get("ARIA_OWNER_TOKEN")
    os.environ["ARIA_OWNER_TOKEN"] = TOKEN
    try:
        client = TestClient(bare, client=(REMOTE, 51997))
        assert client.post("/danger").status_code == 401
        assert client.post("/danger",
                           headers={"x-aria-token": TOKEN}).status_code == 200
    finally:
        if saved is None:
            os.environ.pop("ARIA_OWNER_TOKEN", None)
        else:
            os.environ["ARIA_OWNER_TOKEN"] = saved


def test_expensive_writes_outside_the_execution_prefixes_are_guarded_too(app, anon):
    """The path-prefix inventory covers /api/execute and /api/desk. Routes that
    live under a FREE prefix but still write — the research loop trigger writes
    the prediction log the track record is computed from, and costs minutes of
    CPU — must carry the guard explicitly."""
    r = anon.post("/api/v5/loop/run")
    assert r.status_code in (401, 403)


def test_guard_does_not_read_request_state(app):
    """require_owner re-derives the role instead of trusting middleware state,
    so removing or reordering the middleware cannot silently open the routes."""
    from src.auth import guard
    # Bytecode, not source: a comment explaining why it must not read
    # request.state should not fail the test that enforces it.
    for fn in (guard.require_owner, guard.resolve):
        assert "state" not in fn.__code__.co_names, (
            f"{fn.__name__} reads request.state — the guard would inherit "
            f"whatever the middleware decided, or nothing at all if it is "
            f"bypassed")
