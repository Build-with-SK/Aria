"""
src/intelligence/provenance.py
==============================
What a prediction must carry so its evaluation can be reproduced.

A grade is only as good as the record of what was claimed: when, about which
instrument on which venue, in which market state, over what horizon, with what
kind of number, by which code, on which data, under which regime, chosen by
which route, through which calibration. `completeness(row)` lists the fields a
ledger row is missing; nothing here fills one in. A field that was not
recorded stays missing — "no silent defaults" means exactly that.

`derive_market_context(symbol, at)` is the one place the venue and session
state are computed at write time, from the shared session engine
(src/markets/sessions). It fails open to (None, None): a prediction that
cannot be placed on a venue is still recorded, and is then visibly incomplete.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from src.intelligence import semantics

logger = logging.getLogger(__name__)

#: field -> what it answers. Order is the order a reader asks the questions in.
REQUIRED = {
    "id": "which prediction",
    "created_at": "when it was made",
    "subject": "what it is about, as ARIA named it",
    "provider_symbol": "which instrument was actually evaluated",
    "venue": "where that instrument trades",
    "market_session": "the market state when the call was made",
    "horizon_days": "over how long",
    "direction": "which way",
    "probability_type": "what kind of number travels with it",
    "probability": "the number itself, when its type has one",
    "source": "which model",
    "model_version": "which version of it",
    "model_hash": "the code fingerprint that version ran",
    "feature_version": "which feature set / snapshot it was built on",
    "data_as_of": "how fresh its data were",
    "regime_at": "the regime known at creation",
    "regime_basis": "where that regime came from",
    "routing_decision": "how this model came to be the one consulted",
    "calibration_version": "which calibration (or none) produced the number",
}


def _present(v) -> bool:
    return v is not None and v != ""


def completeness(row: dict) -> dict:
    """{"complete": bool, "missing": [field, ...]} for one ledger row.

    The probability is required only when the row's type HAS a number: a
    technical composite (type NONE) is complete without one; an UNKNOWN type
    is itself a missing field.
    """
    missing = []
    ptype = semantics.type_of(row)
    for f in REQUIRED:
        if f == "probability_type":
            if ptype is semantics.ProbabilityType.UNKNOWN:
                missing.append(f)
            continue
        if f == "probability":
            if ptype in semantics.SCORABLE:
                ok = _present(row.get("p_correct"))
            elif ptype in (semantics.ProbabilityType.CONFIDENCE,
                           semantics.ProbabilityType.SCORE):
                ok = _present(row.get("probability")) or _present(row.get("confidence"))
            elif ptype is semantics.ProbabilityType.NONE:
                ok = True
            else:
                ok = False
            if not ok:
                missing.append(f)
            continue
        if f == "model_hash":
            from src.intelligence.dataset import model_hash
            if not (_present(row.get("model_hash")) or model_hash(row.get("model_version"))):
                missing.append(f)
            continue
        if not _present(row.get(f)):
            missing.append(f)
    return {"complete": not missing, "missing": missing}


def derive_market_context(symbol: Optional[str], at: Optional[datetime]
                          ) -> tuple[Optional[str], Optional[str]]:
    """(venue code, session state) for `symbol` at `at`, or (None, None).

    `at` may be naive (this machine's local time, as the ledger stores it).
    The state is the session engine's own value (OPEN, CLOSED, PRE_MARKET …,
    UNKNOWN) — UNKNOWN is recorded as UNKNOWN, never as a guess.
    """
    if not symbol:
        return None, None
    try:
        from src.markets import sessions
        when = at or datetime.now()
        if when.tzinfo is None:
            when = when.astimezone()
        info = sessions.session(symbol, when)
        return info.venue, info.state.value
    except Exception as e:                       # never block a write
        logger.debug("market context unavailable for %s: %s", symbol, e)
        return None, None


def audit(rows) -> dict:
    """Provenance completeness for many rows, per source."""
    from src.intelligence.dataset import provenance_audit
    return provenance_audit(rows)
