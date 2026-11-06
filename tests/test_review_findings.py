"""Regression tests for docs/trading/CLAUDE_INDEPENDENT_REVIEW.md, one section per finding.

Each test rebuilds the review's reproduction case and asserts the corrected
behaviour. The original failing output at 7385130 is recorded in
docs/trading/claude_review_probes/probes_out.json.
"""
from datetime import timedelta
from decimal import Decimal as D
import json
import sqlite3

import pytest

import mt5_fakes
from test_multiasset_risk import activity, book, calendar, case, holding, NOW, session
from src.trading.contracts import AssetClass, EconomicEvent, RiskAssessment, content_hash
from src.trading.multiasset_risk import assess
from src.trading.mt5_readonly import MT5PermissionDenied, MT5ReadOnlyAdapter, MT5Unavailable
from src.trading.mt5_worker import MT5ReadClient
from src.trading.operational_rules import activity_checks, flatten_deadline, news_checks
from src.trading.review_store import ReviewRefused, ReviewStore
from src.trading.rule_profiles import RuleProfile, evaluate_profile
from pydantic import ValidationError


AUDIT_KEY = b"fixture-review-audit-key-32-bytes"  # held outside the database (SEC-01)


def clock():
    return NOW


def store_at(tmp_path, name="review.sqlite"):
    return ReviewStore(tmp_path / name, clock=clock, audit_key=AUDIT_KEY)


def forged_allow(proposal):
    """The probe R1 forgery: a hand-built ALLOW naming the proposal's hash."""
    return RiskAssessment(verdict="ALLOW", reasons=("forged",), policy_hash="x" * 64,
        proposal_hash=content_hash(proposal), assessed_at=NOW,
        expires_at=NOW + timedelta(seconds=30), quantity=proposal.quantity, monetary_risk="1")


# --- CR-02: the approval store recomputes risk; it never trusts a caller verdict ---

def test_cr02_forged_allow_for_a_denied_trade_is_refused_by_queue(tmp_path, case):
    p, i = case
    # Probe R1: 5 lots EURUSD with a 50-pip stop on a $10k account (~$2,550 at risk).
    big = p.model_copy(update={"quantity": D("5.00")})
    assert assess(big, i, now=NOW).verdict == "DENY"
    store = store_at(tmp_path)
    with pytest.raises(ReviewRefused, match="caller-built assessment"):
        store.queue(big, forged_allow(big), now=NOW)
    with pytest.raises(ReviewRefused, match="verdict DENY"):
        store.queue(big, i, now=NOW)
    assert store.inspect(big.proposal_id) is None


def test_cr02_forged_allow_is_refused_by_reserve_intent(tmp_path, case):
    p, i = case
    store = store_at(tmp_path)
    store.queue(p, i, now=NOW)
    token = store.approve(p.proposal_id, authenticated_owner="fixture-owner", now=NOW)
    with pytest.raises(ReviewRefused, match="caller-built assessment"):
        store.reserve_intent(p, token, forged_allow(p), now=NOW)
    assert store.inspect(p.proposal_id)["state"] == "APPROVED"
    assert len(store.reserve_intent(p, token, i, now=NOW)) == 64


@pytest.mark.parametrize("change,why", [
    ("kill_switch", "EMERGENCY_HALT"),
    ("commission", "authorised risk changed"),
])
def test_cr02_reservation_reassesses_fresh_evidence(tmp_path, case, monkeypatch, change, why):
    p, i = case
    store = store_at(tmp_path)
    store.queue(p, i, now=NOW)
    token = store.approve(p.proposal_id, authenticated_owner="fixture-owner", now=NOW)
    fresh = i
    if change == "kill_switch":
        monkeypatch.setenv("ARIA_KILL_SWITCH", "1")
    else:  # $100 a lot instead of $10: $4.50 more risk than was approved
        fresh = i.model_copy(update={"specification": i.specification.model_copy(
            update={"commission_per_lot": D("100")})})
    with pytest.raises(ReviewRefused, match=why):
        store.reserve_intent(p, token, fresh, now=NOW)
    # A refused reservation consumes nothing.
    assert store.inspect(p.proposal_id)["state"] == "APPROVED"


def test_cr02_caller_clock_must_agree_with_store_clock(tmp_path, case):
    p, i = case
    store = ReviewStore(tmp_path / "review.sqlite", clock=lambda: NOW + timedelta(hours=1))
    with pytest.raises(ReviewRefused, match="clock"):
        store.queue(p, i, now=NOW)


def test_cr02_store_assesses_under_its_own_policy_only(tmp_path, case):
    p, i = case
    store = store_at(tmp_path)
    with pytest.raises(TypeError):
        store.queue(p, i, now=NOW, policy=None)  # no caller-chosen policy parameter
    risk = store.queue(p, i, now=NOW)
    assert risk.policy_hash == assess(p, i, now=NOW, policy=ReviewStore.POLICY).policy_hash


def test_cr02_tampered_stored_assessment_is_refused(tmp_path, case):
    p, i = case
    store = store_at(tmp_path)
    store.queue(p, i, now=NOW)
    with sqlite3.connect(str(store.path)) as db:
        raw = json.loads(db.execute("SELECT risk FROM proposals WHERE id=?", (p.proposal_id,)).fetchone()[0])
        raw["monetary_risk"] = "999999"
        db.execute("UPDATE proposals SET risk=? WHERE id=?", (json.dumps(raw), p.proposal_id))
    with pytest.raises(ReviewRefused, match="audit record"):
        store.approve(p.proposal_id, authenticated_owner="fixture-owner", now=NOW)


# --- shared: the review's E7/E8 shape (0.10 lot EURUSD, 20-pip stop, no costs => $20 risk) ---

def twenty_dollar_trade(case, *, equity, daily_balance, high_water="10000"):
    """No costs: a zero-spread quote, no commission, no stated slippage."""
    p, i = case
    p = p.model_copy(update={"quantity": D("0.10"), "price_tolerance": D("0"),
                             "stop_loss": D("1.09820"), "take_profit": D("1.10420")})
    eq, mid = D(equity), D(daily_balance)
    i = i.model_copy(update={
        "account": i.account.model_copy(update={"equity": eq, "balance": eq}),
        "rule_state": i.rule_state.model_copy(update={"equity": eq, "daily_balance": mid,
                                                      "balance_high_water": D(high_water)}),
        "quote": i.quote.model_copy(update={"bid": D("1.10020")}),
        "specification": i.specification.model_copy(update={"commission_per_lot": D("0")}),
        "slippage_allowance": D("0")})
    return p, i


# --- CR-05: today's loss comes from the midnight balance and equity, not a caller field ---

def test_cr05_floating_loss_carried_over_midnight_halts(case):
    # E8: 00:00 balance 10,000, equity 9,520 (a -$480 floating loss carried over midnight).
    # 480 >= 2% of 9,520 => internal daily stop. At 7385130 this was ALLOWED because a
    # separate "today's P&L" field said 0.
    p, i = twenty_dollar_trade(case, equity="9520", daily_balance="10000")
    r = assess(p, i, now=NOW)
    assert r.verdict == "EMERGENCY_HALT" and r.reasons == ("INTERNAL_DAILY_STOP",)


def test_cr05_no_caller_daily_pnl_field_exists():
    from src.trading.multiasset_risk import RiskInputs
    assert "daily_net_pnl" not in RiskInputs.model_fields


# --- CR-10: a buffer above the provider floor, and risk == budget is refused ---

def test_cr10_trade_sized_to_land_on_the_floor_is_denied(case):
    # E7: equity 9,020, static floor 9,000, $20 risk => equity at the stop = 9,000.00 exactly.
    p, i = twenty_dollar_trade(case, equity="9020", daily_balance="9020")
    r = assess(p, i, now=NOW)
    assert r.verdict == "DENY" and "WITHIN_FLOOR_BUFFER" in r.reasons


def test_cr10_risk_equal_to_budget_is_denied(case):
    # Floor 9,000 + $50 buffer (0.5% of 10,000) leaves exactly $20 of room at equity 9,070.
    p, i = twenty_dollar_trade(case, equity="9070", daily_balance="9070")
    r = assess(p, i, now=NOW)
    assert r.verdict == "DENY" and "RISK_BUDGET_EXCEEDED" in r.reasons


def test_cr10_one_dollar_inside_the_buffered_room_is_allowed(case):
    p, i = twenty_dollar_trade(case, equity="9071", daily_balance="9071")
    assert assess(p, i, now=NOW).verdict == "ALLOW"


def test_cr10_buffer_scales_with_slippage_allowance(case):
    # 3 x $30 slippage = $90 buffer > $50: equity 9,071 is now inside the buffer.
    p, i = twenty_dollar_trade(case, equity="9071", daily_balance="9071")
    i = i.model_copy(update={"slippage_allowance": D("30")})
    assert "WITHIN_FLOOR_BUFFER" in assess(p, i, now=NOW).reasons


# --- CR-03: contract size is checked against the broker's own calculation ---

def metal_or_index_case(case, *, symbol, asset_class, base, contract, quantity,
                        bid, ask, stop, target, broker_one_lot_loss):
    """A USD-quoted instrument whose broker says one lot entry->stop loses `broker_one_lot_loss`."""
    from src.trading.contracts import Instrument, InstrumentSpecification, MarketQuote
    from test_multiasset_risk import broker_calc
    p, i = case
    prov = i.quote.provenance.model_copy(update={"instrument_id": f"fixture:{symbol}",
                                                 "original_symbol": symbol})
    instrument = Instrument(instrument_id=f"fixture:{symbol}", provider_symbol=symbol,
        asset_class=asset_class, product="CFD", venue="FIXTURE_OTC", base_currency=base,
        quote_currency="USD", settlement_currency="USD", calendar="fixture")
    spec = i.specification.model_copy(update={
        "instrument": instrument, "contract_size": D(contract), "tick_size": D("0.01"),
        "tick_value": D(contract) * D("0.01"), "minimum_stop_distance": D("0.01"),
        "volume_max": D("100"), "provenance": prov})
    p = p.model_copy(update={"instrument_id": instrument.instrument_id, "quantity": D(quantity),
        "entry": D(ask), "price_tolerance": D("0"), "stop_loss": D(stop), "take_profit": D(target)})
    i = i.model_copy(update={"specification": spec, "session": session(instrument.instrument_id),
        "quote": MarketQuote(bid=bid, ask=ask, snapshot_id="q1", provenance=prov),
        "broker_calculation": broker_calc(instrument.instrument_id, f"-{broker_one_lot_loss}", "0",
                                          prov, entry=ask, stop=stop)})
    return p, i


def test_cr03_gold_with_wrong_contract_size_is_unverified(case):
    # E5b: true contract 100 oz, mapped as 1. Ten lots with a $5 stop look like $50;
    # the true loss is $5,000 on $2.6M notional. At 7385130 this was ALLOWED.
    p, i = metal_or_index_case(case, symbol="XAUUSD", asset_class=AssetClass.METALS, base="XAU",
        contract="1", quantity="10", bid="2600.00", ask="2600.00", stop="2595.00", target="2610.00",
        broker_one_lot_loss="500")
    r = assess(p, i, now=NOW)
    assert r.verdict == "UNVERIFIED"
    assert "CONTRACT_SPEC_DISAGREES_WITH_BROKER" in r.reasons
    assert "CONTRACT_SIZE_OUTSIDE_REVIEWED_REGISTRY" in r.reasons


def test_cr03_unregistered_instrument_is_caught_by_the_broker_figure_alone(case):
    # An index CFD has no conventional size, so only the broker cross-check applies:
    # mapped as 1 per point, the broker says 10 per point.
    p, i = metal_or_index_case(case, symbol="US30", asset_class=AssetClass.INDICES, base="USD",
        contract="1", quantity="0.10", bid="42000.00", ask="42000.00", stop="41950.00",
        target="42100.00", broker_one_lot_loss="500")
    r = assess(p, i, now=NOW)
    assert r.verdict == "UNVERIFIED" and r.reasons == ("CONTRACT_SPEC_DISAGREES_WITH_BROKER",)


def test_cr03_correct_gold_specification_is_allowed(case):
    # 100 oz, 0.05 lot, $5 stop: $25. Costs: spread 0.20 x 100 x 0.05 = $1, commission
    # $0.50, slippage one spread = $1. Notional $13,000 (1.3x).
    p, i = metal_or_index_case(case, symbol="XAUUSD", asset_class=AssetClass.METALS, base="XAU",
        contract="100", quantity="0.05", bid="2600.00", ask="2600.20", stop="2595.20",
        target="2610.20", broker_one_lot_loss="500")
    r = assess(p, i, now=NOW)
    assert r.verdict == "ALLOW" and r.monetary_risk == D("27.50")


def test_cr03_broker_calculation_is_mandatory_and_must_be_fresh(case):
    p, i = case
    assert assess(p, i.model_copy(update={"broker_calculation": None}), now=NOW).reasons == (
        "BROKER_CALCULATION_REQUIRED",)
    stale = i.broker_calculation.model_copy(update={"provenance": i.broker_calculation.provenance.model_copy(
        update={"observed_at": NOW - timedelta(seconds=16), "retrieved_at": NOW - timedelta(seconds=16)})})
    other = i.broker_calculation.model_copy(update={"instrument_id": "fixture:GBPUSD"})
    for bad in (stale, other):
        assert assess(p, i.model_copy(update={"broker_calculation": bad}), now=NOW).reasons == (
            "BROKER_CALCULATION_UNVERIFIED",)


def test_cr03_small_disagreement_within_tolerance_is_accepted(case):
    p, i = case
    near = i.broker_calculation.model_copy(update={"profit_at_stop": D("-502.40")})  # 0.48%
    far = i.broker_calculation.model_copy(update={"profit_at_stop": D("-502.60")})   # 0.52%
    assert assess(p, i.model_copy(update={"broker_calculation": near}), now=NOW).verdict == "ALLOW"
    assert assess(p, i.model_copy(update={"broker_calculation": far}), now=NOW).verdict == "UNVERIFIED"


def test_cr03_lot_cap_backstop_by_asset_class(case):
    from src.trading.multiasset_risk import ResearchRiskPolicy
    policy = ResearchRiskPolicy()
    assert policy.lot_cap(AssetClass.METALS, D("10000")) == D("0.2")
    assert policy.lot_cap(AssetClass.FOREX, D("25000")) == D("2.5")
    assert policy.lot_cap(AssetClass.OTHER, D("10000")) == D("0")
    p, i = case
    r = assess(p.model_copy(update={"quantity": D("1.10")}), i, now=NOW)
    assert r.verdict == "DENY" and "ASSET_CLASS_LOT_CAP" in r.reasons


def test_cr03_broker_margin_is_used_when_larger(case):
    p, i = case
    # Our estimate: 1.1003 x 100,000 x 0.05 x 1% = $55; broker says $9,000 per lot => $450.
    heavy = i.broker_calculation.model_copy(update={"margin": D("9000")})
    tight = i.account.model_copy(update={"available_margin": D("500")})
    r = assess(p, i.model_copy(update={"broker_calculation": heavy, "account": tight}), now=NOW)
    assert r.verdict == "DENY" and "INSUFFICIENT_MARGIN" in r.reasons


# --- CR-04 / CR-13: conversion from a named pair quote, at the side that hurts ---

def fx_pair_case(case, *, symbol, base, quote, bid, ask, side, quantity, entry, stop, target,
                 broker_one_lot_profit, fx_quote=None):
    from src.trading.contracts import Instrument, MarketQuote
    from test_multiasset_risk import broker_calc
    p, i = case
    prov = i.quote.provenance.model_copy(update={"instrument_id": f"fixture:{symbol}",
                                                 "original_symbol": symbol})
    instrument = Instrument(instrument_id=f"fixture:{symbol}", provider_symbol=symbol,
        asset_class=AssetClass.FOREX, product="CFD", venue="FIXTURE_OTC", base_currency=base,
        quote_currency=quote, settlement_currency=quote, calendar="fixture")
    tick = D("0.001") if quote == "JPY" else D("0.00001")
    spec = i.specification.model_copy(update={"instrument": instrument, "tick_size": tick,
        "tick_value": D("100000") * tick, "tick_value_currency": quote, "provenance": prov})
    p = p.model_copy(update={"instrument_id": instrument.instrument_id, "side": side,
        "quantity": D(quantity), "entry": D(entry), "price_tolerance": D("0"),
        "stop_loss": D(stop), "take_profit": D(target)})
    i = i.model_copy(update={"specification": spec, "fx_quote": fx_quote,
        "session": session(instrument.instrument_id),
        "quote": MarketQuote(bid=bid, ask=ask, snapshot_id="q1", provenance=prov),
        "broker_calculation": broker_calc(instrument.instrument_id, broker_one_lot_profit, "0", prov,
                                          entry=entry, stop=stop, side=side)})
    return p, i


def gbpusd(case, bid, ask):
    from src.trading.contracts import FXPairQuote
    prov = case[1].quote.provenance.model_copy(update={"instrument_id": "fixture:GBPUSD",
                                                       "original_symbol": "GBPUSD"})
    return FXPairQuote(base="GBP", quote="USD", bid=bid, ask=ask, provenance=prov)


def eurgbp(case, fx, quantity):
    # One lot from 0.85000 to 0.84750 loses 250 GBP; the broker, at GBPUSD 1.2701, says $317.53.
    return fx_pair_case(case, symbol="EURGBP", base="EUR", quote="GBP", bid="0.85000",
        ask="0.85000", side="BUY", quantity=quantity, entry="0.85000", stop="0.84750",
        target="0.85500", broker_one_lot_profit="-317.53", fx_quote=fx)


def test_cr04_inverted_rate_with_correct_labels_is_unverified(case):
    # E3b: GBP->USD supplied as 0.7874 (the USDGBP value) under GBPUSD labels.
    # At 7385130 this ALLOWED $39.37 where the true risk is $63.50 (-38%).
    p, i = eurgbp(case, gbpusd(case, "0.7874", "0.7874"), "0.20")
    r = assess(p, i, now=NOW)
    assert r.verdict == "UNVERIFIED" and "CONTRACT_SPEC_DISAGREES_WITH_BROKER" in r.reasons


def test_cr04_correct_pair_uses_the_conservative_side(case):
    p, i = eurgbp(case, gbpusd(case, "1.2700", "1.2702"), "0.10")
    r = assess(p, i, now=NOW)
    # GBP -> USD multiplies by GBPUSD: the ask is the larger loss. 25 GBP x 1.2702, plus
    # costs: zero spread, commission $1.00, slippage $0.50 as stated.
    assert r.verdict == "ALLOW" and r.monetary_risk == D("0.0025") * 100000 * D("0.10") * D("1.2702") + D("1.5")


def test_cr04_no_caller_rate_input_exists():
    from src.trading.multiasset_risk import RiskInputs
    assert "conversion" not in RiskInputs.model_fields and "fx_quote" in RiskInputs.model_fields


def usdjpy(case, side, entry, stop, target, broker_profit):
    return fx_pair_case(case, symbol="USDJPY", base="USD", quote="JPY", bid="150.000",
        ask="150.010", side=side, quantity="0.04", entry=entry, stop=stop, target=target,
        broker_one_lot_profit=broker_profit)


def test_cr13_base_is_account_currency_converts_at_the_stop(case):
    # USDJPY on a USD account, BUY 0.04 lot, 1.5-yen stop: 6,000 JPY. The loss is
    # realised at USDJPY 148.51, not today's 150.00: $40.40, not $40.00. Costs: a
    # 40-yen spread converted the same way, commission $0.40, slippage $0.50 stated.
    p, i = usdjpy(case, "BUY", "150.010", "148.510", "151.510", "-999.97")
    r = assess(p, i, now=NOW)
    assert r.verdict == "ALLOW"
    assert abs(r.monetary_risk - (D("6040") / D("148.510") + D("0.9"))) < D("1e-20")
    assert r.monetary_risk > D("6040") / D("150.000") + D("0.9")


def test_cr13_sell_side_uses_todays_bid_when_it_is_lower(case):
    p, i = usdjpy(case, "SELL", "150.000", "151.500", "148.500", "-999.97")
    r = assess(p, i, now=NOW)
    assert r.verdict == "ALLOW" and abs(r.monetary_risk - (D("6040") / D("150.000") + D("0.9"))) < D("1e-20")


# --- CR-01: the trailing high-water mark is per account and only ever rises ---

def _trailing_profile(case, **changes):
    from datetime import datetime as _dt, timezone as _tz
    _, i = case
    base = dict(drawdown_mode="TRAILING_EOD_BALANCE", account_creation_date=_dt(2026, 11, 1).date(),
                effective_from=_dt(2026, 10, 1, tzinfo=_tz.utc), effective_until=_dt(2026, 12, 31, tzinfo=_tz.utc),
                verified_at=_dt(2026, 10, 30, tzinfo=_tz.utc), user_confirmed_at=_dt(2026, 10, 31, tzinfo=_tz.utc))
    base.update(changes)
    return i.profile.model_copy(update=base)


def _midnight(day):
    from datetime import datetime as _dt
    from zoneinfo import ZoneInfo
    return _dt.combine(day, _dt.min.time(), ZoneInfo("Europe/Prague"))


def _record(store, profile, day, balance, now=NOW):
    from src.trading.contracts import BalanceSnapshot
    snap = BalanceSnapshot(account_id=profile.account_id, currency="USD", balance=balance,
                           equity=balance, observed_at=_midnight(day))
    store.record_rule_boundary(profile, snap, evidence_hash="a" * 64, now=now, cashflow=D("0"))


def test_cr01_reconfirmed_profile_cannot_lower_the_high_water(tmp_path, case):
    # Probe R2: lineage 1 reaches 10,800 (floor 9,800). Re-confirming the profile at
    # 7385130 started a fresh lineage that re-seeded 10,000 (floor 9,000).
    from datetime import date
    from src.trading.rule_profiles import evaluate_profile
    store = store_at(tmp_path, "rules.sqlite")
    v1 = _trailing_profile(case)
    for n, balance in enumerate(["10000", "10400", "10800", "10600", "10500"]):
        _record(store, v1, date(2026, 11, 1) + timedelta(days=n), balance)
    v2 = _trailing_profile(case, user_confirmed_at=v1.user_confirmed_at + timedelta(minutes=30))
    assert v2.profile_hash != v1.profile_hash
    with pytest.raises(ReviewRefused, match="duplicate, reordered or missing"):
        _record(store, v2, date(2026, 11, 1), "10000")  # the re-seed attempt
    _record(store, v2, date(2026, 11, 6), "10450")      # v2 continues the same account history
    for profile in (v1, v2):
        state = store.rule_state(profile, equity=D("10450"), now=NOW)
        assert state.balance_high_water == D("10800")
        assert evaluate_profile(profile, state, now=NOW).overall_floor == D("9800")


def test_cr01_profile_cannot_redefine_the_account_start(tmp_path, case):
    from datetime import date
    store = store_at(tmp_path, "rules.sqlite")
    v1 = _trailing_profile(case)
    _record(store, v1, date(2026, 11, 1), "10000")
    smaller = _trailing_profile(case, account_size=D("9000"))
    with pytest.raises(ReviewRefused, match="recorded start"):
        _record(store, smaller, date(2026, 11, 2), "10100")


def test_cr01_high_water_is_the_running_maximum_every_day(tmp_path, case):
    from datetime import date, datetime as _dt, timezone as _tz
    balances = ["10000", "10300", "10100", "10600", "10200", "10200", "10700", "9900"]
    clock_now = [None]
    store = ReviewStore(tmp_path / "rules.sqlite", clock=lambda: clock_now[0])
    profile = _trailing_profile(case)
    running = D("0")
    for n, balance in enumerate(balances):
        day = date(2026, 11, 1) + timedelta(days=n)
        clock_now[0] = _dt.combine(day, _dt.min.time(), _tz.utc) + timedelta(hours=12)
        _record(store, profile, day, balance, now=clock_now[0])
        running = max(running, D(balance))
        state = store.rule_state(profile, equity=D(balance), now=clock_now[0])
        assert state.balance_high_water == running


def test_cr01_schema_v1_boundaries_are_set_aside_not_trusted(tmp_path, case):
    path = tmp_path / "old.sqlite"
    with sqlite3.connect(str(path)) as db:
        db.executescript("""
            CREATE TABLE rule_boundaries (account_id TEXT NOT NULL, profile_hash TEXT NOT NULL,
                day TEXT NOT NULL, snapshot_at TEXT NOT NULL, balance TEXT NOT NULL,
                high_water TEXT NOT NULL, evidence_hash TEXT NOT NULL,
                PRIMARY KEY(account_id,profile_hash,day));
            INSERT INTO rule_boundaries VALUES('demo-1','p','2026-11-06','2026-11-05T23:00:00+00:00','10000','10000','x');
            PRAGMA user_version=1;""")
    store = ReviewStore(path, clock=clock)
    with pytest.raises(ReviewRefused, match="boundary missing"):
        store.rule_state(case[1].profile, equity=D("10000"), now=NOW)
    with sqlite3.connect(str(path)) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 3
        assert db.execute("SELECT COUNT(*) FROM rule_boundaries_v1_unmigrated").fetchone()[0] == 1


# --- CR-07: what a loss costs decides the account class, not the server's DEMO label ---

def ftmo(case, **changes):
    """A validated FTMO profile on the fixture account (a paid 2-Step Challenge by default)."""
    fields = {**case[1].profile.model_dump(), "provider": "FTMO", "provider_entity": "FTMO s.r.o.",
              "product": "FTMO_2_STEP", "evaluation_stage": "CHALLENGE", "account_type": "STANDARD",
              "account_class": "PAID_EVALUATION",
              "gap_rule": "NO_ENTRY_2H_BEFORE_LONG_BREAK_OR_MAJOR_NEWS"}
    fields.update(changes)
    if fields["evaluation_stage"] == "FUNDED" and fields["account_type"] == "STANDARD":
        for rule, value in (("news_rule", "NO_EXECUTION_2MIN_AROUND_NEWS"),
                            ("weekend_rule", "NO_HOLD_THROUGH_BREAKS_OVER_2H")):
            if rule not in changes:
                fields[rule] = value
    return RuleProfile.model_validate(fields)


def on_profile(case, profile):
    p, i = case
    return (p.model_copy(update={"rule_profile_hash": profile.profile_hash}),
            i.model_copy(update={"profile": profile}))


@pytest.mark.parametrize("stage,klass", [("CHALLENGE", "PAID_EVALUATION"), ("VERIFICATION", "PAID_EVALUATION"),
                                         ("FUNDED", "FUNDED")])
def test_cr07_paid_or_funded_ftmo_account_on_a_demo_server_cannot_be_queued(tmp_path, case, stage, klass):
    p, i = on_profile(case, ftmo(case, evaluation_stage=stage, account_class=klass))
    assert i.account.environment == "DEMO"  # FTMO serves every account from demo servers
    assert assess(p, i, now=NOW).verdict == "ALLOW"  # research sizing still works
    store = store_at(tmp_path)
    with pytest.raises(ReviewRefused, match=f"PRACTICE-only.*{klass}"):
        store.queue(p, i, now=NOW)
    assert store.inspect(p.proposal_id) is None


@pytest.mark.parametrize("product,stage,klass", [
    ("FTMO_2_STEP", "CHALLENGE", "PRACTICE"), ("FTMO_2_STEP", "FUNDED", "PRACTICE"),
    ("FTMO_1_STEP", "FUNDED", "PAID_EVALUATION"), ("FTMO_FREE_TRIAL", "TRIAL", "FUNDED"),
    ("FTMO_1_STEP", "VERIFICATION", "PAID_EVALUATION"),  # 1-Step has no Verification
])
def test_cr07_ftmo_stage_fixes_the_class(case, product, stage, klass):
    with pytest.raises(ValidationError):
        ftmo(case, product=product, evaluation_stage=stage, account_class=klass)


def test_cr07_ftmo_free_trial_is_practice_and_may_be_queued(tmp_path, case):
    p, i = on_profile(case, ftmo(case, product="FTMO_FREE_TRIAL", evaluation_stage="TRIAL",
                                 account_class="PRACTICE"))
    store_at(tmp_path).queue(p, i, now=NOW)


def test_cr07_live_class_is_disabled_whatever_the_server_says(case):
    p, i = on_profile(case, case[1].profile.model_copy(update={"account_class": "LIVE"}))
    r = assess(p, i, now=NOW)
    assert r.verdict == "UNVERIFIED" and "LIVE_ACCOUNT_CLASS_DISABLED" in r.reasons


def test_cr07_undeclared_class_is_unverified(case):
    p, i = on_profile(case, case[1].profile.model_copy(update={"account_class": "UNVERIFIED"}))
    r = assess(p, i, now=NOW)
    assert r.verdict == "UNVERIFIED" and any("account consequence class" in x for x in r.reasons)


def test_cr07_reservation_rechecks_the_class(tmp_path, case):
    p, i = case
    store = store_at(tmp_path)
    store.queue(p, i, now=NOW)
    token = store.approve(p.proposal_id, authenticated_owner="fixture-owner", now=NOW)
    funded = i.model_copy(update={"profile": i.profile.model_copy(update={"account_class": "FUNDED"})})
    with pytest.raises(ReviewRefused, match="PRACTICE-only"):
        store.reserve_intent(p, token, funded, now=NOW)
    assert store.inspect(p.proposal_id)["state"] == "APPROVED"


# --- CR-12: FTMO is recognised by enum values, never by matching free text ---

@pytest.mark.parametrize("field,value", [
    ("provider", "FTMO s.r.o."), ("provider", "ftmo"), ("provider", "FTMO Challenge 1-Step"),
    ("product", "1-Step"), ("product", "FTMO Challenge: 1-Step"), ("product", "FTMO Challenge 1-Step"),
    ("evaluation_stage", "phase2"), ("account_type", "demo"), ("account_class", "DEMO"),
])
def test_cr12_free_text_spellings_are_rejected(case, field, value):
    with pytest.raises(ValidationError):
        ftmo(case, **{field: value})


@pytest.mark.parametrize("provider,product", [("OTHER", "FTMO_1_STEP"), ("FIXTURE_NOT_FTMO", "FTMO_2_STEP"),
                                              ("FTMO", "OTHER"), ("FTMO", "TEST")])
def test_cr12_ftmo_products_belong_to_provider_ftmo_only(case, provider, product):
    with pytest.raises(ValidationError, match="FTMO products"):
        ftmo(case, provider=provider, product=product)


def test_cr12_ftmo_always_gets_the_prague_boundary(case):
    with pytest.raises(ValidationError, match="Prague midnight"):
        ftmo(case, reset_timezone="Europe/London")


def test_cr12_unvalidated_copy_cannot_skip_the_ftmo_checks(case):
    """The review case: a spelling that skipped the Prague check, built without validation."""
    p, i = case
    sneaky = i.profile.model_copy(update={"provider": "FTMO s.r.o.", "product": "FTMO Challenge 1-Step",
                                          "reset_timezone": "Europe/London"})
    assert evaluate_profile(sneaky, i.rule_state, now=NOW).status == "POLICY_UNVERIFIED"
    r = assess(p.model_copy(update={"rule_profile_hash": sneaky.profile_hash}),
               i.model_copy(update={"profile": sneaky}), now=NOW)
    assert r.verdict == "UNVERIFIED" and r.reasons[0].startswith("INVALID_CONTRACT")


def test_cr12_one_step_consistency_selector_runs_on_the_enum(case):
    from datetime import datetime, timezone
    after_cutover = datetime(2026, 10, 20, tzinfo=timezone.utc)
    wrong = ftmo(case, product="FTMO_1_STEP", consistency_rule="BEST_DAY_POSITIVE_DAYS_50",
                 order_created_at=after_cutover)
    right = ftmo(case, product="FTMO_1_STEP", consistency_rule="CONSISTENCY_NET_PROFIT_50",
                 order_created_at=after_cutover)
    assert evaluate_profile(wrong, case[1].rule_state, now=NOW).status == "POLICY_UNVERIFIED"
    assert evaluate_profile(right, case[1].rule_state, now=NOW).status == "PASS"


# --- CR-08: news, gap and long-break rules are enums enforced against evidence ---



def release(at, *, currency="USD", impact="HIGH", instrument_id=None, name="NFP"):
    prov = calendar().provenance
    return EconomicEvent(event_id=f"{name}@{at.isoformat()}", currency=currency, scheduled_at=at,
                         impact=impact, provenance=prov, instrument_id=instrument_id)


def standard_funded(case):
    return ftmo(case, evaluation_stage="FUNDED", account_class="FUNDED")


def swing_funded(case):
    return ftmo(case, evaluation_stage="FUNDED", account_class="FUNDED", account_type="SWING")


def with_evidence(case, profile, **evidence):
    p, i = on_profile(case, profile)
    return p, i.model_copy(update=evidence)


def test_cr08_standard_ftmo_account_nfp_in_90_seconds_is_denied(case):
    p, i = with_evidence(case, standard_funded(case),
                         economic_calendar=calendar([release(NOW + timedelta(seconds=90))]))
    r = assess(p, i, now=NOW)
    assert r.verdict == "DENY"
    assert {"NEWS_EXECUTION_WINDOW", "MAJOR_NEWS_SCHEDULED_TODAY"} <= set(r.reasons)


def test_cr08_news_window_covers_any_release_for_the_standard_account(case):
    p, i = with_evidence(case, standard_funded(case),
                         economic_calendar=calendar([release(NOW - timedelta(seconds=60), impact="LOW")]))
    assert assess(p, i, now=NOW).reasons == ("NEWS_EXECUTION_WINDOW",)


def test_cr08_swing_news_window_does_not_apply_but_gap_rule_does(case):
    low = calendar([release(NOW + timedelta(seconds=60), impact="LOW")])
    p, i = with_evidence(case, swing_funded(case), economic_calendar=low)
    assert assess(p, i, now=NOW).verdict == "ALLOW"
    p, i = with_evidence(case, swing_funded(case),
                         economic_calendar=calendar([release(NOW + timedelta(hours=3))]))
    assert assess(p, i, now=NOW).reasons == ("MAJOR_NEWS_SCHEDULED_TODAY",)


def test_cr08_a_release_already_past_no_longer_blocks(case):
    for ago, verdict in ((timedelta(minutes=5), "ALLOW"), (timedelta(seconds=90), "DENY")):
        p, i = with_evidence(case, ftmo(case), economic_calendar=calendar([release(NOW - ago)]))
        assert assess(p, i, now=NOW).verdict == verdict


def test_cr08_other_currencies_do_not_block(case):
    p, i = with_evidence(case, standard_funded(case),
                         economic_calendar=calendar([release(NOW + timedelta(seconds=30), currency="JPY")]))
    assert assess(p, i, now=NOW).verdict == "ALLOW"


def weekend_in(minutes, *, break_hours=48):
    closes = NOW + timedelta(minutes=minutes)
    return session(closes_at=closes, next_opens_at=closes + timedelta(hours=break_hours))


def test_cr08_entry_90_minutes_before_the_weekend_is_denied(case):
    """The review case: Friday 20:30 UTC entry, FX closes 22:00 UTC for 48 hours."""
    p, i = with_evidence(case, standard_funded(case), session=weekend_in(90))
    r = assess(p, i, now=NOW)
    assert r.verdict == "DENY"
    assert {"GAP_TRADING_WINDOW_BEFORE_LONG_BREAK", "MUST_BE_FLAT_BEFORE_LONG_BREAK"} <= set(r.reasons)
    # A Challenge has no break-holding rule, but gap trading is forbidden in every phase.
    p, i = with_evidence(case, ftmo(case), session=weekend_in(90))
    assert assess(p, i, now=NOW).reasons == ("GAP_TRADING_WINDOW_BEFORE_LONG_BREAK",)


@pytest.mark.parametrize("minutes,break_hours,verdict", [
    (136, 48, "ALLOW"), (134, 48, "DENY"),   # 2 h + the 15-minute internal margin
    (30, 1, "ALLOW"),                         # a one-hour break is not a long break
    (30, 2, "DENY"),                          # two hours is
])
def test_cr08_pre_break_window_edges(case, minutes, break_hours, verdict):
    p, i = with_evidence(case, ftmo(case), session=weekend_in(minutes, break_hours=break_hours))
    assert assess(p, i, now=NOW).verdict == verdict


def test_cr08_flatten_deadline_for_the_standard_account_only(case):
    margin = timedelta(minutes=15)
    weekend = weekend_in(300)
    assert flatten_deadline(standard_funded(case), weekend, margin=margin) == weekend.closes_at - margin
    assert flatten_deadline(swing_funded(case), weekend, margin=margin) is None
    assert flatten_deadline(standard_funded(case), weekend_in(300, break_hours=1), margin=margin) is None


@pytest.mark.parametrize("evidence,reason", [
    ({"economic_calendar": None}, "ECONOMIC_CALENDAR_REQUIRED"),
    ({"economic_calendar": calendar(retrieved_at=NOW - timedelta(hours=7))}, "ECONOMIC_CALENDAR_UNVERIFIED"),
    ({"economic_calendar": calendar(quality="SUSPECT")}, "ECONOMIC_CALENDAR_UNVERIFIED"),
    ({"economic_calendar": calendar(covers_until=NOW + timedelta(hours=6))}, "ECONOMIC_CALENDAR_DOES_NOT_COVER_THE_DAY"),
    ({"session": None}, "SESSION_EVIDENCE_REQUIRED"),
    ({"session": session("fixture:GBPUSD")}, "SESSION_EVIDENCE_UNVERIFIED"),
    ({"session": session(state="UNKNOWN")}, "SESSION_EVIDENCE_UNVERIFIED"),
    ({"session": session(retrieved_at=NOW - timedelta(hours=2))}, "SESSION_EVIDENCE_UNVERIFIED"),
    ({"session": session(next_opens_at=None)}, "SESSION_NEXT_OPEN_UNKNOWN"),
])
def test_cr08_missing_or_stale_evidence_is_unverified(case, evidence, reason):
    p, i = with_evidence(case, ftmo(case), **evidence)
    r = assess(p, i, now=NOW)
    assert r.verdict == "UNVERIFIED" and reason in r.reasons


def test_cr08_rules_set_to_none_need_no_calendar(case):
    p, i = case
    assert assess(p, i.model_copy(update={"economic_calendar": None}), now=NOW).verdict == "ALLOW"
    # The session itself is always required (CR-11), whatever the rules say.
    r = assess(p, i.model_copy(update={"session": None}), now=NOW)
    assert r.verdict == "UNVERIFIED" and r.reasons == ("SESSION_EVIDENCE_REQUIRED",)


@pytest.mark.parametrize("field,value", [("news_rule", "fixture:none"), ("weekend_rule", "fixture:none"),
    ("gap_rule", "checked"), ("instrument_rule", "fixture:EURUSD"),
    ("prohibited_practices_rule", "fixture:checked"), ("overnight_rule", "ok")])
def test_cr08_free_text_rule_values_are_rejected(case, field, value):
    """At 7385130 any string other than "UNVERIFIED" counted as a verified rule."""
    with pytest.raises(ValidationError):
        RuleProfile.model_validate({**case[1].profile.model_dump(), field: value})


@pytest.mark.parametrize("changes", [
    {"evaluation_stage": "FUNDED", "account_class": "FUNDED", "news_rule": "NONE"},
    {"evaluation_stage": "FUNDED", "account_class": "FUNDED", "weekend_rule": "NONE"},
    {"gap_rule": "NONE"},
    {"news_rule": "NO_EXECUTION_2MIN_AROUND_NEWS"},  # Challenge: the published rule is NONE
])
def test_cr08_ftmo_rule_values_follow_stage_and_type(case, changes):
    with pytest.raises(ValidationError, match="FTMO"):
        ftmo(case, **changes)


def test_cr08_instrument_outside_the_allowlist_is_denied(case):
    p, i = on_profile(case, case[1].profile.model_copy(update={"allowed_instruments": ("fixture:GBPUSD",)}))
    assert assess(p, i, now=NOW).reasons == ("INSTRUMENT_NOT_PERMITTED_BY_PROFILE",)
    with pytest.raises(ValidationError, match="allowlist"):
        RuleProfile.model_validate({**case[1].profile.model_dump(), "allowed_instruments": ()})


def test_cr08_equities_need_an_earnings_calendar(case):
    instrument = case[1].specification.instrument.model_copy(update={
        "instrument_id": "fixture:AAPL", "asset_class": AssetClass.EQUITIES})
    profile = ftmo(case)
    assert news_checks(profile, instrument, calendar(), now=NOW, max_age_seconds=21600) == \
        (["EARNINGS_CALENDAR_REQUIRED"], [])
    earnings = calendar([release(NOW + timedelta(hours=4), instrument_id="fixture:AAPL", name="earnings")],
                        covers_earnings=True)
    assert news_checks(profile, instrument, earnings, now=NOW, max_age_seconds=21600) == \
        ([], ["MAJOR_NEWS_SCHEDULED_TODAY"])


# --- CR-09: per-day position, server-request and open-order limits ---

def test_cr09_1600_requests_today_none_in_the_last_minute_is_denied(case):
    p, i = case
    r = assess(p, i.model_copy(update={"activity": activity(server_requests=1600)}), now=NOW)
    assert r.verdict == "DENY" and r.reasons == ("DAILY_SERVER_REQUEST_HEADROOM",)


@pytest.mark.parametrize("field,allowed,denied,reason", [
    ("server_requests", 1597, 1598, "DAILY_SERVER_REQUEST_HEADROOM"),  # +3 per trade vs 80% of 2,000
    ("positions_opened", 1599, 1600, "DAILY_POSITION_LIMIT_HEADROOM"),
    ("open_orders", 159, 160, "OPEN_ORDER_LIMIT_HEADROOM"),            # 80% of 200
])
def test_cr09_headroom_edges(case, field, allowed, denied, reason):
    p, i = case
    assert assess(p, i.model_copy(update={"activity": activity(**{field: allowed})}), now=NOW).verdict == "ALLOW"
    r = assess(p, i.model_copy(update={"activity": activity(**{field: denied})}), now=NOW)
    assert r.reasons == (reason,)


@pytest.mark.parametrize("changes", [{"day": "2026-11-05"}, {"account_id": "other"}, {"complete": False},
                                     {"observed_at": NOW - timedelta(seconds=16)},
                                     {"observed_at": NOW + timedelta(seconds=1)}])
def test_cr09_activity_must_be_todays_complete_fresh_record(case, changes):
    p, i = case
    r = assess(p, i.model_copy(update={"activity": activity(**changes)}), now=NOW)
    assert r.verdict == "UNVERIFIED" and "DAILY_ACTIVITY_UNVERIFIED" in r.reasons


def test_cr09_the_day_is_the_prague_day(case):
    from datetime import datetime, timezone
    late = datetime(2026, 11, 6, 23, 30, tzinfo=timezone.utc)  # 00:30 on 7 November in Prague
    kwargs = dict(now=late, max_age_seconds=15, headroom=D("0.8"), requests_per_trade=3,
                  max_requests_per_minute=2)
    stale = activity(observed_at=late)
    assert activity_checks(case[1].profile, stale, "demo-1", **kwargs) == (["DAILY_ACTIVITY_UNVERIFIED"], [])
    fresh = activity(day="2026-11-07", observed_at=late)
    assert activity_checks(case[1].profile, fresh, "demo-1", **kwargs) == ([], [])


@pytest.mark.parametrize("field,value", [("max_server_requests_per_day", 2001),
                                         ("max_positions_per_day", 2500), ("max_open_orders", 201)])
def test_cr09_ftmo_limits_cannot_exceed_the_published_figures(case, field, value):
    with pytest.raises(ValidationError, match="cannot exceed"):
        ftmo(case, **{field: value})


# --- CR-11: the engine reads the kill switch and derives the book and costs itself ---

SELF_ASSERTED = ("kill_switch_clear", "session_open", "existing_open_risk", "pending_order_risk",
                 "correlated_risk", "current_notional", "position_count", "spread_cost", "commission",
                 "duplicate_position", "cooldown_clear", "abnormal_market", "book_risk_complete",
                 "operational_constraints_verified", "orders_last_minute")


def test_cr11_no_input_field_states_a_result():
    from src.trading.multiasset_risk import RiskInputs
    assert not set(SELF_ASSERTED) & set(RiskInputs.model_fields)


def test_cr11_legacy_kill_switch_from_the_environment_halts(case, monkeypatch):
    """The review's test: engaged legacy switch => EMERGENCY_HALT, no boolean passed."""
    p, i = case
    assert assess(p, i, now=NOW).verdict == "ALLOW"
    monkeypatch.setenv("ARIA_KILL_SWITCH", "1")
    r = assess(p, i, now=NOW)
    assert r.verdict == "EMERGENCY_HALT" and r.reasons == ("KILL_SWITCH",)


def test_cr11_legacy_kill_switch_file_halts_and_release_restores(case):
    from src.execution import kill_switch
    p, i = case
    kill_switch.engage("CR-11 regression test", actor="test")
    assert assess(p, i, now=NOW).verdict == "EMERGENCY_HALT"
    kill_switch.release(actor="test")
    assert assess(p, i, now=NOW).verdict == "ALLOW"


def test_cr11_unreadable_kill_switch_fails_closed(case):
    from src.execution import kill_switch
    p, i = case
    kill_switch.STATE_FILE.write_text("{not json", encoding="utf-8")
    assert assess(p, i, now=NOW).verdict == "EMERGENCY_HALT"


@pytest.mark.parametrize("bad,reason", [
    (book(holding(loss=None)), "BOOK_POSITION_WITHOUT_STOP"),
    (book(account_id="other"), "INCOMPLETE_ACCOUNT_BOOK"),
    (book(observed_at=NOW - timedelta(seconds=16)), "INCOMPLETE_ACCOUNT_BOOK"),
    (book(complete=False), "INCOMPLETE_ACCOUNT_BOOK"),
])
def test_cr11_book_must_be_complete_fresh_and_bounded(case, bad, reason):
    p, i = case
    r = assess(p, i.model_copy(update={"book": bad}), now=NOW)
    assert r.verdict == "UNVERIFIED" and reason in r.reasons


def test_cr11_book_risk_and_correlation_are_derived_from_positions(case):
    p, i = case
    # $100 at risk on GBPUSD shares USD with EURUSD: the 1% correlated budget is used up.
    r = assess(p, i.model_copy(update={"book": book(holding("GBPUSD", loss="100"))}), now=NOW)
    assert r.verdict == "DENY" and "RISK_BUDGET_EXCEEDED" in r.reasons
    # The same $100 on AUDCAD shares nothing: only the 2% aggregate budget applies.
    r = assess(p, i.model_copy(update={"book": book(holding("AUDCAD", base="AUD", quote="CAD",
                                                            loss="100"))}), now=NOW)
    assert r.verdict == "ALLOW"


def test_cr11_duplicate_is_found_in_the_book(case):
    p, i = case
    r = assess(p, i.model_copy(update={"book": book(holding("EURUSD", kind="PENDING_ORDER"))}), now=NOW)
    assert r.verdict == "DENY" and r.reasons == ("DUPLICATE_POSITION",)
    # The opposite side is not a duplicate.
    opposite = book(holding("EURUSD", side="SELL", loss="1"))
    assert assess(p, i.model_copy(update={"book": opposite}), now=NOW).verdict == "ALLOW"


def test_cr11_spread_cost_comes_from_the_quote(case):
    p, i = case
    wide = i.quote.model_copy(update={"bid": D("1.09990")})  # 3 pips instead of 2
    r = assess(p, i.model_copy(update={"quote": wide}), now=NOW)
    # 25.50 stop + 1.50 spread + 0.50 commission + 1.50 slippage (one spread)
    assert r.verdict == "ALLOW" and r.monetary_risk == D("29.00")


def test_cr11_stated_slippage_cannot_go_below_one_spread(case):
    p, i = case
    r = assess(p, i.model_copy(update={"slippage_allowance": D("0")}), now=NOW)
    assert r.monetary_risk == D("28.00")


def test_cr11_commission_comes_from_the_specification(case):
    p, i = case
    for change in ({"commission_per_lot": None, "commission_currency": None},
                   {"commission_currency": "EUR"}):
        spec = i.specification.model_copy(update=change)
        r = assess(p, i.model_copy(update={"specification": spec}), now=NOW)
        assert r.verdict == "UNVERIFIED" and "COMMISSION_UNVERIFIED" in r.reasons
    with pytest.raises(ValidationError, match="commission"):
        type(i.specification).model_validate({**i.specification.model_dump(), "commission_currency": None})


@pytest.mark.parametrize("ago,verdict", [(timedelta(minutes=29), "DENY"), (timedelta(minutes=31), "ALLOW"),
                                         (-timedelta(minutes=1), "UNVERIFIED")])
def test_cr11_cooldown_from_the_last_losing_close(case, ago, verdict):
    p, i = case
    r = assess(p, i.model_copy(update={"activity": activity(last_losing_close_at=NOW - ago)}), now=NOW)
    assert r.verdict == verdict


@pytest.mark.parametrize("state,verdict", [("CLOSED", "DENY"), ("HALTED", "DENY"), ("UNKNOWN", "UNVERIFIED")])
def test_cr11_session_state_comes_from_session_evidence(case, state, verdict):
    p, i = case
    assert assess(p, i.model_copy(update={"session": session(state=state)}), now=NOW).verdict == verdict


# --- CR-06: MT5 reads need an investor login and run in their own process ---

IDENTITY = dict(expected_login=123, expected_server="fixture-demo", expected_currency="USD")


@pytest.mark.parametrize("fake", [mt5_fakes.trading_login, mt5_fakes.algo_terminal])
def test_cr06_a_login_that_can_trade_is_refused_at_connect(fake):
    """The review's test: connect() with trade_allowed=True => MT5PermissionDenied."""
    adapter = MT5ReadOnlyAdapter(sdk=fake())
    with pytest.raises(MT5PermissionDenied):
        adapter.connect(**IDENTITY)
    assert adapter.health()["connected"] is False


def test_cr06_missing_trade_flag_is_refused():
    sdk = mt5_fakes.investor()
    del sdk.account.trade_allowed
    with pytest.raises(MT5PermissionDenied, match="investor"):
        MT5ReadOnlyAdapter(sdk=sdk).connect(**IDENTITY)


def test_cr06_switching_to_a_trading_login_blocks_the_next_read():
    sdk = mt5_fakes.investor()
    adapter = MT5ReadOnlyAdapter(sdk=sdk)
    adapter.connect(**IDENTITY)
    assert adapter.read("symbols_get")
    sdk.account.trade_allowed = True
    with pytest.raises(MT5PermissionDenied):
        adapter.read("symbols_get")


def test_cr06_the_adapter_cannot_load_the_sdk_itself():
    with pytest.raises(TypeError):
        MT5ReadOnlyAdapter()  # an SDK must be handed in, by the worker
    with pytest.raises(MT5Unavailable, match="worker process"):
        MT5ReadOnlyAdapter(sdk=None).connect(**IDENTITY)


def test_cr06_the_client_reads_through_the_worker_and_holds_no_sdk():
    """The review's test: no `_sdk` reachable from the RPC client (probe M1 reached it)."""
    import sys
    client = MT5ReadClient.start(**IDENTITY, sdk_factory="mt5_fakes:investor")
    try:
        assert client.read("symbols_get") == [{"name": "EURUSD", "digits": 5}]
        assert client.health()["execution"] == "DISABLED"
        assert set(MT5ReadClient.__slots__) == {"_conn", "_process", "_timeout"}
        for name in ("_sdk", "_adapter", "sdk"):
            with pytest.raises(AttributeError):
                getattr(client, name)
        with pytest.raises(AttributeError):
            client._sdk = object()  # no attribute can be added to the client
        with pytest.raises(MT5PermissionDenied):
            client.order_send({"action": 1})
        with pytest.raises(MT5PermissionDenied):
            client.read("order_send", {"action": 1})
        # Bypass the client's own check: the worker refuses before the SDK.
        for raw in (("read", "order_send", ({"action": 1},), {}), ("call", "order_send"), "order_send"):
            client._conn.send(raw)
            with pytest.raises(MT5PermissionDenied, match="approved reads"):
                client._receive()
        assert client.read("positions_get") == []
        assert "MetaTrader5" not in sys.modules
    finally:
        client.close()
    assert not client._process.is_alive()


def test_cr06_the_worker_refuses_a_trading_login():
    with pytest.raises(MT5PermissionDenied, match="investor"):
        MT5ReadClient.start(**IDENTITY, sdk_factory="mt5_fakes:trading_login")


# --- CR-14: backup() closes every connection it opens ---

def test_cr14_backup_closes_its_connections(tmp_path, case, monkeypatch):
    import src.trading.review_store as review_store
    opened = []

    class Tracked(sqlite3.Connection):
        closed = False

        def close(self):
            self.closed = True
            super().close()

    real_connect = sqlite3.connect

    def tracking_connect(*args, **kwargs):
        connection = real_connect(*args, factory=Tracked, **kwargs)
        opened.append(connection)
        return connection

    p, i = case
    store = store_at(tmp_path)
    store.queue(p, i, now=NOW)
    backup = tmp_path / "backup.sqlite"
    monkeypatch.setattr(review_store.sqlite3, "connect", tracking_connect)
    store.backup(backup)
    monkeypatch.undo()
    assert len(opened) == 2                       # the source and the target
    assert all(connection.closed for connection in opened)
    backup.unlink()                               # nothing holds the file


# --- SEC-01: the audit chain is keyed, and can be anchored outside the database ---

def rewrite_chain(path, link):
    """What someone who can write the file but lacks the key would do: edit an
    event, then recompute every link with the only function they have."""
    with sqlite3.connect(str(path)) as db:
        rows = db.execute("SELECT sequence, body FROM audit ORDER BY sequence").fetchall()
        previous = "0" * 64
        for sequence, body in rows:
            if sequence == 1:
                body = body[:-1] + ',"tampered":true}'
            digest = link(previous, body)
            db.execute("UPDATE audit SET body=?, previous_hash=?, event_hash=? WHERE sequence=?",
                       (body, previous, digest, sequence))
            previous = digest
    sqlite3.connect(str(path)).close()


def sha256_link(previous, body):
    import hashlib
    return hashlib.sha256((previous + body).encode()).hexdigest()


def approved(tmp_path, case, store):
    p, i = case
    store.queue(p, i, now=NOW)
    token = store.approve(p.proposal_id, authenticated_owner="fixture-owner", now=NOW)
    return p, i, token


def test_sec01_rebuilt_sha256_chain_is_refused_on_a_keyed_store(tmp_path, case):
    store = store_at(tmp_path)
    p, i, token = approved(tmp_path, case, store)
    rewrite_chain(store.path, sha256_link)
    with pytest.raises(ReviewRefused, match="integrity"):
        store.verify_audit()
    with pytest.raises(ReviewRefused, match="integrity"):
        store.reserve_intent(p, token, i, now=NOW)


def test_sec01_keyed_chain_needs_its_key(tmp_path, case):
    store = store_at(tmp_path)
    approved(tmp_path, case, store)
    with pytest.raises(ReviewRefused, match="audit key missing or wrong"):
        ReviewStore(store.path, clock=clock)
    with pytest.raises(ReviewRefused, match="audit key missing or wrong"):
        ReviewStore(store.path, clock=clock, audit_key=b"another-key-of-thirty-two-bytes!")
    with pytest.raises(ReviewRefused, match="at least 32 bytes"):
        ReviewStore(tmp_path / "short.sqlite", clock=clock, audit_key=b"short")


@pytest.mark.parametrize("downgrade", [
    "UPDATE audit_meta SET key_id='UNKEYED'",
    "DELETE FROM audit_meta",
])
def test_sec01_marking_a_keyed_chain_unkeyed_is_refused(tmp_path, case, downgrade):
    store = store_at(tmp_path)
    approved(tmp_path, case, store)
    rewrite_chain(store.path, sha256_link)
    with sqlite3.connect(str(store.path)) as db:
        db.execute(downgrade)
    sqlite3.connect(str(store.path)).close()
    with pytest.raises(ReviewRefused, match="cannot be re-keyed"):
        store_at(tmp_path)


def test_sec01_unkeyed_store_cannot_reserve_or_be_rekeyed(tmp_path, case):
    store = ReviewStore(tmp_path / "review.sqlite", clock=clock)
    p, i, token = approved(tmp_path, case, store)
    assert store.verify_audit()                  # still a working review queue
    with pytest.raises(ReviewRefused, match="keyed audit chain"):
        store.reserve_intent(p, token, i, now=NOW)
    assert store.inspect(p.proposal_id)["state"] == "APPROVED"
    with pytest.raises(ReviewRefused, match="cannot be re-keyed"):
        store_at(tmp_path)


def test_sec01_external_anchor_detects_truncation_and_rewrite(tmp_path, case):
    store = store_at(tmp_path)
    p, i, token = approved(tmp_path, case, store)
    anchor = store.anchor()
    assert store.verify_audit(anchor) == anchor["event_hash"]
    store.reserve_intent(p, token, i, now=NOW)
    assert store.verify_audit(anchor)            # later events keep the anchor
    with sqlite3.connect(str(store.path)) as db:
        db.execute("DELETE FROM audit WHERE sequence >= ?", (anchor["sequence"],))
    sqlite3.connect(str(store.path)).close()
    store.verify_audit()                         # a keyed prefix still links ...
    with pytest.raises(ReviewRefused, match="external anchor"):
        store.verify_audit(anchor)               # ... but has lost its anchor
    with pytest.raises(ReviewRefused, match="external anchor"):
        store.verify_audit({**anchor, "sequence": 1})
