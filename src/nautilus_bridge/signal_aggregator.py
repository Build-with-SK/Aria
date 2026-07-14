"""
signal_aggregator.py — Nautilus Bridge: Signal Aggregator
Runs the full Nautilus-style indicator suite over a pandas DataFrame and
produces a unified multi-timeframe (1d/1w/1mo) signal score (0-100).

Designed to replace / augment main.py's basic _signal_from_df() with a richer view.
"""

from typing import Dict, Optional

try:
    import pandas as pd
except ImportError:
    pd = None

from .indicators import (
    EMA, SMA, RSI, MACD, ATR, BollingerBands,
    Stochastic, ADX, VWAP, KeltnerChannel, VolumeOscillator
)


class SignalAggregator:
    """
    Aggregates multiple Nautilus-style indicators into a single 0-100 score.
    Expects a DataFrame with columns: open, high, low, close, volume (lowercase),
    sorted ascending by date.
    """

    def __init__(self):
        pass

    def score(self, ticker: str, df) -> Dict:
        """
        Run the full indicator suite on a price DataFrame and return a unified
        signal dictionary including a 0-100 composite score and component breakdown.
        """
        if pd is None:
            return {"error": "pandas not available", "ticker": ticker, "score": 50}

        if df is None or len(df) < 30:
            return {"error": "insufficient data", "ticker": ticker, "score": 50}

        df = self._normalise_columns(df)
        if df is None:
            return {"error": "missing required columns", "ticker": ticker, "score": 50}

        closes = df["close"].tolist()
        highs = df["high"].tolist()
        lows = df["low"].tolist()
        volumes = df["volume"].tolist() if "volume" in df.columns else [1.0] * len(closes)

        # Run indicators across full history
        rsi = RSI(14)
        macd = MACD(12, 26, 9)
        bb = BollingerBands(20, 2.0)
        atr = ATR(14)
        adx = ADX(14)
        stoch = Stochastic(14, 3)
        ema_fast = EMA(20)
        ema_slow = EMA(50)
        kc = KeltnerChannel(20, 2.0)
        vol_osc = VolumeOscillator(5, 20)

        for i in range(len(closes)):
            rsi.update_raw(closes[i])
            macd.update_raw(closes[i])
            bb.update_raw(closes[i])
            atr.update_raw(highs[i], lows[i], closes[i])
            adx.update_raw(highs[i], lows[i], closes[i])
            stoch.update_raw(highs[i], lows[i], closes[i])
            ema_fast.update_raw(closes[i])
            ema_slow.update_raw(closes[i])
            kc.update_raw(highs[i], lows[i], closes[i])
            vol_osc.update_raw(volumes[i])

        current_price = closes[-1]

        components = self._score_components(
            rsi, macd, bb, atr, adx, stoch, ema_fast, ema_slow, kc, vol_osc, current_price
        )

        composite = sum(c["weighted"] for c in components.values())
        composite = max(0, min(100, composite))

        # Multi-timeframe: compute score using subsets of data (1w ~ last 5 bars trend, 1mo ~ last 21 bars)
        timeframe_scores = self._multi_timeframe(closes)

        return {
            "ticker": ticker,
            "score": round(composite, 1),
            "current_price": round(current_price, 2),
            "components": {k: round(v["weighted"], 2) for k, v in components.items()},
            "raw_indicators": {
                "rsi": round(rsi.value, 2) if rsi.initialized else None,
                "macd_histogram": round(macd.histogram, 4) if macd.histogram is not None else None,
                "adx": round(adx.value, 2) if adx.initialized else None,
                "plus_di": round(adx.plus_di, 2) if adx.plus_di else None,
                "minus_di": round(adx.minus_di, 2) if adx.minus_di else None,
                "bb_pct_b": round(bb.pct_b, 3) if bb.pct_b is not None else None,
                "atr": round(atr.value, 3) if atr.initialized else None,
                "stoch_k": round(stoch.k, 2) if stoch.k is not None else None,
                "stoch_d": round(stoch.d, 2) if stoch.d is not None else None,
                "ema20_vs_ema50": "bullish" if (ema_fast.initialized and ema_slow.initialized and ema_fast.value > ema_slow.value) else "bearish",
                "volume_oscillator": round(vol_osc.value, 2) if vol_osc.initialized else None,
                "squeeze": kc.is_squeeze(bb) if kc.initialized else False,
            },
            "timeframes": timeframe_scores,
            "direction": "long" if composite > 55 else "short" if composite < 45 else "neutral",
        }

    def _normalise_columns(self, df):
        df = df.copy()
        df.columns = [str(c).lower() for c in df.columns]
        required = {"close", "high", "low"}
        if not required.issubset(set(df.columns)):
            return None
        if "volume" not in df.columns:
            df["volume"] = 1.0
        return df

    def _score_components(self, rsi, macd, bb, atr, adx, stoch, ema_fast, ema_slow, kc, vol_osc, price) -> Dict:
        """
        Each component contributes a weighted score (0-100 scale, weight applied).
        Weights sum to 1.0 to keep the composite in 0-100 range.
        """
        components = {}

        # RSI (weight 0.20): 50 = neutral, >70 overbought, <30 oversold
        if rsi.initialized:
            if rsi.value > 70:
                rsi_score = 100 - (rsi.value - 70) * 2  # Penalise overbought
            elif rsi.value < 30:
                rsi_score = rsi.value * 2  # Reward oversold less (mean reversion play)
            else:
                rsi_score = 50 + (rsi.value - 50) * 1.2
            components["rsi"] = {"raw": rsi_score, "weighted": max(0, min(100, rsi_score)) * 0.20}
        else:
            components["rsi"] = {"raw": 50, "weighted": 50 * 0.20}

        # MACD (weight 0.20): histogram positive = bullish momentum
        if macd.histogram is not None:
            macd_score = 50 + min(max(macd.histogram / (abs(price) * 0.01 + 1e-6), -1), 1) * 50
            components["macd"] = {"raw": macd_score, "weighted": max(0, min(100, macd_score)) * 0.20}
        else:
            components["macd"] = {"raw": 50, "weighted": 50 * 0.20}

        # Trend (EMA cross, weight 0.20)
        if ema_fast.initialized and ema_slow.initialized:
            trend_score = 70 if ema_fast.value > ema_slow.value else 30
            # Scale by separation magnitude
            sep_pct = abs(ema_fast.value - ema_slow.value) / ema_slow.value if ema_slow.value else 0
            trend_score = 50 + (trend_score - 50) * min(1, sep_pct * 20)
            components["trend"] = {"raw": trend_score, "weighted": trend_score * 0.20}
        else:
            components["trend"] = {"raw": 50, "weighted": 50 * 0.20}

        # ADX (weight 0.15): high ADX + positive DI = strong uptrend
        if adx.initialized and adx.plus_di is not None:
            if adx.value > 25:  # Strong trend
                adx_direction = 1 if adx.plus_di > adx.minus_di else -1
                adx_score = 50 + adx_direction * min(adx.value, 60) * 0.7
            else:
                adx_score = 50  # Weak/no trend — neutral
            components["adx"] = {"raw": adx_score, "weighted": max(0, min(100, adx_score)) * 0.15}
        else:
            components["adx"] = {"raw": 50, "weighted": 50 * 0.15}

        # Bollinger %B (weight 0.15): near lower band = oversold (bullish reversion), near upper = overbought
        if bb.pct_b is not None:
            if bb.pct_b > 1.0:
                bb_score = 25  # Above upper band — overbought
            elif bb.pct_b < 0.0:
                bb_score = 75  # Below lower band — oversold, bounce potential
            else:
                bb_score = 50 + (0.5 - bb.pct_b) * 60  # Inverse — lower in band = more bullish reversion bias
            components["bollinger"] = {"raw": bb_score, "weighted": max(0, min(100, bb_score)) * 0.15}
        else:
            components["bollinger"] = {"raw": 50, "weighted": 50 * 0.15}

        # Stochastic (weight 0.10)
        if stoch.k is not None:
            if stoch.k > 80:
                stoch_score = 30
            elif stoch.k < 20:
                stoch_score = 70
            else:
                stoch_score = 50
            components["stochastic"] = {"raw": stoch_score, "weighted": stoch_score * 0.10}
        else:
            components["stochastic"] = {"raw": 50, "weighted": 50 * 0.10}

        return components

    def _multi_timeframe(self, closes: list) -> Dict:
        """Approximate 1d / 1w / 1mo momentum scores from the closing price series."""
        result = {}

        def pct_change_score(window):
            if len(closes) <= window:
                return None
            change = (closes[-1] - closes[-1 - window]) / closes[-1 - window]
            # Map % change to 0-100 score, clipped
            score = 50 + change * 500
            return round(max(0, min(100, score)), 1)

        result["1d"] = pct_change_score(1)
        result["1w"] = pct_change_score(5)
        result["1mo"] = pct_change_score(21)

        return result

    def batch_score(self, price_data: Dict[str, "pd.DataFrame"]) -> Dict[str, Dict]:
        """Run score() across multiple tickers. price_data: {ticker: dataframe}."""
        results = {}
        for ticker, df in price_data.items():
            try:
                results[ticker] = self.score(ticker, df)
            except Exception as e:
                results[ticker] = {"error": str(e), "ticker": ticker, "score": 50}
        return results
