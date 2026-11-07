"""Credential-free financial, provider-rule and adversarial fixtures."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from src.trading.contracts import (AccountSnapshot, AssetClass, BookItem, BookSnapshot,
    BrokerCalculation, DailyActivity, DataProvenance, EconomicCalendar, FXPairQuote, Instrument,
    InstrumentSpecification, MarketQuote, TradeProposal, TradingSession, content_hash)
from src.trading.multiasset_risk import RiskInputs, assess, floor_volume
from src.trading.rule_profiles import RuleProfile, RuleState, evaluate_profile, rule_day

NOW = datetime(2026, 11, 6, 12, tzinfo=timezone.utc)  # a Friday
# Every fixture instrument a test may trade; the profile's allowlist.
FIXTURE_INSTRUMENTS = tuple(f"fixture:{s}" for s in
                            ("EURUSD", "GBPUSD", "EURGBP", "USDJPY", "XAUUSD", "US30"))


def calendar(events=(), *, covers_from=NOW - timedelta(days=1), covers_until=NOW + timedelta(days=2),
             retrieved_at=NOW - timedelta(hours=1), covers_earnings=False, quality="VALID"):
    provenance = DataProvenance(provider="fixture", instrument_id="calendar:economic",
        original_symbol="economic-calendar", observed_at=retrieved_at, retrieved_at=retrieved_at,
        data_type="economic_calendar", freshness="FRESH", quality=quality, permission="RESEARCH")
    return EconomicCalendar(covers_from=covers_from, covers_until=covers_until, events=tuple(events),
                            covers_earnings=covers_earnings, provenance=provenance)


def session(instrument_id="fixture:EURUSD", *, state="OPEN",
            closes_at=datetime(2026, 11, 6, 22, tzinfo=timezone.utc),
            next_opens_at=datetime(2026, 11, 8, 22, tzinfo=timezone.utc), retrieved_at=NOW):
    """FX week: closes Friday 22:00 UTC, reopens Sunday 22:00 UTC (a 48-hour break)."""
    provenance = DataProvenance(provider="fixture", instrument_id=instrument_id,
        original_symbol="session", observed_at=retrieved_at, retrieved_at=retrieved_at,
        data_type="trading_session", freshness="FRESH", quality="VALID", permission="RESEARCH")
    return TradingSession(instrument_id=instrument_id, state=state, opens_at=closes_at - timedelta(days=5),
                          closes_at=closes_at, calendar="fixture-fx", next_opens_at=next_opens_at,
                          provenance=provenance)


def book(*items, **changes):
    fields = dict(account_id="demo-1", observed_at=NOW, complete=True, items=tuple(items))
    return BookSnapshot(**{**fields, **changes})


def holding(symbol="GBPUSD", *, kind="POSITION", side="BUY", loss="10", notional="1000",
            base=None, quote="USD"):
    """A book item; `loss` is the broker's loss to the stop (None = no stop)."""
    return BookItem(instrument_id=f"fixture:{symbol}", kind=kind, side=side, volume="0.01",
                    base_currency=base or symbol[:3], quote_currency=quote,
                    stop_loss=None if loss is None else "1", loss_at_stop=loss, notional=notional)


def activity(**changes):
    fields = dict(account_id="demo-1", day="2026-11-06", positions_opened=0, server_requests=0,
                  open_orders=0, requests_last_minute=0, observed_at=NOW, complete=True)
    return DailyActivity(**{**fields, **changes})


def broker_calc(instrument_id, profit, margin, provenance, *, entry="1.10020",
                stop="1.09520", side="BUY", volume="1", currency="USD"):
    return BrokerCalculation(account_id="demo-1", instrument_id=instrument_id, side=side, volume=volume,
        entry=entry, stop_loss=stop, profit_at_stop=profit, margin=margin,
        currency=currency, provenance=provenance.model_copy(update={"data_type": "broker_calculation"}))


@pytest.fixture
def case(monkeypatch, tmp_path):
    # The risk engine reads the legacy kill switch itself (CR-11): start every
    # case released, on a private state file, whatever another test left behind.
    from src.execution import kill_switch
    monkeypatch.delenv(kill_switch.ENV_VAR, raising=False)
    monkeypatch.setattr(kill_switch, "STATE_FILE", tmp_path / "kill_switch.json")
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
        calculation_mode="LINEAR", provenance=provenance,
        commission_per_lot="10", commission_currency="USD")
    profile = RuleProfile(provider="FIXTURE_NOT_FTMO", provider_entity="fixture", jurisdiction="fixture",
        product="TEST", account_id="demo-1", account_size="10000", account_currency="USD",
        evaluation_stage="NOT_APPLICABLE", account_type="NOT_APPLICABLE", account_class="PRACTICE",
        account_creation_date="2026-11-01",
        effective_from=NOW-timedelta(days=1), effective_until=NOW+timedelta(days=1),
        source_urls=("https://example.invalid/fixture-only",), verified_at=NOW-timedelta(hours=2),
        user_confirmed_at=NOW-timedelta(hours=1), policy_version="test-1", daily_loss_fraction="0.05",
        overall_loss_fraction="0.10", drawdown_mode="STATIC",
        daily_method="MIDNIGHT_BALANCE_MINUS_INITIAL_ALLOWANCE", consistency_rule="NONE",
        overnight_rule="NONE", weekend_rule="NONE", news_rule="NONE", gap_rule="NONE",
        instrument_rule="ALLOWLIST", allowed_instruments=FIXTURE_INSTRUMENTS,
        prohibited_practices_rule="OWNER_REVIEWED_STRATEGY", max_leverage="10",
        max_positions_per_day=2000, max_server_requests_per_day=2000, max_open_orders=200,
        max_server_requests_per_minute=10)
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
        specification=spec, profile=profile, rule_state=state,
        # The broker's own figure for one lot, entry to stop: 0.005 x 100,000 = $500.
        broker_calculation=broker_calc(instrument.instrument_id, "-500", "1100.20", provenance),
        book=book(), activity=activity(), economic_calendar=calendar(), session=session(),
        slippage_allowance="0.5", financing_allowance="0")
    return proposal, inputs


def test_linear_lot_risk_and_costs(case):
    proposal, inputs = case
    risk = assess(proposal, inputs, now=NOW)
    assert risk.verdict == "ALLOW"
    # Stop risk 0.0051 x 100,000 x 0.05 = 25.50; spread 0.0002 x 100,000 x 0.05 = 1.00;
    # commission 10/lot x 0.05 = 0.50; slippage max(0.50 stated, 1 spread = 1.00) = 1.00.
    assert risk.monetary_risk == D("28.00")
    assert risk.quantity == D("0.05")
    assert risk.proposal_hash == content_hash(proposal)
    assert risk.expires_at == NOW + timedelta(seconds=30)


@pytest.mark.parametrize("raw,step,expected", [("0.0199","0.01","0.01"),("1.49","0.25","1.25"),("0.009","0.01","0"),("2","1","2")])
def test_volume_rounds_down(raw, step, expected):
    assert floor_volume(D(raw), D(step)) == D(expected)


@pytest.mark.parametrize("field,value,verdict", [
    ("session",session(state="CLOSED"),"DENY"), ("session",session(state="HALTED"),"DENY"),
    ("book",book(complete=False),"UNVERIFIED"),
    ("activity",activity(last_losing_close_at=NOW-timedelta(minutes=5)),"DENY"),
    ("book",book(holding("EURUSD")),"DENY"),                          # duplicate
    ("book",book(holding("EURUSD",kind="PENDING_ORDER",loss="200",side="SELL")),"DENY"),
    ("book",book(holding("AUDCAD",base="AUD",quote="CAD",loss="200")),"DENY"),  # aggregate
    ("book",book(holding("GBPUSD",loss="100")),"DENY"),               # correlated through USD
    ("activity",activity(requests_last_minute=2),"DENY"),
    ("book",book(*(holding(s,loss="1") for s in ("AUDCAD","NZDCHF","CADCHF","AUDNZD","CHFJPY"))),"DENY"),
])
def test_independent_refusals(case, field, value, verdict):
    p, inputs = case
    r = assess(p, inputs.model_copy(update={field:value}), now=NOW)
    assert r.verdict == verdict and r.reasons


# Midnight balance 10,000. Halt when (10,000 - equity) >= 2% of equity, i.e. equity <= 9,803.92.
@pytest.mark.parametrize("equity,verdict", [
    ("9900", "ALLOW"),           # loss 100 < 198; budget min(49.50, 198-100=98, ...) fits the $28 trade
    ("9804", "DENY"),            # loss 196 < 196.08, but only $0.08 of daily budget is left
    ("9803", "EMERGENCY_HALT"),  # loss 197 >= 196.06
    ("9520", "EMERGENCY_HALT"),  # review case E8
])
def test_internal_daily_stop_from_midnight_balance_and_equity(case, equity, verdict):
    """Today's loss is the 00:00 balance minus equity; no separate P&L input exists."""
    p, i = case
    eq = D(equity)
    i = i.model_copy(update={"account": i.account.model_copy(update={"equity": eq}),
                             "rule_state": i.rule_state.model_copy(update={"equity": eq})})
    assert assess(p, i, now=NOW).verdict == verdict


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


def usdjpy_quote(i, bid="149.25", ask="149.27", symbol="USDJPY"):
    prov=i.quote.provenance.model_copy(update={"instrument_id":"fixture:"+symbol,"original_symbol":symbol})
    return FXPairQuote(base="USD",quote="JPY",bid=bid,ask=ask,provenance=prov)


def test_cross_currency_requires_fresh_pair_quote(case):
    p,i=case
    # A JPY-quoted instrument on a USD account that is not itself USDJPY needs a USDJPY quote.
    instrument=i.specification.instrument.model_copy(update={"quote_currency":"JPY", "settlement_currency":"JPY"})
    spec=i.specification.model_copy(update={"instrument":instrument,"tick_value_currency":"JPY"})
    i=i.model_copy(update={"specification":spec,
        # broker's one-lot loss: 500 JPY at mid 149.26 = $3.3499
        "broker_calculation":i.broker_calculation.model_copy(update={"profit_at_stop":D("-3.35")})})
    assert assess(p,i,now=NOW).reasons == ("FX_PAIR_QUOTE_MISSING_OR_MISMATCHED",)
    r=assess(p,i.model_copy(update={"fx_quote":usdjpy_quote(i)}),now=NOW)
    # JPY -> USD divides by USDJPY; the bid (149.25) is the side that makes the loss larger.
    # Costs: spread 0.0002 x 100,000 x 0.05 = 1 JPY, converted the same way; commission
    # $0.50; slippage max($0.50 stated, one spread) = $0.50.
    expected = (D("0.0051")*100000*D("0.05") + 1)/D("149.25") + 1
    assert r.verdict=="ALLOW" and abs(r.monetary_risk - expected) < D("1e-20")
    for bad in (usdjpy_quote(i,symbol="JPYUSD"),
                usdjpy_quote(i).model_copy(update={"provenance":usdjpy_quote(i).provenance.model_copy(
                    update={"observed_at":NOW-timedelta(seconds=16),"retrieved_at":NOW-timedelta(seconds=16)})}),
                usdjpy_quote(i,bid="140",ask="150")):
        assert assess(p,i.model_copy(update={"fx_quote":bad}),now=NOW).verdict == "UNVERIFIED"


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


def snapshot_source(inputs):
    """Configured fixture transport; independent of the caller's queue inputs."""
    from src.trading.snapshot_source import BoundRiskSource
    return BoundRiskSource(account_id=inputs.account.account_id, provider_id=inputs.quote.provenance.provider,
                           profile=inputs.profile, reader=lambda proposal, now: inputs)
