"""
tests/trading_helpers.py
========================
Builders shared by the src/trading tests. Deliberately explicit: every helper
takes the numbers that matter as arguments, so a test that depends on an edge
being 200 bps or a quote being 40 seconds old says so on the line that matters.

`OPEN_MARKET` is a real NYSE session minute (Tuesday 22 September 2026, 10:00
New York). Tests that must pass regardless of when the suite runs use it — a
suite whose result depends on the wall clock is a suite that fails on a
Saturday.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.trading.account import AccountState, Holding
from src.trading.market import Quote
from src.trading.signals import Signal

OPEN_MARKET = datetime(2026, 9, 22, 14, 0, tzinfo=timezone.utc)   # 10:00 New York
CLOSED_MARKET = datetime(2026, 9, 20, 14, 0, tzinfo=timezone.utc)  # a Sunday


def make_quote(symbol: str = "AAPL", *, bid: float = 99.95, ask: float = 100.05,
               now: datetime | None = None, age_s: float = 0.0,
               bid_size: float = 10_000, ask_size: float = 10_000,
               timestamp="auto", venue: str = "test",
               asset_class: str = "equity") -> Quote:
    """`timestamp=None` means a quote that carries no timestamp at all — which
    the freshness check treats as stale — so the default is the string "auto",
    not None, or that case could not be expressed."""
    now = now or OPEN_MARKET
    ts = now - timedelta(seconds=age_s) if timestamp == "auto" else timestamp
    return Quote(symbol=symbol, bid=bid, ask=ask, timestamp=ts, bid_size=bid_size,
                 ask_size=ask_size, venue=venue, asset_class=asset_class)


def make_account(*, equity: float = 1000.0, cash: float | None = None,
                 available_cash: float | None = None, holdings=(), pending=(),
                 now: datetime | None = None, hwm: float | None = None,
                 day_start: float | None = None, known: bool = True,
                 why_unknown: str = "") -> AccountState:
    now = now or OPEN_MARKET
    cash = equity if cash is None else cash
    return AccountState(
        known=known, currency="USD", equity=equity, cash=cash,
        available_cash=cash if available_cash is None else available_cash,
        holdings=tuple(holdings), pending_orders=tuple(pending),
        performance_equity=equity,
        performance_hwm=equity if hwm is None else hwm,
        performance_day_start=equity if day_start is None else day_start,
        as_of=now, source="test", why_unknown=why_unknown)


def make_holding(symbol: str = "AAPL", qty: float = 1.0, avg_price: float = 100.0,
                 market_price: float | None = None, sector: str = "",
                 strategy_id: str = "") -> Holding:
    return Holding(symbol=symbol, qty=qty, avg_price=avg_price,
                   market_price=avg_price if market_price is None else market_price,
                   sector=sector, strategy_id=strategy_id)


def make_signal(*, strategy_id: str = "test-strategy", symbol: str = "AAPL",
                side: str = "buy", expected_edge_bps: float = 300.0,
                stop_price: float | None = 95.0, now: datetime | None = None,
                data_age_s: float = 0.0, sector: str = "", fractionable: bool = True,
                asset_class: str = "equity") -> Signal:
    now = now or OPEN_MARKET
    return Signal(strategy_id=strategy_id, symbol=symbol, side=side,
                  expected_edge_bps=expected_edge_bps, stop_price=stop_price,
                  data_timestamp=now - timedelta(seconds=data_age_s),
                  asset_class=asset_class, sector=sector, fractionable=fractionable,
                  rationale="test")


ALLOWED = (True, "test strategy is allowed")
REFUSED = (False, "suspended: drift")


# ── a stand-in for the existing Alpaca adapter, for the mode-gate tests ──────

class FakeLegacyBroker:
    """What `src/execution` adapters look like from outside: the paper env var
    it declares, a paper flag, an endpoint that answers for itself."""
    name = "Alpaca"
    PAPER_ENV_VAR = "ALPACA_PAPER"

    def __init__(self, paper: bool = True):
        self._paper = paper
        self.submitted = []
        self.cancelled = []

    @property
    def paper(self) -> bool:
        return self._paper

    def endpoint_is_paper(self):
        return self._paper

    def get_account(self):
        from src.execution.broker_base import AccountInfo
        return AccountInfo(broker="alpaca", cash=1000.0, portfolio_value=1000.0,
                           buying_power=1000.0, currency="USD")

    def get_positions(self):
        return []

    def get_open_orders(self):
        return []

    def get_quote(self, ticker):
        return {"bid": 99.95, "ask": 100.05, "mid": 100.0,
                "timestamp": OPEN_MARKET.isoformat()}

    def submit_order(self, req):
        from src.execution.broker_base import OrderResult, OrderStatus
        self.submitted.append(req)
        return OrderResult(broker_order_id="broker-1", status=OrderStatus.FILLED,
                           filled_qty=req.qty, avg_fill_price=100.05)

    def cancel_order(self, broker_order_id):
        self.cancelled.append(broker_order_id)
        return True
