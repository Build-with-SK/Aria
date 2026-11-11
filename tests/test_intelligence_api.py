"""
tests/test_intelligence_api.py
==============================
The model-intelligence routes: owner-only, step-up for promotion, honest
statuses on an empty record, and a promotion the gate refuses stays refused.
"""
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

REMOTE = "203.0.113.21"
TOKEN = "test-owner-token-for-the-intelligence-api"

ROUTES = [("GET", "/api/intelligence/semantics"), ("GET", "/api/intelligence/baseline"),
          ("GET", "/api/intelligence/models"), ("GET", "/api/intelligence/registry"),
          ("GET", "/api/intelligence/routing"), ("GET", "/api/intelligence/conviction?symbol=AAPL"),
          ("GET", "/api/intelligence/ensemble"), ("GET", "/api/intelligence/debate"),
          ("GET", "/api/intelligence/exclusions"), ("POST", "/api/intelligence/refresh"),
          ("POST", "/api/intelligence/registry/technical/status")]


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
def _isolated_intelligence(tmp_path, monkeypatch):
    """Every file these routes read or write, in a throwaway directory."""
    from src.intelligence import debate_eval, evidence, regrade, registry, walkforward
    monkeypatch.setattr(evidence, "SNAPSHOT_FILE", tmp_path / "snap.json")
    monkeypatch.setattr(registry, "STATUS_LOG", tmp_path / "status.jsonl")
    monkeypatch.setattr(regrade, "CACHE", tmp_path / "regrades.json")
    monkeypatch.setattr(debate_eval, "CACHE", tmp_path / "debate.json")
    monkeypatch.setattr(debate_eval, "DEBATES_DIR", tmp_path / "debates")
    monkeypatch.setattr(walkforward, "TRIALS_FILE", tmp_path / "trials.jsonl")
    monkeypatch.setenv("ARIA_CORE_DB", str(tmp_path / "core.db"))
    from src.core import bus, ledger
    monkeypatch.setattr(bus, "_initialised", False)
    monkeypatch.setattr(ledger, "_ready", False)


@pytest.fixture
def owner(app, sessions):
    client = TestClient(app, client=(REMOTE, 54001))
    assert client.post("/api/auth/login", json={"token": TOKEN}).status_code == 200
    return client


@pytest.mark.parametrize("method,path", ROUTES)
def test_nobody_without_a_session_reaches_model_intelligence(app, method, path):
    r = TestClient(app, client=(REMOTE, 54002)).request(method, path, json={"status": "RETIRED"})
    assert r.status_code in (401, 403), f"{method} {path} -> {r.status_code}"


def test_semantics_names_what_may_be_scored(owner):
    body = owner.get("/api/intelligence/semantics").json()
    assert body["scorable"] == ["CALIBRATED_PROBABILITY", "STATED_PROBABILITY"]


def test_an_empty_record_reports_statuses_not_numbers(owner):
    body = owner.get("/api/intelligence/models").json()
    reg = body["registry"]
    assert reg["technical"]["status"] == "INSUFFICIENT_DATA"
    assert reg["signal-engine"]["status"] == "NOT_RECORDED"
    assert all(c["promotion_check"] is None or c["promotion_check"]["eligible"] is False
               for c in reg.values())
    assert all(v["reason_code"] == "ROUTING_INSUFFICIENT_EVIDENCE"
               for v in body["routing_table"].values())


def test_routing_defaults_and_poor_data_abstains(owner):
    d = owner.get("/api/intelligence/routing?symbol=AAPL&horizon_days=10").json()
    assert d["reason_code"] == "ROUTING_INSUFFICIENT_EVIDENCE" and d["route"] == "DEFAULT"
    assert d["request"]["asset_class"] == "equity"
    p = owner.get("/api/intelligence/routing?symbol=AAPL&data_quality=POOR").json()
    assert p["reason_code"] == "ROUTING_DATA_QUALITY_ABSTAIN"


def test_promotion_is_refused_without_evidence_and_retirement_is_not(owner):
    r = owner.post("/api/intelligence/registry/technical/status",
                   json={"status": "VALIDATED", "reason": "it looks good"})
    assert r.status_code == 409 and "promotion gate does not pass" in r.json()["detail"]
    r = owner.post("/api/intelligence/registry/technical/status",
                   json={"status": "RETIRED", "reason": "not needed"})
    assert r.status_code == 200 and r.json()["recorded"]["status"] == "RETIRED"
    reg = owner.get("/api/intelligence/registry").json()
    assert reg["status_log"][-1]["status"] == "RETIRED"


def test_conviction_ensemble_debate_exclusions_and_baseline_answer_on_an_empty_record(owner):
    c = owner.get("/api/intelligence/conviction?symbol=AAPL").json()
    assert c["per_model"] == [] and c["cross_model_disagreement"]["status"] == "UNKNOWN"
    assert owner.get("/api/intelligence/ensemble").json()["pairs"] == []
    d = owner.get("/api/intelligence/debate").json()
    assert d["operations"]["debates"] == 0
    assert d["evaluation"]["status"] == "NOT_COMPUTED"
    assert owner.get("/api/intelligence/exclusions").json()["count"] == 0
    b = owner.get("/api/intelligence/baseline").json()
    assert b["overall"]["total"] == 0
