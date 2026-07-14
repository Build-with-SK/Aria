# src/strategies/futures_engine.py
"""
Futures Strategy Engine
Equity index, bond, energy, metals, agriculture, FX futures via yfinance.
Strategies: trend following, roll yield, seasonality, carry.
"""

from __future__ import annotations
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class FuturesSignal:
    ticker: str
    name: str
    asset_class: str
    direction: str          # "long" | "short" | "flat"
    trend_signal: str       # "bullish" | "bearish" | "neutral"
    carry_signal: str
    seasonal_signal: str
    combined_score: float   # -100 to +100
    spot_price: float
    sma_fast: float
    sma_slow: float
    atr: float
    stop_loss_price: float
    contract_specs: dict    # point_value, margin, tick_size
    size_in_contracts: float  # recommended position size
    margin_required: float
    conviction: float
    rationale: str

    def _to_json(self) -> dict:
        return {
            "ticker": self.ticker,
            "name": self.name,
            "asset_class": self.asset_class,
            "direction": self.direction,
            "trend_signal": self.trend_signal,
            "carry_signal": self.carry_signal,
            "seasonal_signal": self.seasonal_signal,
            "combined_score": round(self.combined_score, 2),
            "spot_price": round(self.spot_price, 4),
            "sma_fast": round(self.sma_fast, 4),
            "sma_slow": round(self.sma_slow, 4),
            "atr": round(self.atr, 4),
            "stop_loss_price": round(self.stop_loss_price, 4),
            "contract_specs": self.contract_specs,
            "size_in_contracts": round(self.size_in_contracts, 2),
            "margin_required": round(self.margin_required, 2),
            "conviction": round(self.conviction, 1),
            "rationale": self.rationale,
        }


# ---------------------------------------------------------------------------
# Contract specifications
# ---------------------------------------------------------------------------
_CONTRACT_SPECS: Dict[str, dict] = {
    # Equity index
    "ES=F":  {"name": "S&P 500 E-mini",         "asset_class": "Equity Index",   "point_value": 50,    "margin": 12000, "tick_size": 0.25},
    "NQ=F":  {"name": "Nasdaq E-mini",           "asset_class": "Equity Index",   "point_value": 20,    "margin": 16000, "tick_size": 0.25},
    "YM=F":  {"name": "Dow E-mini",              "asset_class": "Equity Index",   "point_value": 5,     "margin": 8000,  "tick_size": 1.0},
    "RTY=F": {"name": "Russell 2000 E-mini",     "asset_class": "Equity Index",   "point_value": 50,    "margin": 7000,  "tick_size": 0.10},
    # Bonds
    "ZN=F":  {"name": "10-Year T-Note",          "asset_class": "Bonds",          "point_value": 1000,  "margin": 1500,  "tick_size": 0.015625},
    "ZB=F":  {"name": "30-Year T-Bond",          "asset_class": "Bonds",          "point_value": 1000,  "margin": 3000,  "tick_size": 0.03125},
    "ZT=F":  {"name": "2-Year T-Note",           "asset_class": "Bonds",          "point_value": 2000,  "margin": 500,   "tick_size": 0.0078125},
    # Energy
    "CL=F":  {"name": "Crude Oil WTI",           "asset_class": "Energy",         "point_value": 1000,  "margin": 5000,  "tick_size": 0.01},
    "NG=F":  {"name": "Natural Gas",             "asset_class": "Energy",         "point_value": 10000, "margin": 3000,  "tick_size": 0.001},
    "RB=F":  {"name": "RBOB Gasoline",           "asset_class": "Energy",         "point_value": 42000, "margin": 5000,  "tick_size": 0.0001},
    # Metals
    "GC=F":  {"name": "Gold",                    "asset_class": "Metals",         "point_value": 100,   "margin": 8000,  "tick_size": 0.10},
    "SI=F":  {"name": "Silver",                  "asset_class": "Metals",         "point_value": 5000,  "margin": 8000,  "tick_size": 0.005},
    "HG=F":  {"name": "Copper",                  "asset_class": "Metals",         "point_value": 25000, "margin": 4000,  "tick_size": 0.0005},
    # Agriculture
    "ZC=F":  {"name": "Corn",                    "asset_class": "Agriculture",    "point_value": 50,    "margin": 1500,  "tick_size": 0.25},
    "ZS=F":  {"name": "Soybeans",               "asset_class": "Agriculture",    "point_value": 50,    "margin": 2000,  "tick_size": 0.25},
    "ZW=F":  {"name": "Wheat",                   "asset_class": "Agriculture",    "point_value": 50,    "margin": 2000,  "tick_size": 0.25},
    # FX futures
    "6E=F":  {"name": "EUR/USD Futures",         "asset_class": "FX Futures",     "point_value": 125000, "margin": 2500, "tick_size": 0.00005},
    "6J=F":  {"name": "JPY/USD Futures",         "asset_class": "FX Futures",     "point_value": 12500000, "margin": 2500, "tick_size": 0.0000005},
    "6B=F":  {"name": "GBP/USD Futures",         "asset_class": "FX Futures",     "point_value": 62500, "margin": 2500,  "tick_size": 0.0001},
}

# Seasonal bias by asset and month (simplified heuristics based on historical patterns)
# +1 = historically bullish, -1 = bearish, 0 = neutral
_SEASONAL_BIAS: Dict[str, Dict[int, int]] = {
    "GC=F":  {1: 1, 2: 1, 3: 0, 4: 0, 5: 0, 6: 0, 7: -1, 8: -1, 9: 0, 10: 0, 11: 1, 12: 1},
    "CL=F":  {1: 0, 2: 1, 3: 1, 4: 1, 5: 0, 6: -1, 7: -1, 8: 0, 9: 1, 10: 0, 11: -1, 12: -1},
    "ZC=F":  {1: 0, 2: 0, 3: 1, 4: 1, 5: 1, 6: 0, 7: -1, 8: -1, 9: 0, 10: 0, 11: 0, 12: 0},
    "ZS=F":  {1: 0, 2: 1, 3: 1, 4: 1, 5: 0, 6: -1, 7: -1, 8: 0, 9: 1, 10: 0, 11: 0, 12: 0},
    "ZW=F":  {1: 1, 2: 1, 3: 0, 4: 0, 5: -1, 6: -1, 7: 0, 8: 0, 9: 0, 10: 0, 11: 1, 12: 1},
    "ES=F":  {1: 1, 2: 0, 3: 0, 4: 1, 5: -1, 6: 0, 7: 1, 8: 0, 9: -1, 10: 0, 11: 1, 12: 1},
}


def _trend_signal(df: pd.DataFrame, fast: int, slow: int) -> tuple:
    """SMA crossover + ATR stop. Returns (signal, sma_fast, sma_slow, atr)."""
    try:
        sma_fast = float(df["Close"].tail(fast).mean())
        sma_slow = float(df["Close"].tail(slow).mean())
        high_low = df["High"] - df["Low"]
        atr = float(high_low.tail(14).mean())

        if sma_fast > sma_slow:
            signal = "bullish"
        elif sma_fast < sma_slow:
            signal = "bearish"
        else:
            signal = "neutral"

        return signal, sma_fast, sma_slow, atr
    except Exception:
        price = float(df["Close"].iloc[-1]) if not df.empty else 0
        return "neutral", price, price, 0.0


def _carry_signal(ticker: str, spot: float, risk_free_rate: float = 0.05) -> str:
    """
    Simplified carry: for commodity futures, backwardation = positive carry.
    For financial futures, rate differential is the carry.
    """
    specs = _CONTRACT_SPECS.get(ticker, {})
    ac = specs.get("asset_class", "")
    if ac in ("Energy", "Metals", "Agriculture"):
        # We can't easily get forward curve from yfinance.
        # Use simple heuristic: if spot above 200d MA, likely backwardation (bullish carry)
        return "positive"  # placeholder — would need futures term structure data
    elif ac == "Equity Index":
        # Equity futures carry = dividend yield - risk free rate
        # With rates high, negative carry for equity futures generally
        return "negative" if risk_free_rate > 0.04 else "positive"
    elif ac == "Bonds":
        return "positive" if risk_free_rate < 0.04 else "neutral"
    return "neutral"


def _seasonal_signal(ticker: str) -> str:
    """Get seasonal bias for current month."""
    import datetime
    month = datetime.datetime.now().month
    bias_map = _SEASONAL_BIAS.get(ticker, {})
    bias = bias_map.get(month, 0)
    if bias == 1:
        return "bullish"
    elif bias == -1:
        return "bearish"
    return "neutral"


def _position_size(
    capital: float,
    atr: float,
    point_value: float,
    risk_pct: float = 0.01,  # 1% risk per trade
) -> float:
    """ATR-based position sizing."""
    try:
        if atr <= 0 or point_value <= 0:
            return 1.0
        dollar_risk = capital * risk_pct
        risk_per_contract = atr * point_value
        return max(1.0, round(dollar_risk / risk_per_contract, 1))
    except Exception:
        return 1.0


def _analyse_future(
    ticker: str,
    df: pd.DataFrame,
    config: dict,
    capital: float,
) -> Optional[FuturesSignal]:
    """Build FuturesSignal for one futures contract."""
    try:
        if df is None or df.empty or len(df) < 50:
            return None

        cfg_fut = config.get("strategies", {}).get("futures", {})
        fast = int(cfg_fut.get("trend_fast", 50))
        slow = int(cfg_fut.get("trend_slow", 200))

        spot = float(df["Close"].iloc[-1])
        trend_sig, sma_f, sma_s, atr = _trend_signal(df, fast, slow)
        carry_sig = _carry_signal(ticker, spot)
        seasonal_sig = _seasonal_signal(ticker)

        # Signal mapping to numeric
        sm = {"bullish": 1, "neutral": 0, "bearish": -1, "positive": 1, "negative": -1}
        trend_num = sm.get(trend_sig, 0)
        carry_num = sm.get(carry_sig, 0)
        seasonal_num = sm.get(seasonal_sig, 0)

        combined = (trend_num * 0.60 + carry_num * 0.25 + seasonal_num * 0.15) * 100

        if combined > 15:
            direction = "long"
        elif combined < -15:
            direction = "short"
        else:
            direction = "flat"

        specs = _CONTRACT_SPECS.get(ticker, {})
        pv = float(specs.get("point_value", 1))
        margin = float(specs.get("margin", 5000))

        size = _position_size(capital, atr, pv)
        stop_loss = spot - (atr * 2) if direction == "long" else spot + (atr * 2)

        conviction = float(np.clip(abs(combined), 10, 85))
        rationale = f"Trend: {trend_sig} | Carry: {carry_sig} | Seasonal: {seasonal_sig} | Score: {combined:.1f}"

        return FuturesSignal(
            ticker=ticker,
            name=specs.get("name", ticker),
            asset_class=specs.get("asset_class", "Unknown"),
            direction=direction,
            trend_signal=trend_sig,
            carry_signal=carry_sig,
            seasonal_signal=seasonal_sig,
            combined_score=round(float(combined), 2),
            spot_price=round(spot, 4),
            sma_fast=round(sma_f, 4),
            sma_slow=round(sma_s, 4),
            atr=round(atr, 4),
            stop_loss_price=round(stop_loss, 4),
            contract_specs=specs,
            size_in_contracts=size,
            margin_required=round(size * margin, 2),
            conviction=round(conviction, 1),
            rationale=rationale,
        )

    except Exception as e:
        logger.debug(f"Futures signal failed for {ticker}: {e}")
        return None


def run_futures_engine(
    featured_data: Dict[str, pd.DataFrame],
    config: dict,
) -> List[FuturesSignal]:
    """
    Build futures signals for all futures tickers in universe.
    Returns list sorted by |combined_score|.
    """
    capital = float(config.get("backtest", {}).get("initial_capital", 100_000))
    signals: List[FuturesSignal] = []

    futures_tickers = [t for t in featured_data if t.endswith("=F")]

    for ticker in futures_tickers:
        df = featured_data.get(ticker)
        sig = _analyse_future(ticker, df, config, capital)
        if sig:
            signals.append(sig)

    signals.sort(key=lambda s: abs(s.combined_score), reverse=True)

    if not signals:
        logger.info("No futures signals generated. Add futures tickers (e.g. ES=F, GC=F) to universe.yaml.")

    return signals
