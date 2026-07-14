"""
indicators.py — Nautilus Bridge: Pure Python Indicators
Implements NautilusTrader-style technical indicators in pure Python.
No Cython, no compilation — same API surface (.update_raw(value), .value, .initialized).
"""

import math
from collections import deque
from typing import Optional


# ── Base ──────────────────────────────────────────────────────────────────────

class Indicator:
    """Base class matching Nautilus indicator API conventions."""
    def __init__(self, period: int):
        self.period = period
        self.value: Optional[float] = None
        self.initialized: bool = False
        self._count: int = 0

    def update_raw(self, value: float):
        raise NotImplementedError

    def reset(self):
        self.value = None
        self.initialized = False
        self._count = 0


# ── Moving Averages ───────────────────────────────────────────────────────────

class SMA(Indicator):
    """Simple Moving Average."""
    def __init__(self, period: int):
        super().__init__(period)
        self._buffer = deque(maxlen=period)

    def update_raw(self, value: float):
        self._buffer.append(value)
        self._count += 1
        if len(self._buffer) == self.period:
            self.value = sum(self._buffer) / self.period
            self.initialized = True

    def reset(self):
        super().reset()
        self._buffer.clear()


class EMA(Indicator):
    """Exponential Moving Average."""
    def __init__(self, period: int):
        super().__init__(period)
        self._alpha = 2 / (period + 1)
        self._seed_buffer = []

    def update_raw(self, value: float):
        self._count += 1
        if self.value is None:
            self._seed_buffer.append(value)
            if len(self._seed_buffer) == self.period:
                self.value = sum(self._seed_buffer) / self.period
                self.initialized = True
        else:
            self.value = self._alpha * value + (1 - self._alpha) * self.value

    def reset(self):
        super().reset()
        self._seed_buffer = []


class WMA(Indicator):
    """Weighted Moving Average — more weight to recent values."""
    def __init__(self, period: int):
        super().__init__(period)
        self._buffer = deque(maxlen=period)
        self._weights = list(range(1, period + 1))
        self._weight_sum = sum(self._weights)

    def update_raw(self, value: float):
        self._buffer.append(value)
        self._count += 1
        if len(self._buffer) == self.period:
            self.value = sum(v * w for v, w in zip(self._buffer, self._weights)) / self._weight_sum
            self.initialized = True

    def reset(self):
        super().reset()
        self._buffer.clear()


# ── Momentum / Oscillators ────────────────────────────────────────────────────

class RSI(Indicator):
    """Relative Strength Index using Wilder smoothing."""
    def __init__(self, period: int = 14, ma_type: str = "wilder"):
        super().__init__(period)
        self.ma_type = ma_type
        self._prev_value = None
        self._avg_gain = None
        self._avg_loss = None
        self._gains = deque(maxlen=period)
        self._losses = deque(maxlen=period)

    def update_raw(self, value: float):
        self._count += 1
        if self._prev_value is None:
            self._prev_value = value
            return

        change = value - self._prev_value
        gain = max(change, 0)
        loss = max(-change, 0)
        self._prev_value = value

        if self._avg_gain is None:
            self._gains.append(gain)
            self._losses.append(loss)
            if len(self._gains) == self.period:
                self._avg_gain = sum(self._gains) / self.period
                self._avg_loss = sum(self._losses) / self.period
                self._compute_rsi()
        else:
            # Wilder smoothing
            self._avg_gain = (self._avg_gain * (self.period - 1) + gain) / self.period
            self._avg_loss = (self._avg_loss * (self.period - 1) + loss) / self.period
            self._compute_rsi()

    def _compute_rsi(self):
        if self._avg_loss == 0:
            self.value = 100.0
        else:
            rs = self._avg_gain / self._avg_loss
            self.value = 100 - (100 / (1 + rs))
        self.initialized = True

    def reset(self):
        super().reset()
        self._prev_value = None
        self._avg_gain = None
        self._avg_loss = None
        self._gains.clear()
        self._losses.clear()


class MACD(Indicator):
    """Moving Average Convergence Divergence with signal line and histogram."""
    def __init__(self, fast: int = 12, slow: int = 26, signal: int = 9):
        super().__init__(slow)
        self.fast_period = fast
        self.slow_period = slow
        self.signal_period = signal
        self._ema_fast = EMA(fast)
        self._ema_slow = EMA(slow)
        self._signal_ema = EMA(signal)
        self.macd_line: Optional[float] = None
        self.signal_line: Optional[float] = None
        self.histogram: Optional[float] = None

    def update_raw(self, value: float):
        self._count += 1
        self._ema_fast.update_raw(value)
        self._ema_slow.update_raw(value)

        if self._ema_fast.initialized and self._ema_slow.initialized:
            self.macd_line = self._ema_fast.value - self._ema_slow.value
            self._signal_ema.update_raw(self.macd_line)
            if self._signal_ema.initialized:
                self.signal_line = self._signal_ema.value
                self.histogram = self.macd_line - self.signal_line
                self.value = self.macd_line
                self.initialized = True

    def reset(self):
        super().reset()
        self._ema_fast.reset()
        self._ema_slow.reset()
        self._signal_ema.reset()
        self.macd_line = self.signal_line = self.histogram = None


class Stochastic(Indicator):
    """Stochastic Oscillator — %K and %D."""
    def __init__(self, k_period: int = 14, d_period: int = 3):
        super().__init__(k_period)
        self.k_period = k_period
        self.d_period = d_period
        self._highs = deque(maxlen=k_period)
        self._lows = deque(maxlen=k_period)
        self._k_buffer = deque(maxlen=d_period)
        self.k: Optional[float] = None
        self.d: Optional[float] = None

    def update_raw(self, high: float, low: float, close: float):
        self._count += 1
        self._highs.append(high)
        self._lows.append(low)

        if len(self._highs) == self.k_period:
            highest = max(self._highs)
            lowest = min(self._lows)
            if highest == lowest:
                self.k = 50.0
            else:
                self.k = 100 * (close - lowest) / (highest - lowest)
            self._k_buffer.append(self.k)
            if len(self._k_buffer) == self.d_period:
                self.d = sum(self._k_buffer) / self.d_period
                self.value = self.k
                self.initialized = True

    def reset(self):
        super().reset()
        self._highs.clear()
        self._lows.clear()
        self._k_buffer.clear()
        self.k = self.d = None


# ── Volatility ─────────────────────────────────────────────────────────────────

class ATR(Indicator):
    """Average True Range — Wilder smoothed."""
    def __init__(self, period: int = 14):
        super().__init__(period)
        self._prev_close = None
        self._tr_buffer = deque(maxlen=period)

    def update_raw(self, high: float, low: float, close: float):
        self._count += 1
        if self._prev_close is None:
            tr = high - low
        else:
            tr = max(
                high - low,
                abs(high - self._prev_close),
                abs(low - self._prev_close),
            )
        self._prev_close = close
        self._tr_buffer.append(tr)

        if len(self._tr_buffer) == self.period and self.value is None:
            self.value = sum(self._tr_buffer) / self.period
            self.initialized = True
        elif self.value is not None:
            # Wilder smoothing
            self.value = (self.value * (self.period - 1) + tr) / self.period

    def reset(self):
        super().reset()
        self._prev_close = None
        self._tr_buffer.clear()


class BollingerBands(Indicator):
    """Bollinger Bands with %B and bandwidth."""
    def __init__(self, period: int = 20, k: float = 2.0):
        super().__init__(period)
        self.k = k
        self._buffer = deque(maxlen=period)
        self.upper: Optional[float] = None
        self.middle: Optional[float] = None
        self.lower: Optional[float] = None
        self.width: Optional[float] = None
        self.pct_b: Optional[float] = None

    def update_raw(self, value: float):
        self._count += 1
        self._buffer.append(value)

        if len(self._buffer) == self.period:
            mean = sum(self._buffer) / self.period
            variance = sum((x - mean) ** 2 for x in self._buffer) / self.period
            std = math.sqrt(variance)

            self.middle = mean
            self.upper = mean + self.k * std
            self.lower = mean - self.k * std
            self.width = (self.upper - self.lower) / self.middle if self.middle else 0
            if self.upper != self.lower:
                self.pct_b = (value - self.lower) / (self.upper - self.lower)
            self.value = self.middle
            self.initialized = True

    def reset(self):
        super().reset()
        self._buffer.clear()
        self.upper = self.middle = self.lower = self.width = self.pct_b = None


class KeltnerChannel(Indicator):
    """Keltner Channel — EMA midline with ATR-based bands. Used for squeeze detection."""
    def __init__(self, period: int = 20, atr_mult: float = 2.0):
        super().__init__(period)
        self.atr_mult = atr_mult
        self._ema = EMA(period)
        self._atr = ATR(period)
        self.upper: Optional[float] = None
        self.middle: Optional[float] = None
        self.lower: Optional[float] = None

    def update_raw(self, high: float, low: float, close: float):
        self._count += 1
        self._ema.update_raw(close)
        self._atr.update_raw(high, low, close)

        if self._ema.initialized and self._atr.initialized:
            self.middle = self._ema.value
            self.upper = self.middle + self.atr_mult * self._atr.value
            self.lower = self.middle - self.atr_mult * self._atr.value
            self.value = self.middle
            self.initialized = True

    def is_squeeze(self, bb: BollingerBands) -> bool:
        """True if Bollinger Bands are inside Keltner Channel — classic squeeze signal."""
        if not (self.initialized and bb.initialized):
            return False
        return bb.upper < self.upper and bb.lower > self.lower

    def reset(self):
        super().reset()
        self._ema.reset()
        self._atr.reset()
        self.upper = self.middle = self.lower = None


# ── Trend Strength ────────────────────────────────────────────────────────────

class ADX(Indicator):
    """Average Directional Index with +DI/-DI."""
    def __init__(self, period: int = 14):
        super().__init__(period)
        self._prev_high = None
        self._prev_low = None
        self._prev_close = None
        self._plus_dm_buf = deque(maxlen=period)
        self._minus_dm_buf = deque(maxlen=period)
        self._tr_buf = deque(maxlen=period)
        self._dx_buf = deque(maxlen=period)
        self.plus_di: Optional[float] = None
        self.minus_di: Optional[float] = None
        self._smoothed_plus_dm = None
        self._smoothed_minus_dm = None
        self._smoothed_tr = None

    def update_raw(self, high: float, low: float, close: float):
        self._count += 1
        if self._prev_high is None:
            self._prev_high, self._prev_low, self._prev_close = high, low, close
            return

        up_move = high - self._prev_high
        down_move = self._prev_low - low

        plus_dm = up_move if (up_move > down_move and up_move > 0) else 0
        minus_dm = down_move if (down_move > up_move and down_move > 0) else 0

        tr = max(
            high - low,
            abs(high - self._prev_close),
            abs(low - self._prev_close),
        )

        self._prev_high, self._prev_low, self._prev_close = high, low, close

        if self._smoothed_tr is None:
            self._plus_dm_buf.append(plus_dm)
            self._minus_dm_buf.append(minus_dm)
            self._tr_buf.append(tr)
            if len(self._tr_buf) == self.period:
                self._smoothed_plus_dm = sum(self._plus_dm_buf)
                self._smoothed_minus_dm = sum(self._minus_dm_buf)
                self._smoothed_tr = sum(self._tr_buf)
                self._update_di_and_adx()
        else:
            self._smoothed_plus_dm = self._smoothed_plus_dm - (self._smoothed_plus_dm / self.period) + plus_dm
            self._smoothed_minus_dm = self._smoothed_minus_dm - (self._smoothed_minus_dm / self.period) + minus_dm
            self._smoothed_tr = self._smoothed_tr - (self._smoothed_tr / self.period) + tr
            self._update_di_and_adx()

    def _update_di_and_adx(self):
        if self._smoothed_tr == 0:
            return
        self.plus_di = 100 * self._smoothed_plus_dm / self._smoothed_tr
        self.minus_di = 100 * self._smoothed_minus_dm / self._smoothed_tr

        di_sum = self.plus_di + self.minus_di
        if di_sum == 0:
            dx = 0
        else:
            dx = 100 * abs(self.plus_di - self.minus_di) / di_sum

        self._dx_buf.append(dx)
        if len(self._dx_buf) == self.period:
            self.value = sum(self._dx_buf) / self.period
            self.initialized = True

    def reset(self):
        super().reset()
        self._prev_high = self._prev_low = self._prev_close = None
        self._plus_dm_buf.clear()
        self._minus_dm_buf.clear()
        self._tr_buf.clear()
        self._dx_buf.clear()
        self.plus_di = self.minus_di = None
        self._smoothed_plus_dm = self._smoothed_minus_dm = self._smoothed_tr = None


# ── Volume ─────────────────────────────────────────────────────────────────────

class VWAP(Indicator):
    """Volume-Weighted Average Price — resets each session in typical usage."""
    def __init__(self):
        super().__init__(period=0)
        self._cum_pv = 0.0
        self._cum_vol = 0.0

    def update_raw(self, price: float, volume: float):
        self._count += 1
        self._cum_pv += price * volume
        self._cum_vol += volume
        if self._cum_vol > 0:
            self.value = self._cum_pv / self._cum_vol
            self.initialized = True

    def reset(self):
        super().reset()
        self._cum_pv = 0.0
        self._cum_vol = 0.0


class VolumeOscillator(Indicator):
    """Volume momentum — fast EMA of volume vs slow EMA of volume, as % difference."""
    def __init__(self, fast: int = 5, slow: int = 20):
        super().__init__(slow)
        self._ema_fast = EMA(fast)
        self._ema_slow = EMA(slow)

    def update_raw(self, volume: float):
        self._count += 1
        self._ema_fast.update_raw(volume)
        self._ema_slow.update_raw(volume)
        if self._ema_fast.initialized and self._ema_slow.initialized and self._ema_slow.value:
            self.value = 100 * (self._ema_fast.value - self._ema_slow.value) / self._ema_slow.value
            self.initialized = True

    def reset(self):
        super().reset()
        self._ema_fast.reset()
        self._ema_slow.reset()
