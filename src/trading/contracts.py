"""Precise, immutable multiasset contracts. No model or broker dependencies.

Legacy equity OrderSpec is deliberately unchanged: its quantities are shares,
whereas InstrumentSpecification below explicitly describes units per volume.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Positive = Annotated[Decimal, Field(gt=0)]
NonNegative = Annotated[Decimal, Field(ge=0)]
Currency = Annotated[str, Field(pattern=r"^[A-Z]{3}$")]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    @field_validator("*", mode="after")
    @classmethod
    def aware_utc(cls, value):
        if isinstance(value, datetime):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("timezone-aware timestamp required")
            return value.astimezone(timezone.utc)
        return value


def revalidated(value: Contract) -> Contract:
    """Re-run every validator on a contract and its nested contracts.

    pydantic's `model_copy(update=...)` does not validate, so an object that
    looks like a contract may hold values its validators would refuse. Every
    trust boundary (the risk engine, rule evaluation, the review store)
    rebuilds its inputs through this before reading them.
    """
    return type(value).model_validate(value.model_dump())


def content_hash(value: Contract | dict) -> str:
    """Canonical JSON hash; callers bind to the complete validated payload."""
    if isinstance(value, Contract):
        value = value.model_dump(mode="json")
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


class AssetClass(str, Enum):
    FOREX = "Forex"
    METALS = "PreciousMetals"
    ENERGY = "EnergyCommodities"
    AGRICULTURE = "AgriculturalCommodities"
    INDICES = "EquityIndices"
    EQUITIES = "IndividualEquities"
    ETFS = "ETFs"
    CRYPTO = "Cryptocurrencies"
    OTHER = "ExtensibleOther"


class DataProvenance(Contract):
    provider: str = Field(min_length=1)
    instrument_id: str = Field(min_length=1)
    original_symbol: str = Field(min_length=1)
    observed_at: datetime
    retrieved_at: datetime
    timezone: str = "UTC"
    data_type: str = Field(min_length=1)
    freshness: Literal["FRESH", "DELAYED", "STALE", "UNKNOWN"]
    quality: Literal["VALID", "SUSPECT", "INVALID", "UNKNOWN"]
    permission: Literal["RESEARCH", "EXECUTION", "UNKNOWN"] = "UNKNOWN"
    source_url: str | None = None

    @model_validator(mode="after")
    def chronology(self):
        from zoneinfo import ZoneInfo
        try:
            ZoneInfo(self.timezone)
        except (KeyError, ValueError) as exc:
            raise ValueError("unknown provenance timezone") from exc
        if self.retrieved_at < self.observed_at:
            raise ValueError("retrieval precedes observation")
        return self


class Instrument(Contract):
    instrument_id: str = Field(min_length=1)
    provider_symbol: str = Field(min_length=1)
    asset_class: AssetClass
    product: Literal["SPOT", "CFD", "FUTURE", "ETF", "EQUITY", "CRYPTO"]
    venue: str = Field(min_length=1)
    base_currency: Currency
    quote_currency: Currency
    settlement_currency: Currency
    calendar: str = Field(min_length=1)


class InstrumentSpecification(Contract):
    instrument: Instrument
    contract_size: Positive
    tick_size: Positive
    # Tick value is provider evidence, not a universal FX pip assumption.
    tick_value: Positive
    tick_value_currency: Currency
    volume_min: Positive
    volume_max: Positive
    volume_step: Positive
    minimum_stop_distance: NonNegative = Decimal("0")
    margin_rate: Annotated[Decimal, Field(gt=0, le=1)]
    supported_order_types: tuple[str, ...]
    quote_max_age_seconds: int = Field(gt=0, le=300)
    negative_prices: bool = False
    calculation_mode: Literal["LINEAR", "BROKER_CALCULATOR_REQUIRED"]
    provenance: DataProvenance

    @model_validator(mode="after")
    def valid_spec(self):
        if self.volume_min > self.volume_max:
            raise ValueError("minimum volume exceeds maximum")
        if self.volume_min % self.volume_step or self.volume_max % self.volume_step:
            raise ValueError("volume bounds must align to volume_step")
        if self.provenance.instrument_id != self.instrument.instrument_id:
            raise ValueError("specification identity mismatch")
        return self


class TradingSession(Contract):
    instrument_id: str
    state: Literal["OPEN", "CLOSED", "HALTED", "UNKNOWN"]
    opens_at: datetime
    closes_at: datetime
    calendar: str
    # When trading resumes after closes_at; the gap is the market break that
    # FTMO's weekend and gap-trading rules measure (review CR-08).
    next_opens_at: datetime | None = None
    provenance: DataProvenance | None = None

    @model_validator(mode="after")
    def interval(self):
        if self.closes_at <= self.opens_at:
            raise ValueError("invalid session interval")
        if self.next_opens_at is not None and self.next_opens_at <= self.closes_at:
            raise ValueError("next session must open after this one closes")
        if self.provenance is not None and self.provenance.instrument_id != self.instrument_id:
            raise ValueError("session identity mismatch")
        return self


class MarketQuote(Contract):
    bid: Decimal
    ask: Decimal
    snapshot_id: str = Field(min_length=1)
    provenance: DataProvenance

    @model_validator(mode="after")
    def spread(self):
        if self.ask < self.bid:
            raise ValueError("crossed quote")
        return self


class OHLCVBar(Contract):
    opens_at: datetime
    closes_at: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: NonNegative | None = None
    volume_type: Literal["TRADED", "TICK", "UNAVAILABLE"]
    completed: bool
    adjustment_basis: str = Field(min_length=1)
    provenance: DataProvenance

    @model_validator(mode="after")
    def valid_bar(self):
        if self.closes_at <= self.opens_at or self.low > min(self.open, self.close) or self.high < max(self.open, self.close) or self.low > self.high:
            raise ValueError("invalid candle interval or OHLC envelope")
        if (self.volume is None) != (self.volume_type == "UNAVAILABLE"):
            raise ValueError("volume availability must be labelled correctly")
        if self.completed and self.provenance.observed_at < self.closes_at:
            raise ValueError("completed candle cannot be observed before its close")
        return self


class FXPairQuote(Contract):
    """An executable two-sided quote for one currency pair: 1 `base` = price `quote`.

    Conversion direction is derived from the pair, never from a caller-supplied
    rate, and the engine converts losses at the side that makes them larger
    (review CR-04). The provenance symbol must name the same pair.
    """
    base: Currency
    quote: Currency
    bid: Positive
    ask: Positive
    provenance: DataProvenance

    @model_validator(mode="after")
    def two_sided(self):
        if self.base == self.quote:
            raise ValueError("a pair needs two different currencies")
        if self.ask < self.bid:
            raise ValueError("crossed FX quote")
        return self


class BrokerCalculation(Contract):
    """The broker's own profit and margin figures for one hypothetical order.

    On MT5 these are `order_calc_profit(action, symbol, volume, price_open,
    price_close)` and `order_calc_margin(action, symbol, volume, price)`. The
    broker knows the true contract size and tick value, so its figure is
    independent evidence against a mis-mapped specification (review CR-03).
    Profit is linear in volume for LINEAR instruments, so any volume will do.
    """
    instrument_id: str = Field(min_length=1)
    side: Literal["BUY", "SELL"]
    volume: Positive
    entry: Decimal
    stop_loss: Decimal
    profit_at_stop: Decimal      # account currency; negative for a loss
    margin: NonNegative          # account currency, for `volume`
    currency: Currency
    provenance: DataProvenance

    @model_validator(mode="after")
    def losing_side(self):
        if (self.side == "BUY" and not self.stop_loss < self.entry) or \
           (self.side == "SELL" and not self.stop_loss > self.entry):
            raise ValueError("calculation stop must be on the losing side of entry")
        return self


class ResearchEvidence(Contract):
    evidence_id: str
    claim: str
    classification: Literal["OBSERVED", "VERIFIED_EXTERNAL_FACT", "MODEL_INTERPRETATION", "FORECAST", "UNVERIFIED", "MISSING"]
    provenance: DataProvenance


class EconomicEvent(Contract):
    event_id: str
    currency: Currency
    scheduled_at: datetime
    impact: Literal["LOW", "MEDIUM", "HIGH", "UNKNOWN"]
    actual: Decimal | None = None
    forecast: Decimal | None = None
    provenance: DataProvenance
    # Set for an event about one instrument only, such as an earnings release.
    instrument_id: str | None = None


class EconomicCalendar(Contract):
    """Every scheduled release in [covers_from, covers_until], from one source.

    An event absent from a calendar that covers its time is taken as not
    scheduled; that is why coverage is part of the evidence (review CR-08).
    """
    covers_from: datetime
    covers_until: datetime
    covers_earnings: bool = False
    events: tuple[EconomicEvent, ...]
    provenance: DataProvenance

    @model_validator(mode="after")
    def coverage(self):
        if self.covers_until <= self.covers_from:
            raise ValueError("invalid calendar coverage")
        if any(not self.covers_from <= e.scheduled_at <= self.covers_until for e in self.events):
            raise ValueError("calendar event outside its stated coverage")
        return self


class DailyActivity(Contract):
    """One account's activity over one policy-local day, from the broker's history.

    Server requests count every send, stop/target modification and cancellation;
    FTMO publishes its limits per day, not per minute (review CR-09).
    """
    account_id: str = Field(min_length=1)
    day: date
    positions_opened: int = Field(ge=0)
    server_requests: int = Field(ge=0)
    open_orders: int = Field(ge=0)
    requests_last_minute: int = Field(ge=0)
    observed_at: datetime
    complete: bool


class FundamentalObservation(Contract):
    metric: str
    value: Decimal
    units: str
    period_end: date
    available_at: datetime
    provenance: DataProvenance


class CorporateAction(Contract):
    action: Literal["SPLIT", "DIVIDEND", "RENAME", "DELISTING"]
    effective_at: datetime
    ratio_or_amount: Positive
    provenance: DataProvenance


class OrderBookSnapshot(Contract):
    bids: tuple[tuple[Decimal, Positive], ...]
    asks: tuple[tuple[Decimal, Positive], ...]
    provenance: DataProvenance


class MarketRegime(Contract):
    label: Literal["TREND", "RANGE", "HIGH_VOLATILITY", "UNKNOWN"]
    as_of: datetime
    evidence_ids: tuple[str, ...]


class IndicatorResult(Contract):
    name: str
    status: Literal["AVAILABLE", "WARMUP", "UNSUPPORTED", "UNAVAILABLE"]
    values: tuple[Decimal | None, ...] = ()
    evidence_ids: tuple[str, ...] = ()


class StrategyDefinition(Contract):
    strategy_id: str
    version: str
    family: str
    supported_asset_classes: tuple[AssetClass, ...]
    required_data: tuple[str, ...]
    required_indicators: tuple[str, ...]
    supported_timeframes: tuple[str, ...]
    supported_regimes: tuple[str, ...]
    parameter_schema_json: str = '{"type":"object","properties":{},"additionalProperties":false}'
    entry_conditions: tuple[str, ...]
    exit_conditions: tuple[str, ...]
    stop_loss_policy: str
    take_profit_policy: str
    max_holding_seconds: int = Field(gt=0)
    market_hours_constraints: str
    invalidation_conditions: tuple[str, ...]
    risk_compatibility: str
    evidence_requirements: tuple[str, ...]


class StrategyVersion(Contract):
    definition: StrategyDefinition
    parameter_json: str
    code_revision: str


class BacktestRun(Contract):
    run_id: str
    validation_status: Literal["BACKTESTED", "OUT_OF_SAMPLE_VALIDATED", "PAPER_TESTED", "BROKER_DEMO_TESTED", "UNVERIFIED"]
    dataset_hash: str
    strategy: StrategyVersion
    costs_json: str
    started_at: datetime
    completed_at: datetime | None = None


class CounterArgument(Contract):
    claim: str
    evidence_ids: tuple[str, ...]


class TradeHypothesis(Contract):
    hypothesis_id: str
    market_context: str
    asset_significance: str
    price_drivers: tuple[str, ...]
    supporting_evidence: tuple[ResearchEvidence, ...]
    contradicting_evidence: tuple[ResearchEvidence, ...]
    alternative_scenarios: tuple[str, ...]
    critical_assumptions: tuple[str, ...]
    missing_information: tuple[str, ...]
    probability_status: Literal["UNCALIBRATED", "CALIBRATED"] = "UNCALIBRATED"
    estimated_probability_if_calibrated: Annotated[Decimal, Field(ge=0, le=1)] | None = None
    calibration_evidence_id: str | None = None
    entry_thesis: str
    invalidation_conditions: tuple[str, ...]
    stop_loss_basis: str
    take_profit_basis: str
    planned_risk: NonNegative
    net_reward_risk: NonNegative | None
    cancellation_conditions: tuple[str, ...]
    reason_to_trade_instead_of_abstaining: str
    research_confidence: Literal["LOW", "MEDIUM", "HIGH", "UNVERIFIED"]
    decision_status: Literal["APPROVE_FOR_HUMAN_REVIEW", "ABSTAIN", "REJECT"]

    @model_validator(mode="after")
    def calibrated(self):
        if self.probability_status == "CALIBRATED":
            if self.estimated_probability_if_calibrated is None or not self.calibration_evidence_id:
                raise ValueError("calibration evidence required")
        elif self.estimated_probability_if_calibrated is not None:
            raise ValueError("uncalibrated probability must be absent")
        if self.decision_status == "APPROVE_FOR_HUMAN_REVIEW" and (not self.supporting_evidence or not self.alternative_scenarios):
            raise ValueError("review requires supporting evidence and alternative scenarios")
        return self


class TradeProposal(Contract):
    proposal_id: str
    account_id: str = Field(min_length=1)
    instrument_id: str = Field(min_length=1)
    side: Literal["BUY", "SELL"]
    order_type: Literal["MARKET", "LIMIT"]
    quantity: Positive
    entry: Decimal
    price_tolerance: NonNegative
    stop_loss: Decimal
    take_profit: Decimal
    expires_at: datetime
    rule_profile_hash: str
    market_snapshot_id: str
    hypothesis_id: str

    @model_validator(mode="after")
    def protective_prices(self):
        if self.side == "BUY" and not self.stop_loss < self.entry < self.take_profit:
            raise ValueError("buy requires stop < entry < target")
        if self.side == "SELL" and not self.take_profit < self.entry < self.stop_loss:
            raise ValueError("sell requires target < entry < stop")
        return self


class RiskAssessment(Contract):
    verdict: Literal["ALLOW", "DENY", "REQUIRE_REVIEW", "UNVERIFIED", "EMERGENCY_HALT"]
    reasons: tuple[str, ...]
    policy_hash: str
    proposal_hash: str
    assessed_at: datetime
    expires_at: datetime
    quantity: NonNegative = Decimal("0")
    monetary_risk: NonNegative = Decimal("0")


class PolicyEvaluation(Contract):
    status: Literal["PASS", "BREACH", "POLICY_UNVERIFIED"]
    reasons: tuple[str, ...]
    profile_hash: str
    daily_floor: Decimal | None = None
    overall_floor: Decimal | None = None


class ApprovalRequest(Contract):
    approval_id: str
    proposal: TradeProposal
    risk: RiskAssessment
    approved_by: str
    approved_at: datetime
    expires_at: datetime


class ExecutionIntent(Contract):
    intent_id: str
    idempotency_key: str
    approval_id: str
    proposal: TradeProposal
    created_at: datetime


class Order(Contract):
    intent_id: str
    account_id: str
    broker_order_id: str | None
    status: str
    observed_at: datetime


class Fill(Contract):
    fill_id: str
    account_id: str
    broker_order_id: str
    instrument_id: str
    quantity: Positive
    price: Decimal
    commission: NonNegative
    currency: Currency
    filled_at: datetime
    provenance: DataProvenance


class Position(Contract):
    position_id: str
    account_id: str
    instrument_id: str
    quantity: Decimal
    average_price: Decimal
    floating_pnl: Decimal
    currency: Currency
    observed_at: datetime


class AccountSnapshot(Contract):
    account_id: str
    environment: Literal["SIMULATION", "DEMO", "LIVE", "UNKNOWN"]
    currency: Currency
    balance: Decimal
    equity: Decimal
    available_margin: NonNegative
    observed_at: datetime
    positions_complete: bool
    orders_complete: bool


class BalanceSnapshot(Contract):
    account_id: str
    currency: Currency
    balance: Decimal
    equity: Decimal
    observed_at: datetime


class BrokerCapability(Contract):
    broker: str
    operation: str
    supported: bool
    environment: str
    reason: str


class PluginManifest(Contract):
    name: str
    version: str
    api_version: str
    entrypoint: str
    capabilities: tuple[str, ...]
    requested_permissions: tuple[str, ...]
    required_secret_names: tuple[str, ...]
    dependency_constraints: tuple[str, ...]
    resource_limits: tuple[str, ...]
    network_scope: tuple[str, ...]
    health_check: str
    environments: tuple[str, ...]


class AuditEvent(Contract):
    event_id: str
    correlation_id: str
    kind: str
    occurred_at: datetime
    payload_hash: str
    previous_hash: str


class ReconciliationResult(Contract):
    account_id: str
    intent_id: str
    status: Literal["MATCH", "MISMATCH", "UNKNOWN"]
    broker_order_ids: tuple[str, ...]
    observed_at: datetime
    reasons: tuple[str, ...]
