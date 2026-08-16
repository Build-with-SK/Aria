"""
src/inference/baseline.py
=========================
BASELINES — captured before the brain changes, because they cannot be
captured afterwards (docs/ARIA_NEXT_SESSION.md step 3).

Four measurements on five fixed tickers:

1. **Debate transcripts.** What she argued, and — more usefully — the judge's
   NUMBERS. Those are computed deterministically from the evidence, never by
   the LLM (`src/desk/debate.py`). So conviction and verdict must come out
   IDENTICAL after a brain swap. If they move, the model has leaked into the
   verdict path, which is a bug of a different order from worse prose.

2. **Module abstention rate.** The 41 research modules do not use an LLM at
   all. This number must be identical too. A change here means something is
   miswired, and the swap is not what it appears to be.

3. **Latency per debate**, against the desk's hunt cycle. A brain that is
   right and slower than the tick it feeds is not usable at the tick.

4. **Structured-output compliance.** The only place an LLM reply is parsed as
   JSON in this system is Fable's trade review (`src/desk/teacher.py`), and
   the only place a strict token format is demanded is the REFLEX veto. Both
   are probed with the real parsers rather than a synthetic one, because a
   made-up schema would measure a model against a prompt nothing uses.

Metrics 1 and 2 are EQUALITY checks, not comparisons — that is the point of
capturing them first. `compare()` marks any drift in them as MISWIRED rather
than as a regression, because the honest reading is not "the new brain is
worse" but "this is not the experiment you think you are running".

Nothing here writes to the prediction log. A baseline run is measurement, not
a call (invariant 5).
"""
from __future__ import annotations

import json
import statistics
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
BASELINE_DIR = ROOT / "data" / "baselines"

#: Five fixed names, chosen once and never varied — a baseline compared
#: against a different universe measures the universe. One mega-cap, one
#: high-beta semi, one bank, one energy name, one index proxy: if a new brain
#: only degrades on, say, the macro-driven names, a single-sector sample would
#: hide it.
FIXED_TICKERS: tuple[str, ...] = ("AAPL", "NVDA", "JPM", "XOM", "SPY")

#: What the numbers must not move by. Zero: they are deterministic.
EQUALITY_KEYS = ("verdict", "conviction", "abstained", "reporting")


@dataclass
class DebateSample:
    ticker: str
    ok: bool = False
    debate_id: str = ""
    verdict: str = ""
    conviction: float | None = None
    llm: str = ""
    rounds: int = 0
    elapsed_ms: int = 0
    error: str = ""

    def to_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class ModuleSample:
    ticker: str
    ok: bool = False
    total: int = 0
    reporting: int = 0
    abstained: int = 0
    abstained_modules: list[str] = field(default_factory=list)
    elapsed_ms: int = 0
    error: str = ""
    # Modules abstain when their inputs are missing, so the data layer has to
    # be recorded alongside the abstention count. Without this, a baseline
    # captured during a yfinance rate-limit and compared against a clean one
    # reports "MISWIRED" about the feed.
    stale: bool | None = None
    vendor: str = ""

    def to_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class StructuredSample:
    probe: str = ""
    attempts: int = 0
    parsed: int = 0
    failures: int = 0
    unreachable: int = 0
    examples: list[str] = field(default_factory=list)

    @property
    def failure_rate(self) -> float | None:
        """Of the replies that ARRIVED, the share that could not be parsed.
        None when nothing arrived — an unreachable brain has no failure rate,
        and reporting 0% for it would read as perfect compliance."""
        answered = self.attempts - self.unreachable
        return round(self.failures / answered, 4) if answered > 0 else None

    def to_dict(self) -> dict:
        d = {k: v for k, v in self.__dict__.items()}
        d["failure_rate"] = self.failure_rate
        return d


# ── capture ──────────────────────────────────────────────────────────────────

def brain_identity() -> dict:
    """Which brain this baseline is OF. A baseline with no record of the model
    that produced it is a number without an experiment attached."""
    out: dict = {"at": datetime.now().isoformat(timespec="seconds")}
    try:
        from src.inference.router import get_router
        out["router"] = get_router().status()
    except Exception as e:
        out["router_error"] = str(e)
    try:
        from src.desk.config import load_config
        cfg = load_config()
        out["desk_llm_model"] = cfg.get("llm_model")
        out["hunt_interval_minutes"] = cfg.get("interval_minutes")
        out["debate_rounds"] = cfg.get("debate_rounds")
    except Exception as e:
        out["desk_config_error"] = str(e)
    return out


def capture_modules(tickers=FIXED_TICKERS) -> list[ModuleSample]:
    """Abstention per ticker, straight from the v5 pipeline. `log=False`:
    a baseline must not add rows to the prediction log."""
    from src.v5 import pipeline

    out = []
    for t in tickers:
        s = ModuleSample(ticker=t)
        t0 = time.time()
        try:
            result = pipeline.analyze(t, log=False)
            counts = result.get("module_count") or {}
            s.total = int(counts.get("total") or 0)
            s.reporting = int(counts.get("reporting") or 0)
            s.abstained = int(counts.get("abstained") or 0)
            s.abstained_modules = sorted(
                m.get("module", "?") for m in (result.get("modules") or [])
                if m.get("abstained"))
            data = result.get("data") or {}
            s.stale = data.get("stale")
            s.vendor = str(data.get("vendor") or data.get("source") or "")
            s.ok = s.total > 0
        except Exception as e:
            s.error = f"{type(e).__name__}: {e}"
        s.elapsed_ms = int((time.time() - t0) * 1000)
        out.append(s)
    return out


def capture_debates(tickers=FIXED_TICKERS, *, rounds: int | None = None
                    ) -> list[DebateSample]:
    """One full debate per ticker, through the same path the desk uses."""
    from src.desk.analysts import (fundamental_agent, macro_agent,
                                   sentiment_agent, technical_agent)
    from src.desk.config import load_config
    from src.desk.debate import DebateEngine

    cfg = load_config()
    engine = DebateEngine(model=cfg["llm_model"],
                          rounds=rounds or cfg["debate_rounds"])
    conditioner = macro_agent.condition()

    out = []
    for t in tickers:
        s = DebateSample(ticker=t)
        t0 = time.time()
        try:
            opinions = [technical_agent.opine(t), fundamental_agent.opine(t),
                        sentiment_agent.opine(t)]
            transcript = engine.debate(t, opinions, conditioner)
            judge = transcript.get("judge") or {}
            s.debate_id = transcript.get("id", "")
            s.verdict = str(judge.get("verdict", ""))
            s.conviction = judge.get("conviction")
            s.llm = str(transcript.get("llm", ""))
            s.rounds = len(transcript.get("rounds") or [])
            s.ok = bool(s.verdict)
        except Exception as e:
            s.error = f"{type(e).__name__}: {e}"
        s.elapsed_ms = int((time.time() - t0) * 1000)
        out.append(s)
    return out


_JSON_PROBE = (
    "A closed paper trade: LONG AAPL, entered 214.30, exited 209.85, "
    "-2.1%, held 6 days, thesis 'momentum continuation after earnings beat'.\n\n"
    "Reply with STRICT JSON only, no prose, no code fence. Keep key_lesson "
    "and do_differently UNDER 20 words each:\n"
    '{"thesis_quality": <0-100>, "direction_right": <true|false>, '
    '"misleading_agent": "<technical|fundamental|sentiment|none>", '
    '"key_lesson": "<=20 words ARIA should remember>", '
    '"do_differently": "<=20 words, one concrete adjustment>"}')

_TOKEN_PROBE = (
    "Trade check. BUY AAPL at 214.30, stop 210.00, target 222.00, "
    "signal composite 61.0, regime risk-on. Reply exactly PROCEED, or "
    "VETO:<=6-word-reason if this is an obviously bad trade.")


def capture_structured(attempts: int = 6, *, complete=None) -> list[StructuredSample]:
    """Does the brain obey a format when told to?

    Probed with the system's OWN parsers: `teacher.parse_review` for JSON and
    the REFLEX veto's PROCEED/VETO contract. A model that writes beautiful
    prose and cannot emit a parseable object breaks the teacher silently — the
    lesson is simply never written, and nothing logs a complaint.
    """
    from src.desk.teacher import parse_review

    if complete is None:
        def complete(prompt: str, max_tokens: int) -> str:
            from src.inference.router import Tier, get_router
            return get_router().complete(
                Tier.STANDARD, [{"role": "user", "content": prompt}],
                max_tokens=max_tokens, temperature=0.0, timeout=90).text

    def run(name, prompt, max_tokens, accept) -> StructuredSample:
        s = StructuredSample(probe=name, attempts=attempts)
        for _ in range(attempts):
            try:
                text = complete(prompt, max_tokens)
            except Exception as e:
                s.unreachable += 1
                if len(s.examples) < 3:
                    s.examples.append(f"unreachable: {type(e).__name__}: {e}"[:200])
                continue
            if accept(text):
                s.parsed += 1
            else:
                s.failures += 1
                if len(s.examples) < 3:
                    s.examples.append((text or "")[:200])
        return s

    return [
        run("teacher_json", _JSON_PROBE, 220,
            lambda t: parse_review(t) is not None),
        run("reflex_token", _TOKEN_PROBE, 30,
            lambda t: (t or "").strip().upper().startswith(("PROCEED", "VETO"))),
    ]


def latency_block(debates: list[DebateSample], *, tick_minutes: int = 30) -> dict:
    """Per-debate latency against the cycle it has to fit inside.

    The desk debates several names per hunt cycle, so the budget that matters
    is n_debates × p95, not one call in isolation.
    """
    times = [d.elapsed_ms for d in debates if d.ok]
    if not times:
        return {"samples": 0, "note": "no debate completed — no latency to report"}
    ordered = sorted(times)
    p95 = ordered[min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1))))]
    budget_ms = tick_minutes * 60 * 1000
    projected = p95 * len(times)
    return {
        "samples": len(times),
        "p50_ms": int(statistics.median(ordered)),
        "p95_ms": int(p95),
        "max_ms": int(ordered[-1]),
        "mean_ms": int(statistics.fmean(ordered)),
        "tick_minutes": tick_minutes,
        "projected_cycle_ms": int(projected),
        "fits_the_tick": projected < budget_ms,
        "headroom_pct": round(100 * (1 - projected / budget_ms), 1),
    }


def capture(tickers=FIXED_TICKERS, *, structured_attempts: int = 6,
            skip_debates: bool = False, skip_structured: bool = False) -> dict:
    """The whole baseline. Slow — it runs the real pipeline and real debates."""
    identity = brain_identity()
    modules = capture_modules(tickers)
    debates = [] if skip_debates else capture_debates(tickers)
    structured = [] if skip_structured else capture_structured(structured_attempts)
    tick = int(identity.get("hunt_interval_minutes") or 30)

    return {
        "schema": "aria.brain_baseline/1",
        "at": datetime.now().isoformat(timespec="seconds"),
        "brain": identity,
        "tickers": list(tickers),
        "modules": [m.to_dict() for m in modules],
        "debates": [d.to_dict() for d in debates],
        "structured": [s.to_dict() for s in structured],
        "latency": latency_block(debates, tick_minutes=tick),
        "caveats": [
            "Module abstention and judge conviction are computed without an "
            "LLM. After a brain swap they must be IDENTICAL; drift means "
            "miswiring, not a worse model.",
            "Five tickers on one date is one market day, not five independent "
            "observations. This is a wiring check, not evidence of skill.",
        ],
    }


def save(baseline: dict, directory: Path = BASELINE_DIR) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = directory / f"brain_baseline_{stamp}.json"
    path.write_text(json.dumps(baseline, indent=2, default=str), encoding="utf-8")
    return path


def load(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def latest(directory: Path = BASELINE_DIR) -> Path | None:
    files = sorted(Path(directory).glob("brain_baseline_*.json"))
    return files[-1] if files else None


# ── compare ──────────────────────────────────────────────────────────────────

def _by_ticker(rows: list[dict]) -> dict:
    return {r.get("ticker"): r for r in rows if r.get("ticker")}


def compare(before: dict, after: dict) -> dict:
    """What moved, and which movements are legitimate.

    Three verdicts:
      MISWIRED  — a deterministic number changed. Stop and find out why.
      REGRESSED — a legitimate metric got worse.
      OK        — within expectations.
    """
    miswired: list[str] = []
    regressed: list[str] = []
    notes: list[str] = []

    b_mod, a_mod = _by_ticker(before.get("modules") or []), _by_ticker(after.get("modules") or [])
    for ticker, b in b_mod.items():
        a = a_mod.get(ticker)
        if a is None:
            notes.append(f"{ticker}: not measured in the new run")
            continue
        if not (b.get("ok") and a.get("ok")):
            notes.append(f"{ticker}: module run failed in one of the two baselines")
            continue
        counts_moved = ((b.get("abstained"), b.get("reporting"))
                        != (a.get("abstained"), a.get("reporting")))
        # Modules abstain on missing inputs. If the data layer differed
        # between the two runs, an abstention change says something about the
        # feed, not about the brain — and calling that MISWIRED would send a
        # future session hunting a wiring bug that is not there.
        data_differs = (b.get("stale") != a.get("stale")
                        or (b.get("vendor") or "") != (a.get("vendor") or ""))
        if counts_moved and data_differs:
            notes.append(
                f"{ticker}: abstention moved {b.get('abstained')}→"
                f"{a.get('abstained')}, but the data layer also differed "
                f"(stale {b.get('stale')}→{a.get('stale')}, vendor "
                f"{b.get('vendor') or '?'}→{a.get('vendor') or '?'}). Not a "
                f"clean comparison — re-capture with the same feed before "
                f"reading anything into it.")
        elif counts_moved:
            miswired.append(
                f"{ticker}: module abstention moved {b.get('abstained')}→"
                f"{a.get('abstained')} (reporting {b.get('reporting')}→"
                f"{a.get('reporting')}). The 41 modules do not call an LLM; a "
                f"brain swap cannot change this number.")
        elif data_differs:
            notes.append(f"{ticker}: same abstention, different data layer "
                         f"(stale {b.get('stale')}→{a.get('stale')})")
        else:
            gained = set(a.get("abstained_modules") or []) - set(b.get("abstained_modules") or [])
            lost = set(b.get("abstained_modules") or []) - set(a.get("abstained_modules") or [])
            if gained or lost:
                miswired.append(
                    f"{ticker}: the same COUNT of modules abstained but not the "
                    f"same modules (now abstaining: {sorted(gained)}; no longer: "
                    f"{sorted(lost)})")

    b_deb, a_deb = _by_ticker(before.get("debates") or []), _by_ticker(after.get("debates") or [])
    for ticker, b in b_deb.items():
        a = a_deb.get(ticker)
        if a is None or not (b.get("ok") and a.get("ok")):
            notes.append(f"{ticker}: no comparable debate in both runs")
            continue
        if b.get("verdict") != a.get("verdict") or b.get("conviction") != a.get("conviction"):
            miswired.append(
                f"{ticker}: judge verdict/conviction moved "
                f"{b.get('verdict')}/{b.get('conviction')} → "
                f"{a.get('verdict')}/{a.get('conviction')}. The judge's numbers "
                f"are deterministic — the LLM writes prose, it never decides "
                f"conviction. This means it now does.")

    b_lat, a_lat = before.get("latency") or {}, after.get("latency") or {}
    if b_lat.get("samples") and a_lat.get("samples"):
        b_p95, a_p95 = b_lat.get("p95_ms", 0), a_lat.get("p95_ms", 0)
        if b_p95 and a_p95 > b_p95 * 1.5:
            regressed.append(f"p95 debate latency {b_p95}ms → {a_p95}ms "
                             f"(+{round(100 * (a_p95 / b_p95 - 1))}%)")
        if b_lat.get("fits_the_tick") and not a_lat.get("fits_the_tick"):
            regressed.append(
                f"a full cycle no longer fits the {a_lat.get('tick_minutes')}-minute "
                f"tick: projected {a_lat.get('projected_cycle_ms')}ms")

    b_str = {s.get("probe"): s for s in (before.get("structured") or [])}
    a_str = {s.get("probe"): s for s in (after.get("structured") or [])}
    for probe, b in b_str.items():
        a = a_str.get(probe)
        if not a:
            continue
        br, ar = b.get("failure_rate"), a.get("failure_rate")
        if br is None or ar is None:
            notes.append(f"{probe}: no failure rate on one side "
                         f"(the brain was unreachable — not 0%)")
        elif ar > br + 0.10:
            regressed.append(f"{probe}: format failures {br:.0%} → {ar:.0%}")

    verdict = "MISWIRED" if miswired else ("REGRESSED" if regressed else "OK")
    return {
        "verdict": verdict,
        "miswired": miswired,
        "regressed": regressed,
        "notes": notes,
        "before": before.get("at"),
        "after": after.get("at"),
        "before_brain": (before.get("brain") or {}).get("desk_llm_model"),
        "after_brain": (after.get("brain") or {}).get("desk_llm_model"),
    }


def format_comparison(result: dict) -> str:
    lines = [f"{result['verdict']}  ({result.get('before_brain')} → "
             f"{result.get('after_brain')})", ""]
    if result["miswired"]:
        lines.append("MISWIRED — a number that cannot move, moved:")
        lines += [f"  ! {m}" for m in result["miswired"]]
        lines.append("")
    if result["regressed"]:
        lines.append("Regressed:")
        lines += [f"  - {r}" for r in result["regressed"]]
        lines.append("")
    if result["notes"]:
        lines.append("Notes:")
        lines += [f"  · {n}" for n in result["notes"]]
    if result["verdict"] == "OK":
        lines.append("Deterministic numbers identical; no metric materially worse.")
    return "\n".join(lines)
