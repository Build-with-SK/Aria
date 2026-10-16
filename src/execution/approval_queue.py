"""
src/execution/approval_queue.py
================================
Persistent approval queue — every trade ARIA wants to make sits here
until the user approves or rejects it via the UI.

File-backed: data/execution/approval_queue.json
"""
from __future__ import annotations

import json
import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from .broker_base import AssetClass, OrderRequest, OrderSide, OrderType

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
    # An unresolved order the OWNER released after checking the broker.
    released_by:     str = ""
    released_at:     str = ""


UNRESOLVED = ("submitting", "unknown")


def _load_queue() -> list[dict]:
    QUEUE_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not QUEUE_FILE.exists():
        return []
    try:
        return json.loads(QUEUE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save_queue(items: list[dict]):
    QUEUE_FILE.parent.mkdir(parents=True, exist_ok=True)
    QUEUE_FILE.write_text(json.dumps(items, indent=2, default=str), encoding="utf-8")


class ApprovalQueue:
    """Thread-safe(ish) in-process approval queue backed by JSON."""

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
            id=str(uuid.uuid4())[:8],
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
        )

        with _QUEUE_LOCK:
            items = _load_queue()
            items.append(asdict(trade))
            _save_queue(items)
        logger.info(f"Trade queued [{trade.id}]: {trade.side.upper()} {trade.qty} {trade.ticker} via {broker}")
        return trade

    def get_pending(self) -> list[PendingTrade]:
        return [PendingTrade(**d) for d in _load_queue() if d["status"] == "pending"]

    def get_all(self, limit: int = 100) -> list[PendingTrade]:
        items = _load_queue()
        return [PendingTrade(**d) for d in items[-limit:]]

    def get_by_id(self, trade_id: str) -> Optional[PendingTrade]:
        for d in _load_queue():
            if d["id"] == trade_id:
                return PendingTrade(**d)
        return None

    def approve(self, trade_id: str) -> Optional[PendingTrade]:
        with _QUEUE_LOCK:
            items = _load_queue()
            for d in items:
                if d["id"] == trade_id and d["status"] == "pending":
                    d["status"] = "approved"
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
        with _QUEUE_LOCK:
            items = _load_queue()
            for d in items:
                if d["id"] == trade_id and d["status"] in allowed:
                    d["status"] = "rejected"
                    d["rejection_reason"] = reason
                    if client_order_id:
                        d["client_order_id"] = client_order_id
                    _save_queue(items)
                    return PendingTrade(**d)
        return None

    def claim_for_submit(self, trade_id: str, client_order_id: str) -> Optional[PendingTrade]:
        """Atomically move an approved (or pending) row to "submitting" with its
        client id recorded — BEFORE anything is sent. None when the row is not
        sendable any more (cancelled, already claimed): then nothing may be sent.
        From here the row cannot be cancelled as "never reached the broker"."""
        with _QUEUE_LOCK:
            items = _load_queue()
            for d in items:
                if d["id"] == trade_id and d["status"] in ("pending", "approved"):
                    d["status"] = "submitting"
                    d["client_order_id"] = client_order_id
                    d["submitting_at"] = datetime.utcnow().isoformat()
                    _save_queue(items)
                    return PendingTrade(**d)
        return None

    def mark_executed(self, trade_id: str, broker_order_id: str, fill_price: float = 0.0,
                      *, client_order_id: str = "", order_status: str = "",
                      filled_qty: Optional[float] = None):
        """The broker acknowledged the order. `order_status` says how far it got
        (submitted / partial / filled); `executed` alone never meant filled."""
        with _QUEUE_LOCK:
            items = _load_queue()
            for d in items:
                if d["id"] == trade_id:
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
                    if filled_qty is not None:
                        d["filled_qty"] = float(filled_qty)
                    _save_queue(items)
                    return
        logger.warning(f"mark_executed: trade {trade_id} not found")

    def mark_ended_unfilled(self, trade_id: str, order_status: str, reason: str) -> Optional[PendingTrade]:
        """The broker acknowledged the order and later cancelled, rejected or
        expired it with NOTHING filled. Recorded as rejected with the broker's
        status — never left reading "executed"."""
        with _QUEUE_LOCK:
            items = _load_queue()
            for d in items:
                if d["id"] == trade_id and d["status"] in ("submitting", "executed", "unknown"):
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
        with _QUEUE_LOCK:
            items = _load_queue()
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
        return [PendingTrade(**d) for d in _load_queue() if d.get("status") in UNRESOLVED]

    def release_unresolved(self, trade_id: str, *, by: str, note: str) -> Optional[PendingTrade]:
        """The OWNER's decision, after checking the broker, that an unresolved
        order was never placed. Nothing automatic calls this: a lookup that
        finds nothing is not proof (Codex review of e4f01ad). Recorded as
        rejected with who released it and why."""
        if not (by and note and note.strip()):
            raise ValueError("a release needs who and why")
        with _QUEUE_LOCK:
            items = _load_queue()
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

    def cancel(self, trade_id: str) -> bool:
        """Cancel a row that was NOT sent. "submitting" and later states are
        refused: the order may already be at the broker."""
        with _QUEUE_LOCK:
            items = _load_queue()
            for d in items:
                if d["id"] == trade_id and d["status"] in ("pending", "approved"):
                    d["status"] = "cancelled"
                    _save_queue(items)
                    return True
        return False

    def stats(self) -> dict:
        items = _load_queue()
        by_status: dict[str, int] = {}
        for d in items:
            by_status[d["status"]] = by_status.get(d["status"], 0) + 1
        return {"total": len(items), **by_status}
