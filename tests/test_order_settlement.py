"""
tests/test_order_settlement.py
==============================
S1.3 (WORK_PLAN): the approval-queue order path — the owner's approve button,
the desk's auto-executor and the reflex lane — sent no client_order_id, so a
submit that timed out after Alpaca accepted the order was recorded REJECTED.
The desk then said "failed", which is not terminal for a reflex playbook: it
stayed armed, and the next crossing could send a second order.

Every order now carries a deterministic client_order_id, written to the queue
row ("submitting") before the POST, and an unknown submit is looked up by it:

    found              → continues as what the broker says (filled, working, …)
    not found / can't ask → UNKNOWN: absence is not proof (Codex review of
                  e4f01ad: three 404s, then the order visible on the fourth
                  look). The queue row stays unknown, the desk records
                  "unknown", the reflex playbook retires as "unknown", and no
                  lane sends another order for the instrument until the
                  broker's answer is found (reconcile) or the owner releases it

The broker here is the real AlpacaBroker adapter over a fake alpaca-py client,
so the timeout classification and the 404 handling are the adapter's own.
Every gate (session, pre-trade risk, kill switch, mode) runs for real.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))

pytest.importorskip("alpaca")
pytest.importorskip("exchange_calendars")

from src.execution.alpaca_broker import AlpacaBroker                         # noqa: E402
from src.execution.broker_base import (AccountInfo, AssetClass, OrderRequest,  # noqa: E402
                                       OrderSide, OrderType)
from src.execution.order_manager import OrderManager, queue_client_order_id   # noqa: E402

UTC = timezone.utc
OPEN_NY = datetime(2026, 10, 6, 15, 0, tzinfo=UTC)     # a Tuesday, 11:00 New York


# ── a fake alpaca-py TradingClient ──────────────────────────────────────────

class ReadTimeout(Exception):
    """A transport failure: no HTTP status, so the order's fate is unknown."""


class NotFound(Exception):
    status_code = 404


class Unprocessable(Exception):
    status_code = 422


def _order(oid="b-1", status="filled", qty=1.0, price=10.0):
    return SimpleNamespace(id=oid, status=status, filled_qty=str(qty) if status == "filled" else "0",
                           filled_avg_price=str(price) if status == "filled" else None,
                           filled_at=None)


class FakeClient:
    """Submits always time out unless `accept` is set; lookups follow `lookups`,
    one entry per call (an Exception instance is raised, None is "no such
    order", anything else is returned). `_retry = 0`: like the adapter's own
    order client, it makes one attempt per request."""
    _base_url = "https://paper-api.alpaca.markets"
    _retry = 0

    def __init__(self, lookups=(), submit_error=ReadTimeout("read timed out"), accept=None):
        self.sent, self.asked = [], []
        self._lookups = list(lookups)
        self._submit_error = submit_error
        self._accept = accept

    def submit_order(self, order_data):
        self.sent.append(order_data)
        if self._accept is not None:
            return self._accept
        raise self._submit_error

    def get_order_by_client_id(self, client_id):
        self.asked.append(client_id)
        nxt = self._lookups.pop(0) if self._lookups else NotFound("order not found")
        if isinstance(nxt, Exception):
            raise nxt
        if nxt is None:
            raise NotFound("order not found")
        return nxt

    def get_order_by_id(self, order_id):
        return _order(order_id)


class FakeAlpaca(AlpacaBroker):
    """The real adapter's submit/lookup code over FakeClient; account, positions
    and a quote timed at `quote_at` so the pre-trade gate can price the order."""

    def __init__(self, client, quote_at=OPEN_NY):
        self._client = client
        self._is_paper = True
        self._stock_data = self._crypto_data = None
        self.quote_at = quote_at

    def is_connected(self):
        return True

    def get_account(self):
        return AccountInfo(broker="alpaca", cash=1e6, portfolio_value=1e6,
                           buying_power=1e6, currency="USD")

    def get_positions(self):
        return []

    def get_open_orders(self):
        return []

    def get_quote(self, ticker):
        at = self.quote_at or datetime.now(UTC)
        return {"bid": 9.99, "ask": 10.01, "mid": 10.0, "timestamp": at.isoformat()}


@pytest.fixture
def queue(tmp_path, monkeypatch):
    from src.execution import approval_queue as aq
    monkeypatch.setattr(aq, "QUEUE_FILE", tmp_path / "q.json")

    def push(ticker="AAPL", ac=AssetClass.EQUITY, qty=1):
        return aq.ApprovalQueue().push(OrderRequest(ticker=ticker, side=OrderSide.BUY, qty=qty,
                                                    order_type=OrderType.MARKET, asset_class=ac),
                                       broker="alpaca")
    push.get = lambda tid: aq.ApprovalQueue().get_by_id(tid)
    return push


def manager(broker):
    mgr = OrderManager(alpaca=broker)
    mgr.SETTLE_WAIT_S = 0           # the lookups themselves are what is under test
    return mgr


# ── the key ─────────────────────────────────────────────────────────────────

def test_every_queued_order_is_sent_with_a_deterministic_client_order_id(queue):
    q, other = queue(), queue()
    cid = queue_client_order_id(q)
    assert cid == queue_client_order_id(queue.get(q.id)), "same trade, same key"
    assert cid != queue_client_order_id(other), "each trade its own key"
    assert cid.startswith("aria-q-") and len(cid) <= 48     # Alpaca allows 128

    client = FakeClient(accept=_order("b-9", "accepted"))
    res = manager(FakeAlpaca(client)).execute(q.id, now=OPEN_NY)
    assert res["ok"] and res["status"] == "submitted"
    assert client.sent[0].client_order_id == cid, "the alpaca-py request carries it"
    assert res["client_order_id"] == cid
    assert queue.get(q.id).client_order_id == cid


# ── timeout, then settled by client id ───────────────────────────────────────

def test_a_timeout_then_the_order_is_found_filled_is_a_fill(queue):
    q = queue()
    client = FakeClient(lookups=[_order("b-1", "filled", qty=1, price=10.02)])
    res = manager(FakeAlpaca(client)).execute(q.id, now=OPEN_NY)
    assert res["ok"] is True, res
    assert res["status"] == "filled" and res["broker_order_id"] == "b-1"
    assert res["fill_price"] == 10.02 and res["filled_qty"] == 1
    assert "found" in res["settled"]
    assert len(client.sent) == 1, "settled by lookup, never resent"
    assert client.asked == [queue_client_order_id(q)]
    t = queue.get(q.id)
    assert t.status == "executed" and t.broker_order_id == "b-1"
    assert t.client_order_id == queue_client_order_id(q)


def test_a_timeout_then_the_order_is_found_working_is_accepted_not_filled(queue):
    q = queue()
    client = FakeClient(lookups=[_order("b-2", "new")])
    res = manager(FakeAlpaca(client)).execute(q.id, now=OPEN_NY)
    assert res["ok"] is True and res["status"] == "submitted"
    assert queue.get(q.id).status == "executed"


@pytest.mark.parametrize("alpaca_status,ours", [("rejected", "rejected"),
                                                 ("canceled", "cancelled")])
def test_a_timeout_then_the_broker_refused_it_after_all_is_a_rejection(queue, alpaca_status, ours):
    q = queue()
    res = manager(FakeAlpaca(FakeClient(lookups=[_order("b-6", alpaca_status)]))).execute(
        q.id, now=OPEN_NY)
    assert res["ok"] is False and res["status"] == ours
    t = queue.get(q.id)
    assert t.status == "rejected" and f"found by client id as {ours}" in t.rejection_reason


def test_a_timeout_then_not_found_stays_unknown_because_absence_is_not_proof(queue):
    """Codex review of e4f01ad: this case used to be recorded REJECTED with
    "nothing was placed" — and the order then appeared on the next look."""
    q = queue()
    mgr = manager(FakeAlpaca(client := FakeClient(lookups=[None, None, None])))
    res = mgr.execute(q.id, now=OPEN_NY)
    assert res["ok"] is False and res["unknown"] is True and res["status"] == "unknown"
    assert "absence is not proof" in res["error"]
    assert len(client.sent) == 1
    assert len(client.asked) == mgr.SETTLE_LOOKUPS
    t = queue.get(q.id)
    assert t.status == "unknown" and t.client_order_id == queue_client_order_id(q)


def test_a_timeout_and_a_failed_lookup_is_unknown_and_never_resent(queue):
    q = queue()
    client = FakeClient(lookups=[ReadTimeout("lookup timed out")] * 3)
    mgr = manager(FakeAlpaca(client))
    res = mgr.execute(q.id, now=OPEN_NY)
    assert res["ok"] is False and res["unknown"] is True
    assert res["status"] == "unknown"
    assert res["client_order_id"] == queue_client_order_id(q)
    assert "UNKNOWN" in res["error"] and "Not resent" in res["error"]
    t = queue.get(q.id)
    assert t.status == "unknown" and t.client_order_id == queue_client_order_id(q)

    again = mgr.execute(q.id, now=OPEN_NY)
    assert again["ok"] is False and "not approvable" in again["error"]
    from src.execution.approval_queue import ApprovalQueue
    assert ApprovalQueue().cancel(q.id) is False, "an order that may exist is not cancellable here"
    assert len(client.sent) == 1, "an UNKNOWN order is never sent twice"


@pytest.mark.parametrize("answers,expect", [
    ([None, None, ReadTimeout("x")], "unknown"),
    ([ReadTimeout("x"), None, None], "unknown"),
    ([ReadTimeout("x"), _order("b-3", "filled")], "filled"),
    ([None, _order("b-4", "new")], "submitted"),
], ids=["not-found-then-error", "error-then-not-found", "error-then-found", "late-found"])
def test_only_a_found_answer_settles_a_timeout(queue, answers, expect):
    q = queue()
    res = manager(FakeAlpaca(FakeClient(lookups=answers))).execute(q.id, now=OPEN_NY)
    assert res["status"] == expect, res


def test_a_definite_refusal_is_not_looked_up(queue):
    """A 4xx is the broker saying no: the order does not exist. Unchanged."""
    q = queue()
    client = FakeClient(submit_error=Unprocessable("insufficient buying power"))
    res = manager(FakeAlpaca(client)).execute(q.id, now=OPEN_NY)
    assert res["ok"] is False and res["status"] == "rejected"
    assert client.asked == []
    assert queue.get(q.id).status == "rejected"


def test_a_duplicate_id_refusal_means_the_broker_already_holds_it(queue):
    """alpaca-py re-POSTs on a 504 by itself. When the first POST was accepted
    behind the timeout, the retry comes back 422 for the duplicate id: the
    order exists, so it is looked up, not recorded rejected."""
    q = queue()
    dup = Unprocessable('{"code":40010001,"message":"client_order_id must be unique"}')
    client = FakeClient(submit_error=dup, lookups=[_order("b-7", "filled")])
    res = manager(FakeAlpaca(client)).execute(q.id, now=OPEN_NY)
    assert res["ok"] is True and res["status"] == "filled" and res["broker_order_id"] == "b-7"
    assert client.asked == [queue_client_order_id(q)]
    assert queue.get(q.id).status == "executed"


def test_a_broker_that_cannot_look_orders_up_leaves_it_unknown(queue):
    from src.execution.broker_base import OrderResult, OrderStatus

    class NoLookup:
        name, paper = "nolookup", True
        sent = []

        def is_connected(self):
            return True

        def get_account(self):
            return AccountInfo(broker="x", cash=1e6, portfolio_value=1e6, buying_power=1e6)

        def get_positions(self):
            return []

        def get_open_orders(self):
            return []

        def get_quote(self, ticker):
            return {"bid": 9.99, "ask": 10.01, "mid": 10.0, "timestamp": OPEN_NY.isoformat()}

        def submit_order(self, req):
            self.sent.append(req)
            return OrderResult(broker_order_id="", status=OrderStatus.UNKNOWN,
                               error_message="timed out")

    q = queue()
    res = manager(NoLookup()).execute(q.id, now=OPEN_NY)
    assert res["unknown"] is True and "cannot look orders up" in res["error"]
    assert queue.get(q.id).status == "unknown"


# ── every gate still stands in front of the send ─────────────────────────────

def test_a_closed_market_sends_nothing_and_asks_nothing(queue):
    q = queue()
    client = FakeClient(lookups=[_order()])
    res = manager(FakeAlpaca(client)).execute(q.id, now=datetime(2026, 10, 4, 15, 0, tzinfo=UTC))
    assert res["ok"] is False and res.get("session_closed")
    assert client.sent == [] and client.asked == []


@pytest.mark.usefixtures("clean_trading_state")
def test_the_kill_switch_refuses_before_any_send_or_lookup(queue):
    from src.execution import kill_switch
    q = queue()
    client = FakeClient(lookups=[_order()])
    kill_switch.engage("test", actor="test")
    try:
        res = manager(FakeAlpaca(client)).execute(q.id, now=OPEN_NY)
    finally:
        kill_switch.release(actor="test")
    assert res["ok"] is False and "KILL SWITCH" in res["error"]
    assert client.sent == [] and client.asked == []
    assert queue.get(q.id).status == "rejected"


@pytest.mark.usefixtures("clean_trading_state")
def test_the_desk_gate_passes_the_lookup_and_still_gates_the_send(queue, monkeypatch):
    """Through gate_for_desk — the wrapper every autonomous order goes
    through. The lookup is a classified READ, so it passes; in RESEARCH mode
    the send itself is refused and nothing is looked up."""
    from src.trading import access, modes
    monkeypatch.setenv("ALPACA_PAPER", "true")
    modes.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)

    modes.STATE_FILE.write_text('{"mode": "PAPER"}', encoding="utf-8")
    q = queue()
    client = FakeClient(lookups=[_order("b-4", "filled")])
    res = manager(access.gate_for_desk(FakeAlpaca(client))).execute(q.id, now=OPEN_NY)
    assert res["ok"] is True and res["status"] == "filled", res
    assert len(client.sent) == 1 and len(client.asked) == 1

    modes.STATE_FILE.write_text('{"mode": "RESEARCH"}', encoding="utf-8")
    q2 = queue()
    client2 = FakeClient(lookups=[_order()])
    res = manager(access.gate_for_desk(FakeAlpaca(client2))).execute(q2.id, now=OPEN_NY)
    assert res["ok"] is False and "RESEARCH" in res["error"]
    assert client2.sent == [] and client2.asked == []


# ── the desk records it ──────────────────────────────────────────────────────

def _desk(tmp_path, monkeypatch, execute_result):
    from src.desk import auto_executor as ae
    from src.desk import notify, position_manager
    from src.execution import approval_queue as aq
    monkeypatch.setattr(aq, "QUEUE_FILE", tmp_path / "desk-q.json")
    monkeypatch.setattr(ae, "EXEC_LOG", tmp_path / "executions.jsonl")
    monkeypatch.setattr(ae, "get_order_manager",
                        lambda *a, **k: SimpleNamespace(execute=lambda trade_id: execute_result))
    monkeypatch.setattr(ae, "account_snapshot", lambda: {"connected": True, "equity": 1e5})
    monkeypatch.setattr(ae.AutoExecutor, "gates",
                        lambda self, account=None: {"auto_allowed": True, "budget_left": 1e9})
    notes, counted, tracked = [], [], []
    monkeypatch.setattr(ae.AutoExecutor, "_notify", lambda self, title, msg: notes.append(title))
    monkeypatch.setattr(ae.AutoExecutor, "_alert_feed", lambda self, *a: None)
    monkeypatch.setattr(ae, "record_trade", lambda notional: counted.append(notional))
    monkeypatch.setattr(position_manager.PositionManager, "track_entry",
                        lambda self, *a: tracked.append(a))
    monkeypatch.setattr(notify, "push", lambda *a, **k: None)
    entry = {"ticker": "BTC-USD", "side": "buy", "qty": 0.01, "asset_class": "crypto",
             "price": 100.0, "notional": 1.0, "conviction": 80, "thesis": "t"}
    ex = ae.AutoExecutor({"auto_execute": True})
    rec = ex.execute_slate([entry], [])[0]
    logged = [json.loads(x) for x in (tmp_path / "executions.jsonl").read_text().splitlines()]
    return rec, logged, notes, counted, tracked


def test_the_desk_records_an_unknown_order_as_unknown_not_failed(tmp_path, monkeypatch):
    rec, logged, notes, counted, tracked = _desk(tmp_path, monkeypatch, {
        "ok": False, "unknown": True, "status": "unknown",
        "client_order_id": "aria-q-abc", "error": "order state UNKNOWN after timeout"})
    assert rec["mode"] == "unknown" and rec["order_status"] == "unknown"
    assert rec["client_order_id"] == "aria-q-abc" and rec["fill_confirmed"] is False
    assert logged[-1]["mode"] == "unknown" and logged[-1]["client_order_id"] == "aria-q-abc"
    assert counted == [1.0], "it may have traded: it counts against the day's caps"
    assert tracked == [], "nothing confirmed a position"
    assert any("UNKNOWN" in n for n in notes)


def test_the_desk_records_the_client_id_on_fills_and_failures(tmp_path, monkeypatch):
    rec, *_ = _desk(tmp_path, monkeypatch, {"ok": True, "status": "filled", "fill_price": 101.0,
                                            "broker_order_id": "b", "client_order_id": "aria-q-1"})
    assert rec["mode"] == "auto" and rec["client_order_id"] == "aria-q-1"
    rec, *_ = _desk(tmp_path, monkeypatch, {"ok": False, "status": "rejected",
                                            "client_order_id": "aria-q-2", "error": "no"})
    assert rec["mode"] == "failed" and rec["client_order_id"] == "aria-q-2"


# ── the reflex lane, end to end through the real order path ─────────────────

def _reflex_world(tmp_path, monkeypatch, client):
    """A crossed BTC-USD playbook, every reflex stage passing, and the real
    AutoExecutor._handle → OrderManager.execute → AlpacaBroker adapter. Crypto
    trades around the clock, so the real session gate passes at any hour; the
    quote is timed now so the real pre-trade gate can price it."""
    from src.desk import auto_executor as ae
    from src.desk import config as desk_config
    from src.desk import desk_daemon, notify, position_manager, reflex
    from src.execution import alpaca_broker
    from src.execution import approval_queue as aq
    monkeypatch.setattr(aq, "QUEUE_FILE", tmp_path / "q.json")
    monkeypatch.setattr(reflex, "PLAYBOOKS_FILE", tmp_path / "playbooks.json")
    monkeypatch.setattr(ae, "EXEC_LOG", tmp_path / "executions.jsonl")
    monkeypatch.setattr(reflex, "load_config", lambda: {**desk_config.DEFAULTS, "reflex_enabled": True})
    monkeypatch.setattr(reflex, "is_expired", lambda p: False)
    monkeypatch.setattr(alpaca_broker, "quotable", lambda t, now=None: True)
    monkeypatch.setattr(desk_daemon, "instrument_open", lambda *a, **k: True)
    monkeypatch.setattr(reflex, "load_data_json", lambda name: {"BTC-USD": {"current_price": 100.0}})
    monkeypatch.setattr(reflex, "signals_freshness", lambda: {"fresh": True, "why": ""})
    monkeypatch.setattr(reflex, "confidence_ok", lambda sig, side, floor: (True, 0.9))

    broker = FakeAlpaca(client, quote_at=None)
    mgr = manager(broker)
    monkeypatch.setattr(ae, "get_order_manager", lambda *a, **k: mgr)
    monkeypatch.setattr(ae, "account_snapshot", lambda: {"connected": True, "equity": 1e6})
    monkeypatch.setattr(ae.AutoExecutor, "gates",
                        lambda self, account=None: {"auto_allowed": True, "budget_left": 1e9})
    monkeypatch.setattr(ae.AutoExecutor, "_notify", lambda self, *a: None)
    monkeypatch.setattr(ae.AutoExecutor, "_alert_feed", lambda self, *a: None)
    monkeypatch.setattr(ae, "record_trade", lambda notional: None)
    monkeypatch.setattr(position_manager.PositionManager, "track_entry", lambda self, *a: None)
    monkeypatch.setattr(notify, "push", lambda *a, **k: None)

    reflex.save_playbooks([{
        "ticker": "BTC-USD", "side": "buy", "trigger_price": 100.0, "stop": None,
        "target": None, "conviction": 80, "debate_id": "", "source": "signal",
        "armed_at": "2026-10-16T09:00:00", "expires_at": "2999-01-01T00:00:00",
        "asset_class": "crypto", "thesis": "t", "status": "armed"}])
    engine = reflex.ReflexEngine.__new__(reflex.ReflexEngine)
    engine.cfg = reflex.load_config()
    engine._last_price = {"BTC-USD": 99.0}
    engine._running = False
    engine._thread = None
    monkeypatch.setattr(engine, "_quote", lambda ticker, m: 101.0, raising=False)
    monkeypatch.setattr(engine, "_size", lambda pb, price, sig, account: {
        "ticker": "BTC-USD", "side": "buy", "qty": 0.01, "price": price, "notional": 1.01,
        "conviction": 80, "thesis": "t", "stop": None, "target": None,
        "asset_class": "crypto", "debate_id": "", "source": "reflex:signal"}, raising=False)
    monkeypatch.setattr(engine, "_risk_check", lambda entry, account: True, raising=False)
    monkeypatch.setattr(engine, "_veto", lambda entry, price, sig: None, raising=False)
    return reflex, engine


@pytest.mark.parametrize("lookups,status,mode", [
    ([_order("b-5", "filled", qty=0.01, price=101.0)], "fired", "auto"),
    # Not found is not "nothing was placed": the playbook retires as unknown.
    ([None, None, None], "unknown", "unknown"),
    ([ReadTimeout("lookup timed out")] * 3, "unknown", "unknown"),
], ids=["found-filled", "not-found", "lookup-fails"])
def test_a_reflex_fire_that_times_out_is_settled_on_the_playbook(tmp_path, monkeypatch,
                                                                  lookups, status, mode):
    client = FakeClient(lookups=lookups)
    reflex, engine = _reflex_world(tmp_path, monkeypatch, client)
    out = engine.scan_once()
    assert len(client.sent) == 1 and out[0]["mode"] == mode, out
    pb = reflex.load_playbooks()[0]        # read back from the real file
    assert pb["status"] == status
    assert pb["client_order_id"].startswith("aria-q-")
    if status == "unknown":
        assert pb["last_attempt"]["uncertain"] is True
        assert pb["last_attempt"]["executed"] is None
        logged = [json.loads(x) for x in (tmp_path / "executions.jsonl").read_text().splitlines()]
        assert logged[-1]["mode"] == "unknown" and logged[-1]["lane"] == "reflex"
        assert logged[-1]["client_order_id"] == pb["client_order_id"]


def test_an_unknown_reflex_order_is_never_followed_by_a_second(tmp_path, monkeypatch):
    client = FakeClient(lookups=[ReadTimeout("lookup timed out")] * 9)
    reflex, engine = _reflex_world(tmp_path, monkeypatch, client)
    engine.scan_once()
    assert reflex.load_playbooks()[0]["status"] == "unknown"

    # The price falls back and crosses again: the retired playbook does not fire.
    engine._last_price["BTC-USD"] = 99.0
    assert engine.scan_once() == []
    # And the deliberate lane cannot re-arm the ticker while the order is unsettled.
    assert reflex._arm("BTC-USD", "buy", 102.0, None, None, 80, "", "signal", {},
                       reflex.load_config(), "spike again") is None
    assert [p["status"] for p in reflex.load_playbooks()] == ["unknown"]
    assert len(client.sent) == 1


def test_an_unknown_playbook_blocks_only_its_own_ticker(tmp_path, monkeypatch):
    from src.desk import config as desk_config
    from src.desk import reflex
    from src.execution import alpaca_broker
    monkeypatch.setattr(reflex, "PLAYBOOKS_FILE", tmp_path / "playbooks.json")
    monkeypatch.setattr(alpaca_broker, "quotable", lambda t, now=None: True)
    reflex.save_playbooks([{"ticker": "BTC-USD", "side": "buy", "status": "unknown",
                            "client_order_id": "aria-q-x"}])
    cfg = dict(desk_config.DEFAULTS)
    assert reflex._arm("BTC-USD", "buy", 1.0, None, None, 80, "", "signal", {}, cfg, "") is None
    assert reflex._arm("ETH-USD", "buy", 1.0, None, None, 80, "", "signal", {}, cfg, "")
    assert sorted(p["ticker"] for p in reflex.load_playbooks()) == ["BTC-USD", "ETH-USD"]
