"""
src/portfolio/related.py
========================
RECOMMENDATIONS THAT RELATE TO WHAT HE ACTUALLY OWNS.

The recommendation screen has been a list about the analysis universe. Useful,
but disconnected: it never knew he was long an oil fund, so it never said
anything about oil.

This walks outward from the holdings — competitors, suppliers, sector peers —
and scores what it finds with the SAME v5 engine the rest of the system uses.
No second opinion, no separate model, no bespoke scoring that could flatter
one screen over another.

TWO HONESTIES BUILT IN
----------------------
1. A related name is scored, and its relationship is stated. "Peer of BNO"
   and "supplier to BNO" are different claims and the reader gets to see
   which one this is.

2. Concentration is reported, not buried. Recommending three more oil funds
   to a man already long oil is a correlation trap, and the fact that they are
   all individually attractive is precisely how it happens. Anything already
   heavy in the book is flagged as ADDS TO EXISTING EXPOSURE.

WHAT IT DOES NOT DO
-------------------
It does not size, order or rank by expected return. No module here has a
measured positive edge — 0 of 46 predictions have resolved — so a confident
ordering would be a fabrication dressed as analysis. It surfaces neighbours
and what the engine currently says about them.
"""
from __future__ import annotations

import logging
from datetime import datetime

logger = logging.getLogger(__name__)

#: How many related names to surface per holding. Small on purpose — twenty
#: suggestions is a screen nobody reads and a decision nobody makes.
PER_HOLDING = 4

#: A related name whose asset class already exceeds this share of the book is
#: flagged rather than quietly recommended.
CONCENTRATION_PCT = 25.0


#: Words that carry no information about WHAT an instrument tracks. Matching
#: on these relates a Brent oil ETF to every fund on earth.
_NOISE = {"the", "fund", "trust", "etf", "etn", "shares", "ishares", "spdr",
          "invesco", "united", "states", "us", "usa", "lp", "plc", "inc",
          "corp", "corporation", "limited", "ltd", "company", "co", "index",
          "and", "of", "series", "class"}


def _tokens(name: str) -> set[str]:
    import re
    return {w for w in re.findall(r"[a-z]+", (name or "").lower())
            if len(w) > 2 and w not in _NOISE}


def _peers_without_a_sector(ticker: str, info: dict, n: int) -> list[dict]:
    """Neighbours for an instrument the sector machinery cannot place.

    `peers()` keys off sector, and a commodity ETF has none — BNO came back
    with nothing at all. The naive repair is to match on the name, and it is
    a trap: "Oil" relates a Brent tracker to Gandhar Oil Refinery and half the
    Indian refining sector, which are not its neighbours in any sense that
    matters to a holder.

    So: stay inside the same ASSET CLASS, and rank by how much of the
    distinctive part of the name is shared. Within commodities, "brent" and
    "oil" mean what they say.
    """
    from src.data.universe import get_universe

    want = _tokens(info.get("name") or "")
    asset_class = info.get("asset_class")
    if not asset_class:
        return []

    rows = []
    with get_universe()._conn() as conn:
        for r in conn.execute(
                """SELECT symbol, yahoo, name, exchange FROM symbols
                   WHERE asset_class = ? AND symbol != ?
                   LIMIT 4000""", (asset_class, info["symbol"])):
            shared = want & _tokens(r["name"])
            if shared:
                rows.append((len(shared), dict(r), sorted(shared)))

    rows.sort(key=lambda x: -x[0])
    out = []
    for score, r, shared in rows[:n]:
        out.append({
            "ticker": (r["yahoo"] or r["symbol"]).upper(),
            "name": r["name"],
            "relationship": (f"tracks the same thing as {ticker} "
                             f"({', '.join(shared)})"),
        })
    return out


def _peers(ticker: str, n: int) -> list[dict]:
    """Neighbours of one holding, each carrying WHY it is a neighbour."""
    out: list[dict] = []
    try:
        from src.data.universe import get_universe
        u = get_universe()

        doss = u.dossier(ticker, prefetch_peers=False)
        related = (doss or {}).get("related") or {}
        for kind in ("competitors", "suppliers"):
            for item in (related.get(kind) or []):
                sym = (item.get("symbol") or item.get("yahoo") or "").upper()
                if sym and sym != ticker:
                    out.append({"ticker": sym,
                                "name": item.get("name") or "",
                                "relationship": f"{kind[:-1]} of {ticker}"})

        for item in (u.peers(ticker, n=n) or []):
            sym = (item.get("yahoo") or item.get("symbol") or "").upper()
            if sym and sym != ticker:
                out.append({"ticker": sym,
                            "name": item.get("name") or "",
                            "relationship": f"sector peer of {ticker}",
                            "sector": item.get("sector") or ""})

        # ETFs, commodities and crypto have no sector, so everything above
        # returns nothing for them — which is most of what this account holds.
        if not out:
            info = u._lookup(ticker)
            if info:
                out.extend(_peers_without_a_sector(ticker, dict(info), n))
    except Exception as e:
        logger.warning("peer lookup for %s failed: %s", ticker, e)

    seen, unique = set(), []
    for r in out:
        if r["ticker"] not in seen:
            seen.add(r["ticker"])
            unique.append(r)
    return unique[:n]


def _score(ticker: str) -> dict:
    """What the v5 engine says about one name, or why it could not say."""
    try:
        from src.v5 import pipeline
        r = pipeline.analyze(ticker, log=False)      # log=False: not a call
        if r.get("error"):
            return {"scored": False, "why": str(r["error"])[:120]}
        ens = r.get("ensemble") or {}
        rec = r.get("recommendation") or {}
        counts = r.get("module_count") or {}
        data = r.get("data") or {}
        return {
            "scored": True,
            "direction": ens.get("direction"),
            "p_bull": ens.get("p_bull"),
            "confidence": rec.get("confidence"),
            "headline": (rec.get("headline") or "")[:200],
            "modules_reporting": counts.get("reporting"),
            "modules_abstained": counts.get("abstained"),
            "stale": data.get("stale"),
            "citation": data.get("citation"),
        }
    except Exception as e:
        return {"scored": False, "why": f"{type(e).__name__}: {e}"[:120]}


def report(limit_per_holding: int = PER_HOLDING, score: bool = True) -> dict:
    """Related names for every holding, scored, with concentration flagged."""
    from src.portfolio.holdings import report as holdings_report

    book = holdings_report()
    holdings = book.get("holdings") or []
    equity = (book.get("account") or {}).get("equity") or 0
    by_class = (book.get("totals") or {}).get("by_asset_class") or {}
    held = {h.get("ticker") for h in holdings}

    exposure_pct = {
        cls: (value / equity * 100 if equity else 0)
        for cls, value in by_class.items()
    }

    groups = []
    for h in holdings:
        ticker = h.get("ticker")
        if not ticker:
            continue
        candidates = []
        for peer in _peers(ticker, limit_per_holding):
            if peer["ticker"] in held:
                peer["already_held"] = True
            row = {**peer}
            if score:
                row.update(_score(peer["ticker"]))
            cls = h.get("asset_class") or "equity"
            if exposure_pct.get(cls, 0) >= CONCENTRATION_PCT:
                row["concentration_warning"] = (
                    f"adds to existing exposure — {cls} is already "
                    f"{exposure_pct[cls]:.0f}% of the book")
            candidates.append(row)
        groups.append({
            "holding": ticker,
            "holding_name": h.get("name") or "",
            "asset_class": h.get("asset_class"),
            "weight_pct": h.get("weight_pct"),
            "related": candidates,
        })

    return {
        "at": datetime.now().isoformat(timespec="seconds"),
        "groups": groups,
        "note": ("Neighbours of what he holds, scored by the same engine as "
                 "everything else. Ordering is by relationship, not by "
                 "expected return: no module here has a measured positive "
                 "edge, so ranking them by attractiveness would be a "
                 "fabrication."
                 if groups else
                 "Nothing is held, so there is nothing to relate to."),
    }
