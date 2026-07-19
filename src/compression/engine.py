"""
src/compression/engine.py
=========================
Progressive context compression — 5-stage cascade by context utilization
(port of Automaton's compression-engine.ts) for ARIA's long chat sessions.

  < 70%   NONE        — leave the transcript alone
  ≥ 70%   COMPACT     — old tool results → retrievable SQLite stubs
  ≥ 80%   SUMMARIZE   — oldest turn batches → LLM summaries (must preserve
                        decisions, tickers, unresolved questions, and user
                        instructions; deterministic fallback if no LLM)
  ≥ 90%   CHECKPOINT  — full transcript stored under a ULID, context reset
                        to a checkpoint notice + the freshest turns
  ≥ 95%   TRUNCATE    — loud emergency cut to the last few turns

Every action is a typed audit event (action, turn ids, tokens saved) in
the SQLite store. Thresholds: configs/compression.json overrides.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from src.compression.store import CompressionStore

logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent.parent.parent
CONFIG_FILE = ROOT / "configs" / "compression.json"

DEFAULTS = {
    "context_limit_tokens": 24000,
    "compact_at": 0.70,
    "summarize_at": 0.80,
    "checkpoint_at": 0.90,
    "truncate_at": 0.95,
    "keep_recent_turns": 6,        # never compress the freshest N messages
    "summary_batch_turns": 8,      # messages per summarized batch
    "summary_max_tokens": 150,     # budget per batch summary
    "checkpoint_keep_turns": 4,
    "truncate_keep_turns": 4,
}


def load_thresholds() -> dict:
    cfg = dict(DEFAULTS)
    if CONFIG_FILE.exists():
        try:
            cfg.update(json.loads(CONFIG_FILE.read_text(encoding="utf-8")))
        except Exception as e:
            logger.warning(f"compression config unreadable: {e}")
    return cfg


def estimate_tokens(messages: list[dict]) -> int:
    """~4 chars/token heuristic over serialized content."""
    total = 0
    for m in messages:
        content = m.get("content", "")
        if not isinstance(content, str):
            content = json.dumps(content, default=str)
        total += len(content) // 4 + 4
    return total


def _is_tool_result(msg: dict) -> bool:
    content = msg.get("content")
    if isinstance(content, list):
        return any(isinstance(b, dict) and b.get("type") == "tool_result"
                   for b in content)
    return bool(msg.get("tool_result"))


class CompressionEngine:
    def __init__(self, store: CompressionStore | None = None,
                 llm=None, config: dict | None = None):
        """llm: optional callable (prompt: str, max_tokens: int) -> str
        used for stage-2 summaries; None → deterministic fallback."""
        self.store = store or CompressionStore()
        self.llm = llm
        self.cfg = config or load_thresholds()

    # ── main entry ───────────────────────────────────────────────────────

    def process(self, messages: list[dict]) -> list[dict]:
        """Apply whatever stages the current utilization demands.
        Returns the (possibly compressed) message list; the input list is
        not mutated. Each stage re-measures before escalating."""
        limit = int(self.cfg["context_limit_tokens"])
        msgs = list(messages)

        if self._util(msgs, limit) >= self.cfg["compact_at"]:
            msgs = self._compact_tool_results(msgs)
        if self._util(msgs, limit) >= self.cfg["summarize_at"]:
            msgs = self._summarize_batches(msgs)
        if self._util(msgs, limit) >= self.cfg["checkpoint_at"]:
            msgs = self._checkpoint_reset(msgs)
        if self._util(msgs, limit) >= self.cfg["truncate_at"]:
            msgs = self._emergency_truncate(msgs)
        return msgs

    def utilization(self, messages: list[dict]) -> float:
        return self._util(messages, int(self.cfg["context_limit_tokens"]))

    def _util(self, msgs: list[dict], limit: int) -> float:
        return estimate_tokens(msgs) / max(1, limit)

    # ── stage 1: compact tool results ────────────────────────────────────

    def _compact_tool_results(self, msgs: list[dict]) -> list[dict]:
        keep = int(self.cfg["keep_recent_turns"])
        before = estimate_tokens(msgs)
        out, compacted = [], []
        cutoff = max(0, len(msgs) - keep)
        for i, m in enumerate(msgs):
            if i < cutoff and _is_tool_result(m):
                content = m.get("content")
                raw = content if isinstance(content, str) else json.dumps(
                    content, default=str)
                if len(raw) > 400:      # tiny results aren't worth a stub
                    sid = self.store.put("tool_result", raw)
                    out.append({"role": m.get("role", "user"),
                                "content": f"[tool result compacted → stub "
                                           f"{sid}; retrievable from the "
                                           f"compression store]"})
                    compacted.append(i)
                    continue
            out.append(m)
        if compacted:
            after = estimate_tokens(out)
            self.store.audit("COMPACT", compacted, before, after,
                             f"{len(compacted)} tool results → stubs")
            logger.info(f"compression COMPACT: {len(compacted)} tool results, "
                        f"{before - after} tokens saved")
        return out

    # ── stage 2: summarize turn batches ──────────────────────────────────

    SUMMARY_PROMPT = (
        "Summarize this conversation excerpt in under {budget} tokens. "
        "You MUST preserve: every decision made, every ticker mentioned, "
        "every unresolved question, and every instruction the user gave. "
        "Drop pleasantries and tool noise.\n\nEXCERPT:\n{excerpt}")

    def _summarize_batches(self, msgs: list[dict]) -> list[dict]:
        keep = int(self.cfg["keep_recent_turns"])
        batch_size = int(self.cfg["summary_batch_turns"])
        budget = int(self.cfg["summary_max_tokens"])
        before = estimate_tokens(msgs)

        compressible = max(0, len(msgs) - keep)
        if compressible < batch_size:
            return msgs
        out = []
        i = 0
        while i + batch_size <= compressible:
            batch = msgs[i:i + batch_size]
            excerpt = "\n".join(
                f"{m.get('role')}: {m.get('content') if isinstance(m.get('content'), str) else json.dumps(m.get('content'), default=str)[:400]}"
                for m in batch)
            summary = self._summarize(excerpt, budget)
            sid = self.store.put("summary_source", excerpt)
            out.append({"role": "user",
                        "content": f"[SUMMARY of turns {i}-{i + batch_size - 1} "
                                   f"(source stub {sid})]: {summary}"})
            self.store.audit("SUMMARIZE", list(range(i, i + batch_size)),
                             estimate_tokens(batch),
                             estimate_tokens([out[-1]]),
                             f"batch → {len(summary) // 4} tokens")
            i += batch_size
        out.extend(msgs[i:])
        after = estimate_tokens(out)
        logger.info(f"compression SUMMARIZE: {i} turns batched, "
                    f"{before - after} tokens saved")
        return out

    def _summarize(self, excerpt: str, budget_tokens: int) -> str:
        char_budget = budget_tokens * 4
        if self.llm is not None:
            try:
                text = self.llm(self.SUMMARY_PROMPT.format(
                    budget=budget_tokens, excerpt=excerpt[:6000]), budget_tokens)
                if text:
                    return text[:char_budget]
            except Exception as e:
                logger.warning(f"summary LLM failed, deterministic fallback: {e}")
        # Deterministic fallback: first sentence of each turn, capped
        lines = [l.strip()[:120] for l in excerpt.splitlines() if l.strip()]
        return " | ".join(lines)[:char_budget]

    # ── stage 3: checkpoint and reset ────────────────────────────────────

    def _checkpoint_reset(self, msgs: list[dict]) -> list[dict]:
        keep = int(self.cfg["checkpoint_keep_turns"])
        before = estimate_tokens(msgs)
        cid = self.store.put("checkpoint", json.dumps(msgs, default=str))
        tail = msgs[-keep:] if keep else []
        out = [{"role": "user",
                "content": f"[CONTEXT CHECKPOINT {cid}: earlier conversation "
                           f"stored and reset. Retrieve the full transcript "
                           f"from the compression store if needed.]"}] + tail
        after = estimate_tokens(out)
        self.store.audit("CHECKPOINT", list(range(len(msgs) - len(tail))),
                         before, after, f"checkpoint {cid}")
        logger.warning(f"compression CHECKPOINT {cid}: context reset, "
                       f"{before - after} tokens saved")
        return out

    # ── stage 4: emergency truncate ──────────────────────────────────────

    def _emergency_truncate(self, msgs: list[dict]) -> list[dict]:
        keep = int(self.cfg["truncate_keep_turns"])
        before = estimate_tokens(msgs)
        out = msgs[-keep:]
        after = estimate_tokens(out)
        self.store.audit("EMERGENCY_TRUNCATE",
                         list(range(len(msgs) - len(out))), before, after,
                         f"dropped {len(msgs) - len(out)} messages")
        logger.error(f"compression EMERGENCY TRUNCATE: dropped "
                     f"{len(msgs) - len(out)} messages "
                     f"({before - after} tokens) — context was at "
                     f">{int(self.cfg['truncate_at'] * 100)}% of limit")
        return out
