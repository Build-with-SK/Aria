"""Additional invariants reproduced by the remediation reviewer at dac263f."""
import hashlib
import sqlite3
from datetime import date, timedelta

import pytest
from test_multiasset_risk import case, NOW
from test_review_findings import store_at, _trailing_profile, _record
from src.trading.review_store import ReviewRefused


def test_database_writer_cannot_invent_approval(tmp_path, case):
    p, i = case
    s = store_at(tmp_path)
    s.queue(p, i, now=NOW)
    token = 'fixture-forged-token'
    with sqlite3.connect(s.path) as db:
        db.execute('UPDATE proposals SET state=?', ('APPROVED',))
        db.execute('INSERT INTO approvals VALUES(?,?,?,?,?,?)',
                   ('fake', p.proposal_id, hashlib.sha256(token.encode()).hexdigest(),
                    'fake-owner', (NOW + timedelta(seconds=20)).isoformat(), 0))
    with pytest.raises(ReviewRefused, match='audit record'):
        s.reserve_intent(p, token, i, now=NOW)


def test_database_writer_cannot_replay_consumed_token(tmp_path, case):
    p, i = case
    s = store_at(tmp_path)
    s.queue(p, i, now=NOW)
    token = s.approve(p.proposal_id, authenticated_owner='fixture-owner', now=NOW)
    s.reserve_intent(p, token, i, now=NOW)
    with sqlite3.connect(s.path) as db:
        db.execute('DELETE FROM intents')
        db.execute('UPDATE approvals SET consumed=0')
        db.execute('UPDATE proposals SET state=?', ('APPROVED',))
    with pytest.raises(ReviewRefused, match='audit record'):
        s.reserve_intent(p, token, i, now=NOW)


@pytest.mark.parametrize('field,value', [('token_hash','0'*64),('actor','changed'),
                                         ('expires_at',(NOW+timedelta(days=1)).isoformat())])
def test_database_writer_cannot_edit_approval_fields(tmp_path, case, field, value):
    p, i = case
    s = store_at(tmp_path)
    s.queue(p, i, now=NOW)
    token = s.approve(p.proposal_id, authenticated_owner='fixture-owner', now=NOW)
    with sqlite3.connect(s.path) as db:
        db.execute(f'UPDATE approvals SET {field}=?', (value,))
    with pytest.raises(ReviewRefused, match='audit record'):
        s.reserve_intent(p, token, i, now=NOW)


def test_deleted_history_cannot_be_reseeded(tmp_path, case):
    s = store_at(tmp_path)
    profile = _trailing_profile(case)
    _record(s, profile, date(2026,11,1), '10000')
    _record(s, profile, date(2026,11,2), '10800')
    with sqlite3.connect(s.path) as db:
        db.execute('DELETE FROM rule_boundaries')
    with pytest.raises(ReviewRefused, match='audit evidence'):
        _record(s, profile, date(2026,11,1), '10000')
