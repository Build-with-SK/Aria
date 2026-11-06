"""Fixture-first multiasset research using existing indicator implementations.

No transport, execution import or model is involved. Results are research
hypotheses; absence of a configured input produces an explicit ABSTAIN.
"""
from datetime import datetime
from decimal import Decimal
from typing import Protocol
from pydantic import Field

from src.trading.contracts import (AssetClass, Contract, Instrument, MarketQuote, OHLCVBar,
    ResearchEvidence, StrategyDefinition, TradeHypothesis, content_hash)


ASSET_CAPABILITIES = {
    AssetClass.FOREX: ("technical", "macro", "yield_differentials"),
    AssetClass.METALS: ("technical", "real_yields", "macro"),
    AssetClass.ENERGY: ("technical", "inventory", "supply_demand"),
    AssetClass.AGRICULTURE: ("technical", "weather", "seasonality"),
    AssetClass.INDICES: ("technical", "earnings", "breadth"),
    AssetClass.EQUITIES: ("technical", "fundamentals", "corporate_actions"),
    AssetClass.ETFS: ("technical", "holdings", "corporate_actions"),
    AssetClass.CRYPTO: ("technical", "funding", "network_data"),
    AssetClass.OTHER: (),
}


class ResearchRequest(Contract):
    instrument: Instrument
    bars: tuple[OHLCVBar, ...] = Field(max_length=5000)
    as_of: datetime
    family: str = "TrendFollowing"
    timeframe: str = "1h"
    regime: str = "UNKNOWN"
    evidence: tuple[ResearchEvidence, ...] = Field(default=(), max_length=100)


def workspace_status(*, now: datetime) -> dict:
    """Status of THIS extension only; does not assert legacy desk state."""
    return {"schema_version":"1", "as_of":now.isoformat(),
            "scope":"multiasset research extension",
            "execution_mode":"DISABLED", "unattended_execution":False, "live_execution":False,
            "account":None, "balance":None, "equity":None, "daily_drawdown":None,
            "total_drawdown":None, "remaining_risk_budget":None,
            "rule_profile":{"status":"POLICY_UNVERIFIED","profile":None},
            "mt5":{"status":"NOT_CONFIGURED","adapter":"READ_ONLY","integration_verified":False},
            "ibkr":{"status":"NOT_VERIFIED_BY_THIS_EXTENSION","legacy_adapter_preserved":True},
            "data_state":"DISCONNECTED", "quotes":[], "positions":[], "pending_orders":[],
            "research_universe":[{"asset_class":a.value,"features":list(features),
                                   "broker_tradability":"UNVERIFIED"} for a,features in ASSET_CAPABILITIES.items()],
            "strategies":[s.model_dump(mode="json") for s in strategy_registry()],
            "decision":{"status":"ABSTAIN","reason":"No verified account profile or configured executable quote source"},
            "third_party_plugins":"DISABLED_NO_SANDBOX", "mcp_execution":"DISABLED",
            "reconciliation":"NO_BROKER_SESSION", "broker_fills_verified":False}


class MarketDataProvider(Protocol):
    name: str
    def quotes(self, instrument: Instrument) -> MarketQuote | None: ...
    def bars(self, instrument: Instrument) -> tuple[OHLCVBar, ...]: ...
    def health(self) -> dict: ...


class FixtureProvider:
    """Immutable fixture transport. Never claims that fixture quotes execute."""
    name = "fixture"

    def __init__(self, *, quotes: tuple[MarketQuote, ...] = (), bars: tuple[OHLCVBar, ...] = ()):
        if any(x.provenance.permission != "RESEARCH" or x.provenance.provider != "fixture" for x in (*quotes, *bars)):
            raise ValueError("fixture provenance must be fixture / RESEARCH")
        self._quotes, self._bars = quotes, bars

    def quotes(self, instrument):
        found = [q for q in self._quotes if q.provenance.instrument_id == instrument.instrument_id
                 and q.provenance.original_symbol == instrument.provider_symbol]
        return max(found, key=lambda q:q.provenance.observed_at) if found else None

    def bars(self, instrument):
        return tuple(b for b in self._bars if b.provenance.instrument_id == instrument.instrument_id
                     and b.provenance.original_symbol == instrument.provider_symbol)

    def health(self):
        return {"status":"FIXTURE", "execution_usable":False,
                "supported":["quotes","historical_ohlcv"],
                "unavailable":["news","fundamentals","economic_releases","yield_curves","order_book"]}


def universe_permissions(instrument_id: str, *, research: set[str], provider: set[str],
                         broker: set[str], account: set[str]) -> dict:
    checks = {"research":instrument_id in research, "provider":instrument_id in provider,
              "broker":instrument_id in broker, "account":instrument_id in account}
    return {**checks, "all_universes_permit":all(checks.values()), "execution_authorised":False}


def validate_bars(instrument: Instrument, bars: tuple[OHLCVBar, ...], *, now: datetime) -> tuple[OHLCVBar, ...]:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("aware clock required")
    completed = tuple(b for b in bars if b.completed and b.closes_at <= now)
    seen, previous, origin = set(), None, None
    for b in completed:
        p=b.provenance
        if p.instrument_id != instrument.instrument_id or p.original_symbol != instrument.provider_symbol:
            raise ValueError("bar identity mismatch")
        if p.quality != "VALID" or p.retrieved_at > now or p.observed_at > now:
            raise ValueError("bar quality or timestamp invalid")
        basis=(p.provider,b.adjustment_basis,p.original_symbol)
        if origin is not None and basis != origin:
            raise ValueError("cannot silently merge providers or adjustment bases")
        origin=basis
        if b.opens_at in seen or (previous is not None and b.opens_at < previous):
            raise ValueError("duplicate or overlapping/out-of-order bars")
        seen.add(b.opens_at)
        previous=b.closes_at
    return completed


def technical_evidence(instrument: Instrument, bars: tuple[OHLCVBar, ...], *, now: datetime) -> dict:
    completed=validate_bars(instrument,bars,now=now)
    if len(completed)<50:
        return {"status":"WARMUP", "completed_bars":len(completed), "required_bars":50}
    import pandas as pd
    from ta.momentum import RSIIndicator
    from ta.trend import EMAIndicator, SMAIndicator, MACD, ADXIndicator
    from ta.volatility import AverageTrueRange, BollingerBands
    from src.data.technical_summary import score_frame
    df=pd.DataFrame([{"Open":float(b.open),"High":float(b.high),"Low":float(b.low),
                      "Close":float(b.close)} for b in completed])
    c,h,l=df.Close,df.High,df.Low
    indicators={"SMA20":SMAIndicator(c,20).sma_indicator().iloc[-1],
                "EMA20":EMAIndicator(c,20).ema_indicator().iloc[-1],
                "RSI14":RSIIndicator(c,14).rsi().iloc[-1],
                "MACD":MACD(c).macd().iloc[-1],
                "ATR14":AverageTrueRange(h,l,c,14).average_true_range().iloc[-1],
                "ADX14":ADXIndicator(h,l,c,14).adx().iloc[-1],
                "BOLLINGER_UPPER":BollingerBands(c).bollinger_hband().iloc[-1],
                "BOLLINGER_LOWER":BollingerBands(c).bollinger_lband().iloc[-1]}
    import math
    values={k:str(v) if math.isfinite(float(v)) else None for k,v in indicators.items()}
    actual_volume=all(b.volume_type=="TRADED" and b.volume is not None for b in completed)
    tick_volume=all(b.volume_type=="TICK" for b in completed)
    if actual_volume:
        total=sum(b.volume for b in completed)
        vwap=sum((b.high+b.low+b.close)/3*b.volume for b in completed)/total if total else None
        # This is anchored to the supplied window, not a fabricated exchange session.
        values["WINDOW_VWAP"]=str(vwap) if vwap is not None else None
    return {"status":"AVAILABLE", "completed_bars":len(completed), "as_of":completed[-1].closes_at.isoformat(),
            "indicators":values, "existing_summary":score_frame(df),
            "volume_kind":"TRADED" if actual_volume else "TICK" if tick_volume else "UNAVAILABLE",
            "vwap_status":"AVAILABLE" if actual_volume else "UNSUPPORTED",
            "order_book_status":"UNAVAILABLE", "dataset_hash":content_hash({"bars":[b.model_dump(mode="json") for b in completed]})}


FAMILIES=("TrendFollowing","Momentum","Breakout","Pullback","MeanReversion",
          "SupportResistance","VolatilityBased","MacroDriven","Intraday","Swing")


def strategy_registry() -> tuple[StrategyDefinition, ...]:
    return tuple(StrategyDefinition(strategy_id=f"research:{family}",version="1.0.0",family=family,
        supported_asset_classes=tuple(a for a in AssetClass if a != AssetClass.OTHER),
        required_data=("completed_ohlcv", "cited_macro") if family=="MacroDriven" else ("completed_ohlcv",),
        required_indicators=("SMA20","ATR14","RSI14"),supported_timeframes=("1h","4h","1d"),
        supported_regimes=("TREND",) if family in ("TrendFollowing","Momentum","Breakout","Pullback","Swing") else ("RANGE","HIGH_VOLATILITY"),
        entry_conditions=(f"{family} deterministic research trigger; see analyse()",),
        exit_conditions=("stop or target", "maximum holding period"),
        stop_loss_policy="2 ATR research basis; separate risk sizing required",
        take_profit_policy="2 times initial stop distance before costs",
        max_holding_seconds=86400 if family=="Intraday" else 604800,
        market_hours_constraints="verified instrument calendar; unknown abstains",
        invalidation_conditions=("missing data", "expired evidence", "regime mismatch"),
        risk_compatibility="research-only; no execution permission",
        evidence_requirements=("completed candles", "alternative scenario", "independent risk calculation"))
        for family in FAMILIES)


def analyse(instrument: Instrument, bars: tuple[OHLCVBar, ...], *, now: datetime,
            family: str="TrendFollowing", timeframe: str="1h", regime: str="UNKNOWN",
            evidence: tuple[ResearchEvidence, ...]=()) -> TradeHypothesis:
    definition=next((s for s in strategy_registry() if s.family==family),None)
    if definition is None:
        raise ValueError("unknown strategy family")
    completed=validate_bars(instrument,bars,now=now)
    missing=[]
    if instrument.asset_class not in definition.supported_asset_classes:
        missing.append("unsupported asset class")
    if timeframe not in definition.supported_timeframes or regime not in definition.supported_regimes:
        missing.append("timeframe/regime unsuitable or unverified")
    if len(completed)<50:
        missing.append("indicator warm-up incomplete")
    duration={"1h":3600,"4h":14400,"1d":86400}.get(timeframe)
    if completed and duration:
        if any((b.closes_at-b.opens_at).total_seconds()!=duration for b in completed):
            missing.append("candle duration does not match strategy timeframe")
        if (now-completed[-1].closes_at).total_seconds()>duration*2:
            missing.append("latest candle too old for the requested research timeframe")
    if family=="MacroDriven":
        missing.append("point-in-time macro strategy and calibrated event model not implemented")
    # Avoid claiming a daily candle can drive an intraday strategy.
    if family=="Intraday" and timeframe!="1h":
        missing.append("intraday requires intraday completed candles")
    technical=technical_evidence(instrument,completed,now=now)
    direction=None
    if not missing:
        close=completed[-1].close
        vals=technical["indicators"]
        sma,rsi,atr=(Decimal(vals[k]) if vals[k] is not None else None for k in ("SMA20","RSI14","ATR14"))
        if sma is None or rsi is None or atr is None or atr<=0:
            missing.append("nonfinite indicator or zero volatility")
        else:
            prior=completed[-21:-1]
            top,bottom=max(b.high for b in prior),min(b.low for b in prior)
            momentum=close-completed[-11].close
            if family in ("TrendFollowing","Swing"):
                direction="BUY" if close>sma and momentum>0 else "SELL" if close<sma and momentum<0 else None
            elif family=="Momentum":
                direction="BUY" if momentum>atr else "SELL" if momentum<-atr else None
            elif family in ("Breakout","Intraday"):
                direction="BUY" if close>top else "SELL" if close<bottom else None
            elif family=="Pullback":
                direction="BUY" if momentum>0 and close>=sma and completed[-1].low<=sma else "SELL" if momentum<0 and close<=sma and completed[-1].high>=sma else None
            elif family=="MeanReversion":
                direction="BUY" if rsi<30 else "SELL" if rsi>70 else None
            elif family=="SupportResistance":
                direction="BUY" if close-bottom<atr/2 else "SELL" if top-close<atr/2 else None
            elif family=="VolatilityBased":
                direction="BUY" if close>Decimal(vals["BOLLINGER_UPPER"]) else "SELL" if close<Decimal(vals["BOLLINGER_LOWER"]) else None
    if not direction:
        missing.append("no qualifying research trigger; no trade")
    supporting=tuple(e for e in evidence if e.classification in ("OBSERVED","VERIFIED_EXTERNAL_FACT"))
    if direction and completed:
        supporting+=(ResearchEvidence(evidence_id=technical["dataset_hash"],
            claim=f"Completed-candle {family} research trigger: {direction}",classification="OBSERVED",
            provenance=completed[-1].provenance),)
    contradicting=()
    if direction and technical.get("indicators"):
        rsi_value=technical["indicators"].get("RSI14")
        if rsi_value is not None and ((direction=="BUY" and Decimal(rsi_value)>70) or (direction=="SELL" and Decimal(rsi_value)<30)):
            contradicting=(ResearchEvidence(evidence_id=technical["dataset_hash"]+":counter-rsi",
                claim="RSI is stretched against the proposed direction; reversal risk warrants review",
                classification="MODEL_INTERPRETATION",provenance=completed[-1].provenance),)
    return TradeHypothesis(hypothesis_id=content_hash({"instrument":instrument.instrument_id,
        "family":family,"version":definition.version,"bars":[b.model_dump(mode="json") for b in completed],
        "timeframe":timeframe,"regime":regime,"evidence":[e.model_dump(mode="json") for e in evidence]}),
        market_context=f"{regime}; {timeframe}; research data only",asset_significance=instrument.asset_class.value,
        price_drivers=("historical price structure",),supporting_evidence=supporting,contradicting_evidence=contradicting,
        alternative_scenarios=("trend reversal or failed breakout", "range persists after transaction costs"),
        critical_assumptions=("past candles are not future evidence", "broker costs and calendar require verification"),
        missing_information=tuple(missing)+("independent account risk and executable broker quote required",),
        entry_thesis=f"{direction or 'ABSTAIN'} research hypothesis using {family}",
        invalidation_conditions=("opposite trigger", "stale/missing input"),stop_loss_basis="2 ATR research basis",
        take_profit_basis="2R before costs; no execution sizing",planned_risk=Decimal("0"),net_reward_risk=None,
        cancellation_conditions=("new quote or rule profile", "evidence expires"),
        reason_to_trade_instead_of_abstaining="research trigger warrants human review" if direction and not missing else "no justified trade",
        research_confidence="UNVERIFIED",decision_status="APPROVE_FOR_HUMAN_REVIEW" if direction and not missing else "ABSTAIN")
