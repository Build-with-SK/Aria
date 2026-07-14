"""
src/execution/broker_base.py
============================
Abstract broker interface. All brokers (Alpaca, IBKR) implement this.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class OrderSide(str, Enum):
    BUY  = "buy"
    SELL = "sell"


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT  = "limit"
    STOP   = "stop"
    STOP_LIMIT = "stop_limit"


class OrderStatus(str, Enum):
    PENDING   = "pending"    # in approval queue, not sent
    SUBMITTED = "submitted"  # sent to broker
    FILLED    = "filled"
    PARTIAL   = "partial"
    CANCELLED = "cancelled"
    REJECTED  = "rejected"
    EXPIRED   = "expired"


class AssetClass(str, Enum):
    EQUITY  = "equity"
    CRYPTO  = "crypto"
    FUTURES = "futures"
    FOREX   = "forex"
    OPTION  = "option"


@dataclass
class OrderRequest:
    """What ARIA wants to execute — sent to broker after user approval."""
    ticker:      str
    side:        OrderSide
    qty:         float          # shares / contracts / units
    order_type:  OrderType      = OrderType.MARKET
    limit_price: Optional[float] = None
    stop_price:  Optional[float] = None
    time_in_force: str          = "day"   # day | gtc | ioc | fok
    asset_class: AssetClass     = AssetClass.EQUITY
    # Risk params from signal engine
    stop_loss:   Optional[float] = None
    take_profit: Optional[float] = None
    # Context
    signal_score:   float = 0.0
    situation:      str   = ""
    thesis_summary: str   = ""


@dataclass
class OrderResult:
    """Broker's response after order submission."""
    broker_order_id: str
    status:          OrderStatus
    filled_qty:      float  = 0.0
    avg_fill_price:  float  = 0.0
    submitted_at:    datetime = field(default_factory=datetime.utcnow)
    filled_at:       Optional[datetime] = None
    error_message:   str    = ""
    raw:             dict   = field(default_factory=dict)


@dataclass
class Position:
    ticker:        str
    qty:           float
    avg_cost:      float
    market_value:  float
    unrealized_pl: float
    side:          str  # "long" | "short"
    asset_class:   AssetClass = AssetClass.EQUITY


@dataclass
class AccountInfo:
    broker:         str
    cash:           float
    portfolio_value: float
    buying_power:   float
    day_trade_count: int = 0
    currency:       str = "USD"


class BrokerBase(ABC):
    """Interface every broker adapter must implement."""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def paper(self) -> bool:
        """True if this is a paper/sim account."""
        ...

    @abstractmethod
    def is_connected(self) -> bool: ...

    @abstractmethod
    def get_account(self) -> AccountInfo: ...

    @abstractmethod
    def get_positions(self) -> list[Position]: ...

    @abstractmethod
    def get_position(self, ticker: str) -> Optional[Position]: ...

    @abstractmethod
    def submit_order(self, req: OrderRequest) -> OrderResult: ...

    @abstractmethod
    def cancel_order(self, broker_order_id: str) -> bool: ...

    @abstractmethod
    def get_order_status(self, broker_order_id: str) -> OrderResult: ...

    @abstractmethod
    def get_quote(self, ticker: str) -> dict: ...

    def supports_asset(self, asset_class: AssetClass) -> bool:
        """Override to restrict which asset classes a broker handles."""
        return True
