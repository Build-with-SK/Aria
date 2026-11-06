from datetime import timedelta
from decimal import Decimal as D
import pytest

from test_multiasset_risk import case, NOW
from src.trading.contracts import OHLCVBar
from src.research.multiasset import (FixtureProvider, analyse, technical_evidence,
    validate_bars, strategy_registry, universe_permissions)
from src.backtesting.experiments import (ExperimentManifest, LabelInterval,
    chronological_split, dataset_hash, RealisedTrade, realised_metrics, block_bootstrap)


def candles(case,count=60):
    _,i=case
    bars=[]
    for j in range(count):
        close=NOW-timedelta(hours=count-j)
        price=D("1.1")+D(j)*D("0.001")
        provenance=i.quote.provenance.model_copy(update={"provider":"fixture","permission":"RESEARCH",
              "observed_at":close,"retrieved_at":close})
        bars.append(OHLCVBar(opens_at=close-timedelta(hours=1),closes_at=close,
            open=price,high=price+D("0.002"),low=price-D("0.002"),close=price+D("0.001"),
            volume=None,volume_type="UNAVAILABLE",completed=True,adjustment_basis="raw",provenance=provenance))
    return tuple(bars)


def test_completed_only_technical_and_existing_summary(case):
    _,i=case
    bars=candles(case)
    output=technical_evidence(i.specification.instrument,bars,now=NOW)
    assert output["status"]=="AVAILABLE" and output["completed_bars"]==60
    assert output["vwap_status"]=="UNSUPPORTED" and output["order_book_status"]=="UNAVAILABLE"
    import pandas as pd
    expected=pd.Series([float(b.close) for b in bars]).rolling(20).mean().iloc[-1]
    assert abs(float(output["indicators"]["SMA20"])-expected)<1e-12
    future=bars[-1].model_copy(update={"opens_at":NOW,"closes_at":NOW+timedelta(hours=1),"completed":False})
    assert technical_evidence(i.specification.instrument,bars+(future,),now=NOW)==output


def test_bar_identity_source_basis_and_duplicates_refused(case):
    _,i=case; instrument=i.specification.instrument; bars=candles(case)
    for changed in (bars+(bars[-1],),tuple(reversed(bars)),
         bars[:-1]+(bars[-1].model_copy(update={"adjustment_basis":"adjusted"}),),
         bars[:-1]+(bars[-1].model_copy(update={"provenance":bars[-1].provenance.model_copy(update={"original_symbol":"other"})}),)):
        with pytest.raises(ValueError): validate_bars(instrument,changed,now=NOW)


def test_fixture_never_executes_or_invents_unavailable_news(case):
    _,i=case
    with pytest.raises(ValueError): FixtureProvider(quotes=(i.quote,))
    provider=FixtureProvider(bars=candles(case))
    assert len(provider.bars(i.specification.instrument))==60
    assert provider.quotes(i.specification.instrument) is None
    assert provider.health()["execution_usable"] is False


def test_strategies_abstain_for_missing_data_and_unknown_regime(case):
    _,i=case
    registry=strategy_registry()
    assert len(registry)==10 and len({s.strategy_id for s in registry})==10
    for s in registry:
        h=analyse(i.specification.instrument,(),now=NOW,family=s.family)
        assert h.decision_status=="ABSTAIN" and h.missing_information
        assert h.probability_status=="UNCALIBRATED" and h.estimated_probability_if_calibrated is None
    h=analyse(i.specification.instrument,candles(case),now=NOW,regime="TREND")
    assert h.decision_status=="APPROVE_FOR_HUMAN_REVIEW"
    assert h.supporting_evidence and h.alternative_scenarios
    assert h.planned_risk==0 and h.net_reward_risk is None
    assert not hasattr(h,"order")


def test_every_universe_permission_required():
    result=universe_permissions("x",research={"x"},provider={"x"},broker={"x"},account=set())
    assert not result["all_universes_permit"] and not result["execution_authorised"]


def test_split_purges_overlapping_labels_and_embargoes():
    rows=tuple(LabelInterval(row_id=str(j),starts_at=NOW+timedelta(days=j),ends_at=NOW+timedelta(days=j+2)) for j in range(12))
    splits=chronological_split(rows,validation_at=NOW+timedelta(days=5),test_at=NOW+timedelta(days=9),embargo=timedelta(days=1))
    assert splits["train"]==["0","1"]
    assert splits["validation"]==[]
    assert splits["test"]==["10","11"]
    assert not set(splits["train"])&set(splits["test"])


def test_costs_reproducible_bootstrap_and_no_ruin_probability():
    trade=RealisedTrade(gross_pnl="10",spread="2",commission="1",slippage="2",financing="1",initial_capital="1000")
    assert trade.net_pnl==D("4")
    assert realised_metrics((trade,))["net_pnl"]=="4"
    assert realised_metrics((trade,))["risk_of_ruin"] is None
    a=block_bootstrap((D("1"),D("-2"),D("3"),D("-1")),block_size=2,samples=100,seed=7)
    assert a==block_bootstrap((D("1"),D("-2"),D("3"),D("-1")),block_size=2,samples=100,seed=7)
    m=ExperimentManifest(strategy_id="fixture",strategy_version="1",code_revision="58f5f25",
        dataset_sha256=dataset_hash(b"fixture"),parameters_json="{}",costs_json="{}",seed=7,
        created_at=NOW,assumptions=("fixture only",))
    assert m.manifest_hash!=m.model_copy(update={"costs_json":'{"slippage":1}'}).manifest_hash
