"""
src/core/ledger.py
==================
The prediction and decision record — §9 and §44 of the brief.

WHY THE TRACK RECORD WAS ZERO
-----------------------------
It was not zero because ARIA was young, and it was not zero because nothing was
running. It was zero for two specific, findable reasons:

  1. Every prediction in data/v5/predictions.jsonl carries a horizon of roughly
     45 TRADING days. The ledger opened on 2026-08-01, so the earliest date any
     call could be graded was 2026-09-28 — and calibration additionally refuses
     to report below 20 resolved calls. The flywheel was built with a two-month
     cold start and nothing shorter to turn it in the meantime.

  2. Outcomes ARIA had already measured never became graded predictions. The
     desk closed 18 trades with realised P&L, an R multiple and the thesis that
     motivated each one. The technical tracker logged 140 dated recommendations
     with entry prices. Both are labelled outcomes. Neither reached the track
     record, because the track record only knew how to read one file.

This module fixes both. It is one table for every claim ARIA makes with a
horizon, regardless of which subsystem made it, plus one table for every
decision a human or the desk took. Existing history is INGESTED rather than
discarded, so the record starts populated with work that was really done.

WHAT COUNTS AS A PREDICTION
---------------------------
A claim that (a) is about the future, (b) names what would make it right or
wrong, and (c) has a date by which that is checkable. Anything failing one of
those is an opinion, and opinions do not go in here — they go on the bus as a
SYSTEM_NOTE.

GRADING
-------
`resolve_due()` grades everything whose horizon has elapsed, against the price
at the horizon date rather than the price today. That distinction matters: a
21-day call graded 80 days late has been graded on an 80-day return, which is
a different claim from the one that was made. Sources that carry their own
realised outcome (a closed trade) are graded by that outcome directly.

Nothing in this module writes a number it did not measure. An unresolvable
prediction stays pending and says so.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent
DATA = ROOT / "data"

from src.core.bus import DB_PATH, connect as _bus_connect, publish  # noqa: E402

_SCHEMA = """
CREATE TABLE IF NOT EXISTS predictions (
    id            TEXT PRIMARY KEY,
    created_at    TEXT NOT NULL,
    source        TEXT NOT NULL,     -- v5 | desk | technical | brain | manual
    subject       TEXT NOT NULL,     -- ticker or entity the claim is about
    claim         TEXT NOT NULL,     -- the sentence, in plain language
    direction     TEXT,              -- bull | bear | neutral
    probability   REAL,              -- stated P(direction is right), 0..1
    confidence    REAL,              -- the system's own confidence, 0..1
    horizon_days  INTEGER,
    resolve_after TEXT,              -- ISO date the horizon elapses
    price_at      REAL,              -- reference price when the claim was made
    regime_at     TEXT,              -- market regime at the time
    strategy      TEXT,              -- which strategy/version produced it
    rationale     TEXT,
    evidence      TEXT,              -- JSON list of {claim, source, weight}
    invalidation  TEXT,              -- what would make this wrong
    resolved      INTEGER NOT NULL DEFAULT 0,
    resolved_at   TEXT,
    price_end     REAL,
    actual_return REAL,
    correct       INTEGER,           -- 1 / 0 / NULL when undecidable
    error         REAL,              -- signed miss where a magnitude was claimed
    outcome_note  TEXT,
    external_ref  TEXT UNIQUE,       -- dedupe key against the source file
    -- Which instrument was ACTUALLY evaluated, and how that was established.
    --
    -- `subject` is what ARIA called the thing and is immutable; these two say
    -- what it denoted, so the record stays reproducible after a ticker changes
    -- hands. They live in CREATE TABLE, not only in the migration's ALTER, or
    -- a fresh install would create a ledger that can never carry identity —
    -- the migration is written to repair an existing database, and a new user
    -- has nothing to repair.
    provider_symbol TEXT,
    identity_basis  TEXT             -- UNAMBIGUOUS | MASTER_RESOLVED | PRICE_VERIFIED | EXACT_PRICE_MATCH
);
CREATE INDEX IF NOT EXISTS idx_pred_resolve ON predictions(resolved, resolve_after);
CREATE INDEX IF NOT EXISTS idx_pred_subject ON predictions(subject, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_pred_source  ON predictions(source, created_at DESC);

CREATE TABLE IF NOT EXISTS decisions (
    id            TEXT PRIMARY KEY,
    at            TEXT NOT NULL,
    kind          TEXT NOT NULL,     -- trade | approval | allocation | strategy | research
    subject       TEXT,
    action        TEXT NOT NULL,
    rationale     TEXT,
    evidence      TEXT,
    expected      TEXT,              -- expected benefit, in words
    risk          TEXT,
    confidence    REAL,
    decided_by    TEXT,              -- owner | desk | policy
    status        TEXT NOT NULL DEFAULT 'open',   -- open | approved | rejected | executed | cancelled
    outcome       TEXT,
    outcome_at    TEXT,
    pnl           REAL,
    external_ref  TEXT UNIQUE
);
CREATE INDEX IF NOT EXISTS idx_dec_at   ON decisions(at DESC);
CREATE INDEX IF NOT EXISTS idx_dec_kind ON decisions(kind, at DESC);
"""

_lock = threading.RLock()
_ready = False


@contextmanager
def connect():
    """Ledger tables live in the same file as the event log, deliberately.

    One database means a prediction and the event announcing it commit or fail
    together, and it means the whole record is one file to back up.
    """
    global _ready
    with _bus_connect() as conn:
        if not _ready:
            with _lock:
                if not _ready:
                    conn.executescript(_SCHEMA)
                    conn.commit()
                    _ready = True
        yield conn


def _uid(*parts: Any) -> str:
    import hashlib
    return hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:16]


# ── recording ───────────────────────────────────────────────────────────────

def record_prediction(*, subject: str, claim: str, source: str = "aria",
                      direction: Optional[str] = None,
                      probability: Optional[float] = None,
                      confidence: Optional[float] = None,
                      horizon_days: int = 21,
                      price_at: Optional[float] = None,
                      regime_at: Optional[str] = None,
                      strategy: Optional[str] = None,
                      rationale: str = "",
                      evidence: Optional[list] = None,
                      invalidation: str = "",
                      created_at: Optional[str] = None,
                      external_ref: Optional[str] = None,
                      announce: bool = True) -> Optional[str]:
    """Write one falsifiable claim into the record.

    `external_ref` is the dedupe key. Ingesting the same source file twice must
    not double the record, and a source that re-emits the same call on the same
    day is one claim, not two.
    """
    created = created_at or datetime.now().isoformat(timespec="seconds")
    # Horizon in trading days → calendar days. Five trading days is a week.
    resolve_after = (datetime.fromisoformat(created)
                     + timedelta(days=int(horizon_days) * 7 / 5)).date().isoformat()
    ref = external_ref or _uid(source, subject, created, direction, horizon_days)
    pid = _uid("pred", ref)

    # ── identity, stamped AT WRITE TIME ────────────────────────────────────
    #
    # `subject` is what ARIA called the thing. `provider_symbol` is which
    # instrument was actually evaluated, and keeping both is what makes the
    # record reproducible after a ticker changes hands.
    #
    # This used to happen only in `scripts/migrate_identity.py`, as a one-off
    # backfill — so every prediction written after that run carried no identity
    # at all, and 88 accumulated in a single week. A guarantee that has to be
    # re-established by hand is not a guarantee; it is a chore that will be
    # forgotten. Resolving here means the invariant holds by construction.
    #
    # It FAILS OPEN, deliberately. An unidentifiable subject must not stop a
    # prediction being recorded: refusing to write the claim would lose the
    # falsifiable statement entirely, which is worse than storing it with a
    # null identity that the migration tooling can still resolve later on
    # price evidence.
    provider_symbol, identity_basis = None, None
    try:
        from src.core import identity as _idt
        ident = _idt.describe(subject)
        if ident.ok and not ident.candidates:
            provider_symbol, identity_basis = ident.provider_symbol, "UNAMBIGUOUS"
        elif ident.ok:
            # The root exists on more than one venue. The symbol master still
            # resolved THIS display symbol to one provider symbol, so record it
            # and say that is what it rests on — a reader can tell this apart
            # from a case nothing had to choose between.
            provider_symbol, identity_basis = ident.provider_symbol, "MASTER_RESOLVED"
    except Exception as e:                      # never block a write
        logger.debug("ledger: identity unresolved for %s: %s", subject, e)

    try:
        with connect() as conn:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(predictions)")}
            has_identity = {"provider_symbol", "identity_basis"} <= cols
            if has_identity:
                conn.execute("""
                    INSERT OR IGNORE INTO predictions
                    (id, created_at, source, subject, claim, direction, probability,
                     confidence, horizon_days, resolve_after, price_at, regime_at,
                     strategy, rationale, evidence, invalidation, external_ref,
                     provider_symbol, identity_basis)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (pid, created, source, subject.upper(), claim, direction,
                     probability, confidence, int(horizon_days), resolve_after,
                     price_at, regime_at, strategy, rationale,
                     json.dumps(evidence, default=str) if evidence else None,
                     invalidation, ref, provider_symbol, identity_basis))
            else:
                # A ledger that predates the identity migration still works.
                conn.execute("""
                    INSERT OR IGNORE INTO predictions
                    (id, created_at, source, subject, claim, direction, probability,
                     confidence, horizon_days, resolve_after, price_at, regime_at,
                     strategy, rationale, evidence, invalidation, external_ref)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (pid, created, source, subject.upper(), claim, direction,
                     probability, confidence, int(horizon_days), resolve_after,
                     price_at, regime_at, strategy, rationale,
                     json.dumps(evidence, default=str) if evidence else None,
                     invalidation, ref))
            changed = conn.total_changes
    except Exception as e:
        logger.warning("ledger.record_prediction failed for %s: %s", subject, e)
        return None

    if announce and changed:
        publish("PREDICTION_CREATED",
                f"{subject.upper()}: {claim}"[:200],
                source=source, subject=subject.upper(),
                payload={"horizon_days": horizon_days, "direction": direction,
                         "confidence": confidence, "resolve_after": resolve_after,
                         "prediction_id": pid})
    return pid


def record_decision(*, kind: str, action: str, subject: Optional[str] = None,
                    rationale: str = "", evidence: Optional[list] = None,
                    expected: str = "", risk: str = "",
                    confidence: Optional[float] = None,
                    decided_by: str = "desk", status: str = "open",
                    at: Optional[str] = None,
                    external_ref: Optional[str] = None,
                    announce: bool = True) -> Optional[str]:
    """Record a decision — proposed, approved, rejected or executed.

    §11 of the brief: when the owner approves or rejects something, that choice
    is itself information about what the owner wants, and it has to be kept or
    ARIA cannot learn from it.
    """
    when = at or datetime.now().isoformat(timespec="seconds")
    ref = external_ref or _uid(kind, subject, action, when)
    did = _uid("dec", ref)
    try:
        with connect() as conn:
            conn.execute("""
                INSERT OR IGNORE INTO decisions
                (id, at, kind, subject, action, rationale, evidence, expected,
                 risk, confidence, decided_by, status, external_ref)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (did, when, kind, (subject or "").upper() or None, action,
                 rationale, json.dumps(evidence, default=str) if evidence else None,
                 expected, risk, confidence, decided_by, status, ref))
            changed = conn.total_changes
    except Exception as e:
        logger.warning("ledger.record_decision failed: %s", e)
        return None

    if announce and changed:
        publish("DECISION_RECORDED", f"{kind}: {action}"[:200],
                source=decided_by, subject=(subject or None),
                severity="action" if status == "open" else "info",
                payload={"decision_id": did, "status": status, "kind": kind})
    return did


def close_decision(external_ref: str, *, status: str, outcome: str = "",
                   pnl: Optional[float] = None) -> bool:
    """Mark a decision resolved. Returns whether a row actually changed."""
    try:
        with connect() as conn:
            cur = conn.execute("""
                UPDATE decisions SET status=?, outcome=?, outcome_at=?, pnl=?
                WHERE external_ref=?""",
                (status, outcome, datetime.now().isoformat(timespec="seconds"),
                 pnl, external_ref))
            return bool(cur.rowcount)
    except Exception as e:
        logger.warning("ledger.close_decision failed: %s", e)
        return False


def grade(prediction_id: str, *, correct: Optional[bool], price_end: Optional[float] = None,
          actual_return: Optional[float] = None, error: Optional[float] = None,
          note: str = "", announce: bool = True) -> bool:
    """Attach an outcome to a prediction.

    `announce=False` is for importing history. A backfill of 346 grades that
    were measured weeks ago must not appear in the activity stream as 346
    things that happened this afternoon — that is precisely the fabricated
    liveness §48 rules out, and I did exactly it on the first run of the
    importer before catching it in the event counts.
    """
    try:
        with connect() as conn:
            cur = conn.execute("""
                UPDATE predictions SET resolved=1, resolved_at=?, price_end=?,
                    actual_return=?, correct=?, error=?, outcome_note=?
                WHERE id=? AND resolved=0""",
                (datetime.now().isoformat(timespec="seconds"), price_end,
                 actual_return, None if correct is None else int(bool(correct)),
                 error, note, prediction_id))
            ok = bool(cur.rowcount)
            row = conn.execute("SELECT subject, claim FROM predictions WHERE id=?",
                               (prediction_id,)).fetchone() if ok else None
    except Exception as e:
        logger.warning("ledger.grade failed: %s", e)
        return False

    if ok and row and announce:
        verdict = "unresolvable" if correct is None else ("right" if correct else "wrong")
        publish("PREDICTION_RESOLVED",
                f"{row['subject']}: {verdict} — {row['claim']}"[:200],
                source="ledger", subject=row["subject"],
                severity="notable",
                payload={"prediction_id": prediction_id, "correct": correct,
                         "actual_return": actual_return, "note": note})
    return ok


# ── grading the due ─────────────────────────────────────────────────────────

def _price_on(ticker: str, on: str) -> Optional[float]:
    """Close on (or immediately before) a date. None when unavailable.

    Graded on the horizon date, not today. See the module docstring.

    `ticker` MUST already be a provider symbol. Callers on the grading path go
    through `_resolved_price_symbol()` first — handing a bare display symbol
    here is exactly how BAE Systems (`BA` -> `BA.L`) got priced against
    Boeing's NYSE close.
    """
    try:
        import yfinance as yf
        end = datetime.fromisoformat(on) + timedelta(days=6)
        start = datetime.fromisoformat(on) - timedelta(days=10)
        hist = yf.Ticker(ticker).history(start=start.date().isoformat(),
                                         end=end.date().isoformat(),
                                         auto_adjust=True)
        if hist is None or hist.empty:
            return None
        cutoff = datetime.fromisoformat(on).date()
        upto = hist[hist.index.date <= cutoff]
        use = upto if not upto.empty else hist
        return float(use["Close"].iloc[-1])
    except Exception as e:
        logger.debug("price lookup failed for %s on %s: %s", ticker, on, e)
        return None


#: How far the price source may disagree with the recorded reference price
#: before the two are treated as different instruments. Generous, because a
#: reference price can legitimately be an intraday quote against a daily close.
REFERENCE_TOLERANCE = 0.25


def _resolved_price_symbol(subject: str) -> tuple[Optional[str], str]:
    """The provider symbol to price `subject` with, or (None, why not).

    §8 — establish identity BEFORE touching a price. The symbol master already
    holds the right answer (`BA` -> `BA.L`); the ledger simply never asked it,
    and handed the bare display symbol to the price feed instead.

    A symbol that cannot be resolved returns None, and the caller records
    GRADING_SKIPPED / IDENTITY_UNRESOLVED rather than guessing. Never force a
    loss, never force a win, never substitute a similarly named ticker.
    """
    try:
        from src.core import identity
        ident = identity.describe(subject)
    except Exception as e:                      # pragma: no cover - defensive
        logger.warning("identity lookup failed for %s: %s", subject, e)
        return None, f"identity lookup failed: {e}"
    if not ident.ok:
        return None, ident.reason
    return ident.provider_symbol, ""


def _reference_matches(ticker: str, made_on: str, price_at: float) -> tuple[bool, str]:
    """Does the price source agree with the price the prediction was made at?

    THE BUG THIS EXISTS FOR
    -----------------------
    The technical tracker logs BARE tickers with no exchange or currency, and
    data/universe.db is itself wrong about several of them — it carries
    ('BA', 'Boeing', 'LSE', 'GBP') and ('JD', 'JD.com', 'LSE', 'GBP'), which
    assign US companies to the London exchange in pence. So a recommendation
    was recorded at BAE Systems' LSE price (2200p) and then graded against
    Boeing's NYSE close ($215) — a -90% "return" that is not a market move and,
    worse, is not even the same company. JD Sports was graded against JD.com;
    Shell's LSE line was graded against its NYSE ADR.

    Six of 116 resolved predictions were corrupted this way, and each one
    poisons every statistic downstream of it.

    Comparing the reference price against the SAME source that will supply the
    exit price catches all three cases precisely — including JD, whose 3.2x
    discrepancy is too small for any plausibility-of-return rule to catch.
    A magnitude rule would also wrongly reject genuine crypto moves; this does
    not, because it tests instrument identity rather than volatility.
    """
    got = _price_on(ticker, made_on)
    if got is None or not price_at:
        # Unverifiable is not the same as mismatched. Grading proceeds; the
        # note on the outcome records that the check could not run.
        return True, "reference price could not be re-checked"
    drift = abs(got - price_at) / price_at
    if drift <= REFERENCE_TOLERANCE:
        return True, ""
    return False, (
        f"the price source returns {got:.4g} for {ticker} on {made_on} but the "
        f"prediction was recorded at {price_at:.4g} — a factor of "
        f"{max(got, price_at) / max(1e-12, min(got, price_at)):.1f}. These are "
        f"almost certainly different instruments (a venue, ADR or "
        f"currency-unit mismatch), so this cannot be graded.")


def resolve_due(*, now: Optional[datetime] = None, limit: int = 80) -> dict:
    """Grade every prediction whose horizon has elapsed. Idempotent and cheap.

    A vendor outage leaves a call pending rather than grading it wrongly — an
    ungraded prediction is honest, a mis-graded one poisons the calibration
    curve permanently.
    """
    now = now or datetime.now()
    today = now.date().isoformat()
    try:
        with connect() as conn:
            due = conn.execute("""
                SELECT * FROM predictions
                WHERE resolved=0 AND resolve_after IS NOT NULL AND resolve_after <= ?
                ORDER BY resolve_after LIMIT ?""", (today, limit)).fetchall()
    except Exception as e:
        logger.warning("ledger.resolve_due query failed: %s", e)
        return {"checked": 0, "resolved": 0, "unresolvable": 0}

    resolved = unresolvable = 0
    for r in due:
        if not r["price_at"]:
            grade(r["id"], correct=None,
                  note="no reference price was recorded, so the claim cannot be scored")
            unresolvable += 1
            continue
        # ── §8: identity, then price. Never the other way round. ──────────
        # Step 1 — which security IS this? The symbol master knows `BA` means
        # `BA.L`; the previous version skipped this and asked the price feed
        # for a bare `BA`, which is Boeing.
        provider, why_not = _resolved_price_symbol(r["subject"])
        if provider is None:
            grade(r["id"], correct=None,
                  note=f"GRADING_SKIPPED / IDENTITY_UNRESOLVED — {why_not}")
            publish("DATA_STALE",
                    f"{r['subject']}: grading skipped, identity unresolved"[:200],
                    source="ledger", subject=r["subject"], severity="warning",
                    payload={"prediction_id": r["id"],
                             "reason": "IDENTITY_UNRESOLVED", "detail": why_not})
            unresolvable += 1
            continue

        # Step 2 — does the resolved instrument agree with the price the
        # prediction was recorded at? Catches a correct-looking resolution that
        # nonetheless prices a different line (ADR vs local, unit mismatch).
        same, why = _reference_matches(provider, (r["created_at"] or "")[:10],
                                       r["price_at"])
        if not same:
            grade(r["id"], correct=None,
                  note=f"GRADING_SKIPPED / IDENTITY_UNRESOLVED — {why}")
            publish("DATA_STALE", f"{r['subject']}: {why}"[:200],
                    source="ledger", subject=r["subject"], severity="warning",
                    payload={"prediction_id": r["id"], "reason": "IDENTITY_UNRESOLVED",
                             "provider_symbol": provider})
            unresolvable += 1
            continue

        # Step 3 — only now, a price, and from the SAME provider symbol the
        # reference was verified against.
        end_px = _price_on(provider, r["resolve_after"])
        if end_px is None:
            continue                      # try again next tick
        ret = (end_px - r["price_at"]) / r["price_at"]
        direction = (r["direction"] or "").lower()
        if direction == "bull":
            correct: Optional[bool] = ret > 0
        elif direction == "bear":
            correct = ret < 0
        else:
            # A neutral call is right when the move stayed small. "Small" is
            # scaled to the horizon rather than a flat number, because 2% over
            # a week and 2% over a quarter are not the same claim.
            band = 0.02 * max(1.0, (r["horizon_days"] or 21) / 21) ** 0.5
            correct = abs(ret) <= band
        grade(r["id"], correct=correct, price_end=end_px, actual_return=round(ret, 5),
              note=f"graded on the close of {r['resolve_after']}")
        resolved += 1

    if resolved:
        logger.info("ledger: graded %d predictions (%d unresolvable)", resolved, unresolvable)
    return {"checked": len(due), "resolved": resolved, "unresolvable": unresolvable}


# ── reading ─────────────────────────────────────────────────────────────────

def predictions(*, limit: int = 200, source: Optional[str] = None,
                subject: Optional[str] = None,
                resolved: Optional[bool] = None) -> list[dict]:
    sql = "SELECT * FROM predictions WHERE 1=1"
    args: list[Any] = []
    if source:
        sql += " AND source=?"; args.append(source)
    if subject:
        sql += " AND subject=?"; args.append(subject.upper())
    if resolved is not None:
        sql += " AND resolved=?"; args.append(int(resolved))
    sql += " ORDER BY created_at DESC LIMIT ?"
    args.append(int(max(1, min(limit, 1000))))
    try:
        with connect() as conn:
            rows = [dict(r) for r in conn.execute(sql, args).fetchall()]
    except Exception as e:
        logger.warning("ledger.predictions failed: %s", e)
        return []
    for d in rows:
        if d.get("evidence"):
            try:
                d["evidence"] = json.loads(d["evidence"])
            except Exception:
                pass
    return rows


def decisions(*, limit: int = 200, kind: Optional[str] = None,
              status: Optional[str] = None) -> list[dict]:
    sql = "SELECT * FROM decisions WHERE 1=1"
    args: list[Any] = []
    if kind:
        sql += " AND kind=?"; args.append(kind)
    if status:
        sql += " AND status=?"; args.append(status)
    sql += " ORDER BY at DESC LIMIT ?"
    args.append(int(max(1, min(limit, 1000))))
    try:
        with connect() as conn:
            return [dict(r) for r in conn.execute(sql, args).fetchall()]
    except Exception as e:
        logger.warning("ledger.decisions failed: %s", e)
        return []


def stats() -> dict:
    """Headline numbers, with every unmeasurable quantity reported as None.

    A hit rate over four resolved calls is not a hit rate, so `by_source`
    reports n alongside every figure and the caller is expected to respect it.
    """
    try:
        with connect() as conn:
            tot = conn.execute("SELECT COUNT(*) n FROM predictions").fetchone()["n"]
            res = conn.execute("SELECT COUNT(*) n FROM predictions WHERE resolved=1").fetchone()["n"]
            hits = conn.execute(
                "SELECT COUNT(*) n FROM predictions WHERE resolved=1 AND correct=1").fetchone()["n"]
            scored = conn.execute(
                "SELECT COUNT(*) n FROM predictions WHERE resolved=1 AND correct IS NOT NULL"
            ).fetchone()["n"]
            by_source = conn.execute("""
                SELECT source,
                       COUNT(*) n,
                       SUM(resolved) n_resolved,
                       SUM(CASE WHEN correct=1 THEN 1 ELSE 0 END) n_correct,
                       SUM(CASE WHEN correct IS NOT NULL THEN 1 ELSE 0 END) n_scored
                FROM predictions GROUP BY source ORDER BY n DESC""").fetchall()
            dec = conn.execute("SELECT status, COUNT(*) n FROM decisions GROUP BY status").fetchall()
            oldest = conn.execute(
                "SELECT MIN(created_at) m FROM predictions").fetchone()["m"]
            next_due = conn.execute(
                "SELECT MIN(resolve_after) m FROM predictions WHERE resolved=0").fetchone()["m"]
    except Exception as e:
        logger.warning("ledger.stats failed: %s", e)
        return {"total": 0, "resolved": 0, "pending": 0, "hit_rate": None,
                "by_source": [], "decisions": {}}

    return {
        "total": tot,
        "resolved": res,
        "pending": tot - res,
        "scored": scored,
        "hit_rate": round(hits / scored, 4) if scored else None,
        "hit_rate_note": (None if scored >= 20 else
                          f"{scored} scored calls — a hit rate is not reported "
                          f"below 20, because it would not survive one more result"),
        "oldest_prediction": oldest,
        "next_resolution_due": next_due,
        "by_source": [{"source": r["source"], "n": r["n"],
                       "resolved": r["n_resolved"] or 0,
                       "scored": r["n_scored"] or 0,
                       "hit_rate": (round((r["n_correct"] or 0) / r["n_scored"], 4)
                                    if r["n_scored"] else None)}
                      for r in by_source],
        "decisions": {r["status"]: r["n"] for r in dec},
    }


def calibration(min_total: int = 20, min_bucket: int = 5) -> dict:
    """Stated probability versus realised frequency — §32.

    Reports Brier score and its skill against the always-say-50% baseline.
    Refuses to draw a curve through too few points, and says so rather than
    returning a shape that looks like evidence.
    """
    rows = [p for p in predictions(limit=1000, resolved=True)
            if p.get("correct") is not None and p.get("probability") is not None]
    if len(rows) < min_total:
        return {"measurable": False, "n_resolved": len(rows), "n_required": min_total,
                "note": (f"{len(rows)} scored calls carry a stated probability. "
                         f"Calibration is not reported below {min_total} — a curve "
                         f"through a handful of points is decoration, not evidence."),
                "buckets": []}

    brier = sum((p["probability"] - (1 if p["correct"] else 0)) ** 2 for p in rows) / len(rows)
    edges = [(0.0, 0.55), (0.55, 0.60), (0.60, 0.65), (0.65, 0.70), (0.70, 0.80), (0.80, 1.01)]
    buckets, ece = [], 0.0
    for lo, hi in edges:
        b = [p for p in rows if lo <= p["probability"] < hi]
        if len(b) < min_bucket:
            buckets.append({"range": f"{lo:.2f}–{hi:.2f}", "n": len(b), "measurable": False})
            continue
        stated = sum(p["probability"] for p in b) / len(b)
        realised = sum(1 for p in b if p["correct"]) / len(b)
        ece += (len(b) / len(rows)) * abs(stated - realised)
        buckets.append({"range": f"{lo:.2f}–{hi:.2f}", "n": len(b), "measurable": True,
                        "stated": round(stated, 4), "realised": round(realised, 4),
                        "gap": round(realised - stated, 4)})
    return {"measurable": True, "n_resolved": len(rows),
            "brier": round(brier, 4), "skill": round(1 - brier / 0.25, 4),
            "ece": round(ece, 4), "buckets": buckets}
