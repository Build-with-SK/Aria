"""
src/trading/killswitch.py
=========================
HALT. Independent of every model, every strategy and every network call that
might hang.

    1. stop new orders           src/execution/kill_switch.engage() — first,
                                 before anything that could fail
    2. disable autonomy          execution mode forced to RESEARCH; the legacy
                                 desk's auto_execute set false
    3. cancel pending orders     every open order at the broker, best effort,
                                 each result recorded
    4. positions                 PRESERVE (default) or FLATTEN — see below

Step 1 happens even if every later step fails, and it is done by the oldest,
dumbest module in the execution stack on purpose: a file and an environment
variable, no imports from the desk, the brain or any broker.

WHAT HAPPENS TO POSITIONS
-------------------------
PRESERVE (default): positions are left exactly as they are, with their
protective orders cancelled only if they were pending ENTRY orders — see
`cancel_protective`. Liquidating a book in a panic is a trading decision, often
a bad one, and a kill switch should not make trading decisions by default.

FLATTEN: closing market orders for every long position, reduce-only (a sell can
never exceed what is held, so it cannot open a short). Chosen explicitly in
data/trading/kill_policy.json. Documented, tested, and off unless chosen.

Cancelling protective stops is itself a choice: with PRESERVE and
`cancel_protective: false` (the default), orders that protect an existing
position (sells of held quantity) are LEFT RESTING, so halting does not strip a
position of its stop.

This module imports nothing from src.brain, src.inference or src.consult.
tests/test_trading_structure.py enforces that.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent.parent.parent
POLICY_FILE = ROOT / "data" / "trading" / "kill_policy.json"
LOG = ROOT / "data" / "trading" / "halts.jsonl"

PRESERVE = "PRESERVE"
FLATTEN = "FLATTEN"
DEFAULT_POLICY = {"positions": PRESERVE, "cancel_protective": False}


def policy() -> dict:
    try:
        raw = json.loads(POLICY_FILE.read_text(encoding="utf-8")) if POLICY_FILE.exists() else {}
    except Exception:
        raw = {}
    p = dict(DEFAULT_POLICY)
    if raw.get("positions") in (PRESERVE, FLATTEN):
        p["positions"] = raw["positions"]
    if isinstance(raw.get("cancel_protective"), bool):
        p["cancel_protective"] = raw["cancel_protective"]
    return p


def set_policy(positions: str, cancel_protective: bool, *, actor: str) -> dict:
    if positions not in (PRESERVE, FLATTEN):
        raise ValueError("positions must be PRESERVE or FLATTEN")
    POLICY_FILE.parent.mkdir(parents=True, exist_ok=True)
    POLICY_FILE.write_text(json.dumps({"positions": positions,
                                       "cancel_protective": bool(cancel_protective),
                                       "by": actor}), encoding="utf-8")
    return policy()


def halt(reason: str, *, actor: str = "owner", broker=None,
         held: Optional[dict[str, float]] = None) -> dict:
    """Stop everything. `broker` is a src/trading BrokerAdapter used for
    cancellation (and, under FLATTEN, reduce-only exits); None skips those
    steps and says so. Never raises."""
    report = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "reason": reason, "by": actor, "policy": policy(), "steps": {}}

    # 1. stop new orders — first, unconditionally
    try:
        from src.execution import kill_switch
        report["steps"]["kill_switch"] = kill_switch.engage(reason, actor=actor)
    except Exception as exc:                      # pragma: no cover
        report["steps"]["kill_switch"] = {"error": str(exc)}
        logger.critical("HALT: kill switch could not be written: %s", exc)

    # 2. disable autonomy
    try:
        from . import modes
        report["steps"]["mode"] = modes.force_down(modes.ExecutionMode.RESEARCH,
                                                   actor=actor, reason=f"halt: {reason}")
    except Exception as exc:
        report["steps"]["mode"] = {"error": str(exc)}
    try:
        from src.desk.config import save_config
        save_config({"auto_execute": False})
        report["steps"]["desk_auto_execute"] = False
    except Exception as exc:
        report["steps"]["desk_auto_execute"] = {"error": str(exc)}

    # 3. cancel pending orders
    held = held or {}
    if broker is None:
        report["steps"]["cancel"] = {"skipped": "no broker supplied"}
    else:
        cancelled, kept, failed = [], [], {}
        try:
            open_orders = broker.get_open_orders()
        except Exception as exc:
            open_orders = []
            failed["_list"] = str(exc)
        for o in open_orders:
            spec = getattr(broker, "specs", {}).get(o.client_order_id)
            protective = bool(spec and spec.side == "sell"
                              and held.get(spec.symbol, 0) > 0)
            if protective and not report["policy"]["cancel_protective"]:
                kept.append(o.client_order_id)
                continue
            try:
                (cancelled if broker.cancel_order(o.client_order_id) else kept).append(
                    o.client_order_id)
            except Exception as exc:
                failed[o.client_order_id] = str(exc)
        report["steps"]["cancel"] = {"cancelled": cancelled, "kept_protective": kept,
                                     "failed": failed}

    # 4. positions
    if report["policy"]["positions"] == FLATTEN and broker is not None:
        report["steps"]["flatten"] = _flatten(broker, reason)
    else:
        report["steps"]["positions"] = "preserved"

    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(report, default=str) + "\n")
    logger.critical("TRADING HALTED by %s: %s", actor, reason)
    return report


def _flatten(broker, reason: str) -> dict:
    """Reduce-only closing orders. Sent directly to the broker adapter: the
    kill switch is engaged by now and would refuse them on the normal path,
    and closing is the one thing a halt is allowed to do."""
    from .broker import OrderSpec
    out = {}
    try:
        positions = broker.get_positions()
    except Exception as exc:
        return {"error": f"could not read positions: {exc}"}
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    for p in positions:
        qty = float(p.get("qty", 0))
        if qty <= 0:
            continue
        spec = OrderSpec(client_order_id=f"aria-halt-{p['symbol']}-{stamp}",
                         symbol=p["symbol"], side="sell", qty=qty,
                         strategy_id="killswitch", signal_id=reason[:40])
        try:
            out[p["symbol"]] = broker.submit_order(spec).to_dict()
        except Exception as exc:
            out[p["symbol"]] = {"error": str(exc)}
    return out
