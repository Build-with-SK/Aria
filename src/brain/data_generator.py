"""
src/brain/data_generator.py
===========================
Generates fine-tuning training data (data/brain_training_data.jsonl)
from the live signal snapshot and historical signal database.

Each sample is a chat-format JSONL record:
  {"messages": [{"role": "system", ...}, {"role": "user", ...},
                {"role": "assistant", ...}]}
suitable for Unsloth / LoRA fine-tuning of a local model.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent.parent.parent
DATA = ROOT / "data"
OUT_FILE = DATA / "brain_training_data.jsonl"

SYSTEM = ("You are ARIA, a trading intelligence brain. You analyse market "
          "signals precisely, cite actual numbers, and always flag risk.")


def _load(name: str) -> dict:
    path = DATA / name
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _sample(user: str, assistant: str) -> dict:
    return {"messages": [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": user},
        {"role": "assistant", "content": assistant},
    ]}


def _signal_samples(signals: dict, macro: dict) -> list:
    """One analysis sample per ticker from the current signal snapshot."""
    regime = macro.get("regime", "Unknown")
    samples = []
    for ticker, s in signals.items():
        if not isinstance(s, dict):
            continue
        score = s.get("composite_score", 0)
        action = s.get("action", "Hold")
        drivers = "; ".join((s.get("drivers") or [])[:3])
        risks = "; ".join((s.get("risks") or [])[:2]) or "no major risks flagged"
        user = (f"Analyse {ticker} given: composite score {score:+.1f}, "
                f"trend {s.get('trend_score', 0):.0f}, momentum {s.get('momentum_score', 0):.0f}, "
                f"macro regime {regime}.")
        assistant = (
            f"{ticker} scores {score:+.1f} → {action} "
            f"({s.get('confidence', 'Low')} confidence). "
            f"Drivers: {drivers}. Risks: {risks}. "
            f"Stop ${s.get('stop_loss', 0):.2f}, target ${s.get('take_profit', 0):.2f}. "
            f"In a {regime} regime this signal "
            f"{'aligns with' if score * macro.get('macro_score', 0) >= 0 else 'conflicts with'} "
            f"the macro backdrop."
        )
        samples.append(_sample(user, assistant))
    return samples


def _history_samples(limit: int = 500) -> list:
    """Samples from the historical signals table in SQLite, if present."""
    db = DATA / "trading_intelligence.db"
    if not db.exists():
        return []
    samples = []
    try:
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        table = "signals" if "signals" in tables else (
            "signal_history" if "signal_history" in tables else None)
        if table:
            rows = conn.execute(
                f"SELECT * FROM {table} ORDER BY rowid DESC LIMIT ?", (limit,)
            ).fetchall()
            for r in rows:
                d = dict(r)
                ticker = d.get("ticker")
                score = d.get("composite_score")
                action = d.get("action")
                if not ticker or score is None or not action:
                    continue
                date = d.get("signal_date") or d.get("date") or "a past session"
                user = (f"On {date}, {ticker} had a composite score of "
                        f"{float(score):+.1f}. What was the appropriate stance?")
                assistant = (f"With a composite score of {float(score):+.1f}, "
                             f"the system's stance on {ticker} was {action}. "
                             f"Signals beyond ±30 with high confidence warrant "
                             f"action; weaker scores call for monitoring.")
                samples.append(_sample(user, assistant))
        conn.close()
    except Exception as e:
        logger.warning(f"History samples skipped: {e}")
    return samples


def generate_all() -> int:
    """Build the full training set and write brain_training_data.jsonl."""
    signals = _load("signals.json")
    macro = _load("macro_data.json")
    samples = _signal_samples(signals, macro) + _history_samples()

    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with OUT_FILE.open("w", encoding="utf-8") as f:
        for s in samples:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")
    logger.info(f"Wrote {len(samples)} training samples to {OUT_FILE}")
    return len(samples)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(f"Generated {generate_all()} samples → {OUT_FILE}")
