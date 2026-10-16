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


def test_order_engine_views_say_they_cover_the_pipeline_only(owner):
    """The journal is fed only by TradingPipeline, which nothing in production
    runs; an unlabelled `orders: 0` read as "ARIA has sent no orders" while the
    desk was trading paper (see tests/test_trading_status_scope.py)."""
    status = owner.get("/api/trading/status").json()["order_engine"]
    assert set(status) >= {"halted", "orders", "scope"}, "existing keys must stay"
    assert status["scope"]["production_path"] is False
    assert "order_manager" in status["scope"]["note"]
    orders = owner.get("/api/trading/orders").json()
    assert set(orders) >= {"halted", "orders", "scope"}
    assert orders["scope"]["production_path"] is False


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


# ── where the order records really are (audit F-01) ──────────────────────────
#
# The pipeline journal is empty here because nothing in production runs the
# pipeline; the orders ARIA sends are in the desk's execution log. Each source
# reports missing / empty / populated / stale for itself, by its own records'
# timestamps — never by file modification time — and no history is invented.

@pytest.fixture
def record_files(tmp_path, monkeypatch):
    from src.desk import auto_executor
    from src.trading import orders
    journal, desk_log = tmp_path / "order_journal.jsonl", tmp_path / "executions.jsonl"
    monkeypatch.setattr(orders, "JOURNAL", journal)
    monkeypatch.setattr(auto_executor, "EXEC_LOG", desk_log)
    return journal, desk_log


def _sources(owner):
    body = owner.get("/api/trading/orders").json()
    return body, body["record_sources"]


def test_missing_record_files_are_reported_as_missing(owner, record_files):
    body, src = _sources(owner)
    assert body["orders"] == {}
    for name in ("pipeline_journal", "desk_executions"):
        assert src[name]["exists"] is False
        assert src[name]["records"] == 0 and src[name]["last_record_at"] is None
    assert src["pipeline_journal"]["production_path"] is False
    assert src["desk_executions"]["production_path"] is True


def test_empty_record_files_are_empty_not_missing(owner, record_files):
    journal, desk_log = record_files
    journal.write_text("", encoding="utf-8")
    desk_log.write_text("\n", encoding="utf-8")
    _, src = _sources(owner)
    assert src["pipeline_journal"]["exists"] is True and src["pipeline_journal"]["records"] == 0
    assert src["desk_executions"]["exists"] is True and src["desk_executions"]["records"] == 0


def test_an_empty_journal_beside_real_executions_says_so(owner, record_files):
    """The original misreading: orders: {} while the desk had traded."""
    import json as _json
    from datetime import datetime
    journal, desk_log = record_files
    now = datetime.now().isoformat()
    desk_log.write_text("\n".join(_json.dumps(r) for r in [
        {"at": now, "action": "entry", "ticker": "AAPL", "mode": "auto"},
        {"at": now, "action": "exit", "ticker": "AAPL", "mode": "broker"}]) + "\n",
        encoding="utf-8")
    body, src = _sources(owner)
    assert body["orders"] == {}, "no pipeline order history may be invented"
    assert src["pipeline_journal"]["records"] == 0
    assert src["desk_executions"]["records"] == 2
    assert src["desk_executions"]["last_record_at"] == now
    assert src["desk_executions"]["age_hours"] is not None and src["desk_executions"]["age_hours"] < 1
    assert src["desk_executions"]["served_by"] == "/api/desk/executions"


def test_a_stale_execution_log_shows_its_real_age(owner, record_files):
    import json as _json
    from datetime import datetime, timedelta
    _, desk_log = record_files
    old = (datetime.now() - timedelta(days=40)).isoformat()
    desk_log.write_text(_json.dumps({"at": old, "action": "exit", "ticker": "SPY"}) + "\n",
                        encoding="utf-8")
    import os as _os, time as _time
    _os.utime(desk_log, (_time.time(), _time.time()))      # file touched just now
    _, src = _sources(owner)
    assert src["desk_executions"]["age_hours"] > 24 * 39, \
        "age must come from the record's own timestamp, not the file's mtime"


def test_a_populated_pipeline_journal_is_shown_as_it_is(owner, record_files):
    import json as _json
    from datetime import datetime, timezone
    journal, _ = record_files
    at = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
    journal.write_text("\n".join(_json.dumps(r) for r in [
        {"at": at, "client_order_id": "aria-abc", "state": "INTENT"},
        {"at": at, "client_order_id": "aria-abc", "state": "FILLED"}]) + "\n",
        encoding="utf-8")
    body, src = _sources(owner)
    assert set(body["orders"]) == {"aria-abc"} and body["orders"]["aria-abc"]["state"] == "FILLED"
    assert src["pipeline_journal"]["records"] == 2
    assert src["pipeline_journal"]["age_hours"] is not None
    status = owner.get("/api/trading/status").json()["order_engine"]
    assert status["orders"] == 1 and status["record_sources"]["pipeline_journal"]["records"] == 2


def test_unreadable_lines_are_counted_not_hidden(owner, record_files):
    _, desk_log = record_files
    desk_log.write_text('{"at": "2026-10-01T10:00:00", "action": "entry"}\n{broken\n',
                        encoding="utf-8")
    _, src = _sources(owner)
    assert src["desk_executions"]["records"] == 1
    assert src["desk_executions"]["unreadable_lines"] == 1
