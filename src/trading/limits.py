"""
src/trading/limits.py
=====================
HARD LIMITS — numbers no strategy, signal or model can argue with.

THREE LAYERS
------------
  CEILINGS   compiled into this file. The loosest any limit may ever be. Raising
             one is a code change, reviewed as a change to what ARIA may do with
             money. They agree with `src/risk/profiles.HARD_LIMITS` where the two
             overlap, and that is asserted by a test.
  DEFAULTS   tighter, sized for a small personal account. Used until the owner
             sets something else.
  OWNER      data/trading/hard_limits.json, written only by `set_limits()`, which
             only the owner's step-up-protected API route calls. It can move a
             limit anywhere at or under its ceiling.

WHY LOOSENING WAITS
-------------------
Tightening takes effect at once — making ARIA more careful should never be slow.
Loosening is scheduled 24 hours ahead. A limit raised in the middle of a losing
day, by a hurried human or by anything that got hold of a session, does not take
effect until the day is over. The pending change is visible and can be cancelled
by setting the old value again.

THE MODEL CANNOT MOVE THESE
---------------------------
Nothing in src/trading calls `set_limits()` except through the owner route, and
tests/test_trading_structure.py fails if anything else in the source tree does.
The risk engine reads limits; it never writes them.
"""
from __future__ import annotations

import json
import logging
import math
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent.parent.parent
LIMITS_FILE = ROOT / "data" / "trading" / "hard_limits.json"
LEDGER = ROOT / "data" / "trading" / "limits_ledger.jsonl"

COOLING_OFF = timedelta(hours=24)

#: The loosest each limit may ever be. Every one is "higher = looser".
CEILINGS: dict[str, float] = {
    "risk_per_trade_pct":          2.0,     # = profiles.HARD_LIMITS
    "max_position_pct":           10.0,     # = profiles.HARD_LIMITS max_name_pct
    "max_instrument_exposure_pct": 10.0,
    "max_sector_exposure_pct":    35.0,     # = profiles.HARD_LIMITS
    "max_strategy_exposure_pct":  50.0,
    "max_daily_loss_pct":          5.0,     # = profiles drawdown_halt_pct
    "max_drawdown_pct":           20.0,
    "max_order_value":          1000.0,     # account currency
    "max_leverage":                1.0,     # no margin, ever, in this phase
    "max_open_positions":         20,
    "max_orders_per_minute":       6,
    "max_orders_per_day":         20,       # = profiles max_trades_per_day
    "max_quote_age_seconds":      30.0,
    "max_price_deviation_pct":     2.0,     # limit price vs mid
    "max_participation_pct":      10.0,     # of size displayed at the touch
}

#: Where a fresh install starts: tight, for a small account.
DEFAULTS: dict[str, float] = {
    "risk_per_trade_pct":          1.0,
    "max_position_pct":           10.0,
    "max_instrument_exposure_pct": 10.0,
    "max_sector_exposure_pct":    35.0,
    "max_strategy_exposure_pct":  50.0,
    "max_daily_loss_pct":          3.0,
    "max_drawdown_pct":           15.0,
    "max_order_value":           100.0,
    "max_leverage":                1.0,
    "max_open_positions":          5,
    "max_orders_per_minute":       2,
    "max_orders_per_day":         10,
    "max_quote_age_seconds":      15.0,
    "max_price_deviation_pct":     1.0,
    "max_participation_pct":       5.0,
}

#: Limits that are counts, not fractions.
INTEGER_LIMITS = {"max_open_positions", "max_orders_per_minute", "max_orders_per_day"}


@dataclass(frozen=True)
class HardLimits:
    risk_per_trade_pct: float
    max_position_pct: float
    max_instrument_exposure_pct: float
    max_sector_exposure_pct: float
    max_strategy_exposure_pct: float
    max_daily_loss_pct: float
    max_drawdown_pct: float
    max_order_value: float
    max_leverage: float
    max_open_positions: int
    max_orders_per_minute: int
    max_orders_per_day: int
    max_quote_age_seconds: float
    max_price_deviation_pct: float
    max_participation_pct: float

    def to_dict(self) -> dict:
        return asdict(self)


class LimitChangeRefused(ValueError):
    pass


def _clean(name: str, value) -> float:
    if name not in CEILINGS:
        raise LimitChangeRefused(f"unknown limit {name!r}")
    try:
        v = float(value)
    except (TypeError, ValueError):
        raise LimitChangeRefused(f"{name} must be a number") from None
    if not math.isfinite(v) or v <= 0:
        raise LimitChangeRefused(f"{name} must be a positive number")
    if v > CEILINGS[name]:
        raise LimitChangeRefused(
            f"{name}={v} is above its hard ceiling of {CEILINGS[name]} — the "
            f"ceiling is compiled into src/trading/limits.py and is not a setting")
    return int(v) if name in INTEGER_LIMITS else v


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _read() -> dict:
    if not LIMITS_FILE.exists():
        return {"values": {}, "pending": []}
    try:
        raw = json.loads(LIMITS_FILE.read_text(encoding="utf-8"))
        return {"values": dict(raw.get("values") or {}),
                "pending": list(raw.get("pending") or [])}
    except Exception as exc:
        # Unreadable owner settings fall back to DEFAULTS, which are tighter
        # than any ceiling — the failure direction is "more careful".
        logger.error("hard limits file unreadable (%s) — using DEFAULTS", exc)
        return {"values": {}, "pending": []}


def _write(state: dict) -> None:
    LIMITS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = LIMITS_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=1), encoding="utf-8")
    tmp.replace(LIMITS_FILE)


def load(now: datetime | None = None) -> HardLimits:
    """The limits in force right now. Pending loosenings whose time has come
    are applied (and persisted); every value is re-checked against its ceiling
    on every load, so an edited file cannot smuggle a number past it."""
    now = now or _now()
    state = _read()
    due = [p for p in state["pending"]
           if datetime.fromisoformat(p["effective_at"]) <= now]
    if due:
        for p in due:
            state["values"][p["name"]] = p["value"]
        state["pending"] = [p for p in state["pending"] if p not in due]
        _write(state)
    merged = dict(DEFAULTS)
    for name, value in state["values"].items():
        try:
            merged[name] = _clean(name, value)
        except LimitChangeRefused as exc:
            logger.error("ignoring stored limit: %s — using the default", exc)
    return HardLimits(**{f.name: merged[f.name] for f in fields(HardLimits)})


def pending() -> list[dict]:
    return _read()["pending"]


def set_limits(changes: dict, *, actor: str, now: datetime | None = None) -> dict:
    """Owner-only. Tighten now; loosen after COOLING_OFF. Returns what happened.

    Called only from the step-up-protected API route. Every other caller is a
    bug, and the structural test says so.
    """
    now = now or _now()
    current = load(now).to_dict()
    state = _read()
    applied, scheduled = {}, {}
    for name, raw in (changes or {}).items():
        value = _clean(name, raw)
        # Setting a limit replaces any pending change to it — including the
        # way to cancel a scheduled loosening: set the current value again.
        state["pending"] = [p for p in state["pending"] if p["name"] != name]
        if value <= current[name]:
            state["values"][name] = value
            applied[name] = value
        else:
            eff = (now + COOLING_OFF).isoformat(timespec="seconds")
            state["pending"].append({"name": name, "value": value,
                                     "effective_at": eff, "by": actor})
            scheduled[name] = {"value": value, "effective_at": eff}
    _write(state)
    with LEDGER.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"at": now.isoformat(timespec="seconds"), "by": actor,
                            "applied": applied, "scheduled": scheduled}) + "\n")
    logger.warning("hard limits changed by %s: applied=%s scheduled=%s",
                   actor, applied, scheduled)
    return {"applied": applied, "scheduled": scheduled,
            "limits": load(now).to_dict()}


def describe() -> dict:
    return {"in_force": load().to_dict(), "ceilings": dict(CEILINGS),
            "defaults": dict(DEFAULTS), "pending": pending(),
            "cooling_off_hours": COOLING_OFF.total_seconds() / 3600}
