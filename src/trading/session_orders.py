"""
src/trading/session_orders.py
=============================
What a trading thesis becomes when its market is not open, what the next open
probably looks like, and how the plan adapts to the open that actually happens.

Pure and deterministic: no broker, no clock reads, no LLM. Nothing in here
executes anything. A plan is a DECISION about an order; executing it still
goes through TradingPipeline's gates in order:

    SESSION -> DATA FRESH -> PRICE VALID -> RISK -> ORDER -> PERMISSION -> MODE

and a market changing from CLOSED to OPEN is never, by itself, a reason to
send anything.

    plan_order(intent, session, policy, now)        -> OrderPlan
    estimate_open(prev_close, gap_history, proxy)   -> OpenEstimate
    adapt_at_open(plan, observation, policy)        -> OpenDecision

THE FOUR THINGS A CLOSED-MARKET THESIS CAN BECOME
-------------------------------------------------
  STANDING     a limit order resting at the venue (GTC). Only for a patient
               strategy, a limit order, a price near the reference, and only if
               the policy allows standing orders at all (default: no — a
               resting order skips the re-check at the open, and overnight
               news is exactly what that re-check exists for).
  CONDITIONAL  fires only when an IN-SESSION price crosses the trigger.
  SCHEDULED    re-evaluated after the next open, once the first valid market
               observations exist, against the opening rules below.
  DEFERRED     the thesis must be re-derived with fresh information (event-
               driven ideas, an unknown or halted session).
Or REJECT, when the thesis expires before the market can reopen, or the
contract is expiring and the order would open a position.

OPENING RULES
-------------
Measured in ATRs of the gap between the actual open and the reference price
the thesis was built on, signed against the trade (a buy that opens higher is
paying up). Defaults: < 0.5 ATR adverse -> normal size; 0.5-1.5 -> reduced;
>= 1.5 either way, or an open through the stop -> REASSESS (do not trade);
material news -> SUSPEND; spread or liquidity out of bounds -> DEFER; a risk
breach -> REJECT. Nothing is decided on the opening print itself: the plan
waits `min_observation_minutes` for real market observations.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Iterable, Optional


class PlanKind(str, Enum):
    EXECUTE_NOW = "EXECUTE_NOW"
    STANDING = "STANDING"
    CONDITIONAL = "CONDITIONAL"
    SCHEDULED = "SCHEDULED"
    DEFERRED = "DEFERRED"
    REJECT = "REJECT"


class OpenAction(str, Enum):
    WAIT = "WAIT"                  # not enough market observations yet
    NORMAL = "NORMAL"
    REDUCE = "REDUCE"
    REASSESS = "REASSESS"          # the thesis must be re-derived; do not trade
    SUSPEND = "SUSPEND"            # news: hold everything until reassessed
    DEFER = "DEFER"                # market conditions (spread, liquidity) not fit
    REJECT = "REJECT"              # risk breached


@dataclass(frozen=True)
class TradeIntent:
    symbol: str
    side: str                                  # buy | sell
    qty: float
    style: str = "patient"                     # patient | momentum | event
    order_type: str = "market"                 # market | limit
    limit_price: Optional[float] = None
    trigger_price: Optional[float] = None      # conditional entry level
    stop_price: Optional[float] = None
    reference_price: Optional[float] = None    # the price the thesis was built on
    atr: Optional[float] = None
    thesis_expires: Optional[datetime] = None
    reduces_position: bool = False


@dataclass(frozen=True)
class SessionPolicy:
    allow_standing_orders: bool = False
    max_standing_distance_atr: float = 1.0
    reduce_gap_atr: float = 0.5
    reassess_gap_atr: float = 1.5
    reduced_size: float = 0.5
    max_spread_bps: float = 50.0
    min_observation_minutes: int = 5
    proxy_max_age_minutes: int = 15


@dataclass
class OrderPlan:
    kind: PlanKind
    symbol: str
    side: str
    qty: float
    venue: Optional[str]
    session_state: str
    valid_from: Optional[str] = None           # ISO UTC
    expires: Optional[str] = None
    order_type: str = "market"
    limit_price: Optional[float] = None
    trigger_price: Optional[float] = None
    time_in_force: str = "day"
    reference_price: Optional[float] = None
    stop_price: Optional[float] = None
    atr: Optional[float] = None
    reasons: list = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["kind"] = self.kind.value
        return d


def _iso(t: Optional[datetime]) -> Optional[str]:
    return t.astimezone(timezone.utc).isoformat() if t else None


def plan_order(intent: TradeIntent, session, policy: SessionPolicy = SessionPolicy(),
               now: Optional[datetime] = None) -> OrderPlan:
    """Decide what `intent` becomes given `session` (a sessions.SessionInfo)."""
    now = now or datetime.now(timezone.utc)
    state = session.state.value if hasattr(session.state, "value") else str(session.state)
    base = dict(symbol=intent.symbol, side=intent.side, qty=intent.qty,
                venue=session.venue, session_state=state,
                order_type=intent.order_type, limit_price=intent.limit_price,
                reference_price=intent.reference_price, stop_price=intent.stop_price,
                atr=intent.atr)

    if session.tradable:
        return OrderPlan(PlanKind.EXECUTE_NOW, **base,
                         reasons=["session OPEN — through the execution gates now"])
    if state == "UNKNOWN":
        return OrderPlan(PlanKind.DEFERRED, **base, reasons=[
            "session UNKNOWN — never treated as open; re-plan when the venue's "
            "calendar can answer"] + list(session.reasons))
    if state == "HALTED":
        return OrderPlan(PlanKind.DEFERRED, **base, reasons=[
            "trading halted — re-derive the thesis after the resumption, with the "
            "information that caused the halt"])
    if state == "EXPIRING" and not intent.reduces_position:
        return OrderPlan(PlanKind.REJECT, **base, reasons=[
            "contract is on its last trading day — not opening a position"])

    nxt = datetime.fromisoformat(session.next_open) if session.next_open else None
    if nxt is None:
        return OrderPlan(PlanKind.DEFERRED, **base, reasons=[
            f"{state}: the next open cannot be determined — not scheduling blind"])
    if intent.thesis_expires is not None and intent.thesis_expires <= nxt:
        return OrderPlan(PlanKind.REJECT, **base, reasons=[
            f"the thesis expires ({_iso(intent.thesis_expires)}) before the market "
            f"reopens ({_iso(nxt)})"])
    valid_from = nxt + timedelta(minutes=policy.min_observation_minutes)
    expires = _iso(intent.thesis_expires)

    if intent.style == "event":
        return OrderPlan(PlanKind.DEFERRED, **base, valid_from=_iso(valid_from),
                         expires=expires, reasons=[
                             "event-driven thesis: re-derive it with the information "
                             "available at the open rather than scheduling the old one"])
    if intent.trigger_price is not None:
        return OrderPlan(PlanKind.CONDITIONAL, **{**base, "trigger_price": intent.trigger_price},
                         valid_from=_iso(valid_from), expires=expires, reasons=[
                             f"fires only on an in-session price through {intent.trigger_price}, "
                             f"then through every execution gate"])
    if (intent.order_type == "limit" and intent.limit_price and intent.style == "patient"):
        dist = (abs(intent.limit_price - intent.reference_price) / intent.atr
                if intent.reference_price and intent.atr else None)
        if policy.allow_standing_orders and dist is not None \
                and dist <= policy.max_standing_distance_atr:
            return OrderPlan(PlanKind.STANDING, **base, valid_from=_iso(nxt),
                             expires=expires, time_in_force="gtc", reasons=[
                                 f"patient limit {dist:.2f} ATR from the reference rests at "
                                 f"the venue from the next open"])
    return OrderPlan(PlanKind.SCHEDULED, **base, valid_from=_iso(valid_from),
                     expires=expires, reasons=[
                         f"{state}: re-evaluated {policy.min_observation_minutes} min after "
                         f"the next open ({_iso(nxt)}) against the opening rules — never "
                         f"sent as a blind order at the open"])


# ── pre-open estimate ───────────────────────────────────────────────────────

@dataclass(frozen=True)
class ProxyMove:
    """A related instrument that traded while the target was shut (index
    futures for a stock, the US session for an ADR's home market...).
    `log_return` is its move SINCE THE TARGET'S LAST CLOSE, observed at
    `observed_at`."""
    symbol: str
    log_return: float
    beta: float
    observed_at: datetime


@dataclass
class OpenEstimate:
    expected_open: Optional[float]
    low: Optional[float]
    high: Optional[float]
    confidence: Optional[float]               # nominal coverage of [low, high]
    coverage_in_sample: Optional[float]
    method: str
    inputs: list = field(default_factory=list)
    reasons: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def estimate_open(prev_close: float, gap_history: Iterable[float], *,
                  proxy: Optional[ProxyMove] = None,
                  target_last_close: Optional[datetime] = None,
                  now: Optional[datetime] = None,
                  policy: SessionPolicy = SessionPolicy(), z: float = 1.0) -> OpenEstimate:
    """Expected open with an interval, from the target's own history of
    overnight gaps plus (optionally) a proxy that traded overnight.

    gap_history: log(open / previous close), one per past session.
    The interval is +/- z standard deviations of those gaps around the
    expected gap; `confidence` is its NOMINAL coverage under normality and
    `coverage_in_sample` the fraction of past gaps that actually fell inside
    the same band. Neither is an out-of-sample validation — `reasons` says so.
    A proxy is used only if it was observed AFTER the target's last close and
    within proxy_max_age_minutes of `now`; anything else is excluded and named.
    """
    now = now or datetime.now(timezone.utc)
    gaps = [g for g in gap_history if g is not None and math.isfinite(g)]
    if not (prev_close and math.isfinite(prev_close) and prev_close > 0):
        return OpenEstimate(None, None, None, None, None, "none",
                            reasons=["no valid previous close"])
    if len(gaps) < 30:
        return OpenEstimate(prev_close, None, None, None, None, "previous close only",
                            inputs=[{"previous_close": prev_close}],
                            reasons=[f"only {len(gaps)} historical gaps — no interval is "
                                     f"stated rather than an invented one"])
    sd = statistics.pstdev(gaps)
    mu = statistics.fmean(gaps)
    method = "historical gap distribution"
    inputs = [{"previous_close": prev_close}, {"gaps": len(gaps), "gap_sd": round(sd, 6)}]
    reasons = []
    if proxy is not None:
        fresh_after_close = target_last_close is None or proxy.observed_at > target_last_close
        age_min = (now - proxy.observed_at).total_seconds() / 60
        if not fresh_after_close:
            reasons.append(f"proxy {proxy.symbol} excluded: observed before the target's "
                           f"last close (not overnight information)")
        elif age_min > policy.proxy_max_age_minutes or age_min < -1:
            reasons.append(f"proxy {proxy.symbol} excluded: {age_min:.0f} min old")
        elif not math.isfinite(proxy.log_return * proxy.beta):
            reasons.append(f"proxy {proxy.symbol} excluded: non-finite move")
        else:
            mu = proxy.beta * proxy.log_return
            method = f"historical gaps + overnight move in {proxy.symbol} (beta {proxy.beta:.2f})"
            inputs.append({"proxy": proxy.symbol, "log_return": proxy.log_return,
                           "beta": proxy.beta,
                           "observed_at": proxy.observed_at.isoformat()})
    lo_g, hi_g = mu - z * sd, mu + z * sd
    # In-sample coverage of the SAME band width, centred where history is.
    centre = statistics.fmean(gaps)
    inside = sum(1 for g in gaps if centre - z * sd <= g <= centre + z * sd)
    nominal = math.erf(z / math.sqrt(2))
    reasons.append("confidence is the nominal coverage of the interval, not an "
                   "out-of-sample validation")
    return OpenEstimate(round(prev_close * math.exp(mu), 6),
                        round(prev_close * math.exp(lo_g), 6),
                        round(prev_close * math.exp(hi_g), 6),
                        round(nominal, 4), round(inside / len(gaps), 4),
                        method, inputs, reasons)


# ── adapting to the open that actually happened ─────────────────────────────

@dataclass(frozen=True)
class OpenObservation:
    open_price: float
    observed_minutes: float                    # minutes of trading observed so far
    spread_bps: Optional[float] = None
    liquidity_ok: Optional[bool] = None
    material_news: bool = False
    risk_ok: bool = True


@dataclass
class OpenDecision:
    action: OpenAction
    size_multiplier: float
    gap_atr: Optional[float]
    reasons: list = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["action"] = self.action.value
        return d


def adapt_at_open(plan: OrderPlan, obs: OpenObservation,
                  policy: SessionPolicy = SessionPolicy()) -> OpenDecision:
    """Apply the opening rules to a SCHEDULED or CONDITIONAL plan."""
    def d(action, mult, gap, why):
        return OpenDecision(action, mult, None if gap is None else round(gap, 3), [why])

    if obs.observed_minutes < policy.min_observation_minutes:
        return d(OpenAction.WAIT, 0.0, None,
                 f"{obs.observed_minutes:.0f} of {policy.min_observation_minutes} minutes "
                 f"observed — not deciding on the opening print")
    if not obs.risk_ok:
        return d(OpenAction.REJECT, 0.0, None, "risk limits breached at the open")
    if obs.material_news:
        return d(OpenAction.SUSPEND, 0.0, None,
                 "material news since the thesis — suspended for reassessment")
    if obs.liquidity_ok is False or (obs.spread_bps is not None
                                     and obs.spread_bps > policy.max_spread_bps):
        return d(OpenAction.DEFER, 0.0, None,
                 f"opening market not fit to trade (spread {obs.spread_bps} bps, "
                 f"liquidity {'ok' if obs.liquidity_ok else 'insufficient'})")
    if obs.liquidity_ok is None or obs.spread_bps is None:
        return d(OpenAction.DEFER, 0.0, None,
                 "spread or liquidity not observed — unknown is not fit to trade")
    ref, atr = plan.reference_price, plan.atr
    if not (ref and atr and atr > 0 and math.isfinite(obs.open_price)):
        return d(OpenAction.REASSESS, 0.0, None,
                 "no reference price or ATR to judge the opening gap against")
    buy = plan.side == "buy"
    if plan.stop_price is not None and (
            (buy and obs.open_price <= plan.stop_price) or
            (not buy and obs.open_price >= plan.stop_price)):
        return d(OpenAction.REASSESS, 0.0, None,
                 f"opened through the stop ({obs.open_price} vs {plan.stop_price}) — "
                 f"the thesis is invalidated, not discounted")
    gap = (obs.open_price - ref) / atr
    adverse = gap if buy else -gap              # positive = paying up / selling lower
    if abs(gap) >= policy.reassess_gap_atr:
        return d(OpenAction.REASSESS, 0.0, gap,
                 f"extreme opening gap {gap:+.2f} ATR — reassess before trading")
    if adverse > policy.reduce_gap_atr:
        return d(OpenAction.REDUCE, policy.reduced_size, gap,
                 f"adverse opening gap {adverse:.2f} ATR — reduced size")
    return d(OpenAction.NORMAL, 1.0, gap,
             f"opening gap {gap:+.2f} ATR is within tolerance — normal size")
