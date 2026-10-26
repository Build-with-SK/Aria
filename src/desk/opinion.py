"""
src/desk/opinion.py
===================
The common currency of the desk: a typed, scored, evidence-cited Opinion.
Every analyst agent returns one. Every claim must carry provenance —
a real number from a real ARIA data file, never LLM recall.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent.parent.parent
DATA = ROOT / "data"


@dataclass
class Opinion:
    agent: str           # "technical" | "fundamental" | "sentiment" | "macro"
    ticker: str
    view: str            # "bull" | "bear" | "neutral"
    conviction: int      # 0-100
    thesis: str          # one paragraph
    evidence: list = field(default_factory=list)   # [{claim, value, source}]

    def to_dict(self) -> dict:
        return asdict(self)

    def bullish_evidence(self) -> list:
        return [e for e in self.evidence if e.get("lean") == "bull"]

    def bearish_evidence(self) -> list:
        return [e for e in self.evidence if e.get("lean") == "bear"]


def ev(claim: str, value, source: str, lean: str = "neutral") -> dict:
    """One evidence item. `lean` marks which side of the debate it feeds."""
    return {"claim": claim, "value": value, "source": source, "lean": lean}


def signals_freshness() -> dict:
    """Is data/signals.json current enough to act on without a human?

    signals.json is written only by the full pipeline, a manual run. On
    2026-10-06 it was nine days old and the reflex lane had armed 44
    playbooks from it that morning — composites, trigger levels and stops
    computed on 27 September, waiting to fire against live prices. Its rows
    now carry evidence timestamps. Every row must fit the world model's existing
    budget (src/core/world.py, "signals": 30 h). Undated rows are stale; a
    checkout or rewrite cannot make old observations fresh.
    """
    try:
        from src.core.world import dataset_freshness
        f = dataset_freshness("signals.json")
    except Exception as e:                       # never fail open on a bug
        return {"fresh": False, "age_hours": None,
                "why": f"signal freshness could not be determined: {e}"}
    fresh = f["status"] == "FRESH"
    return {"fresh": fresh, "age_hours": f.get("age_hours"),
            "as_of": f.get("as_of"),
            "why": "" if fresh else (f.get("why") or "signals.json is missing")}


def load_data_json(filename: str) -> dict:
    """Load a JSON file from data/. Returns {} on any failure."""
    path = DATA / filename
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"desk: could not parse {filename}: {e}")
        return {}
