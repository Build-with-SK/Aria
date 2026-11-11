"""
src/intelligence/dataset.py
===========================
The one evaluation dataset every model metric is computed from.

It is DERIVED from the prediction ledger and never written back to it. A
historical row that cannot be evaluated honestly is not repaired, re-dated or
re-labelled: it is classified, excluded, and the reason is kept, so

    graded rows == included + excluded            (always, per model)

and anyone can ask why a given prediction is not in a number.

WHAT IT ENFORCES
----------------
point in time       `data_as_of` may not be later than the call; a grade may
                    not predate the call or its horizon; with `as_of=T` only
                    calls made by T, graded by T, are visible.
correct horizon     `resolve_after` must follow from `created_at` and
                    `horizon_days` (the ledger's own trading-day rule).
adjusted prices     a return must be measured on ONE series. Rows graded as
                    (adjusted close at the horizon - raw recorded price) / raw
                    recorded price drop every distribution inside the window,
                    and every adjustment after it; for instruments that pay
                    distributions they are excluded as MIXED_PRICE_BASIS
                    unless a same-series regrade is supplied (`regrades`).
no duplicates       one event, one observation (see `event_key`).
probability types   only STATED / CALIBRATED probabilities on directional
                    calls reach `p`; every other row says why not.

Market regime labels are attached only when a point-in-time labeller is
supplied (src/intelligence/regime.MarketRegimeLabeler); the macro label is
attached with a validity status, and a label whose snapshot postdates the call
is treated as unknown.
"""
from __future__ import annotations

import hashlib
import json
import logging
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from functools import lru_cache
from typing import Callable, Iterable, Optional

from src.intelligence import semantics

logger = logging.getLogger(__name__)

# ── exclusion vocabulary ────────────────────────────────────────────────────

EXCLUSION_REASONS = {
    "UNRESOLVABLE": "graded as undecidable — identity unresolved, no reference "
                    "price, or the price source disagreed with the record",
    "NO_SOURCE": "the row names no model",
    "NO_DIRECTION": "the row states no bull / bear / neutral direction",
    "CREATED_AFTER_RESOLUTION": "the grade is timestamped before the call",
    "GRADED_BEFORE_HORIZON": "graded on or before the date its horizon elapsed",
    "HORIZON_INCONSISTENT": "resolve_after does not follow from created_at and "
                            "horizon_days",
    "DATA_AFTER_CREATION": "data_as_of is later than the call — future information",
    "MIXED_PRICE_BASIS": "return measured from a raw recorded price to an adjusted "
                         "close, on an instrument that can pay distributions, with "
                         "no same-series regrade available",
    "DUPLICATE_EVENT": "a repeat assertion of an event already counted",
}

#: Exclusions that mean information from after the call was (or may have been)
#: used. A model with any of these cannot pass the promotion gate.
PIT_REASONS = frozenset({"CREATED_AFTER_RESOLUTION", "GRADED_BEFORE_HORIZON",
                         "DATA_AFTER_CREATION"})

#: Asset classes whose prices carry no dividend or split adjustment in the
#: vendor series, so a raw recorded reference is on the same basis as the close.
NO_DISTRIBUTION_CLASSES = frozenset({"crypto", "forex", "index"})

#: The ledger's trading-day -> calendar-day rule (record_prediction).
CALENDAR_PER_TRADING_DAY = 7 / 5
HORIZON_TOLERANCE_DAYS = 1

HORIZON_BUCKETS = (("1D", 1), ("1W", 5), ("1M", 21), ("3M", 63))


def horizon_bucket(h) -> str:
    try:
        h = int(h)
    except (TypeError, ValueError):
        return "UNKNOWN"
    for name, upper in HORIZON_BUCKETS:
        if h <= upper:
            return name
    return ">3M"


# ── small parsers ───────────────────────────────────────────────────────────

def _dt(value) -> Optional[datetime]:
    """A naive local datetime from the ledger's ISO strings (or None)."""
    if not value:
        return None
    try:
        d = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        try:
            d = datetime.fromisoformat(str(value)[:10])
        except ValueError:
            return None
    if d.tzinfo is not None:
        d = d.astimezone().replace(tzinfo=None)
    return d


def _naive(at: Optional[datetime]) -> Optional[datetime]:
    if at is None:
        return None
    return at.astimezone().replace(tzinfo=None) if at.tzinfo is not None else at


def model_hash(model_version: Optional[str]) -> Optional[str]:
    """The code fingerprint inside a model_version ("v5|code=ab12…|weights=1")."""
    if not model_version:
        return None
    for part in str(model_version).split("|"):
        if part.startswith("code="):
            return part[5:] or None
    return None


@lru_cache(maxsize=4096)
def instrument_of(symbol: str) -> tuple[Optional[str], Optional[str]]:
    """(asset_class, venue) from the shared instrument resolver."""
    try:
        from src.markets import sessions
        inst = sessions.resolve(symbol)
        return inst.asset_class, inst.venue
    except Exception as e:                       # pragma: no cover - defensive
        logger.debug("instrument unresolved for %s: %s", symbol, e)
        return None, None


def information_date(row: dict) -> Optional[str]:
    """The date whose data the call was built on — the cluster key.

    Calls built on the same session share every market-wide shock, so they are
    not independent observations; this is what `evaluation` clusters on.
    """
    sess = row.get("information_session")
    if sess and not str(sess).startswith("day:"):
        return str(sess)[:10]
    d = _dt(row.get("created_at"))
    return d.date().isoformat() if d else None


def event_key(row: dict) -> tuple:
    """What makes two rows the same event.

    The desk re-debates a ticker and its imported rows repeat the same entry
    and exit (BONK-USD eighteen times): those collapse on the move itself, as
    src/core/calibration does. Every other source makes at most one call per
    subject, direction, horizon and information date.
    """
    src = row.get("source")
    if src == "desk" and row.get("price_at") is not None and row.get("price_end") is not None:
        return ("desk", row.get("subject"), row.get("direction"),
                row.get("price_at"), row.get("price_end"))
    return (src, row.get("provider_symbol") or row.get("subject"), row.get("direction"),
            row.get("horizon_days"), information_date(row))


# ── price basis ─────────────────────────────────────────────────────────────

SAME_SERIES = "SAME_SERIES"
RECORDED_REFERENCE = "RECORDED_REFERENCE"


def price_basis(row: dict, v5_return_basis: Optional[str] = None) -> str:
    """How the stored grade's return was measured, from the grade's own note.

    SAME_SERIES          both ends from one vendor series (desk imports: entry
                         and exit read from one frame; ledger grades since
                         2026-10-06 that say "return: adjusted close"; V5
                         grades whose basis is "series close").
    RECORDED_REFERENCE   start = the raw price recorded with the call, end =
                         the vendor's (adjusted) close. Every grade before
                         2026-10-06, and later ones with no session anchor.
    """
    note = row.get("outcome_note") or ""
    if "graded by the desk at the time" in note:
        return SAME_SERIES
    if "grade carried over from the V5 learning loop" in note:
        b = (v5_return_basis or "").lower()
        return SAME_SERIES if b.startswith("series close") else RECORDED_REFERENCE
    if "return: adjusted close" in note or "next-close basis on one adjusted series" in note:
        return SAME_SERIES
    return RECORDED_REFERENCE


# ── regime validity ─────────────────────────────────────────────────────────

def macro_regime(row: dict) -> tuple[Optional[str], str]:
    """(label, status) for the macro regime stamped on a row.

    VERIFIED_PIT      the basis says it was read at creation, or from a world
                      snapshot taken BEFORE the call (and the snapshot's own
                      timestamp agrees)
    SOURCE_RECORDED   recorded by the producing system itself at the time
                      (the desk judge's conditioner); no separate basis kept
    LOOKAHEAD         the snapshot it came from postdates the call
    UNVERIFIED        a label with no basis
    ABSENT            no label
    """
    label = row.get("regime_at")
    if not label:
        return None, "ABSENT"
    basis = row.get("regime_basis") or ""
    created = _dt(row.get("created_at"))
    if basis.startswith("world_snapshot "):
        snap = _dt(basis.split(" ")[1])
        if snap is None or created is None:
            return label, "UNVERIFIED"
        return (label, "VERIFIED_PIT") if snap <= created else (None, "LOOKAHEAD")
    if "at creation" in basis:
        return label, "VERIFIED_PIT"
    if row.get("source") == "desk":
        return label, "SOURCE_RECORDED"
    return label, "UNVERIFIED"


# ── the dataset ─────────────────────────────────────────────────────────────

@dataclass
class EvaluationDataset:
    rows: list = field(default_factory=list)            # included graded events
    excluded: list = field(default_factory=list)        # {id, model, reasons}
    pending: Counter = field(default_factory=Counter)    # model -> ungraded
    probability_exclusions: dict = field(default_factory=dict)  # model -> {reason: n}
    graded: Counter = field(default_factory=Counter)    # model -> graded rows seen
    total: Counter = field(default_factory=Counter)     # model -> rows seen
    regrade: dict = field(default_factory=dict)
    as_of: Optional[str] = None
    built_at: str = ""

    @property
    def dataset_hash(self) -> str:
        """Same included events with the same outcomes -> same hash."""
        h = hashlib.sha256()
        for r in sorted(self.rows, key=lambda r: r["prediction_id"]):
            h.update(f"{r['prediction_id']}|{r['outcome']}|{r.get('p')}\n".encode())
        return h.hexdigest()[:16]

    def for_model(self, model: str) -> list:
        return [r for r in self.rows if r["model"] == model]

    def models(self) -> list:
        return sorted(set(self.total))

    def summary(self) -> dict:
        excl = defaultdict(Counter)
        for e in self.excluded:
            for reason in e["reasons"]:
                excl[e["model"]][reason] += 1
        inc = Counter(r["model"] for r in self.rows)
        out = {}
        for m in self.models():
            cured = Counter(d for r in self.rows if r["model"] == m
                            for d in r.get("cured_defects") or [])
            out[m] = {
                "recorded": self.total[m],
                "graded": self.graded[m],
                "pending": self.pending[m],
                "included": inc[m],
                "included_via_regrade": sum(1 for r in self.rows if r["model"] == m
                                            and r["grade_source"] != "stored"),
                "defects_cured_by_regrade": dict(cured),
                "excluded": sum(1 for e in self.excluded if e["model"] == m),
                "exclusions_by_reason": dict(excl[m]),
                "probability_scored": sum(1 for r in self.rows
                                          if r["model"] == m and r.get("p") is not None),
                "probability_not_scored": dict(self.probability_exclusions.get(m, {})),
            }
        return {"as_of": self.as_of, "built_at": self.built_at,
                "dataset_hash": self.dataset_hash, "regrade": self.regrade,
                "by_model": out,
                "reasons": EXCLUSION_REASONS}


def build(rows: Iterable[dict], *, as_of: Optional[datetime] = None,
          regrades: Optional[dict] = None,
          v5_bases: Optional[dict] = None,
          market_regime: Optional[Callable[[dict], Optional[dict]]] = None
          ) -> EvaluationDataset:
    """Build the canonical dataset from ledger rows.

    rows            ledger `predictions` rows as dicts
    as_of           point in time: only calls made by then, graded by then
    regrades        {prediction_id: {"correct", "actual_return", "basis"}} —
                    same-series re-measurements (src/intelligence/regrade);
                    they replace a MIXED_PRICE_BASIS grade, never a valid one
    v5_bases        {external_ref: return_basis} from the V5 log, so a carried
                    V5 grade can say how it was measured
    market_regime   row -> {"label", ...} computed point in time at creation
    """
    ds = EvaluationDataset(built_at=datetime.now().isoformat(timespec="seconds"),
                           as_of=as_of.isoformat() if as_of else None)
    cut = _naive(as_of)
    regrades = regrades or {}
    v5_bases = v5_bases or {}
    n_regraded = n_changed = 0
    candidates = []

    for raw in rows:
        r = dict(raw)
        model = r.get("source") or ""
        created = _dt(r.get("created_at"))
        if cut is not None and (created is None or created > cut):
            continue                           # not yet made at as_of
        ds.total[model or "unknown"] += 1
        resolved_at = _dt(r.get("resolved_at"))
        graded = bool(r.get("resolved")) and (cut is None or
                                              (resolved_at is not None and resolved_at <= cut))
        if not graded:
            ds.pending[model or "unknown"] += 1
            continue
        ds.graded[model or "unknown"] += 1

        reasons = []
        # Defects in the MEASUREMENT (not the claim): a same-series regrade
        # made with the corrected grader replaces the stored grade for these,
        # and only these.
        measurement = []
        if not model:
            reasons.append("NO_SOURCE")
        direction = (r.get("direction") or "").lower()
        if direction not in ("bull", "bear", "neutral"):
            reasons.append("NO_DIRECTION")
        if r.get("correct") is None:
            reasons.append("UNRESOLVABLE")
        if created and resolved_at and resolved_at < created:
            reasons.append("CREATED_AFTER_RESOLUTION")
        resolve_after = (r.get("resolve_after") or "")[:10]
        if resolved_at and resolve_after and resolved_at.date().isoformat() <= resolve_after:
            measurement.append("GRADED_BEFORE_HORIZON")
        if created and resolve_after and r.get("horizon_days") is not None:
            expected = (created + timedelta(days=int(r["horizon_days"])
                                            * CALENDAR_PER_TRADING_DAY)).date()
            try:
                gap = abs((datetime.fromisoformat(resolve_after).date() - expected).days)
            except ValueError:
                gap = 999
            if gap > HORIZON_TOLERANCE_DAYS:
                reasons.append("HORIZON_INCONSISTENT")
        das = _dt(r.get("data_as_of"))
        if das and created and das.date() > created.date():
            reasons.append("DATA_AFTER_CREATION")

        symbol = r.get("provider_symbol") or r.get("subject") or ""
        asset_class, venue = instrument_of(symbol) if symbol else (None, None)
        basis = price_basis(r, v5_bases.get(r.get("external_ref")))
        outcome = r.get("correct")
        actual_return = r.get("actual_return")
        grade_source = "stored"
        cured: list = []
        if basis == RECORDED_REFERENCE and asset_class not in NO_DISTRIBUTION_CLASSES:
            measurement.append("MIXED_PRICE_BASIS")
        if measurement and "UNRESOLVABLE" not in reasons and not reasons:
            rg = regrades.get(r.get("id"))
            if rg and rg.get("correct") is not None:
                n_regraded += 1
                if bool(rg["correct"]) != bool(outcome):
                    n_changed += 1
                outcome = int(bool(rg["correct"]))
                actual_return = rg.get("actual_return")
                basis, grade_source = SAME_SERIES, "regraded_same_series"
                cured, measurement = measurement, []
        reasons += measurement

        if reasons:
            ds.excluded.append({"id": r.get("id"), "model": model or "unknown",
                                "reasons": reasons})
            continue

        p, why_not = semantics.scorable_probability(r)
        if why_not:
            ds.probability_exclusions.setdefault(model, Counter())[why_not] += 1
        ptype = semantics.type_of(r)
        regime_label, regime_status = macro_regime(r)
        candidates.append({
            "prediction_id": r.get("id"),
            "model": model,
            "model_version": r.get("model_version"),
            "model_hash": r.get("model_hash") or model_hash(r.get("model_version")),
            "subject": r.get("subject"),
            "symbol": symbol,
            "asset_class": asset_class or "unknown",
            "venue": venue or "unknown",
            "created_at": r.get("created_at"),
            "resolved_at": r.get("resolved_at"),
            "resolve_after": resolve_after,
            "information_date": information_date(r),
            "horizon_days": r.get("horizon_days"),
            "horizon_bucket": horizon_bucket(r.get("horizon_days")),
            "direction": direction,
            "probability_type": ptype.value,
            "p": p,
            "outcome": int(bool(outcome)),
            "actual_return": actual_return,
            "price_basis": basis,
            "grade_source": grade_source,
            "cured_defects": cured,
            "regime_macro": regime_label,
            "regime_macro_status": regime_status,
            "regime_market": None,
            "event_key": event_key(r),
            "rationale": (r.get("rationale") or "")[:200],
        })

    # ── duplicates: keep the earliest assertion of each event ──────────────
    candidates.sort(key=lambda c: (c["created_at"] or "", c["prediction_id"] or ""))
    seen = set()
    for c in candidates:
        if c["event_key"] in seen:
            ds.excluded.append({"id": c["prediction_id"], "model": c["model"],
                                "reasons": ["DUPLICATE_EVENT"]})
            continue
        seen.add(c["event_key"])
        if market_regime is not None:
            try:
                mr = market_regime(c)
                c["regime_market"] = (mr or {}).get("label")
            except Exception as e:             # a regime is context, never fatal
                logger.debug("market regime unavailable for %s: %s", c["prediction_id"], e)
        c["event_key"] = json.dumps(c["event_key"], default=str)
        ds.rows.append(c)

    ds.regrade = {"regraded": n_regraded, "outcome_changed": n_changed}
    return ds


def ledger_rows() -> list[dict]:
    """Every ledger prediction, as dicts, through a READ-ONLY connection.

    Not ledger.connect(): that runs the schema migration, and evaluating the
    record must never be what alters it. A ledger without the newest columns
    simply yields rows without them (read as missing).
    """
    import sqlite3
    from src.core.bus import _db_path
    path = _db_path()                            # honours ARIA_CORE_DB, as the bus does
    if not path.exists():
        return []
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=10)
    try:
        conn.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in conn.execute("SELECT * FROM predictions").fetchall()]
        except sqlite3.OperationalError:
            return []                            # no predictions table yet
    finally:
        conn.close()


def v5_return_bases() -> dict:
    """{"v5:<id>": return_basis} from the V5 log, for carried V5 grades."""
    try:
        from src.v5 import learning
        return {f"v5:{e['id']}": e.get("return_basis")
                for e in learning.load_predictions() if e.get("id") and e.get("resolved")}
    except Exception as e:                       # pragma: no cover - defensive
        logger.debug("v5 return bases unavailable: %s", e)
        return {}


def provenance_audit(rows: Iterable[dict]) -> dict:
    """Per model: which provenance fields are present, row by row."""
    from src.intelligence import provenance
    out: dict = {}
    for r in rows:
        m = r.get("source") or "unknown"
        rep = provenance.completeness(r)
        d = out.setdefault(m, {"rows": 0, "complete": 0, "missing": Counter()})
        d["rows"] += 1
        d["complete"] += int(rep["complete"])
        for f in rep["missing"]:
            d["missing"][f] += 1
    return {m: {**d, "missing": dict(d["missing"])} for m, d in out.items()}
