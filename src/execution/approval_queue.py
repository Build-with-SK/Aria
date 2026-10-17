"""
src/execution/approval_queue.py
================================
Persistent approval queue — every trade ARIA wants to make sits here
until the user approves or rejects it via the UI.

File-backed: data/execution/approval_queue.json
"""
from __future__ import annotations

import json
import math
import logging
import os
import sys
import tempfile
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from .broker_base import AssetClass, OrderRequest, OrderSide, OrderType
from .reduce_only import same_symbol

logger = logging.getLogger(__name__)

QUEUE_FILE = Path(__file__).parent.parent.parent / "data" / "execution" / "approval_queue.json"

# Audit L4: the queue is read-modify-write over one JSON file; concurrent
# approve/push/reject from the API thread and desk threads could lose writes.
import threading
_QUEUE_LOCK = threading.Lock()


@dataclass
class PendingTrade:
    """A trade proposal waiting for user approval."""
    id:             str
    created_at:     str
    # pending | approved | submitting | executed | rejected | cancelled | unknown
    # "submitting": claimed for sending, its client id recorded BEFORE the POST.
    #   Not cancellable, not approvable; a crash leaves it here for
    #   reconciliation by lookup, never for a fresh automatic send.
    # "unknown": sent, and the broker's answer never settled — it may hold the
    #   order. Not approvable again, so nothing resends it. Leaves only by a
    #   broker answer found by client id (reconcile) or the owner's release.
    # "executed": the broker acknowledged it — `order_status` says whether it
    #   is working (submitted), partially filled or filled.
    # submitting and unknown are UNRESOLVED: they block another automated
    # order for the same instrument (order_manager), across lanes and restarts.
    status:         str
    # What to trade
    ticker:         str
    side:           str   # buy | sell
    qty:            float
    order_type:     str   # market | limit | stop | stop_limit
    limit_price:    Optional[float]
    stop_price:     Optional[float]
    time_in_force:  str
    asset_class:    str
    # Risk
    stop_loss:      Optional[float]
    take_profit:    Optional[float]
    est_value:      float   # qty * current_price estimate
    risk_amount:    float   # est loss if stop hit
    # Context from ARIA
    signal_score:   float
    confidence:     str
    situation:      str
    thesis_summary: str
    bull_case:      str
    bear_case:      str
    invalidation:   str
    # Broker routing
    broker:         str   # alpaca | ibkr | auto
    # Result after execution
    broker_order_id: str = ""
    fill_price:      float = 0.0
    filled_at:       str = ""
    rejection_reason: str = ""
    executed_at:     str = ""
    # An EXIT: when sent it is re-checked against the broker's position at
    # that moment (src/execution/reduce_only) and clipped or refused, so an
    # exit approved after the position is gone cannot open a new one.
    reduce_only:     bool = False
    # The idempotency key it was sent with (order_manager.queue_client_order_id)
    # — what finds the order at the broker when its outcome is unknown.
    client_order_id: str = ""
    submitting_at:   str = ""
    # The broker's last word on it: submitted | partial | filled | cancelled |
    # rejected | expired | unknown — and how much actually filled.
    order_status:    str = ""
    filled_qty:      float = 0.0
    order_terminal:  bool = False
    # An unresolved order the OWNER released after checking the broker.
    released_by:     str = ""
    released_at:     str = ""
    # 1 means created by the durable-claim protocol. Missing = historical.
    submission_version: int = 0
    legacy_original: Optional[dict] = None
    reconciliation_note: str = ""


UNRESOLVED = ("submitting", "unknown")


def _active(row: dict) -> bool:
    return row.get("status") in UNRESOLVED or (
        row.get("status") == "executed" and not row.get("order_terminal") and
        row.get("order_status") in ("submitted", "partial"))


class QueueStateError(RuntimeError):
    """Unreadable execution state must never be replaced with an empty book."""


@contextmanager
def _queue_guard(path: Optional[Path] = None):
    """Serialize readers and writers across API threads AND process restarts.

    The lock has its own stable inode; the snapshot is replaced atomically.
    Kernel locks are released if a worker crashes. Never lock the replaced file.
    """
    with _QUEUE_LOCK:
        target = path if path is not None else QUEUE_FILE
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.with_suffix(".lock").open("a+b") as lock:
            if sys.platform == "win32":
                import msvcrt
                if lock.seek(0, os.SEEK_END) == 0:
                    lock.write(b"\0")
                    lock.flush()
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if sys.platform == "win32":
                    lock.seek(0)
                    msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _load_queue() -> list[dict]:
    QUEUE_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not QUEUE_FILE.exists():
        return []
    try:
        items = json.loads(QUEUE_FILE.read_text(encoding="utf-8"))
        if not isinstance(items, list):
            raise ValueError("queue must contain a list")
        seen = set()
        for row in items:
            trade = PendingTrade(**row)
            if not trade.id or trade.id in seen or trade.status not in (
                    "pending", "approved", "submitting", "unknown", "executed",
                    "rejected", "cancelled"):
                raise ValueError("invalid or duplicate queue identity/state")
            seen.add(trade.id)
        return items
    except (OSError, ValueError, TypeError) as exc:
        raise QueueStateError("approval queue unreadable; execution is blocked; "
                              "preserve the file and recover from the broker/audit") from exc


def _save_queue(items: list[dict]):
    """Durable snapshot plus append-only state transitions, under _queue_guard.

    A failed write never truncates the prior snapshot. A claim is durable before
    its caller can POST. Audit failure propagates, so an unaudited claim is not
    sent; reconciliation can recover the persisted submitting row.
    """
    QUEUE_FILE.parent.mkdir(parents=True, exist_ok=True)
    before = {row["id"]: row for row in _load_queue()}
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", delete=False,
                                         dir=QUEUE_FILE.parent, suffix=".tmp") as stream:
            temporary = Path(stream.name)
            json.dump(items, stream, indent=2, default=str, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, QUEUE_FILE)
        if os.name != "nt":
            directory = os.open(QUEUE_FILE.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    changed = [row for row in items if row != before.get(row["id"])]
    if changed:
        with QUEUE_FILE.with_suffix(".audit.jsonl").open("a", encoding="utf-8") as audit:
            for row in changed:
                audit.write(json.dumps({"at": datetime.utcnow().isoformat(),
                                        "trade": row}, default=str, allow_nan=False) + "\n")
            audit.flush()
            os.fsync(audit.fileno())


class ApprovalQueue:
    """Process-safe approval queue with durable claims and an execution audit."""

    def _read(self) -> list[dict]:
        with _queue_guard():
            return self._load_safe()

    @staticmethod
    def _load_safe() -> list[dict]:
        """Under the queue lock, quarantine history with no durable send proof.

        A legacy pending proposal with no send evidence is safe to approve.
        All other no-key history needs broker evidence or an owner decision.
        The exact old row survives in the snapshot AND append-only audit.
        """
        items = _load_queue()
        changed = False
        for row in items:
            if (row.get("submission_version", 0) or row.get("client_order_id") or
                    row.get("legacy_original") is not None or row.get("released_by")):
                continue
            if row.get("status") == "pending" and not any(
                    row.get(k) for k in ("broker_order_id", "submitting_at", "executed_at",
                                         "filled_qty", "fill_price")):
                continue
            row["legacy_original"] = dict(row)
            row["status"] = "unknown"
            row["order_status"] = "unknown"
            row["reconciliation_note"] = "Legacy order: no durable client id; broker/owner reconciliation required"
            changed = True
        if changed:
            _save_queue(items)
        return items

    def push(
        self,
        req: OrderRequest,
        *,
        confidence: str = "",
        bull_case: str = "",
        bear_case: str = "",
        invalidation: str = "",
        current_price: float = 0.0,
        broker: str = "auto",
    ) -> PendingTrade:
        """Add a trade proposal to the queue. Returns the PendingTrade."""
        est_value   = req.qty * current_price if current_price else 0.0
        risk_amount = 0.0
        if req.stop_loss and current_price:
            risk_amount = abs(current_price - req.stop_loss) * req.qty

        # Auto-select broker
        if broker == "auto":
            if req.asset_class in (AssetClass.FUTURES, AssetClass.FOREX, AssetClass.OPTION):
                broker = "ibkr"
            else:
                broker = "alpaca"

        trade = PendingTrade(
            id=uuid.uuid4().hex,
            created_at=datetime.utcnow().isoformat(),
            status="pending",
            ticker=req.ticker,
            side=req.side.value,
            qty=req.qty,
            order_type=req.order_type.value,
            limit_price=req.limit_price,
            stop_price=req.stop_price,
            time_in_force=req.time_in_force,
            asset_class=req.asset_class.value,
            stop_loss=req.stop_loss,
            take_profit=req.take_profit,
            est_value=round(est_value, 2),
            risk_amount=round(risk_amount, 2),
            signal_score=req.signal_score,
            confidence=confidence,
            situation=req.situation,
            thesis_summary=req.thesis_summary,
            bull_case=bull_case,
            bear_case=bear_case,
            invalidation=invalidation,
            broker=broker,
            reduce_only=bool(getattr(req, "reduce_only", False)),
            submission_version=1,
        )

        with _queue_guard():
            items = self._load_safe()
            items.append(asdict(trade))
            _save_queue(items)
        logger.info(f"Trade queued [{trade.id}]: {trade.side.upper()} {trade.qty} {trade.ticker} via {broker}")
        return trade

    def get_pending(self) -> list[PendingTrade]:
        return [PendingTrade(**d) for d in self._read() if d["status"] == "pending"]

    def get_all(self, limit: int = 100) -> list[PendingTrade]:
        items = self._read()
        return [PendingTrade(**d) for d in items[-limit:]]

    def get_by_id(self, trade_id: str) -> Optional[PendingTrade]:
        for d in self._read():
            if d["id"] == trade_id:
                return PendingTrade(**d)
        return None

    def approve(self, trade_id: str) -> Optional[PendingTrade]:
        with _queue_guard():
            items = self._load_safe()
            for d in items:
                if d["id"] == trade_id and d["status"] == "pending":
                    d["status"] = "approved"
                    d["submission_version"] = 1
                    _save_queue(items)
                    return PendingTrade(**d)
        return None

    def reject(self, trade_id: str, reason: str = "", *,
               client_order_id: str = "", after_send: bool = False) -> Optional[PendingTrade]:
        """Record a refusal — by the human before it was sent, or by the broker
        after. "approved" is accepted deliberately: OrderManager.execute()
        marks a trade approved *before* it calls the broker, so a broker
        rejection arriving afterwards used to find the trade already out of
        "pending" and silently do nothing. The queue then read "approved" for
        an order that never existed — the audit trail claiming a trade the
        market rejected.

        A row already claimed for sending ("submitting") can be rejected only
        with `after_send` — by the broker's own answer — never by the human's
        "reject before it reaches the broker", which would be false by then."""
        allowed = ("pending", "approved") + (("submitting",) if after_send else ())
        with _queue_guard():
            items = self._load_safe()
            for d in items:
                if d["id"] == trade_id and d["status"] in allowed:
                    d["status"] = "rejected"
                    d["submission_version"] = 1
                    d["rejection_reason"] = reason
                    if client_order_id:
                        d["client_order_id"] = client_order_id
                    _save_queue(items)
                    return PendingTrade(**d)
        return None

    def claim_for_submit(self, trade_id: str, client_order_id: str, *,
                         exclusive: bool = True, protection: bool = False,
                         reduction: bool = False) -> tuple[Optional[PendingTrade], str]:
        """Atomically move an approved (or pending) row to "submitting" with its
        client id recorded — BEFORE anything is sent. From here the row cannot
        be cancelled as "never reached the broker".

        `exclusive`: refuse while ANOTHER row for the same instrument and broker
        is unresolved (submitting / unknown). Checked under the same lock as the
        claim, so two lanes sending at the same instant cannot both pass — the
        second sees the first in flight. Exits pass exclusive=False.

        (trade, "") on success; (None, why) when nothing may be sent."""
        with _queue_guard():
            items = self._load_safe()
            row = next((d for d in items if d["id"] == trade_id), None)
            if row is None or row["status"] not in ("pending", "approved"):
                return None, (f"trade {trade_id} is "
                              f"{'missing' if row is None else row['status']}, not sendable")
            if exclusive or protection or reduction:
                busy = [d for d in items if d["id"] != trade_id and _active(d)
                        and same_symbol(d.get("ticker"), row.get("ticker"))
                        and ((exclusive and (d.get("status") in UNRESOLVED or
                                             d.get("situation") != "protective_exit")) or
                             ((protection or reduction) and d.get("reduce_only")))
                        and d.get("broker") == row.get("broker")]
                if busy:
                    return None, (f"another order for {row.get('ticker')} is unresolved: "
                                  + ", ".join(f"{d['id']} ({d['status']})" for d in busy))
            row["status"] = "submitting"
            row["submission_version"] = 1
            row["client_order_id"] = client_order_id
            row["submitting_at"] = datetime.utcnow().isoformat()
            _save_queue(items)
            return PendingTrade(**row), ""

    def mark_executed(self, trade_id: str, broker_order_id: str, fill_price: float = 0.0,
                      *, client_order_id: str = "", order_status: str = "",
                      filled_qty: Optional[float] = None, order_terminal: bool = False):
        """The broker acknowledged the order. `order_status` says how far it got
        (submitted / partial / filled); `executed` alone never meant filled."""
        with _queue_guard():
            items = self._load_safe()
            for d in items:
                if d["id"] == trade_id:
                    if d.get("broker_order_id") and broker_order_id != d["broker_order_id"]:
                        raise QueueStateError("broker identity changed; manual reconciliation required")
                    prior_qty = float(d.get("filled_qty") or 0)
                    incoming_qty = prior_qty if filled_qty is None else float(filled_qty)
                    if (not math.isfinite(incoming_qty) or incoming_qty < 0 or
                            incoming_qty > float(d["qty"]) + 1e-8 or
                            not math.isfinite(float(fill_price)) or fill_price < 0):
                        raise QueueStateError("invalid broker fill evidence")
                    # Duplicate/delayed callbacks cannot erase fills or reopen a
                    # terminal order. A later larger cumulative fill is retained.
                    if incoming_qty < prior_qty or (d.get("order_terminal") and
                            not order_terminal and incoming_qty <= prior_qty):
                        return
                    if d.get("order_status") == "filled" and order_status != "filled" and incoming_qty <= prior_qty:
                        return
                    if incoming_qty == prior_qty and not fill_price and d.get("fill_price"):
                        fill_price = d["fill_price"]
                    if (d.get("status") == "executed" and d.get("order_status") == order_status
                            and prior_qty == incoming_qty and d.get("fill_price") == fill_price
                            and bool(d.get("order_terminal")) == order_terminal):
                        return
                    if d["status"] in ("cancelled", "rejected"):
                        logger.critical(f"trade {trade_id} was {d['status']} locally but the "
                                        f"broker holds order {broker_order_id} — recording it")
                    d["status"] = "executed"
                    d["broker_order_id"] = broker_order_id
                    d["fill_price"] = fill_price
                    d["executed_at"] = datetime.utcnow().isoformat()
                    if client_order_id:
                        d["client_order_id"] = client_order_id
                    if order_status:
                        d["order_status"] = order_status
                        d["order_terminal"] = bool(d.get("order_terminal")) or order_terminal
                    if filled_qty is not None:
                        d["filled_qty"] = float(filled_qty)
                    _save_queue(items)
                    return
        logger.warning(f"mark_executed: trade {trade_id} not found")

    def mark_ended_unfilled(self, trade_id: str, order_status: str, reason: str) -> Optional[PendingTrade]:
        """The broker acknowledged the order and later cancelled, rejected or
        expired it with NOTHING filled. Recorded as rejected with the broker's
        status — never left reading "executed"."""
        with _queue_guard():
            items = self._load_safe()
            for d in items:
                if d["id"] == trade_id and d["status"] in ("submitting", "executed", "unknown"):
                    if d.get("filled_qty", 0) > 0:
                        # A stale zero-fill cancellation cannot erase a known fill.
                        d["order_status"] = "partial"
                        d["order_terminal"] = True
                        d["status"] = "executed"
                        _save_queue(items)
                        return PendingTrade(**d)
                    d["status"] = "rejected"
                    d["order_status"] = order_status
                    d["filled_qty"] = 0.0
                    d["rejection_reason"] = reason
                    _save_queue(items)
                    return PendingTrade(**d)
        return None

    def mark_unknown(self, trade_id: str, reason: str, *,
                     client_order_id: str = "") -> Optional[PendingTrade]:
        """Sent, and whether the broker holds it could not be settled.
        execute() and cancel() refuse it from now on; it leaves "unknown" only
        on a broker answer found by client id, or the owner's release."""
        with _queue_guard():
            items = self._load_safe()
            for d in items:
                if d["id"] == trade_id and d["status"] in ("pending", "approved", "submitting"):
                    d["status"] = "unknown"
                    d["order_status"] = "unknown"
                    d["rejection_reason"] = reason
                    if client_order_id:
                        d["client_order_id"] = client_order_id
                    _save_queue(items)
                    return PendingTrade(**d)
        logger.warning(f"mark_unknown: trade {trade_id} not found or not in flight")
        return None

    def unresolved(self) -> list[PendingTrade]:
        """Rows that may be at the broker without an answer: submitting, unknown."""
        return [PendingTrade(**d) for d in self._read() if d.get("status") in UNRESOLVED]

    def in_flight(self) -> list[PendingTrade]:
        """Uncertain submissions AND acknowledged orders still working."""
        return [PendingTrade(**d) for d in self._read() if _active(d)]

    def release_unresolved(self, trade_id: str, *, by: str, note: str) -> Optional[PendingTrade]:
        """The OWNER's decision, after checking the broker, that an unresolved
        order was never placed. Nothing automatic calls this: a lookup that
        finds nothing is not proof (Codex review of e4f01ad). Recorded as
        rejected with who released it and why."""
        if not (by and note and note.strip()):
            raise ValueError("a release needs who and why")
        with _queue_guard():
            items = self._load_safe()
            for d in items:
                if d["id"] == trade_id and d["status"] in UNRESOLVED:
                    was = d["status"]
                    d["status"] = "rejected"
                    d["rejection_reason"] = (f"released by {by}: {note.strip()} "
                                             f"(was {was}; client id "
                                             f"{d.get('client_order_id') or 'n/a'})")
                    d["released_by"] = by
                    d["released_at"] = datetime.utcnow().isoformat()
                    _save_queue(items)
                    return PendingTrade(**d)
        return None

    def bind_legacy_evidence(self, trade_id: str, *, by: str, note: str,
                             broker_order_id: str = "", client_order_id: str = "") -> PendingTrade:
        """Owner-operated repair after inspecting broker statements, never a send.

        Binding is NOT settlement: the row remains UNKNOWN until the broker
        confirms it. Never invent a modern deterministic key for an old send.
        """
        if not by.strip() or not note.strip() or not (broker_order_id or client_order_id):
            raise ValueError("owner, broker evidence and a reconciliation note are required")
        with _queue_guard():
            items = self._load_safe()
            for row in items:
                if row["id"] != trade_id:
                    continue
                if not row.get("legacy_original") or row["status"] != "unknown":
                    raise QueueStateError("only unresolved legacy rows accept manual evidence")
                for key, value in (("broker_order_id", broker_order_id), ("client_order_id", client_order_id)):
                    if value:
                        if row.get(key) and row[key] != value:
                            raise QueueStateError("conflicting identity; retain original and investigate")
                        if any(other["id"] != trade_id and other.get("broker") == row.get("broker")
                               and other.get(key) == value for other in items):
                            raise QueueStateError("broker identity already bound to another queue row")
                        row[key] = value
                row["reconciliation_note"] = f"evidence bound by {by}: {note.strip()}"
                _save_queue(items)
                return PendingTrade(**row)
        raise KeyError(trade_id)

    def cancel(self, trade_id: str) -> bool:
        """Cancel a row that was NOT sent. "submitting" and later states are
        refused: the order may already be at the broker."""
        with _queue_guard():
            items = self._load_safe()
            for d in items:
                if d["id"] == trade_id and d["status"] in ("pending", "approved"):
                    d["status"] = "cancelled"
                    d["submission_version"] = 1
                    _save_queue(items)
                    return True
        return False

    def stats(self) -> dict:
        items = self._read()
        by_status: dict[str, int] = {}
        for d in items:
            by_status[d["status"]] = by_status.get(d["status"], 0) + 1
        return {"total": len(items), **by_status}
