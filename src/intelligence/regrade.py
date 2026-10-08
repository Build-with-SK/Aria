"""
src/intelligence/regrade.py
===========================
Re-measure defective historical grades with the corrected grader — as a VIEW.

Two defects sit in grades made before 2026-10-06, both in the measurement, not
in the claim:

  MIXED_PRICE_BASIS      return = (adjusted close at horizon - raw recorded
                         price) / raw recorded price. Every distribution inside
                         the window, and every adjustment after it, is dropped.
  GRADED_BEFORE_HORIZON  graded ON the horizon date, before that date's close
                         existed (e.g. 11:01 London on 2026-08-25 "graded on
                         the close of 2026-08-25"), so the previous session's
                         close was used: a shorter horizon than the one claimed.
                         318 technical and 9 V5 grades.

`regrade(rows)` measures each such call again exactly as
src/core/ledger.resolve_due now does — identity first, then the anchor session
the call was built on, then both ends from ONE adjusted series, with the
horizon date's close only once that close exists. Nothing is written to the
ledger: the stored grade stays as recorded, and the evaluation dataset uses the
re-measurement in its place, flagged `grade_source = regraded_same_series`, and
reports how many outcomes changed.

A call with no session anchor (made inside the session window, or on a venue
with no exchange close) cannot be put on one series for an instrument that pays
distributions; it is not regraded and stays excluded.
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent
CACHE = ROOT / "data" / "intelligence" / "regrades.json"
METHOD = "same-series-v1 (src/core/ledger.resolve_due as of 2026-10-06)"
V5_NEUTRAL_BAND = 0.02          # V5's own neutral rule, kept for V5 calls


def needs_regrade(row: dict, v5_basis: Optional[str] = None) -> bool:
    from src.intelligence import dataset
    if not row.get("resolved") or row.get("correct") is None:
        return False
    symbol = row.get("provider_symbol") or row.get("subject") or ""
    asset_class, _ = dataset.instrument_of(symbol)
    mixed = (dataset.price_basis(row, v5_basis) == dataset.RECORDED_REFERENCE
             and asset_class not in dataset.NO_DISTRIBUTION_CLASSES)
    early = (str(row.get("resolved_at") or "")[:10]
             <= str(row.get("resolve_after") or "")[:10])
    return mixed or early


def _neutral_correct(row: dict, ret: float) -> bool:
    if row.get("source") == "v5":
        return abs(ret) < V5_NEUTRAL_BAND
    band = 0.02 * max(1.0, (row.get("horizon_days") or 21) / 21) ** 0.5
    return abs(ret) <= band


def regrade_one(row: dict, *, price_on: Optional[Callable] = None,
                today: Optional[date] = None) -> dict:
    """{"correct", "actual_return", "anchor", "start", "end", "basis"} or
    {"correct": None, "why": ...}."""
    from src.core import ledger
    price_on = price_on or ledger._price_on
    today = today or date.today()
    ra = str(row.get("resolve_after") or "")[:10]
    if not ra or ra >= today.isoformat():
        return {"correct": None, "why": "horizon close not yet available"}
    provider = row.get("provider_symbol")
    if not provider:
        provider, why = ledger._resolved_price_symbol(row.get("subject") or "")
        if provider is None:
            return {"correct": None, "why": f"identity unresolved: {why}"}
    from src.intelligence import dataset
    asset_class, _ = dataset.instrument_of(provider)
    # The anchor is found on the PROVIDER's venue: the ledger's own helper
    # resolves `subject`, and in the master namespace "BA" is BAE (BA.L), not
    # a New York listing.
    anchor, anchor_why = ledger._grading_anchor({**row, "subject": provider})
    if not anchor and "inside the session window" in anchor_why:
        anchor, anchor_why = _printed_close_anchor(row, provider)
    if not anchor:
        if asset_class in dataset.NO_DISTRIBUTION_CLASSES and row.get("price_at"):
            # No distributions: the recorded reference and the vendor close are
            # already on one basis. Only the horizon date needs correcting.
            return _finish(row, provider, None, float(row["price_at"]), ra, price_on,
                           f"recorded reference -> close on/before {ra} "
                           f"({asset_class}: no distribution adjustment)")
        return {"correct": None, "why": f"no session anchor ({anchor_why})"}
    start = price_on(provider, anchor)
    if start is None:
        return {"correct": None, "why": f"no close for {provider} on {anchor}"}
    if row.get("price_at"):
        same, why = ledger._reference_matches(provider, anchor, row["price_at"], got=start)
        if not same:
            return {"correct": None, "why": f"identity mismatch: {why}"}
    return _finish(row, provider, anchor, start, ra, price_on,
                   f"adjusted close {anchor} -> close on/before {ra} ({anchor_why})")


def _printed_close_anchor(row: dict, provider: str) -> tuple[Optional[str], str]:
    """The session whose close had PRINTED before the call, for a call made in
    the settle buffer after the close (the technical snapshots ran at 16:30 New
    York). The call was built on that close, so anchoring there adds nothing it
    did not have. A call made while the market was still trading has no such
    close and gets None."""
    try:
        from src.markets import sessions
        from src.markets.venues import VENUES
        inst = sessions.resolve(provider)
        spec = VENUES.get(inst.venue or "")
        if spec is None or spec.kind != "exchange":
            return None, "no exchange close"
        at = datetime.fromisoformat(str(row["created_at"]))
        if at.tzinfo is None:
            at = at.astimezone()
        sess = sessions.latest_completed_session(spec.code, at, settle_minutes=0)
        if sess is None:
            return None, "no completed session"
        close = sessions.session_close(spec.code, sess)
        zone = sessions._zone(spec)
        local_day = at.astimezone(zone).date() if zone else None
        if close is None or close > at or local_day != sess:
            return None, "made while the market was trading"
        return sess.isoformat(), (f"close of {sess} had printed at {close:%H:%M %Z}, "
                                  f"before the call (made in the settle buffer)")
    except Exception as e:                       # pragma: no cover - defensive
        return None, f"anchor unavailable: {e}"


def _finish(row: dict, provider: str, anchor: Optional[str], start: float, ra: str,
            price_on: Callable, basis: str) -> dict:
    end = price_on(provider, ra)
    if end is None:
        return {"correct": None, "why": f"no close for {provider} on or before {ra}"}
    if not start:
        return {"correct": None, "why": "zero reference price"}
    ret = (end - start) / start
    d = (row.get("direction") or "").lower()
    correct = (ret > 0 if d == "bull" else ret < 0 if d == "bear"
               else _neutral_correct(row, ret))
    return {"correct": bool(correct), "actual_return": round(ret, 6),
            "anchor": anchor, "end_date": ra, "provider_symbol": provider,
            "start": start, "end": end, "basis": basis, "method": METHOD}


def regrade(rows: list[dict], *, v5_bases: Optional[dict] = None,
            price_on: Optional[Callable] = None,
            today: Optional[date] = None) -> dict:
    """{prediction_id: result} for every row whose stored grade is defective."""
    v5_bases = v5_bases or {}
    out = {}
    for r in rows:
        if not needs_regrade(r, v5_bases.get(r.get("external_ref"))):
            continue
        try:
            out[r["id"]] = regrade_one(r, price_on=price_on, today=today)
        except Exception as e:                   # one bad row costs one row
            logger.debug("regrade failed for %s: %s", r.get("id"), e)
            out[r["id"]] = {"correct": None, "why": f"error: {e}"}
    return out


def save(results: dict) -> None:
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps({"method": METHOD,
                                 "computed_at": datetime.now().isoformat(timespec="seconds"),
                                 "results": results}, indent=1, default=str),
                     encoding="utf-8")


def load() -> dict:
    """{prediction_id: result} from the last saved regrade, or {}."""
    if not CACHE.exists():
        return {}
    try:
        return json.loads(CACHE.read_text(encoding="utf-8")).get("results") or {}
    except (ValueError, OSError):
        return {}
