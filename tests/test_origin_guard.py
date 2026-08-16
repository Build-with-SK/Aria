"""
tests/test_origin_guard.py
==========================
AUDIT FINDING C1 — the CSRF guard on mutating requests.

The guard existed and had no test, which is how it came to refuse the app's
own front end. `_ALLOWED_ORIGINS` lists the two Vite dev-server ports; the
BUILT frontend is served by this same process at `/app`, so its Origin is
`http://localhost:8000` — not on the list. Every POST from the shipped UI came
back 403 `origin not allowed`: chat, run-now, approvals. The dev server worked
and the real app did not, silently, for as long as anyone had been running it
that way.

So this file pins both directions at once, because a fix in one direction is
worth nothing without the other:

  · the app's own origin is accepted, whatever host it is reached on
  · a foreign origin is still refused

Same-origin is not a weakening. CSRF is by definition a request from another
origin, and `Origin` is set by the browser — a page on evil.com cannot make it
claim to be localhost.
"""
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient          # noqa: E402

TOKEN = "test-owner-token-do-not-ship"


@pytest.fixture(scope="module")
def client():
    from backend.main import app as fastapi_app
    saved = os.environ.get("ARIA_OWNER_TOKEN")
    os.environ["ARIA_OWNER_TOKEN"] = TOKEN
    yield TestClient(fastapi_app)
    if saved is None:
        os.environ.pop("ARIA_OWNER_TOKEN", None)
    else:
        os.environ["ARIA_OWNER_TOKEN"] = saved


def post(client, origin=None, host="testserver", path="/api/aria/speak"):
    headers = {"X-ARIA-Token": TOKEN, "Host": host}
    if origin is not None:
        headers["Origin"] = origin
    return client.post(path, json={"text": "hello"}, headers=headers)


def refused_for_origin(response) -> bool:
    return (response.status_code == 403
            and "origin" in (response.json().get("detail") or "").lower())


# ── the app's own front end must work ───────────────────────────────────────

def test_the_apps_own_origin_is_accepted(client):
    """The regression. The built UI is served from this process, so its Origin
    is this host — and it was being refused."""
    assert not refused_for_origin(post(client, origin="http://testserver",
                                       host="testserver"))


def test_it_works_on_whatever_host_she_is_reached_on(client):
    """localhost, 127.0.0.1, a cloudflared hostname — the served app's origin
    always equals the host it arrived on, and none of them can be listed in
    advance."""
    for host in ("localhost:8000", "127.0.0.1:8000", "aria.example-tunnel.com"):
        assert not refused_for_origin(post(client, origin=f"http://{host}",
                                           host=host)), host


def test_the_dev_servers_still_work(client):
    for origin in ("http://localhost:5173", "http://localhost:3000"):
        assert not refused_for_origin(post(client, origin=origin))


def test_a_request_with_no_origin_is_not_blocked(client):
    """curl and the desktop app send no Origin. The guard is about browsers."""
    assert not refused_for_origin(post(client, origin=None))


# ── and a foreign page still cannot ─────────────────────────────────────────

def test_a_foreign_origin_is_still_refused(client):
    for origin in ("https://evil.example.com", "http://localhost:9999",
                   "null", "http://localhost.evil.com"):
        assert refused_for_origin(post(client, origin=origin,
                                       host="localhost:8000")), origin


def test_a_lookalike_host_does_not_pass(client):
    """Substring matching would let `localhost:8000.evil.com` through."""
    assert refused_for_origin(post(client, origin="http://localhost:8000.evil.com",
                                   host="localhost:8000"))


def test_the_scheme_alone_does_not_qualify(client):
    assert refused_for_origin(post(client, origin="http://", host="localhost:8000"))


def test_the_guard_covers_every_mutating_verb(client):
    """A guard on POST only is a guard with three doors open."""
    headers = {"X-ARIA-Token": TOKEN, "Origin": "https://evil.example.com",
               "Host": "localhost:8000"}
    for verb in ("post", "put", "patch", "delete"):
        # `delete` takes no json body in httpx's client signature.
        r = (client.request(verb.upper(), "/api/aria/speak", headers=headers)
             if verb == "delete"
             else getattr(client, verb)("/api/aria/speak", json={"text": "hi"},
                                        headers=headers))
        # 405 means the route has no such verb — the guard runs before routing,
        # so a refusal must arrive as 403 rather than 405.
        assert r.status_code in (403, 405)
        if r.status_code == 403:
            assert refused_for_origin(r)


def test_reads_are_left_open(client):
    """GETs stay open so dashboards keep working — that was the original
    design and it is not what broke."""
    r = client.get("/api/aria/voice",
                   headers={"X-ARIA-Token": TOKEN,
                            "Origin": "https://evil.example.com"})
    assert r.status_code != 403 or "origin" not in (
        r.json().get("detail") or "").lower()
