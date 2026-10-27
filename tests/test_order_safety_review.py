"""
tests/test_order_safety_review.py
=================================
Codex's independent review of S1.3 (e4f01ad): CHANGES REQUIRED, eight findings
(docs/collab/reviews/S1_3_ORDER_SAFETY_REVIEW.md in Codex's folder). One or
more regressions per finding, each against the real AlpacaBroker code over a
fake alpaca-py client and the real gates, then the read-only reconciliation and
the owner's release that resolve what no lookup can.

F1 a 404 never settles; F2 SDK retries cannot hide an accepted POST; F3 a
post-send exception never releases the block; F4 an unresolved order blocks
every lane; F5 acknowledged / working / filled / ended are kept apart; F6 the
row is claimed before the POST; F7 every reflex execution is journalled.
(F8 is the frontend — Codex's area.)
"""
import json

import pytest

from test_order_settlement import (FakeAlpaca, FakeClient, ReadTimeout, OPEN_NY,   # noqa: F401
                                   _desk, _order, _reflex_world, manager, queue)

ARM = ("BTC-USD", "buy", 102.0, None, None, 80, "", "signal", {})


def _arm_again(reflex):
    return reflex._arm(*ARM, reflex.load_config(), "a fresh signal")


def _desk_entry(engine):
    return engine._size({}, 101.0, {}, {})


# ── F1: a 404 never settles ──────────────────────────────────────────────────

def test_f1_late_acceptance_after_three_404s_gets_no_second_order(tmp_path, monkeypatch):
    from src.desk import auto_executor as ae
    late = _order("late-original", qty=0.01, price=101.0)
    client = FakeClient(lookups=[None, None, None, late])
    reflex, engine = _reflex_world(tmp_path, monkeypatch, client)

    assert engine.scan_once()[0]["mode"] == "unknown"
    assert reflex.load_playbooks()[0]["status"] == "unknown"

    client._accept = _order("second-order", qty=0.01, price=101.0)
    engine._last_price["BTC-USD"] = 99.0
    assert engine.scan_once() == [], "a retired playbook does not fire"
    assert _arm_again(reflex) is None, "the ticker does not re-arm"
    blocked = ae.AutoExecutor(engine.cfg).execute_slate([_desk_entry(engine)], [])[0]
    assert blocked["mode"] == "blocked", "the deliberate desk is blocked too"
    assert len(client.sent) == 1

    # The original becomes visible: reconciliation settles the row and the playbook.
    monkeypatch.setattr("src.execution.order_manager.RECONCILE_LOG", tmp_path / "recon.jsonl")
    report = ae.get_order_manager().reconcile_unresolved()
    assert list(report["settled"].values()) == ["filled"]
    assert reflex.reconcile_unknown_playbooks() == {"BTC-USD": "fired"}
    assert _arm_again(reflex) is None, "a fired playbook still inside its window blocks"
    assert len(client.sent) == 1


# ── F2: SDK retries cannot hide an accepted POST ─────────────────────────────

def _sdk(post_codes):
    """The installed alpaca-py TradingClient over a fake HTTP transport: each
    POST answers the next code; every GET (a lookup) answers 404."""
    from alpaca.trading.client import TradingClient
    from requests import Response
    sdk = TradingClient("fixture-key", "fixture-secret", paper=True)
    sdk._retry_wait = 0
    codes, posts = iter(post_codes), []

    def transport(method, url, **kwargs):
        code = 404
        if method.upper() == "POST":
            posts.append(kwargs["json"]["client_order_id"])
            code = next(codes)
        r = Response()
        r.status_code = code
        r._content = json.dumps({"code": code * 100, "message": "fixture"}).encode()
        return r

    sdk._session.request = transport
    return sdk, posts


@pytest.mark.parametrize("codes", [[504, 504, 504, 429], [504, 504, 504, 403]],
                         ids=["final-429", "final-403"])
def test_f2_a_final_4xx_after_sdk_retries_is_unknown(queue, codes):
    q = queue()
    sdk, posts = _sdk(codes)
    res = manager(FakeAlpaca(sdk)).execute(q.id, now=OPEN_NY)
    assert len(posts) == 4 and len(set(posts)) == 1, "the SDK retried with one id"
    assert res["unknown"] is True, res
    assert queue.get(q.id).status == "unknown"


@pytest.mark.parametrize("code,expect", [(504, "unknown"), (403, "rejected"), (429, "rejected")])
def test_f2_the_adapters_order_client_makes_one_attempt(queue, code, expect):
    from src.execution import alpaca_broker as ab
    q = queue()
    sdk, posts = _sdk([code])
    broker = FakeAlpaca(FakeClient())
    broker._client = sdk
    broker._submit_client = ab._no_retry(sdk)
    res = manager(broker).execute(q.id, now=OPEN_NY)
    assert len(posts) == 1, "one POST: the answer is about the only attempt"
    assert res["status"] == expect, res


def test_f2_the_real_adapter_sends_orders_through_a_no_retry_client(monkeypatch):
    from src.execution import alpaca_broker as ab
    monkeypatch.setenv("ALPACA_API_KEY", "fixture-key")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "fixture-secret")
    monkeypatch.setenv("ALPACA_PAPER", "true")
    b = ab.AlpacaBroker()
    assert b._submit_client is not b._client
    assert ab._retries_possible(b._submit_client) is False
    assert ab._retries_possible(b._client) is True, "reads keep the SDK's retries"


def test_f2_a_connection_never_made_is_not_sent_and_not_looked_up(queue):
    from requests.exceptions import ConnectTimeout
    q = queue()
    client = FakeClient(submit_error=ConnectTimeout("connect timed out"))
    res = manager(FakeAlpaca(client)).execute(q.id, now=OPEN_NY)
    assert res["status"] == "rejected" and client.asked == []


def test_f2_a_request_that_could_not_be_built_was_never_sent(queue, monkeypatch):
    from src.execution import alpaca_broker as ab

    def broken(**kw):
        raise ValueError("bad qty")

    monkeypatch.setattr(ab, "MarketOrderRequest", broken)
    q = queue()
    client = FakeClient()
    res = manager(FakeAlpaca(client)).execute(q.id, now=OPEN_NY)
    assert res["status"] == "rejected" and "nothing sent" in res["error"]
    assert client.sent == [] and client.asked == []


# ── F3: a post-send exception never releases the block ───────────────────────

def test_f3_a_notification_failure_after_a_fill_keeps_the_fill_and_the_block(tmp_path, monkeypatch):
    from src.desk import notify

    def broken(*a, **k):
        raise RuntimeError("notification failed after broker fill")

    client = FakeClient(lookups=[_order("filled", qty=0.01, price=101.0)])
    reflex, engine = _reflex_world(tmp_path, monkeypatch, client)
    monkeypatch.setattr(notify, "push", broken)
    out = engine.scan_once()[0]
    assert out["executed"] is True and out["filled"] is True
    assert reflex.load_playbooks()[0]["status"] == "fired"
    assert _arm_again(reflex) is None
    rows = [json.loads(x) for x in (tmp_path / "executions.jsonl").read_text().splitlines()]
    assert rows[-1]["mode"] == "auto" and "notify" in " ".join(rows[-1]["after_entry_errors"])


def test_f3_a_journal_failure_after_unknown_keeps_unknown(tmp_path, monkeypatch):
    from src.desk.auto_executor import AutoExecutor

    def broken(*a, **k):
        raise OSError("journal unavailable")

    client = FakeClient(lookups=[ReadTimeout("lookup unavailable")] * 3)
    reflex, engine = _reflex_world(tmp_path, monkeypatch, client)
    monkeypatch.setattr(AutoExecutor, "_log", broken)
    engine.scan_once()
    assert reflex.load_playbooks()[0]["status"] == "unknown"
    assert _arm_again(reflex) is None


def test_f3_an_exception_after_the_send_is_unknown_with_its_client_id(tmp_path, monkeypatch):
    from src.desk import auto_executor as ae
    client = FakeClient(lookups=[ReadTimeout("x")] * 3)
    reflex, engine = _reflex_world(tmp_path, monkeypatch, client)
    mgr = ae.get_order_manager()

    def raises_after_send(*a, **k):
        raise RuntimeError("crashed after the POST")

    monkeypatch.setattr(mgr, "_settle_unknown", raises_after_send)
    out = engine.scan_once()[0]
    pb = reflex.load_playbooks()[0]
    assert out["order_unknown"] is True and pb["status"] == "unknown"
    assert pb["client_order_id"] == client.sent[0].client_order_id
    assert _arm_again(reflex) is None
    assert ae.AutoExecutor(engine.cfg).execute_slate([_desk_entry(engine)], [])[0]["mode"] == "blocked"
    assert len(client.sent) == 1


def test_f3_an_exception_before_anything_was_queued_does_not_block(tmp_path, monkeypatch):
    from src.execution import approval_queue as aq
    client = FakeClient()
    reflex, engine = _reflex_world(tmp_path, monkeypatch, client)

    def broken(self, *a, **k):
        raise OSError("queue file locked")

    monkeypatch.setattr(aq.ApprovalQueue, "push", broken)
    out = engine.scan_once()[0]
    assert out.get("order_unknown") is None and out["executed"] is False
    assert reflex.load_playbooks()[0]["status"] == "expired"
    assert client.sent == []


# ── F4: an unresolved order blocks every lane ────────────────────────────────

def test_f4_an_unknown_desk_order_blocks_a_fresh_automated_and_an_owner_order(tmp_path, monkeypatch):
    from src.desk import auto_executor as ae
    from src.desk.auto_executor import AutoExecutor
    from src.execution import approval_queue as aq
    from src.execution.broker_base import AssetClass, OrderRequest, OrderSide
    client = FakeClient(lookups=[ReadTimeout("lookup unavailable")] * 3)
    _, engine = _reflex_world(tmp_path, monkeypatch, client)
    ex = AutoExecutor(engine.cfg)
    assert ex.execute_slate([_desk_entry(engine)], [])[0]["mode"] == "unknown"
    client._accept = _order("second", qty=0.01, price=101.0)
    second = ex.execute_slate([_desk_entry(engine)], [])[0]
    assert second["mode"] == "blocked" and "unresolved" in second["reason"]

    owner = aq.ApprovalQueue().push(OrderRequest(ticker="BTC-USD", side=OrderSide.BUY, qty=0.01,
                                                 asset_class=AssetClass.CRYPTO), broker="alpaca")
    res = ae.get_order_manager().execute(owner.id)
    assert res["blocked"] is True and len(client.sent) == 1


def test_f4_two_lanes_sending_at_once_cannot_both_pass(queue, monkeypatch):
    """The early guard reads the queue before the risk gate; another lane can
    claim in between. The claim itself re-checks under the queue lock."""
    from src.execution import approval_queue as aq
    from src.execution.order_manager import OrderManager
    first, second = queue(), queue()
    ok, _ = aq.ApprovalQueue().claim_for_submit(first.id, "aria-q-first")  # lane A in flight
    assert ok is not None
    monkeypatch.setattr(OrderManager, "unresolved_for", lambda self, trade: [])  # B's early read
    client = FakeClient(accept=_order("b"))
    res = manager(FakeAlpaca(client)).execute(second.id, now=OPEN_NY)
    assert res["blocked"] is True and "unresolved" in res["error"]
    assert client.sent == [] and queue.get(second.id).status == "rejected"


def test_f4_an_exit_is_not_blocked_by_an_unresolved_entry(tmp_path, monkeypatch):
    from src.desk import auto_executor as ae
    from src.desk.auto_executor import AutoExecutor
    from src.execution import approval_queue as aq
    from src.execution.broker_base import AssetClass, OrderRequest, OrderSide
    client = FakeClient(lookups=[ReadTimeout("x")] * 3)
    _, engine = _reflex_world(tmp_path, monkeypatch, client)
    AutoExecutor(engine.cfg).execute_slate([_desk_entry(engine)], [])
    exit_ = aq.ApprovalQueue().push(OrderRequest(ticker="BTC-USD", side=OrderSide.SELL, qty=0.01,
                                                 asset_class=AssetClass.CRYPTO, reduce_only=True),
                                    broker="alpaca")
    res = ae.get_order_manager().execute(exit_.id)
    assert not res.get("blocked"), "exits are clipped to the position and still go"
    assert "reduce-only" in res["error"], "no position here, so reduce-only refuses it"


# ── F5: acknowledged, working, filled and ended are different things ─────────

@pytest.mark.parametrize("terminal,ours", [("canceled", "cancelled"), ("rejected", "rejected"),
                                           ("expired", "expired")])
def test_f5_a_working_order_the_broker_then_ends_is_not_a_success(queue, monkeypatch,
                                                                   terminal, ours):
    from src.execution import approval_queue as aq
    monkeypatch.setattr("src.execution.order_manager.time.sleep", lambda s: None)
    q = queue()
    rows = json.loads(aq.QUEUE_FILE.read_text())
    rows[0]["stop_loss"] = 9.0                         # takes the fill-wait path
    aq.QUEUE_FILE.write_text(json.dumps(rows))
    client = FakeClient(accept=_order("working", status="new"))
    client.get_order_by_id = lambda oid: _order(oid, status=terminal)
    mgr = manager(FakeAlpaca(client))
    res = mgr.execute(q.id, now=OPEN_NY)
    assert res["ok"] is False and res["ended_after_ack"] is True and res["status"] == ours
    t = queue.get(q.id)
    assert t.status == "rejected" and t.order_status == ours and t.filled_qty == 0.0


def test_f5_a_status_read_that_fails_is_unknown_not_rejected():
    from src.execution.broker_base import OrderStatus
    client = FakeClient()

    def unavailable(order_id):
        raise ReadTimeout("status read timed out")

    client.get_order_by_id = unavailable
    assert FakeAlpaca(client).get_order_status("working").status == OrderStatus.UNKNOWN


def test_f5_a_failed_poll_is_not_news_about_the_order():
    from src.execution.broker_base import OrderResult, OrderStatus
    reads = iter([ReadTimeout("blip"), _order("w", status="filled", qty=1, price=10.0)])

    def status(order_id):
        r = next(reads)
        if isinstance(r, Exception):
            raise r
        return r

    client = FakeClient()
    client.get_order_by_id = status
    broker = FakeAlpaca(client)
    working = OrderResult(broker_order_id="w", status=OrderStatus.SUBMITTED)
    got = manager(broker)._await_fill(broker, working, timeout_s=5.0, poll_s=0.0)
    assert got.status == OrderStatus.FILLED, "the failed read was skipped, not taken as dead"


def test_f5_a_working_order_is_not_tracked_as_a_position(tmp_path, monkeypatch):
    rec, logged, notes, counted, tracked = _desk(tmp_path, monkeypatch, {
        "ok": True, "status": "submitted", "fill_price": 0.0, "filled_qty": 0.0,
        "broker_order_id": "working", "client_order_id": "aria-q-working"})
    assert tracked == [] and rec["fill_price"] is None and rec["filled_qty"] == 0.0
    assert rec["intended_price"] == 100.0 and rec["fill_confirmed"] is False
    assert counted == [1.0], "still counted against the day's caps"
    assert any("SUBMITTED" in n for n in notes) and not any("BOUGHT" in n for n in notes)


def test_f5_a_partial_fill_tracks_only_the_filled_quantity(tmp_path, monkeypatch):
    rec, logged, notes, counted, tracked = _desk(tmp_path, monkeypatch, {
        "ok": True, "status": "partial", "fill_price": 100.5, "filled_qty": 0.004,
        "broker_order_id": "p", "client_order_id": "aria-q-p"})
    assert len(tracked) == 1 and tracked[0][0]["qty"] == 0.004 and tracked[0][1] == 100.5
    assert rec["filled_qty"] == 0.004 and rec["fill_confirmed"] is False


# ── F6: the row is claimed before the POST ───────────────────────────────────

def test_f6_a_cancel_racing_the_send_is_refused(queue):
    from src.execution.approval_queue import ApprovalQueue
    q = queue()
    client = FakeClient(accept=_order("already-sent"))
    real, answers = client.submit_order, []

    def submit_then_cancel(order_data):
        answers.append(ApprovalQueue().cancel(q.id))
        return real(order_data)

    client.submit_order = submit_then_cancel
    res = manager(FakeAlpaca(client)).execute(q.id, now=OPEN_NY)
    assert answers == [False] and res["ok"] is True
    assert queue.get(q.id).status == "executed"


def test_f6_a_row_left_submitting_by_a_crash_is_never_resent_and_blocks(queue):
    from src.execution import approval_queue as aq
    crashed, fresh = queue(), queue()
    assert aq.ApprovalQueue().claim_for_submit(crashed.id, "aria-q-crashed")
    client = FakeClient(accept=_order("x"))
    mgr = manager(FakeAlpaca(client))
    assert "not approvable" in mgr.execute(crashed.id, now=OPEN_NY)["error"]
    assert mgr.execute(fresh.id, now=OPEN_NY)["blocked"] is True
    assert client.sent == []


def test_f6_the_owner_cannot_reject_a_row_already_claimed(queue):
    from src.execution import approval_queue as aq
    q = queue()
    aq.ApprovalQueue().claim_for_submit(q.id, "aria-q-1")
    assert aq.ApprovalQueue().reject(q.id, "owner says no") is None
    assert aq.ApprovalQueue().cancel(q.id) is False


# ── F7: every reflex execution is journalled ─────────────────────────────────

def test_f7_a_normal_reflex_fill_is_in_the_executions_journal(tmp_path, monkeypatch):
    client = FakeClient(lookups=[_order("normal-fill", qty=0.01, price=101.0)])
    _, engine = _reflex_world(tmp_path, monkeypatch, client)
    out = engine.scan_once()[0]
    rows = [json.loads(x) for x in (tmp_path / "executions.jsonl").read_text().splitlines()]
    assert out["filled"] is True
    assert [r["client_order_id"] for r in rows if r.get("lane") == "reflex"] == [out["client_order_id"]]


# ── reconciliation: reads only, settles only on a found answer ───────────────

@pytest.fixture
def unknown_row(queue, tmp_path, monkeypatch):
    monkeypatch.setattr("src.execution.order_manager.RECONCILE_LOG", tmp_path / "recon.jsonl")
    q = queue()
    client = FakeClient(lookups=[None] * 3)
    mgr = manager(FakeAlpaca(client))
    assert mgr.execute(q.id, now=OPEN_NY)["unknown"] is True
    return q, client, mgr


@pytest.mark.parametrize("answer,status,order_status", [
    (_order("b", "filled", qty=1, price=10.0), "executed", "filled"),
    (_order("b", "new"), "executed", "submitted"),
    (_order("b", "canceled"), "rejected", "cancelled"),
    (None, "unknown", "unknown"),
    (ReadTimeout("lookup down"), "unknown", "unknown"),
], ids=["filled", "working", "cancelled", "not-found", "lookup-fails"])
def test_reconcile_settles_only_on_the_brokers_answer(unknown_row, tmp_path, answer, status,
                                                      order_status):
    q, client, mgr = unknown_row
    client._lookups = [answer]
    sent_before = len(client.sent)
    mgr.reconcile_unresolved()
    t = mgr._queue.get_by_id(q.id)
    assert (t.status, t.order_status) == (status, order_status)
    assert len(client.sent) == sent_before, "reconciliation never submits"
    audit = [json.loads(x) for x in (tmp_path / "recon.jsonl").read_text().splitlines()]
    assert audit[-1]["client_order_id"] == t.client_order_id and audit[-1]["result"]


def test_the_owners_release_is_the_only_way_an_unfound_order_unblocks(unknown_row, queue):
    from src.execution import approval_queue as aq
    q, client, mgr = unknown_row
    with pytest.raises(ValueError):
        aq.ApprovalQueue().release_unresolved(q.id, by="owner", note="  ")
    nxt = queue()
    assert mgr.execute(nxt.id, now=OPEN_NY)["blocked"] is True
    released = aq.ApprovalQueue().release_unresolved(
        q.id, by="owner", note="checked Alpaca by client id: no such order")
    assert released.status == "rejected" and released.released_by == "owner"
    assert "checked Alpaca" in released.rejection_reason
    client._accept = _order("after-release")
    again = queue()
    assert mgr.execute(again.id, now=OPEN_NY)["ok"] is True


# ── the owner's routes ───────────────────────────────────────────────────────

ROUTES = [("GET", "/api/execute/unresolved"), ("POST", "/api/execute/reconcile"),
          ("POST", "/api/execute/unresolved/x/release?note=checked")]
TOKEN = "test-owner-token-for-order-reconciliation"


@pytest.fixture
def api(monkeypatch, tmp_path):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from backend.main import app
    from src.auth import hardening
    from src.auth import session as sess
    monkeypatch.setenv("ARIA_OWNER_TOKEN", TOKEN)
    monkeypatch.delenv("ARIA_OWNER_TOKEN_HASH", raising=False)
    monkeypatch.delenv("ARIA_TRUST_PROXY", raising=False)
    monkeypatch.setattr(sess.SESSIONS, "store", tmp_path / "sessions.json")
    monkeypatch.setattr(sess.SESSIONS, "session_key_file", tmp_path / ".key")
    sess.SESSIONS._attempts.clear()
    hardening._limiter._hits.clear()
    owner = TestClient(app, client=("203.0.113.44", 54100))
    assert owner.post("/api/auth/login", json={"token": TOKEN}).status_code == 200
    return app, owner, TestClient(app, client=("203.0.113.45", 54101))


@pytest.mark.parametrize("method,path", ROUTES)
def test_the_reconciliation_routes_are_owner_only(api, method, path):
    _, _, anon = api
    assert anon.request(method, path).status_code == 401


def test_the_owner_sees_unresolved_orders_and_can_reconcile_them(api, unknown_row, monkeypatch):
    import backend.main as bm
    _, owner, _ = api
    q, client, mgr = unknown_row
    body = owner.get("/api/execute/unresolved").json()
    assert [o["id"] for o in body["orders"]] == [q.id]
    assert body["orders"][0]["client_order_id"].startswith("aria-q-")

    monkeypatch.setattr(bm, "_get_order_manager", lambda: mgr)
    client._lookups = [_order("b", "filled", qty=1, price=10.0)]
    report = owner.post("/api/execute/reconcile").json()
    assert report["settled"] == {q.id: "filled"}
    assert owner.get("/api/execute/unresolved").json()["count"] == 0


def test_a_release_needs_a_note(api, unknown_row):
    _, owner, _ = api
    q, _, _ = unknown_row
    assert owner.post(f"/api/execute/unresolved/{q.id}/release?note=%20").status_code == 400
    r = owner.post(f"/api/execute/unresolved/{q.id}/release?note=checked%20Alpaca")
    assert r.status_code == 200 and r.json()["trade"]["status"] == "rejected"


def test_a_released_or_reconciled_order_settles_its_playbook(tmp_path, monkeypatch):
    from src.execution import approval_queue as aq
    client = FakeClient(lookups=[None] * 3)
    reflex, engine = _reflex_world(tmp_path, monkeypatch, client)
    engine.scan_once()
    pb = reflex.load_playbooks()[0]
    assert pb["status"] == "unknown"
    assert reflex.reconcile_unknown_playbooks() == {}, "still unresolved: unchanged"
    aq.ApprovalQueue().release_unresolved(pb["trade_id"], by="owner", note="not at the broker")
    assert reflex.reconcile_unknown_playbooks() == {"BTC-USD": "rejected"}
    assert _arm_again(reflex) is not None, "settled: the ticker may arm again"
