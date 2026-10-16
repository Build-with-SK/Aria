"""
tests/test_reflex_observability.py
==================================
S2.5 (2026-10-16): 1,300 reflex playbooks armed, 0 fired, and nothing could say
why. A crossed trigger stopped by a gate is not terminal, so the playbook stayed
armed and later read "expired" with no record that it had ever crossed or which
gate held — every attempt is now written onto the playbook.

(The other defect, arming symbols the broker cannot quote — API3-USD 222 times —
is the owner-gated xfail in tests/test_reflex_untradeable.py.)
"""
from types import SimpleNamespace

from src.desk import config as desk_config


def _store(monkeypatch, reflex, initial):
    saved = {"pbs": [dict(p) for p in initial]}
    monkeypatch.setattr(reflex, "load_playbooks", lambda: [dict(p) for p in saved["pbs"]])
    monkeypatch.setattr(reflex, "save_playbooks", lambda pbs: saved.__setitem__("pbs", [dict(p) for p in pbs]))
    return saved


def _scan(monkeypatch, outcome):
    from src.desk import reflex, auto_executor, desk_daemon
    from src.execution import alpaca_broker
    pb = {"ticker": "BTC-USD", "side": "buy", "trigger_price": 100.0, "status": "armed",
          "asset_class": "crypto", "source": "signal", "expires_at": "2999-01-01T00:00:00"}
    saved = _store(monkeypatch, reflex, [pb])
    monkeypatch.setattr(reflex, "load_config", lambda: {**desk_config.DEFAULTS, "reflex_enabled": True})
    monkeypatch.setattr(reflex, "is_expired", lambda p: False)
    monkeypatch.setattr(alpaca_broker, "quotable", lambda t, now=None: True)
    monkeypatch.setattr(desk_daemon, "instrument_open", lambda *a, **k: True)
    connected = SimpleNamespace(_alpaca=SimpleNamespace(is_connected=lambda: True))
    monkeypatch.setattr(auto_executor, "get_order_manager", lambda: connected)
    monkeypatch.setattr(auto_executor, "account_snapshot", lambda: {})
    engine = reflex.ReflexEngine.__new__(reflex.ReflexEngine)
    engine._last_price = {"BTC-USD": 99.0}
    monkeypatch.setattr(engine, "_quote", lambda ticker, mgr: 101.0, raising=False)
    monkeypatch.setattr(engine, "_fire", lambda pb, price, account, mgr: outcome, raising=False)
    engine.scan_once()
    return saved["pbs"][0]


def test_a_gate_stop_is_recorded_and_the_playbook_stays_armed(monkeypatch):
    pb = _scan(monkeypatch, {"stage": "confidence", "reason": "signal prob 0.65 < 0.7", "executed": False})
    assert pb["status"] == "armed"
    assert pb["attempts"] == 1
    assert pb["last_attempt"]["stage"] == "confidence"
    assert pb["last_attempt"]["price"] == 101.0 and "0.65" in pb["last_attempt"]["reason"]


def test_an_execution_is_recorded_and_marks_the_playbook_fired(monkeypatch):
    pb = _scan(monkeypatch, {"stage": "execute", "reason": "paper", "executed": True})
    assert pb["status"] == "fired"
    assert pb["last_attempt"]["executed"] is True


# ── an accepted order is not a fill ──────────────────────────────────────────
# order_manager.execute returns ok for SUBMITTED as well as FILLED: Alpaca acks
# market orders as accepted, and when the poll window ends without a fill the
# desk recorded the intended price as fill_price and the reflex said "fired".

def _desk_handle(tmp_path, monkeypatch, execute_result):
    from src.desk import auto_executor as ae
    from src.desk import position_manager, notify
    from src.execution import approval_queue as aq
    monkeypatch.setattr(aq, "QUEUE_FILE", tmp_path / "queue.json")
    monkeypatch.setattr(ae, "get_order_manager",
                        lambda *a, **k: SimpleNamespace(execute=lambda trade_id: execute_result))
    monkeypatch.setattr(ae, "account_snapshot", lambda: {"connected": True, "equity": 1e5})
    monkeypatch.setattr(ae.AutoExecutor, "gates",
                        lambda self, account=None: {"auto_allowed": True, "budget_left": 1e9})
    monkeypatch.setattr(ae.AutoExecutor, "_notify", lambda self, *a: None)
    monkeypatch.setattr(ae.AutoExecutor, "_alert_feed", lambda self, *a: None)
    monkeypatch.setattr(ae, "record_trade", lambda notional: None)
    monkeypatch.setattr(position_manager.PositionManager, "track_entry", lambda self, *a: None)
    monkeypatch.setattr(notify, "push", lambda *a, **k: None)
    entry = {"ticker": "BTC-USD", "side": "buy", "qty": 0.01, "asset_class": "crypto",
             "price": 100.0, "notional": 1.0, "conviction": 80, "thesis": "t"}
    return ae.AutoExecutor({"auto_execute": True})._handle(entry, {})


def test_an_accepted_order_with_no_confirmed_fill_is_not_recorded_as_a_fill(tmp_path, monkeypatch):
    rec = _desk_handle(tmp_path, monkeypatch, {"ok": True, "status": "submitted",
                                              "fill_price": None, "broker_order_id": "o1"})
    assert rec["mode"] == "auto"
    assert rec["order_status"] == "submitted"
    assert rec["fill_confirmed"] is False


def test_a_confirmed_fill_is_recorded_as_one(tmp_path, monkeypatch):
    rec = _desk_handle(tmp_path, monkeypatch, {"ok": True, "status": "filled",
                                              "fill_price": 101.5, "broker_order_id": "o2"})
    assert rec["order_status"] == "filled"
    assert rec["fill_confirmed"] is True and rec["fill_price"] == 101.5


def test_the_reflex_attempt_record_separates_submitted_from_filled(monkeypatch):
    pb = _scan(monkeypatch, {"stage": "execute", "reason": "paper", "executed": True,
                             "filled": False, "order_status": "submitted"})
    assert pb["status"] == "fired"
    assert pb["last_attempt"]["executed"] is True
    assert pb["last_attempt"]["filled"] is False


def test_the_reflex_reports_no_fill_price_for_an_unconfirmed_order(monkeypatch):
    from src.desk import reflex, auto_executor
    monkeypatch.setattr(auto_executor.AutoExecutor, "_handle", lambda self, entry, t: {
        "mode": "auto", "order_status": "submitted", "fill_confirmed": False,
        "fill_price": 100.0})
    engine = reflex.ReflexEngine.__new__(reflex.ReflexEngine)
    engine.cfg = {**desk_config.DEFAULTS, "reflex_min_prob": 0.0}
    monkeypatch.setattr(reflex, "load_data_json", lambda name: {"BTC-USD": {"current_price": 100.0}})
    monkeypatch.setattr(reflex, "signals_freshness", lambda: {"fresh": True, "why": ""})
    monkeypatch.setattr(reflex, "confidence_ok", lambda sig, side, floor: (True, 0.9))
    monkeypatch.setattr(engine, "_size", lambda pb, price, sig, account: {"ticker": "BTC-USD"}, raising=False)
    monkeypatch.setattr(engine, "_risk_check", lambda entry, account: True, raising=False)
    monkeypatch.setattr(engine, "_veto", lambda entry, price, sig: None, raising=False)
    out = engine._fire({"ticker": "BTC-USD", "side": "buy", "stop": 95.0, "target": 110.0,
                        "trigger_price": 99.0, "asset_class": "crypto", "source": "signal",
                        "debate_id": ""}, 100.0, {}, None)
    assert out["executed"] is True and out["filled"] is False
    assert out["order_status"] == "submitted" and out["fill_price"] is None
