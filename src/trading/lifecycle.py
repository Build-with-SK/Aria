"""
src/trading/lifecycle.py
========================
A strategy earns the right to trade. It is never granted by assertion.

    DRAFT → RESEARCH → BACKTEST → WALK_FORWARD → PAPER → VALIDATED
          → LIVE_ELIGIBLE → LIVE                         (and SUSPENDED, aside)

Forward one stage at a time, and only when the evidence the next stage requires
is on file — a backtest with costs modelled, a purged walk-forward with
out-of-sample trades, a deflated Sharpe above zero, paper trading over enough
days, the owner's own sign-off. `GATES` below is the whole list, in code.

BACKWARDS IS ALWAYS ALLOWED
---------------------------
Demotion and suspension need no evidence and no ceremony, and a SUSPENDED
strategy that is reinstated comes back at PAPER at best, never at LIVE: whatever
went wrong invalidates the evidence that promoted it, so it earns the way back.

WHAT A STAGE PERMITS
--------------------
`may_trade()` answers the risk engine's `strategy_allowed`:

    RESEARCH mode      any registered, unsuspended strategy — evaluation only,
                       the pipeline places no orders in this mode
    PAPER mode         stage PAPER or beyond
    LIVE_AUTONOMOUS    stage LIVE, and nothing else

so reaching real money requires two separate deliberate acts: the mode, and this
strategy's own promotion to LIVE.

NO CODE HERE DECIDES ANYTHING ABOUT A STRATEGY'S MERIT. Evidence is recorded by
research runs and read by the gates; a model cannot talk its way past them,
because nothing here reads prose.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent.parent.parent
REGISTRY = ROOT / "data" / "trading" / "strategies.json"
LEDGER = ROOT / "data" / "trading" / "strategy_ledger.jsonl"


class Stage(str, Enum):
    DRAFT = "DRAFT"
    RESEARCH = "RESEARCH"
    BACKTEST = "BACKTEST"
    WALK_FORWARD = "WALK_FORWARD"
    PAPER = "PAPER"
    VALIDATED = "VALIDATED"
    LIVE_ELIGIBLE = "LIVE_ELIGIBLE"
    LIVE = "LIVE"
    SUSPENDED = "SUSPENDED"


ORDER = (Stage.DRAFT, Stage.RESEARCH, Stage.BACKTEST, Stage.WALK_FORWARD,
         Stage.PAPER, Stage.VALIDATED, Stage.LIVE_ELIGIBLE, Stage.LIVE)

#: The minimum sample sizes. Small numbers here would make every gate a
#: formality; these are the smallest that are not self-deception.
MIN_BACKTEST_TRADES = 30
MIN_OOS_TRADES = 30
MIN_WALK_FORWARD_FOLDS = 3
MIN_PAPER_TRADES = 20
MIN_PAPER_DAYS = 20
#: Realised paper slippage may exceed the modelled figure by this much before
#: the paper evidence is considered unrepresentative.
MAX_SLIPPAGE_OVERRUN_BPS = 5.0


class LifecycleRefused(RuntimeError):
    pass


def _num(d: dict, *path, default=None):
    cur = d
    for p in path:
        if not isinstance(cur, dict) or p not in cur:
            return default
        cur = cur[p]
    return cur


# ── the gates ────────────────────────────────────────────────────────────────
# target stage -> function(evidence) -> list of what is missing ("" = clear)

def _gate_research(e: dict) -> list[str]:
    missing = []
    if not str(_num(e, "hypothesis", default="") or "").strip():
        missing.append("no hypothesis recorded (what edge, and why it should exist)")
    if not _num(e, "data_sources", default=None):
        missing.append("no data sources recorded")
    return missing


def _gate_backtest(e: dict) -> list[str]:
    missing = []
    n = _num(e, "backtest", "trades", default=0) or 0
    if n < MIN_BACKTEST_TRADES:
        missing.append(f"backtest has {n} trades, needs {MIN_BACKTEST_TRADES}")
    if not _num(e, "backtest", "costs_modelled", default=False):
        missing.append("backtest did not model fees, spread and slippage")
    if _num(e, "backtest", "period", default=None) in (None, ""):
        missing.append("backtest period not recorded")
    return missing


def _gate_walk_forward(e: dict) -> list[str]:
    missing = []
    folds = _num(e, "walk_forward", "folds", default=0) or 0
    if folds < MIN_WALK_FORWARD_FOLDS:
        missing.append(f"{folds} walk-forward folds, needs {MIN_WALK_FORWARD_FOLDS}")
    if not _num(e, "walk_forward", "purged", default=False):
        missing.append("walk-forward was not purged/embargoed — leakage unaccounted for")
    oos = _num(e, "walk_forward", "oos_trades", default=0) or 0
    if oos < MIN_OOS_TRADES:
        missing.append(f"{oos} out-of-sample trades, needs {MIN_OOS_TRADES}")
    return missing


def _gate_paper(e: dict) -> list[str]:
    missing = []
    ds = _num(e, "walk_forward", "deflated_sharpe", default=None)
    if ds is None:
        missing.append("no deflated Sharpe recorded (how many variants were tried?)")
    elif ds <= 0:
        missing.append(f"deflated Sharpe {ds} is not above zero — the out-of-sample "
                       f"result does not survive the number of variants tested")
    return missing


def _gate_validated(e: dict) -> list[str]:
    missing = []
    t = _num(e, "paper", "trades", default=0) or 0
    d = _num(e, "paper", "days", default=0) or 0
    if t < MIN_PAPER_TRADES:
        missing.append(f"{t} paper trades, needs {MIN_PAPER_TRADES}")
    if d < MIN_PAPER_DAYS:
        missing.append(f"{d} days of paper trading, needs {MIN_PAPER_DAYS}")
    over = _num(e, "paper", "slippage_over_model_bps", default=None)
    if over is None:
        missing.append("paper slippage was not compared with the modelled figure")
    elif over > MAX_SLIPPAGE_OVERRUN_BPS:
        missing.append(f"paper slippage ran {over} bps over the model "
                       f"(limit {MAX_SLIPPAGE_OVERRUN_BPS}) — the cost model is wrong")
    return missing


def _gate_live_eligible(e: dict) -> list[str]:
    missing = []
    if _num(e, "paper", "sharpe", default=-1) <= 0:
        missing.append("paper Sharpe is not above zero")
    if not _num(e, "capacity", "max_notional", default=0):
        missing.append("no capacity estimate: the largest size this edge survives")
    rev = _num(e, "review", default=None)
    if not (isinstance(rev, dict) and str(rev.get("by", "")).strip()):
        missing.append("no owner review recorded (a human has to have read this)")
    return missing


def _gate_live(e: dict) -> list[str]:
    from . import modes
    if modes.effective_mode() != modes.ExecutionMode.LIVE_AUTONOMOUS:
        return [f"the system's effective execution mode is "
                f"{modes.effective_mode().value}, not LIVE_AUTONOMOUS"]
    return []


GATES: dict[Stage, Callable[[dict], list[str]]] = {
    Stage.RESEARCH: _gate_research,
    Stage.BACKTEST: _gate_backtest,
    Stage.WALK_FORWARD: _gate_walk_forward,
    Stage.PAPER: _gate_paper,
    Stage.VALIDATED: _gate_validated,
    Stage.LIVE_ELIGIBLE: _gate_live_eligible,
    Stage.LIVE: _gate_live,
}


# ── the registry ─────────────────────────────────────────────────────────────

def _load() -> dict:
    if not REGISTRY.exists():
        return {}
    try:
        raw = json.loads(REGISTRY.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception as exc:
        logger.error("strategy registry unreadable (%s)", exc)
        raise


def _save(reg: dict) -> None:
    REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    tmp = REGISTRY.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(reg, indent=1, default=str), encoding="utf-8")
    tmp.replace(REGISTRY)


def _log(strategy_id: str, event: str, **extra) -> None:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                            "strategy_id": strategy_id, "event": event, **extra},
                           default=str) + "\n")


def rank(stage: Stage | str) -> int:
    s = Stage(stage)
    return ORDER.index(s) if s in ORDER else -1


def register(strategy_id: str, *, name: str = "", hypothesis: str = "",
             asset_classes: Optional[list] = None, actor: str = "owner") -> dict:
    reg = _load()
    if strategy_id in reg:
        return reg[strategy_id]
    reg[strategy_id] = {
        "strategy_id": strategy_id, "name": name or strategy_id,
        "stage": Stage.DRAFT.value,
        "since": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "asset_classes": asset_classes or [], "evidence": {},
        "suspension": None, "history": []}
    if hypothesis:
        reg[strategy_id]["evidence"]["hypothesis"] = hypothesis
    _save(reg)
    _log(strategy_id, "registered", by=actor)
    return reg[strategy_id]


def get(strategy_id: str) -> Optional[dict]:
    return _load().get(strategy_id)


def all_strategies() -> dict:
    return _load()


def stage_of(strategy_id: str) -> Optional[Stage]:
    r = get(strategy_id)
    return Stage(r["stage"]) if r else None


def record_evidence(strategy_id: str, key: str, value, *, actor: str = "research") -> dict:
    """File a research result. Evidence is data — numbers and flags the gates
    read — never a recommendation."""
    reg = _load()
    if strategy_id not in reg:
        raise LifecycleRefused(f"{strategy_id} is not registered")
    reg[strategy_id]["evidence"][key] = value
    reg[strategy_id]["evidence_updated"] = datetime.now(timezone.utc).isoformat(
        timespec="seconds")
    _save(reg)
    _log(strategy_id, "evidence", key=key, by=actor)
    return reg[strategy_id]


def blockers(strategy_id: str, target: Stage | str) -> list[str]:
    """What stands between this strategy and `target`. Empty = it qualifies."""
    target = Stage(target)
    r = get(strategy_id)
    if r is None:
        return [f"{strategy_id} is not registered"]
    if r["stage"] == Stage.SUSPENDED.value:
        return [f"suspended: {(r.get('suspension') or {}).get('reason', 'unknown')}"]
    cur = Stage(r["stage"])
    if rank(target) != rank(cur) + 1:
        return [f"{cur.value} -> {target.value} is not a single step forward"]
    gate = GATES.get(target)
    return gate(r.get("evidence", {})) if gate else []


def promote(strategy_id: str, target: Stage | str, *, actor: str = "owner",
            note: str = "") -> dict:
    target = Stage(target)
    missing = blockers(strategy_id, target)
    if missing:
        raise LifecycleRefused(f"{strategy_id} cannot enter {target.value}: "
                               + "; ".join(missing))
    reg = _load()
    r = reg[strategy_id]
    prev = r["stage"]
    r["stage"] = target.value
    r["since"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    r["history"].append({"from": prev, "to": target.value, "by": actor,
                         "at": r["since"], "note": note})
    _save(reg)
    _log(strategy_id, "promoted", **{"from": prev, "to": target.value, "by": actor,
                                     "note": note})
    logger.warning("strategy %s promoted %s -> %s by %s", strategy_id, prev,
                   target.value, actor)
    return r


def demote(strategy_id: str, target: Stage | str, *, reason: str,
           actor: str = "owner") -> dict:
    """Backwards. No gate, no evidence, no argument."""
    target = Stage(target)
    reg = _load()
    if strategy_id not in reg:
        raise LifecycleRefused(f"{strategy_id} is not registered")
    r = reg[strategy_id]
    prev = r["stage"]
    if target is not Stage.SUSPENDED and rank(target) >= rank(prev):
        raise LifecycleRefused(f"{target.value} is not below {prev} — use promote()")
    r["stage"] = target.value
    r["since"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    r["history"].append({"from": prev, "to": target.value, "by": actor,
                         "at": r["since"], "note": reason})
    _save(reg)
    _log(strategy_id, "demoted", **{"from": prev, "to": target.value, "by": actor,
                                    "reason": reason})
    return r


def suspend(strategy_id: str, *, reason: str, actor: str = "drift-monitor") -> dict:
    reg = _load()
    if strategy_id not in reg:
        raise LifecycleRefused(f"{strategy_id} is not registered")
    r = reg[strategy_id]
    prev = r["stage"]
    if prev == Stage.SUSPENDED.value:
        return r
    r["suspension"] = {"reason": reason, "by": actor, "from_stage": prev,
                       "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    r["stage"] = Stage.SUSPENDED.value
    r["since"] = r["suspension"]["at"]
    r["history"].append({"from": prev, "to": Stage.SUSPENDED.value, "by": actor,
                         "at": r["since"], "note": reason})
    _save(reg)
    _log(strategy_id, "suspended", reason=reason, by=actor, from_stage=prev)
    logger.warning("strategy %s SUSPENDED (%s): %s", strategy_id, actor, reason)
    return r


def reinstate(strategy_id: str, *, actor: str = "owner", note: str = "") -> dict:
    """Back from suspension — at PAPER at best. Whatever caused the suspension
    invalidated the evidence that promoted it, so LIVE has to be earned again."""
    reg = _load()
    r = reg.get(strategy_id)
    if r is None:
        raise LifecycleRefused(f"{strategy_id} is not registered")
    if r["stage"] != Stage.SUSPENDED.value:
        raise LifecycleRefused(f"{strategy_id} is not suspended")
    was = (r.get("suspension") or {}).get("from_stage", Stage.RESEARCH.value)
    back = Stage.PAPER if rank(was) >= rank(Stage.PAPER) else Stage(was)
    r["stage"] = back.value
    r["since"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    r["suspension"] = None
    r["history"].append({"from": Stage.SUSPENDED.value, "to": back.value, "by": actor,
                         "at": r["since"], "note": note or "reinstated"})
    _save(reg)
    _log(strategy_id, "reinstated", to=back.value, by=actor, previous_stage=was)
    return r


# ── permissions ──────────────────────────────────────────────────────────────

def any_at_or_beyond(stage: Stage | str) -> bool:
    want = rank(stage)
    return any(rank(r.get("stage", Stage.DRAFT.value)) >= want
               for r in _load().values())


def may_trade(strategy_id: str, mode=None) -> tuple[bool, str]:
    """The risk engine's `strategy_allowed`. Both halves must agree: the
    system's mode, and this strategy's own stage."""
    from . import modes
    mode = modes.ExecutionMode(mode) if mode else modes.effective_mode()
    r = get(strategy_id)
    if r is None:
        return False, (f"{strategy_id} is not in the strategy registry; register it "
                       f"before it may produce orders")
    stage = Stage(r["stage"])
    if stage is Stage.SUSPENDED:
        return False, f"suspended: {(r.get('suspension') or {}).get('reason', '')}"
    if mode is modes.ExecutionMode.RESEARCH:
        return True, f"RESEARCH: evaluation only at stage {stage.value}; no orders"
    if mode is modes.ExecutionMode.PAPER:
        if rank(stage) >= rank(Stage.PAPER):
            return True, f"stage {stage.value} may trade on paper"
        return False, (f"stage {stage.value} has not reached PAPER; it may be "
                       f"researched and backtested, not traded")
    if stage is Stage.LIVE:
        return True, "stage LIVE"
    return False, (f"stage {stage.value} is not LIVE; only a strategy promoted to "
                   f"LIVE may trade real money")


def summary() -> dict:
    reg = _load()
    by_stage: dict[str, list] = {}
    for sid, r in reg.items():
        by_stage.setdefault(r.get("stage", "DRAFT"), []).append(sid)
    return {"count": len(reg), "by_stage": by_stage,
            "live": by_stage.get(Stage.LIVE.value, []),
            "live_eligible_or_beyond": any_at_or_beyond(Stage.LIVE_ELIGIBLE),
            "strategies": {sid: {"stage": r["stage"], "since": r.get("since"),
                                 "next_blockers": _next_blockers(sid, r)}
                           for sid, r in reg.items()}}


def _next_blockers(sid: str, r: dict) -> list[str]:
    cur = Stage(r["stage"])
    if cur is Stage.SUSPENDED or rank(cur) + 1 >= len(ORDER):
        return []
    return blockers(sid, ORDER[rank(cur) + 1])
