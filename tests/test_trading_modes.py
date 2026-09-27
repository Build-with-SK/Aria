"""
tests/test_trading_modes.py
===========================
RESEARCH → PAPER → LIVE_AUTONOMOUS, and the ways in.

Each test here is an attempt to reach a mode without earning it: skipping a
step, guessing the phrase, setting the state file directly, promoting while the
kill switch is engaged. They pass when the attempt fails.

The hard limits are tested in the same file because they are the other half of
the same promise: a mode says whether orders may be placed, the limits say how
large, and neither can be widened from inside the application.
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.trading import limits as _limits          # noqa: E402
from src.trading import modes                      # noqa: E402
from src.trading.modes import ExecutionMode, ModeTransitionRefused  # noqa: E402

pytestmark = pytest.mark.usefixtures("clean_trading_state")

PAPER_PHRASE = "ENABLE PAPER TRADING"
LIVE_PHRASE = "ENABLE LIVE AUTONOMOUS TRADING WITH REAL MONEY"


# ── the default ──────────────────────────────────────────────────────────────

def test_with_no_state_file_the_mode_is_research():
    assert modes.stored_mode() is ExecutionMode.RESEARCH
    assert modes.effective_mode() is ExecutionMode.RESEARCH
    assert modes.can_place_orders() is False
    assert modes.can_access_live_broker() is False


def test_an_unreadable_state_file_reads_as_research():
    modes.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    modes.STATE_FILE.write_text("{ this is not json", encoding="utf-8")
    assert modes.stored_mode() is ExecutionMode.RESEARCH


# ── going up ─────────────────────────────────────────────────────────────────

def test_paper_requires_the_exact_confirmation_phrase():
    with pytest.raises(ModeTransitionRefused) as exc:
        modes.transition("PAPER", actor="owner", reason="testing",
                         confirmation="enable paper trading")
    assert "confirmation phrase" in str(exc.value)
    assert modes.stored_mode() is ExecutionMode.RESEARCH

    modes.transition("PAPER", actor="owner", reason="testing",
                     confirmation=PAPER_PHRASE)
    assert modes.stored_mode() is ExecutionMode.PAPER


def test_a_mode_change_without_a_reason_is_refused():
    with pytest.raises(ModeTransitionRefused):
        modes.transition("PAPER", actor="owner", reason="  ",
                         confirmation=PAPER_PHRASE)


def test_research_cannot_jump_straight_to_live():
    with pytest.raises(ModeTransitionRefused) as exc:
        modes.transition("LIVE_AUTONOMOUS", actor="owner", reason="testing",
                         confirmation=LIVE_PHRASE)
    assert "skips a step" in str(exc.value)
    assert modes.stored_mode() is ExecutionMode.RESEARCH


def test_live_is_refused_without_the_out_of_process_switch(monkeypatch):
    modes.transition("PAPER", actor="owner", reason="t", confirmation=PAPER_PHRASE)
    monkeypatch.delenv(modes.LIVE_ENV, raising=False)
    with pytest.raises(ModeTransitionRefused) as exc:
        modes.transition("LIVE_AUTONOMOUS", actor="owner", reason="t",
                         confirmation=LIVE_PHRASE)
    assert modes.LIVE_ENV in str(exc.value)
    assert modes.stored_mode() is ExecutionMode.PAPER


def test_live_is_refused_while_no_strategy_has_earned_it(monkeypatch):
    modes.transition("PAPER", actor="owner", reason="t", confirmation=PAPER_PHRASE)
    monkeypatch.setenv(modes.LIVE_ENV, "true")
    with pytest.raises(ModeTransitionRefused) as exc:
        modes.transition("LIVE_AUTONOMOUS", actor="owner", reason="t",
                         confirmation=LIVE_PHRASE)
    assert "LIVE_ELIGIBLE" in str(exc.value)


def test_the_live_switch_must_be_exactly_true(monkeypatch):
    for value in ("True", "1", "yes", "TRUE", " true"):
        monkeypatch.setenv(modes.LIVE_ENV, value)
        assert modes.live_gate_open() is False, value
    monkeypatch.setenv(modes.LIVE_ENV, "true")
    assert modes.live_gate_open() is True


def test_an_engaged_kill_switch_blocks_going_up(monkeypatch):
    from src.execution import kill_switch
    kill_switch.engage("testing", actor="test")
    try:
        with pytest.raises(ModeTransitionRefused) as exc:
            modes.transition("PAPER", actor="owner", reason="t",
                             confirmation=PAPER_PHRASE)
        assert "kill switch" in str(exc.value)
    finally:
        kill_switch.release(actor="test")


# ── coming down ──────────────────────────────────────────────────────────────

def test_going_down_needs_no_phrase_and_always_works():
    modes.transition("PAPER", actor="owner", reason="t", confirmation=PAPER_PHRASE)
    out = modes.transition("RESEARCH", actor="owner", reason="stopping")
    assert out["changed"] and modes.stored_mode() is ExecutionMode.RESEARCH


def test_force_down_never_moves_up():
    out = modes.force_down("LIVE_AUTONOMOUS", actor="killswitch", reason="x")
    assert out["changed"] is False
    assert modes.stored_mode() is ExecutionMode.RESEARCH


# ── the effective mode ───────────────────────────────────────────────────────

def test_stored_live_without_the_env_switch_behaves_as_paper(monkeypatch):
    modes.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    modes.STATE_FILE.write_text(json.dumps({"mode": "LIVE_AUTONOMOUS"}),
                                encoding="utf-8")
    monkeypatch.delenv(modes.LIVE_ENV, raising=False)
    assert modes.stored_mode() is ExecutionMode.LIVE_AUTONOMOUS
    assert modes.effective_mode() is ExecutionMode.PAPER
    assert modes.can_access_live_broker() is False


def test_the_kill_switch_closes_live_access_even_in_live_mode(monkeypatch):
    modes.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    modes.STATE_FILE.write_text(json.dumps({"mode": "LIVE_AUTONOMOUS"}),
                                encoding="utf-8")
    monkeypatch.setenv(modes.LIVE_ENV, "true")
    assert modes.can_access_live_broker() is True
    from src.execution import kill_switch
    kill_switch.engage("testing", actor="test")
    try:
        assert modes.can_access_live_broker() is False
        assert modes.can_place_orders() is False
    finally:
        kill_switch.release(actor="test")


def test_every_change_is_written_to_the_ledger():
    modes.transition("PAPER", actor="owner", reason="starting paper",
                     confirmation=PAPER_PHRASE)
    modes.transition("RESEARCH", actor="owner", reason="stopping")
    rows = modes.history()
    assert [r["to"] for r in rows] == ["PAPER", "RESEARCH"]
    assert rows[0]["reason"] == "starting paper" and rows[0]["by"] == "owner"


# ── hard limits ──────────────────────────────────────────────────────────────

def test_a_fresh_install_gets_the_tight_defaults():
    lim = _limits.load()
    assert lim.max_order_value == _limits.DEFAULTS["max_order_value"]
    assert lim.max_leverage == 1.0
    for name, value in _limits.DEFAULTS.items():
        assert value <= _limits.CEILINGS[name], name


def test_tightening_applies_at_once_and_loosening_waits():
    now = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)
    out = _limits.set_limits({"max_order_value": 50}, actor="owner", now=now)
    assert out["applied"] == {"max_order_value": 50.0}
    assert _limits.load(now).max_order_value == 50.0

    out = _limits.set_limits({"max_order_value": 900}, actor="owner", now=now)
    assert out["scheduled"]["max_order_value"]["value"] == 900.0
    assert _limits.load(now).max_order_value == 50.0, "loosening took effect at once"
    later = now + _limits.COOLING_OFF + timedelta(minutes=1)
    assert _limits.load(later).max_order_value == 900.0


def test_a_limit_above_its_ceiling_is_refused():
    with pytest.raises(_limits.LimitChangeRefused) as exc:
        _limits.set_limits({"max_order_value": 5000}, actor="owner")
    assert "ceiling" in str(exc.value)
    with pytest.raises(_limits.LimitChangeRefused):
        _limits.set_limits({"max_leverage": 2.0}, actor="owner")
    with pytest.raises(_limits.LimitChangeRefused):
        _limits.set_limits({"invented_limit": 1}, actor="owner")
    with pytest.raises(_limits.LimitChangeRefused):
        _limits.set_limits({"max_order_value": -1}, actor="owner")


def test_a_value_edited_into_the_file_is_still_clamped_on_load():
    _limits.LIMITS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _limits.LIMITS_FILE.write_text(json.dumps(
        {"values": {"max_order_value": 999_999, "max_leverage": 10}, "pending": []}),
        encoding="utf-8")
    lim = _limits.load()
    assert lim.max_order_value == _limits.DEFAULTS["max_order_value"]
    assert lim.max_leverage == _limits.DEFAULTS["max_leverage"]


def test_setting_a_limit_cancels_a_pending_loosening():
    now = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)
    _limits.set_limits({"max_orders_per_day": 20}, actor="owner", now=now)
    assert _limits.pending()
    _limits.set_limits({"max_orders_per_day": 5}, actor="owner", now=now)
    assert _limits.pending() == []
    assert _limits.load(now + timedelta(days=2)).max_orders_per_day == 5
