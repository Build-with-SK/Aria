"""Transparent descriptive technical consensus. No orders or win probabilities.

Versioned rules are documented in docs/TECHNICAL_RECOMMENDATIONS.md.
Heavy numeric dependencies are imported only when called.
"""
from __future__ import annotations

VERSION = "aria-technical-1"
TIMEFRAMES = {
    "1m": ("1m", "7d"), "5m": ("5m", "1mo"),
    "15m": ("15m", "1mo"), "30m": ("30m", "1mo"),
    "1h": ("60m", "2y"), "5h": ("60m", "2y"),
    "1d": ("1d", "2y"), "1w": ("1wk", "10y"), "1M": ("1mo", "max"),
}
ALIASES = {"1wk": "1w"}
MINUTES = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "5h": 300}


def classify(score):
    if score >= .6:
        return "Strong Buy"
    if score > .15:
        return "Buy"
    if score <= -.6:
        return "Strong Sell"
    if score < -.15:
        return "Sell"
    return "Neutral"


def consensus(signals):
    counts = {k: sum(v["signal"] == k for v in signals.values())
              for k in ("buy", "neutral", "sell")}
    n = sum(counts.values())
    score = (counts["buy"] - counts["sell"]) / n if n else 0.
    return {**counts, "score": score, "label": classify(score)}


def score_candles(df):
    """Pure indicator calculations; caller must validate identity/time/freshness."""
    import math
    import numpy as np
    from ta.momentum import RSIIndicator, StochasticOscillator, StochRSIIndicator, WilliamsRIndicator, ROCIndicator
    from ta.trend import MACD, ADXIndicator, CCIIndicator, IchimokuIndicator
    from ta.volatility import AverageTrueRange, BollingerBands
    c, h, l = df.Close, df.High, df.Low
    price = float(c.iloc[-1])
    mas, indicators = {}, {}

    def add(group, name, value, direction, **extra):
        if value is not None and math.isfinite(float(value)):
            group[name] = {"value": float(value), "signal": direction, **extra}

    def sign(x, band=0.):
        return "buy" if x > band else "sell" if x < -band else "neutral"

    for kind, periods in (("SMA", (20, 50, 100, 200)), ("EMA", (9, 21, 50, 200))):
        for p in periods:
            if len(c) < p:
                continue
            value = (c.rolling(p).mean() if kind == "SMA" else c.ewm(span=p, adjust=False, min_periods=p).mean()).iloc[-1]
            add(mas, f"{kind}{p}", value, sign(price - value, abs(price) * .001))
    rsi_series = RSIIndicator(c, 14).rsi()
    # Wilder's flat-series RSI is conventionally neutral, not ta's 100.
    rsi_series = rsi_series.mask(c.diff().abs().rolling(14).sum() == 0, 50.)
    rsi = rsi_series.iloc[-1]
    add(indicators, "RSI14", rsi, sign(rsi - 50, 5))
    stoch = StochasticOscillator(h, l, c, 14, 3)
    k, d = stoch.stoch().iloc[-1], stoch.stoch_signal().iloc[-1]
    add(indicators, "Stochastic14", k, sign(k - d, 2), signal_line=float(d))
    sr = StochRSIIndicator(c, 14, 3, 3)
    sk, sd = sr.stochrsi_k().iloc[-1], sr.stochrsi_d().iloc[-1]
    add(indicators, "StochRSI14", sk * 100, sign(sk - sd, .02))
    macd = MACD(c, 26, 12, 9)
    hist = macd.macd_diff().iloc[-1]
    add(indicators, "MACD12_26_9", hist, sign(hist, abs(price) * .0001))
    adx = ADXIndicator(h, l, c, 14)
    av, pos, neg = adx.adx().iloc[-1], adx.adx_pos().iloc[-1], adx.adx_neg().iloc[-1]
    add(indicators, "ADX14", av, sign(pos - neg) if av >= 25 else "neutral", plus_di=float(pos), minus_di=float(neg))
    wr = WilliamsRIndicator(h, l, c, 14).williams_r().iloc[-1]
    add(indicators, "WilliamsR14", wr, sign(wr + 50, 10))
    cci = CCIIndicator(h, l, c, 20).cci().iloc[-1]
    add(indicators, "CCI20", cci, sign(cci, 100))
    roc = ROCIndicator(c, 12).roc().iloc[-1]
    add(indicators, "ROC12", roc, sign(roc, .1))
    if len(c) >= 78:
        ichi = IchimokuIndicator(h, l, 9, 26, 52, visual=False)
        # The cloud visible NOW was calculated 26 candles ago. No future shift.
        a, b = ichi.ichimoku_a().shift(26).iloc[-1], ichi.ichimoku_b().shift(26).iloc[-1]
        add(indicators, "IchimokuCloud", (a + b) / 2, "buy" if price > max(a, b) else "sell" if price < min(a, b) else "neutral")
    ma, osc = consensus(mas), consensus(indicators)
    score = .5 * ma["score"] + .5 * osc["score"]
    atr_series = AverageTrueRange(h, l, c, 14).average_true_range()
    atr = float(atr_series.iloc[-1])
    bb = BollingerBands(c, 20, 2)
    width = bb.bollinger_wband().iloc[-1]
    prior_atr = atr_series.iloc[-21:-1].median()
    volume_ratio = None
    if "Volume" in df and len(df) >= 21:
        baseline = df.Volume.iloc[-21:-1].mean()
        if baseline > 0 and np.isfinite(df.Volume.iloc[-1]):
            volume_ratio = float(df.Volume.iloc[-1] / baseline)
    recent, previous = df.iloc[-20:], df.iloc[-40:-20]
    structure = "mixed"
    if len(previous) == 20:
        if recent.High.max() > previous.High.max() and recent.Low.min() > previous.Low.min():
            structure = "higher highs / higher lows"
        elif recent.High.max() < previous.High.max() and recent.Low.min() < previous.Low.min():
            structure = "lower highs / lower lows"
    return {
        "methodology": VERSION, "price": price, "summary": classify(score), "score": score,
        "counts": {k: ma[k] + osc[k] for k in ("buy", "sell", "neutral")},
        "moving_averages": {"label": ma["label"], **{k: v["signal"] for k, v in mas.items()}},
        "oscillators": {"label": osc["label"], **{k: v["signal"] for k, v in indicators.items()}},
        "indicator_values": indicators, "moving_average_values": mas,
        "trend": ma["label"], "momentum": osc["label"],
        "volatility": {"atr14": atr, "atr_pct": atr / abs(price) * 100 if price else None,
                       "bollinger_width_pct": float(width) if np.isfinite(width) else None,
                       "regime": "expanding" if atr > prior_atr * 1.2 else "contracting" if atr < prior_atr * .8 else "typical"},
        "confirmation": {"volume_ratio20": volume_ratio, "support20": float(l.iloc[-21:-1].min()),
                         "resistance20": float(h.iloc[-21:-1].max()), "structure": structure},
        "evidence": {"bars": len(df), "ma_votes": len(mas), "indicator_votes": len(indicators),
                     "expected_ma_votes": 8, "expected_indicator_votes": 9,
                     "warmup_complete": len(df) >= 200, "probability": None,
                     "note": "Descriptive consensus; correlated indicators, no calibrated probability."},
    }


def closed_candles(df, timeframe, now=None):
    """Validate OHLCV and timezone, discard open bars; never fill missing prices.

    Provider timestamps denote bar opens. Intraday uses absolute UTC grids.
    Daily/calendar bars use a conservative next local midnight/period boundary.
    """
    import numpy as np
    import pandas as pd
    from pandas.tseries.offsets import DateOffset
    timeframe = ALIASES.get(timeframe, timeframe)
    if timeframe not in TIMEFRAMES:
        raise ValueError("unsupported timeframe")
    if df is None or df.empty or not {"Open", "High", "Low", "Close"} <= set(df.columns):
        raise ValueError("missing OHLC data")
    if not isinstance(df.index, pd.DatetimeIndex) or df.index.tz is None:
        raise ValueError("timezone-aware candle timestamps required")
    if not df.index.is_monotonic_increasing or df.index.has_duplicates:
        raise ValueError("unordered or duplicate candles")
    values = df[["Open", "High", "Low", "Close"]].to_numpy(dtype=float)
    if not np.isfinite(values).all():
        raise ValueError("non-finite OHLC prices")
    if ((df.High < df[["Open", "Close", "Low"]].max(axis=1)) | (df.Low > df[["Open", "Close", "High"]].min(axis=1))).any():
        raise ValueError("malformed OHLC range")
    if "Volume" in df and ((df.Volume < 0).any() or np.isinf(df.Volume).any()):
        raise ValueError("invalid volume")
    now = pd.Timestamp(now or pd.Timestamp.now(tz="UTC"))
    if now.tzinfo is None:
        raise ValueError("timezone-aware as-of required")
    if (df.index > now).any():
        raise ValueError("future candle timestamp")
    duration = pd.Timedelta(minutes=MINUTES[timeframe]) if timeframe in MINUTES else None
    if timeframe in MINUTES:
        # 5h enters here after explicit hourly resampling by load_frame().
        ends = df.index + duration
    else:
        offset = {"1d": DateOffset(days=1), "1w": DateOffset(weeks=1), "1M": DateOffset(months=1)}[timeframe]
        if timeframe == "1w" and any(df.index.dayofweek != 0):
            raise ValueError("weekly candles must open Monday")
        if timeframe == "1M" and any(df.index.day != 1):
            raise ValueError("monthly candles must open on day 1")
        ends = pd.DatetimeIndex([ts.normalize() + offset for ts in df.index])
    complete = ends <= now
    out = df.loc[complete].copy()
    ends = ends[complete]
    if len(out) < 30:
        raise ValueError("insufficient closed history (minimum 30 bars; full warm-up 200)")
    if duration is not None:
        gaps = out.index.to_series().diff().dropna()
        if (gaps < duration).any() or not (gaps == duration).any():
            raise ValueError("candle timeframe mismatch")
        # Gaps can be exchange closures or missing data; neither is silently repaired.
        gap_count = int((gaps > duration).sum())
    else:
        gap_count = int(sum(out.index[i] > ends[i-1] for i in range(1, len(out))))
    age = float((now - ends[-1]).total_seconds())
    limit = float(duration.total_seconds() * 3) if duration is not None else {"1d": 5*86400, "1w": 14*86400, "1M": 62*86400}[timeframe]
    return out, {"data_as_of": out.index[-1].isoformat(), "last_closed_at": ends[-1].isoformat(),
                 "age_seconds": age, "data_stale": age > limit, "stale_after_seconds": limit,
                 "open_bars_excluded": int((~complete).sum()), "gap_count": gap_count,
                 "calendar_status": "unverified; gaps may include exchange closures",
                 "latency": "provider delay unknown; never labelled real-time"}


def aggregate_five_hours(df, now=None):
    """Five consecutive closed hourly candles per epoch-anchored UTC 5h bin."""
    import pandas as pd
    hourly, _ = closed_candles(df, "1h", now)
    hourly = hourly.tz_convert("UTC")
    grouped = hourly.resample("5h", origin="epoch", closed="left", label="left")
    out = grouped.agg({"Open": "first", "High": "max", "Low": "min", "Close": "last",
                       **({"Volume": "sum"} if "Volume" in hourly else {})})
    # Exact grid membership: no irregular/partial buckets, including session gaps.
    keep = []
    for start, group in grouped:
        if len(group) == 5 and group.index.equals(pd.date_range(start, periods=5, freq="h")):
            keep.append(start)
    return out.loc[keep]


def timeframe_conflicts(rows):
    order = list(TIMEFRAMES)
    directions = {"Strong Buy": 1, "Buy": 1, "Neutral": 0, "Sell": -1, "Strong Sell": -1}
    good = [(ALIASES.get(t, t), d) for t, d in rows.items() if not d.get("error") and not d.get("data_stale") and d.get("summary") in directions]
    good.sort(key=lambda item: order.index(item[0]))
    return [{"lower": a, "higher": b, "lower_signal": x["summary"], "higher_signal": y["summary"],
             "warning": f"{a} {x['summary']} conflicts with {b} {y['summary']}; no unconditional direction."}
            for i, (a, x) in enumerate(good) for b, y in good[i+1:]
            if directions[x["summary"]] * directions[y["summary"]] < 0]
