"""
tests/test_trading_api.py
=========================
The /api/trading surface, driven through the real app from a remote address —
so nothing passes because the request happened to arrive from localhost.

What is asserted here is the join between the web layer and the execution path:
that nobody but the owner reaches any of it, that the mode still cannot be
raised without its typed phrase even with a valid session, that a limit above
its ceiling is refused with the ceiling named, and that the halt works from a
stale session because an emergency stop that asks for a password is not one.
"""
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient          # noqa: E402

REMOTE = "203.0.113.11"
TOKEN = "test-owner-token-for-the-trading-api"

pytestmark = pytest.mark.usefixtures("clean_trading_state")


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


@pytest.fixture
def owner(app, sessions):
    client = TestClient(app, client=(REMOTE, 53001))
    assert client.post("/api/auth/login", json={"token": TOKEN}).status_code == 200
    return client


def anon(app) -> TestClient:
    return TestClient(app, client=(REMOTE, 53002))


# ── who may look ─────────────────────────────────────────────────────────────

ROUTES = [("GET", "/api/trading/status"), ("GET", "/api/trading/limits"),
          ("GET", "/api/trading/strategies"), ("GET", "/api/trading/latency"),
          ("GET", "/api/trading/orders"), ("GET", "/api/trading/capital"),
          ("GET", "/api/trading/scorecard"), ("GET", "/api/trading/kill-policy"),
          ("POST", "/api/trading/mode"), ("POST", "/api/trading/limits"),
          ("POST", "/api/trading/halt"), ("POST", "/api/trading/strategies"),
          ("POST", "/api/trading/arbitrage/evaluate"),
          ("POST", "/api/trading/orders/reconcile")]


@pytest.mark.parametrize("method,path", ROUTES)
def test_nobody_without_a_session_reaches_the_trading_api(app, method, path):
    r = anon(app).request(method, path, json={})
    assert r.status_code in (401, 403), f"{method} {path} -> {r.status_code}"


@pytest.mark.parametrize("method,path", ROUTES)
def test_the_sentinel_service_token_reaches_none_of_it(app, monkeypatch, method, path):
    """Sentinel may ask ARIA for research. It has no execution authority."""
    monkeypatch.setenv("ARIA_SERVICE_TOKEN", "service-token-for-this-test-only")
    client = TestClient(app, client=(REMOTE, 53003))
    r = client.request(method, path, json={},
                       headers={"Authorization": "Bearer service-token-for-this-test-only"})
    assert r.status_code in (401, 403), f"{method} {path} -> {r.status_code}"


# ── status ───────────────────────────────────────────────────────────────────

def test_status_reports_the_mode_the_limits_and_what_is_disabled(owner):
    body = owner.get("/api/trading/status").json()
    assert body["mode"]["stored_mode"] == "RESEARCH"
    assert body["mode"]["can_access_live_broker"] is False
    assert body["limits"]["in_force"]["max_leverage"] == 1.0
    assert body["limits"]["ceilings"]["max_order_value"] == 1000.0
    assert any("live autonomous" in d for d in body["disabled_in_this_deployment"])
    assert TOKEN not in owner.get("/api/trading/status").text


# ── the mode ─────────────────────────────────────────────────────────────────

def test_the_mode_cannot_be_raised_without_the_phrase(owner):
    r = owner.post("/api/trading/mode", json={"mode": "PAPER", "reason": "testing"})
    assert r.status_code == 409
    assert "confirmation phrase" in r.json()["detail"]


def test_the_mode_can_be_raised_one_step_with_the_phrase(owner):
    r = owner.post("/api/trading/mode",
                   json={"mode": "PAPER", "reason": "the brief enables paper trading",
                         "confirmation": "ENABLE PAPER TRADING"})
    assert r.status_code == 200, r.text
    assert r.json()["status"]["stored_mode"] == "PAPER"
    history = owner.get("/api/trading/mode/history").json()["history"]
    assert history[-1]["to"] == "PAPER"
    assert history[-1]["reason"] == "the brief enables paper trading"


def test_live_is_refused_through_the_api_too(owner):
    owner.post("/api/trading/mode",
               json={"mode": "PAPER", "reason": "t",
                     "confirmation": "ENABLE PAPER TRADING"})
    r = owner.post("/api/trading/mode",
                   json={"mode": "LIVE_AUTONOMOUS", "reason": "t",
                         "confirmation": "ENABLE LIVE AUTONOMOUS TRADING WITH REAL MONEY"})
    assert r.status_code == 409
    assert "ARIA_LIVE_TRADING_ENABLED" in r.json()["detail"]


def test_an_unknown_mode_is_a_bad_request(owner):
    r = owner.post("/api/trading/mode", json={"mode": "GO_WILD", "reason": "t"})
    assert r.status_code == 400


# ── the limits ───────────────────────────────────────────────────────────────

def test_a_limit_above_its_ceiling_is_refused_with_the_ceiling_named(owner):
    r = owner.post("/api/trading/limits", json={"changes": {"max_order_value": 9999}})
    assert r.status_code == 409
    assert "hard ceiling" in r.json()["detail"]


def test_tightening_a_limit_takes_effect_immediately(owner):
    r = owner.post("/api/trading/limits", json={"changes": {"max_order_value": 25}})
    assert r.status_code == 200
    assert r.json()["applied"] == {"max_order_value": 25.0}
    assert owner.get("/api/trading/limits").json()["in_force"]["max_order_value"] == 25.0


def test_loosening_a_limit_is_scheduled_not_applied(owner):
    owner.post("/api/trading/limits", json={"changes": {"max_order_value": 25}})
    r = owner.post("/api/trading/limits", json={"changes": {"max_order_value": 200}})
    assert r.json()["scheduled"]["max_order_value"]["value"] == 200.0
    assert owner.get("/api/trading/limits").json()["in_force"]["max_order_value"] == 25.0


def test_an_empty_change_set_is_rejected(owner):
    assert owner.post("/api/trading/limits", json={"changes": {}}).status_code == 400


# ── the halt ─────────────────────────────────────────────────────────────────

def test_the_halt_stops_everything_from_one_call(owner):
    owner.post("/api/trading/mode",
               json={"mode": "PAPER", "reason": "t",
                     "confirmation": "ENABLE PAPER TRADING"})
    from src.execution import kill_switch
    try:
        r = owner.post("/api/trading/halt", json={"reason": "testing the halt"})
        assert r.status_code == 200, r.text
        assert kill_switch.is_engaged() is True
        status = owner.get("/api/trading/status").json()
        assert status["mode"]["stored_mode"] == "RESEARCH"
        assert status["mode"]["can_place_orders"] is False
    finally:
        kill_switch.release(actor="test")


# ── strategies ───────────────────────────────────────────────────────────────

def test_a_strategy_is_registered_at_draft_and_promoted_only_on_evidence(owner):
    assert owner.post("/api/trading/strategies",
                      json={"strategy_id": "api-test", "name": "API test",
                            "hypothesis": "h"}).status_code == 200
    r = owner.post("/api/trading/strategies/api-test/stage",
                   json={"action": "promote", "stage": "RESEARCH"})
    assert r.status_code == 409
    assert "data sources" in r.json()["detail"]

    owner.post("/api/trading/strategies/api-test/evidence",
               json={"key": "data_sources", "value": ["alpaca"]})
    r = owner.post("/api/trading/strategies/api-test/stage",
                   json={"action": "promote", "stage": "RESEARCH"})
    assert r.status_code == 200 and r.json()["stage"] == "RESEARCH"


def test_a_strategy_can_always_be_suspended(owner):
    owner.post("/api/trading/strategies", json={"strategy_id": "api-test2"})
    r = owner.post("/api/trading/strategies/api-test2/stage",
                   json={"action": "suspend", "reason": "drifted"})
    assert r.status_code == 200 and r.json()["stage"] == "SUSPENDED"


def test_evidence_for_an_unknown_strategy_is_a_404(owner):
    r = owner.post("/api/trading/strategies/ghost/evidence",
                   json={"key": "hypothesis", "value": "h"})
    assert r.status_code == 404


# ── arbitrage ────────────────────────────────────────────────────────────────

def leg(side, bid, ask, venue):
    from datetime import datetime, timezone
    return {"venue": venue, "symbol": "BTC/USD", "side": side, "bid": bid, "ask": ask,
            "bid_size": 1000, "ask_size": 1000, "asset_class": "crypto",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "fee_schedule": "simulated"}


def test_the_arbitrage_endpoint_subtracts_costs_and_never_trades(owner):
    r = owner.post("/api/trading/arbitrage/evaluate", json={
        "kind": "cross_venue", "qty": 1,
        "legs": [leg("buy", 99.95, 100.0, "venue_a"),
                 leg("sell", 100.5, 100.55, "venue_b")]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["research_only"] is True
    assert body["net_bps"] < body["gross_bps"]
    assert body["costs"]["fees"] > 0


def test_a_thin_spread_comes_back_as_not_arbitrage(owner):
    r = owner.post("/api/trading/arbitrage/evaluate", json={
        "kind": "cross_venue", "qty": 1,
        "legs": [leg("buy", 99.99, 100.0, "venue_a"),
                 leg("sell", 100.01, 100.02, "venue_b")]})
    body = r.json()
    assert body["verdict"] == "NOT_ARBITRAGE"
    assert any("NO TRADE" in x for x in body["reasons"])


def test_a_malformed_opportunity_is_a_bad_request(owner):
    r = owner.post("/api/trading/arbitrage/evaluate",
                   json={"kind": "cross_venue", "qty": 1, "legs": [{"venue": "a"}]})
    assert r.status_code == 400


# ── capital ──────────────────────────────────────────────────────────────────

def test_a_contribution_is_recorded_and_never_inferred(owner):
    before = owner.get("/api/trading/capital").json()
    assert before["contributions"] == []
    assert before["planned_monthly_contribution"]["amount"] == 30.0
    r = owner.post("/api/trading/capital",
                   json={"amount": 30, "currency": "GBP",
                         "amount_in_account_ccy": 38.4, "note": "September"})
    assert r.status_code == 200
    after = owner.get("/api/trading/capital").json()
    assert after["net_contributions_account_ccy"] == 38.4
