"""
src/trading/orders.py
=====================
The order engine: one signal → at most one order, even across retries and
crashes; and when the state of an order is unknown, nothing else is sent.

IDEMPOTENCY
-----------
The client order id is derived from (strategy, signal, leg). The same signal
always produces the same id, so:

  * a retry after a network error re-sends the SAME id — the broker refuses a
    duplicate, and the journal refuses it before the broker is even asked;
  * a restart that replays a signal cannot open a second position.

JOURNAL
-------
data/trading/order_journal.jsonl, append-only. Every state an order passes
through is a line: INTENT → SUBMITTING → NEW / FILLED / … or UNKNOWN. The latest
line per client id is its state.

FAIL CLOSED
-----------
A timeout, or any answer that leaves an order's existence uncertain, records
UNKNOWN and HALTS the engine. While halted, `submit()` refuses everything.
`reconcile()` asks the broker about every non-final order by client id:

  found        → its real state is recorded
  not found    → recorded NOT_FOUND: the broker never had it
  can't ask    → stays UNKNOWN; the halt stays on

Only when nothing is UNKNOWN or SUBMITTING does the halt lift. The halt is on
disk, so a restart does not forget it.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from src.execution.approval_queue import _queue_guard

from .broker import (BrokerAdapter, BrokerRejected, BrokerTimeout,
                     BrokerUnavailable, OrderAck, OrderSpec, TERMINAL)
from .risk import RiskDecision
from .signals import Signal

logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent.parent.parent
JOURNAL = ROOT / "data" / "trading" / "order_journal.jsonl"
HALT_FILE = ROOT / "data" / "trading" / "order_halt.json"

#: What the journal and halt above cover, for anything that reports them. Only
#: TradingPipeline.run calls OrderEngine.submit, and no production caller runs
#: the pipeline — tests/test_trading_status_scope.py fails the day one does, so
#: this text changes with it. The desk, the reflex lane and the owner's approve
#: button send through src/execution/order_manager.OrderManager instead; those
#: orders are never in this journal, and this halt does not stop them.
JOURNAL_SCOPE = {
    "covers": "orders sent through src/trading/pipeline.TradingPipeline only",
    "production_path": False,
    "note": ("No production caller runs TradingPipeline, so an empty journal and "
             "halted=false say nothing about the orders ARIA actually sends. Those go "
             "through src/execution/order_manager (approval queue, desk auto-executor, "
             "reflex lane); the system-wide stop is the kill switch."),
    "production_orders_at": ["/api/desk/executions", "/api/execute/queue"],
}

INTENT = "INTENT"
SUBMITTING = "SUBMITTING"
UNKNOWN = "UNKNOWN"
NOT_FOUND = "NOT_FOUND"
UNSETTLED = {SUBMITTING, UNKNOWN}


def client_order_id(strategy_id: str, signal_id: str, leg: int = 0) -> str:
    h = hashlib.sha256(f"{strategy_id}|{signal_id}|{leg}".encode()).hexdigest()[:24]
    return f"aria-{h}"


class OrderEngine:
    # ── journal ──────────────────────────────────────────────────────────
    def _append(self, cid: str, state: str, **extra) -> None:
        JOURNAL.parent.mkdir(parents=True, exist_ok=True)
        row = {"at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
               "client_order_id": cid, "state": state, **extra}
        with JOURNAL.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, default=str) + "\n")
            f.flush()
            os.fsync(f.fileno())

    def latest(self) -> dict[str, dict]:
        out: dict[str, dict] = {}
        if not JOURNAL.exists():
            return out
        for line in JOURNAL.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
                if not isinstance(r, dict) or not r.get("client_order_id") or not r.get("state"):
                    raise ValueError("invalid journal row")
            except ValueError as exc:
                raise RuntimeError("order journal is corrupt; recover before sending") from exc
            prev = out.get(r["client_order_id"], {})
            out[r["client_order_id"]] = {**prev, **r}
        return out

    def state_of(self, cid: str) -> Optional[str]:
        r = self.latest().get(cid)
        return r["state"] if r else None

    def recent_submission_times(self) -> list[datetime]:
        """Times orders were actually sent — the risk engine's frequency input."""
        times = []
        if not JOURNAL.exists():
            return times
        for line in JOURNAL.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("state") == SUBMITTING:
                times.append(datetime.fromisoformat(r["at"]))
        return times

    # ── halt ─────────────────────────────────────────────────────────────
    def halted(self) -> Optional[dict]:
        try:
            unsettled = [cid for cid, row in self.latest().items() if row.get("state") in UNSETTLED]
        except Exception:
            return {"halted": True, "reason": "order journal unreadable — failing closed"}
        if unsettled:
            return {"halted": True, "reason": f"unsettled journal claims: {unsettled}"}
        if not HALT_FILE.exists():
            return None
        try:
            h = json.loads(HALT_FILE.read_text(encoding="utf-8"))
            return h if h.get("halted") else None
        except Exception:
            return {"halted": True, "reason": "halt file unreadable — failing closed"}

    def _halt(self, reason: str) -> None:
        HALT_FILE.parent.mkdir(parents=True, exist_ok=True)
        HALT_FILE.write_text(json.dumps({
            "halted": True, "reason": reason,
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}),
            encoding="utf-8")
        logger.critical("ORDER ENGINE HALTED: %s", reason)

    def _clear_halt(self) -> None:
        if HALT_FILE.exists():
            HALT_FILE.write_text(json.dumps({"halted": False}), encoding="utf-8")
        logger.warning("order engine halt cleared after reconciliation")

    # ── building ─────────────────────────────────────────────────────────
    def build(self, signal: Signal, decision: RiskDecision, *, leg: int = 0,
              order_type: str = "market", limit_price: float | None = None) -> OrderSpec:
        return OrderSpec(
            client_order_id=client_order_id(signal.strategy_id, signal.signal_id, leg),
            symbol=signal.symbol, side=signal.side, qty=decision.qty,
            order_type=order_type, limit_price=limit_price,
            asset_class=signal.asset_class, strategy_id=signal.strategy_id,
            signal_id=signal.signal_id)

    # ── submitting ───────────────────────────────────────────────────────
    def submit(self, order: OrderSpec, broker: BrokerAdapter) -> OrderAck:
        """Send once. Never twice. Never while anything is unknown."""
        with _queue_guard(JOURNAL):
            h = self.halted()
            if h:
                return OrderAck(order.client_order_id, "REFUSED",
                                detail=f"order engine halted: {h.get('reason')} — "
                                       f"reconcile before trading")
            prior = self.state_of(order.client_order_id)
            if prior is not None and prior != NOT_FOUND:
                return OrderAck(order.client_order_id, "DUPLICATE",
                                detail=f"already journalled as {prior}; not re-sent")
            self._append(order.client_order_id, SUBMITTING, order=order.to_dict(),
                         broker=broker.name, live=broker.is_live)
            try:
                ack = broker.submit_order(order)
            except BrokerRejected as exc:
                self._append(order.client_order_id, "REJECTED", detail=str(exc))
                return OrderAck(order.client_order_id, "REJECTED", detail=str(exc))
            except BrokerUnavailable as exc:
                # Could not reach the broker at all: nothing was sent. Recorded
                # as NOT_FOUND so the signal may be retried as a new decision.
                self._append(order.client_order_id, NOT_FOUND,
                             detail=f"broker unavailable, nothing sent: {exc}")
                return OrderAck(order.client_order_id, "NOT_SENT", detail=str(exc))
            except (BrokerTimeout, Exception) as exc:
                self._append(order.client_order_id, UNKNOWN, detail=str(exc))
                self._halt(f"state of {order.client_order_id} unknown after "
                           f"{type(exc).__name__}: {exc}")
                return OrderAck(order.client_order_id, UNKNOWN, detail=str(exc))
            self._append(order.client_order_id, ack.status, ack=ack.to_dict())
            return ack

    # ── reconciling ──────────────────────────────────────────────────────
    def reconcile(self, broker: BrokerAdapter) -> dict:
        """Settle every non-final order against the broker. Lifts the halt
        only when nothing remains unknown."""
        with _queue_guard(JOURNAL):
            report = {"settled": {}, "still_unknown": [], "errors": {}}
            for cid, row in self.latest().items():
                st = row.get("state")
                if st in TERMINAL or st in (NOT_FOUND, "REFUSED", "DUPLICATE"):
                    continue
                try:
                    ack = broker.get_order(cid)
                except Exception as exc:
                    report["errors"][cid] = str(exc)
                    report["still_unknown"].append(cid)
                    continue
                if ack is None:
                    if broker.authoritative_absence:
                        self._append(cid, NOT_FOUND, detail="synchronous simulator proves absence")
                        report["settled"][cid] = NOT_FOUND
                    else:
                        self._append(cid, UNKNOWN, detail="not visible; absence is not proof")
                        report["still_unknown"].append(cid)
                else:
                    self._append(cid, ack.status, ack=ack.to_dict(), via="reconcile")
                    report["settled"][cid] = ack.status
            unsettled = [c for c, r in self.latest().items() if r.get("state") in UNSETTLED]
            if unsettled:
                report["still_unknown"] = sorted(set(report["still_unknown"]) | set(unsettled))
                if not self.halted():
                    self._halt(f"{len(unsettled)} order(s) still unsettled")
            elif self.halted():
                self._clear_halt()
            report["halted"] = bool(self.halted())
            return report

    def reconcile_positions(self, broker: BrokerAdapter, expected: dict[str, float]) -> dict:
        """Compare the positions ARIA believes it holds with what the broker
        says. Any mismatch halts: trading on a wrong belief about the book is
        trading blind."""
        try:
            actual = {p["symbol"]: float(p["qty"]) for p in broker.get_positions()}
        except Exception as exc:
            self._halt(f"could not read positions: {exc}")
            return {"ok": False, "error": str(exc)}
        diffs = {}
        for sym in set(actual) | set(expected):
            a, e = actual.get(sym, 0.0), expected.get(sym, 0.0)
            if abs(a - e) > 1e-6:
                diffs[sym] = {"broker": a, "expected": e}
        if diffs:
            self._halt(f"position mismatch: {diffs}")
        return {"ok": not diffs, "differences": diffs}
