"""
src/desk/day_state.py
=====================
Per-day desk state: start-of-day equity (for the drawdown circuit-breaker),
notional spent against the daily budget, and trade count. Resets on date roll.
"""
from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

from src.core.durable import StateUnreadable, atomic_write_json, read_json_state

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
    try:
        state = read_json_state(STATE_FILE, dict, expect=dict)
    except StateUnreadable as e:
        # A damaged file used to read as {} — a fresh day with nothing spent,
        # so a crash mid-write reset the daily notional and trade caps. If the
        # damaged file was written TODAY, today's usage is unknown: report it
        # as such (no budget, no start equity — both gates then hold new
        # entries) and leave the file alone. Written on an earlier day, its
        # contents no longer matter: roll over as on any new day (a copy of
        # the damaged file is kept beside it).
        written = _file_market_day(STATE_FILE)
        if written is None or written >= today:
            logger.critical(f"desk day state unreadable and written today — new "
                            f"entries held for the day: {e}")
            return {"date": today, "start_equity": None, "notional_used": 0.0,
                    "trades_count": 0, "usage_unknown": True, "unreadable": str(e)}
        logger.error(f"desk day state from {written} unreadable; rolling over: {e}")
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
    if state.get("usage_unknown"):
        # Saving here would write a count that starts from zero over the
        # damaged file — exactly the reset this guards against.
        logger.error(f"trade of notional {notional} not counted: today's usage is "
                     f"unknown (day state unreadable); entries stay held")
        return
    state["notional_used"] = round(state.get("notional_used", 0.0) + abs(notional), 2)
    state["trades_count"] = state.get("trades_count", 0) + 1
    _save(state)


def _save(state: dict):
    atomic_write_json(STATE_FILE, state, default=None)


def _file_market_day(path: Path) -> str | None:
    """The US market date on which `path` was last written, or None."""
    try:
        from datetime import datetime
        from zoneinfo import ZoneInfo
        ts = path.stat().st_mtime
        return datetime.fromtimestamp(ts, ZoneInfo("America/New_York")).date().isoformat()
    except Exception:
        return None
