"""Current-API fixture reproductions of the original independent review scenarios.

Run with the repository's Python from any folder. No broker/network connection.
Original probes remain frozen for their reviewed 7385130 API.
"""
import json
from pathlib import Path
import sys
import tempfile
from datetime import date
from decimal import Decimal as D

ROOT=Path(__file__).resolve().parents[3]
sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
import pytest
from test_multiasset_risk import case, NOW, snapshot_source
from test_multiasset_research import candles
from test_review_findings import (twenty_dollar_trade,metal_or_index_case,eurgbp,gbpusd,ftmo,on_profile,
                                  _trailing_profile,_record)
from src.trading.contracts import AssetClass,RiskAssessment,content_hash,ResearchEvidence
from src.trading.multiasset_risk import assess
from src.trading.review_store import ReviewStore,ReviewRefused
from src.research.multiasset import analyse


def main():
    out={'scope':'FIXTURES ONLY; no current prices or broker activity','clock':NOW.isoformat()}
    with tempfile.TemporaryDirectory() as directory, pytest.MonkeyPatch.context() as mp:
        c=case.__wrapped__(mp,Path(directory)); p,i=c
        def show(name,pair):
            r=assess(*pair,now=NOW)
            out[name]={'verdict':r.verdict,'risk':str(r.monetary_risk),'reasons':r.reasons}
        show('E1_EURUSD_20_dollars',twenty_dollar_trade(c,equity='10000',daily_balance='10000'))
        show('E5b_wrong_gold_contract',metal_or_index_case(c,symbol='XAUUSD',asset_class=AssetClass.METALS,
             base='XAU',contract='1',quantity='10',bid='2600',ask='2600',stop='2595',target='2610',
             broker_one_lot_loss='500'))
        show('E3b_inverted_GBPUSD',eurgbp(c,gbpusd(c,'0.7874','0.7874'),'0.20'))
        show('E7_exact_floor',twenty_dollar_trade(c,equity='9020',daily_balance='9020'))
        show('E8_floating_daily_loss',twenty_dollar_trade(c,equity='9520',daily_balance='10000'))
        store=ReviewStore(Path(directory)/'reviews.sqlite',clock=lambda:NOW,audit_key=b'fixture-audit-key-32-bytes-example',
                          evidence_source=snapshot_source(i),anchor_path=Path(directory)/'external.anchor')
        forged=RiskAssessment(verdict='ALLOW',reasons=('forged',),policy_hash='0'*64,proposal_hash=content_hash(p),
              assessed_at=NOW,expires_at=p.expires_at,quantity=p.quantity,monetary_risk=1)
        try: store.queue(p,forged,now=NOW); out['R1_forged_allow']='UNSAFE_ACCEPTED'
        except ReviewRefused: out['R1_forged_allow']='REFUSED'
        profile=_trailing_profile(c)
        for day,balance in enumerate(['10000','10400','10800','10600','10500','10450'],1):
            _record(store,profile,date(2026,11,day),balance)
        renewed=profile.model_copy(update={'policy_version':'renewed'})
        state=store.rule_state(renewed,equity=D('10450'),now=NOW)
        out['R2_high_water_after_reversion']=str(state.balance_high_water)
        fake=ResearchEvidence(evidence_id='fake',claim='Fabricated external forecast',classification='VERIFIED_EXTERNAL_FACT',
                              provenance=i.quote.provenance)
        h=analyse(i.specification.instrument,candles(c),now=NOW,regime='TREND',evidence=(fake,))
        out['S_client_fact']=h.client_evidence[0].classification
        out['S_conflicting_triggers']=h.decision_status
    print(json.dumps(out,indent=2))


if __name__=='__main__':
    main()
