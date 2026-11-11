"""
src/intelligence/conviction.py
==============================
What a prediction may tell the portfolio-risk layer — decomposed, never
collapsed into one number.

    direction               LONG / SHORT / FLAT
    directional_edge        the source's own strength (desk conviction points,
                            V5's edge over a coin) — its units stated
    probability             the number and its TYPE (semantics.ProbabilityType)
    calibrated_probability  UNKNOWN until a validated calibration exists
    calibration_quality     the model's calibration status
    model_reliability       the model's graded directional record: rate, CI,
                            n, verdict — or INSUFFICIENT_DATA
    disagreement            whether the inputs behind the call agree
    data_quality            GOOD / DEGRADED / POOR, and why
    regime_fit              whether the model has a measurable record in the
                            current regime
    routing                 the routing decision that applies

There is deliberately no `confidence = 0.74`. A final score would have to
weigh reliability against disagreement against data quality, and there is no
evidence to set those weights; inventing them is how an opaque number gets
treated as fact. Every component is kept, and `labels()` says in words what a
reader must not assume (UNCALIBRATED, RELIABILITY UNKNOWN, ...).

A conviction is CONTEXT, not permission. `may_increase_size` is always False,
and the only effects it can have on a trade (src/risk/pretrade.assess) are:

    REJECT  the prediction does not support the trade's side (FLAT, or the
            opposite direction) — a prediction cannot justify a trade it
            does not make
    DEFER   its data are POOR (expired horizon, stale inputs): re-evaluate,
            never send blind

It never makes a trade larger, never turns a rejection into an acceptance and
never bypasses the session check or any limit.

Building one is bounded and local: it reads a debate transcript (JSON), the
signals file's age and the last evidence snapshot. No language model, no
network, no ledger scan on the order path.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from src.intelligence import semantics

KNOWN = "KNOWN"
UNKNOWN = "UNKNOWN"
INSUFFICIENT = "INSUFFICIENT_DATA"
NOT_APPLICABLE = "NOT_APPLICABLE"

GOOD, DEGRADED, POOR = "GOOD", "DEGRADED", "POOR"
_QUALITY_RANK = {GOOD: 0, DEGRADED: 1, POOR: 2, UNKNOWN: 1}

#: The ledger's trading-day -> calendar-day rule: a 1-day call has expired
#: once 1.4 calendar days have passed since it was made.
CALENDAR_PER_TRADING_DAY = 7 / 5
SIGNALS_STALE_HOURS = 30          # = src/core/world budget for signals.json

_DEBATE_ID = re.compile(r"\b(dbt-[0-9a-f]{8})\b")
_SIDE_OF = {"LONG": "buy", "SHORT": "sell"}
_DIRECTION_OF = {"bull": "LONG", "bear": "SHORT"}


@dataclass(frozen=True)
class Component:
    value: Any = None
    status: str = UNKNOWN
    basis: str = ""
    detail: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Conviction:
    symbol: str
    direction: str
    as_of: str
    model_id: str
    model_version: Optional[str]
    prediction_ref: Optional[str]
    horizon_days: Optional[int]
    probability_type: str
    directional_edge: Component
    probability: Component
    calibrated_probability: Component
    calibration_quality: Component
    model_reliability: Component
    disagreement: Component
    data_quality: Component
    regime_fit: Component
    routing: dict
    #: The research thesis for the instrument, if one was built at or before
    #: as_of (src/intelligence/research). CONTEXT ONLY: recorded with the risk
    #: decision, never a verdict input, never a size input.
    research_context: dict = field(default_factory=lambda: dict(NO_RESEARCH))
    may_increase_size: bool = False

    def labels(self) -> list[str]:
        out = []
        if self.calibrated_probability.status != KNOWN:
            out.append("UNCALIBRATED")
        if self.probability_type not in {t.value for t in semantics.SCORABLE}:
            out.append(f"NOT A PROBABILITY ({self.probability_type})")
        if self.model_reliability.status != KNOWN:
            out.append("RELIABILITY " + ("INSUFFICIENT DATA"
                                         if self.model_reliability.status == INSUFFICIENT
                                         else "UNKNOWN"))
        elif (self.model_reliability.detail or {}).get("verdict") != "better than chance":
            out.append("NO DEMONSTRATED EDGE")
        if self.disagreement.value == "HIGH":
            out.append("INPUTS DISAGREE")
        if self.data_quality.value in (DEGRADED, POOR):
            out.append(f"DATA {self.data_quality.value}")
        if self.regime_fit.status != KNOWN:
            out.append("REGIME FIT UNKNOWN")
        if (self.research_context or {}).get("state") == "CONTRADICTS":
            out.append("RESEARCH CONTRADICTS")
        if not (self.routing or {}).get("routed"):
            out.append((self.routing or {}).get("reason_code", "ROUTING_INSUFFICIENT_EVIDENCE"))
        return out

    def to_dict(self) -> dict:
        d = asdict(self)
        d["labels"] = self.labels()
        d["may_increase_size"] = False
        return d


# ── what a conviction can do to a trade (pure; mirrored in pretrade) ────────

def blocking(conviction: Optional[dict], side: str) -> list[tuple[str, str]]:
    """[(verdict, reason)] a conviction imposes on an ENTRY of `side`.

    Pure function of the dict, so src/risk/pretrade applies exactly this rule
    without importing this package.
    """
    if not conviction:
        return []
    out = []
    direction = conviction.get("direction")
    if direction not in _SIDE_OF:
        out.append(("REJECT", f"the prediction is {direction or 'missing'}: it supports "
                              f"no entry"))
    elif _SIDE_OF[direction] != (side or "").lower():
        out.append(("REJECT", f"the prediction is {direction}; a {side} entry is not "
                              f"what it says"))
    dq = (conviction.get("data_quality") or {})
    if dq.get("value") == POOR:
        out.append(("DEFER", f"prediction data POOR: {dq.get('basis') or 'unspecified'}"))
    return out


# ── builders ────────────────────────────────────────────────────────────────

def _aware(at) -> datetime:
    if isinstance(at, str):
        at = datetime.fromisoformat(at)
    if at.tzinfo is None:
        at = at.astimezone()
    return at


def _reliability(snapshot: Optional[dict], model_id: str) -> Component:
    if not snapshot:
        return Component(None, UNKNOWN, "no evidence snapshot available")
    ev = (snapshot.get("evaluations") or {}).get(model_id)
    if not ev:
        return Component(None, INSUFFICIENT, "no graded record for this model")
    da = ev.get("directional_accuracy") or {}
    if da.get("status") != "MEASURED":
        return Component(None, INSUFFICIENT,
                         f"{da.get('n', 0)} directional graded calls "
                         f"(need {da.get('n_required')})", {"n": da.get("n")})
    return Component(da["rate"], KNOWN,
                     f"directional hit rate over {da['n']} graded calls on "
                     f"{da['n_clusters']} dates (ledger, canonical dataset)",
                     {"n": da["n"], "ci95_cluster": da.get("ci95_cluster"),
                      "verdict": da.get("verdict"),
                      "snapshot_built_at": snapshot.get("built_at")})


def _calibration(snapshot: Optional[dict], model_id: str) -> Component:
    cal = ((snapshot or {}).get("calibration") or {}).get(model_id) or {}
    if not cal:
        return Component(None, UNKNOWN, "no calibration status available")
    return Component(cal.get("status"), KNOWN,
                     cal.get("why") or "", {"calibration_version": cal.get("calibration_version")})


def _regime_fit(snapshot: Optional[dict], model_id: str, regime: Optional[str]) -> Component:
    ev = ((snapshot or {}).get("evaluations") or {}).get(model_id) or {}
    blk = ev.get("by_regime_macro") or {}
    if blk.get("status") != "MEASURED":
        return Component(None, INSUFFICIENT,
                         blk.get("why") or "no regime-conditional record",
                         {"regime_now": regime})
    sl = (blk.get("slices") or {}).get(regime or "")
    if not sl or sl.get("status") != "MEASURED":
        return Component(None, INSUFFICIENT, f"no measurable record in {regime}",
                         {"regime_now": regime})
    return Component(sl.get("rate"), KNOWN, f"directional rate in {regime}",
                     {"regime_now": regime, "verdict": sl.get("verdict")})


def _routing(snapshot: Optional[dict], asset_class: Optional[str], horizon_days) -> dict:
    from src.intelligence import routing
    return routing.lookup((snapshot or {}).get("routing_table"), asset_class, horizon_days)


def _disagreement(analysts: dict) -> Component:
    voting = {a: v for a, (v, _c) in analysts.items()
              if a != "macro" and v in ("bull", "bear")}
    sides = set(voting.values())
    detail = {"votes": voting,
              "abstained": sorted(a for a, (v, _c) in analysts.items()
                                  if a != "macro" and v not in ("bull", "bear"))}
    macro = analysts.get("macro", (None, None))[0]
    if macro in ("bull", "bear"):
        detail["macro"] = macro
    if len(sides) > 1:
        return Component("HIGH", KNOWN, "the voting analysts point opposite ways", detail)
    if len(voting) >= 2:
        return Component("LOW", KNOWN, f"{len(voting)} voting analysts agree", detail)
    if len(voting) == 1:
        return Component("SINGLE_SOURCE", UNKNOWN,
                         "only one analyst voted; agreement is not measurable", detail)
    return Component(None, UNKNOWN, "no analyst took a side", detail)


def _worst(*qualities: tuple[str, str]) -> Component:
    q = max(qualities, key=lambda kv: _QUALITY_RANK.get(kv[0], 1))
    reasons = [why for v, why in qualities if v != GOOD and why]
    return Component(q[0], KNOWN if q[0] != UNKNOWN else UNKNOWN,
                     "; ".join(reasons) or "inputs fresh, horizon not elapsed")


NO_RESEARCH = {"state": "UNKNOWN", "basis": "no research thesis for this instrument"}
RESEARCH_MAX_AGE_HOURS = 24


def research_context(direction: str, thesis: Optional[dict], at: datetime) -> dict:
    """How a cached research thesis relates to a prediction's direction.

    AGREES / CONTRADICTS / NEUTRAL, or UNKNOWN when there is no thesis, it was
    built after `at` (it could not have been known), or it is older than
    RESEARCH_MAX_AGE_HOURS. Context for a reader and the decision record —
    nothing reads it to decide or size.
    """
    if not thesis:
        return dict(NO_RESEARCH)
    try:
        built = _aware(thesis.get("built_at"))
    except (TypeError, ValueError):
        return {"state": "UNKNOWN", "basis": "thesis has no readable build time"}
    if built > at:
        return {"state": "UNKNOWN", "basis": "the thesis was built after this instant"}
    age_h = (at - built).total_seconds() / 3600
    if age_h > RESEARCH_MAX_AGE_HOURS:
        return {"state": "UNKNOWN", "basis": f"the thesis is {age_h:.0f} h old "
                                             f"(limit {RESEARCH_MAX_AGE_HOURS} h)"}
    td = thesis.get("direction")
    want = {"LONG": "BULLISH", "SHORT": "BEARISH"}.get(direction)
    state = ("NEUTRAL" if td == "NEUTRAL" or want is None else
             "AGREES" if td == want else "CONTRADICTS")
    return {"state": state, "thesis_direction": td, "thesis_basis": thesis.get("basis"),
            "built_at": thesis.get("built_at"), "age_hours": round(age_h, 2),
            "scorecard": thesis.get("scorecard"), "summary": thesis.get("summary"),
            "effect": "context only: recorded with the decision; no verdict or size "
                      "reads it"}


def from_debate(transcript: dict, *, as_of=None, snapshot: Optional[dict] = None,
                signals_age_hours: Optional[float] = None,
                research: Optional[dict] = None) -> Conviction:
    """The conviction behind a desk trade, from its debate transcript."""
    from src.intelligence.dataset import instrument_of
    now = _aware(as_of or datetime.now(timezone.utc))
    judge = transcript.get("judge") or {}
    view = judge.get("view")
    analysts = {o.get("agent"): (o.get("view"), o.get("conviction"))
                for o in transcript.get("opinions") or [] if o.get("agent")}
    ticker = transcript.get("ticker") or ""
    asset_class, _venue = instrument_of(ticker) if ticker else (None, None)
    horizon = 1

    q = []
    try:
        made = _aware(transcript.get("at"))
        age_h = (now - made).total_seconds() / 3600
        expiry_h = horizon * CALENDAR_PER_TRADING_DAY * 24
        q.append((POOR, f"debate is {age_h:.1f} h old: its {horizon}-day horizon has "
                        f"elapsed") if age_h > expiry_h else (GOOD, ""))
    except (TypeError, ValueError):
        q.append((POOR, "debate time unreadable"))
    if signals_age_hours is None:
        q.append((UNKNOWN, "signals.json age unknown"))
    elif signals_age_hours > SIGNALS_STALE_HOURS:
        q.append((DEGRADED, f"signals.json is {signals_age_hours:.0f} h old "
                            f"(budget {SIGNALS_STALE_HOURS} h)"))
    else:
        q.append((GOOD, ""))

    from src.core.versioning import model_version
    try:
        version = model_version("desk-debate", "src/desk", llm=transcript.get("llm"))
    except Exception:                            # pragma: no cover - defensive
        version = None
    return Conviction(
        symbol=ticker, direction=_DIRECTION_OF.get(view, "FLAT"),
        as_of=now.isoformat(), model_id="desk", model_version=version,
        prediction_ref=transcript.get("id"), horizon_days=horizon,
        probability_type=semantics.ProbabilityType.CONFIDENCE.value,
        directional_edge=Component(judge.get("conviction"), KNOWN if judge.get("conviction")
                                   is not None else UNKNOWN,
                                   f"judge conviction points 0-100 against a bar of "
                                   f"{judge.get('conviction_bar')} (a CONFIDENCE)",
                                   {"verdict": judge.get("verdict")}),
        probability=Component(None, NOT_APPLICABLE,
                              "the desk states no probability; conviction is a CONFIDENCE"),
        calibrated_probability=Component(None, UNKNOWN, "no validated calibration "
                                                        "(calibration_version none)"),
        calibration_quality=_calibration(snapshot, "desk"),
        model_reliability=_reliability(snapshot, "desk"),
        disagreement=_disagreement(analysts),
        data_quality=_worst(*q),
        regime_fit=_regime_fit(snapshot, "desk", judge.get("regime")),
        routing=_routing(snapshot, asset_class, horizon),
        research_context=research_context(_DIRECTION_OF.get(view, "FLAT"), research, now),
    )


def debate_id_in(text: Optional[str]) -> Optional[str]:
    m = _DEBATE_ID.search(text or "")
    return m.group(1) if m else None


def for_queued_trade(trade, *, as_of=None, snapshot: Optional[dict] = None) -> Optional[dict]:
    """The conviction for an approval-queue entry that came from a debate, as a
    dict for ProposedOrder — or None when the trade names no debate (a manual
    proposal), in which case the risk layer behaves exactly as before."""
    did = debate_id_in(getattr(trade, "situation", "") or "")
    if not did:
        return None
    from src.desk.debate import load_debate
    transcript = load_debate(did)
    if not transcript:
        return None
    try:
        from src.desk.opinion import signals_freshness
        age = signals_freshness().get("age_hours")
    except Exception:
        age = None
    if snapshot is None:
        from src.intelligence import evidence
        snapshot = evidence.load()
    try:
        from src.intelligence.research.engine import load_latest
        research = load_latest(transcript.get("ticker") or "")
    except Exception:
        research = None
    return from_debate(transcript, as_of=as_of, snapshot=snapshot, research=research,
                       signals_age_hours=age).to_dict()


def from_ledger_row(row: dict, *, as_of=None, snapshot: Optional[dict] = None) -> Conviction:
    """The conviction a recorded prediction carries (API / UI)."""
    from src.intelligence.dataset import instrument_of
    now = _aware(as_of or datetime.now(timezone.utc))
    ptype = semantics.type_of(row)
    p, why_not = semantics.scorable_probability(row)
    model = row.get("source") or "unknown"
    symbol = row.get("provider_symbol") or row.get("subject") or ""
    asset_class, _ = instrument_of(symbol) if symbol else (None, None)
    q = []
    try:
        made = _aware(row.get("created_at"))
        expiry = made + timedelta(days=int(row.get("horizon_days") or 0) * CALENDAR_PER_TRADING_DAY)
        q.append((POOR, "the prediction's horizon has elapsed") if now > expiry else (GOOD, ""))
    except (TypeError, ValueError):
        q.append((POOR, "creation time unreadable"))
    if row.get("data_as_of"):
        q.append((GOOD, ""))
    else:
        q.append((UNKNOWN, "data_as_of not recorded"))
    edge = None
    if p is not None:
        edge = round(p - 0.5, 4)
    return Conviction(
        symbol=symbol, direction=_DIRECTION_OF.get((row.get("direction") or "").lower(), "FLAT"),
        as_of=now.isoformat(), model_id=model, model_version=row.get("model_version"),
        prediction_ref=row.get("id"), horizon_days=row.get("horizon_days"),
        probability_type=ptype.value,
        directional_edge=Component(edge, KNOWN if edge is not None else UNKNOWN,
                                   "stated P(right) - 0.5" if edge is not None
                                   else "no stated probability to measure an edge from"),
        probability=Component(p, KNOWN if p is not None else NOT_APPLICABLE,
                              why_not or "stated P(direction right)"),
        calibrated_probability=Component(None, UNKNOWN, "no validated calibration "
                                                        "(calibration_version none)"),
        calibration_quality=_calibration(snapshot, model),
        model_reliability=_reliability(snapshot, model),
        disagreement=Component(None, UNKNOWN, "a single model's call; see the "
                                              "cross-model view"),
        data_quality=_worst(*q),
        regime_fit=_regime_fit(snapshot, model, row.get("regime_at")),
        routing=_routing(snapshot, asset_class, row.get("horizon_days")),
    )


def cross_model(convictions: list[Conviction]) -> Component:
    """Do the latest calls from different models on one instrument agree?"""
    live = [c for c in convictions if c.data_quality.value != POOR and c.direction != "FLAT"]
    sides = {c.direction for c in live}
    detail = {c.model_id: c.direction for c in convictions}
    if len(live) < 2:
        return Component(None, UNKNOWN, "fewer than two unexpired directional calls", detail)
    return (Component("HIGH", KNOWN, "models point opposite ways", detail) if len(sides) > 1
            else Component("LOW", KNOWN, f"{len(live)} models agree", detail))
