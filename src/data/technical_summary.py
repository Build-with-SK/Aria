"""
src/data/technical_summary.py
=============================
Investing.com-style Technical Summary — a deterministic consensus of moving
averages + oscillators, per timeframe, mapped to Strong Buy / Buy / Neutral /
Sell / Strong Sell. Replicates their published methodology from price data
(no scraping): 12 moving averages (SMA & EMA, periods 5-200; Buy if price is
above) and ~10 oscillators (RSI, Stochastic, StochRSI, MACD, ADX, Williams %R,
CCI, Ultimate Oscillator, ROC, Bull/Bear Power) each classified buy/sell/
neutral, then tallied.

Works for any yfinance symbol (US, .NS/.BO India, crypto -USD). Timeframes map
to yfinance intervals; each fetch pulls enough history to seed a 200-period MA.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# timeframe → (yfinance interval, yfinance period with enough bars for MA200)
TIMEFRAMES = {
    "5m":  ("5m", "5d"),
    "15m": ("15m", "1mo"),
    "30m": ("30m", "2mo"),
    "1h":  ("60m", "3mo"),
    "1d":  ("1d", "2y"),
    "1wk": ("1wk", "10y"),
}
DEFAULT_TFS = ["15m", "1h", "1d", "1wk"]
MA_PERIODS = [5, 10, 20, 50, 100, 200]


# ── pure scoring (unit-tested) ───────────────────────────────────────────────

def label_from_counts(buy: int, sell: int) -> str:
    """Investing.com-style bucket from buy vs sell tallies."""
    total = buy + sell
    if total == 0:
        return "Neutral"
    r = buy / total
    if r >= 0.75:
        return "Strong Buy"
    if r >= 0.55:
        return "Buy"
    if r <= 0.25:
        return "Strong Sell"
    if r <= 0.45:
        return "Sell"
    return "Neutral"


def tally(signals: dict) -> dict:
    buy = sum(1 for v in signals.values() if v == "buy")
    sell = sum(1 for v in signals.values() if v == "sell")
    neutral = sum(1 for v in signals.values() if v == "neutral")
    return {"buy": buy, "sell": sell, "neutral": neutral,
            "label": label_from_counts(buy, sell)}


def _ma_signals(df) -> dict:
    close = df["Close"]
    price = float(close.iloc[-1])
    out = {}
    for p in MA_PERIODS:
        if len(close) >= p:
            sma = float(close.rolling(p).mean().iloc[-1])
            ema = float(close.ewm(span=p, adjust=False).mean().iloc[-1])
            out[f"SMA{p}"] = "buy" if price > sma else "sell"
            out[f"EMA{p}"] = "buy" if price > ema else "sell"
    return out


def _osc_signals(df) -> dict:
    from ta.momentum import (RSIIndicator, StochasticOscillator,
                             StochRSIIndicator, WilliamsRIndicator,
                             ROCIndicator, UltimateOscillator)
    from ta.trend import MACD, ADXIndicator, CCIIndicator
    close, high, low = df["Close"], df["High"], df["Low"]
    o = {}

    def last(series):
        try:
            v = float(series.iloc[-1])
            return v if v == v else None      # NaN guard
        except Exception:
            return None

    rsi = last(RSIIndicator(close, 14).rsi())
    if rsi is not None:
        o["RSI(14)"] = "sell" if rsi > 70 else "buy" if rsi < 30 else "neutral"
    k = last(StochasticOscillator(high, low, close, window=14,
                                  smooth_window=3).stoch())
    if k is not None:
        o["STOCH(14,3)"] = "sell" if k > 80 else "buy" if k < 20 else "neutral"
    srsi = last(StochRSIIndicator(close, 14).stochrsi())
    if srsi is not None:
        srsi *= 100
        o["STOCHRSI(14)"] = "sell" if srsi > 80 else "buy" if srsi < 20 else "neutral"
    macd = MACD(close)
    ml, sl = last(macd.macd()), last(macd.macd_signal())
    if ml is not None and sl is not None:
        o["MACD(12,26)"] = "buy" if ml > sl else "sell"
    adx = ADXIndicator(high, low, close, 14)
    av, pos, neg = last(adx.adx()), last(adx.adx_pos()), last(adx.adx_neg())
    if av is not None and pos is not None and neg is not None:
        o["ADX(14)"] = ("buy" if pos > neg else "sell") if av > 20 else "neutral"
    wr = last(WilliamsRIndicator(high, low, close, 14).williams_r())
    if wr is not None:
        o["Williams %R"] = "buy" if wr < -80 else "sell" if wr > -20 else "neutral"
    cci = last(CCIIndicator(high, low, close, 14).cci())
    if cci is not None:
        o["CCI(14)"] = "buy" if cci > 100 else "sell" if cci < -100 else "neutral"
    roc = last(ROCIndicator(close, 12).roc())
    if roc is not None:
        o["ROC"] = "buy" if roc > 0 else "sell"
    uo = last(UltimateOscillator(high, low, close).ultimate_oscillator())
    if uo is not None:
        o["UltimateOsc"] = "sell" if uo > 70 else "buy" if uo < 30 else "neutral"
    ema13 = close.ewm(span=13, adjust=False).mean()
    e = last(ema13)
    if e is not None:
        bull = float(high.iloc[-1]) - e
        bear = float(low.iloc[-1]) - e
        o["Bull/BearPower"] = "buy" if (bull + bear) > 0 else "sell"
    return o


def score_frame(df) -> dict | None:
    """Full technical summary for one OHLC frame. None if too little data."""
    if df is None or "Close" not in getattr(df, "columns", []):
        return None
    df = df.dropna(subset=["Close"])          # drop incomplete/latest-NaN bars
    if len(df) < 30:
        return None
    ma = _ma_signals(df)
    osc = _osc_signals(df)
    ma_t, osc_t = tally(ma), tally(osc)
    overall = tally({**ma, **osc})
    return {
        "price": round(float(df["Close"].iloc[-1]), 4),
        "summary": overall["label"],
        "counts": {"buy": overall["buy"], "sell": overall["sell"],
                   "neutral": overall["neutral"]},
        "moving_averages": {"label": ma_t["label"], **{k: v for k, v in ma.items()}},
        "oscillators": {"label": osc_t["label"], **{k: v for k, v in osc.items()}},
    }


# ── data fetch + public API ──────────────────────────────────────────────────

def _resolve_yahoo(symbol: str) -> str:
    """Reuse the universe resolver so SAIL → SAIL.NS etc. Falls back to the
    symbol as given."""
    try:
        from src.data.universe import get_universe
        info = get_universe().resolve(symbol)
        if info and info.get("yahoo"):
            return info["yahoo"]
    except Exception:
        pass
    return symbol


def summary(symbol: str, timeframe: str = "1d") -> dict:
    """Technical summary for one symbol + timeframe."""
    import yfinance as yf
    yahoo = _resolve_yahoo(symbol)
    interval, period = TIMEFRAMES.get(timeframe, TIMEFRAMES["1d"])
    try:
        df = yf.Ticker(yahoo).history(period=period, interval=interval,
                                      auto_adjust=True)
    except Exception as e:
        return {"error": f"fetch failed for {yahoo} @ {timeframe}: {e}"}
    scored = score_frame(df)
    if not scored:
        return {"error": f"not enough {timeframe} data for {yahoo}"}
    return {"symbol": symbol, "yahoo": yahoo, "timeframe": timeframe, **scored}


def multi_timeframe(symbol: str, timeframes: list | None = None) -> dict:
    """Consensus across several timeframes — the investing.com summary table."""
    tfs = timeframes or DEFAULT_TFS
    yahoo = _resolve_yahoo(symbol)
    out = {"symbol": symbol, "yahoo": yahoo, "timeframes": {}}
    for tf in tfs:
        s = summary(symbol, tf)
        out["timeframes"][tf] = ({"summary": s["summary"], "counts": s["counts"]}
                                 if "error" not in s else {"error": s["error"]})
    labels = [v["summary"] for v in out["timeframes"].values() if "summary" in v]
    out["consensus"] = _consensus(labels)
    return out


_SCORE = {"Strong Buy": 2, "Buy": 1, "Neutral": 0, "Sell": -1, "Strong Sell": -2}
_INV = {2: "Strong Buy", 1: "Buy", 0: "Neutral", -1: "Sell", -2: "Strong Sell"}


def _consensus(labels: list) -> str:
    if not labels:
        return "Neutral"
    avg = sum(_SCORE.get(l, 0) for l in labels) / len(labels)
    return _INV[max(-2, min(2, round(avg)))]


def recommendations(symbols: list, timeframe: str = "1d",
                    want=("Strong Buy", "Buy", "Sell", "Strong Sell")) -> list:
    """Scan a list of symbols on one timeframe, return those with a directional
    consensus, ranked strongest-first. The investing.com 'recommendations' page.
    Daily timeframe by default (one fetch/symbol) so a scan stays feasible."""
    out = []
    for s in symbols:
        try:
            r = summary(s, timeframe)
        except Exception:
            continue
        if "error" in r or r["summary"] not in want:
            continue
        out.append({"symbol": s, "summary": r["summary"], "counts": r["counts"],
                    "price": r.get("price"), "score": _SCORE.get(r["summary"], 0)})
    out.sort(key=lambda x: (-abs(x["score"]), -x["counts"]["buy"]))
    return out


def scan_universe_recommendations(timeframe: str = "1d", limit: int = 25) -> dict:
    """Rank the strongest technical setups across the signal universe
    (equities/ETFs by |composite|, capped for a feasible scan)."""
    from src.desk.opinion import load_data_json
    sig = load_data_json("signals.json")
    ranked = sorted(
        ((t, d) for t, d in sig.items()
         if isinstance(d, dict) and (d.get("current_price") or 0) > 0
         and not any(c in t for c in ("=", "^"))),   # skip futures/indices
        key=lambda kv: -abs(kv[1].get("composite_score") or 0))
    symbols = [t for t, _ in ranked[:limit]]
    recs = recommendations(symbols, timeframe)
    return {"timeframe": timeframe, "scanned": len(symbols),
            "strong_buy": [r for r in recs if r["summary"] == "Strong Buy"],
            "buy": [r for r in recs if r["summary"] == "Buy"],
            "sell": [r for r in recs if r["summary"] in ("Sell", "Strong Sell")]}
