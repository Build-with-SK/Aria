"""
src/intelligence/research/components.py
=======================================
One piece of a research snapshot, and everything needed to trust it or not.

    value        the measurement, or None
    as_of        the latest information time actually used (ISO, aware)
    source       where it came from (vendor, file, computation)
    version      the code / data version that produced it
    freshness    FRESH | STALE | MISSING | CONFLICTED | NO_DATA | UNKNOWN
    pit          PIT_BY_CONSTRUCTION | PIT_VERIFIED | LIVE_ONLY | UNAVAILABLE
    provenance   free-form: inputs, windows, counts, provider status
    missing      why the value is None, when it is

`NO_DATA` is not `MISSING`: a working news provider that found no story is a
fact about the world ("nothing was published"), while a failed provider is
absence of knowledge. The difference matters to a reader and to the scorecard.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional

FRESH, STALE, MISSING, CONFLICTED, NO_DATA, UNKNOWN = (
    "FRESH", "STALE", "MISSING", "CONFLICTED", "NO_DATA", "UNKNOWN")
FRESHNESS = (FRESH, STALE, MISSING, CONFLICTED, NO_DATA, UNKNOWN)

PIT_BY_CONSTRUCTION = "PIT_BY_CONSTRUCTION"
PIT_VERIFIED = "PIT_VERIFIED"
LIVE_ONLY = "LIVE_ONLY"
UNAVAILABLE = "UNAVAILABLE"

#: Worst first: the scorecard reports the worst freshness of what it read.
_RANK = {MISSING: 5, CONFLICTED: 4, STALE: 3, UNKNOWN: 2, NO_DATA: 1, FRESH: 0}


@dataclass
class Component:
    name: str
    value: Any = None
    as_of: Optional[str] = None
    source: str = ""
    version: str = ""
    freshness: str = UNKNOWN
    pit: str = UNAVAILABLE
    provenance: dict = field(default_factory=dict)
    missing: Optional[str] = None

    @property
    def available(self) -> bool:
        return self.value is not None and self.freshness not in (MISSING, CONFLICTED)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["available"] = self.available
        return d


def missing(name: str, why: str, *, source: str = "", pit: str = UNAVAILABLE,
            provenance: Optional[dict] = None) -> Component:
    return Component(name, None, None, source, "", MISSING, pit, provenance or {}, why)


def worst(freshness: list[str]) -> str:
    if not freshness:
        return UNKNOWN
    return max(freshness, key=lambda f: _RANK.get(f, 2))
