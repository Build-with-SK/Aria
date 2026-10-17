"""The order path's state stores survive a crash mid-write and fail closed
when damaged (src/core/durable).

Before: every store used write_text (truncate, then write) and read a damaged
file as EMPTY, and the next save made the emptiness permanent. For the
approval queue that erased the "submitting"/"unknown" rows that block a second
order for an instrument whose first may be at the broker; for the day state it
reset the daily notional and trade caps.
"""
from __future__ import annotations

import json
import os
import time

import pytest

from src.core import durable


# ── reflex playbooks ─────────────────────────────────────────────────────────

def test_damaged_playbooks_refuse_arming_and_are_not_overwritten(tmp_path, monkeypatch):
    from src.desk import config as desk_config
    from src.desk import reflex
    from src.execution import alpaca_broker
    monkeypatch.setattr(reflex, "PLAYBOOKS_FILE", tmp_path / "playbooks.json")
    monkeypatch.setattr(alpaca_broker, "quotable", lambda t, now=None: True)
    torn = '[{"ticker": "BTC-USD", "status": "unknown", "client_order_id": "aria-q-'
    reflex.PLAYBOOKS_FILE.write_text(torn, encoding="utf-8")
    with pytest.raises(durable.StateUnreadable):
        reflex._arm("BTC-USD", "buy", 102.0, None, None, 80, "", "signal", {},
                    {**desk_config.DEFAULTS}, "a fresh signal")
    assert reflex.PLAYBOOKS_FILE.read_text(encoding="utf-8") == torn
    with pytest.raises(durable.StateUnreadable):
        reflex.reconcile_unknown_playbooks()


def test_playbooks_round_trip_atomically(tmp_path, monkeypatch):
    from src.desk import reflex
    monkeypatch.setattr(reflex, "PLAYBOOKS_FILE", tmp_path / "playbooks.json")
    assert reflex.load_playbooks() == []
    reflex.save_playbooks([{"ticker": "AAPL", "status": "fired"}])
    assert reflex.load_playbooks() == [{"ticker": "AAPL", "status": "fired"}]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["playbooks.json"]


# ── desk day state ───────────────────────────────────────────────────────────

@pytest.fixture
def day(tmp_path, monkeypatch):
    from src.desk import day_state
    monkeypatch.setattr(day_state, "STATE_FILE", tmp_path / "day_state.json")
    return day_state


def test_a_day_state_damaged_today_holds_entries_and_is_not_reset(day, monkeypatch):
    from src.desk import auto_executor as ae
    from src.desk import opinion
    torn = '{"date": "%s", "start_equity": 10000.0, "notional_used": 4900' % day.market_day()
    day.STATE_FILE.write_text(torn, encoding="utf-8")

    state = day.load_day_state(current_equity=10_000.0)
    assert state["usage_unknown"] is True and state["start_equity"] is None
    day.record_trade(500.0)                          # must not write a count from zero
    assert day.STATE_FILE.read_text(encoding="utf-8") == torn

    monkeypatch.setattr(ae, "paper_mode_confirmed", lambda: True)
    monkeypatch.setattr(opinion, "signals_freshness",
                        lambda: {"fresh": True, "age_hours": 1.0})
    ex = ae.AutoExecutor({"auto_execute": True, "daily_notional_budget": 5000,
                          "max_trades_per_day": 5})
    g = ex.gates({"connected": True, "equity": 10_000.0, "paper": True,
                  "paper_confirmed": True})
    assert g["budget_left"] == 0 and g["trades_left"] == 0
    assert g["auto_allowed"] is False
    assert "day state" in ex._gate_reason(g)


def test_a_day_state_damaged_on_an_earlier_day_rolls_over(day):
    day.STATE_FILE.write_text('{"date": "2026-10-01", "notional_us', encoding="utf-8")
    old = time.time() - 3 * 86400
    os.utime(day.STATE_FILE, (old, old))
    state = day.load_day_state(current_equity=10_000.0)
    assert not state.get("usage_unknown")
    assert state["date"] == day.market_day() and state["notional_used"] == 0.0
    assert json.loads(day.STATE_FILE.read_text(encoding="utf-8"))["start_equity"] == 10_000.0
    assert list(day.STATE_FILE.parent.glob("day_state.json.corrupt-*")), "a copy was kept"


# ── desk positions ───────────────────────────────────────────────────────────

def test_damaged_positions_recover_by_adoption_and_keep_a_copy(tmp_path, monkeypatch):
    from src.desk import notify
    from src.desk import position_manager as pm
    monkeypatch.setattr(pm, "POSITIONS_FILE", tmp_path / "positions.json")
    pushed = []
    monkeypatch.setattr(notify, "push", lambda text, cfg=None: pushed.append(text))
    pm.POSITIONS_FILE.write_text('{"AAPL": {"stop": 1', encoding="utf-8")
    mgr = pm.PositionManager({"ntfy_topic": ""})
    assert mgr.load_positions() == {}
    assert pushed and "DAMAGED" in pushed[0]
    assert list(tmp_path.glob("positions.json.corrupt-*"))
    mgr.save_positions({"AAPL": {"qty": 1}})
    assert mgr.load_positions() == {"AAPL": {"qty": 1}}


# ── desk config ──────────────────────────────────────────────────────────────

def test_a_crash_while_saving_the_desk_config_keeps_the_owners_settings(tmp_path, monkeypatch):
    from src.desk import config as desk_config
    monkeypatch.setattr(desk_config, "CONFIG_FILE", tmp_path / "desk_config.json")
    desk_config.save_config({"auto_execute": True, "max_trades_per_day": 3})

    def crash(src, dst):
        raise OSError("power cut")

    with monkeypatch.context() as m:
        m.setattr(durable.os, "replace", crash)
        with pytest.raises(OSError):
            desk_config.save_config({"max_trades_per_day": 4})
    cfg = desk_config.load_config()
    assert cfg["auto_execute"] is True and cfg["max_trades_per_day"] == 3
    assert sorted(p.name for p in tmp_path.iterdir()) == ["desk_config.json"]
