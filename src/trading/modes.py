"""
src/trading/modes.py
====================
THREE EXECUTION MODES, AND NO WAY TO SLIDE BETWEEN THEM BY ACCIDENT.

    RESEARCH          no orders of any kind. The default, and where a missing or
                      unreadable state file lands.
    PAPER             orders only to a confirmed paper account.
    LIVE_AUTONOMOUS   real money, without per-trade approval — still under every
                      hard limit, the risk engine, the validator and the kill
                      switch.

HOW A MODE CHANGES
------------------
Down is always allowed, instantly, with no ceremony: making the system do less
must never be hard. Up is deliberately awkward:

  * one step at a time — RESEARCH cannot jump to LIVE_AUTONOMOUS;
  * an exact confirmation phrase, typed, per target mode;
  * the kill switch must be clear;
  * for LIVE_AUTONOMOUS, `ARIA_LIVE_TRADING_ENABLED=true` must be set in the
    process environment. That switch lives OUTSIDE the application — in .env or
    the launchd job — and needs a restart, so nothing running inside ARIA,
    including a model, can open it;
  * for LIVE_AUTONOMOUS, at least one strategy must have earned LIVE_ELIGIBLE
    through the lifecycle's evidence gates;
  * the API route in front of this additionally requires a recent step-up
    ("confirm it's you").

Every change, up or down, is appended to a ledger with who, when and why.

THE EFFECTIVE MODE
------------------
`effective_mode()` is what everything asks. A stored LIVE_AUTONOMOUS with the
environment switch removed reads as PAPER — pulling the switch out of the
launchd job is a way to stop live trading that works even if the state file
says otherwise.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent.parent.parent
STATE_FILE = ROOT / "data" / "trading" / "mode.json"
LEDGER = ROOT / "data" / "trading" / "mode_ledger.jsonl"

#: Set to exactly "true" outside the application to permit LIVE_AUTONOMOUS.
LIVE_ENV = "ARIA_LIVE_TRADING_ENABLED"


class ExecutionMode(str, Enum):
    RESEARCH = "RESEARCH"
    PAPER = "PAPER"
    LIVE_AUTONOMOUS = "LIVE_AUTONOMOUS"


ORDER = (ExecutionMode.RESEARCH, ExecutionMode.PAPER, ExecutionMode.LIVE_AUTONOMOUS)
DEFAULT = ExecutionMode.RESEARCH

#: Typed, exactly, to move UP into a mode. Long on purpose.
CONFIRMATIONS = {
    ExecutionMode.PAPER: "ENABLE PAPER TRADING",
    ExecutionMode.LIVE_AUTONOMOUS: "ENABLE LIVE AUTONOMOUS TRADING WITH REAL MONEY",
}


class ModeTransitionRefused(RuntimeError):
    pass


def rank(mode: ExecutionMode) -> int:
    return ORDER.index(ExecutionMode(mode))


def lower(a: ExecutionMode, b: ExecutionMode) -> ExecutionMode:
    return a if rank(a) <= rank(b) else b


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def stored_mode() -> ExecutionMode:
    """The persisted mode. Missing or unreadable → RESEARCH (fail closed)."""
    if not STATE_FILE.exists():
        return DEFAULT
    try:
        raw = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return ExecutionMode(raw["mode"])
    except Exception as exc:
        logger.error("trading mode file unreadable (%s) — treating as RESEARCH", exc)
        return DEFAULT


def live_gate_open() -> bool:
    """The out-of-process switch. Exact string, like the paper-account gate:
    a check that accepts "True" or "1" accepts a typo as authorisation."""
    return os.environ.get(LIVE_ENV, "") == "true"


def effective_mode() -> ExecutionMode:
    m = stored_mode()
    if m == ExecutionMode.LIVE_AUTONOMOUS and not live_gate_open():
        logger.warning("mode is LIVE_AUTONOMOUS but %s is not 'true' — acting "
                       "as PAPER", LIVE_ENV)
        return ExecutionMode.PAPER
    return m


def _kill_switch_engaged() -> bool:
    try:
        from src.execution import kill_switch
        return kill_switch.is_engaged()
    except Exception:                       # unreadable kill switch = engaged
        return True


def can_place_orders() -> bool:
    return effective_mode() != ExecutionMode.RESEARCH and not _kill_switch_engaged()


def can_access_live_broker() -> bool:
    return (effective_mode() == ExecutionMode.LIVE_AUTONOMOUS
            and not _kill_switch_engaged())


def _write(mode: ExecutionMode, *, actor: str, reason: str, previous: ExecutionMode):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"mode": mode.value, "since": _now(), "by": actor,
                               "reason": reason}, indent=1), encoding="utf-8")
    tmp.replace(STATE_FILE)
    with LEDGER.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"at": _now(), "from": previous.value, "to": mode.value,
                            "by": actor, "reason": reason}) + "\n")
    logger.warning("trading mode %s -> %s by %s: %s", previous.value, mode.value,
                   actor, reason)


def live_readiness() -> list[str]:
    """What stands between this machine and LIVE_AUTONOMOUS. Empty = ready."""
    blockers = []
    if not live_gate_open():
        blockers.append(f"{LIVE_ENV} is not set to 'true' in the service "
                        f"environment (set outside ARIA, then restart)")
    if _kill_switch_engaged():
        blockers.append("the kill switch is engaged")
    try:
        from . import lifecycle
        if not lifecycle.any_at_or_beyond(lifecycle.Stage.LIVE_ELIGIBLE):
            blockers.append("no strategy has reached LIVE_ELIGIBLE")
    except Exception as exc:
        blockers.append(f"strategy registry unreadable: {exc}")
    try:
        from . import limits
        limits.load()
    except Exception as exc:
        blockers.append(f"hard limits unreadable: {exc}")
    return blockers


def transition(target: str | ExecutionMode, *, actor: str, reason: str,
               confirmation: str = "") -> dict:
    """Change the stored mode, or raise ModeTransitionRefused saying why."""
    target = ExecutionMode(target)
    current = stored_mode()
    if target == current:
        return {"mode": current.value, "changed": False}
    if not (reason or "").strip():
        raise ModeTransitionRefused("a reason is required for every mode change")

    if rank(target) < rank(current):              # down: always allowed
        _write(target, actor=actor, reason=reason, previous=current)
        return {"mode": target.value, "changed": True, "from": current.value}

    if rank(target) != rank(current) + 1:
        raise ModeTransitionRefused(
            f"{current.value} -> {target.value} skips a step; modes move up one "
            f"at a time")
    if confirmation != CONFIRMATIONS[target]:
        raise ModeTransitionRefused(
            f"confirmation phrase does not match; type exactly: "
            f"{CONFIRMATIONS[target]!r}")
    if _kill_switch_engaged():
        raise ModeTransitionRefused("the kill switch is engaged; release it first")
    if target == ExecutionMode.LIVE_AUTONOMOUS:
        blockers = live_readiness()
        if blockers:
            raise ModeTransitionRefused("LIVE_AUTONOMOUS refused: " + "; ".join(blockers))

    _write(target, actor=actor, reason=reason, previous=current)
    return {"mode": target.value, "changed": True, "from": current.value}


def force_down(target: str | ExecutionMode, *, actor: str, reason: str) -> dict:
    """For the kill switch: move down without any check that could fail."""
    target = ExecutionMode(target)
    current = stored_mode()
    if rank(target) >= rank(current):
        return {"mode": current.value, "changed": False}
    _write(target, actor=actor, reason=reason, previous=current)
    return {"mode": target.value, "changed": True, "from": current.value}


def status() -> dict:
    stored = stored_mode()
    return {
        "stored_mode": stored.value,
        "effective_mode": effective_mode().value,
        "live_gate_env": LIVE_ENV,
        "live_gate_open": live_gate_open(),
        "kill_switch_engaged": _kill_switch_engaged(),
        "can_place_orders": can_place_orders(),
        "can_access_live_broker": can_access_live_broker(),
        "next_confirmation": (CONFIRMATIONS.get(ORDER[rank(stored) + 1])
                              if rank(stored) + 1 < len(ORDER) else None),
    }


def history(n: int = 50) -> list[dict]:
    if not LEDGER.exists():
        return []
    rows = []
    for line in LEDGER.read_text(encoding="utf-8").splitlines()[-n:]:
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows
