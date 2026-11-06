from datetime import date, timedelta
from decimal import Decimal as D

import pytest

from test_multiasset_risk import case, NOW
from src.trading.multiasset_risk import assess, floor_volume
from src.trading.rule_profiles import (evaluate_profile, evaluate_objectives,
    ftmo_1step_consistency_version, TradingDayResult)


def test_historical_consistency_variants_are_not_interchangeable(case):
    _,i=case
    days=tuple(TradingDayResult(day=date(2026,11,j+1),closed_net_pnl=str(p),positions_opened=1)
               for j,p in enumerate((600,-200,400,200)))
    legacy=i.profile.model_copy(update={"consistency_rule":"BEST_DAY_POSITIVE_DAYS_50",
              "minimum_trading_days":4,"profit_target_fraction":D("0.1")})
    updated=legacy.model_copy(update={"consistency_rule":"CONSISTENCY_NET_PROFIT_50"})
    old=evaluate_objectives(legacy,balance=D("11000"),days=days,all_positions_closed=True,now=NOW)
    new=evaluate_objectives(updated,balance=D("11000"),days=days,all_positions_closed=True,now=NOW)
    assert old["status"]=="ELIGIBLE_FOR_HUMAN_REVIEW"
    assert new["status"]=="NOT_YET_ELIGIBLE"
    assert any("NOT_A_LOSS_RULE_BREACH" in r for r in new["reasons"])
    assert not old["execution_authorised"]


def test_october_cutover_uses_published_platform_time_and_preserves_legacy():
    from datetime import datetime, timezone
    cutover=datetime(2026,10,12,6,tzinfo=timezone.utc)
    assert ftmo_1step_consistency_version(order_created_at=cutover-timedelta(microseconds=1))=="BEST_DAY_POSITIVE_DAYS_50"
    assert ftmo_1step_consistency_version(order_created_at=cutover)=="CONSISTENCY_NET_PROFIT_50"
    assert ftmo_1step_consistency_version(order_created_at=cutover-timedelta(days=1),merged_at=cutover)=="CONSISTENCY_NET_PROFIT_50"
    assert ftmo_1step_consistency_version(order_created_at=None)=="UNVERIFIED"


def test_missing_exact_account_creation_does_not_select_ftmo_version(case):
    _,i=case
    profile=i.profile.model_copy(update={"provider":"FTMO","product":"1-Step",
         "consistency_rule":"CONSISTENCY_NET_PROFIT_50","order_created_at":None})
    assert evaluate_profile(profile,i.rule_state,now=NOW).status=="POLICY_UNVERIFIED"


def test_phase_targets_minimum_days_and_closed_positions(case):
    _,i=case
    first=i.profile.model_copy(update={"profit_target_fraction":D("0.1"),"minimum_trading_days":4})
    second=first.model_copy(update={"evaluation_stage":"phase2","profit_target_fraction":D("0.05")})
    days=tuple(TradingDayResult(day=date(2026,11,j+1),closed_net_pnl="125",positions_opened=1) for j in range(4))
    assert evaluate_objectives(first,balance=D("10500"),days=days,all_positions_closed=True,now=NOW)["status"]=="NOT_YET_ELIGIBLE"
    assert evaluate_objectives(second,balance=D("10500"),days=days,all_positions_closed=True,now=NOW)["status"]=="ELIGIBLE_FOR_HUMAN_REVIEW"
    assert evaluate_objectives(second,balance=D("10500"),days=days,all_positions_closed=False,now=NOW)["status"]=="NOT_YET_ELIGIBLE"


def test_tick_value_mismatch_cannot_be_treated_as_linear_contract(case):
    p,i=case
    spec=i.specification.model_copy(update={"tick_value":D("99")})
    assert assess(p,i.model_copy(update={"specification":spec}),now=NOW).verdict=="UNVERIFIED"


def test_margin_and_price_tolerance_are_independent_checks(case):
    p,i=case
    poor=i.account.model_copy(update={"available_margin":D("100")})
    assert assess(p,i.model_copy(update={"account":poor}),now=NOW).verdict=="DENY"
    q=i.quote.model_copy(update={"bid":D("1.101"),"ask":D("1.1012")})
    assert "PRICE_TOLERANCE_EXCEEDED" in assess(p,i.model_copy(update={"quote":q}),now=NOW).reasons


def test_floor_volume_property_is_never_above_budget():
    # Deterministic property check without adding a test-only package.
    for step in (D("0.01"),D("0.25"),D("1")):
        for cents in range(1,500):
            raw=D(cents)/D("137")
            floored=floor_volume(raw,step)
            assert floored<=raw<floored+step and floored%step==0
