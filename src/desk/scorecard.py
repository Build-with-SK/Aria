"""
src/desk/scorecard.py
=====================
JUDGE CALIBRATION — track every debate verdict against its realized outcome,
then tilt the judge's agent weights toward whoever is actually predictive.

Per closed trade, each per-ticker agent (technical / fundamental / sentiment)
is scored: it was RIGHT if its stated view pointed the way the trade actually
resolved (bull view + long trade that made money, bear view + long trade that
lost money, etc). Neutral/abstained views don't count either way.

After MIN_TRADES closed trades the weights auto-adjust — bounded to
±MAX_TILT from the defaults, renormalized to sum 1, every change logged.
The bound is the safety: no agent can be silenced or crowned by a streak.

State: data/desk/scorecard.json
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent.parent.parent
SCORECARD_FILE = ROOT / "data" / "desk" / "scorecard.json"

DEFAULT_WEIGHTS = {"technical": 0.5, "fundamental": 0.3, "sentiment": 0.2}
MAX_TILT = 0.15        # each weight stays within ±this of its default
MIN_TRADES = 20        # no adjustment before this many closed trades


def _load() -> dict:
    if SCORECARD_FILE.exists():
        try:
            return json.loads(SCORECARD_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"scorecard unreadable: {e}")
    return {"agents": {a: {"right": 0, "wrong": 0} for a in DEFAULT_WEIGHTS},
            "trades": 0, "weights": dict(DEFAULT_WEIGHTS), "weight_log": []}


def _save(card: dict):
    SCORECARD_FILE.parent.mkdir(parents=True, exist_ok=True)
    SCORECARD_FILE.write_text(json.dumps(card, indent=2, default=str),
                              encoding="utf-8")


def current_weights() -> dict:
    """The judge's live agent weights (defaults until calibrated)."""
    return dict(_load().get("weights") or DEFAULT_WEIGHTS)


def agent_hit_rates() -> dict:
    card = _load()
    out = {}
    for agent, s in card.get("agents", {}).items():
        n = s.get("right", 0) + s.get("wrong", 0)
        out[agent] = {"right": s.get("right", 0), "wrong": s.get("wrong", 0),
                      "hit_rate": round(s.get("right", 0) / n, 3) if n else None}
    out["trades_scored"] = card.get("trades", 0)
    return out


def record_closed_trade(record: dict):
    """Score each agent's debate view against the realized outcome, then
    recalibrate the weights if enough evidence has accumulated."""
    debate_id = record.get("debate_id")
    pnl = float(record.get("pnl") or 0.0)
    if not debate_id or pnl == 0.0:
        return
    # Audit H1: estimated closes (no confirmed fill) must not calibrate the
    # judge — only real outcomes teach.
    if record.get("estimated"):
        return
    # Audit M2: a 50% scale-out leg and its runner are one logical trade —
    # only the final close (fraction 1.0) scores the agents.
    if float(record.get("fraction") or 1.0) < 1.0:
        return
    try:
        from src.desk.debate import load_debate
        transcript = load_debate(debate_id)
    except Exception:
        transcript = {}
    if not transcript:
        return

    entry_side = record.get("entry_side", "buy")     # the direction traded
    trade_won = pnl > 0
    card = _load()
    for op in transcript.get("opinions", []):
        agent, view = op.get("agent"), op.get("view")
        if agent not in DEFAULT_WEIGHTS or view not in ("bull", "bear"):
            continue        # macro conditions separately; neutral abstains
        agreed = (view == "bull") == (entry_side == "buy")
        right = agreed == trade_won
        key = "right" if right else "wrong"
        card["agents"].setdefault(agent, {"right": 0, "wrong": 0})
        card["agents"][agent][key] = card["agents"][agent].get(key, 0) + 1
    card["trades"] = card.get("trades", 0) + 1

    _recalibrate(card)
    _save(card)


def _bounded_normalize(raw: dict) -> dict:
    """Normalize toward sum=1 while keeping every FINAL weight within
    ±MAX_TILT of its default (audit H2: clamping tilts and THEN renormalizing
    silently broke the bound). Violators are pinned to their bound and the
    remaining mass is redistributed among the others."""
    lo = {a: DEFAULT_WEIGHTS[a] - MAX_TILT for a in raw}
    hi = {a: DEFAULT_WEIGHTS[a] + MAX_TILT for a in raw}
    fixed: dict = {}
    free = dict(raw)
    for _ in range(len(raw)):
        remaining = 1.0 - sum(fixed.values())
        total_free = sum(free.values()) or 1.0
        scaled = {a: w * remaining / total_free for a, w in free.items()}
        violators = {a: w for a, w in scaled.items()
                     if w < lo[a] - 1e-9 or w > hi[a] + 1e-9}
        if not violators:
            fixed.update(scaled)
            break
        for a in violators:
            fixed[a] = min(max(scaled[a], lo[a]), hi[a])
            free.pop(a)
        if not free:
            break
    return {a: round(w, 4) for a, w in fixed.items()}


def _recalibrate(card: dict):
    """Bounded tilt toward predictive agents: weight = default + tilt where
    tilt scales with (hit_rate − 0.5); the ±MAX_TILT bound is enforced on
    the final, normalized weights."""
    if card.get("trades", 0) < MIN_TRADES:
        return
    raw = {}
    for agent, default in DEFAULT_WEIGHTS.items():
        s = card["agents"].get(agent, {})
        n = s.get("right", 0) + s.get("wrong", 0)
        if n == 0:
            raw[agent] = default
            continue
        hit = s["right"] / n
        tilt = max(-MAX_TILT, min(MAX_TILT, (hit - 0.5) * 2 * MAX_TILT / 0.5))
        raw[agent] = default + tilt
    new = _bounded_normalize(raw)
    old = card.get("weights") or dict(DEFAULT_WEIGHTS)
    if any(abs(new[a] - old.get(a, DEFAULT_WEIGHTS[a])) > 0.005 for a in new):
        card["weights"] = new
        card.setdefault("weight_log", []).append({
            "at": datetime.now().isoformat(),
            "trades": card["trades"],
            "from": old, "to": new,
            "hit_rates": {a: round(card["agents"][a]["right"] /
                                   max(1, card["agents"][a]["right"] +
                                       card["agents"][a]["wrong"]), 3)
                          for a in DEFAULT_WEIGHTS if a in card["agents"]},
        })
        logger.info(f"judge weights recalibrated after {card['trades']} trades: {new}")
