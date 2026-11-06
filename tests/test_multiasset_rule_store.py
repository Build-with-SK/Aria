from datetime import datetime, timedelta
from decimal import Decimal as D
from zoneinfo import ZoneInfo
import pytest

from test_multiasset_risk import case, NOW
from src.trading.contracts import BalanceSnapshot
from src.trading.review_store import ReviewRefused, ReviewStore


def test_daily_high_water_survives_restart_and_cannot_drop(tmp_path,case):
    _,i=case
    profile=i.profile.model_copy(update={"account_creation_date":datetime(2026,11,5).date()})
    first=BalanceSnapshot(account_id="demo-1",currency="USD",balance="10000",equity="9950",
        observed_at=datetime(2026,11,5,tzinfo=ZoneInfo("Europe/Prague")))
    store=ReviewStore(tmp_path/'rules.sqlite',clock=lambda: NOW)
    store.record_rule_boundary(profile,first,evidence_hash="a"*64,now=NOW,cashflow=D("0"))
    second=first.model_copy(update={"observed_at":first.observed_at+timedelta(days=1),"balance":D("10200")})
    store.record_rule_boundary(profile,second,evidence_hash="b"*64,now=NOW,cashflow=D("0"))
    restarted=ReviewStore(store.path,clock=lambda: NOW)
    state=restarted.rule_state(profile,equity=D("10100"),now=NOW)
    assert state.balance_high_water==D("10200") and state.equity==D("10100")
    for bad in (first,second):
        with pytest.raises(ReviewRefused):
            restarted.record_rule_boundary(profile,bad,evidence_hash="c"*64,now=NOW,cashflow=D("0"))
    import sqlite3
    with sqlite3.connect(str(store.path)) as db:
        db.execute("UPDATE rule_boundaries SET high_water='10000' WHERE day='2026-11-06'")
    with pytest.raises(ReviewRefused,match="audit evidence"):
        restarted.rule_state(profile,equity=D("10100"),now=NOW)


def test_no_guessed_baseline_and_no_cashflow_reset(tmp_path,case):
    _,i=case
    store=ReviewStore(tmp_path/'rules.sqlite',clock=lambda: NOW)
    snapshot=BalanceSnapshot(account_id="demo-1",currency="USD",balance="10000",equity="10000",observed_at=NOW)
    with pytest.raises(ReviewRefused): store.record_rule_boundary(i.profile,snapshot,evidence_hash="a"*64,now=NOW,cashflow=D("0"))
    with pytest.raises(ReviewRefused): store.rule_state(i.profile,equity=D("10000"),now=NOW)
