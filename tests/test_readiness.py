"""
Readiness — the stricter question than liveness.

A process that started, cannot write its data and has no owner credential
answers /health happily. /ready must say no, and must say it without telling an
anonymous caller which part of the machine is failing.
"""
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

REMOTE = ("203.0.113.9", 53001)


@pytest.fixture
def app(monkeypatch, tmp_path):
    from backend.main import app as fastapi_app
    from src.auth import session as sess
    from src.core import readiness
    monkeypatch.setattr(sess.SESSIONS, "store", tmp_path / "auth" / "sessions.json")
    monkeypatch.setattr(readiness, "DATA", tmp_path)
    readiness._last["ready"] = None
    return fastapi_app


def test_ready_when_configured(app, monkeypatch):
    monkeypatch.setenv("ARIA_OWNER_TOKEN", "a-configured-credential")
    r = TestClient(app, client=REMOTE).get("/ready")
    assert r.status_code == 200 and r.json() == {"ready": True}


def test_not_ready_when_nobody_can_sign_in(app, monkeypatch):
    """SERVER NOT INITIALIZED must not present as healthy to a supervisor."""
    monkeypatch.delenv("ARIA_OWNER_TOKEN", raising=False)
    monkeypatch.delenv("ARIA_OWNER_TOKEN_HASH", raising=False)
    r = TestClient(app, client=REMOTE).get("/ready")
    assert r.status_code == 503
    assert r.json() == {"ready": False}, "reasons must not be in the public body"


def test_not_ready_on_a_corrupt_session_store(app, monkeypatch):
    from src.auth import session as sess
    monkeypatch.setenv("ARIA_OWNER_TOKEN", "a-configured-credential")
    sess.SESSIONS.store.parent.mkdir(parents=True, exist_ok=True)
    sess.SESSIONS.store.write_text("{ not json", encoding="utf-8")
    assert TestClient(app, client=REMOTE).get("/ready").status_code == 503


def test_not_ready_on_low_disk(app, monkeypatch):
    from src.core import readiness
    monkeypatch.setenv("ARIA_OWNER_TOKEN", "a-configured-credential")
    monkeypatch.setattr(readiness, "MIN_FREE_BYTES", 10 ** 18)
    assert TestClient(app, client=REMOTE).get("/ready").status_code == 503


def test_reasons_are_available_to_the_owner(app, monkeypatch):
    monkeypatch.delenv("ARIA_OWNER_TOKEN_HASH", raising=False)
    monkeypatch.setenv("ARIA_OWNER_TOKEN", "a-configured-credential")
    from src.core import readiness
    monkeypatch.setattr(readiness, "MIN_FREE_BYTES", 10 ** 18)
    r = TestClient(app, client=REMOTE).get(
        "/api/system/health", headers={"Authorization": "Bearer a-configured-credential"})
    assert r.status_code == 200
    body = r.json()["readiness"]
    assert body["ready"] is False and any("disk" in x for x in body["reasons"])


def test_ready_is_public_but_nothing_new_is(app):
    from src.auth import policy
    assert policy.is_allowed("/ready", policy.ANON)
    assert not policy.is_allowed("/api/system/health", policy.ANON)
