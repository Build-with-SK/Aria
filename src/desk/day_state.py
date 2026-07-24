"""
src/desk/day_state.py
=====================
Per-day desk state: start-of-day equity (for the drawdown circuit-breaker),
notional spent against the daily budget, and trade count. Resets on date roll.
"""
from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path

logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent.parent.parent
STATE_FILE = ROOT / "data" / "desk" / "day_state.json"


def market_day() -> str:
    """The US market's current date (America/New_York) — audit M4: a local
    machine date rolls the budget/circuit-breaker over hours off the actual
    trading day on any non-ET host."""
    try:
        from datetime import datetime
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/New_York")).date().isoformat()
    except Exception:
        return date.today().isoformat()


def load_day_state(current_equity: float | None = None) -> dict:
    """Load today's state, rolling over (and capturing start equity) on a new day."""
    today = market_day()
    state = {}
    if STATE_FILE.exists():
        try:
            state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            state = {}
    if state.get("date") != today:
        state = {"date": today,
                 "start_equity": current_equity or 0.0,
                 "notional_used": 0.0,
                 "trades_count": 0}
        _save(state)
    elif not state.get("start_equity") and current_equity:
        state["start_equity"] = current_equity
        _save(state)
    return state


def record_trade(notional: float):
    state = load_day_state()
    state["notional_used"] = round(state.get("notional_used", 0.0) + abs(notional), 2)
    state["trades_count"] = state.get("trades_count", 0) + 1
    _save(state)


def _save(state: dict):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
