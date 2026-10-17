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
import re
import threading
import time
from datetime import datetime
from typing import Optional

from . import kill_switch
from .broker_base import (
    AssetClass, BrokerBase, AccountInfo, OrderRefused, OrderRequest, OrderResult,
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


# ── which symbols Alpaca can quote at all ───────────────────────────────────
#
# Alpaca's crypto endpoint validates pairs against ^[A-Z]+x?/[A-Z]+$ (so no
# digits: 1INCH and API3 are refused) and its stock endpoint knows US listings
# only. Asking anyway produced ~13,000 identical warnings in one log, one every
# three seconds per symbol, which buried every warning that mattered. A symbol
# the shape check passes but Alpaca still refuses as invalid is remembered for
# UNQUOTABLE_TTL_S, so it is asked about once per window, not forever.

_ALPACA_CRYPTO_PAIR = re.compile(r"^[A-Z]+x?/[A-Z]+$")
_ALPACA_STOCK = re.compile(r"^[A-Z]{1,5}([.][A-Z]{1,2})?$")
UNQUOTABLE_TTL_S = 6 * 3600
_unquotable: dict[str, float] = {}
_unquotable_lock = threading.Lock()


def _shape_ok(ticker: str) -> bool:
    t = (ticker or "").upper().strip()
    if "-USD" in t or "/" in t:
        return bool(_ALPACA_CRYPTO_PAIR.match(crypto_symbol(t)))
    return bool(_ALPACA_STOCK.match(t))


def quotable(ticker: str, now: Optional[float] = None) -> bool:
    """Can Alpaca quote this symbol? Pure: a shape check plus the cache of
    symbols Alpaca has already refused. Touches no network and no account."""
    t = (ticker or "").upper().strip()
    if not _shape_ok(t):
        return False
    now = time.time() if now is None else now
    with _unquotable_lock:
        until = _unquotable.get(t)
        if until is not None and until > now:
            return False
        if until is not None:
            _unquotable.pop(t, None)
    return True


def _mark_unquotable(ticker: str, reason: str) -> None:
    t = (ticker or "").upper().strip()
    with _unquotable_lock:
        first = t not in _unquotable
        _unquotable[t] = time.time() + UNQUOTABLE_TTL_S
    if first:
        logger.warning("Alpaca cannot quote %s (%s) — not asking again for %dh",
                       t, reason[:160], UNQUOTABLE_TTL_S // 3600)


def _is_invalid_symbol_error(e: Exception) -> bool:
    msg = str(e).lower()
    return "invalid symbol" in msg or "does not match" in msg


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


def _status_token(alpaca_status) -> str:
    """The bare status word, whatever shape alpaca-py hands over.

    `str(order.status)` on an alpaca-py enum returns "OrderStatus.FILLED",
    not "filled" — so every lookup in the table below missed and fell through
    to the SUBMITTED default. A live paper fill was found reporting
    SUBMITTED with filled_qty=1 during runtime verification, which is the
    visible half of the bug; the dangerous half is that `_await_fill()` waits
    for FILLED before placing the protective stop and target, so a filled
    position sat naked while the poll timed out, and a REJECTED order read as
    still working.

    Accepts the enum, its .value, or either spelling of the string.
    """
    if alpaca_status is None:
        return ""
    token = getattr(alpaca_status, "value", None) or str(alpaca_status)
    if "." in token:                      # "OrderStatus.FILLED" → "FILLED"
        token = token.rsplit(".", 1)[-1]
    return token.strip().lower()


def _classify_submit_failure(exc: Exception, req: OrderRequest,
                             retried: bool = False) -> "OrderStatus":
    """REJECTED only when the broker definitely refused; otherwise UNKNOWN.

    A 4xx answer to the ONLY attempt is Alpaca saying no: the order does not
    exist. Anything else — a timeout, a dropped connection, a 5xx — leaves the
    order's existence unknown. A missing lookup key never justifies saying
    REJECTED: it leaves an unresolved order for manual broker reconciliation.

    Two 4xx answers are not a refusal of the order:
      * a complaint about the client order id itself — the broker already
        HOLDS an order with that id;
      * any 4xx from a client that may have retried (`retried`). alpaca-py
        re-POSTs by itself on 429/504 (three times, alpaca/common/rest.py); the
        final answer refuses only the LAST attempt and says nothing about an
        earlier one accepted behind a gateway timeout (Codex review of e4f01ad:
        504, 504, 504, 429 was recorded REJECTED). Orders are therefore sent
        through a client with retries off (`_submit_client`), where a 4xx is
        the answer to the one and only POST.
    """
    code = getattr(exc, "status_code", None)
    if isinstance(code, int) and 400 <= code < 500:
        if "client_order_id" in str(exc).lower() or retried:
            return OrderStatus.UNKNOWN
        return OrderStatus.REJECTED
    if _never_sent(exc):
        return OrderStatus.REJECTED
    return OrderStatus.UNKNOWN


def _never_sent(exc: Exception) -> bool:
    """True only when the request provably never left: the TCP connection was
    never established. A read timeout, a reset or a refusal after connecting is
    not that — the request may have been received."""
    try:
        from requests.exceptions import ConnectTimeout
    except ImportError:                                     # pragma: no cover
        return False
    return isinstance(exc, ConnectTimeout)


def _no_retry(client):
    """The same client, answering each request once. alpaca-py takes no
    constructor argument for zero retries (RESTClient accepts only a positive
    count), so the attempt counter is set on the instance; `_retries_possible`
    reads it back, so an SDK that renames it fails safe (treated as retrying)."""
    if client is not None and hasattr(client, "_retry"):
        client._retry = 0
    return client


def _retries_possible(client) -> bool:
    try:
        return int(getattr(client, "_retry")) > 0
    except Exception:
        return True


def _parse_status(alpaca_status, filled_qty: float = 0.0) -> OrderStatus:
    """Map Alpaca's status to ours, letting the fill counter break ties.

    An unknown label is treated as "still working" — never as filled and
    never as dead, because both of those conclusions make the caller act on
    a position that may not be what it thinks.
    """
    token = _status_token(alpaca_status)
    mapping = {
        "new":              OrderStatus.SUBMITTED,
        # Alpaca's full pre-fill vocabulary. These all mean "working" and the
        # default would have said so anyway — they are listed so the warning
        # above stays meaningful, and only fires on a word we genuinely do
        # not know. `pending_new` showed up on a real paper sell.
        "pending_new":      OrderStatus.SUBMITTED,
        "accepted_for_bidding": OrderStatus.SUBMITTED,
        "calculated":       OrderStatus.SUBMITTED,
        "held":             OrderStatus.SUBMITTED,
        "stopped":          OrderStatus.SUBMITTED,
        "suspended":        OrderStatus.SUBMITTED,
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
    mapped = mapping.get(token)
    if mapped is None:
        if token:
            logger.warning("Alpaca status %r is not in the known set — "
                           "treating as still working", token)
        mapped = OrderStatus.SUBMITTED

    # Cancelled or expired after a part-fill still leaves an open position;
    # reporting CANCELLED there tells the caller it owns nothing.
    if filled_qty > 0 and mapped in (OrderStatus.CANCELLED, OrderStatus.EXPIRED,
                                     OrderStatus.REJECTED):
        return OrderStatus.PARTIAL
    return mapped


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
        # Orders go through their own client with the SDK's automatic retries
        # off, so a 4xx answers the one POST that was made (see
        # _classify_submit_failure). Reads keep the retrying client.
        self._submit_client: Optional["TradingClient"] = None
        self._stock_data: Optional["StockHistoricalDataClient"] = None
        self._crypto_data: Optional["CryptoHistoricalDataClient"] = None

        if ALPACA_AVAILABLE and self._api_key:
            try:
                self._client = TradingClient(
                    api_key=self._api_key,
                    secret_key=self._secret_key,
                    paper=self._is_paper,
                )
                self._submit_client = _no_retry(TradingClient(
                    api_key=self._api_key,
                    secret_key=self._secret_key,
                    paper=self._is_paper,
                ))
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
            raise ConnectionError("Alpaca positions unavailable: client not connected")
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
            raise ConnectionError("Alpaca positions unreadable") from e

    def get_position(self, ticker: str) -> Optional[Position]:
        positions = self.get_positions()
        for p in positions:
            if p.ticker == ticker:
                return p
        return None

    def submit_order(self, req: OrderRequest) -> OrderResult:
        # Below every other gate: nothing reaches Alpaca while the switch is on.
        if kill_switch.is_engaged():
            st = kill_switch.status()
            logger.critical("Alpaca order refused — kill switch engaged (%s)", st.get("source"))
            return OrderResult(
                broker_order_id="",
                status=OrderStatus.REJECTED,
                error_message=f"KILL SWITCH ENGAGED ({st.get('source')}): {st.get('reason')}",
            )
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

            if req.client_order_id:
                # alpaca-py request models are pydantic; the field exists on all
                # four order types and Alpaca rejects a reused value, which is
                # the broker-side half of idempotency.
                order_req.client_order_id = req.client_order_id
        except Exception as e:
            # Failed while BUILDING the request: nothing left this process.
            logger.error(f"Alpaca order request not built, nothing sent: {e}")
            return OrderResult(broker_order_id="", status=OrderStatus.REJECTED,
                               error_message=f"order request not built, nothing sent: {e}")
        client = getattr(self, "_submit_client", None) or self._client
        try:
            order = client.submit_order(order_data=order_req)
            filled = float(order.filled_qty or 0)
            return OrderResult(
                broker_order_id=str(order.id),
                status=_parse_status(order.status, filled),
                filled_qty=filled,
                avg_fill_price=float(order.filled_avg_price or 0),
                submitted_at=datetime.utcnow(),
                raw={"id": str(order.id), "status": str(order.status)},
            )
        except Exception as e:
            logger.error(f"Alpaca submit_order error: {e}")
            return OrderResult(
                broker_order_id="",
                status=_classify_submit_failure(e, req, retried=_retries_possible(client)),
                error_message=str(e),
            )

    def get_order_by_client_id(self, client_order_id: str) -> Optional[OrderResult]:
        """The order sent with this client id, or None if Alpaca has none.

        What settles an UNKNOWN: after a timeout, ask by the id we chose. Raises
        on a transport failure — "could not ask" must never read as "not found",
        or an order that did arrive would be sent again.
        """
        if not self._client:
            raise ConnectionError("Alpaca client not connected")
        try:
            order = self._client.get_order_by_client_id(client_order_id)
        except Exception as e:
            code = getattr(e, "status_code", None)
            if code == 404:
                return None
            raise
        filled = float(order.filled_qty or 0)
        return OrderResult(
            broker_order_id=str(order.id),
            status=_parse_status(order.status, filled),
            filled_qty=filled,
            avg_fill_price=float(order.filled_avg_price or 0),
            filled_at=order.filled_at,
            raw={"id": str(order.id), "status": str(order.status),
                 "client_order_id": client_order_id},
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

    def get_order_by_broker_id(self, broker_order_id: str) -> Optional[OrderResult]:
        """Historical recovery by account-scoped broker evidence; never by symbol.

        Unlike get_order_status, this exposes identity fields for reconciliation
        and raises failed reads. A 404 is absence of evidence, not rejection.
        """
        if not self._client:
            raise ConnectionError("Alpaca client not connected")
        try:
            order = self._client.get_order_by_id(broker_order_id)
        except Exception as exc:
            if getattr(exc, "status_code", None) == 404:
                return None
            raise
        filled = float(order.filled_qty or 0)
        return OrderResult(
            broker_order_id=str(order.id), status=_parse_status(order.status, filled),
            filled_qty=filled, avg_fill_price=float(order.filled_avg_price or 0),
            filled_at=order.filled_at,
            raw={"status": str(order.status), "symbol": str(order.symbol),
                 "side": str(getattr(order.side, "value", order.side)), "qty": float(order.qty)})
    def get_order_status(self, broker_order_id: str) -> OrderResult:
        # A status read that fails says nothing about the order: UNKNOWN, never
        # REJECTED — "could not ask" read as "the broker refused it" made a
        # working order look dead (Codex review of e4f01ad).
        if not self._client:
            return OrderResult(broker_order_id=broker_order_id, status=OrderStatus.UNKNOWN,
                               error_message="Alpaca client not connected")
        try:
            order = self._client.get_order_by_id(broker_order_id)
            filled = float(order.filled_qty or 0)
            return OrderResult(
                broker_order_id=broker_order_id,
                status=_parse_status(order.status, filled),
                filled_qty=filled,
                avg_fill_price=float(order.filled_avg_price or 0),
                filled_at=order.filled_at,
                raw={"status": str(order.status)},
            )
        except Exception as e:
            return OrderResult(
                broker_order_id=broker_order_id,
                status=OrderStatus.UNKNOWN,
                error_message=str(e),
            )

    def submit_oco_exit(self, ticker: str, qty: float, entry_side: str,
                        stop: float, target: float, *, client_order_id: Optional[str] = None) -> Optional[str]:
        """One-cancels-other exit: take-profit limit + stop-loss leg on the
        SAME reserved shares — the only way Alpaca allows both protections
        on one position.

        Returns the order id, or None when the outcome is UNKNOWN (a timeout:
        it may exist). Raises OrderRefused when nothing can exist under the
        request — the kill switch, a request that could not be built, or a
        definite refusal of the one POST (e.g. 40310000 insufficient qty).
        Returning None for those too made a refused OCO an UNKNOWN that
        blocked the symbol until the owner released it by hand."""
        if kill_switch.is_engaged():
            logger.critical("Alpaca OCO refused: kill switch engaged")
            raise OrderRefused("kill switch engaged")
        if not self._client:
            raise OrderRefused("Alpaca client not connected")
        try:
            from alpaca.trading.requests import (LimitOrderRequest,
                                                 StopLossRequest,
                                                 TakeProfitRequest)
            from alpaca.trading.enums import OrderClass
            side = (AlpacaSide.SELL if entry_side in ("buy", "long")
                    else AlpacaSide.BUY)
            order_req = LimitOrderRequest(
                symbol=ticker, qty=qty, side=side,
                client_order_id=client_order_id,
                time_in_force=TimeInForce.GTC,
                limit_price=round(target, 2),
                order_class=OrderClass.OCO,
                take_profit=TakeProfitRequest(limit_price=round(target, 2)),
                stop_loss=StopLossRequest(stop_price=round(stop, 2)),
            )
        except Exception as e:
            logger.error(f"Alpaca OCO request for {ticker} not built, nothing sent: {e}")
            raise OrderRefused(f"OCO request not built, nothing sent: {e}") from e
        client = getattr(self, "_submit_client", None) or self._client
        try:
            order = client.submit_order(order_data=order_req)
            return str(order.id)
        except Exception as e:
            logger.error(f"Alpaca submit_oco_exit({ticker}) error: {e}")
            probe = OrderRequest(ticker=ticker, side=OrderSide.SELL, qty=qty,
                                 client_order_id=client_order_id or "")
            if (client_order_id and _classify_submit_failure(
                    e, probe, retried=_retries_possible(client)) == OrderStatus.REJECTED):
                raise OrderRefused(str(e)) from e
            return None

    def get_open_orders(self) -> list[dict]:
        if not self._client:
            raise ConnectionError("Alpaca open orders unavailable: client not connected")
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
                # bracket healing can see the stop actually exists. The
                # parent id links the legs: only one of them can fill, which
                # the reduce-only check (src/execution/reduce_only) relies on.
                for leg in (getattr(o, "legs", None) or []):
                    out.append({**to_dict(leg), "parent_id": str(o.id)})
            return out
        except Exception as e:
            logger.error(f"Alpaca get_open_orders error: {e}")
            raise ConnectionError("Alpaca open orders unreadable") from e

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
        if not quotable(ticker):
            return {}
        try:
            is_crypto = "-USD" in ticker.upper() or "/" in ticker
            if is_crypto:
                if self._crypto_data is None:
                    return {}
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
            ts = getattr(q, "timestamp", None)
            return {"ask": ask, "bid": bid, "mid": mid,
                    "one_sided": not (ask > 0 and bid > 0),
                    # When the VENUE produced the quote. Without it a quote
                    # cannot be checked for staleness, and src/trading treats
                    # an undated quote as stale.
                    "timestamp": ts.isoformat() if ts is not None else None,
                    "bid_size": float(getattr(q, "bid_size", 0) or 0),
                    "ask_size": float(getattr(q, "ask_size", 0) or 0)}
        except Exception as e:
            if _is_invalid_symbol_error(e):
                _mark_unquotable(ticker, str(e))
            else:
                logger.warning(f"Alpaca get_quote({ticker}): {e}")
            return {}
