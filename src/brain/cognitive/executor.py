"""
src/brain/cognitive/executor.py
===============================
EXECUTOR — pushes planned trades into the existing approval queue.
The brain PROPOSES, the human APPROVES. This module never calls
approve_and_execute; every trade waits in the queue for the user.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class BrainExecutor:
    def execute_plan(self, planned_trades: list) -> list:
        """
        Pushes each PlannedTrade into the ApprovalQueue.
        Returns list of trade_ids that were queued.
        Skips any ticker that already has a pending trade.
        """
        from src.execution.approval_queue import ApprovalQueue
        from src.execution.broker_base import (
            AssetClass, OrderRequest, OrderSide, OrderType,
        )
        queue = ApprovalQueue()
        existing = {t.ticker for t in queue.get_all(limit=500) if t.status == "pending"}
        queued = []
        for trade in planned_trades:
            if trade.ticker in existing:
                continue
            req = OrderRequest(
                ticker=trade.ticker,
                side=OrderSide(trade.side),
                qty=trade.qty,
                order_type=OrderType.MARKET,
                asset_class=AssetClass(trade.asset_class),
                stop_loss=trade.stop_loss,
                take_profit=trade.take_profit,
                signal_score=trade.signal_score,
                situation="ARIA brain autonomous cycle",
                thesis_summary=trade.thesis,
            )
            result = queue.push(
                req,
                confidence=f"{trade.conviction}%",
                bull_case=trade.thesis,
                broker=trade.broker,
                current_price=getattr(trade, "current_price", 0.0),
            )
            queued.append(result.id)
            existing.add(trade.ticker)
        if queued:
            logger.info(f"Brain queued {len(queued)} trade(s): {queued}")
        return queued
