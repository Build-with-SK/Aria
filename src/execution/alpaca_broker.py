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


def crypto_symbol(ticker: str) -> str:
    """`BTC-USD` (yfinance, and this codebase) → `BTC/USD` (Alpaca).

    Alpaca's crypto endpoint validates against ^[A-Z]+x?/[A-Z]+$ and rejects
    the hyphen form outright. Nothing translated, so every crypto quote failed
    — 13,293 times in one backend log, once every three seconds from the
    reflex poll, for as long as a crypto name sat on the watchlist. Crypto has
    therefore never worked here, and the only symptom was a warning nobody
    read.

    A module function rather than a method on the adapter, deliberately.
    `live_guard.py` requires every public member of a broker adapter to be
    classified as a read or a write, and it is right to: an unclassified
    member is refused. But this translates a string and touches nothing —
    making it a method would have meant editing the live-money gate to
    describe a function that cannot reach a broker, and that file does not get
    edited for conveniences.
    """
    t = (ticker or "").upper().strip()
    return t.replace("-", "/") if "-" in t else t


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

    # Read by src/execution/live_guard.py — see IBKRBroker for why the adapter
    # names its own variable instead of the gate guessing from the class name.
    PAPER_ENV_VAR = "ALPACA_PAPER"

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

    def submit_oco_exit(self, ticker: str, qty: float, entry_side: str,
                        stop: float, target: float) -> Optional[str]:
        """One-cancels-other exit: take-profit limit + stop-loss leg on the
        SAME reserved shares — the only way Alpaca allows both protections
        on one position. Returns the order id or None."""
        if not self._client:
            return None
        try:
            from alpaca.trading.requests import (LimitOrderRequest,
                                                 StopLossRequest,
                                                 TakeProfitRequest)
            from alpaca.trading.enums import OrderClass
            side = (AlpacaSide.SELL if entry_side in ("buy", "long")
                    else AlpacaSide.BUY)
            order = self._client.submit_order(order_data=LimitOrderRequest(
                symbol=ticker, qty=qty, side=side,
                time_in_force=TimeInForce.GTC,
                limit_price=round(target, 2),
                order_class=OrderClass.OCO,
                take_profit=TakeProfitRequest(limit_price=round(target, 2)),
                stop_loss=StopLossRequest(stop_price=round(stop, 2)),
            ))
            return str(order.id)
        except Exception as e:
            logger.error(f"Alpaca submit_oco_exit({ticker}) error: {e}")
            return None

    def get_open_orders(self) -> list[dict]:
        if not self._client:
            return []
        try:
            from alpaca.trading.requests import GetOrdersRequest
            from alpaca.trading.enums import QueryOrderStatus
            orders = self._client.get_orders(
                GetOrdersRequest(status=QueryOrderStatus.OPEN, limit=500,
                                 nested=True))

            def to_dict(o):
                return {
                    "id": str(o.id),
                    "ticker": str(o.symbol),
                    "side": str(o.side.value if hasattr(o.side, "value") else o.side),
                    "order_type": str(o.type.value if hasattr(o.type, "value") else o.type),
                    "qty": float(o.qty or 0),
                    "stop_price": float(o.stop_price) if o.stop_price else None,
                    "limit_price": float(o.limit_price) if o.limit_price else None,
                    "submitted_at": o.submitted_at.isoformat() if o.submitted_at else "",
                    "status": str(o.status.value if hasattr(o.status, "value") else o.status),
                }

            out = []
            for o in orders:
                out.append(to_dict(o))
                # OCO stop legs sit "held" under the parent — expose them so
                # bracket healing can see the stop actually exists
                for leg in (getattr(o, "legs", None) or []):
                    out.append(to_dict(leg))
            return out
        except Exception as e:
            logger.error(f"Alpaca get_open_orders error: {e}")
            return []

    def last_closed_fill(self, ticker: str, side: str | None = None) -> Optional[dict]:
        """Most recent FILLED order for a symbol from the closed-orders feed —
        the truth source for positions that closed while we weren't looking
        (audit H1: recorded P&L must come from real fills, not estimates)."""
        if not self._client:
            return None
        try:
            from alpaca.trading.requests import GetOrdersRequest
            from alpaca.trading.enums import QueryOrderStatus
            orders = self._client.get_orders(GetOrdersRequest(
                status=QueryOrderStatus.CLOSED, symbols=[ticker], limit=50))
            for o in orders:      # newest first
                status = str(o.status.value if hasattr(o.status, "value") else o.status)
                oside = str(o.side.value if hasattr(o.side, "value") else o.side)
                if status == "filled" and (side is None or oside == side):
                    return {"price": float(o.filled_avg_price or 0),
                            "qty": float(o.filled_qty or 0),
                            "side": oside,
                            "filled_at": str(o.filled_at or "")}
            return None
        except Exception as e:
            logger.warning(f"Alpaca last_closed_fill({ticker}): {e}")
            return None

    def get_quote(self, ticker: str) -> dict:
        if not self._stock_data:
            return {}
        try:
            is_crypto = "-USD" in ticker.upper() or "/" in ticker
            if is_crypto:
                symbol = crypto_symbol(ticker)
                req    = CryptoLatestQuoteRequest(symbol_or_symbols=[symbol])
                quotes = self._crypto_data.get_crypto_latest_quote(req)
            else:
                symbol = ticker
                req    = StockLatestQuoteRequest(symbol_or_symbols=[ticker])
                quotes = self._stock_data.get_stock_latest_quote(req)
            # Alpaca keys the reply by the symbol IT was given, not the one the
            # caller used, so a hyphenated lookup here would miss even after a
            # successful request.
            q = quotes.get(symbol) or quotes.get(ticker)
            if not q:
                return {}
            ask, bid = float(q.ask_price or 0), float(q.bid_price or 0)
            # A MISSING SIDE IS NOT A PRICE OF ZERO. Outside regular hours
            # Alpaca returns ask=0, and averaging that with the bid produced
            # mid = bid/2 — AAPL bid 290.54 came back as a mid of 145.27. A
            # limit priced off that sits fifty percent below the market, which
            # is either an order that never fills or one that fills terribly.
            if ask > 0 and bid > 0:
                mid = round((ask + bid) / 2, 4)
            elif ask > 0 or bid > 0:
                mid = round(ask or bid, 4)
            else:
                return {}
            return {"ask": ask, "bid": bid, "mid": mid,
                    "one_sided": not (ask > 0 and bid > 0)}
        except Exception as e:
            logger.warning(f"Alpaca get_quote({ticker}): {e}")
            return {}
