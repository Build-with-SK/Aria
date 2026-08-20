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


def _real_equity(value) -> float | None:
    """A start-of-day equity is only worth recording if it came from a broker
    that actually answered. Anything else is None — NOT 0.0, and NOT a
    fallback constant.

    Both wrong answers have already cost this desk a day. `current_equity or
    0.0` wrote a 0 whenever the broker was disconnected at the day roll, and a
    0 start makes the drawdown check skip itself silently for the rest of the
    day. The mirror-image bug is worse: seeding from the risk officer's
    100_000 fallback means the first real reading of a $10k account computes a
    90% intraday loss and the circuit breaker halts every entry, permanently,
    for an account that never lost anything. That is the same shape as the
    £100 re-basing halt — a figure that answers a different question, used as
    though it answered this one.
    """
    # Type first, like the desk config sanitiser: float("10000") succeeds and
    # would quietly accept a string that arrived where a number belongs, which
    # is the class of bug that made the reflex risk check raise in the first
    # place. bool is excluded because it subclasses int.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if v <= 0 or v != v or v in (float("inf"), float("-inf")):
        return None
    return v


def load_day_state(current_equity: float | None = None) -> dict:
    """Load today's state, rolling over (and capturing start equity) on a new day.

    start_equity may legitimately be absent — the broker can be down at the
    roll. Absent means UNKNOWN, and callers must treat it as unknown rather
    than as zero or as flat; it is filled in by the first tick that gets a
    real reading.
    """
    today = market_day()
    state = {}
    if STATE_FILE.exists():
        try:
            state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            state = {}
    equity = _real_equity(current_equity)
    if state.get("date") != today:
        state = {"date": today,
                 "start_equity": equity,       # None when the broker was down
                 "notional_used": 0.0,
                 "trades_count": 0}
        _save(state)
    elif not state.get("start_equity") and equity is not None:
        state["start_equity"] = equity
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
