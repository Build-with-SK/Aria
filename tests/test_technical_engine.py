"""Offline calculations, provider identity, timeframe integrity and consultation."""
import json
import numpy as np
import pandas as pd
import pytest

from src.data.technical_engine import (TIMEFRAMES, aggregate_five_hours, classify,
    closed_candles, consensus, score_candles, timeframe_conflicts)
from src.data.technical_recommendations import analyze, analyze_all, instrument
from src.backtesting.technical_replay import replay


def candles(n=260, direction=1, freq="h", start="2026-01-01", flat=False):
    c = np.full(n, 100.) if flat else 100 + direction * np.arange(n) * .1
    return pd.DataFrame({"Open": c-.02, "High": c+.2, "Low": c-.2,
                         "Close": c, "Volume": np.full(n, 1000.)},
                        index=pd.date_range(start, periods=n, freq=freq, tz="UTC"))


@pytest.mark.parametrize("value,label", [(1,"Strong Buy"),(.3,"Buy"),(0,"Neutral"),(-.3,"Sell"),(-1,"Strong Sell")])
def test_five_buckets(value,label):
    assert classify(value) == label


def test_neutral_votes_are_not_discarded():
    votes = {str(i): {"signal": "neutral"} for i in range(9)}
    votes["buy"] = {"signal": "buy"}
    assert consensus(votes)["label"] == "Neutral"


@pytest.mark.parametrize("direction,label", [(1,"Strong Buy"),(-1,"Strong Sell")])
def test_known_linear_indicators(direction,label):
    df = candles(direction=direction)
    result = score_candles(df)
    assert result["summary"] == label
    assert result["moving_average_values"]["SMA20"]["value"] == pytest.approx(df.Close.iloc[-20:].mean())
    expected_ema = df.Close.ewm(span=21,adjust=False).mean().iloc[-1]
    assert result["moving_average_values"]["EMA21"]["value"] == pytest.approx(expected_ema)
    assert result["indicator_values"]["RSI14"]["value"] == pytest.approx(100 if direction > 0 else 0)
    assert result["indicator_values"]["ADX14"]["value"] == pytest.approx(100, abs=.01)
    assert result["volatility"]["atr14"] == pytest.approx(.4)
    expected = (df.Close.iloc[-1]/df.Close.iloc[-13]-1)*100
    assert result["indicator_values"]["ROC12"]["value"] == pytest.approx(expected)
    assert result["evidence"]["probability"] is None
    json.dumps(result, allow_nan=False)


def test_flat_market_is_neutral():
    result = score_candles(candles(flat=True))
    assert result["summary"] == "Neutral"
    assert result["indicator_values"]["RSI14"]["value"] == 50
    assert all(v["signal"] == "neutral" for v in result["moving_average_values"].values())


def test_price_equality_and_band_are_neutral():
    df=candles(flat=True)
    assert score_candles(df)["moving_averages"]["SMA200"] == "neutral"


def test_close_excludes_current_bar():
    df=candles()
    closed,f=closed_candles(df,"1h",df.index[-1]+pd.Timedelta(minutes=30))
    assert len(closed)==259 and f["open_bars_excluded"]==1
    assert f["age_seconds"]==1800


@pytest.mark.parametrize("bad", ["duplicate","reverse","naive","nan","infinity","range","negative_volume","future","wrong_interval"])
def test_bad_data_is_rejected(bad):
    df=candles()
    now=df.index[-1]+pd.Timedelta(hours=1)
    if bad=="duplicate": df.index=list(df.index[:-1])+[df.index[-2]]
    if bad=="reverse": df=df.iloc[::-1]
    if bad=="naive": df.index=df.index.tz_localize(None)
    if bad=="nan": df.iloc[-2,3]=np.nan
    if bad=="infinity": df.iloc[-2,3]=np.inf
    if bad=="range": df.iloc[-2,1]=0
    if bad=="negative_volume": df.iloc[-2,4]=-1
    if bad=="future": now=df.index[-1]-pd.Timedelta(hours=1)
    if bad=="wrong_interval": df.index=pd.date_range('2026-01-01',periods=len(df),freq='5min',tz='UTC')
    with pytest.raises(ValueError): closed_candles(df,"1h",now)


def test_stale_history_is_not_a_current_signal():
    df=candles()
    r=analyze('AAPL','1h',loader=lambda *_:(df,{}),now=df.index[-1]+pd.Timedelta(days=3))
    assert r['data_stale'] and r['summary'] is None
    assert r['calculated_summary']=='Strong Buy'


def test_warmup_and_missing_data():
    df=candles(100)
    r=analyze('AAPL','1h',loader=lambda *_:(df,{}),now=df.index[-1]+pd.Timedelta(hours=1))
    assert r['summary'] is None and not r['evidence']['warmup_complete']
    assert analyze('AAPL','1h',loader=lambda *_:(None,{}))['error']


def test_unsupported_timeframe_never_fetches_daily():
    def forbidden(*_): raise AssertionError('must not fetch')
    assert 'unsupported' in analyze('AAPL','2h',loader=forbidden)['error']


def test_exact_contract_and_alias():
    assert instrument('GCZ6')['provider_symbol']=='GCZ26.CMX'
    assert instrument('GCZ6')['kind']=='dated_future'
    assert instrument('GC=F')['kind']=='continuous_future'
    with pytest.raises(ValueError): instrument('GCZ7')


def test_provider_cannot_substitute_future(monkeypatch):
    import sys
    from types import SimpleNamespace
    from src.data.technical_recommendations import load_frame
    class Fake:
        history_metadata={'symbol':'GC=F','exchangeName':'CMX'}
        def history(self,**kwargs): return candles()
    monkeypatch.setitem(sys.modules,'yfinance',SimpleNamespace(Ticker=lambda _:Fake()))
    with pytest.raises(ValueError,match='not confirmed'): load_frame(instrument('GCZ6'),'1h')


@pytest.mark.parametrize('meta', [
    {'symbol':'GCZ26.CMX','exchangeName':'CMX'},
    {'symbol':'GCZ26.CMX','exchangeName':'NYM','expireDate':1798502400},
    {'symbol':'GCZ26.CMX','exchangeName':'CMX','expireDate':1700000000},
])
def test_missing_expiry_exchange_or_wrong_month(monkeypatch,meta):
    import sys
    from types import SimpleNamespace
    from src.data.technical_recommendations import load_frame
    class Fake:
        history_metadata=meta
        def history(self,**kwargs): return candles()
    monkeypatch.setitem(sys.modules,'yfinance',SimpleNamespace(Ticker=lambda _:Fake()))
    with pytest.raises(ValueError): load_frame(instrument('GCZ6'),'1h')


def test_5h_is_hourly_aggregation_with_no_partial_bins():
    df=candles(300)
    out=aggregate_five_hours(df,df.index[-1]+pd.Timedelta(hours=1))
    assert 58 <= len(out) <= 60
    start=out.index[0]; source=df.loc[start:start+pd.Timedelta(hours=4)]
    assert out.iloc[0].Close==source.iloc[-1].Close
    assert out.iloc[0].Volume==5000
    broken=aggregate_five_hours(df.drop(source.index[2]),df.index[-1]+pd.Timedelta(hours=1))
    assert start not in broken.index


def test_weekly_monthly_alignment_and_minute_case():
    assert '1m' in TIMEFRAMES and '1M' in TIMEFRAMES
    df=candles(260,freq='W-MON')
    assert len(closed_candles(df,'1wk',df.index[-1]+pd.Timedelta(days=8))[0])==260
    with pytest.raises(ValueError): closed_candles(df,'1M',df.index[-1]+pd.Timedelta(days=40))
    monthly=candles(210,freq='MS',start='2000-01-01')
    closed,_=closed_candles(monthly,'1M',monthly.index[-1]+pd.DateOffset(months=1))
    assert len(closed)==210


def test_conflict_is_not_averaged_into_direction():
    rows={'1h':{'summary':'Strong Buy'},'1d':{'summary':'Strong Sell'}}
    assert timeframe_conflicts(rows)[0]['higher']=='1d'
    assert not timeframe_conflicts({'1h':{'summary':'Strong Buy'},'1d':{'summary':'Strong Sell','data_stale':True}})
    def load(_,tf):
        df=candles(direction=1 if tf=='1h' else -1,freq='h' if tf=='1h' else 'D',start='2025-01-01')
        # Give both histories the same as-of, with full coverage.
        df.index=pd.date_range(end='2026-10-14',periods=len(df),freq='h' if tf=='1h' else 'D',tz='UTC')
        return df,{}
    report=analyze_all('AAPL',['1h','1d'],loader=load,now='2026-10-15T00:00:00Z')
    # Hourly feed is deliberately stale; cannot claim agreement or a present conflict.
    assert report['consensus'] is None
    assert report['sentinel']['status']=='NOT_CONSULTED'


def test_consultation_schema_preserves_disagreement(monkeypatch):
    from src.consult import sentinel_client as sc
    monkeypatch.setattr(sc,'_post',lambda *a,**k:{'ok':True,'data':{'position':'DISAGREE','counter_arguments':['Daily trend is bearish']}})
    r=sc.consult('Review hourly and daily evidence',context=json.dumps({'1h':'Strong Buy','1d':'Strong Sell'}))
    assert r['ok'] and r['sentinel_position']=='DISAGREE'
    monkeypatch.setattr(sc,'_post',lambda *a,**k:{'ok':False,'reason':'timeout'})
    r=sc.consult('Review evidence')
    assert not r['ok'] and r['sentinel_position']=='NONE'
    assert 'NOT agreement' in r['guidance']


def test_existing_endpoint_returns_full_evidence(monkeypatch):
    import src.data.technical_recommendations as adapter
    import src.data.technical_summary as public
    df=candles()
    monkeypatch.setattr(adapter,'load_frame',lambda *a,**k:(df,{}))
    report=public.multi_timeframe('AAPL',['1h','bad'])
    assert 'indicator_values' in report['timeframes']['1h']
    assert report['timeframes']['bad']['error']
    assert report['consensus'] is None


def test_causal_replay_costs_and_rollover():
    a=candles(205,freq='D',start='2024-01-01')
    a['Contract']='GCZ26.CMX'; a['Expiration']='2026-12-28T00:00:00Z'
    b=candles(205,freq='D',start='2024-09-01'); b['Contract']='GCG27.CMX'; b['Expiration']='2027-02-25T00:00:00Z'
    df=pd.concat([a,b])
    args=dict(fee_per_side=2.5,slippage_ticks=1,tick_size=.1,multiplier=100)
    r=replay(df,**args)
    assert len(r['trades'])==10 and r['contract_switches']==1
    assert r['fees_and_slippage']==250
    assert all(t['decision_bar'] < t['at'] for t in r['trades'])
    mutated=df.copy(); mutated.loc[mutated.index[-1],'Close']-=.1
    newer=replay(mutated,**args)
    assert [(t['at'],t['direction']) for t in r['trades']]==[(t['at'],t['direction']) for t in newer['trades']]
    assert replay(df.iloc[:203],**args)['trades']==r['trades'][:3]
    with pytest.raises(ValueError): replay(df.drop(columns=['Contract']),**args)


@pytest.mark.parametrize('fee,slip', [(-1,1),(1,-1),(float('nan'),1)])
def test_replay_rejects_invalid_costs(fee,slip):
    with pytest.raises(ValueError): replay(candles(),fee_per_side=fee,slippage_ticks=slip,tick_size=.1,multiplier=100)


def test_all_nine_timeframes_and_real_conflict(monkeypatch):
    from src.data import technical_summary
    monkeypatch.setattr(technical_summary, "_resolve_yahoo", lambda symbol: symbol)
    now = pd.Timestamp("2026-10-15T12:00:00Z")
    def load(_,tf):
        freq = {"1m":"min","5m":"5min","15m":"15min","30m":"30min","1h":"h","5h":"h","1d":"D","1w":"W-MON","1M":"MS"}[tf]
        end = {"1d":"2026-10-14", "1w":"2026-10-05", "1M":"2026-09-01"}.get(tf, "2026-10-15T11:00:00Z")
        n=1300 if tf=="5h" else 260
        if tf in ("1m","5m","15m","30m","1h"):
            end=now-pd.Timedelta(minutes={"1m":1,"5m":5,"15m":15,"30m":30,"1h":60}[tf])
        df=candles(n=n,direction=-1 if tf in ("1d","1w") else 1)
        df.index=pd.date_range(end=end,periods=n,freq=freq,tz="UTC")
        return df,{"provider":"synthetic fixture"}
    # This synthetic hourly fixture trades around the clock. Session-traded
    # equity fixtures must instead use their venue's actual hourly grid.
    report=analyze_all("BTC-USD",loader=load,now=now)
    assert len(report["timeframes"])==9
    assert all(r.get("summary") for r in report["timeframes"].values()), {tf: r.get("error") for tf, r in report["timeframes"].items() if not r.get("summary")}
    assert report["consensus"] is None
    assert any(c["lower"]=="1h" and c["higher"]=="1d" for c in report["conflicts"])
    assert report["timeframes"]["1h"]["summary"]=="Strong Buy"
    assert report["timeframes"]["1d"]["summary"]=="Strong Sell"
    json.dumps(report,allow_nan=False)


def test_v5_does_not_vote_with_stale_diagnostics(monkeypatch):
    from src.v5.modules import price
    import src.data.technical_summary as api
    monkeypatch.setattr(price.md,"signal",lambda _:None)
    monkeypatch.setattr(api,"summary",lambda *_:{"summary":None,"data_stale":True,"counts":{"buy":17,"sell":0,"neutral":0}})
    assert price.technical("AAPL").insufficient_data


def test_ichimoku_visible_cloud_uses_past_values():
    from ta.trend import IchimokuIndicator
    df=candles()
    result=score_candles(df)
    past=df.iloc[:-26]
    cloud=IchimokuIndicator(past.High,past.Low,9,26,52)
    expected=(cloud.ichimoku_a().iloc[-1]+cloud.ichimoku_b().iloc[-1])/2
    assert result["indicator_values"]["IchimokuCloud"]["value"]==pytest.approx(expected)


def test_macd_histogram_matches_exponential_definition():
    df=candles()
    fast=df.Close.ewm(span=12,adjust=False,min_periods=12).mean()
    slow=df.Close.ewm(span=26,adjust=False,min_periods=26).mean()
    line=fast-slow
    hist=line-line.ewm(span=9,adjust=False,min_periods=9).mean()
    assert score_candles(df)["indicator_values"]["MACD12_26_9"]["value"]==pytest.approx(hist.iloc[-1])


def test_contract_expiration_fallback_is_exact_and_time_checked(monkeypatch):
    import sys
    from types import SimpleNamespace
    from src.data.technical_recommendations import load_frame
    class Fake:
        history_metadata={"symbol":"GCZ26.CMX","exchangeName":"CMX"}
        def history(self,**kwargs): return candles()
        def get_info(self): return {"symbol":"GCZ26.CMX","exchange":"CMX","expireDate":int(pd.Timestamp("2026-12-29T00:00:00Z").timestamp())}
    monkeypatch.setitem(sys.modules,"yfinance",SimpleNamespace(Ticker=lambda _:Fake()))
    identity=instrument("GCZ6")
    load_frame(identity,"1h",now="2026-10-15T12:00:00Z")
    assert identity["expiration"]=="2026-12-29T00:00:00+00:00"
    with pytest.raises(ValueError,match="expired"):
        load_frame(instrument("GCZ6"),"1h",now="2027-01-01T00:00:00Z")


def test_existing_http_endpoint_preserves_evidence_and_owner_boundary(monkeypatch):
    from fastapi.testclient import TestClient
    import backend.main as backend
    from src.auth import policy
    import src.data.technical_recommendations as adapter
    monkeypatch.setenv("ARIA_OWNER_TOKEN","technical-test-owner")
    monkeypatch.delenv("ARIA_OWNER_TOKEN_HASH",raising=False)
    monkeypatch.setattr(policy,"owner_token_hash",lambda:"")
    df=candles()
    monkeypatch.setattr(adapter,"load_frame",lambda *a,**k:(df,{}))
    # Do not enter the client's lifespan; no background runtime starts.
    client=TestClient(backend.app)
    response=client.get("/api/technical/summary",params={"symbol":"AAPL","timeframes":"1h,bad"},headers={"Authorization":"Bearer technical-test-owner"})
    assert response.status_code==200
    assert "indicator_values" in response.json()["timeframes"]["1h"]
    assert response.json()["consensus"] is None
    monkeypatch.setenv("ARIA_SERVICE_TOKEN","technical-test-peer")
    denied=client.get("/api/technical/summary",params={"symbol":"AAPL"},headers={"Authorization":"Bearer technical-test-peer"})
    assert denied.status_code==403


@pytest.mark.parametrize("symbol,tz", [("AAPL","America/New_York"),("BA.L","Europe/London"),("RELIANCE.NS","Asia/Kolkata")])
def test_central_daily_date_only_adapter_uses_resolved_venue(monkeypatch,symbol,tz):
    from src.v5 import marketdata as md
    from src.data.technical_recommendations import load_frame
    df=candles(freq="D"); df.index=df.index.tz_localize(None)
    monkeypatch.setattr(md,"history",lambda *a,**k:df)
    monkeypatch.setattr(md,"provenance",lambda *_:{"provider_symbol":symbol,"stale":False,"vendor":"fixture"})
    loaded, provenance=load_frame({"provider_symbol":symbol,"requested":symbol,"kind":"provider_instrument"},"1d")
    assert str(loaded.index.tz)==tz
    assert df.index.tz is None  # never mutate the shared central frame
    assert provenance["exchange_timezone"]==tz
    closed_candles(loaded,"1d",pd.Timestamp("2026-10-15T00:00:00Z"))


def test_central_daily_adapter_refuses_ambiguous_clock_and_wrong_instrument(monkeypatch):
    from src.v5 import marketdata as md
    from src.data.technical_recommendations import load_frame
    df=candles(freq="D"); df.index=df.index.tz_localize(None)+pd.Timedelta(hours=5)
    monkeypatch.setattr(md,"history",lambda *a,**k:df)
    monkeypatch.setattr(md,"provenance",lambda *_:{"provider_symbol":"AAPL","stale":False})
    identity={"provider_symbol":"AAPL","requested":"AAPL","kind":"provider_instrument"}
    with pytest.raises(ValueError,match="ambiguous daily"):load_frame(identity,"1d")
    monkeypatch.setattr(md,"provenance",lambda *_:{"provider_symbol":"BA.L","stale":False})
    with pytest.raises(ValueError,match="different provider"):load_frame(identity,"1d")


def test_upstream_stale_flag_cannot_be_overridden_by_fresh_bar_timestamp():
    df=candles()
    result=analyze("AAPL","1h",loader=lambda *_:(df,{"upstream_stale":True}),now=df.index[-1]+pd.Timedelta(hours=1))
    assert result["data_stale"] and result["summary"] is None
