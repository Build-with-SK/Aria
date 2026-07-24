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
    status:         str   # pending | approved | rejected | executed | cancelled
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

    def reject(self, trade_id: str, reason: str = "") -> Optional[PendingTrade]:
        with _QUEUE_LOCK:
            items = _load_queue()
            for d in items:
                if d["id"] == trade_id and d["status"] == "pending":
                    d["status"] = "rejected"
                    d["rejection_reason"] = reason
                    _save_queue(items)
                    return PendingTrade(**d)
        return None

    def mark_executed(self, trade_id: str, broker_order_id: str, fill_price: float = 0.0):
        with _QUEUE_LOCK:
            items = _load_queue()
            for d in items:
                if d["id"] == trade_id:
                    d["status"] = "executed"
                    d["broker_order_id"] = broker_order_id
                    d["fill_price"] = fill_price
                    d["executed_at"] = datetime.utcnow().isoformat()
                    _save_queue(items)
                    return
        logger.warning(f"mark_executed: trade {trade_id} not found")

    def cancel(self, trade_id: str) -> bool:
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
