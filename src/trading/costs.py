"""
src/trading/costs.py
====================
What a trade costs before it has made anything. Fees, the spread, slippage.

A SMALL ACCOUNT FEELS FIXED COSTS HARDEST
-----------------------------------------
A percentage fee is the same on £3 as on £3,000. A minimum commission is not:
$1 on a $4 order is 25%. That is why `FeeSchedule` carries a per-order minimum
and a fixed component, and why the risk engine's viability check runs on the
actual notional, not on a percentage in the abstract. At £30 a month, "NO TRADE —
the costs exceed the edge" is going to be the most common correct answer, and
this module exists to make sure it is given.

THE NUMBERS ARE CONFIGURATION, NOT FACT
---------------------------------------
Broker fee schedules change. The schedules below are labelled with the date they
were written and are starting points; the owner should check them against the
broker's current published schedule before any capital is at stake. Every
function takes a schedule as an argument, so a correction is a data change.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from .market import Quote


@dataclass(frozen=True)
class FeeSchedule:
    venue: str
    taker_bps: float = 0.0          # of notional
    maker_bps: float = 0.0
    fixed_per_order: float = 0.0    # account currency
    min_per_order: float = 0.0
    per_share: float = 0.0
    sell_regulatory_bps: float = 0.0   # e.g. US SEC fee on sells
    as_of: str = ""
    note: str = ""


SCHEDULES = {
    # Commission-free US equities; regulatory fee on sells only (approximate).
    "alpaca_equity": FeeSchedule("alpaca_equity", sell_regulatory_bps=0.3,
                                 as_of="2026-09", note="verify against Alpaca's current schedule"),
    # Tiered crypto; the lowest-volume tier, which is where a small account sits.
    "alpaca_crypto": FeeSchedule("alpaca_crypto", taker_bps=25.0, maker_bps=15.0,
                                 as_of="2026-09", note="lowest volume tier; verify"),
    # IBKR fixed pricing: per share with a minimum per order.
    "ibkr_equity_fixed": FeeSchedule("ibkr_equity_fixed", per_share=0.005,
                                     min_per_order=1.0, as_of="2026-09",
                                     note="fixed tier; verify"),
    # For simulation only — the paper venue charges what we tell it to.
    "simulated": FeeSchedule("simulated", taker_bps=10.0, maker_bps=5.0,
                             as_of="n/a", note="deliberately non-zero so paper results are not flattered"),
}


def order_fee(schedule: FeeSchedule, *, side: str, qty: float, price: float,
              maker: bool = False) -> float:
    notional = abs(qty) * price
    pct = (schedule.maker_bps if maker else schedule.taker_bps) / 1e4 * notional
    fee = pct + schedule.fixed_per_order + schedule.per_share * abs(qty)
    if side.lower() == "sell":
        fee += schedule.sell_regulatory_bps / 1e4 * notional
    return max(fee, schedule.min_per_order) if notional > 0 else 0.0


def half_spread_cost(quote: Quote, qty: float) -> float:
    return abs(qty) * quote.spread / 2


def slippage_estimate(quote: Quote, qty: float, *,
                      adv: float | None = None,
                      daily_vol: float | None = None) -> float:
    """Expected cost beyond the touch, in account currency.

    Two parts, both deliberately conservative:
      * depth: the part of the order larger than the size displayed at the touch
        pays roughly another half-spread per multiple of that size;
      * impact: with a daily volume and volatility, the square-root law —
        vol * sqrt(qty / ADV) of notional.
    With neither size nor volume known, a floor of one full spread is charged:
    not knowing the book is not the same as the book being deep.
    """
    q = abs(qty)
    if q <= 0:
        return 0.0
    price = quote.mid
    touch = quote.ask_size if quote.ask_size > 0 else 0.0
    if touch > 0:
        excess = max(0.0, q - touch)
        depth = (excess / touch) * (quote.spread / 2) * excess
    else:
        depth = q * quote.spread
    impact = 0.0
    if adv and adv > 0 and daily_vol and daily_vol > 0:
        impact = daily_vol * math.sqrt(q / adv) * q * price
    return depth + impact


@dataclass(frozen=True)
class CostEstimate:
    entry_fee: float
    exit_fee: float
    spread: float
    slippage: float
    notional: float

    @property
    def total(self) -> float:
        return self.entry_fee + self.exit_fee + self.spread + self.slippage

    @property
    def total_bps(self) -> float:
        return (self.total / self.notional) * 1e4 if self.notional > 0 else math.inf

    def to_dict(self) -> dict:
        return {"entry_fee": round(self.entry_fee, 6), "exit_fee": round(self.exit_fee, 6),
                "spread": round(self.spread, 6), "slippage": round(self.slippage, 6),
                "total": round(self.total, 6), "total_bps": round(self.total_bps, 2),
                "notional": round(self.notional, 6)}


def round_trip(schedule: FeeSchedule, quote: Quote, qty: float, side: str, *,
               adv: float | None = None, daily_vol: float | None = None) -> CostEstimate:
    """Entry and exit, both crossing the spread, both paying fees and slippage."""
    price = quote.ask if side.lower() == "buy" else quote.bid
    exit_side = "sell" if side.lower() == "buy" else "buy"
    slip = slippage_estimate(quote, qty, adv=adv, daily_vol=daily_vol)
    return CostEstimate(
        entry_fee=order_fee(schedule, side=side, qty=qty, price=price),
        exit_fee=order_fee(schedule, side=exit_side, qty=qty, price=price),
        spread=2 * half_spread_cost(quote, qty),
        slippage=2 * slip,
        notional=abs(qty) * price,
    )
