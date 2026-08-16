"""
src/brain/self_state.py
=======================
THE SENSE SHE ACTUALLY NEEDS (docs/ARIA_NEXT_SESSION.md step 7, last bullet).

"Sense her own state — stale data, unreachable brain, stalled loop — and say
so unprompted. This is the most useful 'sense' on the list."

The failure mode this addresses is specific and has already happened in this
project: everything answers normally while nothing is accumulating. The
backend serves requests, the UI renders, the chat replies — and the research
loop has not fired for a week, so the track record is not growing and nobody
notices, because nothing anywhere is red.

Four senses, each answering "is this part of me actually working right now":

  brain   — is the model that does her reasoning reachable, and is it the one
            she is supposed to be using?
  data    — how old is the market state she would answer from?
  loop    — is the research flywheel turning? (predict daily, resolve hourly)
  record  — how many calls have resolved? Zero is the honest number and it
            only moves with elapsed time (invariant 5).

Every check degrades to "unknown" rather than to "fine". A health report that
says OK when it could not run is worse than no report: it converts an outage
into a false assurance, which is the exact shape of the thing being detected.

`speak()` turns the report into one sentence she can volunteer. It is silent
when everything is working — an assistant that announces its own health every
turn has taught the owner to ignore it by the third time.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
DATA = ROOT / "data"

OK, DEGRADED, DOWN, UNKNOWN = "ok", "degraded", "down", "unknown"

#: Market state older than this is not what she should answer from without
#: saying so. One trading day plus a margin.
SIGNALS_STALE_HOURS = 30


@dataclass
class Sense:
    name: str
    state: str = UNKNOWN
    detail: str = ""
    facts: dict = field(default_factory=dict)

    @property
    def healthy(self) -> bool:
        return self.state == OK

    def to_dict(self) -> dict:
        return {"name": self.name, "state": self.state, "detail": self.detail,
                **self.facts}


def _age_hours(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        return round((datetime.now() - datetime.fromisoformat(str(iso)))
                     .total_seconds() / 3600, 2)
    except (ValueError, TypeError):
        return None


def sense_brain() -> Sense:
    """Reachable, and the model she is supposed to be using?"""
    from src.inference import policy
    from src.inference.base import ollama_base
    from src.inference.router import load_tier_config

    s = Sense("brain")
    s.facts["policy"] = policy.describe()
    base = ollama_base()
    s.facts["host"] = base

    try:
        tiers = load_tier_config()
        primary = (tiers.get("DEEP") or [[None, None]])[0]
        s.facts["primary"] = f"{primary[0]}/{primary[1]}" if primary[0] else None
    except Exception as e:
        s.facts["primary"] = None
        s.facts["tier_error"] = str(e)

    try:
        with urllib.request.urlopen(f"{base}/api/tags", timeout=4) as r:
            models = [m["name"] for m in json.loads(r.read()).get("models", [])]
    except (urllib.error.URLError, OSError, ValueError, KeyError) as e:
        s.state = DOWN
        s.detail = (f"my reasoning model is unreachable at {base} ({e}). I will "
                    f"abstain rather than answer from something else.")
        return s

    s.facts["models_present"] = models
    wanted = (s.facts.get("primary") or "/").split("/", 1)[-1]
    if wanted and wanted not in models:
        s.state = DEGRADED
        s.detail = (f"the model I am routed to ({wanted}) is not installed on "
                    f"{base}; present: {', '.join(models) or 'none'}")
        return s
    s.state = OK
    s.detail = f"reasoning on {wanted} at {base}"
    return s


def sense_data() -> Sense:
    """How old is the market state she would answer from?"""
    s = Sense("data")
    path = DATA / "signals.json"
    if not path.exists():
        s.state = UNKNOWN
        s.detail = "no signals file has ever been written"
        return s
    age = round((datetime.now().timestamp() - path.stat().st_mtime) / 3600, 2)
    s.facts["signals_age_hours"] = age
    try:
        s.facts["signals_path"] = str(path.relative_to(ROOT))
    except ValueError:      # a data directory outside the repo is legitimate
        s.facts["signals_path"] = str(path)
    try:
        s.facts["tickers"] = len(json.loads(path.read_text(encoding="utf-8")))
    except (ValueError, OSError):
        s.state = DEGRADED
        s.detail = "the signals file is present but unreadable"
        return s
    if age > SIGNALS_STALE_HOURS:
        s.state = DEGRADED
        s.detail = (f"my market state is {age:.0f} hours old — anything I say "
                    f"about live prices is that old too")
        return s
    s.state = OK
    s.detail = f"market state {age:.1f}h old across {s.facts['tickers']} names"
    return s


def sense_loop() -> Sense:
    """Is the research flywheel turning? This is the one that fails silently."""
    s = Sense("loop")
    try:
        from src.v5 import loop as v5_loop
        health = v5_loop.health()
    except Exception as e:
        s.state = UNKNOWN
        s.detail = f"could not read the loop heartbeat: {e}"
        return s

    predict, resolve = health.get("predict") or {}, health.get("resolve") or {}
    s.facts["predict_hours_ago"] = predict.get("hours_ago")
    s.facts["resolve_hours_ago"] = resolve.get("hours_ago")

    if predict.get("last_run") is None and resolve.get("last_run") is None:
        s.state = DOWN
        s.detail = ("the research loop has never run on this machine, so no "
                    "track record can accumulate — the backend has to stay up")
        return s
    if health.get("healthy"):
        s.state = OK
        s.detail = (f"predicting {predict.get('hours_ago')}h ago, "
                    f"resolving {resolve.get('hours_ago')}h ago")
        return s
    s.state = DEGRADED
    stalled = [leg for leg, d in (("predict", predict), ("resolve", resolve))
               if not d.get("ok")]
    s.detail = (f"the {' and '.join(stalled)} leg of the research loop has "
                f"stalled — I am still answering, but I have stopped learning")
    return s


def sense_record() -> Sense:
    """Zero resolved calls is the honest number (invariant 5)."""
    s = Sense("record")
    try:
        from src.v5 import learning
        preds = learning.load_predictions()
    except Exception as e:
        s.state = UNKNOWN
        s.detail = f"could not read the prediction log: {e}"
        return s
    resolved = [p for p in preds if p.get("resolved")]
    s.facts["logged"] = len(preds)
    s.facts["resolved"] = len(resolved)
    s.state = OK          # a thin record is not a fault; it is a fact
    if not resolved:
        s.detail = (f"{len(preds)} predictions logged, none resolved yet — I "
                    f"have no track record to quote, and I will not imply one")
    else:
        s.detail = f"{len(resolved)} of {len(preds)} predictions have resolved"
    return s


SENSES = (sense_brain, sense_data, sense_loop, sense_record)


def report() -> dict:
    senses = []
    for fn in SENSES:
        try:
            senses.append(fn())
        except Exception as e:                       # a sense must not crash
            senses.append(Sense(fn.__name__.replace("sense_", ""), UNKNOWN,
                                f"this check itself failed: {e}"))
    states = [s.state for s in senses]
    if DOWN in states:
        overall = DOWN
    elif DEGRADED in states:
        overall = DEGRADED
    elif UNKNOWN in states:
        overall = UNKNOWN
    else:
        overall = OK
    return {
        "at": datetime.now().isoformat(timespec="seconds"),
        "state": overall,
        "senses": {s.name: s.to_dict() for s in senses},
        "speak": speak(senses),
    }


# ── what she may say about herself ───────────────────────────────────────────

_cache: dict = {"at": 0.0, "report": None}

#: A chat turn must not wait on a health sweep. The senses touch the network
#: and the disk; a minute-old answer to "are you learning" is exactly as true
#: as a fresh one.
CACHE_SECONDS = 60


def report_cached(max_age_s: float = CACHE_SECONDS) -> dict:
    import time
    now = time.time()
    if _cache["report"] is not None and (now - _cache["at"]) < max_age_s:
        return _cache["report"]
    _cache["report"], _cache["at"] = report(), now
    return _cache["report"]


def learning_state() -> dict:
    """Is she learning? The honest answer has three parts and they differ.

    Asked this in her own UI she replied "Yes, I am continuously learning and
    updating my models based on the latest data" — fluent, reassuring, and
    false in the part that matters. No fine-tune has ever run. That is the
    prose outrunning the evidence, which is the one failure this project
    treats as worse than being wrong.

    The truthful answer is layered, and flattening it in either direction
    misleads: "no" would deny the memory and the lessons that genuinely do
    accumulate, "yes" claims a model that improves from outcomes when not one
    outcome has resolved.
    """
    out = {"weights": {}, "memory": {}, "track_record": {}}

    try:
        from src.training import adapters
        status = adapters.status()
        out["weights"] = {
            "fine_tuned": not status["serving_is_base_model"],
            "serving": status["serving"] or status.get("base_model") or "the base model",
            "adapters_registered": len(status.get("registered") or []),
        }
    except Exception as e:
        out["weights"] = {"fine_tuned": False, "unknown": str(e)}

    try:
        from src.v5 import learning
        preds = learning.load_predictions()
        resolved = [p for p in preds if p.get("resolved")]
        out["track_record"] = {"logged": len(preds), "resolved": len(resolved)}
    except Exception:
        out["track_record"] = {"logged": None, "resolved": None}

    lessons = None
    try:
        from src.desk.teacher import read_lessons
        lessons = len(read_lessons())
    except Exception:
        lessons = None
    out["memory"] = {"lessons": lessons, "accumulating": True}
    return out


def for_prompt(max_age_s: float = CACHE_SECONDS) -> str:
    """The block that goes into her system prompt, in her own voice.

    Written as facts plus one instruction. Facts alone get smoothed away by a
    model that has read a million cheerful assistants; the instruction is what
    stops "16 logged, 0 resolved" being narrated as "I'm always improving".
    """
    state = report_cached(max_age_s)
    senses = state.get("senses") or {}
    learning = learning_state()
    weights = learning.get("weights") or {}
    record = learning.get("track_record") or {}

    lines = ["## WHAT IS ACTUALLY TRUE OF YOU RIGHT NOW",
             "These are measured facts about this machine, not background "
             "colour. Where they contradict what an assistant would normally "
             "say, they win."]

    resolved, logged = record.get("resolved"), record.get("logged")
    if resolved is not None:
        lines.append(
            f"- Track record: {logged} predictions logged, {resolved} resolved. "
            + ("You have NO measured track record and cannot claim any skill, "
               "accuracy or improvement over time."
               if not resolved else
               "Quote these numbers rather than an impression of them."))

    if weights.get("fine_tuned"):
        lines.append(f"- Your weights: fine-tuned adapter '{weights.get('serving')}' "
                     f"is serving.")
    else:
        lines.append(
            "- Your weights: NO fine-tune has ever run. You are the base model "
            "as it shipped. You do not learn from conversations, you are not "
            "training, and your model is not updating. Saying otherwise is "
            "false — it is the specific false claim you have made before.")

    lesson_count = (learning.get("memory") or {}).get("lessons")
    lines.append(
        "- What you DO have, and should say so rather than denying it: a "
        "cognitive cycle every 15 minutes writing into long-term memory"
        + (f", {lesson_count} lessons graded from closed trades" if lesson_count
           else ", a teacher that grades closed trades into lessons")
        + ", an eye reading sources, a novelty sense building a history of "
        "normal days, and a gate that will adopt a fine-tuned adapter once one "
        "beats the base model on held-out data. These MECHANISMS exist and run; "
        "they have simply not produced a result yet. Saying you have no way to "
        "improve is as wrong as saying you are already improving — the true "
        "answer is that the machinery is running and the evidence is still "
        "empty.")

    for name in ("brain", "data", "loop"):
        sense = senses.get(name) or {}
        if sense.get("state") in (DEGRADED, DOWN, UNKNOWN) and sense.get("detail"):
            lines.append(f"- {name.capitalize()}: {sense['detail']}")

    lines.append(
        "If you are asked what you are, how well you perform, or whether you "
        "are learning or improving: answer from the numbers above. If they are "
        "thin, say they are thin. Never describe a capability you do not have "
        "in order to sound useful.")
    return "\n".join(lines)


def speak(senses: list[Sense]) -> str:
    """One sentence she can volunteer, or nothing at all.

    Silent when everything works. An assistant that reports its own health
    every turn has trained the owner to skip the line by the third time, and
    then it is not a sense, it is furniture.
    """
    broken = [s for s in senses if s.state in (DOWN, DEGRADED)]
    if not broken:
        return ""
    lead = "Before I answer" if any(s.state == DOWN for s in broken) else "Worth saying"
    return f"{lead}: " + "; ".join(s.detail for s in broken if s.detail) + "."
