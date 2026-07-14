"""
src/brain/cognitive/long_term_memory.py
=======================================
LONG-TERM MEMORY — the brain's permanent memory.
ChromaDB vector store + sentence-transformers embeddings so the brain
can ask "have I seen a situation like this before?" and retrieve
relevant past reasoning cycles semantically.

Each memory = one completed reasoning cycle.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent.parent.parent.parent


@dataclass
class BrainMemory:
    id:                 str
    timestamp:          datetime
    cycle_summary:      str            # what the brain observed and concluded
    tickers_considered: list = field(default_factory=list)
    regime:             str  = ""
    action_taken:       str  = "MONITORING"   # PROPOSED_TRADE | HELD | MONITORING
    trade_proposed:     dict = field(default_factory=dict)
    outcome:            dict = field(default_factory=dict)  # filled by record_outcome()
    tags:               list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id":                 self.id,
            "timestamp":          self.timestamp.isoformat(),
            "cycle_summary":      self.cycle_summary,
            "tickers_considered": self.tickers_considered,
            "regime":             self.regime,
            "action_taken":       self.action_taken,
            "trade_proposed":     self.trade_proposed,
            "outcome":            self.outcome,
            "tags":               self.tags,
        }


def _memory_from_record(doc: str, meta: dict) -> BrainMemory:
    """Rebuild a BrainMemory from a Chroma document + metadata record."""
    try:
        ts = datetime.fromisoformat(meta.get("timestamp", ""))
    except Exception:
        ts = datetime.now()
    return BrainMemory(
        id=meta.get("cycle_id", ""),
        timestamp=ts,
        cycle_summary=doc or "",
        tickers_considered=[t for t in (meta.get("tickers") or "").split(",") if t],
        regime=meta.get("regime", ""),
        action_taken=meta.get("action_taken", "MONITORING"),
        trade_proposed=json.loads(meta.get("trade_proposed") or "{}"),
        outcome=json.loads(meta.get("outcome") or "{}"),
        tags=[t for t in (meta.get("tags") or "").split(",") if t],
    )


class LongTermMemory:
    """
    Stores and retrieves memories as vector embeddings.
    Each memory = one completed reasoning cycle.
    """

    DB_PATH = ROOT / "data" / "brain_memory" / "chromadb"

    def __init__(self):
        import chromadb
        from sentence_transformers import SentenceTransformer
        self.DB_PATH.mkdir(parents=True, exist_ok=True)
        self.client = chromadb.PersistentClient(path=str(self.DB_PATH))
        self.collection = self.client.get_or_create_collection("aria_memories")
        self.embedder = SentenceTransformer("all-MiniLM-L6-v2")  # 80MB, CPU is fine

    # ── store ────────────────────────────────────────────────────────────

    def remember(self, memory: BrainMemory):
        """Store a completed reasoning cycle."""
        text = memory.cycle_summary or "(empty cycle)"
        embedding = self.embedder.encode(text).tolist()
        self.collection.upsert(
            ids=[memory.id],
            embeddings=[embedding],
            documents=[text],
            metadatas=[{
                "cycle_id":       memory.id,
                "timestamp":      memory.timestamp.isoformat(),
                "tickers":        ",".join(memory.tickers_considered),
                "regime":         memory.regime,
                "action_taken":   memory.action_taken,
                "trade_proposed": json.dumps(memory.trade_proposed, default=str),
                "outcome":        json.dumps(memory.outcome, default=str),
                "tags":           ",".join(memory.tags),
            }],
        )
        logger.info(f"Memory stored [{memory.id}] action={memory.action_taken}")

    # ── retrieve ─────────────────────────────────────────────────────────

    def recall(self, query: str, n: int = 5) -> list[BrainMemory]:
        """Semantic search — 'what have I seen similar to this?'"""
        if self.collection.count() == 0:
            return []
        embedding = self.embedder.encode(query).tolist()
        res = self.collection.query(
            query_embeddings=[embedding],
            n_results=min(n, self.collection.count()),
        )
        docs  = (res.get("documents") or [[]])[0]
        metas = (res.get("metadatas") or [[]])[0]
        return [_memory_from_record(d, m or {}) for d, m in zip(docs, metas)]

    def recall_ticker(self, ticker: str, n: int = 10) -> list[BrainMemory]:
        """Get past memories involving a specific ticker."""
        if self.collection.count() == 0:
            return []
        res = self.collection.get(include=["documents", "metadatas"])
        out = []
        for doc, meta in zip(res.get("documents") or [], res.get("metadatas") or []):
            tickers = (meta or {}).get("tickers", "").split(",")
            if ticker in tickers:
                out.append(_memory_from_record(doc, meta or {}))
        out.sort(key=lambda m: m.timestamp, reverse=True)
        return out[:n]

    def recent(self, n: int = 20) -> list[BrainMemory]:
        """Most recent memories, newest first."""
        if self.collection.count() == 0:
            return []
        res = self.collection.get(include=["documents", "metadatas"])
        memories = [_memory_from_record(d, m or {})
                    for d, m in zip(res.get("documents") or [], res.get("metadatas") or [])]
        memories.sort(key=lambda m: m.timestamp, reverse=True)
        return memories[:n]

    # ── learning ─────────────────────────────────────────────────────────

    def record_outcome(self, trade_id: str, outcome: dict):
        """
        After a trade closes, update the memory that proposed it.
        outcome = {ticker, side, entry_price, exit_price, pnl, pnl_pct, duration_days}
        This is how the brain learns — future recalls will see what happened.
        """
        res = self.collection.get(include=["documents", "metadatas"])
        for mem_id, doc, meta in zip(res.get("ids") or [],
                                     res.get("documents") or [],
                                     res.get("metadatas") or []):
            meta = meta or {}
            proposed = json.loads(meta.get("trade_proposed") or "{}")
            if trade_id not in (proposed.get("ids") or []):
                continue
            outcomes = json.loads(meta.get("outcome") or "{}")
            outcomes[trade_id] = outcome
            meta["outcome"] = json.dumps(outcomes, default=str)
            # Append the outcome to the document text so semantic recall sees it
            pnl_pct = outcome.get("pnl_pct")
            note = (f"\nOUTCOME [{outcome.get('ticker')}]: "
                    f"{outcome.get('side','?')} closed at {pnl_pct:+.2f}%"
                    if isinstance(pnl_pct, (int, float)) else "\nOUTCOME recorded.")
            new_doc = (doc or "") + note
            embedding = self.embedder.encode(new_doc).tolist()
            self.collection.update(ids=[mem_id], embeddings=[embedding],
                                   documents=[new_doc], metadatas=[meta])
            logger.info(f"Outcome recorded for trade {trade_id} on memory {mem_id}")
            return
        logger.warning(f"record_outcome: no memory found proposing trade {trade_id}")

    def stats(self) -> dict:
        return {"total_memories": self.collection.count(), "db_path": str(self.DB_PATH)}
