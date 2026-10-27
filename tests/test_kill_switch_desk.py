"""
The kill switch, as the DESK experiences it.

The adapters already refused new orders while the switch was on. What they
allowed — on purpose — was cancellation, and three desk paths cancelled a
position's stop before trying to place something else. With the switch on, the
cancel went through and the re-place was refused: the emergency stop stripped
the protection off the positions it was meant to freeze.

These tests pin the fix:
  * no desk path cancels a working stop unless its replacement is confirmed;
  * with the switch on, the desk cancels pending ENTRY orders only, keeps every
    stop, and the hunt cycle, intents and reflex lane are paused.
"""
from __future__ import annotations

import pytest

from src.execution import kill_switch as ks
from src.execution.broker_base import OrderResult, OrderStatus


@pytest.fixture
def switch(tmp_path, monkeypatch):
    monkeypatch.setattr(ks, "STATE_FILE", tmp_path / "kill_switch.json")
    monkeypatch.delenv(ks.ENV_VAR, raising=False)
    return ks


class _Broker:
    """Paper, connected, records every mutation."""

    def __init__(self, submit_status=OrderStatus.FILLED):
        self.submitted, self.cancelled = [], []
        self.submit_status = submit_status

    def is_connected(self):
        return True

    def submit_order(self, req):
        self.submitted.append(req)
        return OrderResult(broker_order_id="x1", status=self.submit_status,
                           filled_qty=req.qty, avg_fill_price=101.0,
                           error_message="nope" if self.submit_status ==
                           OrderStatus.REJECTED else "")

    def cancel_order(self, oid):
        self.cancelled.append(oid)
        return True

    def get_positions(self):
        return []


class _Mgr:
    def __init__(self, broker, orders=None):
        self._alpaca = broker
        self.orders = orders or {}
        self.placed = []

    def open_orders_by_ticker(self):
        return self.orders

    def place_protective_orders(self, ticker, qty, side, stop, target, **k):
        self.placed.append((ticker, qty, stop, target))
        return {"stop_loss_order": {"broker_order_id": "s-new"},
                "take_profit_order": None}

    def _await_fill(self, broker, result):
        return result


def _engine(monkeypatch):
    from src.desk import position_manager as pm
    eng = pm.PositionManager.__new__(pm.PositionManager)
    eng.cfg = {}
    monkeypatch.setattr(eng, "_log_execution", lambda r: None, raising=False)
    monkeypatch.setattr(eng, "_log_closed", lambda r: None, raising=False)
    monkeypatch.setattr(eng, "_update_scorecard", lambda r: None, raising=False)
    monkeypatch.setattr(eng, "_record_outcome_memory", lambda r: None, raising=False)
    monkeypatch.setattr("src.desk.notify.push", lambda msg, cfg=None: None)
    monkeypatch.setattr("src.execution.live_guard.paper_confirmed", lambda b: True)
    monkeypatch.setattr(pm, "_may_mutate_broker", lambda mgr: True)
    return pm, eng


STOP = {"id": "stop-1", "order_type": "stop", "side": "sell"}
TARGET = {"id": "tgt-1", "order_type": "limit", "side": "sell"}
CRYPTO_POS = {"asset_class": "crypto", "side": "long", "entry_price": 100.0,
              "qty": 2, "stop": 95.0, "target": 120.0}


# ── _close ───────────────────────────────────────────────────────────────────

def test_close_with_switch_engaged_touches_nothing(switch, monkeypatch):
    pm, eng = _engine(monkeypatch)
    broker = _Broker()
    mgr = _Mgr(broker, {"BTC-USD": [STOP]})
    switch.engage("halt")

    out = eng._close({"BTC-USD": dict(CRYPTO_POS)}, "BTC-USD",
                     {"qty": 2, "side": "long"}, mgr, reason="stop hit")

    assert out is None
    assert broker.cancelled == [], "the halt cancelled a protective stop"
    assert broker.submitted == []


def test_rejected_exit_restores_the_stop_it_cancelled(switch, monkeypatch):
    pm, eng = _engine(monkeypatch)
    broker = _Broker(submit_status=OrderStatus.REJECTED)
    mgr = _Mgr(broker, {"BTC-USD": [STOP]})

    out = eng._close({"BTC-USD": dict(CRYPTO_POS)}, "BTC-USD",
                     {"qty": 2, "side": "long"}, mgr, reason="stop hit")

    assert out is None
    assert broker.cancelled == ["stop-1"]
    assert mgr.placed and mgr.placed[0][2] == 95.0, \
        "exit failed after cancelling the stop, and the stop was not put back"


def test_unfilled_exit_restores_the_stop(switch, monkeypatch):
    pm, eng = _engine(monkeypatch)
    broker = _Broker(submit_status=OrderStatus.SUBMITTED)
    mgr = _Mgr(broker, {"BTC-USD": [STOP]})

    eng._close({"BTC-USD": dict(CRYPTO_POS)}, "BTC-USD",
               {"qty": 2, "side": "long"}, mgr, reason="stop hit")

    assert mgr.placed, "unfilled exit left the position without its stop"


def test_scale_out_reprotects_the_runner(switch, monkeypatch):
    pm, eng = _engine(monkeypatch)
    broker = _Broker()
    mgr = _Mgr(broker, {"BTC-USD": [STOP]})
    tracked = {"BTC-USD": dict(CRYPTO_POS)}

    out = eng._close(tracked, "BTC-USD", {"qty": 2, "side": "long"}, mgr,
                     reason="target", fraction=0.5)

    assert out is not None
    assert mgr.placed and mgr.placed[0][1] == pytest.approx(1.0), \
        "the remaining half has no stop after the scale-out"


def test_a_filled_full_exit_does_not_re_place_anything(switch, monkeypatch):
    pm, eng = _engine(monkeypatch)
    broker = _Broker()
    mgr = _Mgr(broker, {"BTC-USD": [STOP]})

    out = eng._close({"BTC-USD": dict(CRYPTO_POS)}, "BTC-USD",
                     {"qty": 2, "side": "long"}, mgr, reason="stop hit")

    assert out is not None and out["exit_price"] == 101.0
    assert mgr.placed == []


# ── the ratchet and bracket healing ──────────────────────────────────────────

def test_ratchet_never_cancels_a_working_stop(switch, monkeypatch):
    pm, eng = _engine(monkeypatch)
    broker = _Broker()
    mgr = _Mgr(broker, {"AAPL": [STOP, TARGET]})

    eng._replace_stop_order("AAPL", {"stop": 99.0, "target": 120, "qty": 5,
                                     "side": "long"}, mgr)

    assert broker.cancelled == []
    assert mgr.placed == []


def test_ratchet_places_a_stop_when_none_is_working(switch, monkeypatch):
    pm, eng = _engine(monkeypatch)
    broker = _Broker()
    mgr = _Mgr(broker, {"AAPL": [TARGET]})

    eng._replace_stop_order("AAPL", {"stop": 99.0, "target": 120, "qty": 5,
                                     "side": "long"}, mgr)

    assert broker.cancelled == ["tgt-1"], "only the lone target may be cleared"
    assert mgr.placed and mgr.placed[0][2] == 99.0


def test_heal_leaves_a_working_stop_alone_even_without_a_target(switch, monkeypatch):
    pm, eng = _engine(monkeypatch)
    broker = _Broker()
    mgr = _Mgr(broker)

    out = eng._heal_brackets("AAPL", {"stop": 95.0, "target": 120.0, "qty": 5,
                                      "side": "long"}, mgr, [STOP])

    assert out is None
    assert broker.cancelled == [] and mgr.placed == []


def test_heal_does_nothing_while_halted(switch, monkeypatch):
    pm, eng = _engine(monkeypatch)
    broker = _Broker()
    mgr = _Mgr(broker)
    switch.engage("halt")

    out = eng._heal_brackets("AAPL", {"stop": 95.0, "qty": 5, "side": "long"},
                             mgr, [TARGET])

    assert out is None
    assert broker.cancelled == [] and mgr.placed == []


# ── the halt sweep ───────────────────────────────────────────────────────────

def test_cancel_pending_entries_keeps_protection(monkeypatch):
    from src.desk import position_manager as pm
    monkeypatch.setattr(pm, "_may_mutate_broker", lambda mgr: True)
    broker = _Broker()
    orders = {
        "AAPL": [STOP, TARGET],                                         # held long
        "MSFT": [{"id": "entry-1", "order_type": "limit", "side": "buy"}],  # not held
        "TSLA": [{"id": "add-1", "order_type": "limit", "side": "sell"},     # held short:
                 {"id": "cover-stop", "order_type": "stop", "side": "buy"}],  # sell adds
    }
    mgr = _Mgr(broker, orders)

    out = pm.cancel_pending_entries(
        mgr, {"AAPL": {"side": "long"}, "TSLA": {"side": "short"}})

    assert sorted(o["id"] for o in out) == ["add-1", "entry-1"]
    assert "stop-1" not in broker.cancelled
    assert "tgt-1" not in broker.cancelled
    assert "cover-stop" not in broker.cancelled


def test_cancel_pending_entries_respects_the_live_gate(monkeypatch):
    from src.desk import position_manager as pm
    monkeypatch.setattr(pm, "_may_mutate_broker", lambda mgr: False)
    broker = _Broker()
    mgr = _Mgr(broker, {"MSFT": [{"id": "e", "order_type": "limit", "side": "buy"}]})
    assert pm.cancel_pending_entries(mgr, {}) == []
    assert broker.cancelled == []


def test_tick_while_halted_cancels_entries_and_closes_nothing(switch, monkeypatch, tmp_path):
    pm, eng = _engine(monkeypatch)
    monkeypatch.setattr(pm, "POSITIONS_FILE", tmp_path / "positions.json")
    eng.save_positions({"AAPL": {"ticker": "AAPL", "side": "long", "qty": 5,
                                 "entry_price": 100.0, "stop": 95.0,
                                 "initial_stop": 95.0, "target": 120.0,
                                 "asset_class": "crypto",
                                 "entry_at": "2026-01-01T10:00:00"}})
    broker = _Broker()
    mgr = _Mgr(broker, {"AAPL": [STOP],
                        "MSFT": [{"id": "entry-1", "order_type": "limit",
                                  "side": "buy"}]})
    from src.desk import auto_executor as ae
    monkeypatch.setattr(ae, "get_order_manager", lambda rebuild=False: mgr)
    monkeypatch.setattr(ae, "account_snapshot", lambda: {
        "connected": True, "equity": 10_000.0,
        "positions": [{"ticker": "AAPL", "qty": 5, "side": "long",
                       "market_value": 450.0, "avg_cost": 100.0}]})
    # Price far through the stop: unhalted, this would close the position.
    monkeypatch.setattr(pm, "load_data_json",
                        lambda name: {"AAPL": {"current_price": 90.0}})
    monkeypatch.setattr(eng, "_current_price", lambda *a: 90.0)
    switch.engage("halt")

    summary = eng.tick()

    assert summary["kill_switch"] is True
    assert summary["entries_cancelled"] == ["entry-1"]
    assert broker.cancelled == ["entry-1"], "a protective order was cancelled"
    assert broker.submitted == [] and mgr.placed == []
    assert summary["exits"] == []


# ── pausing the loops ────────────────────────────────────────────────────────

def test_desk_cycle_is_paused(switch, monkeypatch):
    from src.desk import desk_daemon as dd
    d = dd.DeskDaemon.__new__(dd.DeskDaemon)
    d.cycle_count, d.last_cycle = 3, {}
    import threading
    d._lock = threading.Lock()
    switch.engage("halt")

    d._run_cycle()

    assert d.last_cycle.get("halted") is True
    assert d._lock.acquire(blocking=False), "the cycle ran (it took the lock)"


def test_intent_tick_is_paused(switch, monkeypatch):
    from src.desk import desk_daemon as dd
    called = []
    monkeypatch.setattr("src.desk.intents.check_once",
                        lambda *a, **k: called.append(1) or {})
    switch.engage("halt")
    dd.DeskDaemon.__new__(dd.DeskDaemon)._intent_tick()
    assert called == []


def test_reflex_is_paused_and_playbooks_stay_armed(switch, monkeypatch):
    from src.desk import reflex
    touched = []
    monkeypatch.setattr(reflex, "load_playbooks",
                        lambda: touched.append(1) or [{"status": "armed"}])
    monkeypatch.setattr(reflex, "load_config", lambda: {"reflex_enabled": True})
    switch.engage("halt")
    eng = reflex.ReflexEngine.__new__(reflex.ReflexEngine)
    eng.cfg, eng._last_price = {}, {}

    assert eng.scan_once() == []
    assert touched == [], "reflex read its playbooks while halted"


def test_switch_unreadable_pauses_the_desk(monkeypatch):
    from src.desk import desk_daemon as dd
    from src.desk import position_manager as pm

    def boom():
        raise RuntimeError("disk gone")
    monkeypatch.setattr(ks, "is_engaged", boom)
    assert dd.kill_switch_engaged() is True
    assert pm._switch_engaged() is True


def test_alpaca_oco_exit_respects_the_switch(switch):
    from src.execution import alpaca_broker as ab
    broker = ab.AlpacaBroker.__new__(ab.AlpacaBroker)

    class _Client:
        def submit_order(self, **k):
            raise AssertionError("OCO reached the client with the switch on")
    broker._client = _Client()
    switch.engage("halt")
    assert broker.submit_oco_exit("AAPL", 5, "buy", 95.0, 120.0) is None
