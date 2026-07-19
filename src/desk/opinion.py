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
