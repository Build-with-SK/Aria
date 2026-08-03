"""
src/v5/learning.py
==================
The continuous self-improvement engine (spec §8).

Every recommendation is logged with its inputs, its stated confidence and the
price at the time. When the horizon elapses, the outcome is compared to the
prediction, attributed to a specific cause, and only *then* — and only after a
binomial test clears significance — are module weights allowed to move.

Three properties are non-negotiable here:

  measurable   every weight change records the evidence that justified it
  reversible   every version is kept, and revert_to(version) restores it
  versioned    weights carry a version number that appears in every report

A single good call must never move a weight. That is the whole point.

State:
  data/v5/predictions.jsonl   the append-only prediction log
  data/v5/weights.json        current module weights + full change history
"""
from __future__ import annotations

import json
import logging
import math
import os
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent.parent.parent

# The prediction log is the calibration record — the one file whose integrity
# the whole track record depends on. ARIA_V5_DATA_DIR points the loop at a
# different directory (a demo record, a staging run, a per-user sandbox) so that
# nothing ever has a reason to write synthetic outcomes into the real one.
def _configured_dir() -> Path:
    # `set VAR=path && cmd` on Windows captures the trailing space before &&,
    # which silently yields a different directory. Strip whitespace and quotes.
    raw = (os.environ.get("ARIA_V5_DATA_DIR") or "").strip().strip('"').strip("'")
    return Path(raw) if raw else (ROOT / "data" / "v5")


V5_DIR = _configured_dir()
PRED_LOG = V5_DIR / "predictions.jsonl"
WEIGHTS_FILE = V5_DIR / "weights.json"

MIN_RESOLVED = 25          # no weight moves before this many resolved calls for a module
MAX_TILT = 0.20            # a module's multiplier stays within ±20% of 1.0
SIGNIFICANCE = 0.05        # two-sided binomial test against a 50% null

ATTRIBUTIONS = [
    "market randomness", "incorrect assumptions", "weak feature engineering",
    "poor model selection", "data quality", "regime change", "behavioural bias",
    "risk-management decision",
]


def _ensure_dir() -> None:
    V5_DIR.mkdir(parents=True, exist_ok=True)


# ── the prediction log ──────────────────────────────────────────────────────

def log_prediction(*, ticker: str, direction: str, p_bull: float, confidence: float,
                   horizon_days: int, price: Optional[float], modules: list[dict],
                   rationale: str = "", risk: Optional[dict] = None,
                   weights_version: Optional[int] = None) -> str:
    """Append one prediction. Returns its id.

    Deduplicated by (ticker, calendar day, direction): opening the analysis page
    five times is one call, not five. Without this the hit rate would be
    computed over copies of the same view and would silently overstate the
    sample behind every calibration number.
    """
    _ensure_dir()
    today = datetime.now().date().isoformat()
    for existing in reversed(load_predictions(limit=200)):
        if (existing.get("ticker") == ticker
                and existing.get("direction") == direction
                and not existing.get("resolved")
                and str(existing.get("at", ""))[:10] == today):
            return existing["id"]

    pid = f"v5-{uuid.uuid4().hex[:10]}"
    entry = {
        "id": pid,
        "at": datetime.now().isoformat(timespec="seconds"),
        "ticker": ticker,
        "direction": direction,
        "p_bull": round(float(p_bull), 4),
        "confidence": round(float(confidence), 4),
        "horizon_days": int(horizon_days),
        "resolve_after": (datetime.now() + timedelta(days=int(horizon_days) * 7 / 5)
                          ).date().isoformat(),
        "price_at": float(price) if price is not None else None,
        "modules": [{"module": m.get("module"), "family": m.get("family"),
                     "net": m.get("net"), "view": m.get("view"),
                     "abstained": bool(m.get("insufficient_data"))}
                    for m in modules],
        "rationale": rationale[:600],
        "risk": risk or {},
        "weights_version": weights_version,
        "resolved": False,
    }
    with PRED_LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, default=str) + "\n")
    return pid


def load_predictions(limit: Optional[int] = None) -> list[dict]:
    if not PRED_LOG.exists():
        return []
    out = []
    for line in PRED_LOG.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out[-limit:] if limit else out


def _rewrite(entries: list[dict]) -> None:
    _ensure_dir()
    tmp = PRED_LOG.with_suffix(".jsonl.tmp")
    tmp.write_text("\n".join(json.dumps(e, default=str) for e in entries) + "\n",
                   encoding="utf-8")
    tmp.replace(PRED_LOG)


# ── outcome resolution + attribution ────────────────────────────────────────

def resolve_pending(*, now: Optional[datetime] = None) -> dict:
    """Score every prediction whose horizon has elapsed. Safe to call repeatedly."""
    from src.v5 import marketdata as md

    now = now or datetime.now()
    entries = load_predictions()
    if not entries:
        return {"checked": 0, "resolved": 0}

    resolved = 0
    for e in entries:
        if e.get("resolved") or not e.get("price_at"):
            continue
        try:
            due = datetime.fromisoformat(e["resolve_after"])
        except Exception:
            continue
        if now < due:
            continue
        c = md.closes(e["ticker"], period="6mo")
        if c is None or c.empty:
            continue
        price_now = float(c.iloc[-1])
        ret = price_now / float(e["price_at"]) - 1.0
        correct = (ret > 0) if e["direction"] == "bull" else \
                  (ret < 0) if e["direction"] == "bear" else abs(ret) < 0.02

        e.update({
            "resolved": True,
            "resolved_at": now.isoformat(timespec="seconds"),
            "price_then": price_now,
            "realised_return": round(ret, 5),
            "correct": bool(correct),
            "attribution": _attribute(e, ret, bool(correct)),
        })
        resolved += 1

    if resolved:
        _rewrite(entries)
        update_weights()
    return {"checked": len(entries), "resolved": resolved}


def _attribute(entry: dict, ret: float, correct: bool) -> dict:
    """Assign a specific cause. Generic 'the market moved' is only allowed when
    the move genuinely was inside the noise band."""
    from src.v5 import marketdata as md

    causes, notes = [], []
    mods = entry.get("modules") or []
    voted = [m for m in mods if not m.get("abstained")]
    abstained = len(mods) - len(voted)
    nets = [float(m.get("net") or 0) for m in voted]
    dispersion = (math.sqrt(sum((n - (sum(nets) / len(nets))) ** 2 for n in nets) / len(nets))
                  if nets else 0.0)

    r = md.returns(entry["ticker"], period="1y")
    sigma_h = 0.0
    if r is not None and len(r) > 30:
        sigma_h = float(r.std()) * math.sqrt(max(1, entry.get("horizon_days", 21)))
    if sigma_h and abs(ret) < 0.5 * sigma_h:
        causes.append("market randomness")
        notes.append(f"Move of {ret * 100:+.1f}% was inside half a horizon standard deviation "
                     f"({sigma_h * 100:.1f}%) — this outcome carries little information either way.")

    if not correct:
        if dispersion > 45:
            causes.append("poor model selection")
            notes.append(f"Module dispersion was {dispersion:.0f} points — the ensemble was guessing "
                         f"between genuinely conflicting engines.")
        if abstained >= max(3, len(mods) // 3):
            causes.append("data quality")
            notes.append(f"{abstained} of {len(mods)} modules abstained for want of data.")
        if entry.get("confidence", 0) >= 0.7:
            causes.append("behavioural bias")
            notes.append(f"Confidence was stated at {entry['confidence']:.0%} and the call was wrong — "
                         f"overconfidence, not bad luck, at that level.")
        vix_note = _regime_changed(entry)
        if vix_note:
            causes.append("regime change")
            notes.append(vix_note)
        if not causes:
            causes.append("incorrect assumptions")
            notes.append("Modules agreed, data was present, confidence was moderate — the thesis "
                         "itself was simply wrong.")
    else:
        causes.append("thesis confirmed" if dispersion <= 45 else "correct despite internal disagreement")

    return {"causes": causes, "notes": notes,
            "dispersion": round(dispersion, 1), "abstentions": abstained,
            "horizon_sigma": round(sigma_h, 4)}


def _regime_changed(entry: dict) -> Optional[str]:
    from src.v5 import marketdata as md
    v = md.closes("^VIX", period="1y")
    if v is None or len(v) < 60:
        return None
    try:
        at = datetime.fromisoformat(entry["at"])
    except Exception:
        return None
    before = v[v.index <= at]
    if before.empty:
        return None
    v0, v1 = float(before.iloc[-1]), float(v.iloc[-1])
    if v0 > 0 and abs(v1 / v0 - 1) > 0.35:
        return (f"VIX moved from {v0:.1f} at entry to {v1:.1f} at resolution "
                f"({(v1 / v0 - 1) * 100:+.0f}%) — the volatility regime changed under the call.")
    return None


# ── module scorecard + weights ──────────────────────────────────────────────

def _binom_p(successes: int, n: int) -> float:
    """Two-sided binomial p-value against a fair-coin null."""
    try:
        from scipy import stats
        return float(stats.binomtest(successes, n, 0.5).pvalue)
    except Exception:
        if n == 0:
            return 1.0
        z = (successes / n - 0.5) / math.sqrt(0.25 / n)
        return float(math.erfc(abs(z) / math.sqrt(2)))


def module_scorecard() -> dict:
    """Per-module right/wrong over resolved predictions. A module is scored only
    when it took a side; abstentions and neutral reads never count."""
    card: dict[str, dict] = {}
    for e in load_predictions():
        if not e.get("resolved"):
            continue
        ret = e.get("realised_return")
        if ret is None:
            continue
        for m in e.get("modules") or []:
            view = m.get("view")
            if view not in ("bull", "bear"):
                continue
            name = m.get("module")
            rec = card.setdefault(name, {"right": 0, "wrong": 0, "family": m.get("family")})
            right = (ret > 0) if view == "bull" else (ret < 0)
            rec["right" if right else "wrong"] += 1
    for rec in card.values():
        n = rec["right"] + rec["wrong"]
        rec["n"] = n
        rec["hit_rate"] = round(rec["right"] / n, 4) if n else None
        rec["p_value"] = round(_binom_p(rec["right"], n), 4) if n else None
    return card


def load_weights() -> dict:
    if WEIGHTS_FILE.exists():
        try:
            return json.loads(WEIGHTS_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"v5 weights unreadable: {e}")
    return {"version": 1, "updated_at": None, "multipliers": {}, "history": []}


def current_multipliers() -> dict:
    return dict(load_weights().get("multipliers") or {})


def weights_version() -> int:
    return int(load_weights().get("version", 1))


def update_weights() -> dict:
    """Tilt module multipliers toward what is actually predictive — but only
    where the evidence is statistically significant and the sample is large
    enough. Every change is recorded with the evidence behind it."""
    card = module_scorecard()
    state = load_weights()
    old = dict(state.get("multipliers") or {})
    new, changes = dict(old), []

    for name, rec in card.items():
        n, hr, pv = rec["n"], rec["hit_rate"], rec["p_value"]
        if n < MIN_RESOLVED or hr is None or pv is None:
            continue
        if pv > SIGNIFICANCE:
            continue
        target = 1.0 + max(-MAX_TILT, min(MAX_TILT, (hr - 0.5) * 2 * MAX_TILT / 0.5))
        prev = old.get(name, 1.0)
        if abs(target - prev) < 0.01:
            continue
        new[name] = round(target, 4)
        changes.append({"module": name, "from": prev, "to": new[name],
                        "hit_rate": hr, "n": n, "p_value": pv})

    if not changes:
        return {"changed": 0, "version": state.get("version", 1)}

    state["history"] = (state.get("history") or []) + [{
        "version": state.get("version", 1),
        "replaced_at": datetime.now().isoformat(timespec="seconds"),
        "multipliers": old,
        "changes": changes,
    }]
    state["multipliers"] = new
    state["version"] = int(state.get("version", 1)) + 1
    state["updated_at"] = datetime.now().isoformat(timespec="seconds")
    _ensure_dir()
    WEIGHTS_FILE.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")
    logger.info(f"v5 weights → v{state['version']}: {len(changes)} module(s) reweighted")
    return {"changed": len(changes), "version": state["version"], "changes": changes}


def revert_to(version: int) -> dict:
    """Restore the multipliers as they were at `version`. Reversibility is a
    requirement, not a convenience — an unreversible learning system cannot be
    audited."""
    state = load_weights()
    for h in reversed(state.get("history") or []):
        if int(h.get("version", -1)) == int(version):
            state["history"].append({
                "version": state["version"],
                "replaced_at": datetime.now().isoformat(timespec="seconds"),
                "multipliers": dict(state.get("multipliers") or {}),
                "changes": [{"note": f"manual revert to version {version}"}],
            })
            state["multipliers"] = dict(h.get("multipliers") or {})
            state["version"] = int(state["version"]) + 1
            state["updated_at"] = datetime.now().isoformat(timespec="seconds")
            _ensure_dir()
            WEIGHTS_FILE.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")
            return {"reverted_to": version, "now_version": state["version"]}
    return {"error": f"version {version} not found in history"}


# ── self-audit inputs ───────────────────────────────────────────────────────

def bias_snapshot(n: int = 60) -> dict:
    """What ARIA's own recent output looks like — consumed by the
    self_bias_audit module and by the report's self-audit section."""
    entries = load_predictions(limit=n)
    if not entries:
        return {"n_predictions": 0}
    directions = [e.get("direction") for e in entries]
    bull = sum(1 for d in directions if d == "bull")
    resolved = [e for e in entries if e.get("resolved")]
    hit = (sum(1 for e in resolved if e.get("correct")) / len(resolved)) if resolved else None
    conf = ([float(e.get("confidence") or 0) for e in resolved] if resolved else [])
    streak = ""
    if resolved:
        tail = resolved[-5:]
        streak = " ".join("✓" if e.get("correct") else "✗" for e in tail)
    return {
        "n_predictions": len(entries),
        "bull_share": round(bull / len(entries), 4),
        "n_resolved": len(resolved),
        "hit_rate": round(hit, 4) if hit is not None else None,
        "mean_confidence": round(sum(conf) / len(conf), 4) if conf else None,
        "recent_streak": streak,
    }


def performance_summary() -> dict:
    """Everything the learning tab needs in one call."""
    entries = load_predictions()
    resolved = [e for e in entries if e.get("resolved")]
    causes: dict[str, int] = {}
    for e in resolved:
        for c in (e.get("attribution") or {}).get("causes", []):
            causes[c] = causes.get(c, 0) + 1
    state = load_weights()
    hit = (sum(1 for e in resolved if e.get("correct")) / len(resolved)) if resolved else None
    mean_conf = (sum(float(e.get("confidence") or 0) for e in resolved) / len(resolved)) if resolved else None
    return {
        "total_predictions": len(entries),
        "resolved": len(resolved),
        "pending": len(entries) - len(resolved),
        "hit_rate": round(hit, 4) if hit is not None else None,
        "mean_confidence": round(mean_conf, 4) if mean_conf is not None else None,
        "calibration_gap": (round(mean_conf - hit, 4)
                            if (hit is not None and mean_conf is not None) else None),
        "attribution_counts": causes,
        "module_scorecard": module_scorecard(),
        "weights_version": state.get("version", 1),
        "multipliers": state.get("multipliers", {}),
        "weight_history": (state.get("history") or [])[-10:],
        "recent": list(reversed(entries[-20:])),
    }
