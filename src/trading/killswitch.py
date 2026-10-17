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

FLATTEN: closing market orders for every long position, reduce-only
(src/execution/reduce_only): each sell is at most the position read from the
broker AFTER cancellation, net of every sell still resting on that symbol, so
even if a stop that survived the cancel also fills, the position reaches zero
and never flips. A symbol with a resting order the adapter cannot describe is
skipped, not guessed. Shorts are reported, not covered. Chosen explicitly in
data/trading/kill_policy.json. Documented, tested, and off unless chosen.

FLATTEN is a paper-only, best-effort step: the order goes to the same adapter
every other order does, and through the production adapter
(access.paper_adapter) the mode gate (RESEARCH after step 2) and the broker's
own kill-switch check (step 1) refuse it. Each refusal is in the report. No
order path below the kill switch is opened for it.

Cancelling protective stops is itself a choice: with PRESERVE and
`cancel_protective: false` (the default), orders that protect an existing
position (a sell against a long, a buy against a short) are LEFT RESTING, so
halting does not strip a position of its stop. Positions are read from the
broker when the caller does not pass them: an empty `held` used to make every
stop look like an entry and cancel it.

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
    if held is None and broker is not None:
        held = _held(broker, report)
    positions_unknown = held is None
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
        details = _order_details(broker)
        for o in open_orders:
            d = details.get(o.client_order_id)
            if (not report["policy"]["cancel_protective"] and
                    (positions_unknown or d is None)):
                # A failed read cannot establish that an order is an entry.
                # Keep possible protection; the kill switch already stops sends.
                kept.append(o.client_order_id)
                continue
            protective = bool(d and _reduces(d["side"], d["symbol"], held))
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


def _signed(p: dict) -> float:
    qty = float(p.get("qty", 0) or 0)
    return -abs(qty) if (str(p.get("side") or "").lower() == "short" or qty < 0) else abs(qty)


def _held(broker, report: dict) -> Optional[dict]:
    """Signed positions; None means unreadable and preserves possible stops."""
    try:
        return {p["symbol"]: _signed(p) for p in broker.get_positions()}
    except Exception as exc:
        report["steps"]["positions_read"] = {"error": str(exc)}
        return None


def _order_details(broker) -> dict:
    """{client_order_id: {symbol, side, qty, parent_id}} for open orders, from
    whichever description the adapter offers. Missing = unknown."""
    try:
        if hasattr(broker, "open_order_details"):
            return {d["client_order_id"]: d for d in broker.open_order_details()}
        specs = getattr(broker, "specs", None) or {}
        return {cid: {"client_order_id": cid, "symbol": s.symbol, "side": s.side,
                      "qty": s.qty, "parent_id": None} for cid, s in specs.items()}
    except Exception:
        return {}


def _reduces(side: str, symbol: str, held: dict) -> bool:
    """Does an order on `side` shrink what is held in `symbol`?"""
    from src.execution import reduce_only
    net = sum(q for s, q in held.items() if reduce_only.same_symbol(s, symbol))
    return reduce_only.reducing_side(net) == str(side or "").lower()


def _flatten(broker, reason: str) -> dict:
    """Reduce-only closing orders, sent to the adapter like any other order.

    For each long: sell at most the position read NOW (after step 3's
    cancellations), net of every sell still resting on that symbol — so if a
    stop that survived the cancel fills as well, the book reaches zero and
    does not flip short. Nothing is sent for a symbol whose resting orders
    cannot be accounted for. Shorts are reported, not covered."""
    from src.execution import reduce_only
    from .broker import OrderSpec
    out = {}
    try:
        positions = broker.get_positions()
    except Exception as exc:
        return {"error": f"could not read positions: {exc}"}
    try:
        open_now = broker.get_open_orders()
        details = _order_details(broker)
        resting = [details.get(o.client_order_id) for o in open_now]
    except Exception as exc:
        return {"error": f"could not read resting orders, nothing sent "
                         f"(a flatten could double-sell): {exc}"}
    unaccounted = any(d is None for d in resting)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    for p in positions:
        sym, signed = p["symbol"], _signed(p)
        if signed < 0:
            out[sym] = {"skipped": "short position: FLATTEN closes longs only — cover it "
                                   "at the broker"}
            continue
        if signed <= 0:
            continue
        if unaccounted:
            out[sym] = {"skipped": "an open order the adapter cannot describe is still "
                                   "resting; a flatten could double-sell"}
            continue
        ro = reduce_only.check("sell", signed, signed,
                               reduce_only.resting_reduce_qty(resting, sym, "sell"))
        if not ro.ok:
            out[sym] = {"skipped": ro.reason, "reduce_only": ro.to_dict()}
            continue
        spec = OrderSpec(client_order_id=f"aria-halt-{sym}-{stamp}",
                         symbol=sym, side="sell", qty=ro.qty,
                         strategy_id="killswitch", signal_id=reason[:40])
        try:
            out[sym] = {**broker.submit_order(spec).to_dict(), "reduce_only": ro.to_dict()}
        except Exception as exc:
            out[sym] = {"error": str(exc), "reduce_only": ro.to_dict()}
    return out
