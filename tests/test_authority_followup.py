"""P0 authority follow-up: Claude's independent probes of 268af1d, as regressions.

Each test reproduces a bypass found against a CONFIGURED review store (trusted
evidence source and external checkpoint) at 268af1d. The four history cases are
a regression introduced there: recorded boundary history replaced the trusted
source's account state wholesale, so caller-recorded rows could loosen it.
"""
import shutil
import sqlite3
from datetime import date, timedelta
from decimal import Decimal as D

import pytest

from test_multiasset_risk import case, NOW, snapshot_source
from test_review_findings import AUDIT_KEY, _record, _trailing_profile, clock, store_at, twenty_dollar_trade
from src.trading.multiasset_risk import assess
from src.trading.review_store import ReviewRefused, ReviewStore


def history(store, profile, balances):
    for n, balance in enumerate(balances):
        _record(store, profile, date(2026, 11, 1) + timedelta(days=n), balance)


# --- Recorded history may tighten the trusted source's account state, never loosen it ---

def test_recorded_midnight_below_the_source_cannot_loosen_the_daily_limit(tmp_path, case):
    # The source says today's midnight balance was 10,400 and equity is 10,100:
    # a 300 loss today, past the internal daily stop. A caller records 10,100
    # as today's boundary. At 268af1d the store believed the record: ALLOW.
    p, honest = twenty_dollar_trade(case, equity="10100", daily_balance="10400", high_water="10400")
    assert assess(p, honest, now=NOW).verdict == "EMERGENCY_HALT"
    store = store_at(tmp_path, inputs=honest)
    history(store, honest.profile, ["10000", "10000", "10000", "10000", "10000", "10100"])
    with pytest.raises(ReviewRefused, match="disagrees with the authoritative source"):
        store.queue(p, honest, now=NOW)
    assert store.inspect(p.proposal_id) is None


def trailing(case, *, high_water, daily="9850", equity="9850"):
    p, i = case
    profile = _trailing_profile(case)
    p = p.model_copy(update={"rule_profile_hash": profile.profile_hash})
    i = i.model_copy(update={
        "profile": profile,
        "account": i.account.model_copy(update={"equity": D(equity), "balance": D(equity)}),
        "rule_state": i.rule_state.model_copy(update={"equity": D(equity), "daily_balance": D(daily),
                                                      "balance_high_water": D(high_water)})})
    return p, i


def test_recorded_high_water_below_the_source_cannot_loosen_the_trailing_floor(tmp_path, case):
    # Source: high-water 10,800, so the trailing floor is 9,800 and equity 9,850
    # sits inside the buffer (DENY). Recorded rows never pass 10,000 (floor 9,000).
    p, honest = trailing(case, high_water="10800")
    assert assess(p, honest, now=NOW).verdict == "DENY"
    store = store_at(tmp_path, inputs=honest)
    history(store, honest.profile, ["10000", "10000", "10000", "10000", "9900", "9850"])
    with pytest.raises(ReviewRefused, match="risk engine verdict DENY"):
        store.queue(p, honest, now=NOW)


def test_recorded_high_water_still_raises_a_lower_source_value(tmp_path, case):
    # The other direction is kept: a transport that reconstructs a lower
    # high-water (10,000) cannot erase the 10,800 the store recorded.
    p, source = trailing(case, high_water="10000")
    assert assess(p, source, now=NOW).verdict == "ALLOW"
    store = store_at(tmp_path, inputs=source)
    history(store, source.profile, ["10000", "10800", "10700", "10600", "10500", "9850"])
    with pytest.raises(ReviewRefused, match="risk engine verdict DENY: .*WITHIN_FLOOR_BUFFER"):
        store.queue(p, source, now=NOW)


@pytest.mark.parametrize("field,value", [("cashflows_since_snapshot", D("-500")), ("balance_reset", True)])
def test_source_cashflow_or_reset_survives_recorded_history(tmp_path, case, field, value):
    # A withdrawal or reset the source reports makes the policy unverifiable.
    # At 268af1d the store rebuilt the state from its rows and dropped the flag.
    p, i = case
    honest = i.model_copy(update={"rule_state": i.rule_state.model_copy(update={field: value})})
    assert assess(p, honest, now=NOW).verdict == "UNVERIFIED"
    store = store_at(tmp_path, inputs=honest)
    history(store, honest.profile, ["10000"] * 6)
    with pytest.raises(ReviewRefused, match="risk engine verdict UNVERIFIED"):
        store.queue(p, honest, now=NOW)


def test_recorder_refuses_an_account_the_store_is_not_bound_to(tmp_path, case):
    p, i = case
    store = store_at(tmp_path, inputs=i)
    other = i.profile.model_copy(update={"account_id": "someone-else"})
    with pytest.raises(ReviewRefused, match="bound account"):
        history(store, other, ["10000"])


# --- A keyed store changes only together with its external checkpoint ---

def test_keyed_store_without_its_checkpoint_cannot_change(tmp_path, case):
    p, i = case
    store = store_at(tmp_path, inputs=i)
    store.queue(p, i, now=NOW)
    token = store.approve(p.proposal_id, authenticated_owner="fixture-owner", now=NOW)
    store.reserve_intent(p, token, i, now=NOW)
    unchecked = ReviewStore(store.path, clock=clock, audit_key=AUDIT_KEY)
    with pytest.raises(ReviewRefused, match="external checkpoint"):
        unchecked.recover(now=NOW)
    assert store.inspect(p.proposal_id)["state"] == "SUBMISSION_PENDING"


def test_rollback_cannot_release_a_reconciliation_hold(tmp_path, case):
    # At 268af1d an instance opened without the checkpoint could hold the intent;
    # rolling the file back to the reservation then released the hold unnoticed.
    p, i = case
    store = store_at(tmp_path, inputs=i)
    store.queue(p, i, now=NOW)
    token = store.approve(p.proposal_id, authenticated_owner="fixture-owner", now=NOW)
    store.reserve_intent(p, token, i, now=NOW)
    copy = tmp_path / "at-reservation.sqlite"
    shutil.copyfile(store.path, copy)
    try:
        ReviewStore(store.path, clock=clock, audit_key=AUDIT_KEY).recover(now=NOW)
    except ReviewRefused:
        store.recover(now=NOW)          # the hold, written with the checkpoint
    assert store.inspect(p.proposal_id)["state"] == "RECONCILIATION_REQUIRED"
    shutil.copyfile(copy, store.path)
    with pytest.raises(ReviewRefused, match="checkpoint integrity/rollback"):
        store.recover(now=NOW)


def test_checkpoint_must_name_the_audit_head(tmp_path, case, monkeypatch):
    # Any committed change the checkpoint did not record is a rollback point.
    p, i = case
    store = store_at(tmp_path, inputs=i)
    store.queue(p, i, now=NOW)                    # checkpoint names this head
    second = p.model_copy(update={"proposal_id": "p2"})
    with monkeypatch.context() as m:
        m.setattr(store, "_persist_external_checkpoint", lambda db: None)
        store.queue(second, i, now=NOW)           # committed past the checkpoint
    with pytest.raises(ReviewRefused, match="checkpoint integrity/rollback"):
        store.approve(p.proposal_id, authenticated_owner="fixture-owner", now=NOW)
