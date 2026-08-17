"""
src/portfolio/holdings.py
=========================
WHAT HE ACTUALLY OWNS.

The Portfolio page has been showing a portfolio nobody bought. `/api/portfolio`
serves `data/portfolio_analysis.json`, which `portfolio_analyzer.py` computes
over a FIXED ANALYSIS UNIVERSE — AAPL, MSFT, TSLA, ^GSPC, ^N225, EURUSD=X,
GC=F and twenty others — and the copy on disk was written on 31 May. Meanwhile
the account holds BNO, bought by the desk on 11 August.

So the page answered a question nobody asked ("how would a basket of the
world's largest assets look?") while the one real position lived on a
different screen. The owner's report was exact: he did not recognise any of
the stocks, because none of them were his.

This module answers the other question. Positions come from the BROKER, which
is the only authority on what is owned — the desk's own file records intent
and can drift; the broker records fact. Everything else here decorates that
list and is allowed to be missing: a holding with no dossier is still a
holding, and showing it with blanks beats hiding it behind a failed lookup.

WHAT IS DELIBERATELY NOT HERE
-----------------------------
No optimiser, no suggested reallocation, no "you should hold 12% gold". This
reports. The recommendation of what to do about it belongs to the modules that
have measured track records — and today none of them do.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


def _broker_positions() -> list[dict]:
    """What the broker says is held. The authority."""
    from src.desk.auto_executor import account_snapshot
    account = account_snapshot() or {}
    return list(account.get("positions") or []), account


def _desk_context() -> dict:
    """The desk's own record: why a position was opened, and where its stop is.

    Keyed by ticker. Absent for anything bought outside the desk — a manual
    fill has no debate behind it, and saying so is better than inventing one.
    """
    try:
        from src.desk.position_manager import PositionManager
        return dict(PositionManager().load_positions() or {})
    except Exception as e:
        logger.warning("desk position context unavailable: %s", e)
        return {}


def _dossier(ticker: str) -> dict:
    try:
        from src.data.universe import get_universe
        d = get_universe().dossier(ticker, prefetch_peers=False)
        return d if isinstance(d, dict) and "error" not in d else {}
    except Exception as e:
        logger.debug("dossier for %s unavailable: %s", ticker, e)
        return {}


def _live_price(ticker: str) -> tuple[float | None, str]:
    """Last price and where it came from. Cited, because a stale price shown
    as a live one is the failure this project keeps correcting."""
    try:
        from src.v5 import marketdata as md
        df = md.history(ticker, period="5d")
        if df is None or df.empty:
            return None, "no price data"
        return float(df["Close"].iloc[-1]), md.source_label(ticker)
    except Exception as e:
        return None, f"price unavailable ({type(e).__name__})"


def _days_held(entry_at: str) -> int | None:
    try:
        return max(0, (datetime.now() - datetime.fromisoformat(entry_at)).days)
    except Exception:
        return None


def enrich(position: dict, desk: dict) -> dict:
    """One holding, with everything known about it.

    Every enrichment is optional and failure-tolerant on purpose: the point is
    to show him what he owns, and a dossier lookup that times out must not
    remove a real position from his screen.
    """
    ticker = str(position.get("ticker") or position.get("symbol") or "").upper()
    qty = float(position.get("qty") or 0)
    avg_cost = float(position.get("avg_cost") or 0)
    market_value = float(position.get("market_value") or 0)
    unrealised = float(position.get("unrealized_pl") or 0)

    doss = _dossier(ticker)
    profile = doss.get("profile") or {}
    price, price_source = _live_price(ticker)

    cost_basis = qty * avg_cost
    pl_pct = (unrealised / cost_basis * 100) if cost_basis else None

    row: dict[str, Any] = {
        "ticker": ticker,
        "name": profile.get("name") or doss.get("name") or "",
        "asset_class": position.get("asset_class") or profile.get("asset_class") or "equity",
        "exchange": profile.get("exchange") or "",
        "currency": profile.get("currency") or "USD",
        "sector": profile.get("sector") or "",
        "industry": profile.get("industry") or "",
        "country": profile.get("country") or "",
        # position
        "side": position.get("side") or ("long" if qty >= 0 else "short"),
        "qty": qty,
        "avg_cost": round(avg_cost, 4),
        "cost_basis": round(cost_basis, 2),
        "last_price": round(price, 4) if price is not None else None,
        "price_source": price_source,
        "market_value": round(market_value, 2),
        "unrealised_pl": round(unrealised, 2),
        "unrealised_pl_pct": round(pl_pct, 2) if pl_pct is not None else None,
        "broker": position.get("broker") or "",
    }

    held = desk.get(ticker) or {}
    if held:
        row["opened_by"] = "the desk"
        row["thesis"] = held.get("thesis") or ""
        row["debate_id"] = held.get("debate_id") or ""
        row["stop"] = held.get("stop")
        row["target"] = held.get("target")
        row["entry_at"] = held.get("entry_at")
        row["days_held"] = _days_held(held.get("entry_at") or "")
        if price and held.get("stop"):
            try:
                row["distance_to_stop_pct"] = round(
                    (price - float(held["stop"])) / price * 100, 2)
            except (TypeError, ZeroDivisionError):
                pass
    else:
        # A position the desk did not open — a manual fill, or one whose record
        # was lost. Saying so is better than implying a thesis that never
        # existed.
        row["opened_by"] = "not the desk — no debate is on file for this one"
        row["thesis"] = ""
        row["days_held"] = None
    return row


def report() -> dict:
    """Everything the portfolio screen needs, from the broker outward."""
    try:
        positions, account = _broker_positions()
    except Exception as e:
        return {"ok": False, "error": f"the broker could not be reached: {e}",
                "holdings": [], "at": datetime.now().isoformat(timespec="seconds")}

    desk = _desk_context()
    holdings = []
    for p in positions:
        try:
            holdings.append(enrich(p, desk))
        except Exception as e:              # one bad row must not empty the page
            logger.warning("could not enrich %s: %s", p.get("ticker"), e)
            holdings.append({"ticker": p.get("ticker"), "qty": p.get("qty"),
                             "market_value": p.get("market_value"),
                             "error": str(e)[:120]})

    invested = sum(h.get("market_value") or 0 for h in holdings)
    equity = float(account.get("equity") or 0)
    for h in holdings:
        mv = h.get("market_value") or 0
        h["weight_pct"] = round(mv / equity * 100, 2) if equity else None

    by_class: dict[str, float] = {}
    for h in holdings:
        by_class[h.get("asset_class") or "equity"] = round(
            by_class.get(h.get("asset_class") or "equity", 0)
            + (h.get("market_value") or 0), 2)

    return {
        "ok": True,
        "at": datetime.now().isoformat(timespec="seconds"),
        "account": {
            "equity": round(equity, 2),
            "cash": round(float(account.get("cash") or 0), 2),
            "buying_power": round(float(account.get("buying_power") or 0), 2),
            "paper": bool(account.get("paper", True)),
            "connected": bool(account.get("connected", False)),
        },
        "holdings": sorted(holdings, key=lambda h: -(h.get("market_value") or 0)),
        "totals": {
            "positions": len(holdings),
            "invested": round(invested, 2),
            "unrealised_pl": round(sum(h.get("unrealised_pl") or 0
                                       for h in holdings), 2),
            "cash_pct": round((equity - invested) / equity * 100, 2) if equity else None,
            "by_asset_class": by_class,
        },
        "note": ("These are the positions the broker reports, not a model "
                 "portfolio. Everything here is paper."
                 if holdings else
                 "No open positions. The broker is reachable and reports "
                 "nothing held — this is an empty account, not a failed "
                 "lookup."),
    }
