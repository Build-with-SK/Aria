"""
tests/test_research_api.py
==========================
The research routes: owner-only, validated input, a point-in-time `at` that
must carry a timezone, and a report with no confidence number. The engine is
replaced by the fixture world so no vendor is called.
"""
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

fastapi = pytest.importorskip("fastapi")
pytest.importorskip("exchange_calendars")
from fastapi.testclient import TestClient  # noqa: E402

import research_fixtures as F  # noqa: E402

REMOTE = "203.0.113.31"
TOKEN = "test-owner-token-for-the-research-api"


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
        os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


@pytest.fixture
def sessions(monkeypatch, tmp_path):
    from src.auth import session as sess
    monkeypatch.setattr(sess.SESSIONS, "store", tmp_path / "sessions.json")
    monkeypatch.setattr(sess.SESSIONS, "session_key_file", tmp_path / ".key")
    sess.SESSIONS._attempts.clear()
    return sess


@pytest.fixture(autouse=True)
def _fresh_rate_limiter():
    from src.auth import hardening
    hardening._limiter._hits.clear()
    yield
    hardening._limiter._hits.clear()


@pytest.fixture(autouse=True)
def fixture_world(monkeypatch, tmp_path):
    """Every research request answers from the deterministic fixture world."""
    from src.intelligence.research import engine
    real = engine.research
    monkeypatch.setattr(engine, "LATEST_DIR", tmp_path / "latest")

    def fake(symbol, *, at=None, **kw):
        kw.pop("data", None)
        rep = real(symbol, at=at or F.T, data=F.world(), **kw)
        if at is None:
            rep = {**rep, "live": True}        # as a live request would be
        return rep
    monkeypatch.setattr(engine, "research", fake)


@pytest.fixture
def owner(app, sessions):
    client = TestClient(app, client=(REMOTE, 55001))
    assert client.post("/api/auth/login", json={"token": TOKEN}).status_code == 200
    return client


@pytest.mark.parametrize("path", ["/api/intelligence/research/sources",
                                  "/api/intelligence/research/AAPL"])
def test_nobody_without_a_session_reaches_research(app, path):
    r = TestClient(app, client=(REMOTE, 55002)).get(path)
    assert r.status_code in (401, 403)


def test_the_source_audit_is_served(owner):
    body = owner.get("/api/intelligence/research/sources").json()
    names = {s["source"] for s in body["sources"]}
    assert {"ohlcv", "fundamentals", "news", "macro_releases", "model_predictions"} <= names
    assert any(d["source"] == "technical_indicators" for d in body["duplicates"])


def test_a_research_report_has_no_confidence_number(owner, tmp_path):
    r = owner.get("/api/intelligence/research/AAPL?analogues=false")
    assert r.status_code == 200
    th = r.json()["thesis"]
    assert th["confidence"] is None and th["executable"] is False
    assert th["direction"] in ("BULLISH", "BEARISH", "NEUTRAL")
    from src.intelligence.research import engine
    assert (tmp_path / "latest" / "AAPL.json").exists()     # live request: thesis saved


def test_a_point_in_time_request_needs_a_timezone_and_is_not_saved(owner, tmp_path):
    assert owner.get("/api/intelligence/research/AAPL?at=2026-09-15T21:00:00").status_code == 400
    r = owner.get("/api/intelligence/research/AAPL?at=2026-09-15T21:00:00%2B00:00&analogues=false")
    assert r.status_code == 200 and r.json()["at"].startswith("2026-09-15T21:00")
    assert not (tmp_path / "latest" / "AAPL.json").exists()


@pytest.mark.parametrize("bad", ["..%2F..%2Fsecrets", "A" * 30, "AAPL;rm"])
def test_a_symbol_that_is_not_a_symbol_is_refused(owner, bad):
    assert owner.get(f"/api/intelligence/research/{bad}").status_code in (400, 404)
