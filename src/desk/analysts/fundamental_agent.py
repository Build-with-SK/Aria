"""
src/desk/analysts/fundamental_agent.py
======================================
Fundamentals analyst — grounds its opinion in the universe dossier
(P/E, ROE, growth, margins, 52w return). Uses the 24h dossier cache;
falls back to a neutral opinion if the dossier is unavailable.
"""
from __future__ import annotations

import logging

from src.desk.opinion import Opinion, ev, load_data_json

logger = logging.getLogger(__name__)

SRC = "universe dossier (Yahoo Finance, cached 24h)"


def opine(ticker: str) -> Opinion:
    # Crypto has no company fundamentals (no earnings, P/E, ROE, debt). Scoring
    # a token on equity metrics — or worse, on its price returns — produces
    # spurious high-conviction views (e.g. bear 100 on DOT). Abstain cleanly so
    # crypto decisions rest on the technical (and sentiment) analysts.
    sig = load_data_json("signals.json").get(ticker) or {}
    if ticker.endswith("-USD") or "crypto" in str(sig.get("asset_class", "")).lower():
        return Opinion(agent="fundamental", ticker=ticker, view="neutral",
                       conviction=0, thesis=f"{ticker} is a cryptocurrency — no "
                       f"company fundamentals to analyse. Abstaining.", evidence=[])
    try:
        from src.data.universe import get_universe
        dossier = get_universe().dossier(ticker, prefetch_peers=False)
    except Exception as e:
        logger.warning(f"fundamental_agent: dossier unavailable for {ticker}: {e}")
        dossier = {"error": str(e)}

    if "error" in dossier:
        return Opinion(agent="fundamental", ticker=ticker, view="neutral", conviction=0,
                       thesis=f"No fundamentals dossier available for {ticker} "
                              f"({dossier['error']}). Abstaining.",
                       evidence=[])

    f = dossier.get("fundamentals") or {}
    returns = dossier.get("returns") or {}
    evidence, bull_pts, bear_pts = [], 0, 0

    def cite(claim, value, lean):
        nonlocal bull_pts, bear_pts
        evidence.append(ev(claim, value, SRC, lean))
        if lean == "bull":
            bull_pts += 1
        elif lean == "bear":
            bear_pts += 1

    pe = f.get("trailingPE")
    if isinstance(pe, (int, float)) and pe > 0:
        cite(f"Trailing P/E {pe:.1f}", round(pe, 1),
             "bull" if pe < 18 else "bear" if pe > 40 else "neutral")

    roe = f.get("returnOnEquity")
    if isinstance(roe, (int, float)):
        cite(f"Return on equity {roe:.0%}", round(roe, 3),
             "bull" if roe > 0.15 else "bear" if roe < 0.05 else "neutral")

    rg = f.get("revenueGrowth")
    if isinstance(rg, (int, float)):
        cite(f"Revenue growth {rg:+.0%} YoY", round(rg, 3),
             "bull" if rg > 0.08 else "bear" if rg < 0 else "neutral")

    eg = f.get("earningsGrowth")
    if isinstance(eg, (int, float)):
        cite(f"Earnings growth {eg:+.0%} YoY", round(eg, 3),
             "bull" if eg > 0.10 else "bear" if eg < 0 else "neutral")

    pm = f.get("profitMargins")
    if isinstance(pm, (int, float)):
        cite(f"Profit margin {pm:.0%}", round(pm, 3),
             "bull" if pm > 0.15 else "bear" if pm < 0.03 else "neutral")

    de = f.get("debtToEquity")
    if isinstance(de, (int, float)):
        cite(f"Debt/equity {de:.0f}%", round(de, 1),
             "bear" if de > 150 else "bull" if de < 50 else "neutral")

    r1y = returns.get("1Y")
    if isinstance(r1y, (int, float)):
        cite(f"52-week return {r1y:+.1f}%", r1y,
             "bull" if r1y > 10 else "bear" if r1y < -10 else "neutral")

    r5y = returns.get("5Y")
    if isinstance(r5y, (int, float)):
        cite(f"5-year return {r5y:+.1f}%", r5y,
             "bull" if r5y > 50 else "bear" if r5y < 0 else "neutral")

    # Optional LSE databank: recent open-market insider buys are one of the
    # few unambiguous fundamental tells. Key absent → skipped silently.
    try:
        from src.data import lse_data
        if lse_data.available():
            buys = lse_data.insider_buys(ticker, limit=10)
            if buys:
                latest = buys[0].get("transaction_date", "?")
                cite(f"{len(buys)} insider open-market purchases on record "
                     f"(latest {latest}, LSE /ref/insider_trades)",
                     len(buys), "bull")
    except Exception:
        pass

    if not evidence:
        return Opinion(agent="fundamental", ticker=ticker, view="neutral", conviction=0,
                       thesis=f"Dossier for {ticker} has no usable fundamental fields "
                              f"(likely an index, future, or crypto). Abstaining.",
                       evidence=[])

    net = bull_pts - bear_pts
    view = "bull" if net >= 2 else "bear" if net <= -2 else "neutral"
    conviction = int(min(100, abs(net) / max(len(evidence), 1) * 100 + 20)) if net else 20

    sector = f.get("sector") or dossier.get("asset_class") or "unknown sector"
    thesis = (
        f"{dossier.get('name') or ticker} ({sector}): {bull_pts} bullish vs {bear_pts} bearish "
        f"fundamental factors across valuation, profitability, growth and leverage. "
        f"1Y return {returns.get('1Y', '?')}%. Net fundamental read: {view.upper()}."
    )
    return Opinion(agent="fundamental", ticker=ticker, view=view,
                   conviction=conviction, thesis=thesis, evidence=evidence)
