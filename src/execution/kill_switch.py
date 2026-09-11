"""
src/execution/kill_switch.py
============================
THE KILL SWITCH — one flag that stops ARIA sending orders, anywhere.

This is deliberately the dumbest thing in the execution stack. It has no
dependencies on the desk, the brain, the risk officer or any broker, because
the moment you want a kill switch is the moment you no longer trust those.
It is a file on disk and an environment variable, and either one alone is
enough to stop the desk.

What it stops:  every new order submission — entries, exits, brackets, the
                reflex lane, the auto-executor, a manually approved trade.
What it allows: reads (positions, balances, order status) and cancellations.

Cancelling is risk-reducing and stays available on purpose: "stop trading"
must never mean "and leave the resting brackets you can no longer manage".
Existing positions stay fully observable and reconcilable, which is the whole
point of halting rather than liquidating — flattening a book in a panic is a
trading decision, and this file does not make trading decisions.

Precedence: the kill switch sits BELOW every other gate. auto_execute, the
paper-only guard and the risk officer can each refuse a trade on their own,
and none of them can approve one past this. A release is always explicit and
always a human action; nothing in ARIA may call `release()` on its own.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent.parent.parent
STATE_FILE = ROOT / "data" / "kill_switch.json"

#: Set to any of these (case-insensitive) to engage the switch from the
#: environment. An env-engaged switch cannot be released through the API —
#: whoever set the variable has to unset it and restart.
ENV_VAR = "ARIA_KILL_SWITCH"
_TRUTHY = {"1", "true", "yes", "on", "engaged"}


class KillSwitchEngaged(RuntimeError):
    """Raised when an order is attempted while the kill switch is engaged."""


def _env_engaged() -> bool:
    return (os.environ.get(ENV_VAR) or "").strip().lower() in _TRUTHY


def _read_state() -> dict:
    """Read the on-disk half of the switch.

    A malformed or unreadable state file engages the switch. That is the
    whole contract of this module: if it cannot prove trading is permitted,
    trading is not permitted. Every other failure mode here would be a file
    that silently stops protecting anything.
    """
    if not STATE_FILE.exists():
        return {"engaged": False}
    try:
        raw = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        logger.error("kill switch: state file unreadable (%s) — failing closed", e)
        return {"engaged": True, "reason": f"kill switch state file unreadable: {e}",
                "source": "corrupt-state"}
    if not isinstance(raw, dict):
        logger.error("kill switch: state file is not an object — failing closed")
        return {"engaged": True, "reason": "kill switch state file malformed",
                "source": "corrupt-state"}
    if not isinstance(raw.get("engaged"), bool):
        logger.error("kill switch: 'engaged' is not a bool — failing closed")
        return {"engaged": True, "reason": "kill switch state file malformed",
                "source": "corrupt-state"}
    return raw


def is_engaged() -> bool:
    """True if new orders are forbidden right now."""
    return _env_engaged() or bool(_read_state().get("engaged"))


def status() -> dict:
    """Full state, for the UI and the API. Always truthful about *which*
    half of the switch is holding, because an env-engaged switch cannot be
    released from the UI and the UI has to be able to say so."""
    state = _read_state()
    env = _env_engaged()
    file_engaged = bool(state.get("engaged"))
    if env:
        source = "environment"
    elif file_engaged:
        source = state.get("source") or "file"
    else:
        source = None
    return {
        "engaged": env or file_engaged,
        "source": source,
        "releasable": not env,   # env-engaged switches need a restart
        "reason": state.get("reason") if file_engaged else (
            f"{ENV_VAR} is set" if env else None),
        "engaged_at": state.get("engaged_at") if file_engaged else None,
        "engaged_by": state.get("engaged_by") if file_engaged else None,
        "env_var": ENV_VAR,
    }


def _write(payload: dict) -> dict:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(STATE_FILE)   # atomic: never leave a half-written switch
    return status()


def engage(reason: str = "", actor: str = "owner") -> dict:
    """Stop all new orders. Safe to call when already engaged."""
    logger.critical("KILL SWITCH ENGAGED by %s: %s", actor, reason or "(no reason given)")
    return _write({
        "engaged": True,
        "reason": reason or "engaged manually",
        "engaged_by": actor,
        "engaged_at": datetime.now(timezone.utc).isoformat(),
        "source": "file",
    })


def release(actor: str = "owner") -> dict:
    """Permit orders again. Refuses while the environment variable is set —
    releasing that from inside the process would let ARIA talk its way out of
    a halt somebody imposed from outside it."""
    if _env_engaged():
        logger.warning("kill switch: release refused, %s is set", ENV_VAR)
        return status()
    logger.critical("KILL SWITCH RELEASED by %s", actor)
    return _write({
        "engaged": False,
        "released_by": actor,
        "released_at": datetime.now(timezone.utc).isoformat(),
    })


def assert_clear(what: str = "order") -> None:
    """Raise if the switch is engaged. Call this immediately before anything
    that can reach a broker."""
    if is_engaged():
        st = status()
        raise KillSwitchEngaged(
            f"kill switch engaged ({st.get('source')}): {st.get('reason')} "
            f"— refusing to submit {what}")


def guard(what: str = "order") -> Optional[dict]:
    """Non-raising form, for call sites that return a result dict rather than
    propagating exceptions. Returns None when clear, or the error dict to
    return to the caller when engaged."""
    if not is_engaged():
        return None
    st = status()
    return {
        "ok": False,
        "error": f"KILL SWITCH ENGAGED — {what} refused",
        "kill_switch": st,
    }
