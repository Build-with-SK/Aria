"""
tests/test_data_freshness_api.py
================================
Legacy generated files must say how old they are.

backtest_results.json, options_data.json, portfolio_analysis.json and
daily_report.json have exactly one writer — main_phase4_backup.py, which
nothing runs — and had not moved since 2026-05-31. /api/backtest,
/api/options, /api/portfolio and /api/report served them with no date at all,
so May's numbers read as today's. They are not deleted (the UI and the
Streamlit dashboard still read them); they are dated.
"""
import json
import os
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient          # noqa: E402

REMOTE = "203.0.113.21"
TOKEN = "test-owner-token-for-freshness"


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
def owner(app, monkeypatch, tmp_path):
    from src.auth import hardening
    from src.auth import session as sess
    monkeypatch.setattr(sess.SESSIONS, "store", tmp_path / "sessions.json")
    monkeypatch.setattr(sess.SESSIONS, "session_key_file", tmp_path / ".key")
    sess.SESSIONS._attempts.clear()
    hardening._limiter._hits.clear()
    client = TestClient(app, client=(REMOTE, 53101))
    assert client.post("/api/auth/login", json={"token": TOKEN}).status_code == 200
    return client


@pytest.fixture
def data_dir(monkeypatch, tmp_path):
    """Point the backend loader and the world model at one fixture directory."""
    import backend.main as bm
    from src.core import world
    monkeypatch.setattr(bm, "ROOT", tmp_path)
    (tmp_path / "data").mkdir()
    monkeypatch.setattr(world, "DATA", tmp_path / "data")

    def write(name: str, body, age_hours: float):
        p = tmp_path / "data" / name
        p.write_text(json.dumps(body), encoding="utf-8")
        t = time.time() - age_hours * 3600
        os.utime(p, (t, t))
    return write


def test_options_are_dated_in_headers_and_body_is_unchanged(owner, data_dir):
    data_dir("options_data.json", {"SPY": {"pcr": 0.9}}, age_hours=24 * 120)
    r = owner.get("/api/options")
    assert r.status_code == 200
    assert r.json() == {"SPY": {"pcr": 0.9}}        # UI iterates this map
    assert r.headers["X-ARIA-Data-Status"] == "STALE"
    assert r.headers["X-ARIA-Data-Legacy"] == "true"
    assert float(r.headers["X-ARIA-Data-Age-Hours"]) > 24 * 100


def test_backtest_carries_freshness_in_the_body(owner, data_dir):
    data_dir("backtest_results.json",
             {"AAPL": {"metrics": {"sharpe_ratio": 1.2}}}, age_hours=24 * 120)
    body = owner.get("/api/backtest").json()
    assert body["count"] == 1
    assert body["freshness"]["status"] == "STALE"
    assert body["freshness"]["legacy"] is True


def test_portfolio_model_is_dated(owner, data_dir):
    data_dir("portfolio_analysis.json", {"x": 1}, age_hours=24 * 120)
    r = owner.get("/api/portfolio")
    assert r.json()["freshness"]["status"] == "STALE"
    assert r.headers["X-ARIA-Data-Status"] == "STALE"


def test_a_fresh_file_says_fresh(owner, data_dir):
    data_dir("ml_predictions.json", {"AAPL": {"p": 0.6}}, age_hours=1)
    r = owner.get("/api/ml")
    assert r.json() == {"AAPL": {"p": 0.6}}
    assert r.headers["X-ARIA-Data-Status"] == "FRESH"


def test_the_registry_lists_every_dataset(owner, data_dir):
    data_dir("signals.json", {}, age_hours=1)
    body = owner.get("/api/data/freshness").json()
    names = {r["dataset"]: r for r in body["datasets"]}
    assert names["signals.json"]["status"] == "FRESH"
    assert names["daily_report.json"]["status"] == "UNKNOWN"     # missing
    assert "daily_report.json" in body["stale"]


def test_the_registry_is_not_public(app):
    from src.auth import hardening
    hardening._limiter._hits.clear()
    anon = TestClient(app, client=(REMOTE, 53102))
    assert anon.get("/api/data/freshness").status_code in (401, 403)
