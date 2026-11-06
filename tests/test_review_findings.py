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

from test_multiasset_risk import case, NOW
from src.trading.contracts import RiskAssessment, content_hash
from src.trading.multiasset_risk import assess
from src.trading.review_store import ReviewRefused, ReviewStore


def clock():
    return NOW


def store_at(tmp_path, name="review.sqlite"):
    return ReviewStore(tmp_path / name, clock=clock)


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
    ({"kill_switch_clear": False}, "EMERGENCY_HALT"),
    ({"spread_cost": D("5")}, "authorised risk changed"),
])
def test_cr02_reservation_reassesses_fresh_evidence(tmp_path, case, change, why):
    p, i = case
    store = store_at(tmp_path)
    store.queue(p, i, now=NOW)
    token = store.approve(p.proposal_id, authenticated_owner="fixture-owner", now=NOW)
    with pytest.raises(ReviewRefused, match=why):
        store.reserve_intent(p, token, i.model_copy(update=change), now=NOW)
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
