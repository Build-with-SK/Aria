"""
src/risk/fx.py
==============
FX for portfolio risk: every conversion carries its pair, its rate, the date
of the close it came from, and where that came from — and is read POINT IN
TIME, so a 2025 position is valued at a 2025 rate.

Native currencies come from src/data/currency (provider symbols, so BA is
Boeing in dollars and BA.L is BAE in pence). London's GBp is a quoting unit,
not a currency: 812.4 GBp is £8.124, folded in here as a factor of 0.01.

Lookup order for FROM -> TO at instant `at`:
  1. identity (same currency)
  2. the direct pair  FROMTO=X           (close known by `at`)
  3. the inverse pair TOFROM=X, inverted
  4. through USD: FROM->USD x USD->TO, each by 2-3, both legs recorded
A rate older than `max_age_days` at `at` is STALE and not used. Nothing is
filled from today's rate, a neighbouring date, or a constant: missing is
None, and the caller must treat the position's value as unknown.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Optional

#: Daily FX closes: older than this at the valuation instant is stale (a long
#: weekend plus a holiday is three days; five allows one more without letting
#: a dead feed pass as current).
MAX_AGE_DAYS = 5

#: Quoting units that are a fixed fraction of a currency.
SUBUNITS = {"GBp": ("GBP", 0.01), "GBX": ("GBP", 0.01), "ZAc": ("ZAR", 0.01),
            "ILA": ("ILS", 0.01)}


@dataclass(frozen=True)
class FxRate:
    from_ccy: str
    to_ccy: str
    rate: Optional[float]                 # 1 unit of from_ccy is `rate` to_ccy
    as_of: Optional[str] = None           # date of the close used (oldest leg)
    source: str = ""
    pairs: tuple = ()                     # every pair read, in order
    why: str = ""

    @property
    def ok(self) -> bool:
        return self.rate is not None and self.rate > 0

    def to_dict(self) -> dict:
        d = asdict(self)
        d["ok"] = self.ok
        return d


def major(ccy: str) -> tuple[str, float]:
    """('GBP', 0.01) for 'GBp'; (CCY, 1.0) otherwise."""
    if ccy in SUBUNITS:
        return SUBUNITS[ccy]
    return (ccy or "").upper(), 1.0


#: A broker's separator-less crypto pair ("BTCUSD", how Alpaca reports a
#: position) -> its quote currency. Stablecoin quotes (USDT, USDC) are NOT
#: dollars and stay unknown.
_CRYPTO_QUOTES = ("USD", "EUR", "GBP")


def native_currency(symbol: str, asset_class: str = "") -> Optional[str]:
    """The quote currency of a provider symbol, or None when unknown. A
    crypto pair without a separator is read with its asset class, as the
    session engine reads it — the class settles the format, never more."""
    try:
        from src.data.currency import provider_currency
        ccy = provider_currency(symbol)
    except Exception:
        ccy = None
    if ccy is None and (asset_class or "").lower() == "crypto":
        s = (symbol or "").strip().upper().replace("-", "").replace("/", "")
        for q in _CRYPTO_QUOTES:
            if s.endswith(q) and len(s) > len(q) + 1 and not s.endswith(("USDT", "USDC")):
                return q
    return ccy


def _close(data, pair: str, at: datetime, max_age_days: int):
    h = data.history(pair, at)
    if h is None or "Close" not in h or not len(h):
        return None, None, f"no {pair} history known at {at.isoformat()}"
    c = h["Close"].dropna()
    c = c[c > 0]
    if not len(c):
        return None, None, f"{pair} has no positive close"
    last = c.index[-1]
    age = (pd_date(at) - last.date()).days
    if age > max_age_days:
        return None, last.date().isoformat(), (f"{pair} last close {last.date()} is {age} days "
                                               f"old at {at.date()} — stale")
    return float(c.iloc[-1]), last.date().isoformat(), ""


def pd_date(at: datetime):
    from datetime import timezone
    return at.astimezone(timezone.utc).date()


def _pair(data, a: str, b: str, at: datetime, max_age_days: int):
    """a->b from the direct or inverse pair: (rate, as_of, pair_used, why)."""
    r, d, why = _close(data, f"{a}{b}=X", at, max_age_days)
    if r is not None:
        return r, d, f"{a}{b}=X", ""
    r2, d2, why2 = _close(data, f"{b}{a}=X", at, max_age_days)
    if r2 is not None:
        return 1.0 / r2, d2, f"{b}{a}=X (inverted)", ""
    return None, d or d2, None, f"{why}; {why2}"


def rate(from_ccy: str, to_ccy: str, *, at: datetime, data,
         max_age_days: int = MAX_AGE_DAYS) -> FxRate:
    """`from_ccy` -> `to_ccy` as known at `at`, with provenance."""
    if at.tzinfo is None:
        raise ValueError("FX needs an aware instant")
    if not from_ccy or not to_ccy:
        return FxRate(from_ccy, to_ccy, None, why="currency unknown — not converted")
    a, fa = major(from_ccy)
    b, fb = major(to_ccy)
    scale = fa / fb
    if a == b:
        return FxRate(from_ccy, to_ccy, scale, source="identity",
                      why="same currency" + (" (quoting unit)" if scale != 1 else ""))
    src = "src/v5/marketdata daily close, point in time"
    r, d, used, why = _pair(data, a, b, at, max_age_days)
    if r is not None:
        return FxRate(from_ccy, to_ccy, r * scale, as_of=d, source=src, pairs=(used,))
    if "USD" not in (a, b):
        r1, d1, u1, w1 = _pair(data, a, "USD", at, max_age_days)
        r2, d2, u2, w2 = _pair(data, "USD", b, at, max_age_days)
        if r1 is not None and r2 is not None:
            return FxRate(from_ccy, to_ccy, r1 * r2 * scale, as_of=min(d1, d2),
                          source=src + " via USD", pairs=(u1, u2))
        why = f"{why}; via USD: {w1 or 'ok'} / {w2 or 'ok'}"
    return FxRate(from_ccy, to_ccy, None, as_of=d, source=src, why=why.strip("; "))


def series(from_ccy: str, to_ccy: str, *, at: datetime, data):
    """Daily from->to rates known by `at` (a pd.Series), for converting a
    price HISTORY into the base currency date by date. None when unknown.
    Each date uses that date's own close — never today's rate backwards."""
    import pandas as pd
    a, fa = major(from_ccy)
    b, fb = major(to_ccy)
    scale = fa / fb
    if a == b:
        return scale
    for pair, invert in ((f"{a}{b}=X", False), (f"{b}{a}=X", True)):
        h = data.history(pair, at)
        if h is not None and "Close" in h and len(h):
            c = h["Close"].where(h["Close"] > 0).dropna()
            if len(c):
                return (1.0 / c if invert else c) * scale
    if "USD" not in (a, b):
        s1 = series(a, "USD", at=at, data=data)
        s2 = series("USD", b, at=at, data=data)
        if isinstance(s1, pd.Series) and isinstance(s2, pd.Series):
            j = pd.concat([s1, s2], axis=1, join="inner").dropna()
            if len(j):
                return j.iloc[:, 0] * j.iloc[:, 1] * scale
    return None
