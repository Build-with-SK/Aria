"""
src/consult/bridge.py
=====================
ARIA's side of the consultation, orchestrated.

`sentinel_client.py` is the transport — one authenticated POST with a timeout.
This module is the JUDGEMENT: when is a second opinion worth asking for, what
happens to the answer, and what happens when there is no answer.

It exists because the client alone was never enough to make consultation real.
The client was written, the token was issued, port 8300 was chosen — and nothing
called it. A bridge with no traffic is not an integration; it is a plan.

TWO SYSTEMS, NOT ONE
--------------------
ARIA is the trading specialist. SENTINEL is a separate, more general
intelligence in its own process with its own memory and permissions. They are
not merged and must not be: ARIA imports no SENTINEL code, reads no SENTINEL
database, and holds no reference to its internals. The entire surface is HTTP.

THE RULES THIS MODULE ENFORCES
------------------------------
1. **Never blocking.** Consultation happens around a decision, never inside the
   path that has to complete. Every function here returns a result; none raises,
   and none is awaited by anything that would stall.

2. **Silence is not agreement.** If SENTINEL is unreachable, the verdict says so
   explicitly. An absent consultant must never read as approval — that is the
   single most dangerous way to fail, because it looks exactly like success.

3. **Asking is a decision with a cost.** `should_consult()` in the client is
   deterministic and is the gate. Consulting on everything would be as useless
   as consulting on nothing, and slower.

4. **Automatic consultation is opt-in.** `ARIA_SENTINEL_CONSULT=true` enables
   it on the trading path. It is OFF by default because SENTINEL may not be
   running, and a 120-second timeout inside a fifteen-minute loop is a way to
   turn an enhancement into an outage.

5. **It is advisory.** SENTINEL cannot approve or block a trade. ARIA proposes,
   the human approves; a consultant that could veto would be a second decision
   maker, and the whole point of the approval queue is that there is one.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

#: Automatic consultation on the trading path. Off unless explicitly enabled.
ENV_FLAG = "ARIA_SENTINEL_CONSULT"

#: Shorter than the client's 120s default. A consultant that takes two minutes
#: to answer has already missed the decision it was asked about.
TRADE_TIMEOUT = 45


def enabled() -> bool:
    return os.environ.get(ENV_FLAG, "").strip().lower() in ("1", "true", "yes")


def status() -> dict:
    """Everything a caller needs to know before relying on this."""
    from src.consult import sentinel_client as sc
    st = sc.status()
    st["auto_consult_enabled"] = enabled()
    st["auto_consult_flag"] = ENV_FLAG
    st["operational"] = bool(st["token_configured"] and st["reachable"])
    # `sentinel_status` is the field the UI reads. It is never "AGREES", and
    # when consultation is impossible it says so in a word rather than leaving
    # a caller to infer it from two booleans.
    st.setdefault("sentinel_status", sc.STATUS_AVAILABLE if st["operational"]
                  else sc.STATUS_UNAVAILABLE)
    if not st["token_configured"]:
        st["blocked_by"] = "no SENTINEL_CONSULT_TOKEN configured"
    elif not st["reachable"]:
        st["blocked_by"] = f"nothing answering at {st['url']}"
    else:
        st["blocked_by"] = None
    return st


def _publish(kind: str, summary: str, payload: Optional[dict] = None,
             severity: str = "info") -> None:
    """Record it on the event bus so a consultation is visible in the activity
    stream. An enhancement nobody can see having happened is unauditable."""
    try:
        from src.core.bus import publish
        publish(kind, summary[:180], source="sentinel_bridge",
                severity=severity, payload=payload or {})
    except Exception as e:            # pragma: no cover — never break a caller
        logger.debug("consult bridge: could not publish %s: %s", kind, e)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _audit(question: str, *, reason: str, context: str, result: dict,
           requested_at: str, human_approval_required: bool) -> dict:
    """The structured record of one consultation.

    Everything a later reader needs to judge whether the second opinion was
    worth having: why it was sought, what came back, how long it took, and —
    once `record_outcome` runs — whether ARIA took it.

    The four `aria_*` fields are None here on purpose. They are not knowable at
    the moment of asking, and leaving them absent would make a consultation
    record and an outcome record two different shapes. A reader that has to
    branch on which keys exist gets the branch wrong eventually.
    """
    from src.consult import sentinel_client as sc
    status = (result.get("sentinel_status")
              or sc.status_for(str(result.get("reason") or "")))
    return {
        "timestamp": requested_at,
        "question": str(question)[:500],
        "consultation_reason": reason,
        "aria_context": str(context)[:500],
        "sentinel_status": status,
        "sentinel_position": result.get("sentinel_position", sc.POSITION_NONE),
        "sentinel_confidence": result.get("confidence"),
        "latency_ms": result.get("latency_ms"),
        "request_id": result.get("request_id"),
        # The whole record in one boolean, for anything that needs to ask the
        # question quickly. False on every failure path, including the ones
        # where SENTINEL answered but said nothing usable.
        "second_opinion_obtained": status == sc.STATUS_AVAILABLE,
        "aria_agreed": None,
        "aria_changed_reasoning": None,
        "aria_final_decision": None,
        "human_approval_required": human_approval_required,
    }


def ask(question: str, *, context: str = "", hypothesis: str = "",
        evidence: Optional[list[str]] = None,
        uncertain_about: Optional[list[str]] = None,
        desired_output: Optional[str] = None,
        timeout: Optional[int] = None,
        gate: Optional[dict] = None,
        human_approval_required: bool = False) -> dict:
    """Ask SENTINEL a question. Always returns; never raises.

    `gate` is the keyword set for `should_consult()`. Pass it to have the
    decision made deterministically; omit it for an explicit human request,
    where the human asking IS the decision.

    Every path out of here carries `sentinel_status` and an `audit` record, so
    a caller never has to distinguish outcomes by matching on reason strings.
    """
    from src.consult import sentinel_client as sc
    try:
        return _ask(question, context=context, hypothesis=hypothesis,
                    evidence=evidence, uncertain_about=uncertain_about,
                    desired_output=desired_output, timeout=timeout, gate=gate,
                    human_approval_required=human_approval_required)
    except Exception as e:      # pragma: no cover — the last line of defence
        # A consultation is an enhancement. Nothing it does may reach a caller
        # as an exception, least of all the API layer, where it would surface
        # to the owner as a 500 on a page that was working a moment ago.
        logger.warning("consult bridge: ask() raised, absorbing: %s", e)
        return {"ok": False, "consulted": False, "reason": "error",
                "sentinel_status": sc.STATUS_ERROR,
                "sentinel_position": sc.POSITION_NONE,
                "why": str(e)[:200], "guidance": sc.SILENCE_GUIDANCE}


def _ask(question: str, *, context: str, hypothesis: str,
         evidence: Optional[list[str]], uncertain_about: Optional[list[str]],
         desired_output: Optional[str], timeout: Optional[int],
         gate: Optional[dict], human_approval_required: bool) -> dict:
    from src.consult import sentinel_client as sc

    requested_at = _now()
    reason = "explicitly requested"

    if gate is not None:
        worth_it, why = sc.should_consult(**gate)
        reason = why
        if not worth_it:
            # Not a failure. ARIA answering its own question is the normal and
            # cheapest outcome, and is not published — an event per unasked
            # question would bury the ones that were asked.
            return {"ok": False, "consulted": False, "reason": "not_warranted",
                    "sentinel_status": sc.STATUS_NOT_CONSULTED,
                    "sentinel_position": sc.POSITION_NONE, "why": why,
                    "guidance": "ARIA answered this itself; no second opinion "
                                "was needed."}

    def _unavailable(reason_code: str, why: str, guidance: str) -> dict:
        out = {"ok": False, "consulted": False, "reason": reason_code,
               "sentinel_status": sc.status_for(reason_code),
               "sentinel_position": sc.POSITION_NONE,
               "why": why, "guidance": guidance}
        out["audit"] = _audit(question, reason=reason, context=context,
                              result=out, requested_at=requested_at,
                              human_approval_required=human_approval_required)
        # Published as a warning, and published at all: an absent consultant
        # that leaves no trace is indistinguishable from one that agreed.
        _publish("SENTINEL_UNAVAILABLE",
                 f"SENTINEL {out['sentinel_status']}; ARIA proceeding on its "
                 f"own analysis", out["audit"], severity="warning")
        return out

    # Deliberately not sc.status(): that probes /health, and the liveness check
    # below does it again. Whether a token exists is answerable without leaving
    # the process.
    if not sc.token_configured():
        return _unavailable(
            "no_token", "no SENTINEL_CONSULT_TOKEN configured",
            "Consultation is unavailable. This is NOT agreement.")

    # Cheap liveness check before committing to the long timeout. Without it a
    # dead consultant costs every caller the full window.
    if not sc.is_available():
        return _unavailable(
            "unreachable", f"nothing answering at {sc.sentinel_url()}",
            sc.SILENCE_GUIDANCE)

    result = sc.consult(
        question, context=context, hypothesis=hypothesis,
        evidence=evidence, uncertain_about=uncertain_about,
        desired_output=desired_output or sc.ANALYSIS,
        timeout=timeout or sc.TIMEOUT)
    if not isinstance(result, dict):        # a stubbed client, defensively
        result = {"ok": False, "reason": "malformed"}

    result["consulted"] = bool(result.get("ok"))
    result["audit"] = _audit(question, reason=reason, context=context,
                             result=result, requested_at=requested_at,
                             human_approval_required=human_approval_required)

    if result["consulted"]:
        result["summary"] = sc.summarise(result)
        _publish("SENTINEL_CONSULTED", sc.summarise(result, max_points=2),
                 result["audit"], severity="notable")
    else:
        # Answered badly, or not at all. Same rule as silence.
        result.setdefault("guidance", sc.SILENCE_GUIDANCE)
        _publish("SENTINEL_UNAVAILABLE",
                 f"SENTINEL {result['audit']['sentinel_status']}; ARIA "
                 f"proceeding on its own analysis",
                 result["audit"], severity="warning")
    return result


def red_team_thesis(thesis: str, *, context: str = "",
                    evidence: Optional[list[str]] = None,
                    own_confidence: Optional[float] = None,
                    high_impact: bool = True,
                    timeout: int = TRADE_TIMEOUT) -> dict:
    """Ask SENTINEL to try to break a thesis before ARIA acts on it.

    The useful outcome is not approval. It is the list of things that would make
    the thesis wrong — which is why the request asks for objections rather than
    a verdict.
    """
    from src.consult import sentinel_client as sc
    return ask(
        f"Attempt to break this thesis: {thesis}",
        hypothesis=thesis, context=context, evidence=evidence,
        desired_output=sc.RED_TEAM, timeout=timeout,
        gate={"high_impact": high_impact, "own_confidence": own_confidence},
        human_approval_required=True)


def consider_trade(*, ticker: str, side: str, thesis: str,
                   conviction: Optional[float] = None,
                   evidence: Optional[list[str]] = None,
                   context: str = "") -> dict:
    """The trading-path call. Advisory only, and off unless enabled.

    A trade heading for the approval queue is the most expensive thing ARIA
    produces to reverse, which makes it the one decision worth a second opinion.
    What comes back is attached to the proposal for the HUMAN to read — it never
    changes whether the trade is proposed. SENTINEL is a consultant, not a
    second risk officer.
    """
    from src.consult import sentinel_client as sc
    if not enabled():
        return {"ok": False, "consulted": False, "reason": "disabled",
                "sentinel_status": sc.STATUS_NOT_CONSULTED,
                "sentinel_position": sc.POSITION_NONE,
                "why": f"{ENV_FLAG} is not set",
                "guidance": "Automatic consultation is off. ARIA's own analysis "
                            "stands unreviewed."}

    # conviction arrives 0-100 from the planner; should_consult speaks 0-1.
    own = None
    if conviction is not None:
        own = float(conviction) / 100.0 if conviction > 1 else float(conviction)

    try:
        out = red_team_thesis(
            thesis, context=context or f"{side} {ticker}",
            evidence=evidence, own_confidence=own, high_impact=True)
    except Exception as e:            # pragma: no cover — belt and braces
        logger.warning("consult bridge: red team raised for %s: %s", ticker, e)
        return {"ok": False, "consulted": False, "reason": "error",
                "sentinel_status": sc.STATUS_ERROR,
                "sentinel_position": sc.POSITION_NONE,
                "why": str(e)[:200],
                "guidance": "Consultation failed. This is NOT agreement."}

    out["ticker"] = ticker
    return out


def record_outcome(request_id: str, accepted: bool,
                   what_happened: str = "", *,
                   changed_reasoning: Optional[bool] = None,
                   final_decision: str = "") -> dict:
    """Tell SENTINEL whether ARIA took the advice, and record it locally.

    Rejection is a legitimate outcome and worth recording: it is the only signal
    either system gets about whether the bridge earns its cost.

    The local event is published whether or not SENTINEL is reachable to hear
    it. ARIA's audit trail is ARIA's, and it must not develop holes on the days
    its consultant is down.
    """
    from src.consult import sentinel_client as sc
    if not request_id:
        return {"ok": False, "reason": "no_request_id"}

    record = {
        "timestamp": _now(),
        "request_id": request_id,
        "aria_agreed": bool(accepted),
        "aria_changed_reasoning": changed_reasoning,
        "aria_final_decision": final_decision or what_happened,
        "what_happened": what_happened,
    }
    try:
        result = sc.report_outcome(request_id, accepted, what_happened)
    except Exception as e:      # pragma: no cover
        logger.warning("consult bridge: report_outcome raised: %s", e)
        result = {"ok": False, "reason": "error", "detail": str(e)[:200]}
    record["reported_to_sentinel"] = bool(result.get("ok"))

    _publish("SENTINEL_OUTCOME",
             f"ARIA {'accepted' if accepted else 'rejected'} SENTINEL's analysis",
             record)
    return {**result, "record": record}
