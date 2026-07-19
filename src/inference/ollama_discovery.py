"""
src/inference/ollama_discovery.py
=================================
Ollama model auto-discovery (Automaton's discover.ts, in Python).

On startup and on demand: query /api/tags, parse name / parameter size /
quantization, upsert into a SQLite registry that the inference router
consumes as extra local fallback candidates.

Tier heuristic (config overrides in configs/inference_tiers.json always win):
  ≤ 4B params  → FAST
  5–14B        → STANDARD
  ≥ 15B        → DEEP fallback

Ollama unreachable → warn, mark local models down, continue remote-only.
Never crashes the caller.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import urllib.request
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent.parent.parent
REGISTRY_DB = ROOT / "data" / "model_registry.db"
OLLAMA_BASE = "http://localhost:11434"

FAST_MAX_B = 4.0
STANDARD_MAX_B = 14.0


def parse_param_size(model: dict) -> float | None:
    """Billions of parameters, from details.parameter_size ('7.6B', '70B')
    or, failing that, from the model name ('qwen2.5-coder:7b')."""
    details = model.get("details") or {}
    raw = str(details.get("parameter_size") or "")
    m = re.match(r"([\d.]+)\s*([BM])", raw, re.IGNORECASE)
    if m:
        n = float(m.group(1))
        return n / 1000 if m.group(2).upper() == "M" else n
    m = re.search(r":(\d+(?:\.\d+)?)b\b", str(model.get("name", "")).lower())
    if m:
        return float(m.group(1))
    return None


def tier_for(params_b: float | None) -> str:
    if params_b is None:
        return "STANDARD"          # unknown size — assume mid
    if params_b <= FAST_MAX_B:
        return "FAST"
    if params_b <= STANDARD_MAX_B:
        return "STANDARD"
    return "DEEP"


def _connect() -> sqlite3.Connection:
    REGISTRY_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(REGISTRY_DB))
    conn.execute("""CREATE TABLE IF NOT EXISTS models (
        name TEXT PRIMARY KEY,
        provider TEXT NOT NULL DEFAULT 'ollama',
        params_b REAL,
        quantization TEXT,
        tier TEXT,
        available INTEGER NOT NULL DEFAULT 1,
        last_seen TEXT
    )""")
    return conn


def discover(base_url: str = OLLAMA_BASE, timeout: float = 5.0) -> dict:
    """Query /api/tags and upsert every model. Returns a status dict.
    Unreachable Ollama marks all local models unavailable and returns
    ok=False — callers keep going remote-only."""
    try:
        with urllib.request.urlopen(f"{base_url}/api/tags", timeout=timeout) as r:
            data = json.loads(r.read())
        models = data.get("models") or []
    except Exception as e:
        logger.warning(f"Ollama discovery failed ({e}) — marking local models "
                       f"unavailable, continuing remote-only")
        try:
            with _connect() as conn:
                conn.execute("UPDATE models SET available = 0 "
                             "WHERE provider = 'ollama'")
        except Exception as db_e:
            logger.warning(f"registry update failed: {db_e}")
        return {"ok": False, "error": str(e), "models": []}

    now = datetime.now().isoformat()
    rows = []
    for m in models:
        name = m.get("name")
        if not name:
            continue
        params = parse_param_size(m)
        quant = str((m.get("details") or {}).get("quantization_level") or "")
        rows.append((name, params, quant, tier_for(params), now))
    try:
        with _connect() as conn:
            conn.execute("UPDATE models SET available = 0 WHERE provider = 'ollama'")
            conn.executemany(
                """INSERT INTO models (name, provider, params_b, quantization,
                                       tier, available, last_seen)
                   VALUES (?, 'ollama', ?, ?, ?, 1, ?)
                   ON CONFLICT(name) DO UPDATE SET
                     params_b = excluded.params_b,
                     quantization = excluded.quantization,
                     tier = excluded.tier,
                     available = 1,
                     last_seen = excluded.last_seen""", rows)
    except Exception as e:
        logger.warning(f"registry write failed: {e}")
    logger.info(f"Ollama discovery: {len(rows)} models registered")
    return {"ok": True, "models": [
        {"name": r[0], "params_b": r[1], "quantization": r[2], "tier": r[3]}
        for r in rows]}


def list_models(available_only: bool = True) -> list[dict]:
    try:
        with _connect() as conn:
            conn.row_factory = sqlite3.Row
            q = "SELECT * FROM models"
            if available_only:
                q += " WHERE available = 1"
            return [dict(r) for r in conn.execute(q + " ORDER BY params_b DESC")]
    except Exception as e:
        logger.warning(f"registry read failed: {e}")
        return []


def registry_candidates() -> dict:
    """{tier: [[provider, model], ...]} extra local fallbacks for the router.
    DEEP additionally falls back to the biggest available local model."""
    out: dict = {"FAST": [], "STANDARD": [], "DEEP": []}
    models = list_models(available_only=True)
    for m in models:
        tier = m.get("tier") or "STANDARD"
        if tier in out:
            out[tier].append(["ollama", m["name"]])
    if models and not out["DEEP"]:
        # biggest local model backs DEEP even if it only rates STANDARD
        out["DEEP"].append(["ollama", models[0]["name"]])
    return out
