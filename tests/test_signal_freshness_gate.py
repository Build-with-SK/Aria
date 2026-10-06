"""
tests/test_signal_freshness_gate.py
===================================
Nothing executes unattended on stale signals.

Found 2026-10-06: data/signals.json was nine days old (the full pipeline that
writes it is a manual run) and the reflex lane had armed 44 playbooks from it
that morning — triggers, stops and composite "spikes" computed on 27 September,
waiting to fire against live prices. `_fire` carried the comment "validate —
fresh signal" and checked nothing of the kind.

The rule is the existing safety contract extended by one gate: any red gate
sends the trade to the human approval queue. Freshness is judged by the world
model's own policy, so the API, the chat and the desk cannot disagree.
"""
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from src.core import world
from src.desk import opinion, reflex


@pytest.fixture
def signals_file(tmp_path, monkeypatch):
    monkeypatch.setattr(world, "DATA", tmp_path)
    path = tmp_path / "signals.json"

    def write(age_hours: float):
        path.write_text(json.dumps({"AAPL": {"current_price": 300.0,
                                             "composite_score": 60.0}}),
                        encoding="utf-8")
        t = time.time() - age_hours * 3600
        os.utime(path, (t, t))
    return write


def test_fresh_signals_read_as_fresh(signals_file):
    signals_file(2)
    assert opinion.signals_freshness()["fresh"] is True


def test_signals_past_the_world_budget_are_stale(signals_file):
    signals_file(world.STALE_AFTER_HOURS["signals"] + 1)
    f = opinion.signals_freshness()
    assert f["fresh"] is False and "days old" in f["why"]


def test_missing_signals_are_stale_not_fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(world, "DATA", tmp_path)
    assert opinion.signals_freshness()["fresh"] is False


def test_a_freshness_bug_fails_closed(monkeypatch):
    def boom(name):
        raise RuntimeError("disk on fire")
    monkeypatch.setattr(world, "dataset_freshness", boom)
    f = opinion.signals_freshness()
    assert f["fresh"] is False and "could not be determined" in f["why"]


# ── the execution gate ──────────────────────────────────────────────────────

def _all_other_gates_green(monkeypatch):
    import src.desk.auto_executor as ae
    monkeypatch.setattr(ae, "paper_mode_confirmed", lambda: True)
    monkeypatch.setattr(ae, "load_day_state",
                        lambda current_equity=None: {"notional_used": 0.0,
                                                     "trades_count": 0})
    return ae.AutoExecutor({"auto_execute": True, "daily_notional_budget": 1e5,
                            "max_trades_per_day": 10})


GREEN_ACCOUNT = {"equity": 1e5, "connected": True, "paper": True,
                 "paper_confirmed": True}


def test_stale_signals_close_the_auto_gate(signals_file, monkeypatch):
    ex = _all_other_gates_green(monkeypatch)
    signals_file(200)
    g = ex.gates(GREEN_ACCOUNT)
    assert g["signals_fresh"] is False
    assert g["auto_allowed"] is False
    assert "signals are stale" in ex._gate_reason(g)


def test_fresh_signals_leave_the_gate_to_the_others(signals_file, monkeypatch):
    ex = _all_other_gates_green(monkeypatch)
    signals_file(1)
    g = ex.gates(GREEN_ACCOUNT)
    assert g["signals_fresh"] is True
    assert g["auto_allowed"] is True


# ── the reflex lane ─────────────────────────────────────────────────────────

def test_reflex_will_not_arm_from_stale_signals(signals_file, tmp_path, monkeypatch):
    monkeypatch.setattr(reflex, "PLAYBOOKS_FILE", tmp_path / "pb.json")
    signals_file(200)
    cfg = {"reflex_signal_spike": 40.0, "reflex_playbook_expiry_days": 2}
    assert reflex.arm_from_signal(cfg, account={"positions": []}) == []
    t = {"id": "d", "ticker": "AAPL",
         "judge": {"verdict": "BUY", "conviction": 58}}
    assert reflex.arm_from_debate(t, 65, {**cfg, "reflex_arm_conviction_gap": 10}) is None


def test_reflex_arms_from_fresh_signals(signals_file, tmp_path, monkeypatch):
    monkeypatch.setattr(reflex, "PLAYBOOKS_FILE", tmp_path / "pb.json")
    monkeypatch.setattr(reflex, "load_data_json", lambda f: {
        "AAPL": {"current_price": 300.0, "composite_score": 60.0}})
    signals_file(1)
    cfg = {"reflex_signal_spike": 40.0, "reflex_playbook_expiry_days": 2}
    assert [p["ticker"] for p in reflex.arm_from_signal(cfg, account={"positions": []})] == ["AAPL"]


def test_a_playbook_firing_on_stale_signals_is_retired_not_executed(signals_file, monkeypatch):
    signals_file(200)
    monkeypatch.setattr(reflex, "load_data_json",
                        lambda f: {"AAPL": {"current_price": 300.0}})
    eng = reflex.ReflexEngine(cfg={"reflex_enabled": True, "reflex_min_prob": 0.7})
    pb = {"ticker": "AAPL", "side": "buy", "trigger_price": 301.0,
          "source": "signal", "asset_class": "equity", "stop": None, "target": None}
    out = eng._fire(pb, 305.0, GREEN_ACCOUNT, mgr=None)
    assert out["executed"] is False
    assert out["stage"] == "validate"
    assert out["terminal"] is True
    assert "signals stale" in out["reason"]
