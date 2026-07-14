"""
src/execution/order_manager.py
================================
Orchestrates approved trades to the correct broker.
Called by the FastAPI endpoint after user approval.

Flow:
  1. User clicks APPROVE in UI
  2. API calls order_manager.execute(trade_id)
  3. OrderManager looks up the trade in ApprovalQueue
  4. Routes to Alpaca (equities/crypto) or IBKR (futures/forex/options)
  5. Submits order, updates queue with fill details
  6. Optionally places bracket stop-loss + take-profit orders
"""
from __future__ import annotations

import logging
from typing import Optional

from .approval_queue import ApprovalQueue, PendingTrade
from .broker_base import (
    AssetClass, OrderRequest, OrderResult, OrderSide,
    OrderStatus, OrderType, BrokerBase
)

logger = logging.getLogger(__name__)


class OrderManager:
    def __init__(self, alpaca: Optional[BrokerBase] = None, ibkr: Optional[BrokerBase] = None):
        self._alpaca = alpaca
        self._ibkr   = ibkr
        self._queue  = ApprovalQueue()

    def _broker_for(self, trade: PendingTrade) -> Optional[BrokerBase]:
        if trade.broker == "ibkr":
            return self._ibkr
        if trade.broker == "alpaca":
            return self._alpaca
        # Auto: IBKR for futures/forex, Alpaca for everything else
        ac = trade.asset_class
        if ac in ("futures", "forex", "option"):
            return self._ibkr or self._alpaca
        return self._alpaca or self._ibkr

    def _build_order_request(self, trade: PendingTrade) -> OrderRequest:
        return OrderRequest(
            ticker=trade.ticker,
            side=OrderSide(trade.side),
            qty=trade.qty,
            order_type=OrderType(trade.order_type),
            limit_price=trade.limit_price,
            stop_price=trade.stop_price,
            time_in_force=trade.time_in_force,
            asset_class=AssetClass(trade.asset_class),
            stop_loss=trade.stop_loss,
            take_profit=trade.take_profit,
            signal_score=trade.signal_score,
            situation=trade.situation,
            thesis_summary=trade.thesis_summary,
        )

    def execute(self, trade_id: str) -> dict:
        """Execute an approved trade. Returns status dict."""
        trade = self._queue.get_by_id(trade_id)
        if not trade:
            return {"ok": False, "error": f"Trade {trade_id} not found"}
        if trade.status not in ("pending", "approved"):
            return {"ok": False, "error": f"Trade {trade_id} status is '{trade.status}', not approvable"}

        # Mark approved
        self._queue.approve(trade_id)

        broker = self._broker_for(trade)
        if not broker:
            self._queue.reject(trade_id, "No broker connected")
            return {"ok": False, "error": "No broker connected for this asset class"}

        if not broker.is_connected():
            self._queue.reject(trade_id, f"{broker.name} not connected")
            return {"ok": False, "error": f"{broker.name} is not connected"}

        req    = self._build_order_request(trade)
        result = broker.submit_order(req)

        if result.status in (OrderStatus.SUBMITTED, OrderStatus.FILLED, OrderStatus.PARTIAL):
            self._queue.mark_executed(
                trade_id,
                broker_order_id=result.broker_order_id,
                fill_price=result.avg_fill_price,
            )
            logger.info(
                f"Order executed [{trade_id}]: {trade.side.upper()} {trade.qty} {trade.ticker} "
                f"via {broker.name} → order_id={result.broker_order_id}"
            )

            # Place protective stop-loss if provided and broker supports it
            sl_result = None
            tp_result = None
            if trade.stop_loss and result.status == OrderStatus.FILLED:
                sl_result = self._place_stop_loss(broker, trade, result.avg_fill_price)
            if trade.take_profit and result.status == OrderStatus.FILLED:
                tp_result = self._place_take_profit(broker, trade, result.avg_fill_price)

            return {
                "ok": True,
                "broker": broker.name,
                "broker_order_id": result.broker_order_id,
                "status": result.status.value,
                "fill_price": result.avg_fill_price,
                "filled_qty": result.filled_qty,
                "stop_loss_order": sl_result,
                "take_profit_order": tp_result,
            }
        else:
            self._queue.reject(trade_id, result.error_message)
            return {
                "ok": False,
                "broker": broker.name,
                "error": result.error_message or f"Order rejected with status {result.status.value}",
            }

    def _place_stop_loss(self, broker: BrokerBase, trade: PendingTrade, fill_price: float) -> Optional[dict]:
        try:
            # Stop-loss is opposite side
            sl_side = OrderSide.SELL if trade.side == "buy" else OrderSide.BUY
            req = OrderRequest(
                ticker=trade.ticker,
                side=sl_side,
                qty=trade.qty,
                order_type=OrderType.STOP,
                stop_price=trade.stop_loss,
                time_in_force="gtc",
                asset_class=AssetClass(trade.asset_class),
            )
            result = broker.submit_order(req)
            logger.info(f"Stop-loss placed for {trade.ticker} @ {trade.stop_loss}")
            return {"broker_order_id": result.broker_order_id, "stop_price": trade.stop_loss}
        except Exception as e:
            logger.error(f"Failed to place stop-loss: {e}")
            return None

    def _place_take_profit(self, broker: BrokerBase, trade: PendingTrade, fill_price: float) -> Optional[dict]:
        try:
            tp_side = OrderSide.SELL if trade.side == "buy" else OrderSide.BUY
            req = OrderRequest(
                ticker=trade.ticker,
                side=tp_side,
                qty=trade.qty,
                order_type=OrderType.LIMIT,
                limit_price=trade.take_profit,
                time_in_force="gtc",
                asset_class=AssetClass(trade.asset_class),
            )
            result = broker.submit_order(req)
            logger.info(f"Take-profit placed for {trade.ticker} @ {trade.take_profit}")
            return {"broker_order_id": result.broker_order_id, "limit_price": trade.take_profit}
        except Exception as e:
            logger.error(f"Failed to place take-profit: {e}")
            return None

    def get_broker_status(self) -> dict:
        alpaca_connected = self._alpaca.is_connected() if self._alpaca else False
        ibkr_connected   = self._ibkr.is_connected()   if self._ibkr   else False

        result = {
            "alpaca": {
                "connected": alpaca_connected,
                "paper": self._alpaca.paper if self._alpaca else None,
            },
            "ibkr": {
                "connected": ibkr_connected,
                "paper": self._ibkr.paper if self._ibkr else None,
            },
        }

        if alpaca_connected:
            try:
                acct = self._alpaca.get_account()
                result["alpaca"]["account"] = {
                    "cash": acct.cash,
                    "portfolio_value": acct.portfolio_value,
                    "buying_power": acct.buying_power,
                }
            except Exception:
                pass

        if ibkr_connected:
            try:
                acct = self._ibkr.get_account()
                result["ibkr"]["account"] = {
                    "cash": acct.cash,
                    "portfolio_value": acct.portfolio_value,
                    "buying_power": acct.buying_power,
                }
            except Exception:
                pass

        return result

    def get_all_positions(self) -> list[dict]:
        positions = []
        if self._alpaca and self._alpaca.is_connected():
            for p in self._alpaca.get_positions():
                positions.append({**p.__dict__, "broker": "Alpaca"})
        if self._ibkr and self._ibkr.is_connected():
            for p in self._ibkr.get_positions():
                positions.append({**p.__dict__, "broker": "IBKR"})
        return positions
