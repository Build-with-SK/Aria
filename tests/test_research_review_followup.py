"""Source authority and strategy conflict regressions (RQ-01/RQ-02)."""
from datetime import timedelta
from decimal import Decimal as D
import pytest
from test_multiasset_risk import case, NOW
from test_multiasset_research import candles, agreeing_candles
from src.trading.contracts import ResearchEvidence
from src.research.multiasset import analyse, triggers_on_latest_bar
from src.research.source_resolver import SourceResolver, MAX_BYTES


def claim(case):
    return ResearchEvidence(evidence_id='fixture-claim', claim='ECB will cut rates 200bp tomorrow (fabricated)',
        classification='VERIFIED_EXTERNAL_FACT', provenance=case[1].quote.provenance.model_copy(
            update={'source_url':'https://source.invalid/release','permission':'RESEARCH'}))


def test_rq01_client_fact_never_becomes_support(case):
    e = claim(case)
    h = analyse(case[1].specification.instrument, agreeing_candles(case), now=NOW, regime='TREND', evidence=(e,))
    assert h.client_evidence[0].classification == 'UNVERIFIED'
    assert e.evidence_id not in {x.evidence_id for x in h.supporting_evidence}


def test_rq01_server_resolver_requires_fetched_claim_and_retains_hash(case):
    e = claim(case)
    resolver = SourceResolver(allowed_hosts=['source.invalid'], fetch=lambda url:e.claim.encode(), clock=lambda:NOW)
    verified = resolver.resolve(e)
    assert verified.classification == 'VERIFIED_EXTERNAL_FACT'
    assert resolver.record(e.evidence_id).content_sha256
    assert not resolver.verified(verified.model_copy(update={'claim':'invented'}), as_of=NOW)
    assert not resolver.verified(verified, as_of=NOW-timedelta(seconds=1))
    h = analyse(case[1].specification.instrument, agreeing_candles(case), now=NOW, regime='TREND',
                evidence=(verified,), sources=resolver)
    assert verified in h.supporting_evidence


@pytest.mark.parametrize('url', ['http://source.invalid/x','https://other.invalid/x',
                                 'https://user:password@source.invalid/x','https://source.invalid:444/x'])
def test_rq01_unapproved_source_is_never_fetched(case,url):
    def refuse(_):
        raise AssertionError('must not fetch')
    resolver = SourceResolver(allowed_hosts=['source.invalid'],fetch=refuse,clock=lambda:NOW)
    e=claim(case); e=e.model_copy(update={'provenance':e.provenance.model_copy(update={'source_url':url})})
    assert resolver.resolve(e).classification == 'UNVERIFIED'


@pytest.mark.parametrize('body', [b'No matching statement', b'x'*(MAX_BYTES+1)], ids=['missing','oversize'])
def test_rq01_missing_or_oversize_source_verifies_nothing(case,body):
    resolver=SourceResolver(allowed_hosts=['source.invalid'],fetch=lambda url:body,clock=lambda:NOW)
    assert resolver.resolve(claim(case)).classification == 'UNVERIFIED'


def test_rq02_conflict_abstains_and_lists_opposite_evidence(case):
    h=analyse(case[1].specification.instrument,candles(case),now=NOW,regime='TREND')
    assert h.decision_status == 'ABSTAIN'
    assert any('SELL' in e.claim for e in h.contradicting_evidence)


def test_rq02_break_below_support_is_never_a_bounce(case):
    import math
    bars=[]
    for n, b in enumerate(candles(case)):
        price=D('1.1000')+D(str(round(.001*math.sin(n/3),5)))
        closed=D('1.0950') if n==59 else price
        bars.append(b.model_copy(update={'open':price,'close':closed,
            'high':max(price,closed)+D('.0003'),'low':min(price,closed)-D('.0003')}))
    directions=triggers_on_latest_bar(case[1].specification.instrument,tuple(bars),now=NOW)
    assert directions['SupportResistance'] != 'BUY'
    assert directions['Breakout'] == 'SELL'
    for family,regime in [('SupportResistance','RANGE'),('Breakout','TREND'),('MeanReversion','RANGE')]:
        h=analyse(case[1].specification.instrument,tuple(bars),now=NOW,family=family,regime=regime)
        assert h.decision_status == 'ABSTAIN'

