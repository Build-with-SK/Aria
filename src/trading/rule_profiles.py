"""Versioned provider rule calculations; no executable FTMO defaults.

Only the declared balance-at-local-midnight methodology is implemented. An
unrecognised methodology, missing confirmation or unsupported constraint fails
closed rather than borrowing another product's rules.
"""
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from pydantic import Field, ValidationError, model_validator

from .contracts import Contract, Currency, Positive, PolicyEvaluation, content_hash, revalidated

Fraction = Annotated[Decimal, Field(gt=0, le=1)]

# Closed vocabularies (review CR-12): FTMO is recognised by an enum value, never
# by matching free text such as "FTMO s.r.o." or a product spelling.
Provider = Literal["FTMO", "OTHER", "FIXTURE_NOT_FTMO"]
Product = Literal["FTMO_2_STEP", "FTMO_1_STEP", "FTMO_FREE_TRIAL", "OTHER", "TEST"]
Stage = Literal["TRIAL", "CHALLENGE", "VERIFICATION", "FUNDED", "NOT_APPLICABLE"]
AccountType = Literal["STANDARD", "SWING", "NOT_APPLICABLE"]
# What a loss on this account actually costs (review CR-07). A broker's "demo"
# label is not this: every FTMO account, paid or funded, is a demo account.
AccountClass = Literal["PRACTICE", "PAID_EVALUATION", "FUNDED", "LIVE", "UNVERIFIED"]

# Operational rules (review CR-08). Each value names exactly what is enforced;
# a provider rule that cannot be written as one of these stays UNVERIFIED.
NewsRule = Literal["NONE", "NO_EXECUTION_2MIN_AROUND_NEWS", "UNVERIFIED"]
BreakHoldRule = Literal["NONE", "NO_HOLD_THROUGH_BREAKS_OVER_2H", "UNVERIFIED"]
GapRule = Literal["NONE", "NO_ENTRY_2H_BEFORE_LONG_BREAK_OR_MAJOR_NEWS", "UNVERIFIED"]
OvernightRule = Literal["NONE", "UNVERIFIED"]
InstrumentRule = Literal["ALLOWLIST", "UNVERIFIED"]
PracticesRule = Literal["OWNER_REVIEWED_STRATEGY", "UNVERIFIED"]
ConsistencyRule = Literal["NONE", "BEST_DAY_POSITIVE_DAYS_50", "CONSISTENCY_NET_PROFIT_50", "UNVERIFIED"]

NEWS_WINDOW = timedelta(minutes=2)   # either side of a release
LONG_BREAK = timedelta(hours=2)      # a market break at least this long
PRE_BREAK = timedelta(hours=2)       # no new entry this close to a long break

FTMO_PRODUCTS = ("FTMO_2_STEP", "FTMO_1_STEP", "FTMO_FREE_TRIAL")
# Published FTMO maxima (2026-11-06): 2,000 positions a day, more than 2,000
# server requests a day is hyperactive, 200 orders open at a time. A profile may
# set lower figures, never higher.
FTMO_DAILY_LIMITS = {"max_positions_per_day": 2000, "max_server_requests_per_day": 2000,
                     "max_open_orders": 200}
FTMO_STAGE_CLASS = {"TRIAL": "PRACTICE", "CHALLENGE": "PAID_EVALUATION",
                    "VERIFICATION": "PAID_EVALUATION", "FUNDED": "FUNDED"}
FTMO_PRODUCT_STAGES = {"FTMO_FREE_TRIAL": ("TRIAL",),
                       "FTMO_2_STEP": ("CHALLENGE", "VERIFICATION", "FUNDED"),
                       "FTMO_1_STEP": ("CHALLENGE", "FUNDED")}


class RuleProfile(Contract):
    provider: Provider
    provider_entity: str = Field(min_length=1)
    jurisdiction: str = Field(min_length=1)
    product: Product
    account_id: str = Field(min_length=1)
    account_size: Positive
    account_currency: Currency
    evaluation_stage: Stage
    account_type: AccountType
    account_class: AccountClass = "UNVERIFIED"
    account_creation_date: date
    effective_from: datetime
    effective_until: datetime
    source_urls: tuple[str, ...]
    verified_at: datetime | None = None
    user_confirmed_at: datetime | None = None
    policy_version: str = Field(min_length=1)
    order_created_at: datetime | None = None
    last_account_merge_at: datetime | None = None
    reset_timezone: str = "Europe/Prague"
    reset_hour: int = Field(default=0, ge=0, le=23)
    daily_loss_fraction: Fraction
    overall_loss_fraction: Fraction
    drawdown_mode: Literal["STATIC", "TRAILING_EOD_BALANCE"]
    daily_method: Literal["MIDNIGHT_BALANCE_MINUS_INITIAL_ALLOWANCE", "UNVERIFIED"]
    profit_target_fraction: Fraction | None = None
    minimum_trading_days: int = Field(default=0, ge=0)
    consistency_rule: ConsistencyRule = "UNVERIFIED"
    overnight_rule: OvernightRule = "UNVERIFIED"
    weekend_rule: BreakHoldRule = "UNVERIFIED"
    news_rule: NewsRule = "UNVERIFIED"
    gap_rule: GapRule = "UNVERIFIED"
    instrument_rule: InstrumentRule = "UNVERIFIED"
    allowed_instruments: tuple[str, ...] = ()
    prohibited_practices_rule: PracticesRule = "UNVERIFIED"
    max_leverage: Positive
    max_positions_per_day: int = Field(gt=0)
    max_server_requests_per_day: int = Field(gt=0)
    max_open_orders: int = Field(gt=0)
    # Only when the provider publishes one; FTMO does not.
    max_server_requests_per_minute: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def valid_window(self):
        try:
            ZoneInfo(self.reset_timezone)
        except (KeyError, ValueError) as exc:
            raise ValueError("unknown rule timezone") from exc
        if self.effective_until <= self.effective_from:
            raise ValueError("invalid policy validity interval")
        if (self.provider == "FTMO") != (self.product in FTMO_PRODUCTS):
            raise ValueError("FTMO products belong to provider FTMO and only to it")
        if self.provider == "FTMO":
            if self.reset_timezone != "Europe/Prague" or self.reset_hour != 0:
                raise ValueError("FTMO boundary must use verified Prague midnight")
            if self.evaluation_stage not in FTMO_PRODUCT_STAGES[self.product]:
                raise ValueError(f"{self.product} has no {self.evaluation_stage} stage")
            if self.account_type == "NOT_APPLICABLE":
                raise ValueError("FTMO accounts are STANDARD or SWING")
            expected = FTMO_STAGE_CLASS[self.evaluation_stage]
            if self.account_class not in ("UNVERIFIED", expected):
                raise ValueError(f"an FTMO {self.evaluation_stage} account is {expected}, "
                                 f"not {self.account_class}")
            # News and break-holding restrictions bind the Standard FTMO Account
            # only; Swing and the evaluation stages are exempt. Gap trading is a
            # forbidden practice in every phase.
            standard_funded = self.evaluation_stage == "FUNDED" and self.account_type == "STANDARD"
            for name, value in (
                    ("news_rule", "NO_EXECUTION_2MIN_AROUND_NEWS" if standard_funded else "NONE"),
                    ("weekend_rule", "NO_HOLD_THROUGH_BREAKS_OVER_2H" if standard_funded else "NONE"),
                    ("gap_rule", "NO_ENTRY_2H_BEFORE_LONG_BREAK_OR_MAJOR_NEWS"),
                    ("overnight_rule", "NONE")):
                if getattr(self, name) not in ("UNVERIFIED", value):
                    raise ValueError(f"FTMO {self.account_type} {self.evaluation_stage}: {name} is {value}")
            for name, ceiling in FTMO_DAILY_LIMITS.items():
                if getattr(self, name) > ceiling:
                    raise ValueError(f"FTMO {name} cannot exceed {ceiling}")
        if self.instrument_rule == "ALLOWLIST" and not self.allowed_instruments:
            raise ValueError("an instrument allowlist needs at least one instrument")
        if any(not url.startswith("https://") for url in self.source_urls):
            raise ValueError("policy evidence requires HTTPS URLs")
        return self

    @property
    def profile_hash(self) -> str:
        return content_hash(self)

    def verification_reasons(self, now: datetime) -> tuple[str, ...]:
        reasons = []
        if not self.verified_at or not self.user_confirmed_at or not self.source_urls:
            reasons.append("POLICY_UNVERIFIED: source verification and human confirmation required")
        if self.verified_at and (self.verified_at > now or (self.user_confirmed_at and self.user_confirmed_at < self.verified_at)):
            reasons.append("POLICY_UNVERIFIED: confirmation must follow source verification")
        if self.user_confirmed_at and self.user_confirmed_at > now:
            reasons.append("POLICY_UNVERIFIED: future confirmation")
        if not self.effective_from <= now < self.effective_until:
            reasons.append("POLICY_UNVERIFIED: profile outside validity interval")
        if self.daily_method == "UNVERIFIED":
            reasons.append("POLICY_UNVERIFIED: daily snapshot methodology unknown")
        if self.account_class == "UNVERIFIED":
            reasons.append("POLICY_UNVERIFIED: account consequence class (PRACTICE/PAID_EVALUATION/FUNDED/LIVE)")
        if self.product=="FTMO_1_STEP":
            expected=ftmo_1step_consistency_version(order_created_at=self.order_created_at,
                                                   merged_at=self.last_account_merge_at)
            if expected=="UNVERIFIED" or expected!=self.consistency_rule:
                reasons.append("POLICY_UNVERIFIED: account creation/merge rule version unresolved")
        for name in ("consistency_rule", "overnight_rule", "weekend_rule", "news_rule", "gap_rule",
                     "instrument_rule", "prohibited_practices_rule"):
            if getattr(self, name) == "UNVERIFIED":
                reasons.append(f"POLICY_UNVERIFIED: {name}")
        return tuple(reasons)


class RuleState(Contract):
    account_id: str
    account_currency: Currency
    daily_balance: Positive
    # Exact policy-local day, captured by an authoritative account snapshot.
    daily_snapshot_at: datetime
    equity: Decimal
    balance_high_water: Positive
    high_water_observed_at: datetime
    cashflows_since_snapshot: Decimal = Decimal("0")
    balance_reset: bool = False


def rule_day(profile: RuleProfile, now: datetime) -> date:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("timezone-aware clock required")
    local = now.astimezone(ZoneInfo(profile.reset_timezone))
    # Wall-clock day arithmetic; adding 24 UTC hours is wrong across DST.
    return (local - timedelta(hours=profile.reset_hour)).date()


def order_stage_permitted(profile: RuleProfile) -> bool:
    """Only a PRACTICE account may enter any order-review stage in this release."""
    return profile.account_class == "PRACTICE"


def evaluate_profile(profile: RuleProfile, state: RuleState, *, now: datetime) -> PolicyEvaluation:
    try:
        profile, state = revalidated(profile), revalidated(state)
    except ValidationError:
        return PolicyEvaluation(status="POLICY_UNVERIFIED", reasons=("POLICY_UNVERIFIED: invalid profile or state contract",),
                                profile_hash=content_hash(profile.model_dump(mode="json")))
    day = rule_day(profile, now)
    reasons = list(profile.verification_reasons(now))
    if state.account_id != profile.account_id or state.account_currency != profile.account_currency:
        reasons.append("POLICY_UNVERIFIED: account identity/currency mismatch")
    boundary = datetime.combine(day, time(profile.reset_hour), ZoneInfo(profile.reset_timezone))
    if state.daily_snapshot_at != boundary:
        reasons.append("POLICY_UNVERIFIED: exact local reset snapshot missing")
    if state.cashflows_since_snapshot or state.balance_reset:
        reasons.append("POLICY_UNVERIFIED: withdrawal/deposit/reset needs a new verified baseline")
    if state.high_water_observed_at > now or state.balance_high_water < profile.account_size:
        reasons.append("POLICY_UNVERIFIED: invalid balance high-water state")
    if reasons:
        return PolicyEvaluation(status="POLICY_UNVERIFIED", reasons=tuple(reasons), profile_hash=profile.profile_hash)
    daily = state.daily_balance - profile.account_size * profile.daily_loss_fraction
    anchor = profile.account_size if profile.drawdown_mode == "STATIC" else state.balance_high_water
    overall = anchor - profile.account_size * profile.overall_loss_fraction
    breached = []
    # Conservatively reserve the exact boundary; do not place a trade at zero room.
    if state.equity <= daily:
        breached.append("DAILY_EQUITY_LIMIT")
    if state.equity <= overall:
        breached.append("OVERALL_EQUITY_LIMIT")
    return PolicyEvaluation(status="BREACH" if breached else "PASS", reasons=tuple(breached),
                            profile_hash=profile.profile_hash, daily_floor=daily, overall_floor=overall)


def ftmo_1step_consistency_version(*, order_created_at: datetime | None,
                                  merged_at: datetime | None = None) -> str:
    """Discovery selector from the Oct 8 official update, NOT profile approval.

    Publication specifies 09:00 GMT+3, distinct from Prague rule-day boundaries.
    Missing creation precision cannot be inferred from an account creation date.
    """
    from datetime import timezone
    cutover=datetime(2026,10,12,6,tzinfo=timezone.utc)
    for stamp in (order_created_at,merged_at):
        if stamp is not None and (stamp.tzinfo is None or stamp.utcoffset() is None):
            raise ValueError("aware account order/merge time required")
    if merged_at is not None and merged_at>=cutover:
        return "CONSISTENCY_NET_PROFIT_50"
    if order_created_at is None:
        return "UNVERIFIED"
    return "CONSISTENCY_NET_PROFIT_50" if order_created_at>=cutover else "BEST_DAY_POSITIVE_DAYS_50"


class TradingDayResult(Contract):
    day: date
    closed_net_pnl: Decimal
    positions_opened: int = Field(ge=0)


def evaluate_objectives(profile: RuleProfile, *, balance: Decimal,
                        days: tuple[TradingDayResult, ...], all_positions_closed: bool,
                        now: datetime) -> dict:
    """Eligibility research check; not a loss-rule verdict or trading mandate."""
    rule_day(profile,now)  # validate clock even with incomplete evidence
    try:
        profile=revalidated(profile)
    except ValidationError:
        return {"status":"POLICY_UNVERIFIED","reasons":["POLICY_UNVERIFIED: invalid profile contract"],
                "profile_hash":content_hash(profile.model_dump(mode="json"))}
    reasons=list(profile.verification_reasons(now))
    if len({d.day for d in days})!=len(days) or not balance.is_finite():
        reasons.append("POLICY_UNVERIFIED: invalid/duplicate trading-day data")
    if any(d.day>rule_day(profile,now) for d in days):
        reasons.append("POLICY_UNVERIFIED: future trading-day data")
    if reasons:
        return {"status":"POLICY_UNVERIFIED","reasons":reasons,"profile_hash":profile.profile_hash}
    unmet=[]
    if profile.profit_target_fraction is not None and balance<profile.account_size*(1+profile.profit_target_fraction):
        unmet.append("PROFIT_TARGET_NOT_MET")
    if not all_positions_closed:
        unmet.append("OPEN_POSITIONS_REMAIN")
    if sum(d.positions_opened>0 for d in days)<profile.minimum_trading_days:
        unmet.append("MINIMUM_TRADING_DAYS_NOT_MET")
    if profile.consistency_rule!="NONE":
        pnl=[d.closed_net_pnl for d in days]
        best=max([Decimal("0"),*pnl])
        denominator=sum((max(Decimal("0"),p) for p in pnl),Decimal("0")) if profile.consistency_rule=="BEST_DAY_POSITIVE_DAYS_50" else sum(pnl,Decimal("0"))
        if denominator<=0 or best>denominator*Decimal("0.5"):
            unmet.append("CONSISTENCY_NOT_MET; NOT_A_LOSS_RULE_BREACH")
    return {"status":"NOT_YET_ELIGIBLE" if unmet else "ELIGIBLE_FOR_HUMAN_REVIEW",
            "reasons":unmet,"profile_hash":profile.profile_hash,"execution_authorised":False}
