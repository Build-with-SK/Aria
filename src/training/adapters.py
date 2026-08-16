"""
src/training/adapters.py
========================
VERSIONED ADAPTERS, AND THE GATE A NEW ONE HAS TO PASS
(docs/ARIA_NEXT_SESSION.md step 6).

"Version every adapter, keep the previous one, and evaluate each new one
against the incumbent on held-out data BEFORE promoting it. If it does not
win, it is not adopted. Adopting a worse model because it is newer is the
failure mode; guard against it."

So: promotion is a decision with reasons, not an assignment. `decide()`
returns why, `promote()` refuses to act on anything `decide()` did not
approve, and nothing here deletes a previous adapter — rollback has to be
possible on the evening it turns out to be needed, not reconstructible from a
backup.

THE FIRST ADAPTER IS NOT EXEMPT
-------------------------------
With no incumbent, the baseline is the BASE MODEL — the un-fine-tuned weights
already serving. A first adapter that loses to the model it was supposed to
improve is not a starting point, it is a regression with nothing to compare
against yet. `decide()` treats "no incumbent" as "compare against base", never
as "promote by default".

WHAT IS NOT HERE
----------------
Anything that promotes automatically. A promotion changes what her reasoning
runs on, and the standing arrangement is that she proposes and a human
approves. `promote()` takes `approved_by` and records it.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from src.training.evaluate import Comparison

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent
ADAPTER_DIR = ROOT / "data" / "training" / "adapters"
REGISTRY_FILE = ADAPTER_DIR / "registry.json"


@dataclass
class Adapter:
    version: str
    path: str = ""
    base_model: str = ""
    trained_at: str = ""
    trained_on: dict = field(default_factory=dict)   # dataset manifest
    train_rows: int = 0
    holdout_rows: int = 0
    notes: str = ""
    contains_personal_data: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Decision:
    promote: bool = False
    version: str = ""
    against: str = ""
    reasons: list[str] = field(default_factory=list)
    comparison: dict = field(default_factory=dict)
    at: str = ""

    def describe(self) -> str:
        head = (f"PROMOTE {self.version} over {self.against}" if self.promote
                else f"HOLD {self.version} — {self.against} keeps serving")
        return "\n".join([head, *(f"  - {r}" for r in self.reasons)])

    def to_dict(self) -> dict:
        return asdict(self)


# ── the registry ─────────────────────────────────────────────────────────────

def _empty() -> dict:
    return {"schema": "aria.adapters/1", "serving": "", "previous": "",
            "base_model": "", "adapters": {}, "decisions": []}


def load(path: Path = REGISTRY_FILE) -> dict:
    path = Path(path)
    if not path.exists():
        return _empty()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as e:
        # A corrupt registry must not read as "nothing is serving" — that
        # would look exactly like a clean install and invite a promotion on
        # top of it.
        raise RuntimeError(f"the adapter registry at {path} is unreadable "
                           f"({e}). Fix or restore it before promoting "
                           f"anything.") from e
    return {**_empty(), **data}


def save(registry: dict, path: Path = REGISTRY_FILE) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(registry, indent=2, default=str), encoding="utf-8")
    return path


def register(adapter: Adapter, *, path: Path = REGISTRY_FILE) -> dict:
    """Record an adapter. Registering is NOT promoting — a registered adapter
    is a candidate and nothing else."""
    registry = load(path)
    if adapter.version in registry["adapters"]:
        raise ValueError(f"adapter {adapter.version} is already registered; "
                         f"versions are immutable so a decision log stays "
                         f"meaningful")
    registry["adapters"][adapter.version] = adapter.to_dict()
    save(registry, path)
    return registry


def serving(path: Path = REGISTRY_FILE) -> Adapter | None:
    """The adapter currently in the reasoning path, or None for the base
    model."""
    registry = load(path)
    version = registry.get("serving")
    if not version:
        return None
    data = registry["adapters"].get(version)
    return Adapter(**data) if data else None


def previous(path: Path = REGISTRY_FILE) -> Adapter | None:
    registry = load(path)
    version = registry.get("previous")
    data = registry["adapters"].get(version) if version else None
    return Adapter(**data) if data else None


def history(path: Path = REGISTRY_FILE) -> list[dict]:
    return list(load(path).get("decisions") or [])


# ── the gate ─────────────────────────────────────────────────────────────────

def decide(version: str, comparison: Comparison, *,
           path: Path = REGISTRY_FILE) -> Decision:
    """Should this adapter replace what is serving? Reasons either way."""
    registry = load(path)
    incumbent = registry.get("serving") or (registry.get("base_model")
                                            or "the base model")
    d = Decision(version=version, against=incumbent,
                 at=datetime.now().isoformat(timespec="seconds"),
                 comparison=comparison.to_dict())

    if version not in registry["adapters"]:
        d.reasons.append(f"{version} is not registered — an adapter is "
                         f"promoted from the registry, not from a path "
                         f"somebody typed")
        return d

    if not registry.get("serving"):
        d.reasons.append(
            "no adapter is serving, so the baseline is the base model itself. "
            "A first adapter still has to beat what it was meant to improve.")

    if comparison.verdict == "better":
        d.promote = True
        d.reasons.extend(comparison.reasons)
        return d

    if comparison.verdict == "insufficient":
        d.reasons.append("not enough held-out evidence to decide. Held, not "
                         "rejected — re-run when the sample grows.")
    elif comparison.verdict == "worse":
        d.reasons.append("the candidate is measurably worse. Not adopted.")
    else:
        d.reasons.append("indistinguishable from what is already serving. A "
                         "tie goes to the incumbent: swapping models on a tie "
                         "is how a worse one eventually gets adopted for being "
                         "newer.")
    d.reasons.extend(comparison.reasons)
    return d


def promote(decision: Decision, *, approved_by: str,
            path: Path = REGISTRY_FILE) -> dict:
    """Make the candidate the serving adapter. Refuses anything `decide()`
    did not approve, and keeps the outgoing one."""
    if not decision.promote:
        raise PermissionError(
            "refusing to promote an adapter the gate held:\n" + decision.describe())
    if not (approved_by or "").strip():
        raise PermissionError(
            "a promotion changes what her reasoning runs on — it is approved "
            "by a person, and the person is recorded")

    registry = load(path)
    if decision.version not in registry["adapters"]:
        raise ValueError(f"{decision.version} is not registered")

    registry["previous"] = registry.get("serving", "")
    registry["serving"] = decision.version
    entry = {**decision.to_dict(), "action": "promote", "approved_by": approved_by}
    registry.setdefault("decisions", []).append(entry)
    save(registry, path)
    logger.info("adapter %s promoted over %s by %s", decision.version,
                decision.against, approved_by)
    return registry


def record_hold(decision: Decision, *, path: Path = REGISTRY_FILE) -> dict:
    """Log a candidate that did NOT win. The held ones are the more useful
    half of the record: they are the evidence that the gate does something."""
    registry = load(path)
    registry.setdefault("decisions", []).append(
        {**decision.to_dict(), "action": "hold"})
    save(registry, path)
    return registry


def rollback(*, approved_by: str, reason: str = "",
             path: Path = REGISTRY_FILE) -> dict:
    """Put the previous adapter back. Available because the evening it is
    needed is not the evening to be reconstructing one from a backup."""
    registry = load(path)
    prior = registry.get("previous")
    if not prior:
        raise ValueError("there is no previous adapter to roll back to")
    if not (approved_by or "").strip():
        raise PermissionError("a rollback is approved by a person too")
    outgoing = registry.get("serving", "")
    registry["serving"], registry["previous"] = prior, outgoing
    registry.setdefault("decisions", []).append({
        "action": "rollback", "version": prior, "against": outgoing,
        "reasons": [reason or "rolled back by the owner"],
        "at": datetime.now().isoformat(timespec="seconds"),
        "approved_by": approved_by, "promote": False, "comparison": {}})
    save(registry, path)
    return registry


def status(path: Path = REGISTRY_FILE) -> dict:
    registry = load(path)
    now = serving(path)
    return {
        "serving": now.to_dict() if now else None,
        "serving_is_base_model": now is None,
        "base_model": registry.get("base_model", ""),
        "previous": registry.get("previous", ""),
        "registered": list(registry.get("adapters", {})),
        "decisions": len(registry.get("decisions") or []),
        "note": ("No adapter is serving; her reasoning runs on the base model "
                 "as it comes. That is the honest state until an adapter has "
                 "beaten it on held-out data." if now is None else ""),
    }
