"""
src/trading/validator.py
========================
The last check before an order leaves: is THIS order, as built, sendable now?

The risk engine decided the trade is justified and sized it. The validator does
not second-guess that; it checks the concrete order against the world:

    symbol valid · side valid · order type allowed · quantity valid
    price sane · market open · data fresh · capital sufficient
    order within limits · position within limits · not a duplicate

It returns a list of violations — empty means clear — and never raises, so a
caller cannot mistake a crash in here for a pass.
"""
from __future__ import annotations

import math
import re
from datetime import datetime
from typing import Optional

from .account import AccountState
from .broker import OrderSpec
from .limits import HardLimits
from .market import Quote, check as freshness_check, market_open

SYMBOL = re.compile(r"^[A-Z0-9][A-Z0-9.\-/]{0,15}$")
ALLOWED_TYPES = {"market", "limit"}
ALLOWED_TIF = {"day", "gtc", "ioc", "fok"}


def validate(order: OrderSpec, *, quote: Optional[Quote], account: AccountState,
             limits: HardLimits, now: datetime, seen_client_ids: set[str],
             tradable_symbols: Optional[set[str]] = None,
             allow_fractional: bool = True,
             fee_rate: float = 0.0) -> list[str]:
    v: list[str] = []
    try:
        if not SYMBOL.match(order.symbol or ""):
            v.append(f"symbol {order.symbol!r} is not a valid symbol")
        if tradable_symbols is not None and order.symbol not in tradable_symbols:
            v.append(f"{order.symbol} is not in the tradable universe")
        if order.side not in ("buy", "sell"):
            v.append(f"side {order.side!r} is not buy or sell")
        if order.order_type not in ALLOWED_TYPES:
            v.append(f"order type {order.order_type!r} is not allowed "
                     f"(allowed: {sorted(ALLOWED_TYPES)})")
        if order.time_in_force not in ALLOWED_TIF:
            v.append(f"time in force {order.time_in_force!r} is not allowed")
        if not (isinstance(order.qty, (int, float)) and math.isfinite(order.qty)
                and order.qty > 0):
            v.append(f"quantity {order.qty!r} is not a positive number")
        elif not allow_fractional and order.qty != int(order.qty):
            v.append(f"fractional quantity {order.qty} not allowed for {order.symbol}")
        if order.client_order_id in seen_client_ids:
            v.append(f"duplicate client order id {order.client_order_id}")

        ok, why = market_open(order.asset_class, now)
        if not ok:
            v.append(f"market closed: {why}")
        fr = freshness_check(quote, max_age_s=limits.max_quote_age_seconds, now=now)
        if not fr.ok:
            v.append(f"market data not fresh: {fr.reason}")
            return v                      # nothing price-based is meaningful now

        ref = quote.ask if order.side == "buy" else quote.bid
        if order.order_type == "limit":
            lp = order.limit_price
            if lp is None or not math.isfinite(lp) or lp <= 0:
                v.append("limit order without a valid limit price")
            else:
                dev = abs(lp - quote.mid) / quote.mid * 100
                if dev > limits.max_price_deviation_pct:
                    v.append(f"limit {lp} is {dev:.2f}% from mid {quote.mid:.4f} "
                             f"(limit {limits.max_price_deviation_pct}%)")
                ref = lp
        notional = order.qty * ref
        if notional > limits.max_order_value + 1e-9:
            v.append(f"order value {notional:.2f} exceeds max_order_value "
                     f"{limits.max_order_value}")
        if not account.known:
            v.append(f"account state unknown: {account.why_unknown}")
            return v
        if order.side == "buy":
            need = notional * (1 + fee_rate)
            if need > account.available_cash + 1e-9:
                v.append(f"insufficient cash: needs {need:.2f}, available "
                         f"{account.available_cash:.2f}")
            after = account.exposure(symbol=order.symbol) + notional
            cap = account.equity * limits.max_position_pct / 100
            if after > cap + 1e-9:
                v.append(f"position in {order.symbol} would be {after:.2f}, above "
                         f"{limits.max_position_pct}% of equity ({cap:.2f})")
        else:
            held = account.held_qty(order.symbol)
            if order.qty > held + 1e-9:
                v.append(f"sell {order.qty} exceeds held {held} — no short selling")
    except Exception as exc:                  # pragma: no cover - never a silent pass
        v.append(f"validator error: {type(exc).__name__}: {exc}")
    return v
