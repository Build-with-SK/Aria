"""
tests/test_claude_final_review_probes.py
========================================
Reproductions for defects found in the Claude final architecture review of
`codex/aria-final-integration` @ edc2796 (docs/CLAUDE_TRADING_SAFETY_REVIEW.md).

These formerly strict-xfail probes now run as regression tests for the
reviewed repairs. The original failing baseline is retained in release evidence. No probe touches a real broker, account or production file.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.execution.broker_base import (OrderRequest, OrderResult,  # noqa: E402
                                       OrderSide, OrderStatus, OrderType,
                                       AssetClass)

UTC = timezone.utc
TUE_OPEN = datetime(2026, 10, 6, 15, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def isolated_approval_queue(tmp_path, monkeypatch):
    from src.execution import approval_queue as aq
    monkeypatch.setattr(aq, "QUEUE_FILE", tmp_path / "queue.json")


def _freeze(monkeypatch, instant):
    from src.markets import sessions as S

    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return instant if tz is None else instant.astimezone(tz)
    monkeypatch.setattr(S, "datetime", Frozen)


# ── F-01: the emergency halt cannot cancel a resting Alpaca order ───────────
#
# /api/trading/halt -> killswitch.halt(broker=access.paper_adapter()).
# LegacyBrokerAdapter.get_open_orders() keys each order by
# `o.get("client_order_id") or o.get("id")`; AlpacaBroker.get_open_orders()
# never returns a client_order_id, so the key is Alpaca's BROKER order id.
# LegacyBrokerAdapter.cancel_order(key) then looks that broker id up as a
# CLIENT order id (get_order_by_client_id), Alpaca answers 404, the adapter
# returns False, and the halt files the order under "kept_protective".
# The real AlpacaBroker class is used below with a fake alpaca-py client.

class _APIError(Exception):
    def __init__(self, code):
        super().__init__(f"HTTP {code}")
        self.status_code = code


class _Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _FakeTradingClient:
    """The slice of alpaca-py's TradingClient the adapter calls."""
    _base_url = "https://paper-api.alpaca.markets"

    def __init__(self, orders):
        self.orders = {o.id: o for o in orders}
        self.cancelled = []

    def get_account(self):
        return _Obj(cash="1000", portfolio_value="1000", buying_power="1000",
                    daytrade_count=0)

    def get_all_positions(self):
        return []

    def get_orders(self, filter=None):
        return [o for o in self.orders.values() if o.status == "new"]

    def get_order_by_client_id(self, client_order_id):
        for o in self.orders.values():
            if o.client_order_id == client_order_id:
                return o
        raise _APIError(404)

    def get_order_by_id(self, order_id):
        if order_id in self.orders:
            return self.orders[order_id]
        raise _APIError(404)

    def cancel_order_by_id(self, order_id):
        if order_id not in self.orders:
            raise _APIError(404)
        self.orders[order_id].status = "canceled"
        self.cancelled.append(order_id)


def _alpaca_with(client):
    from src.execution.alpaca_broker import AlpacaBroker
    b = AlpacaBroker.__new__(AlpacaBroker)
    b._api_key, b._secret_key, b._is_paper = "k", "s", True
    b._client = b._submit_client = client
    b._stock_data = b._crypto_data = None
    return b


@pytest.mark.usefixtures("clean_trading_state")
def test_f01_halt_cancels_a_resting_alpaca_entry(monkeypatch):
    pytest.importorskip("alpaca")
    from src.execution import kill_switch
    from src.trading import access, killswitch, modes
    monkeypatch.setenv("ALPACA_PAPER", "true")
    modes.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    modes.STATE_FILE.write_text('{"mode": "PAPER"}', encoding="utf-8")
    entry = _Obj(id="9f1c-broker-uuid", client_order_id="aria-q-abc", symbol="AAPL",
                 side=_Obj(value="buy"), type=_Obj(value="limit"), qty="5",
                 stop_price=None, limit_price="100", submitted_at=TUE_OPEN,
                 status="new", legs=None, filled_qty="0", filled_avg_price=None,
                 filled_at=None)
    client = _FakeTradingClient([entry])
    adapter = access.paper_adapter(_alpaca_with(client))
    try:
        report = killswitch.halt("review probe", actor="test", broker=adapter)
    finally:
        kill_switch.release(actor="test")
    # What the defect produces today: nothing cancelled, and a flat-account
    # BUY entry reported as "kept_protective".
    assert client.cancelled == ["9f1c-broker-uuid"], report["steps"]["cancel"]


# ── F-02: a failed desk exit leaves the position unprotected ────────────────
#
# PositionManager._close cancels every open order on the ticker (the bracket)
# BEFORE it knows the exit will fill. When the market exit does not fill in the
# poll window (a halt, an auction), _close cancels the exit and returns None —
# but the exit's queue row stays "executed / submitted", non-terminal. The same
# tick's bracket healing then calls place_protective_orders, whose reduce-only
# check refuses: "a durable exit claim is not visible at the broker". The
# management tick reconciles only AFTER PositionManager.tick(), so the
# position holds no protective order until the next tick at the earliest.

class _HaltedBook:
    """Alpaca-named book whose market orders rest unfilled (a trading halt)."""
    name = "Alpaca"
    paper = True

    def __init__(self, holdings):
        self.holdings = dict(holdings)
        self.resting = {}
        self.status_of = {}
        self.client_orders = {}
        self._n = 0

    def _id(self):
        self._n += 1
        return f"o{self._n}"

    def is_connected(self):
        return True

    def get_account(self):
        from src.execution.broker_base import AccountInfo
        return AccountInfo(broker="Alpaca", cash=1e6, portfolio_value=1e6,
                           buying_power=1e6, currency="USD")

    def get_quote(self, ticker):
        return {"bid": 9.99, "ask": 10.01, "mid": 10.0, "timestamp": TUE_OPEN.isoformat()}

    def get_positions(self):
        return [{"ticker": t, "qty": abs(q), "side": "long" if q > 0 else "short"}
                for t, q in self.holdings.items() if q]

    def get_open_orders(self):
        return [dict(o) for o in self.resting.values()]

    def get_order_status(self, oid):
        return OrderResult(oid, self.status_of.get(oid, OrderStatus.UNKNOWN))

    def get_order_by_client_id(self, cid):
        return self.client_orders.get(cid)

    def submit_order(self, req):
        side = req.side.value
        oid = self._id()
        self.resting[oid] = {"id": oid, "ticker": req.ticker, "side": side,
                             "qty": req.qty, "order_type": req.order_type.value}
        self.status_of[oid] = OrderStatus.SUBMITTED
        r = OrderResult(oid, OrderStatus.SUBMITTED)
        if req.client_order_id:
            self.client_orders[req.client_order_id] = r
        return r

    def submit_oco_exit(self, ticker, qty, entry_side, stop, target, *, client_order_id=None):
        side = "sell" if entry_side in ("buy", "long") else "buy"
        pid, leg = self._id(), self._id()
        self.resting[pid] = {"id": pid, "ticker": ticker, "side": side, "qty": qty,
                             "order_type": "limit", "limit_price": target}
        self.resting[leg] = {"id": leg, "ticker": ticker, "side": side, "qty": qty,
                             "order_type": "stop", "stop_price": stop, "parent_id": pid}
        self.status_of[pid] = OrderStatus.SUBMITTED
        if client_order_id:
            self.client_orders[client_order_id] = OrderResult(pid, OrderStatus.SUBMITTED)
        return pid

    def cancel_order(self, oid):
        o = self.resting.pop(oid, None)
        if o is None:
            return False
        for k in [k for k, v in self.resting.items() if v.get("parent_id") == oid]:
            self.resting.pop(k, None)
        self.status_of[oid] = OrderStatus.CANCELLED
        r = next((cid for cid, res in self.client_orders.items()
                  if res.broker_order_id == oid), None)
        if r:
            self.client_orders[r] = OrderResult(oid, OrderStatus.CANCELLED)
        return True


class _DeskMgr:
    """What PositionManager._close reaches for on the desk's order manager."""
    def __init__(self, book):
        self._alpaca = book

    def open_orders_by_ticker(self):
        out = {}
        for o in self._alpaca.get_open_orders():
            out.setdefault(o["ticker"], []).append(o)
        return out

    def _await_fill(self, broker, result, **k):
        return result                      # the poll window passes, no fill


def _failed_exit_then_heal(monkeypatch):
    from src.desk import position_manager as pm
    from src.execution.order_manager import OrderManager
    _freeze(monkeypatch, TUE_OPEN)
    book = _HaltedBook({"AAPL": 10})
    om = OrderManager(alpaca=book)
    om.place_protective_orders("AAPL", 10, "buy", stop=9.0, target=12.0)
    assert any(o["order_type"] == "stop" for o in book.resting.values())

    pmgr = pm.PositionManager.__new__(pm.PositionManager)
    pmgr.cfg = {}
    for name in ("_log_execution", "_log_closed", "_update_scorecard", "_record_outcome_memory"):
        monkeypatch.setattr(pmgr, name, lambda rec: None, raising=False)
    monkeypatch.setattr("src.desk.notify.push", lambda msg, cfg: None)
    monkeypatch.setattr("src.execution.live_guard.paper_confirmed", lambda b: True)
    tracked = {"AAPL": {"asset_class": "equity", "side": "long", "entry_price": 10.0,
                        "qty": 10, "stop": 9.0, "target": 12.0}}
    out = pmgr._close(tracked, "AAPL", {"qty": 10, "side": "long"}, _DeskMgr(book),
                      reason="target hit")
    assert out is None, "the exit did not fill"
    assert book.holdings["AAPL"] == 10 and not book.resting, \
        "position still held, bracket AND exit both cancelled"
    # The same tick's bracket healing, exactly as tick() calls it:
    om.place_protective_orders("AAPL", 10, "buy", stop=9.0, target=12.0)
    return book, om


def test_f02_failed_exit_does_not_leave_the_position_naked(monkeypatch):
    book, _ = _failed_exit_then_heal(monkeypatch)
    assert any(o["order_type"] == "stop" for o in book.resting.values()), \
        "10 AAPL held with no protective stop at the broker"


def test_f02_control_reconcile_first_restores_protection(monkeypatch):
    """Control: the protection comes back once the cancelled exit is reconciled
    — which the management tick does only after PositionManager.tick()."""
    book, om = _failed_exit_then_heal(monkeypatch)
    assert any(o["order_type"] == "stop" for o in book.resting.values())
    om.reconcile_unresolved()
    om.place_protective_orders("AAPL", 10, "buy", stop=9.0, target=12.0)
    assert any(o["order_type"] == "stop" for o in book.resting.values())


# ── F-03: the live gate re-queues every refused order against Alpaca ────────

class _LiveIbkr:
    name = "IBKR"
    paper = False
    PAPER_ENV_VAR = "IBKR_PAPER"

    def endpoint_is_paper(self):
        return False

    def submit_order(self, req):                 # pragma: no cover - must not run
        raise AssertionError("live order reached the broker")


def test_f03_refused_ibkr_order_is_requeued_for_ibkr(monkeypatch):
    from src.execution.approval_queue import ApprovalQueue
    from src.execution.live_guard import PaperOnlyBroker
    monkeypatch.setenv("IBKR_PAPER", "false")
    gate = PaperOnlyBroker(_LiveIbkr())
    r = gate.submit_order(OrderRequest(ticker="ES", side=OrderSide.BUY, qty=1,
                                       order_type=OrderType.MARKET,
                                       asset_class=AssetClass.FUTURES))
    assert r.status == OrderStatus.REJECTED
    rows = ApprovalQueue().get_pending()
    assert len(rows) == 1
    assert rows[0].broker == "ibkr", f"re-queued for {rows[0].broker!r}"


# ── F-04: ML resume reuses a row trained before the latest bar ──────────────
#
# run_ml skips every ticker whose row's generated_at is younger than
# ml.resume_max_age_hours (20 h). The row's data_as_of is never compared with
# the bar the ticker's featured data now ends on, so a rerun after a new close
# keeps a prediction made from the previous bar — and stamps the whole pass as
# fresh.

def test_f04_resume_retrains_a_row_whose_bar_is_behind(tmp_path, monkeypatch):
    import json
    from datetime import timedelta
    import pandas as pd
    import main as pipeline
    import src.models.model_trainer as mt
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    made = (datetime.now() - timedelta(hours=2)).isoformat(timespec="seconds")
    (tmp_path / "data" / "ml_predictions.json").write_text(json.dumps(
        {"AAA": {"ticker": "AAA", "generated_at": made, "data_as_of": "2026-10-15"}}),
        encoding="utf-8")
    featured = {"AAA": pd.DataFrame({"Close": [1.0, 2.0, 3.0]},
                                    index=pd.date_range(end="2026-10-16", periods=3, freq="B"))}
    seen = {}

    class _Trainer:
        def __init__(self, cfg):
            pass

        def train_and_predict_all(self, featured, *, skip=(), on_ticker=None):
            seen["skip"] = set(skip)
            return {}

    monkeypatch.setattr(mt, "ModelTrainer", _Trainer)
    monkeypatch.setattr("src.intelligence.recorders.record_ml_predictions",
                        lambda payload, **k: {"recorded": 0})
    pipeline.run_ml(featured, {"ml": {}})
    assert "AAA" not in seen["skip"], "a row made from the 2026-10-15 bar was reused " \
                                      "although the data now ends 2026-10-16"


# ── F-07: inferred loopback ownership ignores the Host header ───────────────
#
# With no owner credential configured, a caller on a loopback socket is the
# owner (policy BASIS_LOOPBACK) whatever Host it asked for. A page the owner
# visits can rebind its own hostname to 127.0.0.1 (DNS rebinding); the browser
# then sends Host: attacker.example from a loopback socket, and the Origin
# check passes because Origin and Host agree by construction. Execution routes
# demand proven ownership; owner-only READS (vault, portfolio) do not.

def test_f07_loopback_fallback_refuses_a_foreign_host(monkeypatch):
    from fastapi.testclient import TestClient
    from backend.main import app
    from src.auth import policy
    for k in ("ARIA_OWNER_TOKEN", "ARIA_OWNER_TOKEN_HASH", "ARIA_TRUST_PROXY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setitem(policy._PROXY_SEEN, "detected", False)
    local = TestClient(app, client=("127.0.0.1", 51900))
    own = local.get("/api/auth/me", headers={"Host": "127.0.0.1:8000"}).json()
    assert own.get("role") == policy.OWNER, "control: a real loopback caller is the owner"
    foreign = local.get("/api/auth/me", headers={"Host": "attacker.example:8000"}).json()
    assert foreign.get("role") != policy.OWNER, foreign


# ── F-09: bracket healing sizes protection from the tracked qty ─────────────
#
# positions.json records qty at adoption, at track_entry (the FILLED part of an
# entry whose fill window closed on a partial) and at a scale-out; nothing
# re-reads it from the broker. _heal_brackets passes that qty to
# place_protective_orders, and reduce-only clips only DOWN. When the rest of a
# partial entry fills later, the stop covers the first fill only — and once a
# stop exists, has_stop is True and healing never revisits it.

def test_f09_healing_protects_the_whole_broker_position(monkeypatch):
    from src.desk import position_manager as pm
    from src.execution.order_manager import OrderManager
    _freeze(monkeypatch, TUE_OPEN)
    monkeypatch.setattr("src.execution.live_guard.paper_confirmed", lambda b: True)
    book = _HaltedBook({"AAPL": 10})              # the entry finished filling: 10 held
    pmgr = pm.PositionManager.__new__(pm.PositionManager)
    pmgr.cfg = {}
    tracked = {"ticker": "AAPL", "side": "long", "qty": 4.0, "stop": 9.0, "target": 12.0,
               "asset_class": "equity"}           # tracked when only 4 had filled
    pmgr._heal_brackets("AAPL", tracked, OrderManager(alpaca=book), [])
    protected = max((o["qty"] for o in book.resting.values()
                     if o["order_type"] == "stop"), default=0)
    assert protected == 10, f"stop covers {protected} of 10 shares held"


# ── F-10: the auto-execution freshness gate ages signals by file mtime ──────
#
# Gate 6 (AutoExecutor.gates -> desk.opinion.signals_freshness) is FRESH when
# data/signals.json was WRITTEN in the last 30 h. data/signals.json is tracked
# in git, so a checkout, merge or reset — including the cutover of this very
# candidate, whose committed copy dates from 2026-09-27 — rewrites the file and
# makes weeks-old signals pass the gate. Rows stamped by the pipeline carry
# data_as_of / generated_at; the gate ignores them.

def test_f10_freshly_written_old_signals_do_not_pass_gate_6(tmp_path, monkeypatch):
    import json
    from datetime import timedelta
    from src.core import world
    from src.desk.opinion import signals_freshness
    monkeypatch.setattr(world, "DATA", tmp_path)
    old = datetime.now() - timedelta(days=21)
    (tmp_path / "signals.json").write_text(json.dumps({
        "AAPL": {"composite_score": 42.0, "current_price": 100.0,
                 "data_as_of": old.date().isoformat(),
                 "generated_at": old.isoformat(timespec="seconds")}}), encoding="utf-8")
    gate = signals_freshness()
    assert gate["fresh"] is False, f"21-day-old signals read as {gate}"


def test_f10_unstamped_signals_written_now_fail_gate_6(tmp_path, monkeypatch):
    """Rows without evidence timestamps are stale even immediately after checkout.
    This is exactly the committed candidate copy after a checkout."""
    import json
    from src.core import world
    from src.desk.opinion import signals_freshness
    monkeypatch.setattr(world, "DATA", tmp_path)
    (tmp_path / "signals.json").write_text(json.dumps({"AAPL": {"composite_score": 42.0}}),
                                           encoding="utf-8")
    assert signals_freshness()["fresh"] is False
