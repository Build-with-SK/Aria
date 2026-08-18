"""
src/options/chain.py
====================
THE OPTIONS CHAIN, WITH THE DEAD CONTRACTS MARKED AS DEAD.

A naive chain view prints `lastPrice` in a column called "price" and lets the
reader assume it is one. The first AAPL contract the vendor returned while this
was being written says everything:

    strike 250, lastPrice 55.20, bid 0.00, ask 0.00,
    last traded four days ago, open interest 2

Fifty-five dollars is not a price. It is the memory of a trade somebody made
last Thursday, and there is nobody on either side of it now. A reader who sizes
a position off that number is pricing against a ghost. So every contract here
carries a liquidity verdict computed from the spread, the open interest and the
age of the last trade, and the ones that cannot be transacted say so before
they say anything else.

WHOSE NUMBERS THESE ARE
-----------------------
- bid, ask, volume, open interest, implied volatility: the VENDOR's, passed
  through and attributed. IV especially — it is a model output, not a
  measurement, and two vendors will disagree about it.
- greeks: computed HERE, by Black-Scholes, and therefore approximate for
  American equity options. The assumptions ride on every response rather than
  sitting in a docstring: European exercise, no dividends, and a risk-free rate
  taken from the macro snapshot when one exists. Approximate greeks are worth
  having. Approximate greeks presented as exact are not.

WHY NOTHING HERE TRADES
-----------------------
No margin model, no assignment handling, no early-exercise logic, and no risk
gate that understands what short gamma does to an account overnight. `TRADEABLE`
is False and the refusal travels with the data, so a future caller who wires
this to an executor has to delete a line that explains why it is there.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

#: This package does not place orders, and the flag rides along with every
#: response so a UI cannot quietly grow a buy button.
TRADEABLE = False
REFUSAL = ("Research only. There is no margin model, no assignment handling "
           "and no risk gate that understands short gamma, so ARIA does not "
           "trade options. Read the chain, decide yourself, place it with a "
           "broker.")

#: A spread wider than this fraction of the mid is a quote, not a market.
WIDE_SPREAD = 0.25

#: Below this open interest there is unlikely to be anyone to trade with.
THIN_OI = 25

#: A last trade older than this says nothing about where the thing is now.
STALE_TRADE_HOURS = 24

#: Used when the macro snapshot carries no yield. Stated, never hidden.
DEFAULT_RISK_FREE = 0.04

#: Below this, the vendor's implied vol is not a volatility — it is the vendor
#: saying it does not know. Feeding 0.00001 into Black-Scholes returns delta
#: 1.0 on a deep in-the-money call, which is arithmetically correct and
#: practically a lie: it reads as certainty produced by a missing input.
MIN_USABLE_IV = 0.005


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def risk_free_rate() -> tuple[float, str]:
    """The rate, and where it came from."""
    try:
        import json
        from pathlib import Path
        p = Path(__file__).resolve().parent.parent.parent / "data" / "macro_data.json"
        if p.exists():
            y = json.loads(p.read_text(encoding="utf-8")).get("treasury_10y")
            if isinstance(y, (int, float)) and 0 < float(y) < 25:
                return float(y) / 100.0, "10y treasury from the macro snapshot"
    except Exception as e:
        logger.debug("risk-free rate unavailable: %s", e)
    return DEFAULT_RISK_FREE, f"assumed {DEFAULT_RISK_FREE:.0%} - no macro yield on file"


def greeks(spot: float, strike: float, years: float, vol: float,
           rate: float, is_call: bool) -> dict:
    """Black-Scholes greeks. Approximate for American options.

    Returns EMPTY rather than nonsense when the inputs cannot support the
    model. An expired contract or a zero volatility has no meaningful delta,
    and returning 0.0 would be a number somebody uses.
    """
    if spot <= 0 or strike <= 0 or years <= 0 or vol <= 0:
        return {}
    try:
        d1 = ((math.log(spot / strike) + (rate + 0.5 * vol * vol) * years)
              / (vol * math.sqrt(years)))
        d2 = d1 - vol * math.sqrt(years)
        discount = math.exp(-rate * years)
        delta = _norm_cdf(d1) if is_call else _norm_cdf(d1) - 1.0
        gamma = _norm_pdf(d1) / (spot * vol * math.sqrt(years))
        vega = spot * _norm_pdf(d1) * math.sqrt(years) / 100.0
        if is_call:
            theta_year = (-(spot * _norm_pdf(d1) * vol) / (2 * math.sqrt(years))
                          - rate * strike * discount * _norm_cdf(d2))
        else:
            theta_year = (-(spot * _norm_pdf(d1) * vol) / (2 * math.sqrt(years))
                          + rate * strike * discount * _norm_cdf(-d2))
        return {
            "delta": round(delta, 4),
            "gamma": round(gamma, 6),
            "vega": round(vega, 4),                  # per 1 vol point
            "theta": round(theta_year / 365.0, 4),   # per day
        }
    except (ValueError, ZeroDivisionError):
        return {}


def market_open() -> bool | None:
    """True, False, or None when it cannot be determined."""
    try:
        from src.desk.desk_daemon import us_equities_open
        return bool(us_equities_open())
    except Exception:
        return None


def liquidity(bid: float, ask: float, oi: int | None,
              last_trade_age_h: float | None,
              is_open: bool | None = None) -> dict:
    """Can this actually be traded, and if not, why not.

    Three independent ways for a contract to be untradeable, and they are
    reported separately because they mean different things: nobody quoting it,
    a spread that is a dare rather than a market, and a price whose last
    evidence is a day stale.
    """
    reasons: list[str] = []
    two_sided = bid > 0 and ask > 0
    spread_pct = None

    if not two_sided:
        # A CLOSED MARKET IS NOT A DEAD CONTRACT. Outside hours the vendor
        # returns zero on both sides for everything, and calling a strike with
        # 4,859 open interest "dead" is the view lying in the other direction.
        if is_open is False:
            return {"verdict": "market closed", "spread_pct": None,
                    "why": ["no live quotes while the market is shut - open "
                            "interest is the only evidence of liquidity here"],
                    "open_interest_hint": oi}
        reasons.append("no two-sided market - nobody is quoting it")
    else:
        mid = (bid + ask) / 2
        spread_pct = round((ask - bid) / mid, 4) if mid else None
        if spread_pct is not None and spread_pct > WIDE_SPREAD:
            reasons.append(f"spread is {spread_pct:.0%} of mid")

    if oi is not None and oi < THIN_OI:
        reasons.append(f"open interest {oi}")
    if last_trade_age_h is not None and last_trade_age_h > STALE_TRADE_HOURS:
        reasons.append(f"last traded {last_trade_age_h:.0f}h ago")

    verdict = ("tradeable" if not reasons
               else "dead" if not two_sided else "thin")
    return {"verdict": verdict, "spread_pct": spread_pct, "why": reasons}


def _age_hours(ts) -> float | None:
    try:
        import pandas as pd
        t = pd.Timestamp(ts)
        if t.tzinfo is None:
            t = t.tz_localize(timezone.utc)
        delta = datetime.now(timezone.utc) - t.to_pydatetime()
        return round(delta.total_seconds() / 3600, 1)
    except Exception:
        return None


def expiries(symbol: str) -> list[str]:
    try:
        import yfinance as yf
        return list(yf.Ticker(symbol).options or [])
    except Exception as e:
        logger.warning("no expiries for %s: %s", symbol, e)
        return []


def _spot(symbol: str) -> tuple[float | None, str]:
    try:
        from src.v5 import marketdata as md
        df = md.history(symbol, period="5d")
        if df is not None and not df.empty:
            return float(df["Close"].iloc[-1]), md.source_label(symbol)
    except Exception as e:
        logger.debug("spot for %s unavailable: %s", symbol, e)
    return None, "no spot price"


def _num(v, default=0.0) -> float:
    """A vendor number, or the default. NaN is not a number.

    `float(x or 0)` looks like it handles this and does not: NaN is truthy, so
    it passes straight through and poisons every comparison downstream. NaN
    open interest raised on the first real put chain.
    """
    try:
        f = float(v)
        return default if f != f else f
    except (TypeError, ValueError):
        return default


def _int(v, default=None):
    f = _num(v, float("nan"))
    return default if f != f else int(f)


def _row(r, is_call: bool, spot: float | None, years: float,
         rate: float, is_open: bool | None = None) -> dict:
    bid = _num(r.get("bid"))
    ask = _num(r.get("ask"))
    oi = _int(r.get("openInterest"), 0)
    iv = _num(r.get("impliedVolatility"))
    strike = _num(r.get("strike"))
    age = _age_hours(r.get("lastTradeDate"))
    volume = _int(r.get("volume"))

    row = {
        "contract": r.get("contractSymbol"),
        "type": "call" if is_call else "put",
        "strike": strike,
        "bid": bid,
        "ask": ask,
        "mid": round((bid + ask) / 2, 4) if bid > 0 and ask > 0 else None,
        "last": _num(r.get("lastPrice")),
        "last_trade_age_hours": age,
        "volume": volume,
        "open_interest": oi,
        "implied_vol": round(iv, 4) if iv else None,
        "in_the_money": bool(r.get("inTheMoney")),
        "liquidity": liquidity(bid, ask, oi, age, is_open),
    }
    if spot and strike:
        row["moneyness"] = round(strike / spot - 1, 4)
    if spot and years > 0 and iv >= MIN_USABLE_IV:
        row["greeks"] = greeks(spot, strike, years, iv, rate, is_call)
    elif spot and years > 0:
        row["greeks"] = {}
        row["greeks_note"] = (f"no greeks: the vendor's implied vol is "
                              f"{iv:.5f}, which is a missing number rather "
                              f"than a low one")
    return row


def chain(symbol: str, expiry: str = "", around: int = 8, fetch=None) -> dict:
    """One expiry's chain, centred on the money.

    `around` strikes either side. The wings are where the dead contracts live,
    and printing two hundred rows of them buries the ten that matter.
    """
    symbol = (symbol or "").upper().strip()
    out: dict = {
        "symbol": symbol,
        "tradeable": TRADEABLE,
        "refusal": REFUSAL,
        "at": datetime.now().isoformat(timespec="seconds"),
    }
    if not symbol:
        return {**out, "error": "no symbol given"}

    available = expiries(symbol) if fetch is None else fetch("expiries", symbol)
    if not available:
        return {**out, "expiries": [],
                "error": f"no listed options for {symbol}"}
    chosen = expiry if expiry in available else available[0]

    spot, spot_source = _spot(symbol)
    rate, rate_source = risk_free_rate()
    try:
        days = max((datetime.fromisoformat(chosen).date()
                    - datetime.now().date()).days, 0)
    except ValueError:
        days = 0
    years = days / 365.0

    try:
        if fetch is None:
            import yfinance as yf
            raw = yf.Ticker(symbol).option_chain(chosen)
            calls, puts = raw.calls, raw.puts
        else:
            calls, puts = fetch("chain", symbol, chosen)
    except Exception as e:
        return {**out, "expiries": available,
                "error": f"chain unavailable: {type(e).__name__}: {e}"}

    is_open = market_open()
    all_calls = [_row(r, True, spot, years, rate, is_open)
                 for _, r in calls.iterrows()]
    all_puts = [_row(r, False, spot, years, rate, is_open)
                for _, r in puts.iterrows()]

    if spot:
        def near(rows):
            return sorted(rows, key=lambda x: abs(x["strike"] - spot))[:around * 2]
        all_calls, all_puts = near(all_calls), near(all_puts)
    all_calls.sort(key=lambda x: x["strike"])
    all_puts.sort(key=lambda x: x["strike"])

    every = all_calls + all_puts
    counts = {v: sum(1 for c in every if c["liquidity"]["verdict"] == v)
              for v in ("tradeable", "thin", "dead", "market closed")}

    # NO QUOTES AT ALL, WITH THE MARKET OPEN, IS A VENDOR PROBLEM. Free option
    # feeds frequently return zero on both sides for an entire chain. Reporting
    # sixteen liquid strikes as "dead" would blame the market for the data
    # source — and the strikes here carry thousands of contracts of open
    # interest, which is the opposite of dead.
    quoteless = all(c["mid"] is None for c in every) if every else False
    vendor_gap = quoteless and is_open is True

    return {
        **out,
        "expiry": chosen,
        "expiries": available,
        "days_to_expiry": days,
        "spot": round(spot, 4) if spot else None,
        "spot_source": spot_source,
        "calls": all_calls,
        "puts": all_puts,
        "summary": {"contracts": len(every), "market_open": is_open, **counts},
        "assumptions": {
            "greeks": ("Black-Scholes: European exercise, no dividends. "
                       "American equity options can be exercised early, so "
                       "these are approximations - good enough to reason "
                       "with, not good enough to hedge with."),
            "risk_free_rate": rate,
            "risk_free_source": rate_source,
            "implied_vol": "the vendor's number, not measured here",
        },
        "vendor_quotes_missing": vendor_gap,
        "note": ((f"The vendor returned no bid or ask for ANY contract while "
                  f"the market is open. That is a gap in the data source, not "
                  f"an illiquid chain — these strikes carry "
                  f"{max((c['open_interest'] for c in every), default=0):,} "
                  f"contracts of open interest at the top. Read the open "
                  f"interest and the last price, and price nothing off "
                  f"either.") if vendor_gap else
                 (f"The market is shut, so no contract has a live quote. "
                  f"Open interest is the only liquidity evidence available "
                  f"right now.") if is_open is False else
                 (f"{counts['tradeable']} of {len(every)} contracts near the "
                  f"money can actually be transacted. The rest are quoted "
                  f"too wide, traded too long ago, or empty.")),
    }
