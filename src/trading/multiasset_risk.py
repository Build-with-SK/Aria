"""Deterministic linear-contract research risk. Never execution authority.

Unsupported profit-calculation modes require an actual broker calculator and
return UNVERIFIED. No universal pip value, guessed margin or missing-rate fallback.
"""
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_FLOOR
from typing import Annotated

from pydantic import Field, ValidationError

from .contracts import (AccountSnapshot, AssetClass, BookSnapshot, BrokerCalculation, Contract,
                        DailyActivity, EconomicCalendar, FXPairQuote, Instrument,
                        InstrumentSpecification, MarketQuote, NonNegative, Positive,
                        RiskAssessment, TradeProposal, TradingSession, content_hash,
                        revalidated)
from .instrument_registry import expected_contract_sizes
from .operational_rules import (activity_checks, break_checks, instrument_checks, news_checks,
                                session_checks)
from .rule_profiles import RuleProfile, RuleState, evaluate_profile


class ResearchRiskPolicy(Contract):
    version: str = "research-v1"
    ordinary_fraction: Positive = Decimal("0.005")
    exceptional_fraction: Positive = Decimal("0.01")
    internal_daily_fraction: Positive = Decimal("0.02")
    aggregate_fraction: Positive = Decimal("0.02")
    correlated_fraction: Positive = Decimal("0.01")
    max_leverage: Positive = Decimal("3")
    max_position_count: int = Field(default=5, gt=0)
    max_spread_ticks: Positive = Decimal("30")
    min_available_margin: NonNegative = Decimal("100")
    max_orders_per_minute: int = Field(default=2, gt=0)
    max_snapshot_age_seconds: int = Field(default=15, gt=0, le=120)
    max_specification_age_seconds: int = Field(default=86400, gt=0, le=86400)
    approval_ttl_seconds: int = Field(default=30, gt=0, le=60)
    # Provider per-day limits are refused at this share of each (CR-09), and a
    # new trade is counted as this many server requests: send, stop/target
    # modification, close.
    daily_limit_headroom: Annotated[Decimal, Field(gt=0, le=1)] = Decimal("0.8")
    requests_per_trade: int = Field(default=3, gt=0)
    # Extra minutes on top of the provider's 2-hour pre-break window (CR-08).
    pre_break_margin_minutes: int = Field(default=15, ge=0)
    max_calendar_age_seconds: int = Field(default=21600, gt=0, le=86400)
    max_session_age_seconds: int = Field(default=3600, gt=0, le=86400)
    # A stop is a market exit: allow at least this many spreads of slippage,
    # whatever smaller allowance a caller states (review CR-11).
    min_slippage_spreads: NonNegative = Decimal("1")
    # No new entry this soon after a losing close.
    loss_cooldown_minutes: int = Field(default=30, ge=0)
    # Internal floor = provider floor + buffer, so a trade sized to "just fit"
    # cannot breach on one pip of extra slippage, a swap or a gap (CR-10).
    floor_buffer_fraction: Positive = Decimal("0.005")       # of initial account size
    floor_buffer_slippage_multiple: Positive = Decimal("3")  # × the slippage allowance
    # The engine's loss figure must match the broker's own calculation (CR-03).
    broker_agreement_tolerance: Positive = Decimal("0.005")
    # A conversion quote wider than this is not a usable price (CR-04).
    max_fx_spread_fraction: Positive = Decimal("0.01")
    # Backstop for mapping errors: maximum lots per $10,000 of equity, by asset
    # class. The leverage cap normally binds first; this one binds when the
    # contract size is wrong and notional looks small. Absent class => 0 lots.
    lots_per_10k_equity: tuple[tuple[AssetClass, NonNegative], ...] = (
        (AssetClass.FOREX, Decimal("1.0")),
        (AssetClass.METALS, Decimal("0.2")),
        (AssetClass.ENERGY, Decimal("0.5")),
        (AssetClass.AGRICULTURE, Decimal("0.5")),
        (AssetClass.INDICES, Decimal("2.0")),
        (AssetClass.EQUITIES, Decimal("50")),
        (AssetClass.ETFS, Decimal("50")),
        (AssetClass.CRYPTO, Decimal("0.1")),
    )

    def lot_cap(self, asset_class: AssetClass, equity: Decimal) -> Decimal:
        per_10k = dict(self.lots_per_10k_equity).get(asset_class, Decimal("0"))
        return per_10k * max(equity, Decimal("0")) / Decimal("10000")


class RiskInputs(Contract):
    """Evidence only. Every derived figure — book risk, notional, position count,
    duplicates, spread and commission cost, session state, cooldown, kill
    switch — is computed by `assess` from this evidence or read by it directly
    (review CR-11). There is no field in which a caller can state a result.
    """
    account: AccountSnapshot
    quote: MarketQuote
    specification: InstrumentSpecification
    # Needed only when the instrument's quote currency differs from the account
    # currency AND the instrument is not itself that currency pair.
    fx_quote: FXPairQuote | None = None
    broker_calculation: BrokerCalculation | None = None
    profile: RuleProfile
    rule_state: RuleState
    # Every open position and pending order (CR-11).
    book: BookSnapshot
    # Today's broker activity, for the provider's per-day limits (CR-09).
    activity: DailyActivity
    # The instrument's session; always required (CR-11), and the long-break
    # rules read its next open (CR-08).
    session: TradingSession | None = None
    # Required only when the profile's news or gap rules need it (CR-08).
    economic_calendar: EconomicCalendar | None = None
    # Assumptions, in account currency, that can only add risk: slippage is
    # raised to `min_slippage_spreads` of the live spread, and financing is
    # the caller's allowance for holding costs.
    slippage_allowance: NonNegative
    financing_allowance: NonNegative


def kill_switch_engaged() -> bool:
    """The legacy global kill switch, read here rather than trusted from a caller.

    An unreadable switch counts as engaged (review CR-11).
    """
    try:
        from src.execution import kill_switch
        return kill_switch.is_engaged()
    except Exception:
        return True


def conversion_factors(instrument: Instrument, account_currency: str, quote: MarketQuote,
                       fx: FXPairQuote | None, stop_loss: Decimal, *, now: datetime,
                       policy: "ResearchRiskPolicy") -> tuple[Decimal | None, Decimal | None, list[str]]:
    """Quote-currency amount -> account currency: (at mid, conservative, reasons).

    The mid factor is what a broker's calculator would use, for the CR-03
    cross-check. The conservative factor is what risk is measured with: the
    pair side that makes a loss larger, and, when the instrument is itself the
    account/quote pair (USDJPY on a USD account), the stop price too, because
    that is the rate at which the loss is realised (CR-13).
    """
    q, a = instrument.quote_currency, account_currency
    one = Decimal("1")
    if q == a:
        return one, one, []
    if instrument.asset_class == AssetClass.FOREX and instrument.base_currency == a:
        lowest = min(quote.bid, stop_loss)
        if lowest <= 0:
            return None, None, ["NONPOSITIVE_CONVERSION_PRICE"]
        return one / ((quote.bid + quote.ask) / 2), one / lowest, []
    if fx is None or {fx.base, fx.quote} != {q, a}:
        return None, None, ["FX_PAIR_QUOTE_MISSING_OR_MISMATCHED"]
    reasons = []
    pv = fx.provenance
    if (pv.quality != "VALID" or pv.freshness != "FRESH" or pv.permission != "EXECUTION"
            or pv.retrieved_at > now
            or not 0 <= (now - pv.observed_at).total_seconds() <= policy.max_snapshot_age_seconds):
        reasons.append("FX_PAIR_QUOTE_UNVERIFIED")
    if not pv.original_symbol.upper().replace("/", "").startswith(fx.base + fx.quote):
        reasons.append("FX_PAIR_SYMBOL_MISMATCH")
    mid = (fx.bid + fx.ask) / 2
    if (fx.ask - fx.bid) / mid > policy.max_fx_spread_fraction:
        reasons.append("FX_PAIR_QUOTE_ABNORMAL_SPREAD")
    if reasons:
        return None, None, reasons
    if fx.base == q:   # e.g. GBPUSD turns GBP into USD by multiplying: the ask is worse
        return mid, fx.ask, []
    return one / mid, one / fx.bid, []   # e.g. USDJPY turns JPY into USD by dividing: the bid is worse


def floor_volume(raw: Decimal, step: Decimal) -> Decimal:
    if not raw.is_finite() or not step.is_finite() or raw < 0 or step <= 0:
        raise ValueError("finite nonnegative volume and positive step required")
    return (raw / step).to_integral_value(rounding=ROUND_FLOOR) * step


def assess(proposal: TradeProposal, inputs: RiskInputs, *, now: datetime,
           policy: ResearchRiskPolicy | None = None) -> RiskAssessment:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("timezone-aware clock required")
    policy = policy or ResearchRiskPolicy()
    try:
        # model_copy(update=...) skips validation; rebuild so every validator runs.
        proposal, inputs, policy = revalidated(proposal), revalidated(inputs), revalidated(policy)
    except ValidationError as exc:
        return RiskAssessment(verdict="UNVERIFIED", reasons=("INVALID_CONTRACT: "+exc.errors()[0]["type"],),
                              policy_hash="0"*64, proposal_hash="0"*64, assessed_at=now, expires_at=now,
                              quantity=Decimal("0"), monetary_risk=Decimal("0"))
    ph = content_hash({"research": policy.model_dump(mode="json"), "provider": inputs.profile.profile_hash})

    def result(verdict, reasons, quantity=Decimal("0"), risk=Decimal("0")):
        return RiskAssessment(verdict=verdict, reasons=tuple(reasons), policy_hash=ph,
                              proposal_hash=content_hash(proposal), assessed_at=now,
                              expires_at=min(proposal.expires_at, now + timedelta(seconds=policy.approval_ttl_seconds)),
                              quantity=quantity, monetary_risk=risk)

    a, q, spec = inputs.account, inputs.quote, inputs.specification
    instrument = spec.instrument
    if kill_switch_engaged():
        return result("EMERGENCY_HALT", ["KILL_SWITCH"])
    unverified = []
    if proposal.account_id != a.account_id or a.account_id != inputs.profile.account_id:
        unverified.append("ACCOUNT_IDENTITY_MISMATCH")
    if proposal.instrument_id != instrument.instrument_id or q.provenance.instrument_id != instrument.instrument_id or q.provenance.original_symbol != instrument.provider_symbol:
        unverified.append("INSTRUMENT_IDENTITY_MISMATCH")
    if proposal.rule_profile_hash != inputs.profile.profile_hash:
        unverified.append("RULE_PROFILE_CHANGED")
    if a.currency != inputs.profile.account_currency:
        unverified.append("ACCOUNT_CURRENCY_MISMATCH")
    if a.environment not in ("SIMULATION", "DEMO"):
        unverified.append("LIVE_OR_UNKNOWN_ACCOUNT_DISABLED")
    # The broker's DEMO label says nothing about consequences: every FTMO account
    # is a demo account, including a paid Challenge and a funded account (CR-07).
    if inputs.profile.account_class == "LIVE":
        unverified.append("LIVE_ACCOUNT_CLASS_DISABLED")
    # The book, from evidence (CR-11): every position and pending order, fresh.
    book = inputs.book
    book_age = (now - book.observed_at).total_seconds()
    if not (a.positions_complete and a.orders_complete and book.complete
            and book.account_id == a.account_id and 0 <= book_age <= policy.max_snapshot_age_seconds):
        unverified.append("INCOMPLETE_ACCOUNT_BOOK")
    if any(item.loss_at_stop is None for item in book.items):
        unverified.append("BOOK_POSITION_WITHOUT_STOP")
    if spec.commission_per_lot is None or spec.commission_currency != a.currency:
        unverified.append("COMMISSION_UNVERIFIED")
    # Provider operational rules from evidence, not from a caller's "verified" flag (CR-08, CR-09).
    news_unverified, news_denied = news_checks(inputs.profile, instrument, inputs.economic_calendar,
                                               now=now, max_age_seconds=policy.max_calendar_age_seconds)
    session_unverified, session_denied = session_checks(
        instrument, inputs.session, now=now, max_age_seconds=policy.max_session_age_seconds)
    break_unverified, break_denied = [], []
    if not (session_unverified or session_denied):
        break_unverified, break_denied = break_checks(
            inputs.profile, inputs.session, now=now,
            margin=timedelta(minutes=policy.pre_break_margin_minutes))
    activity_unverified, activity_denied = activity_checks(
        inputs.profile, inputs.activity, a.account_id, now=now,
        max_age_seconds=policy.max_snapshot_age_seconds, headroom=policy.daily_limit_headroom,
        requests_per_trade=policy.requests_per_trade,
        max_requests_per_minute=policy.max_orders_per_minute)
    unverified.extend(session_unverified + news_unverified + break_unverified + activity_unverified)
    operational_denied = (instrument_checks(inputs.profile, instrument) + session_denied
                          + news_denied + break_denied + activity_denied)
    for label, observed in (("ACCOUNT", a.observed_at), ("QUOTE", q.provenance.observed_at)):
        age = (now - observed).total_seconds()
        max_age = min(spec.quote_max_age_seconds, policy.max_snapshot_age_seconds)
        if not 0 <= age <= max_age:
            unverified.append(f"{label}_STALE_OR_FUTURE")
    if q.provenance.quality != "VALID" or q.provenance.freshness != "FRESH" or q.provenance.permission != "EXECUTION":
        unverified.append("EXECUTABLE_QUOTE_UNVERIFIED")
    if spec.provenance.quality != "VALID" or spec.provenance.freshness != "FRESH" or spec.provenance.permission != "EXECUTION" or spec.provenance.observed_at > now or spec.provenance.retrieved_at > now:
        unverified.append("INSTRUMENT_SPEC_UNVERIFIED")
    if (now-spec.provenance.retrieved_at).total_seconds()>policy.max_specification_age_seconds:
        unverified.append("INSTRUMENT_SPEC_EXPIRED")
    if spec.calculation_mode != "LINEAR":
        unverified.append("BROKER_PROFIT_CALCULATOR_REQUIRED")
    if spec.tick_value_currency != instrument.quote_currency or spec.tick_value != spec.contract_size * spec.tick_size:
        unverified.append("TICK_VALUE_REQUIRES_BROKER_CALCULATOR")
    if instrument.settlement_currency != instrument.quote_currency:
        unverified.append("NONLINEAR_SETTLEMENT_REQUIRES_BROKER_CALCULATOR")
    factor_now, factor, fx_reasons = conversion_factors(
        instrument, a.currency, q, inputs.fx_quote, proposal.stop_loss, now=now, policy=policy)
    unverified.extend(fx_reasons)
    factor_known = factor is not None
    expected_sizes = expected_contract_sizes(instrument)
    if expected_sizes is not None and spec.contract_size not in expected_sizes:
        unverified.append("CONTRACT_SIZE_OUTSIDE_REVIEWED_REGISTRY")
    bc = inputs.broker_calculation
    broker_margin = Decimal("0")
    if bc is None:
        unverified.append("BROKER_CALCULATION_REQUIRED")
    elif (bc.instrument_id != instrument.instrument_id or bc.currency != a.currency
          or bc.account_id != a.account_id or bc.side != proposal.side
          or bc.provenance.provider != q.provenance.provider
          or bc.provenance.original_symbol != instrument.provider_symbol
          or bc.provenance.permission != "EXECUTION"
          or bc.provenance.instrument_id != instrument.instrument_id
          or bc.provenance.quality != "VALID" or bc.provenance.freshness != "FRESH"
          or bc.provenance.retrieved_at > now
          or not 0 <= (now - bc.provenance.observed_at).total_seconds() <= policy.max_snapshot_age_seconds):
        unverified.append("BROKER_CALCULATION_UNVERIFIED")
    else:
        broker_margin = bc.margin * proposal.quantity / bc.volume
        if factor_known:
            # Same order, two calculators: ours from the specification, the
            # broker's from its own contract data. They must agree.
            engine_loss = abs(bc.entry - bc.stop_loss) * spec.contract_size * bc.volume * factor_now
            broker_loss = -bc.profit_at_stop
            if broker_loss <= 0 or abs(engine_loss - broker_loss) > broker_loss * policy.broker_agreement_tolerance:
                unverified.append("CONTRACT_SPEC_DISAGREES_WITH_BROKER")
    pe = evaluate_profile(inputs.profile, inputs.rule_state, now=now)
    if inputs.rule_state.equity != a.equity:
        unverified.append("RULE_EQUITY_SNAPSHOT_MISMATCH")
    if q.provenance.retrieved_at > now:
        unverified.append("QUOTE_RETRIEVED_IN_FUTURE")
    if pe.status == "POLICY_UNVERIFIED":
        unverified.extend(pe.reasons)
    if unverified:
        return result("UNVERIFIED", unverified)
    if pe.status == "BREACH":
        return result("EMERGENCY_HALT", pe.reasons)
    denied = []
    if a.equity <= 0 or a.balance <= 0:
        denied.append("NONPOSITIVE_EQUITY_OR_BALANCE")
    if proposal.expires_at <= now:
        denied.append("PROPOSAL_EXPIRED")
    if proposal.market_snapshot_id != q.snapshot_id:
        denied.append("MARKET_SNAPSHOT_CHANGED")
    positions = [x for x in book.items if x.kind == "POSITION"]
    pending = [x for x in book.items if x.kind == "PENDING_ORDER"]

    def at_stop(items):
        return sum((x.loss_at_stop for x in items), Decimal("0"))

    # Conservative correlation: any position sharing a currency with this instrument.
    shared = {instrument.base_currency, instrument.quote_currency}
    existing_open_risk, pending_order_risk = at_stop(positions), at_stop(pending)
    correlated_risk = at_stop(x for x in positions if {x.base_currency, x.quote_currency} & shared)
    current_notional = sum((x.notional for x in book.items), Decimal("0"))
    if any(x.instrument_id == instrument.instrument_id and x.side == proposal.side for x in book.items):
        denied.append("DUPLICATE_POSITION")
    last_loss = inputs.activity.last_losing_close_at
    if last_loss is not None and now - last_loss < timedelta(minutes=policy.loss_cooldown_minutes):
        denied.append("LOSS_COOLDOWN")
    if len(positions) >= policy.max_position_count:
        denied.append("POSITION_COUNT_CAP")
    denied.extend(operational_denied)
    if proposal.order_type not in spec.supported_order_types:
        denied.append("ORDER_TYPE_UNSUPPORTED")
    if proposal.quantity % spec.volume_step or not spec.volume_min <= proposal.quantity <= spec.volume_max:
        denied.append("INVALID_VOLUME_STEP_OR_BOUNDS")
    if proposal.quantity > policy.lot_cap(instrument.asset_class, a.equity):
        denied.append("ASSET_CLASS_LOT_CAP")
    if not spec.negative_prices and min(q.bid, proposal.entry, proposal.stop_loss, proposal.take_profit) <= 0:
        denied.append("NONPOSITIVE_PRICE")
    if any(px % spec.tick_size for px in (proposal.entry, proposal.stop_loss, proposal.take_profit)):
        denied.append("PRICE_NOT_ON_TICK")
    if (q.ask - q.bid) / spec.tick_size > policy.max_spread_ticks:
        denied.append("SPREAD_CAP")
    touch = q.ask if proposal.side == "BUY" else q.bid
    # Pending/limit execution needs fill- and gap-specific validation, not a spot shortcut.
    if proposal.order_type != "MARKET":
        return result("UNVERIFIED", ["LIMIT_FILL_MODEL_UNIMPLEMENTED"])
    if abs(touch - proposal.entry) > proposal.price_tolerance:
        denied.append("PRICE_TOLERANCE_EXCEEDED")
    stop_distance = abs(proposal.entry - proposal.stop_loss)
    if stop_distance < max(spec.minimum_stop_distance, spec.tick_size):
        denied.append("STOP_TOO_CLOSE")
    # Include the worst approved entry movement; costs are in account currency.
    per_volume = (stop_distance + proposal.price_tolerance) * spec.contract_size * factor
    # Costs from evidence: the live spread and the published commission (CR-11).
    spread_cost = (q.ask - q.bid) * spec.contract_size * proposal.quantity * factor
    commission = spec.commission_per_lot * proposal.quantity
    slippage = max(inputs.slippage_allowance, spread_cost * policy.min_slippage_spreads)
    costs = spread_cost + commission + slippage + inputs.financing_allowance
    risk = per_volume * proposal.quantity + costs
    # Today's loss from the account's own evidence: the policy-local midnight
    # balance against current equity (which includes floating P/L, swaps and
    # commissions). A position carried over midnight with a floating loss counts.
    # No separate "today's P&L" input exists to contradict it (CR-05).
    daily_loss = max(Decimal("0"), inputs.rule_state.daily_balance - a.equity)
    existing = existing_open_risk + pending_order_risk
    if daily_loss >= a.equity * policy.internal_daily_fraction:
        return result("EMERGENCY_HALT", ["INTERNAL_DAILY_STOP"])
    floor = max(pe.daily_floor, pe.overall_floor)
    buffer = max(inputs.profile.account_size * policy.floor_buffer_fraction,
                 slippage * policy.floor_buffer_slippage_multiple)
    if a.equity - floor <= buffer:
        denied.append("WITHIN_FLOOR_BUFFER")
    budget = min(a.equity * policy.ordinary_fraction,
                 a.equity * policy.aggregate_fraction - existing,
                 a.equity * policy.correlated_fraction - correlated_risk - pending_order_risk,
                 a.equity * policy.internal_daily_fraction - daily_loss - existing,
                 a.equity - floor - buffer - existing)
    max_volume = floor_volume(max(Decimal("0"), budget - costs) / per_volume, spec.volume_step)
    if max_volume * per_volume + costs >= budget:
        max_volume -= spec.volume_step  # strictly inside the budget, never on it
    if max_volume < spec.volume_min:
        denied.append("MINIMUM_LOT_EXCEEDS_RISK_BUDGET")
    # Strict: a trade whose worst case lands exactly on the budget is refused.
    if risk >= budget:
        denied.append("RISK_BUDGET_EXCEEDED")
    notional = (abs(proposal.entry) + proposal.price_tolerance) * spec.contract_size * proposal.quantity * factor
    if a.equity <= 0 or (current_notional + notional) > max(a.equity, Decimal("0")) * min(policy.max_leverage, inputs.profile.max_leverage):
        denied.append("LEVERAGE_CAP")
    # The larger of our estimate and the broker's own margin figure.
    margin_needed = max(notional * spec.margin_rate, broker_margin)
    if a.available_margin - margin_needed < policy.min_available_margin:
        denied.append("INSUFFICIENT_MARGIN")
    if denied:
        return result("DENY", denied)
    return result("ALLOW", ["RESEARCH_CHECKS_PASS; NOT_EXECUTION_AUTHORITY"], proposal.quantity, risk)
