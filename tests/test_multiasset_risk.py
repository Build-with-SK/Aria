"""Credential-free financial, provider-rule and adversarial fixtures."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from src.trading.contracts import (AccountSnapshot, AssetClass, DataProvenance,
    FXConversion, Instrument, InstrumentSpecification, MarketQuote, TradeProposal,
    content_hash)
from src.trading.multiasset_risk import RiskInputs, assess, floor_volume
from src.trading.rule_profiles import RuleProfile, RuleState, evaluate_profile, rule_day

NOW = datetime(2026, 11, 6, 12, tzinfo=timezone.utc)


@pytest.fixture
def case():
    provenance = DataProvenance(provider="fixture", instrument_id="fixture:EURUSD",
        original_symbol="EURUSD", observed_at=NOW, retrieved_at=NOW,
        data_type="quote", freshness="FRESH", quality="VALID", permission="EXECUTION")
    instrument = Instrument(instrument_id="fixture:EURUSD", provider_symbol="EURUSD",
        asset_class=AssetClass.FOREX, product="CFD", venue="FIXTURE_OTC",
        base_currency="EUR", quote_currency="USD", settlement_currency="USD", calendar="fixture")
    spec = InstrumentSpecification(instrument=instrument, contract_size="100000",
        tick_size="0.00001", tick_value="1", tick_value_currency="USD", volume_min="0.01",
        volume_max="10", volume_step="0.01", minimum_stop_distance="0.00010",
        margin_rate="0.01", supported_order_types=("MARKET",), quote_max_age_seconds=15,
        calculation_mode="LINEAR", provenance=provenance)
    profile = RuleProfile(provider="FIXTURE_NOT_FTMO", provider_entity="fixture", jurisdiction="fixture",
        product="TEST", account_id="demo-1", account_size="10000", account_currency="USD",
        evaluation_stage="fixture", account_type="demo", account_creation_date="2026-11-01",
        effective_from=NOW-timedelta(days=1), effective_until=NOW+timedelta(days=1),
        source_urls=("https://example.invalid/fixture-only",), verified_at=NOW-timedelta(hours=2),
        user_confirmed_at=NOW-timedelta(hours=1), policy_version="test-1", daily_loss_fraction="0.05",
        overall_loss_fraction="0.10", drawdown_mode="STATIC",
        daily_method="MIDNIGHT_BALANCE_MINUS_INITIAL_ALLOWANCE", consistency_rule="NONE",
        overnight_rule="fixture:none", weekend_rule="fixture:none", news_rule="fixture:none",
        instrument_rule="fixture:EURUSD", prohibited_practices_rule="fixture:checked",
        max_leverage="10", max_server_requests_per_minute=10)
    state = RuleState(account_id="demo-1", account_currency="USD", daily_balance="10000",
        daily_snapshot_at=datetime(2026,11,6,tzinfo=ZoneInfo("Europe/Prague")), equity="10000",
        balance_high_water="10000", high_water_observed_at=NOW)
    proposal = TradeProposal(proposal_id="p1", account_id="demo-1", instrument_id=instrument.instrument_id,
        side="BUY", order_type="MARKET", quantity="0.05", entry="1.10020", price_tolerance="0.00010",
        stop_loss="1.09520", take_profit="1.11020", expires_at=NOW+timedelta(minutes=1),
        rule_profile_hash=profile.profile_hash, market_snapshot_id="q1", hypothesis_id="h1")
    inputs = RiskInputs(account=AccountSnapshot(account_id="demo-1", environment="DEMO", currency="USD",
        balance="10000", equity="10000", available_margin="9000", observed_at=NOW,
        positions_complete=True, orders_complete=True),
        quote=MarketQuote(bid="1.10000", ask="1.10020", snapshot_id="q1", provenance=provenance),
        specification=spec, profile=profile, rule_state=state, existing_open_risk="0",
        pending_order_risk="0", correlated_risk="0", current_notional="0", daily_net_pnl="0",
        position_count=0, orders_last_minute=0, spread_cost="1", commission="0.5",
        slippage_allowance="0.5", financing_allowance="0", session_open=True,
        kill_switch_clear=True, operational_constraints_verified=True, book_risk_complete=True,
        cooldown_clear=True, duplicate_position=False, abnormal_market=False)
    return proposal, inputs


def test_linear_lot_risk_and_costs(case):
    proposal, inputs = case
    risk = assess(proposal, inputs, now=NOW)
    assert risk.verdict == "ALLOW"
    assert risk.monetary_risk == D("27.50")  # 0.0051 * 100000 * .05 + 2
    assert risk.quantity == D("0.05")
    assert risk.proposal_hash == content_hash(proposal)
    assert risk.expires_at == NOW + timedelta(seconds=30)


@pytest.mark.parametrize("raw,step,expected", [("0.0199","0.01","0.01"),("1.49","0.25","1.25"),("0.009","0.01","0"),("2","1","2")])
def test_volume_rounds_down(raw, step, expected):
    assert floor_volume(D(raw), D(step)) == D(expected)


@pytest.mark.parametrize("field,value,verdict", [
    ("kill_switch_clear",False,"EMERGENCY_HALT"), ("session_open",False,"DENY"),
    ("book_risk_complete",False,"UNVERIFIED"), ("operational_constraints_verified",False,"UNVERIFIED"),
    ("cooldown_clear",False,"DENY"), ("duplicate_position",True,"DENY"),
    ("abnormal_market",True,"DENY"), ("pending_order_risk",D("200"),"DENY"),
    ("existing_open_risk",D("200"),"DENY"), ("correlated_risk",D("100"),"DENY"),
    ("orders_last_minute",2,"DENY"), ("position_count",5,"DENY"),
    ("daily_net_pnl",D("-201"),"EMERGENCY_HALT"),
])
def test_independent_refusals(case, field, value, verdict):
    p, inputs = case
    r = assess(p, inputs.model_copy(update={field:value}), now=NOW)
    assert r.verdict == verdict and r.reasons


@pytest.mark.parametrize("field,value", [("environment","LIVE"),("environment","UNKNOWN"),
    ("currency","EUR"),("account_id","other"),("positions_complete",False),
    ("orders_complete",False),("observed_at",NOW-timedelta(seconds=16)),
    ("observed_at",NOW+timedelta(seconds=1))])
def test_account_fail_closed(case, field, value):
    p, i = case
    i = i.model_copy(update={"account":i.account.model_copy(update={field:value})})
    assert assess(p, i, now=NOW).verdict == "UNVERIFIED"


@pytest.mark.parametrize("field,value", [("permission","RESEARCH"),("quality","SUSPECT"),
    ("freshness","DELAYED"),("original_symbol","EURUSD=X"),
    ("instrument_id","other"),("observed_at",NOW-timedelta(seconds=16))])
def test_quote_fail_closed(case, field, value):
    p,i = case
    q=i.quote.model_copy(update={"provenance":i.quote.provenance.model_copy(update={field:value})})
    assert assess(p, i.model_copy(update={"quote":q}), now=NOW).verdict == "UNVERIFIED"


def test_cross_currency_requires_fresh_directional_conversion(case):
    p,i=case
    instrument=i.specification.instrument.model_copy(update={"quote_currency":"JPY", "settlement_currency":"JPY"})
    spec=i.specification.model_copy(update={"instrument":instrument,"tick_value_currency":"JPY"})
    i=i.model_copy(update={"specification":spec})
    assert assess(p,i,now=NOW).verdict == "UNVERIFIED"
    fx=FXConversion(from_currency="JPY",to_currency="USD",rate="0.0067",provenance=i.quote.provenance)
    assert assess(p,i.model_copy(update={"conversion":fx}),now=NOW).monetary_risk == D("2.17085")
    assert assess(p,i.model_copy(update={"conversion":fx.model_copy(update={"from_currency":"USD","to_currency":"JPY"})}),now=NOW).verdict == "UNVERIFIED"


@pytest.mark.parametrize("qty", ["0.001","0.051","11","0.50"])
def test_volume_or_risk_refuses(case,qty):
    p,i=case
    assert assess(p.model_copy(update={"quantity":D(qty)}),i,now=NOW).verdict == "DENY"


def test_unconfirmed_and_expired_profiles(case):
    p,i=case
    for changes in ({"user_confirmed_at":None},{"verified_at":None},
                    {"effective_until":NOW},{"news_rule":"UNVERIFIED"}):
        profile=i.profile.model_copy(update=changes)
        assert evaluate_profile(profile,i.rule_state,now=NOW).status == "POLICY_UNVERIFIED"
        assert profile.profile_hash != i.profile.profile_hash


@pytest.mark.parametrize("equity,status", [("9501","PASS"),("9500","BREACH"),("9499","BREACH"),("8999","BREACH")])
def test_floating_equity_and_exact_limit(case,equity,status):
    _,i=case
    state=i.rule_state.model_copy(update={"equity":D(equity)})
    r=evaluate_profile(i.profile,state,now=NOW)
    assert r.status == status
    assert r.daily_floor == D("9500") and r.overall_floor == D("9000")


def test_trailing_balance_and_withdrawal_reset_refusal(case):
    _,i=case
    profile=i.profile.model_copy(update={"drawdown_mode":"TRAILING_EOD_BALANCE"})
    state=i.rule_state.model_copy(update={"balance_high_water":D("11000")})
    assert evaluate_profile(profile,state,now=NOW).overall_floor == D("10000")
    for changed in ({"cashflows_since_snapshot":D("-100")},{"balance_reset":True}):
        assert evaluate_profile(profile,state.model_copy(update=changed),now=NOW).status == "POLICY_UNVERIFIED"


@pytest.mark.parametrize("utc,day", [
    ("2026-03-28T22:59:59+00:00","2026-03-28"),
    ("2026-03-28T23:00:00+00:00","2026-03-29"),
    ("2026-03-29T22:00:00+00:00","2026-03-30"),
    ("2026-10-24T22:00:00+00:00","2026-10-25"),
    ("2026-10-25T22:59:59+00:00","2026-10-25"),
    ("2026-10-25T23:00:00+00:00","2026-10-26"),
])
def test_prague_midnight_and_dst(case,utc,day):
    _,i=case
    assert str(rule_day(i.profile,datetime.fromisoformat(utc))) == day


def test_prior_day_snapshot_is_not_current(case):
    _,i=case
    state=i.rule_state.model_copy(update={"daily_snapshot_at":i.rule_state.daily_snapshot_at-timedelta(days=1)})
    assert evaluate_profile(i.profile,state,now=NOW).status == "POLICY_UNVERIFIED"


@pytest.mark.parametrize("bad", ["NaN","Infinity","-Infinity"])
def test_nonfinite_money_rejected(case,bad):
    p,_=case
    with pytest.raises(ValidationError):
        TradeProposal.model_validate({**p.model_dump(),"quantity":bad})


def test_naive_timestamp_rejected(case):
    p,_=case
    with pytest.raises(ValidationError):
        TradeProposal.model_validate({**p.model_dump(),"expires_at":datetime(2026,11,6)})
