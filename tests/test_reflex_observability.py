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


# ── failure paths, persisted to a real playbooks file and read back ──────────
# Codex review of b96cf10 (2026-10-16): an exception from _fire left no record,
# and a damaged attempts counter could raise after an executed fire and before
# the playbook left "armed" — so a restart could fire it a second time.

def _file_lane(tmp_path, monkeypatch, playbooks, quotes, fire, account=lambda: {}):
    from src.desk import reflex, auto_executor, desk_daemon
    from src.execution import alpaca_broker
    monkeypatch.setattr(reflex, "PLAYBOOKS_FILE", tmp_path / "playbooks.json")
    reflex.save_playbooks(playbooks)
    monkeypatch.setattr(reflex, "load_config", lambda: {**desk_config.DEFAULTS, "reflex_enabled": True})
    monkeypatch.setattr(reflex, "is_expired", lambda p: False)
    monkeypatch.setattr(alpaca_broker, "quotable", lambda t, now=None: True)
    monkeypatch.setattr(desk_daemon, "instrument_open", lambda *a, **k: True)
    connected = SimpleNamespace(_alpaca=SimpleNamespace(is_connected=lambda: True))
    monkeypatch.setattr(auto_executor, "get_order_manager", lambda: connected)
    monkeypatch.setattr(auto_executor, "account_snapshot", account)
    engine = reflex.ReflexEngine.__new__(reflex.ReflexEngine)
    engine._last_price = {t: 99.0 for t in quotes}
    monkeypatch.setattr(engine, "_quote", lambda ticker, mgr: quotes[ticker](), raising=False)
    monkeypatch.setattr(engine, "_fire", fire, raising=False)
    return reflex, engine


def _pb(ticker, **kw):
    return {"ticker": ticker, "side": "buy", "trigger_price": 100.0, "status": "armed",
            "asset_class": "crypto", "source": "signal", "expires_at": "2999-01-01T00:00:00", **kw}


def _boom(*a, **k):
    raise RuntimeError("broker client wedged")


def test_a_fire_that_raises_is_recorded_and_retired_not_left_armed(tmp_path, monkeypatch):
    reflex, engine = _file_lane(tmp_path, monkeypatch, [_pb("BTC-USD")],
                                {"BTC-USD": lambda: 101.0}, _boom)
    out = engine.scan_once()
    assert out[0]["stage"] == "error" and out[0]["uncertain"] is True
    stored = reflex.load_playbooks()[0]                     # read back from disk
    assert stored["status"] == "expired", "an uncertain fire must not stay armed"
    assert stored["attempts"] == 1
    assert stored["last_attempt"]["stage"] == "error"
    assert "RuntimeError" in stored["last_attempt"]["reason"]
    # the order may have been sent: unknown, never "not executed"
    assert stored["last_attempt"]["executed"] is None and stored["last_attempt"]["filled"] is None
    assert stored["last_attempt"]["uncertain"] is True


def test_no_account_snapshot_is_recorded_and_leaves_the_playbook_armed(tmp_path, monkeypatch):
    fired = []
    reflex, engine = _file_lane(tmp_path, monkeypatch, [_pb("BTC-USD")],
                                {"BTC-USD": lambda: 101.0},
                                lambda *a: fired.append(a) or {"executed": True}, account=_boom)
    engine.scan_once()
    assert fired == [], "nothing may be sent without an account snapshot"
    stored = reflex.load_playbooks()[0]
    assert stored["status"] == "armed" and stored["attempts"] == 1
    assert stored["last_attempt"]["stage"] == "account"


def test_a_damaged_counter_cannot_keep_an_executed_playbook_armed(tmp_path, monkeypatch):
    reflex, engine = _file_lane(
        tmp_path, monkeypatch, [_pb("BTC-USD", attempts="damaged")],
        {"BTC-USD": lambda: 101.0},
        lambda *a: {"stage": "execute", "reason": "paper", "executed": True, "filled": True})
    engine.scan_once()
    stored = reflex.load_playbooks()[0]
    assert stored["status"] == "fired"
    assert stored["attempts"] == 1


def test_a_later_playbook_raising_does_not_lose_an_earlier_fire(tmp_path, monkeypatch):
    def quote_eth():
        raise RuntimeError("quote feed down")
    reflex, engine = _file_lane(
        tmp_path, monkeypatch, [_pb("BTC-USD"), _pb("ETH-USD")],
        {"BTC-USD": lambda: 101.0, "ETH-USD": quote_eth},
        lambda *a: {"stage": "execute", "reason": "paper", "executed": True, "filled": True})
    try:
        engine.scan_once()
    except RuntimeError:
        pass                                   # the loop logs it; the save must still happen
    stored = {p["ticker"]: p for p in reflex.load_playbooks()}
    assert stored["BTC-USD"]["status"] == "fired"
    assert stored["ETH-USD"]["status"] == "armed"


def test_attempts_accumulate_across_a_restart(tmp_path, monkeypatch):
    stop = lambda *a: {"stage": "confidence", "reason": "signal prob 0.60 < 0.7", "executed": False}
    reflex, engine = _file_lane(tmp_path, monkeypatch, [_pb("BTC-USD")],
                                {"BTC-USD": lambda: 101.0}, stop)
    engine.scan_once()
    # a new engine (a restart) reads the stored playbook and sees a fresh crossing
    restarted = reflex.ReflexEngine.__new__(reflex.ReflexEngine)
    restarted._last_price = {"BTC-USD": 99.0}
    monkeypatch.setattr(restarted, "_quote", lambda ticker, mgr: 101.0, raising=False)
    monkeypatch.setattr(restarted, "_fire", stop, raising=False)
    restarted.scan_once()
    stored = reflex.load_playbooks()[0]
    assert stored["status"] == "armed" and stored["attempts"] == 2


def test_a_filled_status_with_no_average_price_is_not_a_confirmed_fill(tmp_path, monkeypatch):
    """Alpaca reports a missing average price as 0.0, never None."""
    rec = _desk_handle(tmp_path, monkeypatch, {"ok": True, "status": "filled",
                                              "fill_price": 0.0, "broker_order_id": "o3"})
    assert rec["fill_confirmed"] is False


def test_overlapping_scans_cannot_put_a_fired_playbook_back_to_armed(tmp_path, monkeypatch):
    """Independent review of ab78b1d: the loop's scan B waits in a slow gate for
    ETH while a manual scan A fires BTC and saves it fired; B then saved its
    stale copy, BTC read "armed" again, and the next crossing sent a second
    order. Scans now serialise on the playbook lock."""
    import threading
    entered, release, sends = threading.Event(), threading.Event(), []

    def fire(pb, price, account, mgr):
        if pb["ticker"] == "ETH-USD":
            if not entered.is_set():
                entered.set()
                release.wait(5)                     # B's slow gate
            return {"stage": "veto", "reason": "slow", "executed": False}
        sends.append(pb["ticker"])
        return {"stage": "execute", "reason": "paper", "executed": True, "filled": True}

    reflex, engine = _file_lane(tmp_path, monkeypatch, [_pb("ETH-USD"), _pb("BTC-USD")],
                                {"ETH-USD": lambda: 101.0, "BTC-USD": lambda: 101.0}, fire)
    b = threading.Thread(target=engine.scan_once)
    b.start()
    assert entered.wait(5)
    a = threading.Thread(target=engine.scan_once)    # manual POST /api/desk/reflex-scan
    a.start()
    a.join(0.5)                                      # A must not run while B holds the lock
    release.set()
    b.join(5); a.join(5)
    stored = {p["ticker"]: p for p in reflex.load_playbooks()}
    assert stored["BTC-USD"]["status"] == "fired"
    assert sends == ["BTC-USD"]
