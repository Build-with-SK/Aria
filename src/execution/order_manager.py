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
import time
from datetime import datetime, timedelta
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

            # Alpaca market orders come back "accepted", not "filled" — poll
            # until the fill confirms, THEN place the protective brackets.
            # A position must never sit naked because the ack was async.
            if result.status != OrderStatus.FILLED and (trade.stop_loss or trade.take_profit):
                result = self._await_fill(broker, result)

            sl_result = None
            tp_result = None
            fill_price = result.avg_fill_price or trade.limit_price or 0.0
            if result.status == OrderStatus.FILLED:
                if trade.stop_loss:
                    sl_result = self._place_stop_loss(broker, trade, fill_price)
                if trade.take_profit:
                    tp_result = self._place_take_profit(broker, trade, fill_price)
                if (trade.stop_loss and not sl_result) or (trade.take_profit and not tp_result):
                    logger.error(f"BRACKETS INCOMPLETE for {trade.ticker} — "
                                 f"reconciliation must heal on next desk tick")
            elif trade.stop_loss or trade.take_profit:
                logger.warning(f"{trade.ticker} entry not confirmed filled within poll "
                               f"window — brackets deferred to reconciliation")

            # Refresh queue with the real fill price once known
            if result.avg_fill_price:
                self._queue.mark_executed(trade_id,
                                          broker_order_id=result.broker_order_id,
                                          fill_price=result.avg_fill_price)

            return {
                "ok": True,
                "broker": broker.name,
                "broker_order_id": result.broker_order_id,
                "status": result.status.value,
                "fill_price": result.avg_fill_price,
                "filled_qty": result.filled_qty,
                "stop_loss_order": sl_result,
                "take_profit_order": tp_result,
                "brackets_pending": bool(
                    (trade.stop_loss and not sl_result) or
                    (trade.take_profit and not tp_result)),
            }
        else:
            self._queue.reject(trade_id, result.error_message)
            return {
                "ok": False,
                "broker": broker.name,
                "error": result.error_message or f"Order rejected with status {result.status.value}",
            }

    def _await_fill(self, broker: BrokerBase, result: OrderResult,
                    timeout_s: float = 30.0, poll_s: float = 2.0) -> OrderResult:
        """Poll the broker until the order fills (or the window closes).
        Returns the freshest OrderResult either way."""
        deadline = time.monotonic() + timeout_s
        latest = result
        while time.monotonic() < deadline:
            time.sleep(poll_s)
            latest = broker.get_order_status(result.broker_order_id)
            if latest.status == OrderStatus.FILLED:
                return latest
            if latest.status in (OrderStatus.CANCELLED, OrderStatus.REJECTED,
                                 OrderStatus.EXPIRED):
                return latest
        return latest

    def place_protective_orders(self, ticker: str, qty: float, entry_side: str,
                                stop: Optional[float], target: Optional[float],
                                asset_class: str = "equity",
                                broker: Optional[BrokerBase] = None) -> dict:
        """Place SL/TP for an already-held position (used by fill flow and by
        the desk's reconciliation pass when brackets are found missing)."""
        broker = broker or self._alpaca
        out = {"stop_loss_order": None, "take_profit_order": None}
        if not broker or not broker.is_connected():
            return out
        # Equities demand whole-cent prices — sub-penny is rejected (42210000)
        # — and fractional qtys cannot be GTC, so floor to whole shares
        # (the sub-share dust stays managed by the 5-min software tick).
        if asset_class != "crypto":
            stop = round(stop, 2) if stop else stop
            target = round(target, 2) if target else target
            qty = float(int(qty))
            if qty <= 0:
                return out
        # Both protections on one position need ONE OCO order — separate
        # stop + limit sells fight over the same reserved shares (40310000).
        if stop and target and hasattr(broker, "submit_oco_exit"):
            oid = broker.submit_oco_exit(ticker, qty, entry_side, stop, target)
            if oid:
                out["stop_loss_order"] = {"broker_order_id": oid, "stop_price": stop}
                out["take_profit_order"] = {"broker_order_id": oid, "limit_price": target}
                return out
            logger.warning(f"OCO exit failed for {ticker} — falling back to stop-only")
            target = None       # protect the downside at minimum
        exit_side = OrderSide.SELL if entry_side in ("buy", "long") else OrderSide.BUY
        try:
            if stop:
                r = broker.submit_order(OrderRequest(
                    ticker=ticker, side=exit_side, qty=qty,
                    order_type=OrderType.STOP, stop_price=stop,
                    time_in_force="gtc", asset_class=AssetClass(asset_class)))
                if r.status not in (OrderStatus.REJECTED,):
                    out["stop_loss_order"] = {"broker_order_id": r.broker_order_id,
                                              "stop_price": stop}
            if target:
                r = broker.submit_order(OrderRequest(
                    ticker=ticker, side=exit_side, qty=qty,
                    order_type=OrderType.LIMIT, limit_price=target,
                    time_in_force="gtc", asset_class=AssetClass(asset_class)))
                if r.status not in (OrderStatus.REJECTED,):
                    out["take_profit_order"] = {"broker_order_id": r.broker_order_id,
                                                "limit_price": target}
        except Exception as e:
            logger.error(f"place_protective_orders({ticker}) failed: {e}")
        return out

    def _connected_brokers(self) -> list[BrokerBase]:
        return [b for b in (self._alpaca, self._ibkr) if b and b.is_connected()]

    def open_orders_by_ticker(self, broker: Optional[BrokerBase] = None) -> dict:
        """{ticker: [open order dicts]} — for bracket healing and stale cleanup.

        Single-broker by default, and that is deliberate rather than an
        oversight. Merging every broker's orders into one dict keyed only by
        ticker briefly looked like the fix for "IBKR's open orders are never
        read", and a review pointed out what it actually did: consumers pass
        each order's id straight back to ONE broker's cancel_order, and
        bracket healing concluded an Alpaca position was protected because an
        IBKR stop existed on the same symbol — a naked position produced by a
        safety check.

        So callers that mean "every broker" say so by passing one explicitly in
        a loop (see cancel_stale_orders). IBKR's orders are read there.
        """
        brokers = [broker] if broker is not None else (
            [self._alpaca] if self._alpaca else [])
        grouped: dict = {}
        for b in brokers:
            if not b or not b.is_connected():
                continue
            try:
                for o in b.get_open_orders():
                    grouped.setdefault(o["ticker"], []).append(
                        {**o, "broker": getattr(b, "name", "")})
            except Exception as e:
                logger.error(f"open_orders_by_ticker({getattr(b, 'name', '?')}): {e}")
        return grouped

    def cancel_stale_orders(self, max_age_days: float = 1.0) -> list[dict]:
        """Cancel unfilled orders older than max_age_days for tickers with NO
        open position (i.e. stale entries). Protective GTC stops/targets belong
        to held tickers and are deliberately left alone."""
        cutoff = datetime.utcnow() - timedelta(days=max_age_days)
        cancelled = []
        for broker in self._connected_brokers():
            held = {p.ticker for p in broker.get_positions()}
            for ticker, orders in self.open_orders_by_ticker(broker).items():
                if ticker in held:
                    continue
                for o in orders:
                    raw = (o.get("submitted_at") or "").replace("Z", "+00:00")
                    try:
                        sub = datetime.fromisoformat(raw).replace(tzinfo=None)
                    except Exception:
                        # An unparseable timestamp means "cannot judge its
                        # age", which is not the same as "not stale". Say so
                        # rather than skipping in silence — a broker adapter
                        # returning the wrong shape here disables the whole
                        # cleanup and looks exactly like having nothing stale.
                        logger.warning(
                            "stale-order check: %s order %s has an unreadable "
                            "submitted_at (%r) — leaving it alone",
                            getattr(broker, "name", "?"), o.get("id"), raw)
                        continue
                    if sub < cutoff and broker.cancel_order(o["id"]):
                        cancelled.append(o)
                        logger.info(f"Cancelled stale order {o['id']} "
                                    f"({o['side']} {o['qty']:g} {ticker})")
        return cancelled

    def _place_stop_loss(self, broker: BrokerBase, trade: PendingTrade, fill_price: float) -> Optional[dict]:
        try:
            # Stop-loss is opposite side
            sl_side = OrderSide.SELL if trade.side == "buy" else OrderSide.BUY
            stop_price = (trade.stop_loss if trade.asset_class == "crypto"
                          else round(trade.stop_loss, 2))
            req = OrderRequest(
                ticker=trade.ticker,
                side=sl_side,
                qty=trade.qty,
                order_type=OrderType.STOP,
                stop_price=stop_price,
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
            limit_price = (trade.take_profit if trade.asset_class == "crypto"
                           else round(trade.take_profit, 2))
            req = OrderRequest(
                ticker=trade.ticker,
                side=tp_side,
                qty=trade.qty,
                order_type=OrderType.LIMIT,
                limit_price=limit_price,
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
