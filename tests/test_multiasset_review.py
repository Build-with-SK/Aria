from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import sqlite3

import pytest

from test_multiasset_risk import case, NOW, snapshot_source
from src.trading.multiasset_risk import assess
from src.trading.review_store import ReviewRefused, ReviewStore, State, validate_transition


KEY = b"fixture-review-audit-key-32-bytes"  # held outside the database (SEC-01)


def clock():
    return NOW


def queued(tmp_path,case):
    """Queue and approve the fixture; the store assesses the evidence itself."""
    p,i=case
    store=ReviewStore(tmp_path/'review.sqlite',clock=clock,audit_key=KEY,evidence_source=snapshot_source(case[1]))
    risk=store.queue(p,i,now=NOW)
    assert risk==assess(p,i,now=NOW,policy=ReviewStore.POLICY)
    token=store.approve(p.proposal_id,authenticated_owner="fixture-owner",now=NOW)
    return store,p,i,token


def test_atomic_single_use_and_restart_recovery(tmp_path,case):
    store,p,i,token=queued(tmp_path,case)
    intent=store.reserve_intent(p,token,i,now=NOW)
    assert len(intent)==64
    with pytest.raises(ReviewRefused):
        store.reserve_intent(p,token,i,now=NOW)
    restarted=ReviewStore(store.path,clock=clock,audit_key=KEY)
    assert restarted.recover(now=NOW)==1
    assert restarted.recover(now=NOW)==0
    assert restarted.inspect(p.proposal_id)["state"] == "RECONCILIATION_REQUIRED"
    assert len(restarted.verify_audit())==64


def test_racing_store_connections_reserve_once(tmp_path,case):
    store,p,i,token=queued(tmp_path,case)
    def reserve(_):
        try:
            return ReviewStore(store.path,clock=clock,audit_key=KEY,evidence_source=snapshot_source(i)).reserve_intent(p,token,i,now=NOW)
        except ReviewRefused:
            return None
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sum(x is not None for x in pool.map(reserve,range(4)))==1


@pytest.mark.parametrize("field,value", [("account_id","other"),("instrument_id","other"),
    ("market_snapshot_id","other"),("rule_profile_hash","changed"),
    ("hypothesis_id","other")])
def test_approval_payload_substitution_denied(tmp_path,case,field,value):
    store,p,i,token=queued(tmp_path,case)
    changed=p.model_copy(update={field:value})
    with pytest.raises(ReviewRefused) as refused:
        store.reserve_intent(changed,token,i,now=NOW)
    if field=="hypothesis_id":
        # The engine does not read the hypothesis id, so this one reaches the
        # store's own binding check rather than an engine refusal.
        assert "substitution" in str(refused.value)
    assert store.inspect(p.proposal_id)["state"]=="APPROVED"


def test_expiry_policy_change_and_wrong_token(tmp_path,case,monkeypatch):
    store,p,i,token=queued(tmp_path,case)
    with pytest.raises(ReviewRefused,match="invalid approval"):
        store.reserve_intent(p,"bad",i,now=NOW)
    later=NOW+timedelta(seconds=30)
    with pytest.raises(ReviewRefused):
        ReviewStore(store.path,clock=lambda: later,audit_key=KEY,evidence_source=snapshot_source(i)).reserve_intent(p,token,i,now=later)
    from src.trading.multiasset_risk import ResearchRiskPolicy
    stricter=ResearchRiskPolicy(ordinary_fraction="0.004")
    monkeypatch.setattr(ReviewStore,"POLICY",stricter)
    with pytest.raises(ReviewRefused,match="policy"):
        store.reserve_intent(p,token,i,now=NOW)
    assert store.inspect(p.proposal_id)["state"]=="APPROVED"


def test_audit_tamper_refused_and_backup_restore(tmp_path,case):
    store,p,i,token=queued(tmp_path,case)
    backup=tmp_path/'backup.sqlite'
    digest=store.verify_audit()
    store.backup(backup)
    assert ReviewStore(backup,clock=clock,audit_key=KEY).verify_audit()==digest
    with sqlite3.connect(str(store.path)) as db:
        db.execute("UPDATE audit SET body='tampered' WHERE sequence=1")
    with pytest.raises(ReviewRefused,match="integrity"):
        store.reserve_intent(p,token,i,now=NOW)


def test_transaction_failure_does_not_consume_approval(tmp_path,case,monkeypatch):
    store,p,i,token=queued(tmp_path,case)
    original=store._event
    def crash(*args,**kwargs):
        raise RuntimeError("fixture crash before commit")
    monkeypatch.setattr(store,"_event",crash)
    with pytest.raises(RuntimeError):
        store.reserve_intent(p,token,i,now=NOW)
    assert store.inspect(p.proposal_id)["state"]=="APPROVED"
    monkeypatch.setattr(store,"_event",original)
    assert store.reserve_intent(p,token,i,now=NOW)


def test_terminal_states_cannot_return_to_approved():
    for state in (State.FILLED,State.CANCELLED,State.REJECTED,State.EXPIRED):
        with pytest.raises(ReviewRefused):
            validate_transition(state,State.APPROVED)
    validate_transition(State.SUBMITTED,State.PARTIALLY_FILLED)
    validate_transition(State.UNKNOWN_STATUS,State.RECONCILIATION_REQUIRED)
