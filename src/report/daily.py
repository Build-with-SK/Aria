"""
src/report/daily.py
===================
The daily intelligence product — one report per day, kept forever.

WHAT WAS WRONG WITH THE OLD ONE
-------------------------------
`data/daily_report.json` is a single file that every run overwrote. So:

  * there was exactly one report, and its `date` said 2026-05-31 — three months
    stale — while the UI rendered it under a heading that implied today;
  * a day with no run silently showed the previous day's conclusions as
    current, which is the worst possible failure for a record whose only value
    is being a record;
  * yesterday's reasoning was destroyed by today's, so nothing could ever be
    checked against what was actually said at the time.

A daily report that can be overwritten is not a record, it is a cache.

THE MODEL HERE
--------------
    data/reports/YYYY-MM-DD.json     one file per day, write-once
    date IS the identity

`generate()` refuses to overwrite an existing date unless explicitly told to
supersede it, and a superseded report is ARCHIVED beside the new one rather than
destroyed. `get(date)` returns exactly that day or reports its absence —
`NOT_GENERATED` is a real, first-class answer, never quietly filled with the
most recent report available.

WHAT IS IN IT — and what is deliberately not
--------------------------------------------
Every section is assembled from a subsystem that already computed it: the world
model, the regime classifier, the ledger, the research eye. This module does no
analysis of its own beyond arranging and diffing, because a report that
originates its own claims has no source to check them against.

Where a source is missing the section says so. A daily report with an empty
NEWS section is accurate on a quiet day; a daily report with an invented NEWS
section is worthless on every day.
"""
from __future__ import annotations

import json
import logging
import shutil
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent
REPORTS = ROOT / "data" / "reports"
ARCHIVE = REPORTS / "superseded"

SCHEMA_VERSION = 3

NOT_GENERATED = "NOT_GENERATED"
GENERATED = "GENERATED"


def today_key() -> str:
    return date.today().isoformat()


def _path(day: str) -> Path:
    return REPORTS / f"{day}.json"


def _valid_day(day: str) -> str:
    """Reject anything that is not a plain ISO date.

    The date is the filename, so this is also the path-traversal guard: a
    `date` of `../../secrets` must never become a write target.
    """
    try:
        return date.fromisoformat(str(day)).isoformat()
    except Exception:
        raise ValueError(f"not an ISO date: {day!r}")


# ── section builders ────────────────────────────────────────────────────────

def _macro_section() -> dict:
    from src.macro import regime as R
    reg = R.current()
    inputs = reg.get("inputs", {})

    def val(key):
        row = inputs.get(key) or {}
        return {"value": row.get("value"), "status": row.get("status"),
                "label": row.get("label")}

    macro_json = {}
    try:
        macro_json = json.loads(
            (ROOT / "data" / "macro_data.json").read_text(encoding="utf-8"))
    except Exception:
        pass

    return {
        "regime": reg["regime"],
        "regime_meaning": reg["meaning"],
        "regime_confidence": reg["confidence"],
        "regime_coverage": reg["coverage"],
        "regime_evidence": reg["evidence"],
        "regime_caveats": reg["caveats"],
        "would_flip_to": reg["would_flip_to"],
        "as_of": reg.get("as_of"),
        "stale": reg.get("stale"),
        "vix": val("vix"),
        "cpi_yoy": val("cpi_yoy"),
        "spread_10y2y": val("yield_spread_10y2y"),
        "unemployment": val("unemployment_rate"),
        "dxy": macro_json.get("dxy"),
        "treasury_2y": macro_json.get("treasury_2y"),
        "treasury_10y": macro_json.get("treasury_10y"),
        "macro_score": macro_json.get("macro_score"),
    }


def _market_section(world: dict) -> dict:
    m = (world or {}).get("market") or {}
    breadth = m.get("breadth") or {}

    top_bull, top_bear = [], []
    try:
        signals = json.loads(
            (ROOT / "data" / "signals.json").read_text(encoding="utf-8"))
        rows = [
            {"ticker": k,
             "provider_symbol": v.get("provider_symbol"),
             "price_unit": v.get("price_unit"),
             "name": v.get("name"),
             "price": v.get("current_price"),
             "score": v.get("composite_score"),
             "action": v.get("action")}
            for k, v in signals.items()
            if isinstance(v, dict) and v.get("composite_score") is not None
        ]
        rows.sort(key=lambda r: r["score"], reverse=True)
        top_bull = rows[:8]
        top_bear = rows[-8:][::-1]
    except Exception as e:
        logger.debug("daily report: signals unreadable: %s", e)

    return {
        "breadth": breadth,
        "volatility": m.get("volatility") or {},
        "top_bullish": top_bull,
        "top_bearish": top_bear,
        "signals_note": (
            "Prices carry their own provider symbol and quote unit. A bare "
            "ticker is a label, not an instrument."),
    }


def _news_section(hours: int = 24) -> dict:
    """Verified-ish developments, ranked, with the ladder attached.

    This reads the SAME observation store the live feed reads, but takes the
    slow view of it: a day's worth, deduplicated and ranked, rather than
    whatever arrived in the last minute. The two are different products over
    one source, which is the point — not two event stores.
    """
    try:
        from src.research.live_news import digest
        return digest(hours=hours, limit=12)
    except Exception as e:
        logger.debug("daily report: news unavailable: %s", e)
        return {"items": [], "note": f"the research eye could not be read: {e}"}


def _record_section(world: dict) -> dict:
    rec = ((world or {}).get("record") or {}).get("stats") or {}
    calib: dict = {}
    try:
        from src.core import calibration as C
        # `investigate()` is the rigorous view: it counts distinct EVENTS
        # rather than ledger rows, keeps traded and non-traded populations
        # apart, and puts a Wilson interval on every rate.
        #
        # This used to call a `C.report()` that has never existed, behind a
        # `hasattr` guard — so it silently produced `{}` and the report's
        # calibration section was permanently empty while looking populated.
        # A guard that turns a missing function into a plausible-looking blank
        # is worse than the AttributeError it suppresses.
        calib = C.investigate()
        calib["headline"] = C.summary_line()
    except Exception as e:
        logger.warning("daily report: calibration unavailable: %s", e)
        calib = {"error": str(e),
                 "note": "calibration could not be computed for this report"}

    recent: list[dict] = []
    try:
        from src.core.ledger import connect
        with connect() as conn:
            recent = [dict(r) for r in conn.execute(
                "SELECT subject, provider_symbol, claim, direction, confidence, "
                "       correct, actual_return, resolved_at "
                "FROM predictions WHERE resolved=1 "
                "ORDER BY resolved_at DESC LIMIT 8")]
    except Exception as e:
        logger.debug("daily report: ledger unreadable: %s", e)

    return {
        "stats": rec,
        "recent_resolved": recent,
        "calibration": calib,
        "limitation": (
            "Hit rate over a few hundred predictions has a wide interval "
            "around it. Treat these as a record of what was said, not as a "
            "measured edge."),
    }


def _assessment(world: dict, macro: dict, market: dict,
                changes: dict) -> dict:
    """What matters, what changed, what would invalidate it.

    Assembled from the sections above rather than generated: every line here
    points at a number that appears elsewhere in this same report, so a reader
    can check it without leaving the page.
    """
    moved = (changes or {}).get("changed") or []
    watching: list[str] = []
    research = (world or {}).get("research") or {}
    for n in (research.get("active_narratives") or [])[:6]:
        watching.append(f"{n.get('subject')} ({n.get('mentions')} mentions)")

    matters = []
    if macro["stale"]:
        matters.append(
            f"The macro snapshot is dated {macro['as_of']}, so today's regime "
            f"call rests on old inputs.")
    else:
        matters.append(
            f"Regime is {macro['regime']} on {macro['regime_coverage']:.0%} "
            f"observed inputs — {macro['regime_meaning']}.")
    b = market.get("breadth") or {}
    if b.get("bullish") is not None:
        matters.append(
            f"Breadth: {b.get('bullish')} bullish vs {b.get('bearish')} bearish "
            f"of {b.get('tracked')} tracked.")
    vol = market.get("volatility") or {}
    if vol.get("vix") is not None:
        matters.append(f"VIX at {vol.get('vix')}.")

    return {
        "what_matters": matters,
        "what_changed": [
            f"{c.get('field')}: {c.get('from')} → {c.get('to')}" for c in moved
        ] or ["Nothing in the world model moved in the window."],
        "watching": watching or ["The research eye has surfaced no narrative."],
        "what_would_invalidate": [
            f"{f['if']} (currently {f['distance']}{f['unit']} away)"
            for f in macro.get("would_flip_to", [])
        ],
    }


def _blind_spots(world: dict, macro: dict, news: dict, record: dict) -> list[str]:
    out = list((world or {}).get("unknowns") or [])
    out.extend(macro.get("regime_caveats") or [])
    if not (news.get("items") or []):
        out.append("No verified developments were captured in the window — "
                   "that is an absence of observation, not an absence of news.")
    if not (record.get("recent_resolved") or []):
        out.append("No predictions resolved recently, so nothing new has been "
                   "learned about calibration.")
    if news.get("quality_note"):
        out.append(news["quality_note"])
    return out


# ── build / generate / read ─────────────────────────────────────────────────

def build(day: Optional[str] = None) -> dict:
    """Assemble a report for `day`. Pure — writes nothing."""
    day = _valid_day(day or today_key())

    world, changes = {}, {}
    try:
        from src.core import world as W
        world = W.snapshot()
        changes = W.changes(24)
    except Exception as e:
        logger.warning("daily report: world model unavailable: %s", e)

    macro = _macro_section()
    market = _market_section(world)
    news = _news_section()
    record = _record_section(world)
    assessment = _assessment(world, macro, market, changes)

    summary = _executive_summary(day, macro, market, assessment, news)

    return {
        "schema_version": SCHEMA_VERSION,
        "date": day,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "status": GENERATED,
        "market_regime": macro["regime"],
        "executive_summary": summary,
        "macro": macro,
        "market": market,
        "news": news,
        "assessment": assessment,
        "track_record": record,
        "blind_spots": _blind_spots(world, macro, news, record),
    }


def _executive_summary(day: str, macro: dict, market: dict,
                       assessment: dict, news: dict) -> str:
    b = market.get("breadth") or {}
    vol = market.get("volatility") or {}
    bits = [f"{day}."]
    bits.append(f"Regime {macro['regime']} at {macro['regime_coverage']:.0%} "
                f"input coverage.")
    if vol.get("vix") is not None:
        bits.append(f"VIX {vol['vix']}.")
    if b.get("bullish") is not None:
        bits.append(f"Breadth {b.get('bullish')}/{b.get('bearish')} "
                    f"bull/bear of {b.get('tracked')}.")
    n = len(news.get("items") or [])
    bits.append(f"{n} verified development{'s' if n != 1 else ''} captured "
                f"in the last 24h.")
    moved = assessment.get("what_changed") or []
    if moved and not moved[0].startswith("Nothing"):
        bits.append(f"{len(moved)} field(s) moved in the world model.")
    else:
        bits.append("The world model did not move.")
    return " ".join(bits)


def generate(day: Optional[str] = None, *, supersede: bool = False) -> dict:
    """Build and STORE the report for `day`.

    Refuses to overwrite an existing day unless `supersede=True`, and even then
    the previous version is archived rather than destroyed. A daily record whose
    history can be silently rewritten is not a record.
    """
    day = _valid_day(day or today_key())
    REPORTS.mkdir(parents=True, exist_ok=True)
    target = _path(day)

    if target.exists() and not supersede:
        raise FileExistsError(
            f"a report for {day} already exists. Daily reports are write-once "
            f"— pass supersede=True to replace it, and the existing one will be "
            f"archived under {ARCHIVE.name}/ rather than deleted.")

    report = build(day)

    if target.exists():
        ARCHIVE.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%H%M%S")
        shutil.copy2(target, ARCHIVE / f"{day}.{stamp}.json")
        prior = json.loads(target.read_text(encoding="utf-8"))
        report["superseded"] = {
            "previous_generated_at": prior.get("generated_at"),
            "archived_as": f"{ARCHIVE.name}/{day}.{stamp}.json",
        }

    target.write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    logger.info("daily report written: %s", target)
    return report


def get(day: Optional[str] = None) -> dict:
    """One day's report, or an explicit NOT_GENERATED.

    This never falls back to the most recent report. Showing yesterday's
    conclusions under today's date is the failure this whole module exists to
    make impossible.
    """
    day = _valid_day(day or today_key())
    path = _path(day)
    if not path.exists():
        prev = history(limit=1)
        return {
            "date": day,
            "status": NOT_GENERATED,
            "message": f"No report has been generated for {day}.",
            "most_recent_available": prev[0]["date"] if prev else None,
            "note": ("Reports are not carried forward. The most recent "
                     "available report is offered as a LINK, not as today's."),
        }
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        return {"date": day, "status": "UNREADABLE", "message": str(e)}


def history(limit: int = 60) -> list[dict]:
    """Every stored report, newest first — date, summary and regime only."""
    if not REPORTS.exists():
        return []
    out = []
    for path in sorted(REPORTS.glob("*.json"), reverse=True):
        try:
            day = _valid_day(path.stem)
        except ValueError:
            continue                      # superseded/ archive files, etc.
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        out.append({
            "date": day,
            "generated_at": data.get("generated_at"),
            "market_regime": data.get("market_regime"),
            "regime_confidence": (data.get("macro") or {}).get("regime_confidence"),
            "executive_summary": data.get("executive_summary"),
            "superseded": bool(data.get("superseded")),
        })
        if len(out) >= limit:
            break
    return out


def latest() -> Optional[dict]:
    h = history(limit=1)
    return get(h[0]["date"]) if h else None


def ensure_today() -> dict:
    """Generate today's report if it does not exist yet. Idempotent."""
    day = today_key()
    if _path(day).exists():
        return get(day)
    return generate(day)


def backfill_from_legacy() -> Optional[dict]:
    """Import the single overwritten `data/daily_report.json` under ITS OWN date.

    That file is the only surviving report from before this module, and its
    `date` field says which day it actually describes. Filing it under that date
    preserves it; filing it under today would be the same lie the old design
    told. Never overwrites a real report.
    """
    legacy = ROOT / "data" / "daily_report.json"
    if not legacy.exists():
        return None
    try:
        data = json.loads(legacy.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("legacy report unreadable: %s", e)
        return None
    day = str(data.get("date") or "")[:10]
    try:
        day = _valid_day(day)
    except ValueError:
        logger.warning("legacy report has no usable date: %r", data.get("date"))
        return None

    REPORTS.mkdir(parents=True, exist_ok=True)
    target = _path(day)
    if target.exists():
        return None
    payload = dict(data)
    payload.update({
        "schema_version": 1,
        "date": day,
        "status": GENERATED,
        "imported_from": "data/daily_report.json",
        "import_note": (
            "Imported from the single-file report that every run used to "
            "overwrite. It is filed under the date it describes, not the date "
            "it was imported. Sections added in later schema versions are "
            "absent because they were never captured that day."),
        "executive_summary": (
            f"{day}. Imported legacy report. Regime "
            f"{data.get('market_regime', 'unknown')}."),
    })
    target.write_text(json.dumps(payload, indent=1, default=str), encoding="utf-8")
    logger.info("legacy daily report imported as %s", target)
    return payload
