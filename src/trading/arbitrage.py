"""
src/trading/arbitrage.py
========================
Arbitrage, evaluated honestly — which mostly means finding out there isn't any.

A price difference is not an opportunity. What matters is what is left after:

    fees (both legs) · the spread actually crossed · slippage beyond the touch
    latency between seeing and acting · liquidity at that size · borrow cost
    transfer time and cost between venues · funding · execution probability

    net edge = gross spread − all of the above

and then whether the size that clears all of it is large enough to be worth
anything at £30 a month. Usually it is not, and `NOT_ARBITRAGE` is the correct,
most common answer.

WHAT THIS MODULE REFUSES TO CALL ARBITRAGE
------------------------------------------
false arbitrage   a spread too large to be real is a stale feed, a different
                  instrument, a different settlement date or a halted symbol,
                  not free money
stale quotes      an old quote is a price that no longer exists
illiquid          an edge that exists for 3 shares is not an edge
statistical       "statistical arbitrage" is mean reversion with a risk of loss.
                  It is evaluated here, and labelled `statistical`, and never
                  given the verdict of a true arbitrage
one-legged        if the legs cannot execute simultaneously, the position after
                  a half-fill is an outright directional bet. The expected value
                  used here charges for that, explicitly.

RESEARCH ONLY
-------------
This module computes and explains. It imports no order engine and no broker
adapter, and nothing in it can place an order: an opportunity becomes a `Signal`
for the normal pipeline only if the owner builds one, and that signal then faces
the same risk engine and validator as everything else.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from . import costs as _costs
from .market import Quote, check as freshness_check

CROSS_VENUE = "cross_venue"
TRIANGULAR = "triangular"
CASH_FUTURES = "cash_futures"
STATISTICAL = "statistical"
TRUE_ARBITRAGE = (CROSS_VENUE, TRIANGULAR, CASH_FUTURES)
KINDS = TRUE_ARBITRAGE + (STATISTICAL,)

# verdicts
CANDIDATE = "CANDIDATE"
NOT_ARBITRAGE = "NOT_ARBITRAGE"
STALE = "STALE"
ILLIQUID = "ILLIQUID"
EXECUTION_RISK = "EXECUTION_RISK"
FALSE_ARBITRAGE = "FALSE_ARBITRAGE"
STATISTICAL_SPREAD = "STATISTICAL_SPREAD"

#: A gross spread above this on a supposedly riskless trade is not an
#: opportunity anyone left lying around; it is a data problem.
IMPLAUSIBLE_SPREAD_BPS = 300.0
#: Minimum probability that every leg executes before it is worth considering.
MIN_EXECUTION_PROBABILITY = 0.75


@dataclass(frozen=True)
class Leg:
    venue: str
    symbol: str
    side: str                       # buy | sell
    quote: Quote
    fees: _costs.FeeSchedule
    ratio: float = 1.0              # units of this leg per unit of the opportunity
    held_qty: float = 0.0           # already held: a sell of this much needs no borrow
    borrow_bps_per_day: float = 0.0
    funding_bps: float = 0.0
    transfer_cost: float = 0.0      # moving cash or asset to this venue
    transfer_s: float = 0.0         # how long that takes
    settles_in_s: float = 0.0       # T+n, in seconds


@dataclass
class ArbResult:
    kind: str
    verdict: str
    qty: float
    gross_bps: float = 0.0
    net_bps: float = 0.0
    gross_profit: float = 0.0
    net_profit: float = 0.0
    costs: dict = field(default_factory=dict)
    max_executable_qty: float = 0.0
    execution_probability: float = 0.0
    simultaneous: bool = True
    leg_failure_loss: float = 0.0
    expected_value: float = 0.0
    reasons: list = field(default_factory=list)
    legs: list = field(default_factory=list)
    research_only: bool = True

    @property
    def tradable_candidate(self) -> bool:
        return self.verdict == CANDIDATE

    def to_dict(self) -> dict:
        return {"kind": self.kind, "verdict": self.verdict, "qty": self.qty,
                "gross_bps": round(self.gross_bps, 2), "net_bps": round(self.net_bps, 2),
                "gross_profit": round(self.gross_profit, 6),
                "net_profit": round(self.net_profit, 6), "costs": self.costs,
                "max_executable_qty": round(self.max_executable_qty, 6),
                "execution_probability": round(self.execution_probability, 4),
                "simultaneous": self.simultaneous,
                "leg_failure_loss": round(self.leg_failure_loss, 6),
                "expected_value": round(self.expected_value, 6),
                "reasons": self.reasons, "legs": self.legs,
                "research_only": True}


def _leg_price(leg: Leg) -> float:
    """The price this leg actually gets: crossing the spread, not the mid."""
    return leg.quote.ask if leg.side == "buy" else leg.quote.bid


def evaluate(kind: str, legs: list[Leg], qty: float, *, now: datetime,
             max_quote_age_s: float = 5.0, latency_ms: float = 250.0,
             min_net_edge_bps: float = 10.0, participation_pct: float = 10.0,
             holding_days: float = 0.0, adv: Optional[float] = None) -> ArbResult:
    """Evaluate one opportunity at one size. Never raises; every refusal is a
    verdict with its arithmetic attached."""
    if kind not in KINDS:
        raise ValueError(f"unknown arbitrage kind {kind!r}; one of {KINDS}")
    r = ArbResult(kind=kind, verdict=CANDIDATE, qty=qty)
    r.legs = [{"venue": l.venue, "symbol": l.symbol, "side": l.side,
               "price": _leg_price(l), "bid": l.quote.bid, "ask": l.quote.ask,
               "ratio": l.ratio} for l in legs]

    # ── structure ────────────────────────────────────────────────────────
    if len(legs) < 2:
        r.verdict = NOT_ARBITRAGE
        r.reasons.append("an arbitrage needs at least two legs")
        return r
    sides = {l.side for l in legs}
    if sides != {"buy", "sell"}:
        r.verdict = NOT_ARBITRAGE
        r.reasons.append("every leg is on the same side — this is a directional "
                         "position, not an arbitrage")
        return r
    if kind == CROSS_VENUE and len({l.venue for l in legs}) < 2:
        r.verdict = NOT_ARBITRAGE
        r.reasons.append("a cross-venue arbitrage needs two venues")
        return r
    if qty <= 0:
        r.verdict = NOT_ARBITRAGE
        r.reasons.append("size must be positive")
        return r

    # ── freshness: an old price is not a price ───────────────────────────
    for l in legs:
        fr = freshness_check(l.quote, max_age_s=max_quote_age_s, now=now)
        if not fr.ok:
            r.verdict = STALE
            r.reasons.append(f"{l.venue}/{l.symbol}: {fr.reason}")
    if r.verdict == STALE:
        return r

    # ── gross ────────────────────────────────────────────────────────────
    buy_cost = sum(_leg_price(l) * qty * l.ratio for l in legs if l.side == "buy")
    sell_proceeds = sum(_leg_price(l) * qty * l.ratio for l in legs if l.side == "sell")
    if buy_cost <= 0:
        r.verdict = NOT_ARBITRAGE
        r.reasons.append("no priced buy leg")
        return r
    r.gross_profit = sell_proceeds - buy_cost
    r.gross_bps = r.gross_profit / buy_cost * 1e4

    # ── false arbitrage ──────────────────────────────────────────────────
    if kind in TRUE_ARBITRAGE and r.gross_bps > IMPLAUSIBLE_SPREAD_BPS:
        r.verdict = FALSE_ARBITRAGE
        r.reasons.append(
            f"gross spread {r.gross_bps:.0f} bps is implausible for a riskless "
            f"trade; treat as a data problem (stale feed, different instrument, "
            f"different settlement, or a halted symbol) until proven otherwise")
        return r

    # ── costs ────────────────────────────────────────────────────────────
    fees = slippage = transfer = borrow = funding = 0.0
    for l in legs:
        q = qty * l.ratio
        price = _leg_price(l)
        fees += _costs.order_fee(l.fees, side=l.side, qty=q, price=price)
        slippage += _costs.slippage_estimate(l.quote, q, adv=adv)
        transfer += l.transfer_cost
        if l.side == "sell":
            borrowed = max(0.0, q - l.held_qty)
            days = max(holding_days, (l.settles_in_s + l.transfer_s) / 86400)
            borrow += borrowed * price * (l.borrow_bps_per_day / 1e4) * days
        funding += abs(q * price) * (l.funding_bps / 1e4)
    # The spread is already paid in the prices above (bid for sells, ask for
    # buys), so it is not added again here — double-counting it would reject
    # trades that are actually viable.
    total_cost = fees + slippage + transfer + borrow + funding
    r.costs = {"fees": round(fees, 6), "slippage": round(slippage, 6),
               "transfer": round(transfer, 6), "borrow": round(borrow, 6),
               "funding": round(funding, 6), "total": round(total_cost, 6),
               "spread": "paid in the leg prices (ask for buys, bid for sells)"}
    r.net_profit = r.gross_profit - total_cost
    r.net_bps = r.net_profit / buy_cost * 1e4

    # ── liquidity ────────────────────────────────────────────────────────
    caps = []
    for l in legs:
        depth = l.quote.ask_size if l.side == "buy" else l.quote.bid_size
        if depth and depth > 0:
            caps.append(depth * participation_pct / 100 / max(l.ratio, 1e-9))
    r.max_executable_qty = min(caps) if caps else 0.0
    if not caps:
        r.verdict = ILLIQUID
        r.reasons.append("no displayed size on at least one leg — the size that "
                         "could actually trade is unknown, so it is treated as none")
        return r
    if qty > r.max_executable_qty:
        r.verdict = ILLIQUID
        r.reasons.append(f"size {qty:g} exceeds {participation_pct:g}% of displayed "
                         f"depth ({r.max_executable_qty:.4f})")
        return r

    # ── simultaneity and execution probability ───────────────────────────
    r.simultaneous = all(l.transfer_s <= 0 and l.settles_in_s <= 0 for l in legs)
    p = 1.0
    for l in legs:
        depth = (l.quote.ask_size if l.side == "buy" else l.quote.bid_size) or 0.0
        share = (qty * l.ratio) / depth if depth > 0 else 1.0
        age = freshness_check(l.quote, max_age_s=max_quote_age_s, now=now).age_s
        # A quote that is already half its allowed age, plus the latency still to
        # be spent getting there, is less likely to be there on arrival.
        time_used = min(1.0, (age + latency_ms / 1000) / max(max_quote_age_s, 1e-9))
        p_leg = max(0.0, (1 - 0.8 * time_used) * (1 - 0.5 * min(1.0, share)))
        p *= p_leg
    if not r.simultaneous:
        p *= 0.5
        r.reasons.append("legs cannot execute simultaneously (transfer or settlement "
                         "delay) — a half-filled pair is an outright position")
    r.execution_probability = p

    # What one leg failing costs: unwinding the filled leg back across the
    # spread, paying fees again.
    unwind = 0.0
    for l in legs:
        q = qty * l.ratio
        unwind += q * l.quote.spread + 2 * _costs.order_fee(
            l.fees, side=l.side, qty=q, price=_leg_price(l))
    r.leg_failure_loss = unwind / max(len(legs), 1)
    r.expected_value = p * r.net_profit - (1 - p) * r.leg_failure_loss

    # ── verdict ──────────────────────────────────────────────────────────
    if kind == STATISTICAL:
        r.verdict = STATISTICAL_SPREAD
        r.reasons.append("statistical spread: mean reversion with a real risk of "
                         "loss, not arbitrage. It must earn its way through the "
                         "strategy lifecycle like any other directional idea")
        return r
    if r.net_bps < min_net_edge_bps:
        r.verdict = NOT_ARBITRAGE
        r.reasons.append(
            f"NO TRADE — gross {r.gross_bps:.1f} bps minus {total_cost:.4f} in costs "
            f"leaves {r.net_bps:.1f} bps (needs {min_net_edge_bps:.1f}); "
            f"net {r.net_profit:+.4f} on {buy_cost:.2f} traded")
        return r
    if p < MIN_EXECUTION_PROBABILITY or r.expected_value <= 0:
        r.verdict = EXECUTION_RISK
        r.reasons.append(
            f"execution probability {p:.0%} (minimum {MIN_EXECUTION_PROBABILITY:.0%}); "
            f"expected value {r.expected_value:+.4f} after charging "
            f"{r.leg_failure_loss:.4f} for a one-legged fill")
        return r
    r.reasons.append(
        f"net {r.net_bps:.1f} bps after {total_cost:.4f} of costs, "
        f"{p:.0%} execution probability, expected value {r.expected_value:+.4f} — "
        f"a research candidate, not an instruction to trade")
    return r


def scan(opportunities: list[dict], **kwargs) -> dict:
    """Evaluate several and summarise. `opportunities` are dicts of
    {kind, legs, qty}; everything else comes from `kwargs`."""
    results = []
    for o in opportunities:
        try:
            results.append(evaluate(o["kind"], o["legs"], o["qty"], **kwargs).to_dict())
        except Exception as exc:
            results.append({"kind": o.get("kind"), "verdict": "ERROR",
                            "reasons": [f"{type(exc).__name__}: {exc}"]})
    by_verdict: dict[str, int] = {}
    for r in results:
        by_verdict[r["verdict"]] = by_verdict.get(r["verdict"], 0) + 1
    return {"evaluated": len(results), "by_verdict": by_verdict,
            "candidates": [r for r in results if r["verdict"] == CANDIDATE],
            "results": results, "research_only": True}


def best_size(kind: str, legs: list[Leg], *, now: datetime, lo: float = 1e-6,
              hi: Optional[float] = None, steps: int = 24, **kwargs) -> dict:
    """The size with the highest expected value, found by scanning — because
    net edge is not monotonic in size: fees have minimums, and slippage grows.
    Returns the best evaluation, or the reason there is none."""
    if hi is None:
        # The largest size worth trying is what the depth allows at the
        # participation cap — anything above it is ILLIQUID by construction, and
        # scanning there would report "illiquid" as the reason a spread failed
        # when the real reason is that it was never wide enough.
        part = kwargs.get("participation_pct", 10.0)
        depths = [((l.quote.ask_size if l.side == "buy" else l.quote.bid_size) or 0.0)
                  * part / 100 / max(l.ratio, 1e-9) for l in legs]
        hi = max(min(depths) if depths else 0.0, lo)
    best = None
    for i in range(1, steps + 1):
        q = lo + (hi - lo) * i / steps
        res = evaluate(kind, legs, q, now=now, **kwargs)
        if res.verdict == CANDIDATE and (best is None or
                                         res.expected_value > best.expected_value):
            best = res
    if best is None:
        probe = evaluate(kind, legs, hi, now=now, **kwargs)
        return {"found": False, "why": probe.reasons, "verdict": probe.verdict,
                "sizes_tried": steps, "max_size_tried": round(hi, 6)}
    return {"found": True, "best": best.to_dict(),
            "expected_value": round(best.expected_value, 6)}


def total_return_check(gross_bps: float, cost_bps: float) -> dict:
    """The arithmetic, on its own, for a dashboard or a note: net = gross − costs.
    Stated explicitly because it is the step most often skipped."""
    net = gross_bps - cost_bps
    return {"gross_bps": round(gross_bps, 2), "cost_bps": round(cost_bps, 2),
            "net_bps": round(net, 2), "positive": net > 0,
            "note": ("a positive gross spread with negative net edge is the normal "
                     "case, not an anomaly" if net <= 0 else
                     "net of modelled costs only; execution probability and "
                     "liquidity still apply")}
