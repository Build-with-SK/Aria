"""
tests/research_fixtures.py
==========================
A synthetic, deterministic world for the research-engine tests.

Prices come from known factors with known loadings, so attribution can be
checked against the betas that generated them, and an idiosyncratic jump is
injected into AAPL's last 21 sessions so the UNEXPLAINED_RESIDUAL has a known
size. Frames run well past T, so a test can rewrite the future and require
nothing at T to move, and rewrite the past and require something to.

Every non-price record has traps on both sides of T: a fundamentals snapshot
taken after T, an earnings date first observed after T, a news item published
before T but seen after it, a macro release published after T.

Not a test module (no `test_` prefix).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from src.intelligence.research.pit_data import (Event, FrameResearchData,
                                                FundamentalSnapshot, MacroObservation,
                                                NewsItem)

UTC = timezone.utc
NY = ZoneInfo("America/New_York")
#: Tuesday 15 Sep 2026, 17:00 New York — the session has closed and settled.
T = datetime(2026, 9, 15, 17, 0, tzinfo=NY).astimezone(UTC)
DATES = pd.bdate_range("2022-06-01", "2026-12-31")
LAST_IN = DATES[DATES <= pd.Timestamp("2026-09-15")]
WINDOW = LAST_IN[-21:]                          # the 21 sessions ending at T
JUMP = 0.004                                    # per-day idiosyncratic drift in the window


def build_frames(seed: int = 11) -> dict:
    rng = np.random.default_rng(seed)
    n = len(DATES)

    def N(s):
        return rng.normal(0.0, s, n)
    m = rng.normal(0.0004, 0.010, n)
    tech = N(0.007)
    size, val, mom, qual, lowv = N(0.004), N(0.004), N(0.004), N(0.004), N(0.004)
    rates, credit, usd, cmdty, crypto = N(0.004), N(0.003), N(0.003), N(0.010), N(0.030)
    jp = N(0.006)
    fxjpy = N(0.004)
    aapl_idio = N(0.003)
    in_window = np.isin(DATES, WINDOW)
    aapl_idio = aapl_idio + np.where(in_window, JUMP, 0.0)
    r = {
        "SPY": m + N(0.0005), "ACWI": 0.9 * m + N(0.002), "IWM": m + size, "IWD": m + val,
        "IWF": m - val, "MTUM": m + mom, "QUAL": m + qual, "USMV": m + lowv,
        "IEF": rates, "TLT": 1.8 * rates + N(0.003), "HYG": rates + credit, "UUP": usd,
        "DBC": cmdty, "GLD": -0.5 * usd + N(0.008), "QQQ": 1.1 * m + 0.6 * tech + N(0.002),
        "XLK": m + tech + N(0.002), "XLE": m + 0.8 * cmdty + N(0.004),
        "^VIX": -3.0 * m + N(0.03), "BTC-USD": 0.5 * m + crypto,
        "AAPL": 1.2 * m + 0.8 * tech + aapl_idio,
        "EWJ": 0.7 * m + jp, "7203.T": 0.6 * m + 1.1 * jp + N(0.008),
        "USDJPY=X": fxjpy,
    }
    start = {"7203.T": 2500.0, "USDJPY=X": 150.0, "BTC-USD": 60000.0, "^VIX": 18.0}
    frames = {}
    for s, x in r.items():
        p = start.get(s, 100.0) * np.exp(np.cumsum(x))
        vol = rng.integers(2_000_000, 6_000_000, n).astype(float)
        if s in ("USDJPY=X", "^VIX"):
            vol = np.zeros(n)
        frames[s] = pd.DataFrame({"Open": p, "High": p * 1.005, "Low": p * 0.995, "Close": p,
                                  "Volume": vol}, index=DATES)
    return frames


def at_utc(y, mo, d, h=0, mi=0, tz=UTC):
    return datetime(y, mo, d, h, mi, tzinfo=tz).astimezone(UTC)


def fundamentals() -> list:
    base = {"trailingPE": 30.0, "forwardPE": 27.0, "revenueGrowth": 0.06,
            "earningsGrowth": 0.09, "profitMargins": 0.25, "sector": "Technology"}
    return [
        FundamentalSnapshot("AAPL", dict(base), at_utc(2026, 6, 1, 12), "vendor-a"),
        FundamentalSnapshot("AAPL", {**base, "revenueGrowth": 0.05}, at_utc(2026, 9, 1, 12),
                            "vendor-a"),
        # Taken AFTER T, and very different: must never be seen at T.
        FundamentalSnapshot("AAPL", {**base, "revenueGrowth": -0.40, "forwardPE": 60.0},
                            at_utc(2026, 9, 20, 12), "vendor-a"),
    ]


def events() -> list:
    return [
        Event("EARNINGS:AAPL:2026-07-30", "EARNINGS", "AAPL", at_utc(2026, 7, 30, 16, 0, NY),
              "vendor", "HIGH", "results", observed_at=at_utc(2026, 7, 1),
              schedule={"eps_estimate": 1.89}, outcome={"reported_eps": 2.02,
                                                        "surprise_pct": 6.9}),
        Event("EARNINGS:AAPL:2026-10-01", "EARNINGS", "AAPL", at_utc(2026, 10, 1, 16, 0, NY),
              "vendor", "HIGH", "results", observed_at=at_utc(2026, 9, 1),
              schedule={"eps_estimate": 1.98}),
        # Scheduled inside the horizon but first observed AFTER T: invisible at T.
        Event("EARNINGS:AAPL:2026-09-30-late", "EARNINGS", "AAPL", at_utc(2026, 9, 30, 16, 0, NY),
              "vendor", "HIGH", "results", observed_at=at_utc(2026, 9, 16)),
        Event("DIVIDEND:AAPL:2026-08-10", "DIVIDEND", "AAPL", at_utc(2026, 8, 10, 0, 0, NY),
              "vendor", "LOW", "mechanical", outcome={"amount": 0.27}),
        # A future split with no publication time: invisible until it happens.
        Event("SPLIT:AAPL:2026-09-25", "SPLIT", "AAPL", at_utc(2026, 9, 25, 0, 0, NY),
              "vendor", "MEDIUM", "corporate action", outcome={"ratio": 4.0}),
        # Observed, then withdrawn before T.
        Event("EARNINGS:AAPL:2026-09-22-withdrawn", "EARNINGS", "AAPL",
              at_utc(2026, 9, 22, 16, 0, NY), "vendor", "HIGH", "results",
              observed_at=at_utc(2026, 8, 1), withdrawn_at=at_utc(2026, 9, 10)),
    ]


def _news(i, headline, publisher, published, seen, hint="AAPL", backend="googlenews",
          url=None):
    return NewsItem(f"n{i}", headline, publisher, url or f"https://example.com/{i}", backend,
                    published, seen, hint)


def news() -> list:
    t0 = at_utc(2026, 9, 14, 13)
    syndicated = [_news(i, "Apple (AAPL) raises outlook after record iPhone demand",
                        f"outlet-{i}", t0 + timedelta(minutes=i), t0 + timedelta(minutes=i + 5))
                  for i in range(15)]
    return syndicated + [
        _news(100, "Apple (AAPL) raises outlook after record iPhone demand", "outlet-0",
              t0 + timedelta(hours=3), t0 + timedelta(hours=3, minutes=1)),        # repeat
        _news(101, "Apple (AAPL) faces antitrust probe over App Store fees", "wire",
              at_utc(2026, 9, 5, 12), at_utc(2026, 9, 5, 12, 5)),                  # stale
        _news(102, "Bill to tax fruit imports advances in committee", "politics",
              at_utc(2026, 9, 14, 20), at_utc(2026, 9, 14, 20, 5), hint="BILL"),   # weak
        _news(103, "Apple (AAPL) shares plunge after supply warning", "wire",
              at_utc(2026, 9, 16, 12), at_utc(2026, 9, 16, 12, 1)),                # future
        _news(104, "Apple (AAPL) wins approval in EU court", "wire",
              at_utc(2026, 9, 15, 12), at_utc(2026, 9, 17, 9)),                    # seen after T
    ]


def macro() -> list:
    return [
        MacroObservation("cpi_yoy", 3.1, "percent", date(2026, 8, 31), None,
                         at_utc(2026, 9, 11, 20), "BLS", "monthly"),
        MacroObservation("unemployment_rate", 4.2, "percent", date(2026, 8, 31), None,
                         at_utc(2026, 9, 5, 20), "BLS", "monthly"),
        MacroObservation("cpi_yoy", 4.9, "percent", date(2026, 9, 30), None,
                         at_utc(2026, 10, 14, 20), "BLS", "monthly"),               # after T
    ]


def model_records() -> list:
    from intel_helpers import row
    return [
        row(1, source="technical", subject="AAPL", created="2026-09-14T22:30:00",
            horizon=10, resolved=False),
        row(2, source="v5", subject="AAPL", created="2026-09-01T22:30:00", horizon=45,
            resolved=False, kind="stated", p_correct=0.56),
    ]


def world(**overrides) -> FrameResearchData:
    kw = dict(fundamentals=fundamentals(), events=events(), news=news(), macro=macro(),
              model_records=model_records())
    kw.update(overrides)
    frames = kw.pop("frames", None) or build_frames()
    return FrameResearchData(frames, **kw)
