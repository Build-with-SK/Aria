"""
src/intelligence/debate_eval.py
===============================
What the debate desk measurably adds — including the answer "nothing".

WHAT THE DESK IS, FROM ITS CODE (src/desk/debate.py)
----------------------------------------------------
The judge's view and conviction are a deterministic weighted vote of the
per-ticker analysts (technical 0.5, fundamental 0.3, sentiment 0.2, over the
analysts that voted), minus contradiction and macro penalties. A language
model writes the bull/bear rounds and the judge's explanation and DECIDES
NOTHING: no LLM output can change a view, a conviction or a verdict. So the
LLM's incremental predictive value is zero by construction, and a fluent
explanation is not evidence of anything. What CAN be measured is whether the
judge's aggregation beats its own technical input, which the archive records
for every debate.

WHAT IS MEASURED
----------------
operations   debates, LLM model, how often the prose fell back to the
             deterministic composer (an LLM failure), frontier consults,
             latency (recorded per debate from 2026-10-08; UNMEASURED before)
standalone   the judge's directional calls graded on a 1-day next-bar
             horizon: entry at the close of the first bar STRICTLY after the
             debate's calendar day, exit one bar later (the rule
             scripts/backfill_outcomes.py used for the imported desk rows)
incremental  the judge vs the technical analyst on the SAME events, by an
             exact McNemar test on the events where they disagree; and the
             judge vs always-long
calibration  conviction is a CONFIDENCE (0-100), not a probability: only its
             ordering is tested (do higher-conviction calls do better?)

Events are deduplicated on (ticker, view, entry bar): the desk re-debates a
ticker many times a day and those debates share one outcome.
"""
from __future__ import annotations

import json
import logging
import statistics
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from src.intelligence import evaluation

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent
DEBATES_DIR = ROOT / "data" / "desk" / "debates"
CONSULT_LOG = ROOT / "data" / "brain_consult_log.jsonl"
CACHE = ROOT / "data" / "intelligence" / "debate_eval.json"
DETERMINISTIC = "deterministic (Ollama offline)"
HORIZON_BARS = 1


def load(directory: Optional[Path] = None) -> list[dict]:
    """Compact records of every debate transcript in the archive."""
    d = directory or DEBATES_DIR
    out = []
    for p in sorted(d.glob("*.json")) if d.exists() else []:
        try:
            t = json.loads(p.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        judge = t.get("judge") or {}
        rounds = [r for r in t.get("rounds") or [] if isinstance(r.get("round"), int)]
        composed = sum(1 for r in rounds for k in ("bull", "bear")
                       if str(r.get(k, "")).startswith("["))
        analysts = {o.get("agent"): (o.get("view"), o.get("conviction"))
                    for o in t.get("opinions") or [] if o.get("agent")}
        out.append({
            "id": t.get("id"), "ticker": t.get("ticker"), "at": t.get("at"),
            "llm": t.get("llm"),
            "llm_requested": t.get("llm") not in (None, DETERMINISTIC),
            "composed_rounds": composed, "rounds": len(rounds) * 2,
            "view": judge.get("view"), "conviction": judge.get("conviction"),
            "verdict": judge.get("verdict"), "bar": judge.get("conviction_bar"),
            "regime": judge.get("regime"), "analysts": analysts,
            "timings_ms": t.get("timings_ms"),
            "prose_source": t.get("prose_source"),
        })
    return out


def operations(debates: list[dict]) -> dict:
    by_month = Counter(str(d["at"] or "")[:7] for d in debates)
    llm = Counter(d["llm"] for d in debates)
    requested = [d for d in debates if d["llm_requested"]]
    failed = [d for d in requested if d["composed_rounds"]]
    timed = [d["timings_ms"]["total"] for d in debates
             if isinstance(d.get("timings_ms"), dict) and d["timings_ms"].get("total") is not None]
    consults = 0
    if CONSULT_LOG.exists():
        for line in CONSULT_LOG.read_text(encoding="utf-8").splitlines():
            if "DESK_JUDGE" in line:
                consults += 1
    tech_dir = [d for d in debates if (d["analysts"].get("technical") or (None,))[0]
                in ("bull", "bear") and d["view"] in ("bull", "bear")]
    same = sum(1 for d in tech_dir if d["analysts"]["technical"][0] == d["view"])
    return {
        "debates": len(debates),
        "period": [min((d["at"] for d in debates if d["at"]), default=None),
                   max((d["at"] for d in debates if d["at"]), default=None)],
        "by_month": dict(sorted(by_month.items())),
        "llm_models": dict(llm),
        "verdicts": dict(Counter(d["verdict"] for d in debates)),
        "llm_failure_rate": {
            "debates_with_llm_requested": len(requested),
            "debates_with_a_composed_fallback_round": len(failed),
            "rate": round(len(failed) / len(requested), 4) if requested else None},
        "latency_ms": ({"status": "MEASURED", "n": len(timed),
                        "median": round(statistics.median(timed), 1),
                        "p95": round(sorted(timed)[int(0.95 * (len(timed) - 1))], 1)}
                       if timed else
                       {"status": "UNMEASURED",
                        "why": "debates before 2026-10-08 recorded no timings"}),
        "cost": {"local_llm_api_cost_usd": 0.0,
                 "frontier_judge_prose_consults": consults,
                 "note": "local Ollama model: no per-call API charge; compute time "
                         "is the cost, and is in latency_ms once recorded"},
        "judge_agrees_with_technical_analyst": {
            "n": len(tech_dir), "rate": round(same / len(tech_dir), 4) if tech_dir else None},
        "decided_by": "deterministic weighted analyst vote (src/desk/debate._judge); "
                      "the LLM writes prose only",
    }


def _entry_exit(closes, when: str, horizon: int = HORIZON_BARS):
    """(entry_date, entry, exit) for the first bar strictly after the debate's
    calendar day and the bar `horizon` later, or None."""
    import pandas as pd
    idx = closes.index
    ts = pd.Timestamp(datetime.fromisoformat(str(when)).date())
    if getattr(idx, "tz", None) is not None:
        ts = ts.tz_localize(idx.tz)
    pos = idx.searchsorted(ts, side="right")
    if pos + horizon >= len(idx):
        return None
    entry, exit_ = float(closes.iloc[pos]), float(closes.iloc[pos + horizon])
    if not (entry > 0 and exit_ > 0):
        return None
    return str(idx[pos].date()), entry, exit_


def grade(debates: list[dict], closes_for: Callable[[str], object],
          horizon: int = HORIZON_BARS) -> list[dict]:
    """One graded event per (ticker, judge view, entry bar)."""
    cache: dict = {}
    events, seen = [], set()
    for d in sorted(debates, key=lambda x: x["at"] or ""):
        if d["view"] not in ("bull", "bear") or not d["ticker"] or not d["at"]:
            continue
        t = d["ticker"]
        if t not in cache:
            try:
                cache[t] = closes_for(t)
            except Exception as e:
                logger.debug("no closes for %s: %s", t, e)
                cache[t] = None
        c = cache[t]
        if c is None or len(c) == 0:
            continue
        ee = _entry_exit(c, d["at"], horizon)
        if ee is None:
            continue
        entry_date, entry, exit_ = ee
        key = (t, d["view"], entry_date)
        if key in seen:
            continue
        seen.add(key)
        ret = exit_ / entry - 1
        tech = (d["analysts"].get("technical") or (None, None))
        events.append({
            "debate_id": d["id"], "ticker": t, "created_at": d["at"],
            "information_date": entry_date, "direction": d["view"],
            "conviction": d["conviction"], "verdict": d["verdict"],
            "technical_view": tech[0], "technical_conviction": tech[1],
            "actual_return": round(ret, 6),
            "outcome": int(ret > 0 if d["view"] == "bull" else ret < 0),
        })
    return events


def _mcnemar(events: list[dict], other_view: str) -> dict:
    """Judge vs another call on the same events: exact test on disagreements."""
    pairs = [e for e in events if e.get(other_view) in ("bull", "bear")]
    def right(view, ret):
        return ret > 0 if view == "bull" else ret < 0
    b = sum(1 for e in pairs if e["outcome"] and not right(e[other_view], e["actual_return"]))
    c = sum(1 for e in pairs if not e["outcome"] and right(e[other_view], e["actual_return"]))
    base = {"paired_events": len(pairs),
            "disagreements": sum(1 for e in pairs if e[other_view] != e["direction"]),
            "judge_right_other_wrong": b, "judge_wrong_other_right": c}
    if b + c < evaluation.MIN_DISCORDANT:
        return {**base, "status": "INSUFFICIENT_DATA",
                "n_required": evaluation.MIN_DISCORDANT,
                "why": "too few events where the judge and the other call disagree on the outcome"}
    pv = evaluation._binom_two_sided(b, b + c)
    return {**base, "status": "MEASURED", "p_value": round(pv, 4),
            "verdict": ("judge adds value" if pv < 0.05 and b > c else
                        "judge subtracts value" if pv < 0.05 and c > b else
                        "no measurable difference")}


def _ordering(events: list[dict]) -> dict:
    """Does higher conviction go with being right? Conviction is not a
    probability, so this is the only calibration question it can answer."""
    usable = sorted((e for e in events if e.get("conviction") is not None),
                    key=lambda e: e["conviction"])
    if len(usable) < 2 * evaluation.MIN_EVENTS:
        return evaluation.insufficient(len(usable), 2 * evaluation.MIN_EVENTS,
                                       "events with a conviction")
    half = len(usable) // 2
    lo, hi = evaluation.rate_block(usable[:half]), evaluation.rate_block(usable[-half:])
    if lo["status"] != "MEASURED" or hi["status"] != "MEASURED":
        return {"status": "INSUFFICIENT_DATA", "lower_half": lo, "upper_half": hi}
    sep = lo["ci95_cluster"][1] < hi["ci95_cluster"][0] or hi["ci95_cluster"][1] < lo["ci95_cluster"][0]
    return {"status": "MEASURED", "lower_half": lo, "upper_half": hi,
            "reading": ("higher conviction is right more often" if sep and hi["rate"] > lo["rate"]
                        else "INVERTED: higher conviction is right less often" if sep
                        else "undetermined — the halves' intervals overlap")}


def evaluate(events: list[dict]) -> dict:
    by_pop = defaultdict(list)
    for e in events:
        by_pop["traded (BUY/SELL)" if e["verdict"] in ("BUY", "SELL") else "NO_TRADE"].append(e)
    standalone = evaluation.rate_block(events)
    tech_events = [dict(e, direction=e["technical_view"],
                        outcome=int(e["actual_return"] > 0 if e["technical_view"] == "bull"
                                    else e["actual_return"] < 0))
                   for e in events if e.get("technical_view") in ("bull", "bear")]
    inc = _mcnemar(events, "technical_view")
    out = {
        "events": len(events),
        "horizon_bars": HORIZON_BARS,
        "judge_standalone": standalone,
        "technical_analyst_standalone_same_events": evaluation.rate_block(tech_events),
        "judge_vs_technical_analyst": inc,
        "judge_vs_always_long": evaluation.vs_always_long(events),
        "by_population": {k: evaluation.rate_block(v) for k, v in by_pop.items()},
        "conviction_ordering": _ordering(events),
        "llm_incremental_value": {
            "status": "ZERO_BY_CONSTRUCTION",
            "why": "the LLM writes prose; the judge's view, conviction and verdict "
                   "are computed without it (src/desk/debate._judge)"},
    }
    if inc.get("status") == "MEASURED" and inc["verdict"] == "judge adds value":
        out["conclusion"] = ("The judge's aggregation beats its technical input on the "
                             "events where they disagree.")
    else:
        out["conclusion"] = ("No measurable incremental value: the language model decides "
                             "nothing by construction, and the judge's aggregation is "
                             + ("not distinguishable from its technical input"
                                if inc.get("status") == "MEASURED" else
                                "not yet testable against its technical input")
                             + " on the graded events.")
    return out


def run(closes_for: Optional[Callable] = None, *, directory: Optional[Path] = None) -> dict:
    """Operations always; grading when a price source is supplied."""
    debates = load(directory)
    out = {"computed_at": datetime.now().isoformat(timespec="seconds"),
           "operations": operations(debates)}
    if closes_for is not None:
        out["evaluation"] = evaluate(grade(debates, closes_for))
    else:
        out["evaluation"] = {"status": "NOT_COMPUTED",
                             "why": "grading needs a price source; run "
                                    "scripts/model_intelligence_baseline.py"}
    return out


def vendor_closes(ticker: str):
    """Daily closes for a debate ticker, through the identity resolver."""
    from src.core import ledger
    from src.v5 import marketdata as md
    provider, _ = ledger._resolved_price_symbol(ticker)
    return md.provider_closes(provider or ticker)


def save(result: dict) -> None:
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(result, indent=1, default=str), encoding="utf-8")


def cached() -> Optional[dict]:
    if not CACHE.exists():
        return None
    try:
        return json.loads(CACHE.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
