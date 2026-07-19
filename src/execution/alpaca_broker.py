"""
src/execution/alpaca_broker.py
==============================
Alpaca Markets broker adapter.
Handles: US Equities, ETFs, Crypto.
Paper trading by default — set ALPACA_PAPER=false in env to go live.

Requires:
  pip install alpaca-py

Keys in .env:
  ALPACA_API_KEY=...
  ALPACA_SECRET_KEY=...
  ALPACA_PAPER=true   # or false for live
"""
from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Optional

from .broker_base import (
    AssetClass, BrokerBase, AccountInfo, OrderRequest, OrderResult,
    OrderSide, OrderStatus, OrderType, Position
)

logger = logging.getLogger(__name__)

try:
    from alpaca.trading.client import TradingClient
    from alpaca.trading.requests import (
        MarketOrderRequest, LimitOrderRequest,
        StopOrderRequest, StopLimitOrderRequest,
        GetOrderByIdRequest,
    )
    from alpaca.trading.enums import (
        OrderSide as AlpacaSide,
        TimeInForce, OrderType as AlpacaOrderType,
    )
    from alpaca.data.historical import StockHistoricalDataClient, CryptoHistoricalDataClient
    from alpaca.data.requests import StockLatestQuoteRequest, CryptoLatestQuoteRequest
    ALPACA_AVAILABLE = True
except ImportError:
    ALPACA_AVAILABLE = False
    logger.warning("alpaca-py not installed. Run: pip install alpaca-py")


def _to_alpaca_side(side: OrderSide) -> "AlpacaSide":
    return AlpacaSide.BUY if side == OrderSide.BUY else AlpacaSide.SELL


def _to_alpaca_tif(tif: str) -> "TimeInForce":
    mapping = {
        "day": TimeInForce.DAY,
        "gtc": TimeInForce.GTC,
        "ioc": TimeInForce.IOC,
        "fok": TimeInForce.FOK,
    }
    return mapping.get(tif.lower(), TimeInForce.DAY)


def _parse_status(alpaca_status: str) -> OrderStatus:
    mapping = {
        "new":              OrderStatus.SUBMITTED,
        "partially_filled": OrderStatus.PARTIAL,
        "filled":           OrderStatus.FILLED,
        "done_for_day":     OrderStatus.EXPIRED,
        "canceled":         OrderStatus.CANCELLED,
        "expired":          OrderStatus.EXPIRED,
        "replaced":         OrderStatus.CANCELLED,
        "pending_cancel":   OrderStatus.SUBMITTED,
        "pending_replace":  OrderStatus.SUBMITTED,
        "accepted":         OrderStatus.SUBMITTED,
        "rejected":         OrderStatus.REJECTED,
    }
    return mapping.get(alpaca_status, OrderStatus.SUBMITTED)


class AlpacaBroker(BrokerBase):
    """Alpaca Markets adapter — equities + crypto."""

    def __init__(self):
        self._api_key    = os.getenv("ALPACA_API_KEY", "")
        self._secret_key = os.getenv("ALPACA_SECRET_KEY", "")
        self._is_paper   = os.getenv("ALPACA_PAPER", "true").lower() != "false"
        self._client: Optional["TradingClient"] = None
        self._stock_data: Optional["StockHistoricalDataClient"] = None
        self._crypto_data: Optional["CryptoHistoricalDataClient"] = None

        if ALPACA_AVAILABLE and self._api_key:
            try:
                self._client = TradingClient(
                    api_key=self._api_key,
                    secret_key=self._secret_key,
                    paper=self._is_paper,
                )
                self._stock_data  = StockHistoricalDataClient(self._api_key, self._secret_key)
                self._crypto_data = CryptoHistoricalDataClient(self._api_key, self._secret_key)
                logger.info(f"Alpaca connected ({'PAPER' if self._is_paper else 'LIVE'})")
            except Exception as e:
                logger.error(f"Alpaca connection failed: {e}")

    @property
    def name(self) -> str:
        return "Alpaca"

    @property
    def paper(self) -> bool:
        return self._is_paper

    def is_connected(self) -> bool:
        if not ALPACA_AVAILABLE or not self._client:
            return False
        try:
            self._client.get_account()
            return True
        except Exception:
            return False

    def supports_asset(self, asset_class: AssetClass) -> bool:
        return asset_class in (AssetClass.EQUITY, AssetClass.CRYPTO)

    def get_account(self) -> AccountInfo:
        if not self._client:
            return AccountInfo(broker="Alpaca", cash=0, portfolio_value=0, buying_power=0)
        acct = self._client.get_account()
        return AccountInfo(
            broker="Alpaca",
            cash=float(acct.cash),
            portfolio_value=float(acct.portfolio_value),
            buying_power=float(acct.buying_power),
            day_trade_count=int(acct.daytrade_count or 0),
            currency="USD",
        )

    def get_positions(self) -> list[Position]:
        if not self._client:
            return []
        try:
            positions = self._client.get_all_positions()
            return [
                Position(
                    ticker=p.symbol,
                    qty=float(p.qty),
                    avg_cost=float(p.avg_entry_price),
                    market_value=float(p.market_value),
                    unrealized_pl=float(p.unrealized_pl),
                    side="long" if float(p.qty) > 0 else "short",
                    asset_class=AssetClass.CRYPTO if "-USD" in p.symbol else AssetClass.EQUITY,
                )
                for p in positions
            ]
        except Exception as e:
            logger.error(f"Alpaca get_positions error: {e}")
            return []

    def get_position(self, ticker: str) -> Optional[Position]:
        positions = self.get_positions()
        for p in positions:
            if p.ticker == ticker:
                return p
        return None

    def submit_order(self, req: OrderRequest) -> OrderResult:
        if not self._client:
            return OrderResult(
                broker_order_id="",
                status=OrderStatus.REJECTED,
                error_message="Alpaca client not connected",
            )
        try:
            side = _to_alpaca_side(req.side)
            tif  = _to_alpaca_tif(req.time_in_force)

            if req.order_type == OrderType.MARKET:
                order_req = MarketOrderRequest(
                    symbol=req.ticker,
                    qty=req.qty,
                    side=side,
                    time_in_force=tif,
                )
            elif req.order_type == OrderType.LIMIT:
                order_req = LimitOrderRequest(
                    symbol=req.ticker,
                    qty=req.qty,
                    side=side,
                    time_in_force=tif,
                    limit_price=req.limit_price,
                )
            elif req.order_type == OrderType.STOP:
                order_req = StopOrderRequest(
                    symbol=req.ticker,
                    qty=req.qty,
                    side=side,
                    time_in_force=tif,
                    stop_price=req.stop_price,
                )
            elif req.order_type == OrderType.STOP_LIMIT:
                order_req = StopLimitOrderRequest(
                    symbol=req.ticker,
                    qty=req.qty,
                    side=side,
                    time_in_force=tif,
                    limit_price=req.limit_price,
                    stop_price=req.stop_price,
                )
            else:
                order_req = MarketOrderRequest(
                    symbol=req.ticker, qty=req.qty, side=side, time_in_force=tif
                )

            order = self._client.submit_order(order_data=order_req)
            return OrderResult(
                broker_order_id=str(order.id),
                status=_parse_status(str(order.status)),
                filled_qty=float(order.filled_qty or 0),
                avg_fill_price=float(order.filled_avg_price or 0),
                submitted_at=datetime.utcnow(),
                raw={"id": str(order.id), "status": str(order.status)},
            )
        except Exception as e:
            logger.error(f"Alpaca submit_order error: {e}")
            return OrderResult(
                broker_order_id="",
                status=OrderStatus.REJECTED,
                error_message=str(e),
            )

    def cancel_order(self, broker_order_id: str) -> bool:
        if not self._client:
            return False
        try:
            self._client.cancel_order_by_id(broker_order_id)
            return True
        except Exception as e:
            logger.error(f"Alpaca cancel_order error: {e}")
            return False

    def get_order_status(self, broker_order_id: str) -> OrderResult:
        if not self._client:
            return OrderResult(broker_order_id=broker_order_id, status=OrderStatus.REJECTED)
        try:
            order = self._client.get_order_by_id(broker_order_id)
            return OrderResult(
                broker_order_id=broker_order_id,
                status=_parse_status(str(order.status)),
                filled_qty=float(order.filled_qty or 0),
                avg_fill_price=float(order.filled_avg_price or 0),
                filled_at=order.filled_at,
            )
        except Exception as e:
            return OrderResult(
                broker_order_id=broker_order_id,
                status=OrderStatus.REJECTED,
                error_message=str(e),
            )

    def get_open_orders(self) -> list[dict]:
        if not self._client:
            return []
        try:
            from alpaca.trading.requests import GetOrdersRequest
            from alpaca.trading.enums import QueryOrderStatus
            orders = self._client.get_orders(
                GetOrdersRequest(status=QueryOrderStatus.OPEN, limit=500))
            return [{
                "id": str(o.id),
                "ticker": str(o.symbol),
                "side": str(o.side.value if hasattr(o.side, "value") else o.side),
                "order_type": str(o.type.value if hasattr(o.type, "value") else o.type),
                "qty": float(o.qty or 0),
                "stop_price": float(o.stop_price) if o.stop_price else None,
                "limit_price": float(o.limit_price) if o.limit_price else None,
                "submitted_at": o.submitted_at.isoformat() if o.submitted_at else "",
                "status": str(o.status.value if hasattr(o.status, "value") else o.status),
            } for o in orders]
        except Exception as e:
            logger.error(f"Alpaca get_open_orders error: {e}")
            return []

    def get_quote(self, ticker: str) -> dict:
        if not self._stock_data:
            return {}
        try:
            if "-USD" in ticker:
                req    = CryptoLatestQuoteRequest(symbol_or_symbols=[ticker])
                quotes = self._crypto_data.get_crypto_latest_quote(req)
            else:
                req    = StockLatestQuoteRequest(symbol_or_symbols=[ticker])
                quotes = self._stock_data.get_stock_latest_quote(req)
            q = quotes.get(ticker)
            if not q:
                return {}
            return {
                "ask":  float(q.ask_price),
                "bid":  float(q.bid_price),
                "mid":  round((float(q.ask_price) + float(q.bid_price)) / 2, 4),
            }
        except Exception as e:
            logger.warning(f"Alpaca get_quote({ticker}): {e}")
            return {}
