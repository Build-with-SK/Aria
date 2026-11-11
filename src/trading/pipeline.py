"""
src/trading/pipeline.py
=======================
The whole path, in one place, in order:

    signal → market data → RISK ENGINE → order generation → VALIDATOR
           → order engine (idempotent) → broker adapter

and every stage timed with a monotonic clock, because the only honest claim
about speed is a measured one (see `latency.py`).

A MODEL IS NOT IN THIS PICTURE
------------------------------
The pipeline takes a `Signal` — a dataclass with a direction, a gross edge and a
stop. Where that came from (a rule, a screen, a research note a model helped
write) is irrelevant here, and nothing downstream can ask. There is no branch in
this module that a language model can reach, no field it can set that changes
sizing, and no way to raise a limit by arguing. src/trading imports nothing from
src.brain, src.inference or src.consult, and a test enforces it.

MODES
-----
RESEARCH  runs everything — quote, risk, order construction, validation — and
          then stops, returning the order that WOULD have been sent. That is
          what makes research meaningful: the same code path, minus the send.
PAPER     submits to a paper account through the mode gate.
LIVE      submits through the live adapter, which re-checks the mode on every
          call, and still passes the risk engine and validator first.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from . import account as _account
from . import costs as _costs
from . import latency as _latency
from . import lifecycle, limits as _limits, modes
from .account import AccountState, Holding
from .broker import BrokerAdapter, OrderAck, OrderSpec
from .orders import OrderEngine
from .risk import RiskDecision, RiskEngine
from .signals import Signal
from .validator import validate

logger = logging.getLogger(__name__)


@dataclass
class PipelineResult:
    signal_id: str
    symbol: str
    mode: str
    stage_reached: str                 # session | market_data | risk | validation | submitted
    submitted: bool = False
    dry_run: bool = False
    reasons: list = field(default_factory=list)
    decision: Optional[dict] = None
    violations: list = field(default_factory=list)
    order: Optional[dict] = None
    ack: Optional[dict] = None
    timings_ms: dict = field(default_factory=dict)
    session: Optional[dict] = None
    session_at_send: Optional[dict] = None
    portfolio_risk: Optional[dict] = None
    plan: Optional[dict] = None

    def to_dict(self) -> dict:
        return {"signal_id": self.signal_id, "symbol": self.symbol, "mode": self.mode,
                "session": self.session, "session_at_send": self.session_at_send,
                "portfolio_risk": self.portfolio_risk, "plan": self.plan,
                "stage_reached": self.stage_reached, "submitted": self.submitted,
                "dry_run": self.dry_run, "reasons": self.reasons,
                "decision": self.decision, "violations": self.violations,
                "order": self.order, "ack": self.ack, "timings_ms": self.timings_ms}


class _Clock:
    """Monotonic stage timer. Wall clocks go backwards; this does not."""

    def __init__(self):
        self.t0 = time.perf_counter()
        self.marks: dict[str, float] = {}
        self._last = self.t0

    def mark(self, name: str) -> None:
        now = time.perf_counter()
        self.marks[name] = (now - self._last) * 1000
        self._last = now

    def total(self) -> dict:
        return {**{k: round(v, 3) for k, v in self.marks.items()},
                "total": round((time.perf_counter() - self.t0) * 1000, 3)}


class TradingPipeline:
    def __init__(self, broker: BrokerAdapter, *,
                 fee_schedule: Optional[_costs.FeeSchedule] = None,
                 risk_engine: Optional[RiskEngine] = None,
                 order_engine: Optional[OrderEngine] = None,
                 min_net_edge_bps: float = 10.0,
                 tradable_symbols: Optional[set] = None,
                 market_data=None, portfolio_limits=None):
        self.broker = broker
        self.fees = fee_schedule or _costs.SCHEDULES["simulated"]
        self.risk = risk_engine or RiskEngine(fee_schedule=self.fees,
                                              min_net_edge_bps=min_net_edge_bps)
        self.orders = order_engine or OrderEngine()
        self.tradable_symbols = tradable_symbols
        # Portfolio risk (src/risk/pretrade). `market_data` is a src/risk/
        # market_data.MarketData read point in time; without it the check runs
        # on exposures alone and holds unknown-risk positions to a fraction of
        # the name limit. Never a network default: a test or a replay supplies
        # its data, production passes MdMarketData().
        self.market_data = market_data
        self.portfolio_limits = portfolio_limits

    # ── inputs ───────────────────────────────────────────────────────────
    def account_snapshot(self, *, persist: bool = True,
                         now: Optional[datetime] = None) -> AccountState:
        """Read the account. Any failure produces a KNOWN-UNKNOWN state, which
        the risk engine refuses to trade against — never a zero-filled guess."""
        try:
            acct = self.broker.get_account()
            positions = self.broker.get_positions()
        except Exception as exc:
            return _account.unknown(f"{self.broker.name}: {type(exc).__name__}: {exc}")
        holdings = []
        for p in positions:
            price = p.get("market_price")
            if price is None:
                try:
                    q = self.broker.get_quote(p["symbol"])
                    price = q.mid if q else p.get("avg_price", 0.0)
                except Exception:
                    price = p.get("avg_price", 0.0)
            holdings.append(Holding(symbol=p["symbol"], qty=float(p.get("qty", 0)),
                                    avg_price=float(p.get("avg_price", 0) or 0),
                                    market_price=float(price or 0),
                                    sector=p.get("sector", ""),
                                    strategy_id=p.get("strategy_id", "")))
        pending = []
        try:
            for o in self.broker.get_open_orders():
                spec = getattr(self.broker, "specs", {}).get(o.client_order_id)
                if spec:
                    q = self.broker.get_quote(spec.symbol)
                    px = spec.limit_price or (q.ask if q else 0.0)
                    pending.append({"symbol": spec.symbol, "side": spec.side,
                                    "qty": spec.qty, "notional": spec.qty * px,
                                    "strategy_id": spec.strategy_id})
        except Exception as exc:
            return _account.unknown(f"open orders unreadable: {exc}")
        return _account.from_snapshot(
            equity=float(acct.get("equity", 0)), cash=float(acct.get("cash", 0)),
            holdings=holdings, pending_orders=pending,
            currency=acct.get("currency", "USD"),
            fees_paid=float(acct.get("fees_paid", 0) or 0),
            source=self.broker.name, persist=persist, now=now)

    # ── the run ──────────────────────────────────────────────────────────
    def run(self, signal: Signal, *, now: Optional[datetime] = None,
            adv: Optional[float] = None, daily_vol: Optional[float] = None,
            order_type: str = "market", limit_price: Optional[float] = None,
            force_dry_run: bool = False,
            send_at: Optional[datetime] = None,
            conviction: Optional[dict] = None) -> PipelineResult:
        """`now` is when the decision is made; `send_at` is when the order
        actually leaves (default: `now` when the caller fixed it, else the
        wall clock at that moment). The session is checked at both, and the
        SEND-time state is the one that authorizes.

        `conviction` is the prediction behind the signal, as a plain dict
        (src/intelligence/conviction.Conviction.to_dict). It reaches the
        portfolio-risk gate as context; it can veto or defer the entry, never
        size it up. The pipeline does not import the code that builds it."""
        explicit_now = now is not None
        now = now or datetime.now(timezone.utc)
        mode = modes.effective_mode()
        dry = force_dry_run or mode is modes.ExecutionMode.RESEARCH
        res = PipelineResult(signal_id=signal.signal_id, symbol=signal.symbol,
                             mode=mode.value, stage_reached="market_data", dry_run=dry)
        clock = _Clock()

        # 0. SESSION — the first gate of
        #    SESSION -> DATA FRESH -> PRICE -> RISK -> ORDER -> PERMISSION -> MODE.
        # Resolved for THIS instrument (venue, timezone, calendar) by the same
        # abstraction prediction, planning and backtests use. Only OPEN passes;
        # UNKNOWN is refused, never guessed. A thesis that meets a closed
        # market is not executed here — src/trading/session_orders.py decides
        # whether it becomes a standing, conditional or scheduled order.
        from src.markets import sessions as _sessions
        sess = _sessions.session(signal.symbol, now, asset_class=signal.asset_class)
        res.session = sess.to_dict()
        if not sess.tradable:
            res.stage_reached = "session"
            res.reasons.append(
                f"market closed: {sess.state.value} at {sess.venue or 'an unresolved venue'}"
                + (f" — {'; '.join(sess.reasons)}" if sess.reasons else "")
                + (f"; next open {sess.next_open}" if sess.next_open else ""))
            # The thesis is deferred, not discarded: an inert plan (scheduled /
            # conditional / deferred) says when it may be re-evaluated. Nothing
            # executes it — it re-enters this pipeline, every gate again.
            from src.risk import pretrade as _pretrade
            res.plan = _pretrade.deferred_plan(signal.symbol, signal.side, sess, now)
            clock.mark("session")
            res.timings_ms = clock.total()
            _latency.record(res.timings_ms, broker=self.broker.name, outcome="session_closed")
            return res

        # 1. market data + account
        try:
            quote = self.broker.get_quote(signal.symbol)
        except Exception as exc:
            quote = None
            res.reasons.append(f"quote unavailable: {type(exc).__name__}: {exc}")
        account = self.account_snapshot(now=now, persist=not dry)
        clock.mark("market_data")

        # 2. risk
        lim = _limits.load()
        decision: RiskDecision = self.risk.evaluate(
            signal, quote=quote, account=account, limits=lim, now=now,
            kill_switch_engaged=self._kill_switch(),
            strategy_allowed=lifecycle.may_trade(signal.strategy_id, mode),
            recent_orders=self.orders.recent_submission_times(),
            adv=adv, daily_vol=daily_vol)
        clock.mark("decision")
        res.decision = decision.to_dict()
        res.stage_reached = "risk"
        if not decision.approved:
            res.reasons.extend(decision.reasons)
            res.timings_ms = clock.total()
            _latency.record(res.timings_ms, broker=self.broker.name, outcome="no_trade")
            return res

        # 2b. PORTFOLIO RISK — how much risk this adds to THE book: exposure,
        #     concentration, currency, correlation, volatility, factors,
        #     liquidity (src/risk/pretrade). It may reduce the size; it never
        #     enlarges it. A signal's confidence is not permission.
        pr = self._portfolio_risk(signal, decision, quote, account, lim, now,
                                  conviction=conviction)
        clock.mark("portfolio_risk")
        res.portfolio_risk = pr.to_dict()
        if not pr.ok:
            res.stage_reached = "portfolio_risk"
            res.reasons.extend(f"portfolio risk {pr.verdict.value}: {r}" for r in pr.reasons)
            res.timings_ms = clock.total()
            _latency.record(res.timings_ms, broker=self.broker.name, outcome="no_trade")
            return res
        if pr.approved_qty < decision.qty - 1e-12:
            decision = self.risk.resize(decision, pr.approved_qty, signal=signal, quote=quote,
                                        reason=f"portfolio risk REDUCE_SIZE ({pr.binding})",
                                        adv=adv, daily_vol=daily_vol)
            res.decision = decision.to_dict()
            if not decision.approved:
                res.reasons.extend(decision.reasons)
                res.timings_ms = clock.total()
                _latency.record(res.timings_ms, broker=self.broker.name, outcome="no_trade")
                return res

        # 3. order generation + validation
        order: OrderSpec = self.orders.build(signal, decision, order_type=order_type,
                                             limit_price=limit_price)
        res.order = order.to_dict()
        fee_rate = (self.fees.taker_bps + self.fees.sell_regulatory_bps) / 1e4
        res.violations = validate(
            order, quote=quote, account=account, limits=lim, now=now,
            seen_client_ids=set(self.orders.latest()),
            tradable_symbols=self.tradable_symbols,
            allow_fractional=signal.fractionable, fee_rate=fee_rate)
        clock.mark("order_generation")
        res.stage_reached = "validation"
        if res.violations:
            res.reasons.extend(res.violations)
            res.timings_ms = clock.total()
            _latency.record(res.timings_ms, broker=self.broker.name, outcome="rejected")
            return res

        # 4. send — or not
        if dry:
            res.reasons.append(
                f"{mode.value}: the order was built and validated but not sent")
            res.timings_ms = clock.total()
            _latency.record(res.timings_ms, broker=self.broker.name, outcome="dry_run")
            return res
        if not modes.can_place_orders():
            res.reasons.append("orders are not permitted in the current mode or the "
                               "kill switch is engaged")
            res.timings_ms = clock.total()
            return res

        # 5. SESSION again, at the send boundary. Everything above was
        #    decided at `now`; the order leaves at `send_at`. A market that
        #    closed, halted or became UNKNOWN in between refuses here.
        from src.markets import sessions as _sessions
        when = send_at or (now if explicit_now else datetime.now(timezone.utc))
        at_send = _sessions.session(signal.symbol, when, asset_class=signal.asset_class)
        if not at_send.tradable:
            res.session_at_send = at_send.to_dict()
            res.stage_reached = "session"
            res.reasons.append(
                f"session changed before send: {at_send.state.value} at "
                f"{at_send.venue or 'an unresolved venue'}"
                + (f" — {'; '.join(at_send.reasons)}" if at_send.reasons else ""))
            res.timings_ms = clock.total()
            _latency.record(res.timings_ms, broker=self.broker.name, outcome="session_closed")
            return res

        ack: OrderAck = self.orders.submit(order, self.broker)
        clock.mark("broker_ack")
        res.ack = ack.to_dict()
        res.submitted = ack.status not in ("REFUSED", "DUPLICATE", "NOT_SENT")
        res.stage_reached = "submitted" if res.submitted else "refused"
        if not res.submitted:
            res.reasons.append(ack.detail)
        res.timings_ms = clock.total()
        _latency.record(res.timings_ms, broker=self.broker.name,
                        outcome=ack.status.lower())
        return res

    def _portfolio_risk(self, signal, decision, quote, account, lim, now, conviction=None):
        from src.risk import decisions as _decisions
        from src.risk import pretrade as _pretrade
        limits = self.portfolio_limits or _pretrade.PortfolioRiskLimits.from_trading_limits(lim)
        order = _pretrade.ProposedOrder(
            symbol=signal.symbol, side=signal.side, qty=decision.qty, price=decision.price,
            price_at=getattr(quote, "timestamp", None), asset_class=signal.asset_class,
            spread_bps=getattr(quote, "spread_bps", None),
            lot=1e-6 if signal.fractionable else 1.0, sector=signal.sector,
            strategy_id=signal.strategy_id, reduce_only=decision.closing,
            conviction=conviction)
        pr = _pretrade.assess(order, account, at=now, limits=limits, data=self.market_data)
        _decisions.record(pr, path="pipeline", context={
            "strategy_id": signal.strategy_id, "signal_id": signal.signal_id,
            "broker": self.broker.name})
        return pr

    @staticmethod
    def _kill_switch() -> bool:
        try:
            from src.execution import kill_switch
            return kill_switch.is_engaged()
        except Exception:
            return True                       # unreadable = engaged
