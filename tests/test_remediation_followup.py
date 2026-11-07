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
    s = store_at(tmp_path, inputs=case[1])
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
    s = store_at(tmp_path, inputs=case[1])
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
    s = store_at(tmp_path, inputs=case[1])
    s.queue(p, i, now=NOW)
    token = s.approve(p.proposal_id, authenticated_owner='fixture-owner', now=NOW)
    with sqlite3.connect(s.path) as db:
        db.execute(f'UPDATE approvals SET {field}=?', (value,))
    with pytest.raises(ReviewRefused, match='audit record'):
        s.reserve_intent(p, token, i, now=NOW)


def test_deleted_history_cannot_be_reseeded(tmp_path, case):
    s = store_at(tmp_path, inputs=case[1])
    profile = _trailing_profile(case)
    _record(s, profile, date(2026,11,1), '10000')
    _record(s, profile, date(2026,11,2), '10800')
    with sqlite3.connect(s.path) as db:
        db.execute('DELETE FROM rule_boundaries')
    with pytest.raises(ReviewRefused, match='audit evidence'):
        _record(s, profile, date(2026,11,1), '10000')

# Quantitative regressions from the remediation review.
import pandas as pd
from decimal import Decimal as D
from datetime import datetime, timezone
from test_review_findings import probe_frame, put, release, calendar
from src.backtesting.backtester import run_backtest
from src.backtesting.metrics import periods_per_year
from src.backtesting.challenge_replay import replay_path, EquityObservation
from src.trading.operational_rules import news_checks


@pytest.mark.parametrize('missing', ['column', 'nan'])
def test_missing_open_cannot_create_retroactive_fill(missing):
    df = put(probe_frame(), 1, High=111., Low=99., Close=110.)
    if missing == 'column':
        df = df.drop(columns=['Open'])
    else:
        df.loc[df.index[1], 'Open'] = float('nan')
    with pytest.raises(ValueError, match='Open prices required'):
        run_backtest('FIXTURE', df)


def test_final_curve_includes_initial_capital_and_exit_fees():
    r = run_backtest('FIXTURE', probe_frame(), commission_pct=.01, slippage_pct=0,
                     risk_per_trade=1, stop_loss_pct=.5)
    assert r.equity_curve.iloc[0] == 100000
    assert r.equity_curve.iloc[-1] == pytest.approx(100000 * .99 / 1.01)
    assert r.metrics['total_return'] == pytest.approx(-.0198, abs=.0001)
    assert r.metrics['max_drawdown'] < 0


def test_missing_range_value_is_reported():
    df = probe_frame()
    df.loc[df.index[1], 'Low'] = float('nan')
    assert 'missing High/Low' in run_backtest('FIXTURE', df).warning


def test_hourly_trade_records_keep_time():
    r = run_backtest('FIXTURE', probe_frame(freq='h'))
    assert 'T' in r.trades[0].entry_date and 'T' in r.trades[0].exit_date


def test_twenty_one_hour_crypto_bars_are_not_daily():
    idx = pd.date_range('2025-01-01', periods=100, freq='21h')
    assert periods_per_year(idx, 'CRYPTO') == pytest.approx(365*24/21)


def test_midnight_snapshot_sets_daily_floor(case):
    utc = timezone.utc
    observations = (
        EquityObservation(at=datetime(2026,11,6,12,tzinfo=utc), balance=10000, equity=10000),
        EquityObservation(at=datetime(2026,11,6,23,tzinfo=utc), balance=10400, equity=10400),
        EquityObservation(at=datetime(2026,11,7,0,tzinfo=utc), balance=10400, equity=9890))
    r = replay_path(case[1].profile, observations)
    assert r.outcome == 'DAILY_BREACH' and r.floor == D('9900')


def test_sparse_new_day_needs_reset_snapshot(case):
    observations = (
        EquityObservation(at=NOW, balance=10000, equity=10000),
        EquityObservation(at=NOW+timedelta(days=1), balance=10400, equity=10400))
    with pytest.raises(ValueError, match='exact reset balance'):
        replay_path(case[1].profile, observations)


def test_no_gap_rule_does_not_block_whole_news_day(case):
    profile = case[1].profile.model_copy(update={'news_rule':'NO_EXECUTION_2MIN_AROUND_NEWS', 'gap_rule':'NONE'})
    result = news_checks(profile, case[1].specification.instrument,
                         calendar(events=(release(NOW+timedelta(hours=1)),)), now=NOW, max_age_seconds=21600)
    assert result == ([], [])

# Trusted acquisition boundary: changing submitted evidence cannot change authority.
from test_multiasset_risk import snapshot_source, book, holding, activity
from test_review_findings import ftmo, on_profile, AUDIT_KEY
from src.trading.review_store import ReviewStore
from src.trading.snapshot_source import BoundRiskSource


def configured_store(tmp_path, inputs, **kwargs):
    return ReviewStore(tmp_path/'authority.sqlite', clock=lambda:NOW, audit_key=AUDIT_KEY,
                       evidence_source=snapshot_source(inputs), **kwargs)


def test_order_review_requires_configured_source(tmp_path,case):
    p,i=case
    s=ReviewStore(tmp_path/'unconfigured.sqlite',clock=lambda:NOW,audit_key=AUDIT_KEY)
    with pytest.raises(ReviewRefused,match='account-bound evidence source'):
        s.queue(p,i,now=NOW)


@pytest.mark.parametrize('which',['book','activity','midnight'])
def test_caller_cannot_replace_authoritative_account_evidence(tmp_path,case,which):
    p,i=case
    if which=='book':
        actual=i.model_copy(update={'book':book(holding('EURUSD'))})
    elif which=='activity':
        actual=i.model_copy(update={'activity':activity(server_requests=1600)})
    else:
        actual=i.model_copy(update={'rule_state':i.rule_state.model_copy(update={'daily_balance':D('10480')})})
    s=configured_store(tmp_path,actual)
    with pytest.raises(ReviewRefused,match='authoritative snapshot'):
        s.queue(p,i,now=NOW)
    assert s.inspect(p.proposal_id) is None


def test_paid_account_cannot_be_relabelled_by_submission(tmp_path,case):
    paid=ftmo(case)
    pp,pi=on_profile(case,paid)
    trial=ftmo(case,product='FTMO_FREE_TRIAL',evaluation_stage='TRIAL',account_class='PRACTICE')
    tp,ti=on_profile(case,trial)
    s=configured_store(tmp_path,pi)
    with pytest.raises(ReviewRefused,match='acquisition failed'):
        s.queue(tp,ti,now=NOW)


def test_calculator_provider_account_symbol_and_permission_are_bound(case):
    p,i=case
    changes=[{'account_id':'other'}, {'side':'SELL','stop_loss':D('1.2')},
             {'provenance':i.broker_calculation.provenance.model_copy(update={'provider':'other'})},
             {'provenance':i.broker_calculation.provenance.model_copy(update={'original_symbol':'GBPJPY'})},
             {'provenance':i.broker_calculation.provenance.model_copy(update={'permission':'RESEARCH'})}]
    for changed in changes:
        bad=i.model_copy(update={'broker_calculation':i.broker_calculation.model_copy(update=changed)})
        from src.trading.multiasset_risk import assess
        assert assess(p,bad,now=NOW).verdict == 'UNVERIFIED'
        with pytest.raises(ValueError):
            snapshot_source(bad).acquire(p,NOW)


def test_store_history_overrides_transport_reconstructed_state(tmp_path,case):
    from test_review_findings import _trailing_profile
    p,i=case
    profile=_trailing_profile(case)
    state=i.rule_state.model_copy(update={'equity':D('9850'),'daily_balance':D('9850'),
                                         'balance_high_water':D('10000')})
    actual=i.model_copy(update={'profile':profile, 'rule_state':state,
                               'account':i.account.model_copy(update={'equity':D('9850'),'balance':D('9850')})})
    p=p.model_copy(update={'rule_profile_hash':profile.profile_hash})
    s=configured_store(tmp_path,actual)
    for day,balance in enumerate(['10000','10800','10700','10600','10500','10450'],1):
        _record(s,profile,date(2026,11,day),balance)
    with pytest.raises(ReviewRefused,match='EMERGENCY_HALT'):
        s.queue(p,actual,now=NOW)


def test_reservation_reacquires_source_and_cannot_use_cached_book(tmp_path,case):
    p,i=case
    current=[i]; reads=[]
    def reader(proposal,now):
        reads.append(proposal.proposal_id)
        return current[0]
    binding=BoundRiskSource(i.account.account_id,'fixture',i.profile,reader)
    s=ReviewStore(tmp_path/'fresh.sqlite',clock=lambda:NOW,audit_key=AUDIT_KEY,evidence_source=binding)
    s.queue(p,i,now=NOW)
    token=s.approve(p.proposal_id,authenticated_owner='fixture-owner',now=NOW)
    current[0]=i.model_copy(update={'book':book(holding('EURUSD'))})
    with pytest.raises(ReviewRefused,match='authoritative snapshot'):
        s.reserve_intent(p,token,i,now=NOW)
    assert len(reads)==2


def test_reopening_same_account_under_new_registration_is_refused(tmp_path,case):
    p,i=case
    s=configured_store(tmp_path,i)
    s.queue(p,i,now=NOW)
    modified=i.profile.model_copy(update={'policy_version':'different'})
    j=i.model_copy(update={'profile':modified})
    other=configured_store(tmp_path,j)
    changed=p.model_copy(update={'proposal_id':'p2','rule_profile_hash':modified.profile_hash})
    with pytest.raises(ReviewRefused,match='registration changed'):
        other.queue(changed,j,now=NOW)
