"""
src.nautilus_bridge — Pure Python NautilusTrader-style indicators.
No Cython, no compilation. Same API conventions (.update_raw(), .value, .initialized).
"""

from .indicators import (
    Indicator, SMA, EMA, WMA, RSI, MACD, ATR,
    BollingerBands, Stochastic, ADX, VWAP,
    KeltnerChannel, VolumeOscillator,
)
from .signal_aggregator import SignalAggregator

__all__ = [
    "Indicator", "SMA", "EMA", "WMA", "RSI", "MACD", "ATR",
    "BollingerBands", "Stochastic", "ADX", "VWAP",
    "KeltnerChannel", "VolumeOscillator",
    "SignalAggregator",
]
