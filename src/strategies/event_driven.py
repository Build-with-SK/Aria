# src/strategies/event_driven.py
"""
Event Driven Strategy
Earnings announcements, dividend ex-dates, stock splits, M&A signals.
Pre-event positioning, post-earnings drift, M&A arbitrage stub.
"""

from __future__ import annotations
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class CorporateEvent:
    ticker: str
    event_type: str     # earnings | dividend | split | ma_target | ma_acquirer
    event_date: str     # ISO format
    days_until: int
    expected_move_pct: float    # IV-implied or historical
    recommended_action: str     # "long" | "short" | "neutral" | "straddle"
    conviction: float
    rationale: str
    entry_days_before: int
    exit_days_after: int


@dataclass
class EventCalendar:
    events: List[CorporateEvent]
    active_positions: List[dict]    # positions currently held
    upcoming_count: int
    earnings_count: int
    ma_count: int
    strategy_score: float
    notes: List[str]

    def _to_json(self) -> dict:
        return {
            "events": [vars(e) for e in self.events],
            "active_positions": self.active_positions,
            "upcoming_count": self.upcoming_count,
            "earnings_count": self.earnings_count,
            "ma_count": self.ma_count,
            "strategy_score": round(self.strategy_score, 2),
            "notes": self.notes,
        }


_MA_KEYWORDS = [
    "acqui", "merger", "takeover", "buyout", "acquisition",
    "bid for", "deal with", "offer for", "purchase of", "tender offer",
    "private equity", "leveraged buyout", "lbo",
]


def _detect_ma_from_sentiment(
    ticker: str,
    sentiment_results: Optional[dict],
) -> Optional[CorporateEvent]:
    """Scan sentiment news headlines for M&A keywords."""
    if sentiment_results is None:
        return None
    try:
        headlines = sentiment_results.get(ticker, {}).get("headlines", [])
        for h in headlines:
            h_lower = str(h).lower()
            if any(kw in h_lower for kw in _MA_KEYWORDS):
                return CorporateEvent(
                    ticker=ticker,
                    event_type="ma_target",
                    event_date=datetime.now().strftime("%Y-%m-%d"),
                    days_until=0,
                    expected_move_pct=15.0,     # M&A premium heuristic
                    recommended_action="long",
                    conviction=60.0,
                    rationale=f"M&A keyword detected in news: '{h[:80]}'",
                    entry_days_before=0,
                    exit_days_after=5,
                )
    except Exception:
        pass
    return None


_ETF_NO_EARNINGS = {
    "SPY","QQQ","IWM","DIA","TLT","IEF","AGG","TIP",
    "HYG","JNK","LQD","VCIT","BKLN","SJNK","SHYG","CWB",
    "GLD","SLV","USO",
}

def _fetch_earnings_events(ticker: str, df: Optional[pd.DataFrame]) -> Optional[CorporateEvent]:
    """Try to get next earnings date from yfinance calendar."""
    if ticker in _ETF_NO_EARNINGS:
        return None
    try:
        import yfinance as yf
        stock = yf.Ticker(ticker)
        cal = stock.calendar
        if cal is None:
            return None
        # calendar may be a DataFrame or dict depending on yfinance version
        if isinstance(cal, dict):
            date_val = cal.get("Earnings Date")
            if date_val and len(date_val) > 0:
                earn_date = pd.Timestamp(date_val[0])
            else:
                return None
        elif isinstance(cal, pd.DataFrame):
            if "Earnings Date" in cal.index:
                earn_date = pd.Timestamp(cal.loc["Earnings Date"].iloc[0])
            else:
                return None
        else:
            return None

        today = pd.Timestamp.now()
        days_until = (earn_date - today).days

        if days_until < -5 or days_until > 30:
            return None     # too far out or already past

        # Estimate expected move from historical earnings vol
        hist_move = _estimate_earnings_move(df)
        action = "straddle" if hist_move > 5 else "neutral"

        return CorporateEvent(
            ticker=ticker,
            event_type="earnings",
            event_date=earn_date.strftime("%Y-%m-%d"),
            days_until=int(days_until),
            expected_move_pct=round(hist_move, 2),
            recommended_action=action,
            conviction=55.0,
            rationale=f"Earnings in {days_until}d, hist move ±{hist_move:.1f}%",
            entry_days_before=5,
            exit_days_after=1,
        )
    except Exception:
        return None


def _estimate_earnings_move(df: Optional[pd.DataFrame]) -> float:
    """Estimate expected earnings move from 1-day return distribution."""
    if df is None or df.empty or len(df) < 60:
        return 4.0
    ret = df["Close"].pct_change().dropna()
    # 90th percentile of daily absolute returns as proxy for earnings surprise magnitude
    return float(ret.abs().quantile(0.90) * 100)


def _fetch_dividend_events(ticker: str) -> Optional[CorporateEvent]:
    """Get upcoming ex-dividend date."""
    if ticker in _ETF_NO_EARNINGS:
        return None
    try:
        import yfinance as yf
        stock = yf.Ticker(ticker)
        cal = stock.calendar
        if cal is None:
            return None
        if isinstance(cal, dict):
            ex_date = cal.get("Ex-Dividend Date")
        elif isinstance(cal, pd.DataFrame):
            ex_date = cal.loc["Ex-Dividend Date"].iloc[0] if "Ex-Dividend Date" in cal.index else None
        else:
            return None

        if ex_date is None:
            return None
        ex_dt = pd.Timestamp(ex_date)
        days_until = (ex_dt - pd.Timestamp.now()).days

        if not (0 < days_until <= 10):
            return None

        return CorporateEvent(
            ticker=ticker,
            event_type="dividend",
            event_date=ex_dt.strftime("%Y-%m-%d"),
            days_until=int(days_until),
            expected_move_pct=-0.3,     # typically small drop on ex-date
            recommended_action="long",  # capture dividend, exit on ex-date
            conviction=50.0,
            rationale=f"Ex-dividend in {days_until}d — capture yield",
            entry_days_before=3,
            exit_days_after=0,
        )
    except Exception:
        return None


def _post_earnings_drift_signal(
    ticker: str,
    df: pd.DataFrame,
    sentiment_scores: Optional[dict],
) -> Optional[dict]:
    """
    If recent earnings beat + positive sentiment → post-earnings drift long.
    Proxy: if last 1-day return was very large (>3%) AND positive sentiment.
    """
    try:
        if len(df) < 5:
            return None
        last_ret = float(df["Close"].pct_change().iloc[-1])
        sent_score = (sentiment_scores or {}).get(ticker, {})
        if isinstance(sent_score, dict):
            sent_score = sent_score.get("score", 0)
        if last_ret > 0.03 and float(sent_score) > 10:
            return {
                "ticker": ticker,
                "type": "post_earnings_drift",
                "direction": "long",
                "days_remaining": 5,
                "conviction": 65,
                "rationale": f"Large +ret {last_ret*100:.1f}% + positive sentiment → PEAD signal",
            }
    except Exception:
        pass
    return None


def run_event_driven(
    featured_data: Dict[str, pd.DataFrame],
    config: dict,
    sentiment_results: Optional[dict] = None,
) -> EventCalendar:
    """
    Scan universe for upcoming corporate events.
    Returns EventCalendar with events and active positions.
    """
    cfg = config.get("strategies", {}).get("event_driven", {})
    entry_days_before = int(cfg.get("entry_days_before", 5))
    exit_days_after = int(cfg.get("exit_days_after", 1))
    max_tickers = int(cfg.get("max_tickers_scanned", 30))

    notes = []
    events: List[CorporateEvent] = []
    active_positions: List[dict] = []

    skip_suffixes = ["=X", "-USD", "=F"]
    equity_tickers = [
        t for t in featured_data
        if not any(t.endswith(s) for s in skip_suffixes)
        and featured_data[t] is not None
        and not featured_data[t].empty
    ][:max_tickers]

    earnings_count = 0
    ma_count = 0

    for ticker in equity_tickers:
        df = featured_data.get(ticker)

        # Earnings
        earn_event = _fetch_earnings_events(ticker, df)
        if earn_event:
            events.append(earn_event)
            earnings_count += 1

        # Dividend
        div_event = _fetch_dividend_events(ticker)
        if div_event:
            events.append(div_event)

        # M&A detection from sentiment
        ma_event = _detect_ma_from_sentiment(ticker, sentiment_results)
        if ma_event:
            events.append(ma_event)
            ma_count += 1

        # Post-earnings drift
        if df is not None and not df.empty:
            ped = _post_earnings_drift_signal(ticker, df, sentiment_results)
            if ped:
                active_positions.append(ped)

    # Sort events by days_until ascending
    events.sort(key=lambda e: e.days_until)

    notes.append(f"Scanned {len(equity_tickers)} tickers.")
    notes.append(f"Found {len(events)} events: {earnings_count} earnings, {ma_count} M&A signals.")
    if len(events) == 0:
        notes.append("No near-term corporate events detected (yfinance calendar may be limited).")

    # Strategy score: positive if more events with bullish action
    long_events = sum(1 for e in events if e.recommended_action == "long")
    short_events = sum(1 for e in events if e.recommended_action == "short")
    strategy_score = float(np.clip((long_events - short_events) * 10, -100, 100))

    return EventCalendar(
        events=events,
        active_positions=active_positions,
        upcoming_count=len(events),
        earnings_count=earnings_count,
        ma_count=ma_count,
        strategy_score=round(strategy_score, 2),
        notes=notes,
    )
