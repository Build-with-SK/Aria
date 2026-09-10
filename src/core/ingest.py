"""
src/core/ingest.py
==================
Pull the outcomes ARIA already measured into the ledger.

THE FINDING THIS MODULE EXISTS FOR
----------------------------------
data/desk/backfilled_outcomes.jsonl holds 306 directional calls that were
already graded — `predicted`, `return_pct`, `correct`, the regime at the time
and the conviction behind each one. Nothing in the codebase reads that file.
Grepping for its name across src/ and backend/ returns the writer and nobody
else.

Those 306 rows are 61 DISTINCT EVENTS. The desk re-debates the same ticker and
each debate writes a row with the same entry, exit and outcome — BONK-USD
appears eighteen times with identical prices. An earlier version of this module
keyed on the debate id and so admitted all 306 as independent observations,
which inflated every sample size fivefold and made the resulting calibration
curve and its skill score unusable. Keying on the event identity fixes it.

Deduplicated, the record over 2026-07-18 → 2026-08-01 is 61 events at a 45.9%
hit rate. At n=61 that is NOT distinguishable from a coin flip, and the honest
statement is "no measurable directional edge yet", not "sub-random accuracy".

Three caveats travel with every row this module writes:

  * the horizon is ONE day, so this measures the desk's very short-term
    directional opinion, not its trading edge;
  * 53 of the 61 were NO_TRADE verdicts — views that never became positions.
    The desk's risk gates declined most of them, so the traded record and the
    opinion record are different populations and must not be pooled;
  * mean conviction differs sharply between those populations (26 for NO_TRADE
    versus 79 for traded), so a conviction-versus-outcome curve drawn across
    both is largely a comparison BETWEEN populations, not a calibration curve.

WHAT GETS INGESTED
------------------
  data/desk/backfilled_outcomes.jsonl   → 306 pre-graded 1-day directional calls
  data/desk/closed_trades.jsonl         → realised round trips, with P&L
  data/technical/recommendations_log.jsonl → dated recs, graded forward
  data/v5/predictions.jsonl             → the long-horizon ensemble calls
  data/execution/*                      → approvals the owner actually made

Every row is keyed by a stable `external_ref`, so this is safe to run on every
boot and on a schedule. Re-ingesting is a no-op, never a duplicate.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional

from src.core import ledger

logger = logging.getLogger(__name__)


def _uid(*parts) -> str:
    """Stable short hash for an event identity."""
    import hashlib
    return hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:16]

ROOT = Path(__file__).resolve().parent.parent.parent
DATA = ROOT / "data"


def _jsonl(path: Path) -> Iterator[dict]:
    if not path.exists():
        return
    try:
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except Exception:
                    continue
    except Exception as e:
        logger.warning("could not read %s: %s", path, e)


def _iso(v) -> str:
    if not v:
        return datetime.now().isoformat(timespec="seconds")
    return str(v)[:19]


# ── desk: pre-graded directional calls ──────────────────────────────────────

def ingest_desk_outcomes() -> dict:
    """The 306 graded calls, imported with their grades intact.

    These arrive already resolved, so they are written and then immediately
    graded from the outcome the desk recorded — no price lookup is needed and
    none is done. Conviction (0–100) becomes the stated probability, which is
    what makes these rows usable for calibration: the desk asserted a strength
    and we can now check whether that strength meant anything.
    """
    n = graded = 0
    for r in _jsonl(DATA / "desk" / "backfilled_outcomes.jsonl"):
        tid = r.get("id")
        ticker = r.get("ticker")
        if not tid or not ticker:
            continue
        # DEDUPLICATE ON THE EVENT, NOT ON THE DEBATE.
        #
        # The first version keyed on the debate id, which is unique per debate,
        # so all 306 rows entered the ledger as 306 independent observations.
        # They are not. The desk re-debates the same ticker repeatedly and each
        # debate writes a row carrying the SAME entry price, exit price and
        # outcome: BONK-USD bear @ 3e-06 → 3e-06 appears eighteen times;
        # BTC-USD, SOL-USD, XTZ-USD, DOT-USD and BCH-USD twelve or thirteen
        # times each. Across the file, 306 rows are 61 distinct events — 80%
        # are repeats.
        #
        # Treating them as independent inflates every sample size fivefold and
        # makes every significance test on this data wrong. It also skewed the
        # calibration curve, because repeated events are not repeated at
        # random: the desk re-debates what it is already interested in.
        #
        # Keying on (ticker, view, entry, exit) collapses them back to one
        # observation each. The repeats are not lost — they are the same fact
        # asserted more than once, and a ledger counts facts.
        ref = ("desk-outcome:" + _uid(ticker, r.get("view"),
                                      r.get("entry"), r.get("exit")))
        view = (r.get("view") or r.get("predicted") or "").lower()
        conviction = r.get("conviction")
        # Conviction is a 0–100 strength, not a probability. Mapping it onto
        # 0.5–0.95 keeps the ordering (which is the part calibration tests)
        # without pretending the desk stated a probability it never stated.
        prob = (0.5 + min(max(float(conviction), 0), 100) / 100 * 0.45) if conviction is not None else None
        verdict = r.get("verdict") or ""
        pid = ledger.record_prediction(
            subject=ticker, source="desk",
            claim=f"{view or 'directional'} view over {r.get('horizon_days', 1)} day(s)",
            direction=view if view in ("bull", "bear") else "neutral",
            probability=prob, confidence=prob,
            horizon_days=int(r.get("horizon_days") or 1),
            price_at=r.get("entry"), regime_at=r.get("regime"),
            strategy="desk-debate",
            rationale=(f"Desk verdict {verdict} at conviction {conviction} "
                       f"(bar {r.get('conviction_bar')})."),
            invalidation=(f"price through {r['invalidation_level']}"
                          if r.get("invalidation_level") else ""),
            created_at=_iso(r.get("at")), external_ref=ref, announce=False)
        n += 1
        if pid and r.get("correct") is not None:
            ret = r.get("return_pct")
            ok = ledger.grade(
                pid, correct=bool(r["correct"]), price_end=r.get("exit"),
                actual_return=(float(ret) / 100 if ret is not None else None),
                announce=False,
                note=(f"graded by the desk at the time; verdict was {verdict}. "
                      f"1-day horizon, so this scores the directional opinion, "
                      f"not a held position."))
            graded += int(ok)
    return {"source": "desk_outcomes", "seen": n, "graded_now": graded}


# ── desk: realised round trips ──────────────────────────────────────────────

def ingest_closed_trades() -> dict:
    """Closed positions — the only rows in ARIA with real money attached.

    These become decisions (a trade was taken) rather than predictions: the
    claim was already recorded as a debate outcome, and double-counting the
    same view once as an opinion and once as a fill would inflate the record.
    """
    n = 0
    for r in _jsonl(DATA / "desk" / "closed_trades.jsonl"):
        ticker, at = r.get("ticker"), r.get("at")
        if not ticker or not at:
            continue
        ref = f"desk-trade:{r.get('debate_id') or ''}:{at}"
        pnl = r.get("pnl")
        did = ledger.record_decision(
            kind="trade", subject=ticker,
            action=(f"{r.get('entry_side', '?')} {r.get('qty')} @ {r.get('entry_price')} → "
                    f"exit @ {r.get('exit_price')}"),
            rationale=(r.get("thesis") or "")[:1200],
            expected=f"R multiple {r.get('r_multiple')}",
            risk=r.get("reason") or "",
            decided_by="desk", status="executed",
            at=_iso(r.get("entry_at") or at), external_ref=ref, announce=False)
        if did:
            ledger.close_decision(
                ref, status="executed",
                outcome=(f"closed {r.get('pnl_pct')}% ({r.get('reason')}), "
                         f"R={r.get('r_multiple')}, mode={r.get('mode')}"),
                pnl=float(pnl) if pnl is not None else None)
            n += 1
    return {"source": "closed_trades", "seen": n}


# ── technical recommendations ───────────────────────────────────────────────

_TECH_DIRECTION = {
    "strong buy": "bull", "buy": "bull",
    "strong sell": "bear", "sell": "bear",
    "neutral": "neutral", "hold": "neutral",
}


def ingest_technical() -> dict:
    """Dated technical recommendations, graded forward on a 10-day horizon.

    These carry an entry price and a date, which is everything needed to score
    them — they simply were never scored. A 10-day horizon is chosen because
    the tracker snapshots daily on a 1d timeframe; anything longer would be
    grading a different claim from the one the indicator made.
    """
    n = 0
    for r in _jsonl(DATA / "technical" / "recommendations_log.jsonl"):
        sym, at = r.get("symbol"), r.get("at")
        summary = (r.get("summary") or "").strip().lower()
        if not sym or not at or summary not in _TECH_DIRECTION:
            continue
        ref = f"tech:{sym}:{r.get('date')}:{r.get('timeframe')}"
        ledger.record_prediction(
            subject=sym, source="technical",
            claim=f"technical composite reads {r.get('summary')} on {r.get('timeframe')}",
            direction=_TECH_DIRECTION[summary],
            probability=None, confidence=None,
            horizon_days=10, price_at=r.get("entry_price"),
            strategy=f"technical-composite:{r.get('timeframe')}",
            rationale=f"Indicator composite summary: {r.get('summary')}.",
            created_at=_iso(at), external_ref=ref, announce=False)
        n += 1
    return {"source": "technical", "seen": n}


# ── V5 ensemble ─────────────────────────────────────────────────────────────

def ingest_v5() -> dict:
    """The long-horizon ensemble calls, with their stated probability kept.

    Rows already graded in the JSONL bring their grade with them; the rest stay
    pending and are picked up by the ledger's own resolver when their horizon
    elapses.
    """
    n = graded = 0
    for r in _jsonl(DATA / "v5" / "predictions.jsonl"):
        tid, ticker = r.get("id"), r.get("ticker")
        if not tid or not ticker:
            continue
        ref = f"v5:{tid}"
        pid = ledger.record_prediction(
            subject=ticker, source="v5",
            claim=(f"{r.get('direction')} over {r.get('horizon_days')} trading days"),
            direction=r.get("direction"), probability=r.get("p_bull"),
            confidence=r.get("confidence"),
            horizon_days=int(r.get("horizon_days") or 21),
            price_at=r.get("price_at"), strategy=f"v5-ensemble:v{r.get('weights_version')}",
            rationale=(r.get("rationale") or "")[:1200],
            evidence=(r.get("modules") or [])[:40],
            created_at=_iso(r.get("at")), external_ref=ref, announce=False)
        n += 1
        if pid and r.get("resolved") and r.get("correct") is not None:
            graded += int(ledger.grade(
                pid, correct=bool(r["correct"]), price_end=r.get("price_end"),
                actual_return=r.get("actual_return"),
                announce=False,
                note="grade carried over from the V5 learning loop"))
    return {"source": "v5", "seen": n, "graded_now": graded}


# ── approvals the owner made ────────────────────────────────────────────────

def ingest_approvals() -> dict:
    """Every proposal the owner approved, rejected or cancelled.

    §11: an approval queue that forgets what the owner chose teaches ARIA
    nothing. The order manager already persists these; they simply had no home
    in the record.
    """
    n = 0
    candidates = [DATA / "execution" / "approval_queue.json",
                  DATA / "execution" / "trades.json",
                  DATA / "execution" / "queue.json"]
    for path in candidates:
        if not path.exists():
            continue
        try:
            blob = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        rows = blob if isinstance(blob, list) else (blob.get("trades") or blob.get("queue") or [])
        for r in rows if isinstance(rows, list) else []:
            if not isinstance(r, dict):
                continue
            tid = r.get("trade_id") or r.get("id")
            if not tid:
                continue
            status = (r.get("status") or "open").lower()
            ledger.record_decision(
                kind="approval", subject=r.get("ticker") or r.get("symbol"),
                action=f"{r.get('side', '?')} {r.get('quantity') or r.get('qty', '?')}",
                rationale=(r.get("reason") or r.get("rationale") or "")[:1200],
                confidence=r.get("confidence"),
                decided_by="owner" if status in ("approved", "rejected") else "desk",
                status=status, at=_iso(r.get("created_at") or r.get("at")),
                external_ref=f"approval:{tid}", announce=False)
            n += 1
    return {"source": "approvals", "seen": n}


def ingest_all() -> dict:
    """Run every importer. Safe to call repeatedly — refs deduplicate."""
    out = {}
    for fn in (ingest_desk_outcomes, ingest_closed_trades, ingest_technical,
               ingest_v5, ingest_approvals):
        try:
            r = fn()
            out[r["source"]] = r
        except Exception as e:
            logger.warning("ingest %s failed: %s", fn.__name__, e)
            out[fn.__name__] = {"error": str(e)}
    out["ledger"] = ledger.stats()
    return out
