"""
src/desk/teacher.py
===================
THE TEACHER — Fable grades ARIA's work and teaches her from it.

For every CLOSED trade (grounded in real realized P&L), Fable (claude-fable-5)
reviews the original debate against what actually happened and produces a
structured lesson: was the thesis sound, was the direction right, which
analyst was misleading, and what to do differently. Lessons are stored in a
plain file (data/desk/lessons.jsonl — works on the Mac mini with no ChromaDB)
and recalled into future debates on the same ticker, so ARIA's next decision
carries what she learned from the last one.

Safety / discipline:
- The teacher NEVER trades and never touches the risk or exit rules.
- Fable ADVISES; it does not set the judge weights. The scorecard's bounded,
  deterministic calibration (audit H2) stays the only thing that moves weights.
  The teacher's influence is purely through recalled lessons in the debate
  prompt — exactly like the existing memory recall.
- Off by default (`teacher_enabled`), budget-capped (`teacher_daily_cap`),
  routed through the inference router so a dead API never stalls anything.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime
from pathlib import Path

from src.desk.config import load_config

logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent.parent.parent
DESK_DIR = ROOT / "data" / "desk"
LESSONS_FILE = DESK_DIR / "lessons.jsonl"
STATE_FILE = DESK_DIR / "teacher_state.json"


# ── pure helpers (unit-tested, no I/O) ───────────────────────────────────────

def build_review_prompt(record: dict, transcript: dict) -> str:
    """The teaching prompt: the debate that was, and the outcome that came."""
    judge = (transcript or {}).get("judge") or {}
    opinions = (transcript or {}).get("opinions") or []
    views = "; ".join(f"{o.get('agent')}={o.get('view')}({o.get('conviction')})"
                      for o in opinions if o.get("agent") in
                      ("technical", "fundamental", "sentiment"))
    return (
        f"You are Fable, a master trading mentor reviewing a junior desk's CLOSED "
        f"paper trade. Judge it honestly and teach.\n\n"
        f"TICKER: {record.get('ticker')}  SIDE: {record.get('entry_side')}\n"
        f"Entry {record.get('entry_price')} → exit {record.get('exit_price')}  "
        f"P&L {record.get('pnl_pct')}%  ({record.get('r_multiple')}R)  "
        f"exit reason: {record.get('reason')}\n"
        f"Original verdict: {judge.get('verdict')} conviction {judge.get('conviction')}, "
        f"key risk cited: {judge.get('key_risk')}\n"
        f"Analyst views at entry: {views or 'n/a'}\n"
        f"Thesis: {(record.get('thesis') or '')[:400]}\n\n"
        f"Reply with STRICT JSON only, no prose, no code fence. Keep key_lesson "
        f"and do_differently UNDER 20 words each:\n"
        f'{{"thesis_quality": <0-100>, "direction_right": <true|false>, '
        f'"misleading_agent": "<technical|fundamental|sentiment|none>", '
        f'"key_lesson": "<=20 words ARIA should remember>", '
        f'"do_differently": "<=20 words, one concrete adjustment>"}}')


def parse_review(text: str) -> dict | None:
    """Extract the JSON lesson from Fable's reply; None if unparseable."""
    if not text:
        return None
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
    except Exception:
        return None
    q = d.get("thesis_quality")
    try:
        q = max(0, min(100, int(q)))
    except Exception:
        q = None
    agent = str(d.get("misleading_agent", "none")).lower().strip()
    if agent not in ("technical", "fundamental", "sentiment", "none"):
        agent = "none"
    lesson = str(d.get("key_lesson", "")).strip()[:280]
    if not lesson:
        return None
    return {
        "thesis_quality": q,
        "direction_right": bool(d.get("direction_right")),
        "misleading_agent": agent,
        "key_lesson": lesson,
        "do_differently": str(d.get("do_differently", "")).strip()[:280],
    }


# ── state / budget ───────────────────────────────────────────────────────────

def _load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"reviewed": [], "day": "", "count": 0}


def _save_state(s: dict):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(s, indent=2), encoding="utf-8")


def _budget_left(cfg: dict, state: dict) -> int:
    today = date.today().isoformat()
    if state.get("day") != today:
        state["day"], state["count"] = today, 0
    return int(cfg.get("teacher_daily_cap", 40)) - int(state.get("count", 0))


# ── the teacher ──────────────────────────────────────────────────────────────

def _call_fable(prompt: str, model: str) -> str | None:
    try:
        from src.inference.router import get_router
        return get_router().complete_with(
            "anthropic", model,
            [{"role": "user", "content": prompt}],
            max_tokens=400, temperature=0.3, timeout=45).text
    except Exception as e:
        logger.warning(f"teacher Fable call failed: {e}")
        return None


def review_trade(record: dict, transcript: dict, cfg: dict | None = None) -> dict | None:
    """Review one closed trade with Fable → a persisted lesson (or None)."""
    cfg = cfg or load_config()
    lesson = parse_review(_call_fable(build_review_prompt(record, transcript),
                                      cfg.get("teacher_model", "claude-fable-5")))
    if not lesson:
        return None
    entry = {
        "at": datetime.now().isoformat(),
        "debate_id": record.get("debate_id", ""),
        "ticker": record.get("ticker"),
        "side": record.get("entry_side"),
        "pnl_pct": record.get("pnl_pct"),
        "r_multiple": record.get("r_multiple"),
        **lesson,
        "teacher": cfg.get("teacher_model", "claude-fable-5"),
    }
    _append_lesson(entry)
    return entry


def teach_from_closed_trades(cfg: dict | None = None, limit: int = 20) -> dict:
    """Review closed trades not yet taught. Called by the desk's LEARN step.
    Skips silently when disabled or out of budget — never blocks the cycle."""
    cfg = cfg or load_config()
    if not cfg.get("teacher_enabled"):
        return {"reviewed": 0, "skipped": "teacher disabled"}
    from src.desk.position_manager import read_closed_trades
    state = _load_state()
    reviewed = set(state.get("reviewed", []))
    trades = [t for t in read_closed_trades(500)
              if t.get("debate_id") and t.get("debate_id") not in reviewed
              and not t.get("estimated")            # real fills only (audit H1)
              and float(t.get("fraction") or 1.0) >= 1.0]
    done = []
    for t in trades[-limit:]:
        if _budget_left(cfg, state) <= 0:
            break
        from src.desk.debate import load_debate
        lesson = review_trade(t, load_debate(t["debate_id"]), cfg)
        reviewed.add(t["debate_id"])
        state["count"] = int(state.get("count", 0)) + 1
        if lesson:
            done.append({"ticker": t["ticker"], "lesson": lesson["key_lesson"]})
    state["reviewed"] = list(reviewed)[-2000:]
    _save_state(state)
    if done:
        logger.info(f"teacher: Fable taught {len(done)} lessons")
    return {"reviewed": len(done), "lessons": done}


# ── lessons store + recall ───────────────────────────────────────────────────

def _append_lesson(entry: dict):
    try:
        LESSONS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with LESSONS_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except Exception as e:
        logger.warning(f"could not write lesson: {e}")


def read_lessons(n: int = 200) -> list[dict]:
    if not LESSONS_FILE.exists():
        return []
    out = []
    for line in LESSONS_FILE.read_text(encoding="utf-8").splitlines()[-n:]:
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def recall_lessons(ticker: str, n: int = 3) -> str:
    """Fable's lessons for this ticker, newest first, as debate-ready text.
    Injected into future debates so ARIA carries what she was taught."""
    tk = (ticker or "").upper()
    hits = [l for l in read_lessons(500) if str(l.get("ticker", "")).upper() == tk]
    if not hits:
        return ""
    lines = []
    for l in reversed(hits[-n:]):
        lines.append(f"- Fable's lesson ({l.get('pnl_pct')}% trade): "
                     f"{l.get('key_lesson')}"
                     + (f" Do differently: {l.get('do_differently')}"
                        if l.get('do_differently') else ""))
    return "Lessons from Fable on past " + tk + " trades:\n" + "\n".join(lines)
