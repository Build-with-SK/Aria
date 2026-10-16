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
