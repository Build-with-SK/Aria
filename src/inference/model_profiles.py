"""
src/inference/model_profiles.py
===============================
Per-model limits for local models that cannot be run like the 7B default —
and the one explicit switch that puts such a model in ARIA's reasoning tiers.

WHY A PROFILE. qwen2.5:7b fits the RTX 4060's 8 GB with an 8192 window and
answers at ~40 tok/s, so one set of options serves every call. A 27B model at
2-bit (saluki-27b-iq2, 7.36 GiB of weights) does not: measured on this laptop
it keeps 46 of 64 layers on the GPU at a 4096 window, generates 4.3 tok/s and
leaves ~300 MB of RAM free. For such a model the window is a VRAM decision,
the reply must be bounded, a wait for the model must be bounded, and a prompt
that does not fit must be refused rather than silently truncated.

SAME NUMBERS AS SENTINEL. SENTINEL (Documents\\atlas, mind/router.py
BUILTIN_PROFILES) talks to the same Ollama. Ollama reloads a resident model
whenever a request asks for different load options (num_ctx), so both projects
must send the same ones — 7.6 s per switch was measured for qwen2.5 between
8192 and 16384. Change one, change the other.

NO num_gpu. Pinning the layer count was measured to make Ollama load the model
NEXT TO the ones already resident instead of evicting them: VRAM
oversubscribed, 8-15 MB of RAM left. Automatic placement evicts correctly.

THE SWITCH. `ARIA_REASONING_MODEL=<model>` puts that local model first in the
STANDARD and DEEP tiers (chat, chat stream, the teacher); FAST stays on
gemma3:4b because the reflex lane has a 3 s budget. Unset, nothing changes.
The model it replaces stays second in the tier, where it answers only under
ARIA_DEGRADE=disclose and is then labelled degraded. The desk debate and the
brain reasoner name their model explicitly (data/desk_config.json
`llm_model`) and are NOT moved by the switch.

Everything here is about how a request is shaped. Which provider may answer
is src/inference/policy.py's business, and nothing in this file widens it.
"""
from __future__ import annotations

import json
import os
import threading

from src.inference.errors import ContextOverflow, ModelBusy

SWITCH_ENV = "ARIA_REASONING_MODEL"
SWITCHED_TIERS = ("STANDARD", "DEEP")

#: Measured 2026-11-01/02 on the owner's RTX 4060 Laptop (8 GB) / 16 GB RAM,
#: Ollama 0.40.13. docs/collab/SALUKI_TRIAL.md has the numbers.
BUILTIN: dict[str, dict] = {
    "saluki-27b-iq2": {
        "num_ctx": 4096,          # 46/64 layers on GPU; 8192 spills further
        "think": False,           # thinking is on by default in its template
        "timeout_s": 180,         # cap on any one call, streamed or not
        "max_predict": 768,       # ~180 s at the measured 4.3 tok/s
        "max_waiting": 2,         # callers allowed to queue behind the one running
        "wait_timeout_s": 90,     # longest a caller waits for the model
        "refuse_overflow": True,  # a prompt that cannot fit is refused, not cut
        "auto_pick": False,       # never chosen by discovery, only by name
    },
}

#: Characters per token for the overflow backstop. Below English's ~4 so the
#: estimate errs towards "does not fit"; src/inference/context.py budgets the
#: chat prompt properly before this is ever reached.
OVERFLOW_CHARS_PER_TOKEN = 3.5


def _base(model: str) -> str:
    m = (model or "").strip()
    return m[:-len(":latest")] if m.endswith(":latest") else m


def _owner_overrides() -> dict:
    """ARIA_MODEL_PROFILES: a JSON object {model: {key: value}} merged over
    the built-in profile — for tuning without a code change."""
    raw = os.environ.get("ARIA_MODEL_PROFILES", "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}


def profile(model: str) -> dict:
    """The profile for `model`, or {} for an ordinary model (unchanged path)."""
    name = _base(model)
    out = dict(BUILTIN.get(name, {}))
    for key, extra in _owner_overrides().items():
        if _base(key) == name and isinstance(extra, dict):
            out.update(extra)
    return out


def num_ctx_for(model: str, default: int) -> int:
    return int(profile(model).get("num_ctx") or default)


def reply_tokens_for(model: str, requested: int) -> int:
    cap = profile(model).get("max_predict")
    return min(int(requested), int(cap)) if cap else int(requested)


def timeout_for(model: str, requested: float) -> float:
    cap = profile(model).get("timeout_s")
    return min(float(requested), float(cap)) if cap else float(requested)


def stream_deadline_for(model: str) -> float | None:
    """Total seconds a STREAMED reply may take. A socket timeout only bounds
    the gap between pieces, so without this a slow model streams for as long
    as num_predict lets it."""
    cap = profile(model).get("timeout_s")
    return float(cap) if cap else None


def think_for(model: str):
    """The `think` value to send, or None to send nothing."""
    prof = profile(model)
    return bool(prof["think"]) if "think" in prof else None


def check_fits(model: str, system: str, messages: list[dict], reply_tokens: int,
               num_ctx: int) -> None:
    if not profile(model).get("refuse_overflow"):
        return
    chars = len(system or "") + sum(len(str(m.get("content") or "")) for m in messages)
    need = int(chars / OVERFLOW_CHARS_PER_TOKEN) + int(reply_tokens)
    if need > num_ctx:
        raise ContextOverflow(
            f"prompt (~{need - reply_tokens} tokens) plus a {reply_tokens}-token reply does "
            f"not fit {_base(model)}'s context window ({num_ctx}); refused rather than "
            f"truncated", "ollama", model)


def auto_pick_allowed(model: str) -> bool:
    return profile(model).get("auto_pick", True) is not False


# ── bounded queue ────────────────────────────────────────────────────────

class _Gate:
    """One request at a time to a profiled model, at most `max_waiting`
    callers queued behind it, each waiting at most `wait_timeout_s`. Ollama
    queues requests itself, but without a bound: a burst of desk ticks behind
    a 3-minute answer would sit there for a quarter of an hour."""

    def __init__(self, max_waiting: int, wait_timeout_s: float):
        self.sem = threading.Semaphore(1)
        self.lock = threading.Lock()
        self.waiting = 0
        self.max_waiting = int(max_waiting)
        self.wait_timeout_s = float(wait_timeout_s)

    def __enter__(self):
        if self.sem.acquire(blocking=False):
            return self
        with self.lock:
            if self.waiting >= self.max_waiting:
                raise ModelBusy(f"model busy: {self.waiting} request(s) already waiting")
            self.waiting += 1
        try:
            if not self.sem.acquire(timeout=self.wait_timeout_s):
                raise ModelBusy(f"model busy: waited {self.wait_timeout_s:.0f}s")
        finally:
            with self.lock:
                self.waiting -= 1
        return self

    def __exit__(self, *exc):
        self.sem.release()
        return False


_gates: dict[str, _Gate] = {}
_gates_lock = threading.Lock()


class _NoGate:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def gate(model: str):
    prof = profile(model)
    if prof.get("max_waiting") is None:
        return _NoGate()
    name = _base(model)
    with _gates_lock:
        g = _gates.get(name)
        if g is None:
            g = _gates[name] = _Gate(prof.get("max_waiting", 0),
                                     prof.get("wait_timeout_s", 60))
        return g


# ── the switch ───────────────────────────────────────────────────────────

def switched_model() -> str:
    return os.environ.get(SWITCH_ENV, "").strip()


def apply_switch(tiers: dict) -> dict:
    """Put ARIA_REASONING_MODEL first in STANDARD and DEEP; the previous
    first choice stays right behind it. A no-op when the variable is unset."""
    model = switched_model()
    if not model:
        return tiers
    for tier in SWITCHED_TIERS:
        cands = [list(c) for c in tiers.get(tier) or []
                 if not (c[0] == "ollama" and _base(c[1]) == _base(model))]
        tiers[tier] = [["ollama", model]] + cands
    return tiers


def describe() -> dict:
    model = switched_model()
    return {"switch_env": SWITCH_ENV, "reasoning_model": model or None,
            "switched_tiers": list(SWITCHED_TIERS) if model else [],
            "profiles": {name: profile(name) for name in BUILTIN}}


def reset_for_tests() -> None:
    with _gates_lock:
        _gates.clear()

