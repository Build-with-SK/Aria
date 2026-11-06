"""Deterministic linear-contract research risk. Never execution authority.

Unsupported profit-calculation modes require an actual broker calculator and
return UNVERIFIED. No universal pip value, guessed margin or missing-rate fallback.
"""
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_FLOOR

from pydantic import Field

from .contracts import (AccountSnapshot, Contract, FXConversion,
                        InstrumentSpecification, MarketQuote, NonNegative,
                        Positive, RiskAssessment, TradeProposal, content_hash)
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
    # Internal floor = provider floor + buffer, so a trade sized to "just fit"
    # cannot breach on one pip of extra slippage, a swap or a gap (CR-10).
    floor_buffer_fraction: Positive = Decimal("0.005")       # of initial account size
    floor_buffer_slippage_multiple: Positive = Decimal("3")  # × the slippage allowance


class RiskInputs(Contract):
    account: AccountSnapshot
    quote: MarketQuote
    specification: InstrumentSpecification
    conversion: FXConversion | None = None
    profile: RuleProfile
    rule_state: RuleState
    # These are independently measured worst-case risk, including pending orders.
    existing_open_risk: NonNegative
    pending_order_risk: NonNegative
    correlated_risk: NonNegative
    current_notional: NonNegative
    position_count: int = Field(ge=0)
    orders_last_minute: int = Field(ge=0)
    spread_cost: NonNegative
    commission: NonNegative
    slippage_allowance: NonNegative
    financing_allowance: NonNegative
    # Unknown operational checks are not false/clear by default.
    session_open: bool
    kill_switch_clear: bool
    operational_constraints_verified: bool
    book_risk_complete: bool
    cooldown_clear: bool
    duplicate_position: bool
    abnormal_market: bool


def floor_volume(raw: Decimal, step: Decimal) -> Decimal:
    if not raw.is_finite() or not step.is_finite() or raw < 0 or step <= 0:
        raise ValueError("finite nonnegative volume and positive step required")
    return (raw / step).to_integral_value(rounding=ROUND_FLOOR) * step


def assess(proposal: TradeProposal, inputs: RiskInputs, *, now: datetime,
           policy: ResearchRiskPolicy | None = None) -> RiskAssessment:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("timezone-aware clock required")
    policy = policy or ResearchRiskPolicy()
    ph = content_hash({"research": policy.model_dump(mode="json"), "provider": inputs.profile.profile_hash})

    def result(verdict, reasons, quantity=Decimal("0"), risk=Decimal("0")):
        return RiskAssessment(verdict=verdict, reasons=tuple(reasons), policy_hash=ph,
                              proposal_hash=content_hash(proposal), assessed_at=now,
                              expires_at=min(proposal.expires_at, now + timedelta(seconds=policy.approval_ttl_seconds)),
                              quantity=quantity, monetary_risk=risk)

    a, q, spec = inputs.account, inputs.quote, inputs.specification
    instrument = spec.instrument
    if not inputs.kill_switch_clear:
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
    if not (a.positions_complete and a.orders_complete and inputs.book_risk_complete):
        unverified.append("INCOMPLETE_ACCOUNT_BOOK")
    if not inputs.operational_constraints_verified:
        unverified.append("OPERATIONAL_RULES_UNVERIFIED")
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
    factor = Decimal("1")
    if instrument.quote_currency != a.currency:
        fx = inputs.conversion
        if fx is None or fx.from_currency != instrument.quote_currency or fx.to_currency != a.currency:
            unverified.append("FX_CONVERSION_MISSING_OR_REVERSED")
        elif not 0 <= (now - fx.provenance.observed_at).total_seconds() <= policy.max_snapshot_age_seconds or fx.provenance.retrieved_at > now or fx.provenance.quality != "VALID" or fx.provenance.freshness != "FRESH" or fx.provenance.permission != "EXECUTION":
            unverified.append("FX_CONVERSION_UNVERIFIED")
        else:
            factor = fx.rate
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
    if not inputs.session_open or inputs.abnormal_market:
        denied.append("SESSION_CLOSED_UNKNOWN_OR_ABNORMAL")
    if not inputs.cooldown_clear or inputs.duplicate_position:
        denied.append("COOLDOWN_OR_DUPLICATE_POSITION")
    if inputs.position_count >= policy.max_position_count or inputs.orders_last_minute >= min(policy.max_orders_per_minute, inputs.profile.max_server_requests_per_minute):
        denied.append("POSITION_OR_FREQUENCY_CAP")
    if proposal.order_type not in spec.supported_order_types:
        denied.append("ORDER_TYPE_UNSUPPORTED")
    if proposal.quantity % spec.volume_step or not spec.volume_min <= proposal.quantity <= spec.volume_max:
        denied.append("INVALID_VOLUME_STEP_OR_BOUNDS")
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
    costs = inputs.spread_cost + inputs.commission + inputs.slippage_allowance + inputs.financing_allowance
    risk = per_volume * proposal.quantity + costs
    # Today's loss from the account's own evidence: the policy-local midnight
    # balance against current equity (which includes floating P/L, swaps and
    # commissions). A position carried over midnight with a floating loss counts.
    # No separate "today's P&L" input exists to contradict it (CR-05).
    daily_loss = max(Decimal("0"), inputs.rule_state.daily_balance - a.equity)
    existing = inputs.existing_open_risk + inputs.pending_order_risk
    if daily_loss >= a.equity * policy.internal_daily_fraction:
        return result("EMERGENCY_HALT", ["INTERNAL_DAILY_STOP"])
    floor = max(pe.daily_floor, pe.overall_floor)
    buffer = max(inputs.profile.account_size * policy.floor_buffer_fraction,
                 inputs.slippage_allowance * policy.floor_buffer_slippage_multiple)
    if a.equity - floor <= buffer:
        denied.append("WITHIN_FLOOR_BUFFER")
    budget = min(a.equity * policy.ordinary_fraction,
                 a.equity * policy.aggregate_fraction - existing,
                 a.equity * policy.correlated_fraction - inputs.correlated_risk - inputs.pending_order_risk,
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
    if a.equity <= 0 or (inputs.current_notional + notional) > max(a.equity, Decimal("0")) * min(policy.max_leverage, inputs.profile.max_leverage):
        denied.append("LEVERAGE_CAP")
    if a.available_margin - notional * spec.margin_rate < policy.min_available_margin:
        denied.append("INSUFFICIENT_MARGIN")
    if denied:
        return result("DENY", denied)
    return result("ALLOW", ["RESEARCH_CHECKS_PASS; NOT_EXECUTION_AUTHORITY"], proposal.quantity, risk)
