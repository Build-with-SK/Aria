"""
src/desk/debate.py
==================
THE DEBATE — bull researcher vs bear researcher, N rounds, then a judge.

Elevates the aria_core debate idea into the desk pipeline:
- Both researchers argue ONLY from the analysts' evidence lists — every
  claim in the transcript is traceable to a real ARIA number.
- Each round must answer the opponent's strongest point, not restate.
- Prose comes from the local Ollama model when available; when it is not,
  a deterministic composer builds the argument straight from the evidence,
  so the desk never stalls. The judge's verdict numbers are ALWAYS
  computed deterministically from the evidence — the LLM writes prose,
  it never decides conviction.
- The full transcript persists to data/desk/debates/<id>.json.
- Contradiction guard: flags when bull and bear cite the same number to
  opposite ends, and folds in the cognitive ContradictionEngine when
  available.
"""
from __future__ import annotations

import json
import logging
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path

from src.desk.opinion import Opinion, load_data_json

logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent.parent.parent
DEBATES_DIR = ROOT / "data" / "desk" / "debates"
OLLAMA_BASE = "http://localhost:11434"

# Judge weights over the per-ticker analysts (macro conditions separately)
WEIGHTS = {"technical": 0.5, "fundamental": 0.3, "sentiment": 0.2}
CONTRADICTION_PENALTY = 8       # conviction points per flagged contradiction


def _ollama_available() -> bool:
    try:
        urllib.request.urlopen(f"{OLLAMA_BASE}/api/tags", timeout=2)
        return True
    except Exception:
        return False


def _llm(prompt: str, model: str, num_predict: int = 260) -> str | None:
    """One STANDARD-tier local call via the inference router (breakers +
    retries). None on any failure — callers must fall back deterministically."""
    try:
        from src.inference.router import get_router
        return get_router().complete_with(
            "ollama", model,
            [{"role": "user", "content": prompt}],
            max_tokens=num_predict, temperature=0.6, timeout=90).text
    except Exception as e:
        logger.warning(f"debate LLM call failed: {e}")
        return None


def _fmt_evidence(items: list, cap: int = 8) -> str:
    return "\n".join(f"- {e['claim']}  [source: {e['source']}]" for e in items[:cap]) or "- (none)"


class DebateEngine:
    def __init__(self, model: str = "qwen2.5-coder:7b", rounds: int = 2, memory=None):
        self.model = model
        self.rounds = max(1, rounds)
        self.memory = memory            # LongTermMemory (optional)
        self.use_llm = _ollama_available()

    # ── main entry ────────────────────────────────────────────────────────

    def debate(self, ticker: str, opinions: list[Opinion], conditioner) -> dict:
        """Run the full debate for one ticker. Returns the persisted transcript dict."""
        debate_id = f"dbt-{uuid.uuid4().hex[:8]}"
        per_ticker = [o for o in opinions if o.agent in WEIGHTS]
        macro_op = conditioner.opinion

        bull_ev = [e for o in per_ticker for e in o.bullish_evidence()]
        bear_ev = [e for o in per_ticker for e in o.bearish_evidence()]
        bear_ev += macro_op.bearish_evidence()      # macro conditions the bear case
        bull_ev += macro_op.bullish_evidence()

        recall = self._recall(ticker)
        contradictions = self._contradiction_guard(ticker, bull_ev, bear_ev)

        rounds = self._run_rounds(ticker, bull_ev, bear_ev, recall, contradictions)
        judge = self._judge(ticker, per_ticker, conditioner, bull_ev, bear_ev,
                            contradictions, rounds)

        transcript = {
            "id": debate_id,
            "ticker": ticker,
            "at": datetime.now().isoformat(),
            "llm": self.model if self.use_llm else "deterministic (Ollama offline)",
            "opinions": [o.to_dict() for o in per_ticker] + [macro_op.to_dict()],
            "memory_context": recall,
            "contradictions": contradictions,
            "rounds": rounds,
            "judge": judge,
        }
        self._persist(transcript)
        return transcript

    # ── researchers ───────────────────────────────────────────────────────

    def _run_rounds(self, ticker, bull_ev, bear_ev, recall, contradictions) -> list:
        rounds = []
        last_bull, last_bear = "", ""
        for n in range(1, self.rounds + 1):
            bull = self._argue("bull", ticker, bull_ev, last_bear, recall, n)
            bear = self._argue("bear", ticker, bear_ev, bull, recall, n)
            rounds.append({"round": n, "bull": bull, "bear": bear})
            last_bull, last_bear = bull, bear
        if contradictions:
            rounds.append({
                "round": "guard",
                "note": "Contradiction guard: " + "; ".join(c["flag"] for c in contradictions),
            })
        return rounds

    def _argue(self, side: str, ticker: str, evidence: list,
               opponent_last: str, recall: str, round_no: int) -> str:
        role = "bull researcher building the long case" if side == "bull" \
            else "bear researcher rebutting the long case"
        if self.use_llm:
            counter = (f"\nYour opponent just argued:\n{opponent_last[:700]}\n"
                       f"You MUST answer their strongest point directly — do not restate your own case."
                       if opponent_last else "")
            memo = f"\nRelevant past outcomes from memory:\n{recall[:500]}" if recall else ""
            out = _llm(
                f"You are the {role} on {ticker} at an autonomous trading desk.\n"
                f"Round {round_no}. Argue ONLY from this evidence — cite the numbers, "
                f"never invent data:\n{_fmt_evidence(evidence)}\n{memo}{counter}\n"
                f"Write one tight paragraph (max 120 words).",
                self.model)
            if out:
                return out
        # Deterministic composer — evidence speaks for itself
        pts = "; ".join(e["claim"] for e in evidence[:5]) or "no supporting evidence"
        rebut = " Answering the opponent: the cited numbers above stand regardless." if opponent_last else ""
        return (f"[{side.upper()} case, round {round_no}, from evidence] {pts}.{rebut}")

    # ── judge ─────────────────────────────────────────────────────────────

    def _judge(self, ticker, per_ticker, conditioner, bull_ev, bear_ev,
               contradictions, rounds) -> dict:
        # Deterministic verdict math — an LLM never touches these numbers.
        # Weights come from the scorecard (bounded, outcome-calibrated);
        # the static WEIGHTS are the uncalibrated defaults.
        try:
            from src.desk.scorecard import current_weights
            weights = current_weights()
        except Exception:
            weights = dict(WEIGHTS)
        net = 0.0
        for o in per_ticker:
            direction = 1 if o.view == "bull" else -1 if o.view == "bear" else 0
            net += weights.get(o.agent, WEIGHTS[o.agent]) * o.conviction * direction
        view = "bull" if net > 0 else "bear" if net < 0 else "neutral"
        conviction = int(min(100, abs(net)))
        conviction -= CONTRADICTION_PENALTY * len(contradictions)
        if conditioner.opinion.view == "bear" and view == "bull":
            conviction -= 10        # fighting the macro tape costs conviction
        conviction = max(0, conviction)

        bar = conditioner.conviction_bar
        verdict = ("BUY" if view == "bull" and conviction >= bar else
                   "SELL" if view == "bear" and conviction >= bar else
                   "NO_TRADE")

        sig = load_data_json("signals.json").get(ticker) or {}
        invalidation = sig.get("invalidation") or sig.get("stop_loss") or 0.0
        key_risk = (bear_ev[0]["claim"] if bear_ev else "No explicit bear evidence — thin debate.")

        reasoning = self._judge_prose(ticker, view, conviction, verdict, bar,
                                      bull_ev, bear_ev, rounds, conditioner)
        return {
            "view": view,
            "conviction": conviction,
            "conviction_bar": bar,
            "verdict": verdict,
            "key_risk": key_risk,
            "invalidation_level": round(float(invalidation), 4),
            "regime": conditioner.regime,
            "risk_multiplier": conditioner.risk_multiplier,
            "reasoning": reasoning,
        }

    def _judge_prose(self, ticker, view, conviction, verdict, bar,
                     bull_ev, bear_ev, rounds, conditioner) -> str:
        """Prose only. May consult the frontier (existing switch + budget)."""
        prompt = (
            f"You are the judge of a bull-vs-bear debate on {ticker}.\n"
            f"Bull evidence:\n{_fmt_evidence(bull_ev, 6)}\n"
            f"Bear evidence:\n{_fmt_evidence(bear_ev, 6)}\n"
            f"Macro: {conditioner.opinion.thesis}\n"
            f"The computed verdict is {verdict} ({view}, conviction {conviction}/100, bar {bar}).\n"
            f"In 3 sentences, explain WHY this verdict follows from the evidence, "
            f"naming the single strongest point on each side. Do not change the verdict."
        )
        # Frontier consult — DEEP tier via the router; the brain's consult
        # switch and daily budget cap stay the routing policy.
        try:
            from src.brain.cognitive.reasoner import (
                _consult_config, _consult_budget_left, _consult_log)
            cfg = _consult_config()
            if cfg.get("enabled") and _consult_budget_left(cfg):
                from src.inference.router import Tier, get_router
                result = get_router().complete_with(
                    "anthropic", cfg.get("frontier_model", "claude-sonnet-4-6"),
                    [{"role": "user", "content": prompt}],
                    max_tokens=300, timeout=45)
                if result.text:
                    _consult_log(f"DESK_JUDGE_{ticker}")
                    return result.text
        except Exception as e:
            logger.debug(f"frontier judge prose unavailable: {e}")

        if self.use_llm:
            out = _llm(prompt, self.model, num_predict=200)
            if out:
                return out
        strongest_bull = bull_ev[0]["claim"] if bull_ev else "none"
        strongest_bear = bear_ev[0]["claim"] if bear_ev else "none"
        return (f"Verdict {verdict}: weighted analyst conviction nets {view} at {conviction}/100 "
                f"against a bar of {bar}. Strongest bull point: {strongest_bull}. "
                f"Strongest bear point: {strongest_bear}.")

    # ── memory recall (Module 5 reflection, read side) ────────────────────

    def _recall(self, ticker: str) -> str:
        """'Last time this thesis fired, what happened?' — per-thesis memory."""
        if self.memory is None:
            return ""
        try:
            mems = self.memory.recall(f"desk debate {ticker} trade outcome", n=3)
            lines = []
            for m in mems:
                if "desk-debate" not in (m.tags or []):
                    continue
                for _, o in (m.outcome or {}).items():
                    if isinstance(o, dict) and "pnl_pct" in o:
                        lines.append(
                            f"- Past desk trade on {o.get('ticker')}: {o.get('side')} closed "
                            f"{o['pnl_pct']:+.1f}% in {o.get('duration_days', '?')} days "
                            f"(debate {m.id}).")
            return "\n".join(lines[:3])
        except Exception as e:
            logger.debug(f"debate recall failed: {e}")
            return ""

    # ── contradiction guard ───────────────────────────────────────────────

    def _contradiction_guard(self, ticker: str, bull_ev: list, bear_ev: list) -> list:
        flags = []
        # Same number cited to opposite ends → the debate is spinning one fact
        bear_vals = {(e["source"], e["value"]) for e in bear_ev if e.get("value") is not None}
        for e in bull_ev:
            if e.get("value") is not None and (e["source"], e["value"]) in bear_vals:
                flags.append({"flag": f"Both sides cite the same figure "
                                      f"({e['value']} from {e['source']}) to opposite ends",
                              "value": e["value"], "source": e["source"]})
        # Fold in the cognitive contradiction engine when its stack is available
        try:
            from src.cognitive.contradiction_engine import ContradictionEngine
            from src.cognitive.working_memory import WorkingMemory
            wm = WorkingMemory(str(ROOT))
            wm.refresh()
            report = ContradictionEngine().analyse_ticker(ticker, wm)
            if report.has_contradictions():
                for c in report.contradictions[:3]:
                    desc = getattr(c, "description", None) or str(c)
                    flags.append({"flag": desc, "value": None,
                                  "source": "cognitive contradiction_engine"})
        except Exception as e:
            logger.debug(f"cognitive contradiction engine unavailable: {e}")
        return flags

    # ── persistence ───────────────────────────────────────────────────────

    def _persist(self, transcript: dict):
        try:
            DEBATES_DIR.mkdir(parents=True, exist_ok=True)
            path = DEBATES_DIR / f"{transcript['id']}.json"
            path.write_text(json.dumps(transcript, indent=2, default=str), encoding="utf-8")
        except Exception as e:
            logger.warning(f"could not persist debate {transcript.get('id')}: {e}")


def load_debate(debate_id: str) -> dict:
    path = DEBATES_DIR / f"{debate_id}.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
