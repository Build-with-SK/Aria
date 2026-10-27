"""
tests/test_step_up.py
=====================
AUTHENTICATED IS NOT THE SAME AS ALLOWED RIGHT NOW.

A session that has sat open on a phone for five days proves somebody signed in
five days ago. It does not prove the owner is holding the phone when a trade is
approved. So the routes that move money or loosen a safety control need the
credential to have been presented recently, and the routes that REDUCE risk
must never ask.

Both halves are asserted here, because the second is as easy to get wrong as
the first: a kill switch that stops to ask for a password is one that gets
pressed too late.

Every test drives the real app from a remote address, so loopback inference
can never be what lets a request through.
"""
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient          # noqa: E402

REMOTE = "203.0.113.9"
TOKEN = "test-owner-token-for-step-up-only"

#: Raises risk. Must demand a recent confirmation from a session.
HIGH_RISK = [
    ("POST", "/api/execute/approve/no-such-trade", None),
    ("POST", "/api/execute/kill-switch/release", None),
    ("PATCH", "/api/desk/config", {"interval_minutes": 30}),
    ("POST", "/api/desk/auto-execute?enabled=true", None),
    # The autonomous trading path: the mode decides whether orders may exist,
    # the limits decide how large, the kill policy decides what a halt does to
    # open positions, and a promotion decides which strategy may trade.
    ("POST", "/api/trading/mode", {"mode": "PAPER", "reason": "t"}),
    ("POST", "/api/trading/limits", {"changes": {"max_order_value": 50}}),
    ("POST", "/api/trading/kill-policy", {"positions": "PRESERVE"}),
    ("POST", "/api/trading/strategies/none/stage", {"stage": "RESEARCH"}),
    # Releasing an unresolved order unblocks its instrument for new orders —
    # the owner's judgement that a lookup could not prove (S1.3 review).
    ("POST", "/api/execute/unresolved/no-such-trade/release?note=checked", None),
]

#: Reduces risk. Must NEVER be held behind a step-up.
SAFETY = [
    ("POST", "/api/execute/kill-switch/engage", {"reason": "test"}),
    ("POST", "/api/execute/reject/no-such-trade", None),
    ("POST", "/api/execute/cancel/no-such-trade", None),
    ("POST", "/api/desk/auto-execute?enabled=false", None),
    ("POST", "/api/trading/halt", {"reason": "test"}),
]


@pytest.fixture(scope="module")
def app():
    from backend.main import app as fastapi_app
    saved = {k: os.environ.get(k) for k in ("ARIA_OWNER_TOKEN", "ARIA_TRUST_PROXY",
                                            "ARIA_OWNER_TOKEN_HASH")}
    os.environ["ARIA_OWNER_TOKEN"] = TOKEN
    os.environ.pop("ARIA_OWNER_TOKEN_HASH", None)
    os.environ.pop("ARIA_TRUST_PROXY", None)
    yield fastapi_app
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


@pytest.fixture
def sessions(monkeypatch, tmp_path):
    from src.auth import session as sess
    monkeypatch.setattr(sess.SESSIONS, "store", tmp_path / "sessions.json")
    monkeypatch.setattr(sess.SESSIONS, "session_key_file", tmp_path / ".key")
    sess.SESSIONS._attempts.clear()
    return sess


@pytest.fixture
def clock(monkeypatch):
    from src.auth import ownerauth
    import time
    state = {"t": time.time()}
    monkeypatch.setattr(ownerauth, "_now", lambda: state["t"])
    return state


@pytest.fixture(autouse=True)
def _fresh_rate_limiter():
    """The per-IP limiter is process-global. Tests sign in many times from one
    address; without this they measure each other rather than the guard."""
    from src.auth import hardening
    hardening._limiter._hits.clear()
    yield
    hardening._limiter._hits.clear()


@pytest.fixture(autouse=True)
def _no_real_kill_switch(monkeypatch, tmp_path):
    """Engaging the kill switch in a test must not engage the owner's real one."""
    from src.execution import kill_switch
    for name in dir(kill_switch):
        val = getattr(kill_switch, name)
        if isinstance(val, Path) and "kill_switch" in val.name:
            monkeypatch.setattr(kill_switch, name, tmp_path / val.name)


def _signed_in(app, sessions):
    client = TestClient(app, client=(REMOTE, 52001))
    r = client.post("/api/auth/login", json={"token": TOKEN})
    assert r.status_code == 200, r.text
    return client


def _call(client, method, path, body):
    return client.request(method, path, json=body) if body is not None \
        else client.request(method, path)


def _is_step_up(r) -> bool:
    if r.status_code != 403:
        return False
    detail = (r.json() or {}).get("detail")
    return isinstance(detail, dict) and detail.get("error") == "step_up_required"


# ── the high-risk half ───────────────────────────────────────────────────────

@pytest.mark.parametrize("method,path,body", HIGH_RISK)
def test_a_fresh_sign_in_is_enough(app, sessions, method, path, body):
    """Nobody should be asked twice in the same minute. Whatever the handler
    then says (404 for a trade that does not exist, 409 for an unconfirmed paper
    account), it must not be the step-up refusal."""
    client = _signed_in(app, sessions)
    assert not _is_step_up(_call(client, method, path, body))


@pytest.mark.parametrize("method,path,body", HIGH_RISK)
def test_a_stale_session_must_confirm(app, sessions, clock, method, path, body):
    from src.auth import ownerauth
    client = _signed_in(app, sessions)
    clock["t"] += ownerauth.STEP_UP_SECONDS + 5
    assert client.get("/api/auth/me").json()["owner"] is True, \
        "the session itself must still be alive — this is step-up, not expiry"
    r = _call(client, method, path, body)
    assert _is_step_up(r), f"{method} {path} -> {r.status_code} {r.text[:200]}"


@pytest.mark.parametrize("method,path,body", HIGH_RISK)
def test_confirming_unlocks_it_again(app, sessions, clock, method, path, body):
    from src.auth import ownerauth
    client = _signed_in(app, sessions)
    clock["t"] += ownerauth.STEP_UP_SECONDS + 5
    r = client.post("/api/auth/confirm", json={"token": TOKEN})
    assert r.status_code == 200, r.text
    assert not _is_step_up(_call(client, method, path, body))


@pytest.mark.parametrize("method,path,body", HIGH_RISK)
def test_a_bearer_caller_has_already_confirmed(app, method, path, body):
    """It presented the credential on THIS request — the most recent
    confirmation there can be."""
    client = TestClient(app, client=(REMOTE, 52002))
    r = client.request(method, path, json=body,
                       headers={"Authorization": f"Bearer {TOKEN}"})
    assert not _is_step_up(r)


def test_a_wrong_confirmation_does_not_unlock(app, sessions, clock):
    from src.auth import ownerauth
    client = _signed_in(app, sessions)
    clock["t"] += ownerauth.STEP_UP_SECONDS + 5
    assert client.post("/api/auth/confirm", json={"token": "wrong"}).status_code == 401
    assert _is_step_up(client.post("/api/execute/kill-switch/release"))


def test_confirm_needs_an_existing_session(app, sessions):
    """There is nothing to confirm without one — and it must not become a
    second, differently-throttled sign-in route."""
    anon = TestClient(app, client=(REMOTE, 52003))
    assert anon.post("/api/auth/confirm", json={"token": TOKEN}).status_code == 401


def test_the_refusal_tells_the_interface_where_to_confirm(app, sessions, clock):
    from src.auth import ownerauth
    client = _signed_in(app, sessions)
    clock["t"] += ownerauth.STEP_UP_SECONDS + 5
    detail = client.post("/api/execute/kill-switch/release").json()["detail"]
    assert detail["confirm"] == "/api/auth/confirm"
    assert TOKEN not in repr(detail)


# ── the safety half ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("method,path,body", SAFETY)
def test_reducing_risk_never_asks(app, sessions, clock, method, path, body):
    """A safety control that stops to ask for a password is one that gets
    pressed too late. These must work on a stale session."""
    from src.auth import ownerauth
    client = _signed_in(app, sessions)
    clock["t"] += ownerauth.STEP_UP_SECONDS + 5
    r = _call(client, method, path, body)
    assert not _is_step_up(r), f"{method} {path} demanded a step-up"
    assert r.status_code not in (401, 403), f"{method} {path} -> {r.status_code}"


# ── every high-risk route is still owner-only first ──────────────────────────

@pytest.mark.parametrize("method,path,body", HIGH_RISK)
def test_anonymous_callers_are_refused_before_step_up_is_considered(app, method, path, body):
    anon = TestClient(app, client=(REMOTE, 52004))
    r = _call(anon, method, path, body)
    assert r.status_code == 401
