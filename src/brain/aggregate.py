"""
src/brain/aggregate.py
======================
ONE brain state — the single thing the Brain workspace reads.

THE PROBLEM THIS SOLVES
-----------------------
The interface presented ARIA as several competing intelligences: "ARIA", "Live
Mind", "Cognitive Brain", a "Live Thought Stream", a "Memory Browser" and a
chat, each fetching its own state from its own endpoint and each looking like a
peer of the others. A user could reasonably ask "is Live Mind thinking, or is
the Brain?" — and the interface offered no way to answer, because nothing in it
asserted they were the same thing.

They are the same thing. There is one daemon, one memory store, one world
model. What existed was several VIEWS of it that had drifted into looking like
several minds.

So this module composes the one state, once:

    status      idle / thinking / analysing / waiting / error — ONE status
    regime      the market regime, with its evidence and coverage
    cognition   the current reasoning phase, as an ACTIVITY summary
    memory      what the brain holds and can recall
    world       the market/macro/portfolio context it reasons over
    knowledge   the Obsidian vault index it can draw on
    capability  which surfaces are actually available right now

IT AGGREGATES, IT DOES NOT COMPUTE
----------------------------------
Every field comes from a subsystem that already owns it — `brain_daemon`,
`src.core.world`, `src.macro.regime`, `src.brain.vault`. Nothing here derives a
new claim about the market, because a second place that computes the regime is
a second regime.

ACTIVITY, NOT CHAIN-OF-THOUGHT
------------------------------
`cognition` reports WHAT ARIA IS DOING — "reviewing the macro regime",
"comparing technical and ML evidence" — and the conclusion each phase reached.
It does not stream the model's internal reasoning text. That distinction is
kept in `phase_activity()` below, deliberately and in one place.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional

logger = logging.getLogger(__name__)

IDLE = "IDLE"
THINKING = "THINKING"
ANALYSING = "ANALYSING"
WAITING = "WAITING"
ERROR = "ERROR"

#: The reasoning loop, and what each phase MEANS in plain language.
#:
#: This mapping is the safety boundary between "what ARIA is doing" and "what
#: ARIA is privately reasoning". The UI renders this text; it does not render
#: the model's own narration. A user is entitled to know the system is weighing
#: risk without being handed the intermediate tokens it used to do so.
PHASES: dict[str, dict] = {
    "ORIENT":  {"activity": "Reviewing the macro regime and market state",
                "order": 1},
    "FOCUS":   {"activity": "Choosing what deserves attention right now",
                "order": 2},
    "RECALL":  {"activity": "Checking historical analogues and stored memory",
                "order": 3},
    "ANALYSE": {"activity": "Comparing technical, fundamental and ML evidence",
                "order": 4},
    "DECIDE":  {"activity": "Evaluating risk and forming a position",
                "order": 5},
    "REFLECT": {"activity": "Reviewing what it got right and wrong",
                "order": 6},
}


def phase_activity(step: Optional[str]) -> dict:
    """A safe activity summary for a reasoning step.

    The daemon names its steps `ORIENT`, `RECALL`, `ANALYSE_ARKK` — phase plus,
    sometimes, the subject. Both halves are ACTIVITY: "analysing ARKK" says what
    ARIA is doing, not what it is privately concluding, and hiding the subject
    would make the stream less useful without making it any safer.

    An unknown phase gets a generic label rather than the raw step name, so a
    phase added to the daemon later cannot leak an internal identifier into the
    UI by default.
    """
    raw = (step or "").strip()
    if not raw:
        return {"phase": None, "subject": None,
                "activity": "Starting a reasoning cycle", "order": 0,
                "known": False}

    base, _, subject = raw.upper().partition("_")
    spec = PHASES.get(base)
    if not spec:
        return {"phase": None, "subject": None, "activity": "Working",
                "order": 0, "known": False}

    activity = spec["activity"]
    # Only a plausible ticker is echoed back. Anything else is dropped rather
    # than rendered, so an internal suffix cannot become UI text.
    looks_like_a_ticker = (
        subject
        and len(subject) <= 12
        and subject.replace(".", "").replace("-", "").isalnum()
    )
    if looks_like_a_ticker:
        activity = f"{activity} — {subject}"
    return {"phase": base, "subject": subject or None, "activity": activity,
            "order": spec["order"], "known": True}


def _daemon() -> dict:
    try:
        from src.brain.brain_daemon import peek_brain
        b = peek_brain()
        if b is None:
            return {"running": False, "thinking": False, "cycle_count": 0,
                    "last_cycle_at": None, "memory_count": None,
                    "model": None, "step": None, "present": False}
        s = b.status() or {}
        return {
            "present": True,
            "running": bool(s.get("running")),
            "thinking": bool(s.get("thinking")),
            "cycle_count": int(s.get("cycle_count") or 0),
            "last_cycle_at": s.get("last_cycle_at"),
            "memory_count": s.get("memory_count"),
            "model": s.get("model"),
            "interval_minutes": s.get("interval_minutes"),
            "step": s.get("step") or s.get("current_step"),
        }
    except Exception as e:
        logger.debug("brain aggregate: daemon peek failed: %s", e)
        return {"present": False, "running": False, "thinking": False,
                "cycle_count": 0, "last_cycle_at": None,
                "memory_count": None, "model": None, "step": None,
                "error": str(e)}


def _status(daemon: dict, llm_up: bool) -> tuple[str, str]:
    """ONE status for ONE intelligence, and the reason for it.

    Previously the rail, the brain page and the live view each derived their own
    liveness from a different endpoint, so they could disagree on screen. This
    is the only place the question is answered.
    """
    if daemon.get("error"):
        return ERROR, f"the brain could not be read: {daemon['error']}"
    if not llm_up:
        return WAITING, ("the local model server is not reachable, so no "
                         "reasoning cycle can start")
    if not daemon.get("running"):
        return IDLE, "the reasoning loop is not running"
    if daemon.get("thinking"):
        phase = phase_activity(daemon.get("step"))
        if phase["phase"] in ("ANALYSE", "DECIDE"):
            return ANALYSING, phase["activity"]
        return THINKING, phase["activity"]
    if daemon.get("running") and daemon.get("step"):
        return IDLE, (f"between cycles — last step was "
                      f"{phase_activity(daemon['step'])['activity'].lower()}")
    return IDLE, "the loop is running and between cycles"


def _cognition() -> dict:
    """The current cycle as an ACTIVITY stream — never raw chain-of-thought."""
    import json
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent.parent
    try:
        state = json.loads(
            (root / "data" / "brain_state.json").read_text(encoding="utf-8"))
    except Exception as e:
        return {"steps": [], "cycle_id": None,
                "note": f"no reasoning cycle recorded yet ({e})"}

    steps = []
    for s in (state.get("thinking_steps") or []):
        info = phase_activity(s.get("step"))
        steps.append({
            "phase": info["phase"],
            # What ARIA is DOING. The model's own narration stays server-side.
            "activity": info["activity"],
            # The conclusion IS shareable — it is the output of the step, not
            # the working. A user who cannot see conclusions cannot audit the
            # system at all.
            "conclusion": s.get("conclusion"),
            "order": info["order"],
        })
    return {
        "cycle_id": state.get("cycle_id"),
        "cycle_count": state.get("cycle_count"),
        "at": state.get("last_cycle_at") or state.get("at"),
        "steps": steps,
        "decisions": state.get("decisions") or [],
        "trades_queued": state.get("trades_queued"),
        "note": ("Activity summaries, not internal reasoning. Each line says "
                 "what ARIA was doing and what it concluded."),
    }


def _memory() -> dict:
    out: dict[str, Any] = {"count": None, "recent": [], "available": False}
    try:
        from src.brain.brain_daemon import peek_brain
        b = peek_brain()
        if b is not None:
            s = b.status() or {}
            out["count"] = s.get("memory_count")
            out["available"] = True
    except Exception as e:
        out["error"] = str(e)
    return out


def _knowledge() -> dict:
    """The Obsidian vault, as a capability rather than a second intelligence."""
    try:
        from src.brain.vault import get_vault_path
        path = get_vault_path()
        return {"available": bool(path and path.exists()),
                "indexed": True,
                "note": ("Vault knowledge is injected into the brain's RECALL "
                         "step and into chat context. It is owner-only.")}
    except Exception as e:
        return {"available": False, "indexed": False, "error": str(e)}


def state(*, include_world: bool = True) -> dict:
    """The single Brain state. This is what `/api/brain` returns."""
    daemon = _daemon()

    llm_up = False
    try:
        from backend.main import _ollama_available
        llm_up = bool(_ollama_available())
    except Exception:
        try:
            import urllib.request
            with urllib.request.urlopen("http://localhost:11434/api/tags",
                                        timeout=1.5) as r:
                llm_up = r.status == 200
        except Exception:
            llm_up = False

    status, why = _status(daemon, llm_up)

    regime: dict = {}
    try:
        from src.macro import regime as R
        regime = R.current()
    except Exception as e:
        logger.debug("brain aggregate: regime unavailable: %s", e)
        regime = {"regime": None, "error": str(e)}

    world: dict = {}
    if include_world:
        try:
            from src.core import world as W
            world = W.snapshot()
        except Exception as e:
            logger.debug("brain aggregate: world unavailable: %s", e)
            world = {"error": str(e)}

    memory = _memory()
    cognition = _cognition()

    return {
        "at": datetime.now().isoformat(timespec="seconds"),
        # ONE intelligence. The name is stated here so no surface has to invent
        # one, and so nothing downstream can present a second.
        "identity": {
            "name": "ARIA",
            "surface": "COGNITIVE BRAIN",
            "note": ("There is one intelligence. Chat, memory, the reasoning "
                     "loop and vault knowledge are all facets of it, not "
                     "separate minds."),
        },
        "status": status,
        "status_reason": why,
        "daemon": daemon,
        "model": daemon.get("model"),
        "llm_available": llm_up,
        "cycle": {
            "count": daemon.get("cycle_count"),
            "last_at": daemon.get("last_cycle_at"),
            "interval_minutes": daemon.get("interval_minutes"),
        },
        "regime": {
            "label": regime.get("regime"),
            "meaning": regime.get("meaning"),
            "confidence": regime.get("confidence"),
            "coverage": regime.get("coverage"),
            "as_of": regime.get("as_of"),
            "stale": regime.get("stale"),
            "evidence": regime.get("evidence"),
            "caveats": regime.get("caveats"),
        },
        "vix": ((regime.get("inputs") or {}).get("vix") or {}).get("value"),
        "memory": memory,
        "knowledge": _knowledge(),
        "cognition": cognition,
        "world": world,
        # What this brain can actually do right now, so the UI disables rather
        # than offering a button that will fail.
        "capability": {
            "chat": True,
            "local_reasoning": llm_up,
            "memory_search": memory.get("available", False),
            "vault": _knowledge().get("available", False),
        },
    }
