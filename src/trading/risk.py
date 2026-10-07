"""
src/trading/risk.py
===================
THE RISK ENGINE. It can reject a signal the strategy was sure about.

    signal → state known? → strategy allowed? → data fresh? → drawdown / daily loss
           → order frequency → open positions → SIZE → liquidity → COSTS → leverage
           → RiskDecision (a size) or NO TRADE (with every reason)

PURE ON PURPOSE
---------------
`evaluate()` does no I/O and reads no clock except the `now` it is given. Every
input — the kill switch, the strategy's lifecycle permission, recent orders — is
passed in. That is what makes each rule testable on its own, and what makes a
decision reproducible afterwards from its recorded inputs.

SIZING IS HERE, NOT IN THE STRATEGY
-----------------------------------
A strategy proposes a direction, an expected edge and the price at which it is
wrong. The size is the smallest of: risk-per-trade against the stop, the position
cap, the order-value cap, cash actually available (no margin), and what is left
of the instrument, sector and strategy exposure caps. "I'm very confident" buys
nothing — confidence is not an input.

NO TRADE IS AN ANSWER
---------------------
When the round-trip costs at the size this account can actually take exceed the
edge, the result is NO TRADE with the arithmetic attached. At a few tens of
pounds that will be common, and it is correct: an edge that does not survive its
costs is not an edge.

CLOSING IS NOT OPENING
----------------------
A sell that closes a held position reduces risk, so drawdown, daily-loss,
frequency-of-entry, exposure and viability checks do not block it. There is no
short selling in this phase: a sell with nothing held is refused.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from . import costs as _costs
from .account import AccountState
from .limits import HardLimits
from .market import Quote, check as freshness_check
from .signals import Signal

#: A trade smaller than this, in account currency, is refused outright: the
#: rounding in fees and fills dominates anything it could earn.
MIN_NOTIONAL = 1.0
#: The account snapshot must be at least this recent to size against.
MAX_ACCOUNT_AGE_S = 120.0


@dataclass
class RiskDecision:
    approved: bool
    qty: float = 0.0
    notional: float = 0.0
    price: float = 0.0
    closing: bool = False
    reasons: list = field(default_factory=list)          # why NOT, if not approved
    checks: dict = field(default_factory=dict)           # name -> {ok, detail}
    sizing: dict = field(default_factory=dict)
    costs: Optional[dict] = None
    expected_net_edge_bps: Optional[float] = None

    def to_dict(self) -> dict:
        return {"approved": self.approved, "qty": self.qty, "notional": self.notional,
                "price": self.price, "closing": self.closing, "reasons": self.reasons,
                "checks": self.checks, "sizing": self.sizing, "costs": self.costs,
                "expected_net_edge_bps": self.expected_net_edge_bps}


class RiskEngine:
    def __init__(self, *, fee_schedule: _costs.FeeSchedule,
                 min_net_edge_bps: float = 10.0):
        self.fees = fee_schedule
        self.min_net_edge_bps = float(min_net_edge_bps)

    def evaluate(self, signal: Signal, *, quote: Optional[Quote],
                 account: AccountState, limits: HardLimits, now: datetime,
                 kill_switch_engaged: bool, strategy_allowed: tuple[bool, str],
                 recent_orders: list[datetime] | None = None,
                 adv: float | None = None, daily_vol: float | None = None) -> RiskDecision:
        d = RiskDecision(approved=False)

        def check(name: str, ok: bool, detail: str = "") -> bool:
            d.checks[name] = {"ok": bool(ok), "detail": detail}
            if not ok:
                d.reasons.append(f"{name}: {detail}")
            return ok

        # ── state that must be known before anything else is worth asking ──
        if not check("kill_switch", not kill_switch_engaged,
                     "engaged — no new orders" if kill_switch_engaged else "clear"):
            return d
        if not check("account_known", account.known,
                     account.why_unknown or "read from broker"):
            return d
        acct_age = (now - account.as_of).total_seconds() if account.as_of else math.inf
        if not check("account_fresh", acct_age <= MAX_ACCOUNT_AGE_S,
                     f"snapshot {acct_age:.0f}s old (limit {MAX_ACCOUNT_AGE_S:.0f}s)"):
            return d
        ok, why = strategy_allowed
        if not check("strategy_allowed", ok, why):
            return d
        fr = freshness_check(quote, max_age_s=limits.max_quote_age_seconds, now=now)
        if not check("market_data_fresh", fr.ok, fr.reason or f"{fr.age_s:.1f}s old"):
            return d
        sig_age = (now - signal.data_timestamp).total_seconds()
        if not check("signal_data_fresh", sig_age <= limits.max_quote_age_seconds * 4,
                     f"signal computed from data {sig_age:.0f}s old"):
            return d

        held = account.held_qty(signal.symbol)
        closing = signal.side == "sell"
        d.closing = closing
        if closing and held <= 0:
            check("no_short_selling", False,
                  f"sell with nothing held in {signal.symbol}; short selling is "
                  f"not enabled in this phase")
            return d
        price = quote.ask if signal.side == "buy" else quote.bid
        d.price = price

        # ── closing: size = what is held; nothing below may block it ───────
        if closing:
            d.qty = held
            d.notional = held * price
            d.sizing = {"rule": "close the held position", "held": held}
            d.costs = _costs.round_trip(self.fees, quote, held, "sell").to_dict()
            check("closing_position", True, f"reduce-only: sell {held} held")
            d.approved = True
            return d

        # ── opening: every protection applies ──────────────────────────────
        check("max_drawdown", account.drawdown_pct < limits.max_drawdown_pct,
              f"drawdown {account.drawdown_pct:.2f}% vs limit {limits.max_drawdown_pct}%")
        check("max_daily_loss", -account.daily_pnl_pct < limits.max_daily_loss_pct,
              f"today {account.daily_pnl_pct:+.2f}% vs limit -{limits.max_daily_loss_pct}%")
        recent = recent_orders or []
        last_min = sum(1 for t in recent if now - t <= timedelta(seconds=60))
        today = sum(1 for t in recent if t.date() == now.date())
        check("order_frequency_minute", last_min < limits.max_orders_per_minute,
              f"{last_min} orders in the last minute (limit {limits.max_orders_per_minute})")
        check("order_frequency_day", today < limits.max_orders_per_day,
              f"{today} orders today (limit {limits.max_orders_per_day})")
        open_names = {h.symbol for h in account.holdings if h.qty > 0}
        if signal.symbol not in open_names:
            check("max_open_positions", len(open_names) < limits.max_open_positions,
                  f"{len(open_names)} open (limit {limits.max_open_positions})")
        if d.reasons:
            return d

        eq = account.equity
        if not check("equity_positive", eq > 0, f"equity {eq}"):
            return d
        if signal.stop_price is None or signal.stop_price <= 0:
            check("stop_defined", False, "no stop price — a trade whose invalidation "
                                         "is undefined cannot be sized")
            return d
        per_unit_risk = abs(price - signal.stop_price)
        if not check("stop_sane", 0 < per_unit_risk < price,
                     f"stop {signal.stop_price} vs entry {price}"):
            return d

        fee_frac = (self.fees.taker_bps + self.fees.sell_regulatory_bps) / 1e4
        candidates = {
            "risk_per_trade": eq * limits.risk_per_trade_pct / 100 / per_unit_risk,
            "max_position": eq * limits.max_position_pct / 100 / price,
            "max_order_value": limits.max_order_value / price,
            "available_cash": account.available_cash / (price * (1 + fee_frac)),
            "instrument_exposure": max(0.0, eq * limits.max_instrument_exposure_pct / 100
                                       - account.exposure(symbol=signal.symbol)) / price,
            "strategy_exposure": max(0.0, eq * limits.max_strategy_exposure_pct / 100
                                     - account.exposure(strategy_id=signal.strategy_id)) / price,
        }
        if signal.sector:
            candidates["sector_exposure"] = max(
                0.0, eq * limits.max_sector_exposure_pct / 100
                - account.exposure(sector=signal.sector)) / price
        if quote.ask_size > 0:
            candidates["liquidity"] = quote.ask_size * limits.max_participation_pct / 100
        binding = min(candidates, key=candidates.get)
        qty = candidates[binding]
        qty = math.floor(qty * 1e6) / 1e6 if signal.fractionable else float(math.floor(qty))
        d.sizing = {k: round(v, 6) for k, v in candidates.items()}
        d.sizing["binding_constraint"] = binding
        if not check("size_viable", qty > 0 and qty * price >= MIN_NOTIONAL,
                     f"largest allowed size is {qty:g} ({qty * price:.2f} notional), "
                     f"limited by {binding}; minimum trade is {MIN_NOTIONAL}"):
            return d

        est = _costs.round_trip(self.fees, quote, qty, signal.side,
                                adv=adv, daily_vol=daily_vol)
        d.costs = est.to_dict()
        net = signal.expected_edge_bps - est.total_bps
        d.expected_net_edge_bps = round(net, 2)
        if not check("economically_viable", net >= self.min_net_edge_bps,
                     f"NO TRADE — at {qty * price:.2f} notional the round trip costs "
                     f"{est.total_bps:.1f} bps against a gross edge of "
                     f"{signal.expected_edge_bps:.1f} bps, leaving {net:.1f} bps "
                     f"(needs {self.min_net_edge_bps:.1f})"):
            return d

        lev_after = (account.gross_exposure + qty * price) / eq
        if not check("max_leverage", lev_after <= limits.max_leverage + 1e-9,
                     f"leverage after trade {lev_after:.2f} (limit {limits.max_leverage})"):
            return d

        d.qty, d.notional, d.approved = qty, round(qty * price, 6), True
        return d

    def resize(self, decision: RiskDecision, qty: float, *, signal: Signal,
               quote: Quote, reason: str, adv: float | None = None,
               daily_vol: float | None = None) -> RiskDecision:
        """The same decision at a SMALLER size imposed by a later gate (the
        portfolio-risk layer). The minimum trade and the cost viability are
        checked again at the new size: an edge that survives its costs at 10
        shares need not survive them at 3. Never enlarges a decision."""
        d = RiskDecision(approved=False, price=decision.price, closing=decision.closing,
                         checks=dict(decision.checks), sizing=dict(decision.sizing),
                         reasons=[])
        qty = min(float(qty), decision.qty)
        qty = math.floor(qty * 1e6) / 1e6 if signal.fractionable else float(math.floor(qty))
        d.sizing["portfolio_risk"] = round(qty, 6)
        d.sizing["binding_constraint"] = "portfolio_risk"
        d.checks["portfolio_risk_size"] = {"ok": True, "detail": reason}
        if qty <= 0 or qty * d.price < MIN_NOTIONAL:
            d.checks["size_viable"] = {"ok": False, "detail": f"reduced size {qty:g} "
                                                              f"({qty * d.price:.2f}) is below the minimum trade"}
            d.reasons.append(f"size_viable: reduced to {qty:g} by portfolio risk — below "
                             f"the minimum trade of {MIN_NOTIONAL}")
            return d
        if not d.closing:
            est = _costs.round_trip(self.fees, quote, qty, signal.side, adv=adv, daily_vol=daily_vol)
            d.costs = est.to_dict()
            net = signal.expected_edge_bps - est.total_bps
            d.expected_net_edge_bps = round(net, 2)
            ok = net >= self.min_net_edge_bps
            d.checks["economically_viable"] = {
                "ok": ok, "detail": f"at the reduced {qty * d.price:.2f} notional the round trip "
                                    f"costs {est.total_bps:.1f} bps, leaving {net:.1f} bps"}
            if not ok:
                d.reasons.append(f"economically_viable: NO TRADE at the reduced size — "
                                 f"{net:.1f} bps net (needs {self.min_net_edge_bps:.1f})")
                return d
        d.qty, d.notional, d.approved = qty, round(qty * d.price, 6), True
        return d
