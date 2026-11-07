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
