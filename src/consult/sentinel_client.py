"""
src/consult/sentinel_client.py
==============================
ARIA's side of the consultation bridge.

SENTINEL is a separate general intelligence running in its own process, with its
own memory and its own permissions. ARIA does not import it, does not read its
database, and does not depend on it. This module is the whole of the coupling:
one HTTP call, authenticated, with a timeout.

THE RULE THIS FILE EXISTS TO ENFORCE
------------------------------------
If SENTINEL is unavailable, ARIA continues working. Every function here fails
soft and returns a result whose `ok` is False — never raises into a caller, never
blocks a trading cycle, never retries in a loop. A specialist that stops working
because its consultant is offline was never independent to begin with.

WHEN TO CALL
------------
Consultation costs time and tokens. Ask only when the answer would change what
ARIA does:

  - an unfamiliar problem outside the trading domain
  - conflicting evidence ARIA cannot resolve itself
  - an anomaly that may indicate a broken assumption
  - a thesis important enough to be worth attacking before acting on it
  - a technical or architectural problem
  - a suspicion that ARIA's own reasoning is wrong

Do not consult for anything ARIA already knows how to answer. `should_consult`
below encodes that judgement so it is made consistently rather than by whichever
caller happens to feel uncertain.

WHAT COMES BACK
---------------
An independent analysis that may disagree with ARIA. Disagreement is the point.
ARIA is free to reject it — and should record that it did, via `report_outcome`,
because that is the only signal either system gets about whether the bridge is
worth its cost.
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent.parent.parent
KEYS_FILE = ROOT / "data" / "research_keys.json"

DEFAULT_URL = "http://127.0.0.1:8300"
TIMEOUT = 120

ANALYSIS = "ANALYSIS"
VERIFY = "VERIFY"
RED_TEAM = "RED_TEAM"
TECHNICAL = "TECHNICAL"

# ---------------------------------------------------------------------------
# THE STATE OF THE CONSULTANT
#
# Every consultation resolves to exactly one of these, and none of them is
# "AGREES". That is the entire point of naming them: before this existed a
# caller distinguished outcomes by reading ad-hoc reason strings, and the
# difference between "SENTINEL said nothing" and "SENTINEL raised nothing"
# lived only in whoever happened to be reading the log.
# ---------------------------------------------------------------------------
STATUS_AVAILABLE = "AVAILABLE"          # answered, and the answer parsed
STATUS_UNAVAILABLE = "UNAVAILABLE"      # nothing at the other end
STATUS_TIMEOUT = "TIMEOUT"              # something there, too slow to matter
STATUS_MALFORMED = "MALFORMED"          # answered with something unusable
STATUS_UNAUTHORISED = "UNAUTHORISED"    # the token was refused
STATUS_NO_TOKEN = "NO_TOKEN"            # no token to offer
STATUS_ERROR = "ERROR"                  # anything else that went wrong
STATUS_NOT_CONSULTED = "NOT_CONSULTED"  # never asked — gated off or disabled

#: Statuses in which ARIA obtained no second opinion. Kept as a set so callers
#: test membership rather than re-deriving the list and getting it wrong.
NO_OPINION = frozenset({
    STATUS_UNAVAILABLE, STATUS_TIMEOUT, STATUS_MALFORMED,
    STATUS_UNAUTHORISED, STATUS_NO_TOKEN, STATUS_ERROR, STATUS_NOT_CONSULTED,
})

# ---------------------------------------------------------------------------
# WHAT SENTINEL SAID
#
# The position is advisory in the strongest sense: nothing in ARIA branches on
# it. It exists so a human reading a proposal can see at a glance whether the
# consultant pushed back, without reading eight hundred characters of prose.
# ---------------------------------------------------------------------------
POSITION_AGREE = "AGREE"
POSITION_DISAGREE = "DISAGREE"
POSITION_UNCERTAIN = "UNCERTAIN"
POSITION_INSUFFICIENT = "INSUFFICIENT_EVIDENCE"
POSITION_ALTERNATIVE = "ALTERNATIVE_HYPOTHESIS"
POSITION_WARNING = "WARNING"
POSITION_NONE = "NONE"                  # no position taken — NOT agreement

POSITIONS = frozenset({
    POSITION_AGREE, POSITION_DISAGREE, POSITION_UNCERTAIN,
    POSITION_INSUFFICIENT, POSITION_ALTERNATIVE, POSITION_WARNING,
    POSITION_NONE,
})

#: The one sentence every failure path returns. Written once because the rule
#: it states is the one most likely to be quietly dropped by a future edit.
SILENCE_GUIDANCE = ("SENTINEL did not answer. This is NOT agreement. Proceed "
                    "on ARIA's own analysis and note that no second opinion "
                    "was obtained.")

_STATUS_BY_REASON = {
    "no_token": STATUS_NO_TOKEN,
    "unreachable": STATUS_UNAVAILABLE,
    "timeout": STATUS_TIMEOUT,
    "malformed": STATUS_MALFORMED,
    "unauthorised": STATUS_UNAUTHORISED,
    "not_warranted": STATUS_NOT_CONSULTED,
    "disabled": STATUS_NOT_CONSULTED,
}


def status_for(reason: str) -> str:
    """Map a failure reason onto a status. Unknown reasons are errors, never
    availability — an unrecognised failure must not decay into agreement."""
    return _STATUS_BY_REASON.get(reason or "", STATUS_ERROR)


def read_position(data: dict) -> str:
    """Normalise SENTINEL's stance into one of `POSITIONS`.

    AGREE is only ever returned when SENTINEL states it. It is deliberately not
    inferred from the absence of objections: an empty answer and an endorsement
    are the same shape, and treating them alike is the exact failure this whole
    module exists to prevent.
    """
    if not isinstance(data, dict):
        return POSITION_NONE
    stated = str(data.get("position") or data.get("stance") or "").strip().upper()
    if stated in POSITIONS:
        return stated
    if _nonempty_list(data.get("counter_arguments")):
        return POSITION_DISAGREE
    if _nonempty_list(data.get("warnings")):
        return POSITION_WARNING
    if _nonempty_list(data.get("alternative_hypotheses")):
        return POSITION_ALTERNATIVE
    if _nonempty_list(data.get("uncertainties")):
        return POSITION_UNCERTAIN
    return POSITION_NONE


def _nonempty_list(value: Any) -> bool:
    return isinstance(value, (list, tuple)) and len(value) > 0


def sentinel_url() -> str:
    return (os.environ.get("SENTINEL_URL") or DEFAULT_URL).rstrip("/")


# ---------------------------------------------------------------------------
# WHAT MAY LEAVE
#
# SENTINEL is a separate system on the other side of a socket. It gets the
# question and nothing else it was not given deliberately.
#
# The risk is not that a caller passes a key on purpose — it is that one passes
# an error message, a traceback, or a config dump as `context`, and a
# credential rides along inside it. That is a plausible accident rather than a
# theoretical one, so the payload is scrubbed on the way out rather than
# trusted to every present and future caller.
# ---------------------------------------------------------------------------
_SECRET_HINTS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD", "CREDENTIAL")

#: Below this length a value is too short to be a credential and too likely to
#: be a common word; redacting it would corrupt ordinary prose.
_MIN_SECRET_LEN = 8


def _secret_values() -> list[str]:
    """Live secret values visible to this process, longest first so that a
    value containing another is replaced before its own substring is."""
    found = []
    for name, value in os.environ.items():
        if any(hint in name.upper() for hint in _SECRET_HINTS):
            v = (value or "").strip()
            if len(v) >= _MIN_SECRET_LEN:
                found.append(v)
    token = _token()
    if len(token) >= _MIN_SECRET_LEN:
        found.append(token)
    return sorted(set(found), key=len, reverse=True)


def _redact(value: Any) -> Any:
    """Strip live secrets out of anything outbound. Recurses into the lists the
    payload carries, because evidence is where a pasted traceback would land."""
    secrets = _secret_values()
    if not secrets:
        return value

    def scrub(item: Any) -> Any:
        if isinstance(item, str):
            for secret in secrets:
                if secret in item:
                    item = item.replace(secret, "[REDACTED]")
            return item
        if isinstance(item, list):
            return [scrub(x) for x in item]
        if isinstance(item, dict):
            return {k: scrub(v) for k, v in item.items()}
        return item

    return scrub(value)


def _token() -> str:
    """
    The shared secret SENTINEL issued. Never generated here — if it is absent,
    consultation is simply unavailable, which is a normal operating state.
    """
    env = os.environ.get("SENTINEL_CONSULT_TOKEN", "").strip()
    if env:
        return env
    if KEYS_FILE.exists():
        try:
            return str(json.loads(KEYS_FILE.read_text(encoding="utf-8"))
                       .get("SENTINEL_CONSULT_TOKEN", "")).strip()
        except (OSError, ValueError):
            pass
    return ""


def token_configured() -> bool:
    """Whether a consultation token exists at all — answered locally.

    Callers used to learn this from status(), which also probes /health, so a
    consultation paid for two liveness checks: one to discover it had a token
    and one to discover whether anyone was listening. With an absent consultant
    that was eight seconds of timeout on the trading path to establish the same
    fact twice.
    """
    return bool(_token())


def _post(path: str, payload: dict, timeout: int = TIMEOUT) -> dict:
    token = _token()
    if not token:
        return {"ok": False, "reason": "no_token", "status": STATUS_NO_TOKEN,
                "detail": ("no SENTINEL_CONSULT_TOKEN configured; consultation "
                           "is unavailable and ARIA proceeds on its own analysis")}
    req = urllib.request.Request(
        f"{sentinel_url()}{path}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json",
                 "X-Sentinel-Consult-Token": token})
    started = time.monotonic()

    def _elapsed() -> float:
        # One decimal, not a whole number: a loopback consultation completes in
        # well under a millisecond, and truncating that to 0 makes a real
        # measurement look like a missing one.
        return round((time.monotonic() - started) * 1000, 1)

    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read()[:300].decode("utf-8", "replace")
        reason = "unauthorised" if exc.code in (401, 403) else f"http_{exc.code}"
        return {"ok": False, "reason": reason, "latency_ms": _elapsed(),
                "status": (STATUS_UNAUTHORISED if reason == "unauthorised"
                           else STATUS_ERROR),
                "detail": detail}
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        # A consultant that is slow and a consultant that is absent are
        # different operational facts — one says fix the network, the other
        # says raise the timeout — so they are not folded into one reason.
        # Both are logged at info: ARIA running without a consultant is a
        # normal state, not a fault.
        timed_out = _is_timeout(exc)
        logger.info("SENTINEL %s (%s) — continuing without consultation",
                    "timed out" if timed_out else "unavailable", exc)
        return {"ok": False, "latency_ms": _elapsed(),
                "reason": "timeout" if timed_out else "unreachable",
                "status": STATUS_TIMEOUT if timed_out else STATUS_UNAVAILABLE,
                "detail": str(exc)[:200]}

    # Answered — but an answer ARIA cannot read is not an answer. Anything that
    # is not a JSON object is refused here rather than splatted into a dict two
    # frames up, where it used to raise TypeError straight through the caller.
    try:
        data = json.loads(body)
    except ValueError as exc:
        logger.info("SENTINEL returned unparseable body — ignoring (%s)", exc)
        return {"ok": False, "reason": "malformed", "status": STATUS_MALFORMED,
                "latency_ms": _elapsed(),
                "detail": f"response was not JSON: {str(exc)[:120]}"}
    if not isinstance(data, dict):
        logger.info("SENTINEL returned %s, not an object — ignoring",
                    type(data).__name__)
        return {"ok": False, "reason": "malformed", "status": STATUS_MALFORMED,
                "latency_ms": _elapsed(),
                "detail": f"response was {type(data).__name__}, not an object"}
    return {"ok": True, "data": data, "status": STATUS_AVAILABLE,
            "latency_ms": _elapsed()}


def _is_timeout(exc: BaseException) -> bool:
    """urllib wraps a socket timeout in URLError, so the type alone is not
    enough to tell a slow consultant from an absent one."""
    if isinstance(exc, TimeoutError):
        return True
    reason = getattr(exc, "reason", None)
    if isinstance(reason, TimeoutError):
        return True
    return "timed out" in str(exc).lower()


def is_available(timeout: int = 4) -> bool:
    """Cheap liveness check. Never raises."""
    try:
        with urllib.request.urlopen(f"{sentinel_url()}/health", timeout=timeout) as r:
            return json.loads(r.read()).get("status") == "ok"
    except Exception:
        return False


def should_consult(*, unfamiliar: bool = False, conflicting_evidence: bool = False,
                   anomaly: bool = False, high_impact: bool = False,
                   outside_domain: bool = False,
                   own_confidence: Optional[float] = None) -> tuple[bool, str]:
    """
    Decide whether this is worth asking about. Deterministic on purpose — using
    a language model to decide whether to use a language model is a cost with no
    corresponding gain.

    Returns (should_ask, why).
    """
    if outside_domain:
        return True, "the question is outside ARIA's trading domain"
    if conflicting_evidence:
        return True, "the evidence conflicts and ARIA cannot resolve it alone"
    if anomaly:
        return True, "an anomaly may indicate a broken assumption"
    if high_impact and (own_confidence is None or own_confidence < 0.75):
        return True, "high-impact decision held with less than firm confidence"
    if unfamiliar:
        return True, "the problem is unfamiliar"
    if own_confidence is not None and own_confidence < 0.35:
        return True, "ARIA's own confidence is too low to act on"
    return False, "ARIA can answer this itself; consultation would cost without informing"


def consult(question: str, *, problem: str = "", context: str = "",
            hypothesis: str = "", evidence: Optional[list[str]] = None,
            counter_evidence: Optional[list[str]] = None,
            already_tried: Optional[list[str]] = None,
            uncertain_about: Optional[list[str]] = None,
            desired_output: str = ANALYSIS, urgency: str = "NORMAL",
            timeout: int = TIMEOUT) -> dict:
    """
    Ask SENTINEL for an independent view.

    Returns {"ok": bool, ...}. On failure, `ok` is False and ARIA must proceed on
    its own analysis — never treat an unavailable consultant as agreement.
    """
    payload = {
        "caller": "ARIA",
        "question": question,
        "problem": problem,
        "aria_context": context,
        "current_hypothesis": hypothesis,
        "evidence": evidence or [],
        "counter_evidence": counter_evidence or [],
        "what_aria_already_tried": already_tried or [],
        "what_aria_is_uncertain_about": uncertain_about or [],
        "desired_output": desired_output,
        "urgency": urgency,
    }
    # Scrubbed as one object rather than field by field: a future field added
    # to the payload is then covered by default instead of by remembering.
    payload = _redact(payload)

    result = _post("/api/consult", payload, timeout=timeout)
    if not result.get("ok"):
        reason = result.get("reason", "error")
        return {"ok": False, "reason": reason,
                "sentinel_status": result.get("status") or status_for(reason),
                "sentinel_position": POSITION_NONE,
                "latency_ms": result.get("latency_ms"),
                "detail": result.get("detail", ""),
                "guidance": SILENCE_GUIDANCE}

    data = result.get("data")
    if not isinstance(data, dict):
        # Reachable only when a caller has stubbed `_post`; the real transport
        # already refuses this. Kept because the check costs nothing and the
        # cost of missing it was a TypeError raised through the API layer.
        return {"ok": False, "reason": "malformed",
                "sentinel_status": STATUS_MALFORMED,
                "sentinel_position": POSITION_NONE,
                "latency_ms": result.get("latency_ms"),
                "detail": f"response was {type(data).__name__}, not an object",
                "guidance": SILENCE_GUIDANCE}

    # SENTINEL's own fields go in FIRST so ARIA's overwrite them. A consultant
    # must not be able to set `ok`, its own status, or its own position by
    # putting those keys in its reply: the advisor does not get to describe
    # the advice as accepted.
    return {**data, "ok": True,
            "sentinel_status": STATUS_AVAILABLE,
            "sentinel_position": read_position(data),
            "latency_ms": result.get("latency_ms")}


def red_team(thesis: str, *, evidence: Optional[list[str]] = None,
             counter_evidence: Optional[list[str]] = None,
             context: str = "", timeout: int = TIMEOUT) -> dict:
    """
    Ask SENTINEL to actively try to break a thesis before ARIA acts on it.

    Worth spending on any decision that is expensive to reverse. The useful
    outcome is not approval — it is the list of things that would make it wrong.
    """
    return consult(
        f"Attempt to break this thesis: {thesis}",
        hypothesis=thesis, context=context, evidence=evidence,
        counter_evidence=counter_evidence, desired_output=RED_TEAM,
        timeout=timeout)


def report_outcome(request_id: str, accepted: bool, what_happened: str = "") -> dict:
    """
    Tell SENTINEL whether ARIA accepted the analysis and what followed.

    ARIA rejecting a consultation is a legitimate and expected outcome; both
    systems are better off for it being recorded rather than silently ignored.
    """
    result = _post("/api/consult/outcome",
                   {"request_id": request_id, "accepted": accepted,
                    "what_happened": what_happened}, timeout=20)
    return {"ok": result["ok"], **(result.get("data") or
                                   {"detail": result.get("detail", "")})}


def _point(item: Any) -> str:
    """One objection, rendered.

    SENTINEL is a separate system and its reply is untrusted input: an entry
    may be a dict, a bare string, or a number, and none of those may be allowed
    to raise inside a summary a human is waiting to read. It used to: a list of
    strings where dicts were expected took down the whole summary with a
    TypeError.
    """
    if isinstance(item, dict):
        kind = str(item.get("kind") or "").strip()
        point = str(item.get("point") or item.get("text") or "").strip()
        if not point:
            return ""
        return f"[{kind}] {point}" if kind else point
    return str(item).strip()


def _points(value: Any, limit: int) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [r for r in (_point(x) for x in list(value)[:limit]) if r]


def summarise(response: dict, max_points: int = 4) -> str:
    """Render a consultation response for a human or a log line.

    Never raises. This text is what the approving human reads next to a trade,
    and a formatting exception here would mean they read nothing at all.
    """
    if not isinstance(response, dict):
        return "No second opinion available. Proceeding on ARIA's own analysis."
    if not response.get("ok"):
        state = response.get("sentinel_status") or status_for(
            str(response.get("reason") or ""))
        return (f"No second opinion available (SENTINEL: {state}). "
                f"Proceeding on ARIA's own analysis. Silence is not agreement.")

    position = response.get("sentinel_position") or read_position(response)
    lines = [f"SENTINEL [{position}] (independent, confidence "
             f"{response.get('confidence', 0)}):",
             str(response.get("independent_analysis") or "").strip()[:800]]

    objections = _points(response.get("counter_arguments"), max_points)
    if objections:
        lines.append("\nObjections raised:")
        lines += [f"  {o}" for o in objections]
    alternatives = _points(response.get("alternative_hypotheses"), max_points)
    if alternatives:
        lines.append("\nAlternative explanations:")
        lines += [f"  {a}" for a in alternatives]
    uncertainties = _points(response.get("uncertainties"), 3)
    if uncertainties:
        lines.append("\nNot established: " + "; ".join(uncertainties))
    if response.get("recommendation"):
        lines.append(f"\nRecommendation: {response['recommendation']}")
    if response.get("disagreement_recorded"):
        lines.append("\n(A disagreement record was opened. ARIA may reject this.)")
    return "\n".join(lines)


def status() -> dict[str, Any]:
    """Whether consultation is currently possible, and why not if it is not."""
    token, reachable = bool(_token()), is_available()
    if not token:
        state = STATUS_NO_TOKEN
    elif not reachable:
        state = STATUS_UNAVAILABLE
    else:
        state = STATUS_AVAILABLE
    return {"url": sentinel_url(), "token_configured": token,
            "reachable": reachable, "sentinel_status": state,
            "note": ("ARIA operates fully without SENTINEL; consultation is "
                     "an enhancement, never a dependency")}
