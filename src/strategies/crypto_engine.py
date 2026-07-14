# src/strategies/crypto_engine.py
"""
Crypto Strategy Engine
BTC/ETH and altcoins via yfinance.
On-chain proxy signals: BTC dominance (CoinGecko), Fear & Greed (alternative.me).
Crypto-specific indicators: MVRV proxy, Pi Cycle, Puell multiple proxy.
"""

from __future__ import annotations
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

_CRYPTO_TICKERS = [
    "BTC-USD", "ETH-USD", "BNB-USD", "SOL-USD", "XRP-USD",
    "ADA-USD", "AVAX-USD", "DOGE-USD", "DOT-USD", "MATIC-USD",
]

_CRYPTO_NAMES = {
    "BTC-USD": "Bitcoin", "ETH-USD": "Ethereum", "BNB-USD": "BNB",
    "SOL-USD": "Solana", "XRP-USD": "XRP", "ADA-USD": "Cardano",
    "AVAX-USD": "Avalanche", "DOGE-USD": "Dogecoin", "DOT-USD": "Polkadot",
    "MATIC-USD": "Polygon",
}


@dataclass
class CryptoSignal:
    ticker: str
    name: str
    current_price: float
    technical_score: float      # -100 to +100 from indicators
    onchain_score: float        # -100 to +100 from on-chain data
    regime: str                 # "risk_on" | "risk_off" | "neutral"
    mvrv_z_proxy: float         # price / 200d MA ratio (proxy)
    pi_cycle_signal: str        # "near_top" | "neutral" | "near_bottom"
    puell_proxy: float          # 30d avg daily return / 365d avg
    fear_greed_index: int       # 0-100 (from API or cached)
    btc_dominance: float        # from CoinGecko or cached
    signal: str                 # "strong_long" | "long" | "neutral" | "short" | "strong_short"
    recommended_allocation_pct: float   # % of crypto budget
    conviction: float
    rationale: str

    def _to_json(self) -> dict:
        return {
            "ticker": self.ticker,
            "name": self.name,
            "current_price": round(self.current_price, 4),
            "technical_score": round(self.technical_score, 2),
            "onchain_score": round(self.onchain_score, 2),
            "regime": self.regime,
            "mvrv_z_proxy": round(self.mvrv_z_proxy, 4),
            "pi_cycle_signal": self.pi_cycle_signal,
            "puell_proxy": round(self.puell_proxy, 4),
            "fear_greed_index": self.fear_greed_index,
            "btc_dominance": round(self.btc_dominance, 2),
            "signal": self.signal,
            "recommended_allocation_pct": round(self.recommended_allocation_pct, 2),
            "conviction": round(self.conviction, 1),
            "rationale": self.rationale,
        }


# ---------------------------------------------------------------------------
# On-chain data fetchers (public APIs, no key needed)
# ---------------------------------------------------------------------------

def _fetch_fear_greed() -> int:
    """Fear & Greed Index from alternative.me (free, no auth)."""
    try:
        import requests
        r = requests.get("https://api.alternative.me/fng/?limit=1", timeout=5)
        data = r.json()
        return int(data["data"][0]["value"])
    except Exception:
        logger.debug("Fear & Greed API unavailable — using default 50.")
        return 50


def _fetch_btc_dominance() -> float:
    """BTC dominance from CoinGecko free API."""
    try:
        import requests
        r = requests.get("https://api.coingecko.com/api/v3/global", timeout=5)
        data = r.json()
        dom = data["data"]["market_cap_percentage"]["btc"]
        return float(dom)
    except Exception:
        logger.debug("CoinGecko API unavailable — using default 50%.")
        return 50.0


# ---------------------------------------------------------------------------
# Crypto-specific indicators
# ---------------------------------------------------------------------------

def _mvrv_z_proxy(df: pd.DataFrame) -> float:
    """
    MVRV Z-score proxy: (price - 200d MA) / stddev.
    High Z (+2) → overvalued, Low Z (-2) → undervalued.
    """
    try:
        if len(df) < 200:
            return 0.0
        ma200 = df["Close"].tail(200).mean()
        std200 = df["Close"].tail(200).std()
        price = float(df["Close"].iloc[-1])
        if std200 == 0:
            return 0.0
        return float((price - ma200) / std200)
    except Exception:
        return 0.0


def _pi_cycle_signal(df: pd.DataFrame) -> str:
    """
    Pi Cycle Top Indicator:
    When 111d MA crosses above 2x 350d MA → near cycle top.
    Below 350d MA → near bottom.
    """
    try:
        if len(df) < 350:
            return "neutral"
        ma111 = float(df["Close"].tail(111).mean())
        ma350 = float(df["Close"].tail(350).mean())
        ma350_2x = ma350 * 2

        if ma111 >= ma350_2x * 0.95:
            return "near_top"
        elif float(df["Close"].iloc[-1]) < ma350 * 0.8:
            return "near_bottom"
        else:
            return "neutral"
    except Exception:
        return "neutral"


def _puell_multiple_proxy(df: pd.DataFrame) -> float:
    """
    Puell Multiple proxy: 30d avg daily return / 365d avg daily return.
    High ratio (>2) → overvalued. Low (<0.5) → undervalued.
    """
    try:
        daily_ret = df["Close"].pct_change().dropna().abs()
        if len(daily_ret) < 365:
            return 1.0
        avg_30 = float(daily_ret.tail(30).mean())
        avg_365 = float(daily_ret.tail(365).mean())
        return float(avg_30 / avg_365) if avg_365 > 0 else 1.0
    except Exception:
        return 1.0


def _technical_score_crypto(df: pd.DataFrame) -> float:
    """Technical score using standard indicators on crypto OHLCV."""
    try:
        if len(df) < 50:
            return 0.0
        price = float(df["Close"].iloc[-1])
        ma50 = float(df["Close"].tail(50).mean())
        ma200 = float(df["Close"].tail(200).mean()) if len(df) >= 200 else ma50

        # RSI
        delta = df["Close"].diff()
        gain = delta.clip(lower=0).rolling(14).mean()
        loss = (-delta.clip(upper=0)).rolling(14).mean()
        rs = gain / loss.replace(0, np.nan)
        rsi = float((100 - 100 / (1 + rs)).iloc[-1])

        # Trend score
        trend = 0
        if price > ma50:
            trend += 1
        if price > ma200:
            trend += 1
        if ma50 > ma200:
            trend += 1

        trend_score = (trend / 3.0 - 0.5) * 100

        # RSI score
        if rsi < 30:
            rsi_score = 50
        elif rsi > 70:
            rsi_score = -50
        else:
            rsi_score = (50 - rsi) * 1.5

        return float(np.clip(trend_score * 0.6 + rsi_score * 0.4, -100, 100))
    except Exception:
        return 0.0


def _onchain_score(
    mvrv_z: float,
    pi_signal: str,
    puell: float,
    fear_greed: int,
    btc_dom: float,
    is_btc: bool,
) -> float:
    """Aggregate on-chain signals into a score [-100, +100]."""
    scores = []

    # MVRV Z: -3 to +3 mapped to -100 to +100
    mvrv_score = float(np.clip(-mvrv_z * 33, -100, 100))
    scores.append(mvrv_score)

    # Pi Cycle
    if pi_signal == "near_top":
        scores.append(-80)
    elif pi_signal == "near_bottom":
        scores.append(80)
    else:
        scores.append(0)

    # Puell multiple: <0.5 = buy, >2 = sell
    if puell < 0.5:
        puell_score = 60
    elif puell > 2.0:
        puell_score = -60
    else:
        puell_score = (1.25 - puell) * 48   # linear from -60 to +60
    scores.append(float(puell_score))

    # Fear & Greed: 0-25 = extreme fear (buy), 75-100 = extreme greed (sell)
    fg_score = float((50 - fear_greed) * 2)  # 0 → +100, 100 → -100
    scores.append(fg_score)

    # BTC dominance: rising = risk-off (bearish altcoins), falling = risk-on (bullish altcoins)
    if not is_btc:
        dom_score = float(np.clip((50 - btc_dom) * 2, -50, 50))
        scores.append(dom_score)

    return float(np.clip(np.mean(scores), -100, 100))


def _determine_regime(btc_dom: float, fear_greed: int, btc_score: float) -> str:
    """Crypto market regime classification."""
    if fear_greed < 30 or btc_dom > 55:
        return "risk_off"
    elif fear_greed > 65 and btc_dom < 50 and btc_score > 20:
        return "risk_on"
    else:
        return "neutral"


def _signal_from_score(combined: float) -> str:
    if combined >= 60:
        return "strong_long"
    elif combined >= 25:
        return "long"
    elif combined <= -60:
        return "strong_short"
    elif combined <= -25:
        return "short"
    else:
        return "neutral"


def _allocation_pct(signal: str, regime: str, is_btc: bool) -> float:
    """Recommended % of crypto budget for this coin."""
    base = {
        "strong_long": 25, "long": 15, "neutral": 5, "short": 0, "strong_short": 0
    }
    pct = base.get(signal, 5)
    if is_btc:
        pct = int(pct * 1.5)    # BTC gets larger allocation as base currency
    if regime == "risk_off" and not is_btc:
        pct = max(0, pct - 10)  # reduce altcoin exposure in risk-off
    return float(min(pct, 40))


def run_crypto_engine(
    featured_data: Dict[str, pd.DataFrame],
    config: dict,
) -> List[CryptoSignal]:
    """
    Run crypto strategy analysis for all crypto tickers in universe.
    Returns list of CryptoSignal sorted by conviction.
    """
    cfg = config.get("strategies", {}).get("crypto", {})
    include_onchain = bool(cfg.get("include_onchain", True))

    # Fetch on-chain data once
    if include_onchain:
        fear_greed = _fetch_fear_greed()
        btc_dom = _fetch_btc_dominance()
    else:
        fear_greed = 50
        btc_dom = 50.0

    crypto_tickers = [t for t in featured_data if t.endswith("-USD")]
    signals: List[CryptoSignal] = []

    # Compute BTC score first (used for regime)
    btc_df = featured_data.get("BTC-USD")
    btc_tech = _technical_score_crypto(btc_df) if btc_df is not None else 0.0

    regime = _determine_regime(btc_dom, fear_greed, btc_tech)

    for ticker in crypto_tickers:
        df = featured_data.get(ticker)
        if df is None or df.empty or len(df) < 30:
            continue
        try:
            price = float(df["Close"].iloc[-1])
            tech_score = _technical_score_crypto(df)
            mvrv_z = _mvrv_z_proxy(df)
            pi_sig = _pi_cycle_signal(df) if ticker == "BTC-USD" else "neutral"
            puell = _puell_multiple_proxy(df)
            is_btc = ticker == "BTC-USD"

            oc_score = _onchain_score(mvrv_z, pi_sig, puell, fear_greed, btc_dom, is_btc)

            combined = float(tech_score * 0.50 + oc_score * 0.50)
            signal = _signal_from_score(combined)
            alloc = _allocation_pct(signal, regime, is_btc)
            conviction = float(np.clip(abs(combined), 10, 85))

            rationale_parts = [
                f"Tech={tech_score:.0f}",
                f"OnChain={oc_score:.0f}",
                f"F&G={fear_greed}",
                f"BTC_dom={btc_dom:.1f}%",
                f"MVRV_z={mvrv_z:.2f}",
                f"Regime={regime}",
            ]
            if ticker == "BTC-USD":
                rationale_parts.append(f"PiCycle={pi_sig}")

            signals.append(CryptoSignal(
                ticker=ticker,
                name=_CRYPTO_NAMES.get(ticker, ticker),
                current_price=round(price, 4),
                technical_score=round(tech_score, 2),
                onchain_score=round(oc_score, 2),
                regime=regime,
                mvrv_z_proxy=round(mvrv_z, 4),
                pi_cycle_signal=pi_sig,
                puell_proxy=round(puell, 4),
                fear_greed_index=fear_greed,
                btc_dominance=round(btc_dom, 2),
                signal=signal,
                recommended_allocation_pct=alloc,
                conviction=round(conviction, 1),
                rationale=" | ".join(rationale_parts),
            ))

        except Exception as e:
            logger.debug(f"Crypto signal failed for {ticker}: {e}")

    signals.sort(key=lambda s: s.conviction, reverse=True)
    return signals
