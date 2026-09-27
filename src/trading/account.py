"""
src/trading/account.py
======================
What ARIA actually has to trade with. Never assumed, always read.

    account_equity    available_cash    risk_budget     margin_available
    open_positions    pending_orders    fees            realised / unrealised P&L
    drawdown

CONTRIBUTIONS ARE NOT PROFITS
-----------------------------
The plan is roughly £30 a month paid in. Naively, a £30 deposit after a £10 loss
looks like a £20 gain, and a drawdown measured on raw equity would be hidden by
the next deposit. So drawdown and daily P&L are measured on PERFORMANCE equity:
equity minus the net contributions made since tracking began. Deposits move the
capital base; only trading moves the performance curve.

`capital_ledger.jsonl` is where contributions are recorded — by the owner, via
the API or `record_contribution()`. Nothing infers a deposit from a balance jump:
an unexplained jump is exactly the kind of thing that should look wrong.

UNKNOWN STATE STOPS TRADING
---------------------------
`AccountState.known` is False when it could not be read. The risk engine refuses
every signal against an unknown account.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent.parent.parent
CAPITAL_LEDGER = ROOT / "data" / "trading" / "capital_ledger.jsonl"
EQUITY_HISTORY = ROOT / "data" / "trading" / "equity_history.jsonl"

#: The intended monthly contribution. A plan, recorded for the dashboard and for
#: "is this strategy viable at the size we will actually have" — never assumed
#: to have arrived until it is recorded.
PLANNED_MONTHLY_CONTRIBUTION = {"amount": 30.0, "currency": "GBP"}


@dataclass(frozen=True)
class Holding:
    symbol: str
    qty: float
    avg_price: float
    market_price: float
    asset_class: str = "equity"
    sector: str = ""
    strategy_id: str = ""

    @property
    def market_value(self) -> float:
        return self.qty * self.market_price

    @property
    def unrealised_pnl(self) -> float:
        return (self.market_price - self.avg_price) * self.qty


@dataclass(frozen=True)
class AccountState:
    known: bool
    currency: str = "USD"
    equity: float = 0.0
    cash: float = 0.0
    available_cash: float = 0.0
    margin_available: float = 0.0
    holdings: tuple = ()
    pending_orders: tuple = ()               # dicts: symbol, side, qty, notional
    fees_paid: float = 0.0
    realised_pnl: float = 0.0
    performance_equity: float = 0.0          # equity minus net contributions
    performance_hwm: float = 0.0
    performance_day_start: float = 0.0
    as_of: Optional[datetime] = None
    source: str = ""
    why_unknown: str = ""

    # ── derived ──────────────────────────────────────────────────────────
    @property
    def unrealised_pnl(self) -> float:
        return sum(h.unrealised_pnl for h in self.holdings)

    @property
    def gross_exposure(self) -> float:
        return sum(abs(h.market_value) for h in self.holdings)

    @property
    def leverage(self) -> float:
        return self.gross_exposure / self.equity if self.equity > 0 else float("inf")

    @property
    def drawdown_pct(self) -> float:
        if self.performance_hwm <= 0:
            return 0.0
        return max(0.0, (self.performance_hwm - self.performance_equity)
                   / self.performance_hwm * 100)

    @property
    def daily_pnl_pct(self) -> float:
        if self.performance_day_start <= 0:
            return 0.0
        return (self.performance_equity - self.performance_day_start) \
            / self.performance_day_start * 100

    def exposure(self, *, symbol: str = "", sector: str = "", strategy_id: str = "") -> float:
        total = 0.0
        for h in self.holdings:
            if symbol and h.symbol != symbol:
                continue
            if sector and h.sector != sector:
                continue
            if strategy_id and h.strategy_id != strategy_id:
                continue
            total += abs(h.market_value)
        for o in self.pending_orders:
            if symbol and o.get("symbol") != symbol:
                continue
            if sector and o.get("sector") != sector:
                continue
            if strategy_id and o.get("strategy_id") != strategy_id:
                continue
            total += abs(float(o.get("notional", 0.0)))
        return total

    def held_qty(self, symbol: str) -> float:
        return sum(h.qty for h in self.holdings if h.symbol == symbol)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["as_of"] = self.as_of.isoformat() if self.as_of else None
        d.update(drawdown_pct=round(self.drawdown_pct, 4),
                 daily_pnl_pct=round(self.daily_pnl_pct, 4),
                 unrealised_pnl=round(self.unrealised_pnl, 6),
                 gross_exposure=round(self.gross_exposure, 6),
                 leverage=round(self.leverage, 4) if self.equity > 0 else None)
        return d


def unknown(why: str) -> AccountState:
    return AccountState(known=False, why_unknown=why,
                        as_of=datetime.now(timezone.utc))


# ── the capital ledger ───────────────────────────────────────────────────────

def record_contribution(amount: float, currency: str = "GBP", *,
                        amount_in_account_ccy: Optional[float] = None,
                        note: str = "", actor: str = "owner") -> dict:
    """Record money paid in (positive) or withdrawn (negative)."""
    if amount == 0:
        raise ValueError("a contribution of zero records nothing")
    row = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "amount": float(amount), "currency": currency,
           "amount_in_account_ccy": amount_in_account_ccy, "note": note, "by": actor}
    CAPITAL_LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with CAPITAL_LEDGER.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")
    return row


def contributions() -> list[dict]:
    if not CAPITAL_LEDGER.exists():
        return []
    rows = []
    for line in CAPITAL_LEDGER.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def net_contributions_account_ccy() -> float:
    """Sum of contributions converted to the account currency, using the
    conversion recorded WITH each contribution. A contribution recorded without
    one is excluded and logged — a guessed exchange rate would make the
    performance curve fiction."""
    total = 0.0
    for r in contributions():
        v = r.get("amount_in_account_ccy")
        if v is None:
            logger.warning("contribution on %s has no account-currency amount — "
                           "excluded from performance accounting", r.get("at"))
            continue
        total += float(v)
    return total


def _history() -> list[dict]:
    if not EQUITY_HISTORY.exists():
        return []
    out = []
    for line in EQUITY_HISTORY.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def record_equity(performance_equity: float, now: Optional[datetime] = None) -> None:
    now = now or datetime.now(timezone.utc)
    EQUITY_HISTORY.parent.mkdir(parents=True, exist_ok=True)
    with EQUITY_HISTORY.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"at": now.isoformat(timespec="seconds"),
                            "performance_equity": performance_equity}) + "\n")


def performance_marks(current: float, now: Optional[datetime] = None) -> tuple[float, float]:
    """(high-water mark, start-of-day value) of performance equity, including
    `current`. Day boundaries are UTC."""
    now = now or datetime.now(timezone.utc)
    hist = _history()
    values = [float(r["performance_equity"]) for r in hist] + [current]
    today = now.date().isoformat()
    todays = [float(r["performance_equity"]) for r in hist
              if str(r.get("at", "")).startswith(today)]
    return max(values), (todays[0] if todays else current)


def from_snapshot(*, equity: float, cash: float, holdings: list[Holding],
                  pending_orders: list[dict] | None = None, currency: str = "USD",
                  fees_paid: float = 0.0, realised_pnl: float = 0.0,
                  source: str = "", persist: bool = True,
                  now: Optional[datetime] = None) -> AccountState:
    """Build a known AccountState from what a broker reported."""
    now = now or datetime.now(timezone.utc)
    pending = list(pending_orders or [])
    reserved = sum(float(o.get("notional", 0.0)) for o in pending
                   if o.get("side") == "buy")
    perf = equity - net_contributions_account_ccy()
    hwm, day_start = performance_marks(perf, now)
    if persist:
        record_equity(perf, now)
    return AccountState(
        known=True, currency=currency, equity=equity, cash=cash,
        available_cash=max(0.0, cash - reserved),
        margin_available=0.0,                   # no margin in this phase
        holdings=tuple(holdings), pending_orders=tuple(pending),
        fees_paid=fees_paid, realised_pnl=realised_pnl,
        performance_equity=perf, performance_hwm=hwm,
        performance_day_start=day_start, as_of=now, source=source)
