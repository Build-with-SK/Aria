"""
src/inference/model_profiles.py
===============================
Per-model limits for a local model that cannot be run like the 7B default.

NONE SHIPS. `BUILTIN` is empty, so every model is sent exactly what it was
sent before this file existed. The owner opts a model in through the
ARIA_MODEL_PROFILES environment variable, a JSON object such as

    {"big-model": {"num_ctx": 4096, "think": false, "timeout_s": 180,
                   "max_predict": 768, "max_waiting": 2, "wait_timeout_s": 90,
                   "refuse_overflow": true, "auto_pick": false}}

WHY A PROFILE. qwen2.5:7b fits the RTX 4060's 8 GB with an 8192 window and
answers at ~40 tok/s, so one set of options serves every call. A model that
does not fit like that (a 27B at 2-bit was measured 2026-11-02: 46 of 64
layers on the GPU at 4096, 6.4-7.0 tok/s) needs its window treated as a VRAM
decision, a bounded reply, a bounded wait, and a prompt that does not fit
refused rather than silently truncated by Ollama.

  num_ctx           its window, sent on every request for it
  think             sent as is (thinking kept out of the answer)
  timeout_s         cap on any one call, streamed or not; a timeout is then
                    ModelTimeout, not retried
  max_predict       cap on the reply
  max_waiting       callers allowed to queue behind the one running (ModelBusy
                    beyond that); wait_timeout_s caps each wait
  refuse_overflow   a prompt that cannot fit is refused (ContextOverflow)
  auto_pick false   never added to a tier by discovery, only used when named

SAME NUMBERS AS SENTINEL. SENTINEL (Documents\\atlas, mind/router.py
`model_profiles`) talks to the same Ollama, which reloads a resident model
whenever a request asks for different load options (7.6 s measured for
qwen2.5 between 8192 and 16384). A model both projects use needs the same
num_ctx in both.

NO num_gpu. Pinning the layer count was measured (Ollama 0.40.13) to make
Ollama load the model NEXT TO the ones already resident instead of evicting
them: VRAM oversubscribed, 8-15 MB of RAM left. Automatic placement evicts
correctly.

Everything here is about how a request is shaped. Which provider may answer
is src/inference/policy.py's business, and nothing in this file widens it.
"""
from __future__ import annotations

import json
import os
import threading

from src.inference.errors import ContextOverflow, ModelBusy

PROFILES_ENV = "ARIA_MODEL_PROFILES"

#: Profiles shipped with the code: none. See the module docstring.
BUILTIN: dict[str, dict] = {}

#: Characters per token for the overflow backstop. Below English's ~4 so the
#: estimate errs towards "does not fit"; src/inference/context.py budgets the
#: chat prompt properly before this is ever reached.
OVERFLOW_CHARS_PER_TOKEN = 3.5


def _base(model: str) -> str:
    m = (model or "").strip()
    return m[:-len(":latest")] if m.endswith(":latest") else m


def _owner_overrides() -> dict:
    """ARIA_MODEL_PROFILES: a JSON object {model: {key: value}} merged over
    any built-in profile. Unreadable JSON is ignored, which is today's
    behaviour, not a half-applied profile."""
    raw = os.environ.get(PROFILES_ENV, "").strip()
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


def describe() -> dict:
    """For /api/inference status: which models are profiled, and how."""
    names = set(BUILTIN) | {_base(k) for k in _owner_overrides()}
    return {"profiles_env": PROFILES_ENV,
            "profiles": {name: profile(name) for name in sorted(names)}}


def reset_for_tests() -> None:
    with _gates_lock:
        _gates.clear()
