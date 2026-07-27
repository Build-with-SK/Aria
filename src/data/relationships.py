"""
src/data/relationships.py
=========================
Curated competitor/supplier graph — the "who's related to whom" that price
data alone can't give. Seeded in data/relationships.json (yahoo-ready symbols),
merged on top of the progressive sector-peer discovery in universe.py.

Lookups are symbol-keyed and case-insensitive, and match either the display
symbol (SAIL) or the yahoo symbol (SAIL.NS), so a name resolved on demand
still finds its curated relations.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent.parent.parent
REL_FILE = ROOT / "data" / "relationships.json"

_cache: dict | None = None


def _load() -> dict:
    global _cache
    if _cache is not None:
        return _cache
    data = {}
    try:
        if REL_FILE.exists():
            raw = json.loads(REL_FILE.read_text(encoding="utf-8"))
            data = {k.upper(): v for k, v in raw.items() if not k.startswith("_")}
    except Exception as e:
        logger.warning(f"relationships.json unreadable: {e}")
    _cache = data
    return data


def _keys_for(symbol: str, yahoo: str | None) -> list[str]:
    out = []
    for s in (symbol, yahoo):
        if s:
            out.append(s.upper())
            # also try the bare form (SAIL.NS -> SAIL) and vice-versa
            base = s.upper().split(".")[0]
            if base != s.upper():
                out.append(base)
    return out


def related_symbols(symbol: str, yahoo: str | None = None) -> dict:
    """{'competitors': [...], 'suppliers': [...]} of yahoo-ready symbols for a
    ticker, or empty lists if the name isn't in the seed."""
    data = _load()
    for key in _keys_for(symbol, yahoo):
        if key in data:
            entry = data[key]
            return {"competitors": list(entry.get("competitors") or []),
                    "suppliers": list(entry.get("suppliers") or [])}
    return {"competitors": [], "suppliers": []}


def has_relationships(symbol: str, yahoo: str | None = None) -> bool:
    r = related_symbols(symbol, yahoo)
    return bool(r["competitors"] or r["suppliers"])
