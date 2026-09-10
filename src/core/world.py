"""
src/core/world.py
=================
ARIA's world model — §20 of the brief.

The single answer to "what does ARIA currently believe about the world?", and,
by diffing two snapshots, to "what changed?".

THIS MODULE INVENTS NOTHING
---------------------------
Every field is read from a subsystem that already computes it. Where a source
is missing or stale, the field says so instead of carrying the last value
forward — which is the specific failure this codebase already had. Consider:

    data/macro_data.json  →  "data_date": "2026-05-31",  "vix": 15.32

That file has not been rewritten since May. The command deck renders its VIX
and macro score under the heading "LIVE MARKET STATE", so a three-month-old
number is displayed as today's. Nothing is lying on purpose; nobody ever
attached an age to the value. Here, every block carries `as_of` and `stale`,
and a stale block is reported as stale at the top level rather than being
quietly folded into a headline.

STRUCTURE
---------
    market      regime, breadth, volatility, momentum, macro
    risk        portfolio exposure, concentration, drawdown, day budget
    portfolio   positions, equity, P&L
    strategies  what is live, what the desk is running, what the lab produced
    research    what is being watched and why, active narratives
    record      the ledger's own view of how right ARIA has been
    system      worker health and data freshness
    unknowns    the explicit list of things ARIA cannot currently see

`unknowns` is not decoration. A world model that omits its own blind spots
invites the reader to treat absence of evidence as evidence of absence, and in
this system that has already happened once.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent
DATA = ROOT / "data"

# How old a block may be before it stops counting as current. Chosen per
# source, because these have genuinely different natural cadences: signals are
# a daily pipeline, the desk ticks in minutes, macro is daily at best.
STALE_AFTER_HOURS = {
    "signals": 30,          # one trading day plus a margin
    "macro": 48,
    "brain": 6,
    "desk": 2,
    "research": 24,
    "portfolio": 6,
    # ML predictions come from the full pipeline, which is a manual run taking
    # several hours. Two days is generous; the point is that they age at all.
    # Until this existed, data/ml_predictions.json went 98 days without moving
    # while /api/ml, the chat context and the brain's perception layer all read
    # it as current — the world model tracked six artefacts and this was not
    # one of them.
    "ml": 48,
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS world_snapshots (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    at       TEXT NOT NULL,
    digest   TEXT NOT NULL,
    snapshot TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_world_at ON world_snapshots(at DESC);
"""

_ready = False


def _db():
    from src.core.bus import connect
    return connect()


def _ensure():
    global _ready
    if _ready:
        return
    try:
        with _db() as conn:
            conn.executescript(_SCHEMA)
            conn.commit()
        _ready = True
    except Exception as e:
        logger.warning("world snapshot table unavailable: %s", e)


# ── helpers ─────────────────────────────────────────────────────────────────

def _read_json(name: str) -> Optional[Any]:
    p = DATA / name
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        logger.debug("could not read %s: %s", name, e)
        return None


def _mtime(name: str) -> Optional[datetime]:
    p = DATA / name
    try:
        return datetime.fromtimestamp(p.stat().st_mtime) if p.exists() else None
    except Exception:
        return None


def _age_block(name: str, kind: str, stated_date: Optional[str] = None) -> dict:
    """Freshness for one source.

    `stated_date` wins over file mtime when present: a file rewritten today
    whose contents say "data_date: 2026-05-31" is stale data in a fresh file,
    and the contents are the thing that matters.
    """
    when = None
    if stated_date:
        try:
            when = datetime.fromisoformat(str(stated_date)[:19])
        except Exception:
            when = None
    if when is None:
        when = _mtime(name)
    if when is None:
        return {"as_of": None, "age_hours": None, "stale": True,
                "why": f"{name} is missing"}
    age = (datetime.now() - when).total_seconds() / 3600
    limit = STALE_AFTER_HOURS.get(kind, 24)
    return {"as_of": when.isoformat(timespec="seconds"),
            "age_hours": round(age, 1),
            "stale": age > limit,
            "why": (f"{name} is {round(age / 24, 1)} days old (fresh under "
                    f"{limit}h)" if age > limit else None)}


# ── the blocks ──────────────────────────────────────────────────────────────

def _market() -> dict:
    signals = _read_json("signals.json") or {}
    macro = _read_json("macro_data.json") or {}
    brain = _read_json("brain_state.json") or {}

    sig_age = _age_block("signals.json", "signals")
    ml_age = _age_block("ml_predictions.json", "ml")
    ml_rows = _read_json("ml_predictions.json") or {}
    # `data_date` is when the snapshot was BUILT. With the keyless feeds wired
    # in, that is today on every refresh, so it alone would report a macro
    # block as fresh even if every underlying series had gone dark. The daily
    # series (Treasury, EFFR) are the ones that should drive staleness —
    # monthly statistics are legitimately weeks old and must not be flagged
    # for it.
    # Each series now classifies its OWN freshness against its publication
    # cadence (src/macro/feeds.py FREQUENCY), so the block no longer re-derives
    # it from an age heuristic that could not tell a monthly statistic from a
    # dead daily feed.
    _obs = macro.get("observations") or {}
    _bad = {k: v for k, v in _obs.items()
            if isinstance(v, dict) and v.get("status") in ("STALE", "MISSING",
                                                           "SOURCE_UNAVAILABLE")}
    macro_age = _age_block("macro_data.json", "macro", macro.get("data_date"))
    if _obs:
        if _bad:
            macro_age = {**macro_age, "stale": True,
                         "why": ("; ".join(
                             f"{k} is {v['status']}"
                             + (f" (last refresh {v.get('last_attempt_status')})"
                                if v.get("last_attempt_status") == "FAILED" else "")
                             for k, v in sorted(_bad.items())))}
        else:
            macro_age = {**macro_age, "stale": False, "why": None}
    brain_age = _age_block("brain_state.json", "brain", brain.get("timestamp"))

    scores = [v.get("composite_score", 0) for v in signals.values()
              if isinstance(v, dict)]
    n = len(scores) or 1
    bull = sum(1 for s in scores if s > 10)
    bear = sum(1 for s in scores if s < -10)
    neutral = n - bull - bear

    ranked = sorted((v for v in signals.values() if isinstance(v, dict)),
                    key=lambda x: x.get("composite_score", 0))
    vols = [v.get("realised_vol") for v in signals.values()
            if isinstance(v, dict) and isinstance(v.get("realised_vol"), (int, float))]

    return {
        # ML predictions, WITH their age. A probability with no timestamp is
        # indistinguishable from a current one, which is how a file frozen in
        # May kept being read as today's view.
        "ml": {
            "tickers": len(ml_rows),
            "bullish": sum(1 for v in ml_rows.values()
                           if isinstance(v, dict)
                           and v.get("overall_signal") == "Bullish"),
            "bearish": sum(1 for v in ml_rows.values()
                           if isinstance(v, dict)
                           and v.get("overall_signal") == "Bearish"),
            **ml_age,
        },
        "regime": {
            # The regime is claimed by two independent sources; report both
            # rather than silently preferring one. When they disagree, that
            # disagreement is itself the finding.
            "macro_model": macro.get("regime"),
            "brain": brain.get("regime"),
            "agree": bool(macro.get("regime") and brain.get("regime")
                          and macro.get("regime") == brain.get("regime")),
            "as_of": macro_age["as_of"],
            "stale": macro_age["stale"],
        },
        "breadth": {
            "tracked": len(scores), "bullish": bull, "bearish": bear,
            "neutral": neutral,
            "advancing_pct": round(bull / n * 100, 1),
            "net_tilt": round((bull - bear) / n * 100, 1),
            **sig_age,
        },
        "volatility": {
            "vix": macro.get("vix"),
            "vix_stale": macro_age["stale"],
            "median_realised_vol": (round(sorted(vols)[len(vols) // 2], 4) if vols else None),
            "n_measured": len(vols),
        },
        "macro": {
            "score": macro.get("macro_score"),
            "fed_funds_rate": macro.get("fed_funds_rate"),
            "treasury_10y": macro.get("treasury_10y"),
            "treasury_2y": macro.get("treasury_2y"),
            "yield_spread_10y2y": macro.get("yield_spread_10y2y"),
            "cpi_yoy": macro.get("cpi_yoy"),
            "unemployment_rate": macro.get("unemployment_rate"),
            "dxy": macro.get("dxy"),
            "missing_fields": macro.get("warnings") or [],
            # Phase 10: per-field date semantics. A monthly statistic fetched
            # today still describes last month, and the block-level `as_of`
            # cannot say that — so the per-series dates travel too, and the
            # oldest of them is what `_unknowns` reasons about.
            "observations": macro.get("observations") or {},
            "oldest_observation_days": max(
                [v.get("age_days") for v in (macro.get("observations") or {}).values()
                 if isinstance(v, dict) and v.get("age_days") is not None] or [0]) or None,
            **macro_age,
        },
        "leaders": [{"ticker": v.get("ticker"), "score": v.get("composite_score"),
                     "action": v.get("action")} for v in ranked[-5:][::-1]],
        "laggards": [{"ticker": v.get("ticker"), "score": v.get("composite_score"),
                      "action": v.get("action")} for v in ranked[:5]],
        "brain_focus": brain.get("focus_tickers") or [],
        "brain_alerts": (brain.get("alerts") or [])[:12],
        "brain_as_of": brain_age["as_of"],
    }


def _portfolio() -> dict:
    positions = _read_json("desk/positions.json")
    day = _read_json("desk/day_state.json") or {}
    scorecard = _read_json("desk/scorecard.json") or {}
    equity_rows = []
    p = DATA / "desk" / "equity_curve.jsonl"
    if p.exists():
        try:
            equity_rows = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines()
                           if l.strip()][-40:]
        except Exception:
            equity_rows = []

    pos_list: list = []
    if isinstance(positions, dict):
        pos_list = list(positions.values())
    elif isinstance(positions, list):
        pos_list = positions

    equity = day.get("start_equity")
    latest_equity = equity_rows[-1].get("equity") if equity_rows else None
    peak = max((r.get("equity") or 0) for r in equity_rows) if equity_rows else None
    drawdown = (round((latest_equity - peak) / peak, 4)
                if (latest_equity and peak) else None)

    # Concentration is only meaningful when there are positions to concentrate.
    weights = []
    for pos in pos_list:
        if isinstance(pos, dict):
            v = pos.get("notional") or pos.get("market_value")
            if isinstance(v, (int, float)) and latest_equity:
                weights.append(abs(v) / latest_equity)

    return {
        "open_positions": len(pos_list),
        "positions": pos_list[:25],
        "start_equity": equity,
        "equity": latest_equity,
        "peak_equity": peak,
        "drawdown_from_peak": drawdown,
        "day": {"date": day.get("date"), "trades_today": day.get("trades_count"),
                "notional_used": day.get("notional_used")},
        "concentration": {
            "largest_weight": round(max(weights), 4) if weights else None,
            "top3_weight": round(sum(sorted(weights)[-3:]), 4) if weights else None,
            "measurable": bool(weights),
            "why": None if weights else "no open positions to concentrate",
        },
        "scorecard": scorecard,
        **_age_block("desk/positions.json", "portfolio"),
    }


def _strategies() -> dict:
    lab = _read_json("quant_lab/state.json") or {}
    strategies = _read_json("quant_lab/strategies.json") or {}
    desk_cfg = _read_json("desk_config.json") or {}
    slate = _read_json("desk/slate.json") or {}

    rows = strategies if isinstance(strategies, list) else list(
        strategies.values()) if isinstance(strategies, dict) else []
    return {
        "lab": {"runs": lab.get("runs"), "last_run": lab.get("last_run"),
                "last_summary": lab.get("last_summary")},
        "catalogued": len(rows),
        "desk": {
            # Only keys that actually exist in data/desk_config.json. `mode` was
            # asked for here and is not a field that file has ever had.
            "auto_execute": desk_cfg.get("auto_execute"),
            "focus_tickers": desk_cfg.get("focus_tickers"),
            "min_conviction": desk_cfg.get("min_conviction"),
            "daily_notional_budget": desk_cfg.get("daily_notional_budget"),
            "max_trades_per_day": desk_cfg.get("max_trades_per_day"),
            "slate_size": len(slate.get("picks", []) if isinstance(slate, dict) else []),
        },
        "slate": (slate.get("picks") or [])[:10] if isinstance(slate, dict) else [],
    }


def _research() -> dict:
    eye = _read_json("research/eye_state.json") or {}
    watches = eye.get("watches") or {}
    obs_path = DATA / "research" / "observations.jsonl"
    recent_obs = []
    if obs_path.exists():
        try:
            lines = obs_path.read_text(encoding="utf-8").splitlines()
            for line in lines[-25:]:
                if line.strip():
                    recent_obs.append(json.loads(line))
        except Exception:
            pass
    recent_obs.reverse()

    # An "active narrative" is a subject several independent observations have
    # landed on recently. One article is not a narrative.
    from collections import Counter
    subjects = Counter(o.get("watch_id") for o in recent_obs if o.get("watch_id"))
    narratives = [{"subject": k, "mentions": v} for k, v in subjects.most_common(6) if v > 1]

    return {
        "watching": [{"id": k, "why": (v or {}).get("why") if isinstance(v, dict) else None}
                     for k, v in list(watches.items())[:20]],
        "watch_count": len(watches),
        "recent_observations": [
            {"title": o.get("title"), "source": o.get("source"),
             "salience": o.get("salience"), "why": o.get("why"),
             "url": o.get("url"), "seen_at": o.get("seen_at"),
             # §14/§46 — the reader must be able to tell a filing from a
             # Reddit rumour without leaving the page.
             "claim_type": (o.get("quality") or {}).get("claim_type"),
             "source_tier": (o.get("quality") or {}).get("source_tier"),
             "claim_confidence": (o.get("quality") or {}).get("claim_confidence")}
            for o in recent_obs[:10]],
        "active_narratives": narratives,
        "evidence_mix": _evidence_mix(recent_obs),
        **_age_block("research/observations.jsonl", "research"),
    }


def _evidence_mix(observations: list[dict]) -> dict:
    """What KIND of evidence ARIA is currently looking at.

    A watchlist fed entirely by aggregators and anonymous posts is a different
    epistemic position from one carrying filings, and the difference should be
    visible without reading every row. §14: repetition across aggregators is
    not corroboration.
    """
    from collections import Counter
    claims = Counter((o.get("quality") or {}).get("claim_type") or "UNSTAMPED"
                     for o in observations)
    tiers = Counter((o.get("quality") or {}).get("source_tier") or "UNSTAMPED"
                    for o in observations)
    unstamped = claims.get("UNSTAMPED", 0)
    note = None
    if observations and unstamped == len(observations):
        note = ("None of these observations carry an evidence tier — they were "
                "collected before the classifier existed. New ones are stamped.")
    elif claims:
        weak = claims.get("RUMOUR", 0) + claims.get("CLAIM", 0) + claims.get("UNCLASSIFIED", 0)
        if weak > len(observations) / 2:
            note = (f"{weak} of {len(observations)} recent observations are "
                    f"rumour, unverified claim or unclassified. Little of what I "
                    f"am watching is primary evidence.")
    return {"by_claim_type": dict(claims), "by_source_tier": dict(tiers),
            "note": note}


def _record() -> dict:
    """The track record, measured on INDEPENDENT events.

    `ledger.calibration()` counts ledger rows. src.core.calibration counts
    distinct events, keeps populations apart and attaches an interval to every
    rate — which is the difference between "confidence is inverted" and "the
    sample cannot tell yet". Both are reported: the first is what is stored,
    the second is what may be concluded.
    """
    try:
        from src.core import ledger
        st = ledger.stats()
    except Exception as e:
        return {"error": str(e)}
    out = {"stats": st}
    try:
        from src.core import calibration as _cal
        out["investigation"] = _cal.investigate()
        out["headline"] = _cal.summary_line()
    except Exception as e:
        out["investigation"] = {"error": str(e)}
    try:
        # §16 — WHY, not just whether. The reading separates decisive moves
        # from noise-band ones, which is the difference between a thesis
        # failure and a coin landing on its edge.
        from src.core import attribution as _attr
        d = _attr.decompose()
        out["attribution"] = {"reading": d.get("reading"),
                              "reason_mix": d.get("reason_mix"),
                              "decisive_moves": d.get("decisive_moves"),
                              "by_regime_measurable": (d.get("by_regime") or {}).get("measurable")}
    except Exception as e:
        out["attribution"] = {"error": str(e)}
    return out


def _system() -> dict:
    out: dict = {}
    try:
        from src.core import workers
        out["workers"] = workers.status()
    except Exception as e:
        out["workers"] = {"error": str(e)}
    try:
        from src.brain import self_state
        out["senses"] = self_state.report_cached()
    except Exception as e:
        out["senses"] = {"error": str(e)}
    try:
        from src.core import bus
        out["activity_24h"] = bus.counts_since(24)
    except Exception as e:
        out["activity_24h"] = {"error": str(e)}
    return out


def _unknowns(market: dict, portfolio: dict, record: dict, system: dict,
              research: Optional[dict] = None) -> list[str]:
    """What ARIA cannot currently see. Stated plainly, in its own voice.

    This is the most important block in the file. Every entry here is a place
    where a confident answer would be unfounded, and ARIA is expected to
    volunteer these rather than wait to be caught out.
    """
    u: list[str] = []
    if market["macro"].get("stale"):
        u.append(f"Macro is stale — {market['macro'].get('why')}. I should not be "
                 f"quoting VIX or the macro score as current.")
    if market["breadth"].get("stale"):
        u.append(f"The signal pipeline is stale — {market['breadth'].get('why')}. "
                 f"Breadth reflects the last completed run, not today.")
    ml = market.get("ml") or {}
    # Absence first, then age. "I have none" and "mine are old" are different
    # statements and the first is the more useful one — a missing file also
    # reads as stale, so checking staleness first would report every absence as
    # an ageing problem and never say the predictions are simply not there.
    if not ml.get("tickers"):
        u.append("I hold no ML predictions at all, so anything I say about "
                 "model-implied direction is coming from somewhere else.")
    elif ml.get("stale"):
        u.append(f"My ML predictions are stale — {ml.get('why')}. They come from "
                 f"the last full pipeline run, and I should not present them as "
                 f"a current view.")
    if not market["regime"]["agree"] and market["regime"]["brain"] and market["regime"]["macro_model"]:
        u.append(f"My two regime estimates disagree: the macro model says "
                 f"'{market['regime']['macro_model']}', the brain says "
                 f"'{market['regime']['brain']}'.")
    for w in (market["macro"].get("missing_fields") or []):
        if "Missing fields" in str(w) or "unavailable" in str(w):
            u.append(str(w))
    if not portfolio["concentration"]["measurable"]:
        u.append("No open positions, so portfolio risk, concentration and factor "
                 "exposure are not measurable right now.")
    inv = (record.get("investigation") or {})
    overall = inv.get("overall") or {}
    if overall.get("verdict", "").startswith(("undecided", "indistinguishable")):
        u.append(f"My directional record is {overall.get('rate')} over "
                 f"{overall.get('n')} independent events (95% CI "
                 f"{overall.get('ci95')}) — {overall.get('verdict')}. I should "
                 f"not present a track record as evidence of skill yet.")
    warn = (inv.get("duplication") or {}).get("population_warning")
    if warn:
        u.append(warn)
    cal = (inv.get("calibration") or {})
    if cal.get("measurable") is False:
        u.append(cal.get("note") or "Confidence calibration is not yet measurable.")
    degraded = ((system.get("workers") or {}).get("degraded") or [])
    for d in degraded:
        u.append(f"{d.get('label')} is {d.get('state')} — anything downstream of it "
                 f"is not updating.")
    # §14 — the quality of what ARIA is reading is itself a blind spot worth
    # volunteering. A watchlist fed entirely by aggregators is a different
    # epistemic position from one carrying filings.
    mix_note = ((research or {}).get("evidence_mix") or {}).get("note")
    if mix_note:
        u.append(mix_note)
    return u


# ── assembly ────────────────────────────────────────────────────────────────

def snapshot() -> dict:
    """Assemble the whole belief state. Cheap enough to call per request."""
    market = _market()
    portfolio = _portfolio()
    record = _record()
    system = _system()
    strategies = _strategies()
    research = _research()

    stale_blocks = [name for name, blk in
                    (("market.breadth", market["breadth"]),
                     ("market.macro", market["macro"]),
                     ("portfolio", portfolio),
                     ("research", research))
                    if blk.get("stale")]

    return {
        "at": datetime.now().isoformat(timespec="seconds"),
        "market": market,
        "portfolio": portfolio,
        "strategies": strategies,
        "research": research,
        "record": record,
        "system": system,
        "stale_blocks": stale_blocks,
        "unknowns": _unknowns(market, portfolio, record, system, research),
    }


def _digest(snap: dict) -> str:
    """The handful of values whose change is worth calling a change.

    Deliberately narrow: if the digest included every number, every snapshot
    would differ from the last and "what changed?" would answer "everything",
    which is the same as answering nothing.
    """
    m, p = snap["market"], snap["portfolio"]
    return json.dumps({
        "regime_macro": m["regime"]["macro_model"],
        "regime_brain": m["regime"]["brain"],
        "bullish": m["breadth"]["bullish"],
        "bearish": m["breadth"]["bearish"],
        "vix": m["volatility"]["vix"],
        "positions": p["open_positions"],
        "equity": p["equity"],
        "resolved": (snap["record"].get("stats") or {}).get("resolved"),
        "stale": snap["stale_blocks"],
    }, sort_keys=True, default=str)


def save_snapshot(snap: Optional[dict] = None) -> dict:
    """Persist a snapshot if the digest moved. Returns what changed.

    Storing identical snapshots on a timer would make the history unreadable,
    so only genuine movement is recorded — which also means the snapshot table
    doubles as a log of when ARIA's worldview actually shifted.
    """
    _ensure()
    snap = snap or snapshot()
    dig = _digest(snap)
    try:
        with _db() as conn:
            last = conn.execute(
                "SELECT digest FROM world_snapshots ORDER BY id DESC LIMIT 1").fetchone()
            if last and last["digest"] == dig:
                return {"stored": False, "reason": "worldview unchanged"}
            conn.execute("INSERT INTO world_snapshots (at, digest, snapshot) VALUES (?,?,?)",
                         (snap["at"], dig, json.dumps(snap, default=str)))
    except Exception as e:
        logger.warning("world.save_snapshot failed: %s", e)
        return {"stored": False, "error": str(e)}

    from src.core.bus import publish
    publish("MARKET_UPDATE", "World model updated", source="world",
            payload=json.loads(dig))
    return {"stored": True, "digest": json.loads(dig)}


def changes(hours: int = 24) -> dict:
    """What moved in ARIA's worldview over the window — §20's second question.

    Returns field-level before/after pairs, not a prose summary. The prose is
    the language model's job; the facts are this module's.
    """
    _ensure()
    since = (datetime.now() - timedelta(hours=hours)).isoformat(timespec="seconds")
    try:
        with _db() as conn:
            rows = conn.execute(
                "SELECT at, digest FROM world_snapshots WHERE at >= ? ORDER BY at",
                (since,)).fetchall()
            if not rows:
                oldest = conn.execute(
                    "SELECT at, digest FROM world_snapshots ORDER BY id DESC LIMIT 1"
                ).fetchone()
                rows = [oldest] if oldest else []
    except Exception as e:
        return {"window_hours": hours, "error": str(e), "changed": []}

    if len(rows) < 2:
        return {"window_hours": hours, "changed": [],
                "note": ("Not enough history yet — the world model needs at least two "
                         "stored snapshots before it can say what changed."),
                "snapshots_in_window": len(rows)}

    first, last = json.loads(rows[0]["digest"]), json.loads(rows[-1]["digest"])
    changed = []
    for k in sorted(set(first) | set(last)):
        a, b = first.get(k), last.get(k)
        if a != b:
            changed.append({"field": k, "from": a, "to": b})

    events = []
    try:
        from src.core import bus
        events = bus.recent(limit=25, since=since, min_severity="notable")
    except Exception:
        pass

    return {"window_hours": hours,
            "from": rows[0]["at"], "to": rows[-1]["at"],
            "snapshots_in_window": len(rows),
            "changed": changed,
            "notable_events": [{"ts": e["ts"], "kind": e["kind"],
                                "summary": e["summary"], "subject": e.get("subject")}
                               for e in events]}


def brief() -> str:
    """The world model as a paragraph a language model can be given as context.

    Kept tight on purpose — this is injected into every chat turn, so it has a
    token budget, and a summary nobody can afford to include is worth nothing.
    """
    s = snapshot()
    m, p, r = s["market"], s["portfolio"], s["record"]
    stats = r.get("stats") or {}
    parts = [
        f"Regime: {m['regime']['macro_model'] or 'unknown'}"
        + ("" if m["regime"]["agree"] else f" (brain reads it as {m['regime']['brain']})"),
        f"Breadth: {m['breadth']['bullish']} bullish / {m['breadth']['bearish']} bearish "
        f"of {m['breadth']['tracked']} tracked.",
        f"Leaders: {', '.join(x['ticker'] for x in m['leaders'][:3] if x.get('ticker'))}. "
        f"Laggards: {', '.join(x['ticker'] for x in m['laggards'][:3] if x.get('ticker'))}.",
        f"Portfolio: {p['open_positions']} open positions"
        + (f", equity {p['equity']}" if p.get("equity") else "") + ".",
        # The headline is the interval-aware sentence, not the bare rate: a
        # rate without its n reads as a claim about skill.
        "Record: " + (r.get("headline") or
                      f"{stats.get('resolved', 0)} of {stats.get('total', 0)} graded."),
    ]
    if s["unknowns"]:
        parts.append("Known blind spots: " + " ".join(s["unknowns"][:4]))
    return " ".join(parts)
