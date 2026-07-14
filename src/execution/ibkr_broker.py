"""
src/execution/ibkr_broker.py
=============================
Interactive Brokers adapter via ib_insync.
Handles: Stocks, Options, Futures, Forex, Crypto.

Requires:
  pip install ib_insync
  TWS or IB Gateway running locally with API enabled on port 7497 (paper) or 7496 (live)

.env keys:
  IBKR_HOST=127.0.0.1
  IBKR_PORT=7497          # 7497 = paper TWS, 7496 = live TWS, 4002 = paper gateway, 4001 = live gateway
  IBKR_CLIENT_ID=1
  IBKR_PAPER=true
"""
from __future__ import annotations

import logging
import os
from typing import Optional

from .broker_base import (
    AssetClass, BrokerBase, AccountInfo, OrderRequest, OrderResult,
    OrderSide, OrderStatus, OrderType, Position
)

logger = logging.getLogger(__name__)

try:
    import ib_insync as ibi
    IBKR_AVAILABLE = True
except ImportError:
    IBKR_AVAILABLE = False
    logger.warning("ib_insync not installed. Run: pip install ib_insync")


def _make_contract(ticker: str, asset_class: AssetClass) -> "ibi.Contract":
    """Build an IBKR Contract object from ticker + asset class."""
    if asset_class == AssetClass.FUTURES:
        # e.g. "ES=F" → ES
        sym = ticker.split("=")[0]
        return ibi.Future(sym, exchange="CME")
    elif asset_class == AssetClass.FOREX:
        # e.g. "EURUSD=X" → EUR.USD
        base = ticker.replace("=X", "").replace("/", "")
        pair = f"{base[:3]}.{base[3:]}" if len(base) == 6 else base
        return ibi.Forex(pair)
    elif asset_class == AssetClass.CRYPTO:
        sym = ticker.replace("-USD", "")
        return ibi.Crypto(sym, "PAXOS", "USD")
    else:
        return ibi.Stock(ticker, "SMART", "USD")


def _parse_ibkr_status(status: str) -> OrderStatus:
    mapping = {
        "PreSubmitted": OrderStatus.SUBMITTED,
        "Submitted":    OrderStatus.SUBMITTED,
        "Filled":       OrderStatus.FILLED,
        "Cancelled":    OrderStatus.CANCELLED,
        "Inactive":     OrderStatus.REJECTED,
        "ApiCancelled": OrderStatus.CANCELLED,
    }
    return mapping.get(status, OrderStatus.SUBMITTED)


class IBKRBroker(BrokerBase):
    """Interactive Brokers adapter — full asset class support."""

    def __init__(self):
        self._host      = os.getenv("IBKR_HOST", "127.0.0.1")
        self._port      = int(os.getenv("IBKR_PORT", "7497"))
        self._client_id = int(os.getenv("IBKR_CLIENT_ID", "1"))
        self._is_paper  = os.getenv("IBKR_PAPER", "true").lower() != "false"
        self._ib: Optional["ibi.IB"] = None

        if IBKR_AVAILABLE:
            try:
                self._ib = ibi.IB()
                self._ib.connect(self._host, self._port, clientId=self._client_id, timeout=3)
                logger.info(f"IBKR connected ({'PAPER' if self._is_paper else 'LIVE'}) on port {self._port}")
            except Exception as e:
                logger.warning(f"IBKR connection failed (TWS/Gateway may not be running): {e}")
                self._ib = None

    @property
    def name(self) -> str:
        return "IBKR"

    @property
    def paper(self) -> bool:
        return self._is_paper

    def is_connected(self) -> bool:
        return IBKR_AVAILABLE and self._ib is not None and self._ib.isConnected()

    def get_account(self) -> AccountInfo:
        if not self.is_connected():
            return AccountInfo(broker="IBKR", cash=0, portfolio_value=0, buying_power=0)
        try:
            vals = self._ib.accountValues()
            def _get(tag: str) -> float:
                for v in vals:
                    if v.tag == tag and v.currency == "USD":
                        return float(v.value)
                return 0.0
            return AccountInfo(
                broker="IBKR",
                cash=_get("CashBalance"),
                portfolio_value=_get("NetLiquidation"),
                buying_power=_get("BuyingPower"),
                currency="USD",
            )
        except Exception as e:
            logger.error(f"IBKR get_account: {e}")
            return AccountInfo(broker="IBKR", cash=0, portfolio_value=0, buying_power=0)

    def get_positions(self) -> list[Position]:
        if not self.is_connected():
            return []
        try:
            result = []
            for p in self._ib.positions():
                sym = p.contract.symbol
                ac  = AssetClass.FUTURES if isinstance(p.contract, ibi.Future) else \
                      AssetClass.FOREX   if isinstance(p.contract, ibi.Forex)  else \
                      AssetClass.CRYPTO  if isinstance(p.contract, ibi.Crypto) else \
                      AssetClass.EQUITY
                result.append(Position(
                    ticker=sym,
                    qty=float(p.position),
                    avg_cost=float(p.avgCost),
                    market_value=float(p.position) * float(p.avgCost),
                    unrealized_pl=0.0,  # requires market data subscription
                    side="long" if float(p.position) > 0 else "short",
                    asset_class=ac,
                ))
            return result
        except Exception as e:
            logger.error(f"IBKR get_positions: {e}")
            return []

    def get_position(self, ticker: str) -> Optional[Position]:
        for p in self.get_positions():
            if p.ticker == ticker:
                return p
        return None

    def submit_order(self, req: OrderRequest) -> OrderResult:
        if not self.is_connected():
            return OrderResult(
                broker_order_id="",
                status=OrderStatus.REJECTED,
                error_message="IBKR not connected. Start TWS or IB Gateway.",
            )
        try:
            contract = _make_contract(req.ticker, req.asset_class)
            self._ib.qualifyContracts(contract)

            side = "BUY" if req.side == OrderSide.BUY else "SELL"

            if req.order_type == OrderType.MARKET:
                order = ibi.MarketOrder(side, req.qty)
            elif req.order_type == OrderType.LIMIT:
                order = ibi.LimitOrder(side, req.qty, req.limit_price)
            elif req.order_type == OrderType.STOP:
                order = ibi.StopOrder(side, req.qty, req.stop_price)
            elif req.order_type == OrderType.STOP_LIMIT:
                order = ibi.StopLimitOrder(side, req.qty, req.limit_price, req.stop_price)
            else:
                order = ibi.MarketOrder(side, req.qty)

            order.tif = req.time_in_force.upper()
            trade = self._ib.placeOrder(contract, order)
            self._ib.sleep(1)  # wait for acknowledgement

            return OrderResult(
                broker_order_id=str(trade.order.orderId),
                status=_parse_ibkr_status(trade.orderStatus.status),
                filled_qty=float(trade.orderStatus.filled),
                avg_fill_price=float(trade.orderStatus.avgFillPrice),
                raw={"orderId": trade.order.orderId, "status": trade.orderStatus.status},
            )
        except Exception as e:
            logger.error(f"IBKR submit_order: {e}")
            return OrderResult(
                broker_order_id="",
                status=OrderStatus.REJECTED,
                error_message=str(e),
            )

    def cancel_order(self, broker_order_id: str) -> bool:
        if not self.is_connected():
            return False
        try:
            for trade in self._ib.openTrades():
                if str(trade.order.orderId) == broker_order_id:
                    self._ib.cancelOrder(trade.order)
                    return True
            return False
        except Exception as e:
            logger.error(f"IBKR cancel_order: {e}")
            return False

    def get_order_status(self, broker_order_id: str) -> OrderResult:
        if not self.is_connected():
            return OrderResult(broker_order_id=broker_order_id, status=OrderStatus.REJECTED)
        try:
            for trade in self._ib.openTrades():
                if str(trade.order.orderId) == broker_order_id:
                    return OrderResult(
                        broker_order_id=broker_order_id,
                        status=_parse_ibkr_status(trade.orderStatus.status),
                        filled_qty=float(trade.orderStatus.filled),
                        avg_fill_price=float(trade.orderStatus.avgFillPrice),
                    )
            return OrderResult(broker_order_id=broker_order_id, status=OrderStatus.FILLED)
        except Exception as e:
            return OrderResult(
                broker_order_id=broker_order_id,
                status=OrderStatus.REJECTED,
                error_message=str(e),
            )

    def get_quote(self, ticker: str) -> dict:
        if not self.is_connected():
            return {}
        try:
            contract = ibi.Stock(ticker, "SMART", "USD")
            self._ib.qualifyContracts(contract)
            [ticker_data] = self._ib.reqTickers(contract)
            return {
                "ask":  float(ticker_data.ask  or 0),
                "bid":  float(ticker_data.bid  or 0),
                "last": float(ticker_data.last or 0),
                "mid":  round((float(ticker_data.ask or 0) + float(ticker_data.bid or 0)) / 2, 4),
            }
        except Exception as e:
            logger.warning(f"IBKR get_quote({ticker}): {e}")
            return {}

    def disconnect(self):
        if self._ib and self._ib.isConnected():
            self._ib.disconnect()
