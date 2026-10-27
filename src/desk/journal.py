"""
src/desk/journal.py
===================
THE TRADE JOURNAL — every desk decision and what happened afterwards.

Step 1 of making the desk trade on its own. Everything after it (post-mortems,
per-regime weighting, comparing versions) is computed from these two tables,
so the rule here is the ledger's rule: write what was decided at the moment it
was decided, and grade it only against prices that came after.

TABLES (data/desk/aria_journal.db)
---------------------------------
decisions   one row per desk debate — BUY, SELL and NO_TRADE alike. A desk
            that only records what it traded can tell you what it lost, never
            what it missed.
outcomes    one row per (decision, kind):
              trade        the realised round trip, after estimated costs
              horizon_1d   the decision's view graded 1 trading day on
              horizon_1w   the same, 5 trading days on
            Every decision gets the two horizon grades, traded or not, so the
            traded and untraded populations can be compared on equal terms.

THE VOTERS
----------
The desk's real voters, recorded with a one-line reason each: the three
per-ticker analysts (technical, fundamental, sentiment), the macro conditioner,
the bull and bear researchers, and the judge. The judge's verdict is computed
deterministically from the analysts; the researchers argue but do not vote, so
they are graded as positions (bull right if the price rose) rather than as
inputs to the verdict.

SENTINEL's second opinion is recorded where TradingAgents' agreement would have
gone — it is the one independent reviewer the desk actually has. It is only
consulted for trades the planner sizes, so a NO_TRADE row says NOT_CONSULTED.

GRADING
-------
The horizon grades are not computed here. Each decision is mirrored into the
ledger (src/core/ledger.py) as two predictions, and the ledger's resolver —
which checks instrument identity before it touches a price and grades on the
close of the horizon date — does the grading. This module copies those grades
back and adds what the ledger does not track: the worst and best price seen
(sampled hourly, so it is approximate and labelled as such), whether the stop
or target was touched, the hypothetical P&L after costs, and which voter was
right. One grader, not two that can disagree.

Nothing in this module may raise into the desk. Every public function catches
and logs; a journal outage costs a row, never a trade or a cycle.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent.parent.parent
JOURNAL_DB = ROOT / "data" / "desk" / "aria_journal.db"

JOURNAL_SCHEMA_VERSION = 1

#: Estimated cost PER SIDE, in basis points, covering fee + spread + slippage.
#: Alpaca equities are commission-free, so 5 bps is spread and slippage on
#: liquid names. Alpaca crypto charges a taker fee of about 25 bps; 30 adds the
#: spread. Estimates, labelled as such on every row; overridable in the desk
#: config (cost_bps_equity / cost_bps_crypto).
DEFAULT_COST_BPS = {"equity": 5.0, "crypto": 30.0}

#: |net P&L| at or under this (in %) is a scratch, not a win or a loss.
SCRATCH_BAND_PCT = 0.10

#: Decisions are price-tracked for this long — past the 1-week horizon with
#: room for weekends and a late ledger grade.
TRACK_DAYS = 12

HORIZONS = {"horizon_1d": 1, "horizon_1w": 5}     # trading days

PER_TICKER_VOTERS = ("technical", "fundamental", "sentiment")
VOTERS = PER_TICKER_VOTERS + ("macro", "bull", "bear", "judge")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS decisions (
    id               TEXT PRIMARY KEY,      -- the debate id
    created_at       TEXT NOT NULL,
    ticker           TEXT NOT NULL,
    asset_class      TEXT,
    direction        TEXT NOT NULL,         -- long | short | no-trade
    verdict          TEXT NOT NULL,         -- BUY | SELL | NO_TRADE
    view             TEXT,                  -- the judge's net view: bull | bear | neutral
    confidence       INTEGER,               -- judge conviction 0..100
    conviction_bar   INTEGER,
    horizon          TEXT,                  -- '1d,1w' graded; trades also max-hold
    max_hold_days    INTEGER,
    entry_price      REAL,                  -- price when the decision was made
    stop_loss        REAL,                  -- planned
    target           REAL,                  -- planned
    invalidation     REAL,
    tis_signals      TEXT,                  -- JSON: the signal record that fed it
    votes            TEXT,                  -- JSON: {voter: {view, conviction, reason}}
    vote_technical   TEXT,
    vote_fundamental TEXT,
    vote_sentiment   TEXT,
    vote_macro       TEXT,
    contradictions   TEXT,                  -- JSON list
    n_contradictions INTEGER,
    sentinel         TEXT,                  -- JSON: SENTINEL second opinion
    sentinel_status  TEXT,
    regime           TEXT,
    risk_multiplier  REAL,
    aria_version     TEXT,
    prompt_version   TEXT,
    llm              TEXT,
    judge_weights    TEXT,                  -- JSON: agent weights in force
    slate_status     TEXT,                  -- slated | rejected: … | not proposed
    planned_qty      REAL,
    executed         INTEGER NOT NULL DEFAULT 0,
    execution_mode   TEXT,                  -- auto | queued | failed | …
    trade_id         TEXT,
    fill_price       REAL,
    filled_at        TEXT,
    status           TEXT NOT NULL DEFAULT 'open',   -- open | traded | closed
    track_low        REAL,                  -- lowest price seen while tracked
    track_high       REAL,                  -- highest price seen while tracked
    track_last       REAL,
    track_last_at    TEXT,
    stop_touched_at  TEXT,
    target_touched_at TEXT,
    ledger_pred_1d   TEXT,
    ledger_pred_1w   TEXT
);
CREATE INDEX IF NOT EXISTS idx_jdec_created ON decisions(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_jdec_ticker  ON decisions(ticker, created_at DESC);

CREATE TABLE IF NOT EXISTS outcomes (
    decision_id      TEXT NOT NULL REFERENCES decisions(id),
    kind             TEXT NOT NULL,         -- trade | horizon_1d | horizon_1w
    status           TEXT NOT NULL,         -- final | partial | ungradable
    exit_at          TEXT,
    exit_price       REAL,
    underlying_return_pct REAL,             -- price move, ignoring direction
    pnl_pct_gross    REAL,                  -- in the decision's direction
    pnl_pct_net      REAL,                  -- after estimated costs
    pnl_abs_gross    REAL,                  -- trades only
    pnl_abs_net      REAL,
    costs_abs        REAL,
    cost_bps_per_side REAL,
    mae_pct          REAL,                  -- worst move against (<= 0)
    mfe_pct          REAL,                  -- best move in favour (>= 0)
    mae_price        REAL,
    mfe_price        REAL,
    excursion_basis  TEXT,                  -- how MAE/MFE were measured
    stop_hit         INTEGER,
    target_hit       INTEGER,
    result           TEXT,                  -- win | loss | scratch | NULL
    right_voters     TEXT,                  -- JSON {voter: true|false|null}
    exit_reason      TEXT,
    estimated        INTEGER NOT NULL DEFAULT 0,
    legs             TEXT,                  -- JSON: scale-out legs of one trade
    note             TEXT,
    graded_at        TEXT NOT NULL,
    PRIMARY KEY (decision_id, kind)
);
CREATE INDEX IF NOT EXISTS idx_jout_kind ON outcomes(kind, graded_at DESC);
"""

_lock = threading.RLock()


@contextmanager
def _connect():
    path = Path(JOURNAL_DB)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        with _lock:
            conn.executescript(_SCHEMA)
            yield conn
            conn.commit()
    finally:
        conn.close()


# ── versions ────────────────────────────────────────────────────────────────

def aria_version() -> str:
    """The code that made the decision: ARIA_VERSION, else the git commit.

    Read from .git directly rather than by running git — the desk must not
    spawn processes, and a missing .git (a copied deploy) just reads 'unknown'.
    """
    env = os.environ.get("ARIA_VERSION")
    if env:
        return env.strip()[:40]
    try:
        git = ROOT / ".git"
        head = (git / "HEAD").read_text(encoding="utf-8").strip()
        if head.startswith("ref:"):
            ref = head.split(" ", 1)[1].strip()
            loose = git / ref
            if loose.exists():
                return loose.read_text(encoding="utf-8").strip()[:12]
            packed = git / "packed-refs"
            if packed.exists():
                for line in packed.read_text(encoding="utf-8").splitlines():
                    if line.endswith(" " + ref):
                        return line.split(" ", 1)[0][:12]
            return "unknown"
        return head[:12]
    except Exception:
        return "unknown"


# ── costs ───────────────────────────────────────────────────────────────────

def cost_bps(asset_class: str, cfg: Optional[dict] = None) -> float:
    ac = "crypto" if (asset_class or "").lower().startswith("crypto") else "equity"
    cfg = cfg or {}
    try:
        v = float(cfg.get(f"cost_bps_{ac}", DEFAULT_COST_BPS[ac]))
        return v if v >= 0 and v == v else DEFAULT_COST_BPS[ac]
    except Exception:
        return DEFAULT_COST_BPS[ac]


def trade_costs(asset_class: str, entry: float, exit_price: float, qty: float,
                cfg: Optional[dict] = None) -> dict:
    """Estimated round-trip cost of a trade: bps per side on each side's
    notional. Returned as an absolute amount and as % of entry notional."""
    bps = cost_bps(asset_class, cfg)
    entry, exit_price, qty = float(entry or 0), float(exit_price or 0), abs(float(qty or 0))
    costs = bps / 10_000.0 * qty * (entry + exit_price)
    basis = entry * qty
    return {"cost_bps_per_side": bps, "costs": round(costs, 4),
            "cost_pct": round(costs / basis * 100.0, 4) if basis else 0.0}


def round_trip_cost_pct(asset_class: str, cfg: Optional[dict] = None) -> float:
    """Cost of a hypothetical round trip as % of entry (no qty needed)."""
    return 2 * cost_bps(asset_class, cfg) / 100.0


def _result(pnl_pct_net: Optional[float]) -> Optional[str]:
    if pnl_pct_net is None:
        return None
    if pnl_pct_net > SCRATCH_BAND_PCT:
        return "win"
    if pnl_pct_net < -SCRATCH_BAND_PCT:
        return "loss"
    return "scratch"


# ── reading a transcript ────────────────────────────────────────────────────

def _one_line(text, cap: int = 200) -> str:
    s = " ".join(str(text or "").split())
    for stop in (". ", "\n"):
        if stop in s[:cap]:
            s = s[:s.index(stop, 0, cap) + 1]
            break
    return s[:cap]


def _direction(verdict: str) -> str:
    return {"BUY": "long", "SELL": "short"}.get(verdict, "no-trade")


def _sign(view_or_direction: str) -> int:
    v = (view_or_direction or "").lower()
    if v in ("bull", "long", "buy"):
        return 1
    if v in ("bear", "short", "sell"):
        return -1
    return 0


def votes_from_transcript(t: dict) -> dict:
    """{voter: {view, conviction, reason}} for every real desk voter."""
    votes: dict = {}
    for op in t.get("opinions") or []:
        agent = op.get("agent")
        if agent in PER_TICKER_VOTERS or agent == "macro":
            votes[agent] = {"view": op.get("view"),
                            "conviction": op.get("conviction"),
                            "reason": _one_line(op.get("thesis"))}
    rounds = [r for r in (t.get("rounds") or []) if isinstance(r.get("round"), int)]
    last = rounds[-1] if rounds else {}
    votes["bull"] = {"view": "bull", "conviction": None,
                     "reason": _one_line(last.get("bull"))}
    votes["bear"] = {"view": "bear", "conviction": None,
                     "reason": _one_line(last.get("bear"))}
    judge = t.get("judge") or {}
    votes["judge"] = {"view": judge.get("view"), "verdict": judge.get("verdict"),
                      "conviction": judge.get("conviction"),
                      "reason": _one_line(judge.get("reasoning"))}
    return votes


_SIGNAL_KEYS = ("composite_score", "action", "confidence", "bullish_prob",
                "bearish_prob", "trend_score", "momentum_score",
                "volatility_score", "regime_score", "macro_score",
                "sentiment_score", "risk_level", "realised_vol", "atr_pct",
                "regime", "drivers", "risks", "current_price")


def _signal_snapshot(ticker: str) -> dict:
    try:
        from src.desk.opinion import load_data_json
        sig = load_data_json("signals.json").get(ticker) or {}
    except Exception:
        sig = {}
    return sig


def _probability(conviction) -> Optional[float]:
    """Conviction 0-100 onto 0.5-0.95 — the same mapping the ledger's desk
    importer uses, so journal rows and imported rows calibrate alike."""
    if conviction is None:
        return None
    return round(0.5 + min(max(float(conviction), 0), 100) / 100 * 0.45, 4)


# ── writing: decisions ──────────────────────────────────────────────────────

def log_decision(transcript: dict, conditioner=None,
                 cfg: Optional[dict] = None) -> Optional[str]:
    """Record one debate verdict — BUY, SELL or NO_TRADE. Never raises.

    Idempotent on the debate id: a second call for the same debate is a no-op.
    """
    try:
        return _log_decision(transcript, conditioner, cfg or {})
    except Exception as e:
        logger.warning("journal: log_decision failed for %s: %s",
                       (transcript or {}).get("id"), e)
        return None


def _log_decision(t: dict, conditioner, cfg: dict) -> Optional[str]:
    did, ticker = t.get("id"), t.get("ticker")
    if not did or not ticker:
        return None
    judge = t.get("judge") or {}
    verdict = judge.get("verdict") or "NO_TRADE"
    sig = _signal_snapshot(ticker)
    votes = votes_from_transcript(t)
    asset_class = ("crypto" if ("crypto" in str(sig.get("asset_class", "")).lower()
                                or ticker.endswith("-USD")) else "equity")
    entry = sig.get("current_price")
    try:
        from src.desk.scorecard import current_weights
        weights = current_weights()
    except Exception:
        weights = None
    sentinel = ({"sentinel_status": "NOT_CONSULTED",
                 "why": "no trade proposed — SENTINEL is only asked about sized trades"}
                if verdict == "NO_TRADE" else
                {"sentinel_status": "PENDING", "why": "awaiting the planner"})
    max_hold = int(cfg.get("max_hold_days", 10) or 10)
    row = {
        "id": did,
        "created_at": t.get("at") or datetime.now().isoformat(),
        "ticker": ticker,
        "asset_class": asset_class,
        "direction": _direction(verdict),
        "verdict": verdict,
        "view": judge.get("view"),
        "confidence": judge.get("conviction"),
        "conviction_bar": judge.get("conviction_bar"),
        "horizon": "1d,1w" + (f"; max hold {max_hold}d" if verdict != "NO_TRADE" else ""),
        "max_hold_days": max_hold if verdict != "NO_TRADE" else None,
        "entry_price": entry,
        "stop_loss": sig.get("stop_loss"),
        "target": sig.get("take_profit"),
        "invalidation": judge.get("invalidation_level") or sig.get("invalidation"),
        "tis_signals": json.dumps({k: sig.get(k) for k in _SIGNAL_KEYS if k in sig},
                                  default=str),
        "votes": json.dumps(votes, default=str),
        "vote_technical": (votes.get("technical") or {}).get("view"),
        "vote_fundamental": (votes.get("fundamental") or {}).get("view"),
        "vote_sentiment": (votes.get("sentiment") or {}).get("view"),
        "vote_macro": (votes.get("macro") or {}).get("view"),
        "contradictions": json.dumps(t.get("contradictions") or [], default=str),
        "n_contradictions": len(t.get("contradictions") or []),
        "sentinel": json.dumps(sentinel),
        "sentinel_status": sentinel["sentinel_status"],
        "regime": judge.get("regime") or getattr(conditioner, "regime", None),
        "risk_multiplier": judge.get("risk_multiplier")
                           or getattr(conditioner, "risk_multiplier", None),
        "aria_version": aria_version(),
        "prompt_version": t.get("prompt_version"),
        "llm": t.get("llm"),
        "judge_weights": json.dumps(weights) if weights else None,
        "slate_status": "not proposed" if verdict == "NO_TRADE" else "pending",
        "track_low": entry,
        "track_high": entry,
        "track_last": entry,
        "track_last_at": t.get("at") or datetime.now().isoformat(),
    }
    cols = ", ".join(row)
    marks = ", ".join("?" for _ in row)
    with _connect() as conn:
        cur = conn.execute(f"INSERT OR IGNORE INTO decisions ({cols}) VALUES ({marks})",
                           tuple(row.values()))
        inserted = bool(cur.rowcount)
    if inserted:
        _mirror_to_ledger(row, votes)
    return did


def _mirror_to_ledger(row: dict, votes: dict) -> None:
    """Two falsifiable claims per decision — the view over 1 and 5 trading
    days — so the ledger's identity-checked resolver grades them."""
    try:
        from src.core import ledger
    except Exception as e:
        logger.debug("journal: ledger unavailable: %s", e)
        return
    if not row.get("entry_price"):
        return          # no reference price → nothing the ledger could grade
    view = (row.get("view") or "neutral").lower()
    direction = view if view in ("bull", "bear") else "neutral"
    prob = _probability(row.get("confidence"))
    ids = {}
    for kind, days in HORIZONS.items():
        tag = kind.split("_")[1]
        try:
            ids[kind] = ledger.record_prediction(
                subject=row["ticker"], source="desk-journal",
                claim=(f"desk {row['verdict']} — {direction} view over "
                       f"{days} trading day(s)"),
                direction=direction, probability=prob, confidence=prob,
                horizon_days=days, price_at=row["entry_price"],
                regime_at=row.get("regime"),
                strategy=f"desk-debate:{row.get('prompt_version') or '?'}",
                rationale=(votes.get("judge") or {}).get("reason", "")[:1200],
                evidence=[{"voter": k, **{kk: vv for kk, vv in v.items()
                                          if kk in ("view", "conviction")}}
                          for k, v in votes.items()],
                invalidation=(f"price through {row['invalidation']}"
                              if row.get("invalidation") else ""),
                created_at=str(row["created_at"])[:19],
                external_ref=f"journal:{row['id']}:{tag}", announce=False)
        except Exception as e:
            logger.debug("journal: ledger mirror %s failed: %s", kind, e)
    if ids:
        with _connect() as conn:
            conn.execute("UPDATE decisions SET ledger_pred_1d=?, ledger_pred_1w=? "
                         "WHERE id=?", (ids.get("horizon_1d"), ids.get("horizon_1w"),
                                        row["id"]))


def _sentinel_summary(op) -> Optional[dict]:
    if not isinstance(op, dict):
        return None
    keep = ("sentinel_status", "sentinel_position", "consulted", "ok", "reason",
            "why", "summary", "request_id")
    out = {k: op.get(k) for k in keep if k in op}
    if isinstance(out.get("summary"), str):
        out["summary"] = out["summary"][:600]
    return out


def record_slate(slate_result: dict, exec_results: list) -> None:
    """After the risk officer and executor: what happened to each proposal,
    and SENTINEL's opinion on it. Never raises."""
    try:
        updates: dict = {}
        for e in slate_result.get("slate") or []:
            updates[e.get("debate_id")] = {
                "slate_status": "slated", "planned_qty": e.get("qty"),
                "stop_loss": e.get("stop"), "target": e.get("target"),
                "sentinel": _sentinel_summary(e.get("sentinel"))}
        for r in slate_result.get("rejected") or []:
            e = r.get("trade") or {}
            updates[e.get("debate_id")] = {
                "slate_status": f"rejected: {r.get('reason', '')}"[:300],
                "planned_qty": e.get("qty"), "stop_loss": e.get("stop"),
                "target": e.get("target"),
                "sentinel": _sentinel_summary(e.get("sentinel"))}
        for x in exec_results or []:
            u = updates.setdefault(x.get("debate_id"), {})
            u["execution_mode"] = x.get("mode")
            u["trade_id"] = x.get("trade_id")
            if x.get("mode") == "auto":
                u["executed"] = 1
                u["fill_price"] = x.get("fill_price")
                u["filled_at"] = x.get("at")
        with _connect() as conn:
            pending = conn.execute(
                "SELECT id FROM decisions WHERE slate_status='pending'").fetchall()
            for p in pending:          # proposed by the judge, never sized
                updates.setdefault(p["id"], {}).setdefault(
                    "slate_status", "not sized by the planner")
            for did, u in updates.items():
                if not did:
                    continue
                if "sentinel" in u:
                    s = u.pop("sentinel") or {
                        "sentinel_status": "NOT_CONSULTED",
                        "why": "the planner did not consult (disabled or skipped)"}
                    u["sentinel"] = json.dumps(s, default=str)
                    u["sentinel_status"] = s.get("sentinel_status")
                if u.get("executed"):
                    u["status"] = "traded"
                sets = ", ".join(f"{k}=?" for k in u)
                conn.execute(f"UPDATE decisions SET {sets} WHERE id=?",
                             (*u.values(), did))
    except Exception as e:
        logger.warning("journal: record_slate failed: %s", e)


def mark_executed(debate_id: str, fill_price: float, trade_id: str = "") -> None:
    """A position opened on this decision — via the cycle OR the reflex lane
    (which can fire on a NO_TRADE debate that landed just under the bar)."""
    if not debate_id:
        return
    try:
        with _connect() as conn:
            conn.execute("""UPDATE decisions SET executed=1, status='traded',
                            fill_price=COALESCE(fill_price, ?),
                            filled_at=COALESCE(filled_at, ?),
                            trade_id=COALESCE(NULLIF(trade_id, ''), ?)
                            WHERE id=?""",
                         (fill_price, datetime.now().isoformat(), trade_id, debate_id))
    except Exception as e:
        logger.warning("journal: mark_executed failed for %s: %s", debate_id, e)


# ── writing: outcomes ───────────────────────────────────────────────────────

def _decision(conn, did: str) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT * FROM decisions WHERE id=?", (did,)).fetchone()


def right_voters(votes: dict, underlying_return: Optional[float],
                 traded_pnl_net: Optional[float] = None,
                 verdict: Optional[str] = None) -> dict:
    """Which voter was right, given the price move.

    Analysts, macro and the two researchers are graded on direction: a bull
    view was right if the price rose, a bear view if it fell; a neutral or
    abstaining voter is null. The judge is graded on its DECISION: a trade was
    right if it made money after costs; a NO_TRADE was right if the view it
    declined to act on would not have won.
    """
    out: dict = {}
    if underlying_return is None:
        return {k: None for k in votes}
    for voter, v in (votes or {}).items():
        if voter == "judge":
            continue
        s = _sign((v or {}).get("view"))
        out[voter] = None if s == 0 or underlying_return == 0 else (
            s * underlying_return > 0)
    if traded_pnl_net is not None:
        out["judge"] = traded_pnl_net > 0
    elif verdict == "NO_TRADE":
        s = _sign((votes.get("judge") or {}).get("view"))
        out["judge"] = None if s == 0 else not (s * underlying_return > 0)
    else:
        out["judge"] = None
    return out


def log_outcome(record: dict, pos: Optional[dict] = None,
                cfg: Optional[dict] = None) -> None:
    """The realised outcome of a desk trade — called on every close leg.

    A scale-out and its runner are one trade: legs accumulate under one
    'trade' row and the totals are final when the last leg (fraction 1.0)
    closes. Never raises.
    """
    try:
        _log_outcome(record, pos or {}, cfg or {})
    except Exception as e:
        logger.warning("journal: log_outcome failed for %s: %s",
                       record.get("debate_id"), e)


def _excursions(direction: int, entry: float, low: Optional[float],
                high: Optional[float]) -> dict:
    """MAE (worst move against, <=0) and MFE (best in favour, >=0), in % of
    entry, from the lowest and highest prices seen."""
    if not entry or direction == 0:
        return {"mae_pct": None, "mfe_pct": None, "mae_price": None, "mfe_price": None}
    low = min(low, entry) if low else entry
    high = max(high, entry) if high else entry
    worst, best = (low, high) if direction > 0 else (high, low)
    return {
        "mae_pct": round(min(0.0, (worst - entry) / entry * 100 * direction), 4),
        "mfe_pct": round(max(0.0, (best - entry) / entry * 100 * direction), 4),
        "mae_price": worst, "mfe_price": best,
    }


def _log_outcome(record: dict, pos: dict, cfg: dict) -> None:
    did = record.get("debate_id")
    if not did:
        return          # adopted position — no decision to attach it to
    entry = float(record.get("entry_price") or 0.0)
    exit_price = float(record.get("exit_price") or 0.0)
    qty = float(record.get("qty") or 0.0)
    direction = 1 if record.get("entry_side", "buy") == "buy" else -1
    asset_class = pos.get("asset_class") or (
        "crypto" if str(record.get("ticker", "")).endswith("-USD") else "equity")
    costs = (record.get("costs") if record.get("costs") is not None
             else trade_costs(asset_class, entry, exit_price, qty, cfg)["costs"])
    gross = float(record.get("pnl") or 0.0)
    leg = {"at": record.get("at"), "qty": qty, "exit_price": exit_price,
           "pnl_gross": gross, "costs": costs, "fraction": record.get("fraction", 1.0),
           "reason": record.get("reason"), "estimated": bool(record.get("estimated"))}
    final = float(record.get("fraction") or 1.0) >= 1.0

    with _connect() as conn:
        d = _decision(conn, did)
        if d is None:
            return      # debate predates the journal
        prev = conn.execute("SELECT legs FROM outcomes WHERE decision_id=? AND "
                            "kind='trade'", (did,)).fetchone()
        legs = (json.loads(prev["legs"]) if prev and prev["legs"] else []) + [leg]
        tot_qty = sum(float(l["qty"]) for l in legs) or qty
        tot_gross = sum(float(l["pnl_gross"]) for l in legs)
        tot_costs = sum(float(l["costs"]) for l in legs)
        avg_exit = (sum(float(l["exit_price"]) * float(l["qty"]) for l in legs)
                    / tot_qty) if tot_qty else exit_price
        basis = entry * tot_qty
        pct_gross = round(tot_gross / basis * 100, 4) if basis else None
        pct_net = round((tot_gross - tot_costs) / basis * 100, 4) if basis else None
        exc = _excursions(direction, entry,
                          min(filter(None, [pos.get("low_water"), pos.get("high_water"),
                                            exit_price])) if pos else exit_price,
                          max(filter(None, [pos.get("low_water"), pos.get("high_water"),
                                            exit_price])) if pos else exit_price)
        reasons = " | ".join(str(l["reason"]) for l in legs if l.get("reason"))
        underlying = round((avg_exit - entry) / entry, 6) if entry else None
        votes = json.loads(d["votes"] or "{}")
        right = right_voters(votes, underlying,
                             traded_pnl_net=(tot_gross - tot_costs) if final else None)
        conn.execute("""INSERT OR REPLACE INTO outcomes
            (decision_id, kind, status, exit_at, exit_price, underlying_return_pct,
             pnl_pct_gross, pnl_pct_net, pnl_abs_gross, pnl_abs_net, costs_abs,
             cost_bps_per_side, mae_pct, mfe_pct, mae_price, mfe_price,
             excursion_basis, stop_hit, target_hit, result, right_voters,
             exit_reason, estimated, legs, note, graded_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
            did, "trade", "final" if final else "partial", record.get("at"),
            round(avg_exit, 6), round(underlying * 100, 4) if underlying is not None else None,
            pct_gross, pct_net, round(tot_gross, 4), round(tot_gross - tot_costs, 4),
            round(tot_costs, 4), cost_bps(asset_class, cfg),
            exc["mae_pct"], exc["mfe_pct"], exc["mae_price"], exc["mfe_price"],
            "position high/low water, checked every management tick",
            int(any("stop" in str(l.get("reason", "")).lower() for l in legs)),
            int(any("target" in str(l.get("reason", "")).lower() for l in legs)),
            _result(pct_net) if final else None, json.dumps(right),
            reasons[:500], int(any(l["estimated"] for l in legs)), json.dumps(legs),
            "costs are estimates (bps per side)", datetime.now().isoformat()))
        if final:
            conn.execute("UPDATE decisions SET status='closed', executed=1 WHERE id=?",
                         (did,))


# ── the hourly tracker ──────────────────────────────────────────────────────

def _default_price(ticker: str) -> Optional[float]:
    """Latest price: the broker quote (a read, allowed through the paper
    gate), else the pipeline's last signal price."""
    try:
        from src.desk.auto_executor import get_order_manager
        mgr = get_order_manager()
        if mgr and mgr._alpaca and mgr._alpaca.is_connected():
            q = mgr._alpaca.get_quote(ticker) or {}
            if q.get("mid"):
                return float(q["mid"])
    except Exception:
        pass
    try:
        p = _signal_snapshot(ticker).get("current_price")
        return float(p) if p else None
    except Exception:
        return None


def _touch(direction: int, level: Optional[float], entry: Optional[float],
           price: float, kind: str) -> bool:
    """Did `price` touch a stop/target on the side that makes sense for
    `direction`? A level on the wrong side (signal levels are drawn for a long)
    is ignored rather than read as an instant hit."""
    if not level or not entry or direction == 0:
        return False
    if kind == "stop":
        if direction > 0:
            return level < entry and price <= level
        return level > entry and price >= level
    if direction > 0:
        return level > entry and price >= level
    return level < entry and price <= level


def track_once(price_fn: Optional[Callable[[str], Optional[float]]] = None,
               now: Optional[datetime] = None) -> dict:
    """One pass of the hourly job. No human input.

    1. Sample the latest price for every decision still inside its tracking
       window: running low/high (for MAE/MFE) and the first stop/target touch.
    2. Copy back any horizon grade the ledger has produced, adding the
       hypothetical P&L after costs, excursions and which voter was right.
    """
    out = {"sampled": 0, "graded": 0, "errors": 0}
    try:
        price_fn = price_fn or _default_price
        now = now or datetime.now()
        since = (now - timedelta(days=TRACK_DAYS)).isoformat()
        with _connect() as conn:
            rows = conn.execute("SELECT * FROM decisions WHERE created_at >= ? "
                                "ORDER BY created_at", (since,)).fetchall()
        prices: dict = {}
        for d in rows:
            graded = _graded_kinds(d["id"])
            if all(k in graded for k in HORIZONS):
                continue
            t = d["ticker"]
            if t not in prices:
                try:
                    prices[t] = price_fn(t)
                except Exception:
                    prices[t] = None
            px = prices[t]
            if px:
                _sample(d, float(px), now)
                out["sampled"] += 1
            out["graded"] += _pull_ledger_grades(d["id"], now)
    except Exception as e:
        logger.warning("journal: track_once failed: %s", e)
        out["errors"] += 1
    if out["graded"]:
        logger.info("journal: %d horizon outcome(s) graded", out["graded"])
    return out


def _graded_kinds(did: str) -> set:
    with _connect() as conn:
        return {r["kind"] for r in conn.execute(
            "SELECT kind FROM outcomes WHERE decision_id=?", (did,))}


def _view_direction(d) -> int:
    s = _sign(d["direction"])
    return s if s else _sign(d["view"])


def _sample(d, px: float, now: datetime) -> None:
    direction = _view_direction(d)
    lo = min(x for x in (d["track_low"], px) if x is not None)
    hi = max(x for x in (d["track_high"], px) if x is not None)
    stop_at = d["stop_touched_at"] or (
        now.isoformat() if _touch(direction, d["stop_loss"], d["entry_price"], px,
                                  "stop") else None)
    tgt_at = d["target_touched_at"] or (
        now.isoformat() if _touch(direction, d["target"], d["entry_price"], px,
                                  "target") else None)
    with _connect() as conn:
        conn.execute("""UPDATE decisions SET track_low=?, track_high=?,
                        track_last=?, track_last_at=?, stop_touched_at=?,
                        target_touched_at=? WHERE id=?""",
                     (lo, hi, px, now.isoformat(), stop_at, tgt_at, d["id"]))


def _pull_ledger_grades(did: str, now: datetime) -> int:
    try:
        from src.core import ledger
    except Exception:
        return 0
    n = 0
    with _connect() as conn:
        d = _decision(conn, did)
        if d is None:
            return 0
        done = {r["kind"] for r in conn.execute(
            "SELECT kind FROM outcomes WHERE decision_id=?", (did,))}
    for kind, col in (("horizon_1d", "ledger_pred_1d"), ("horizon_1w", "ledger_pred_1w")):
        if kind in done or not d[col]:
            continue
        try:
            with ledger.connect() as lconn:
                p = lconn.execute("SELECT * FROM predictions WHERE id=?",
                                  (d[col],)).fetchone()
        except Exception as e:
            logger.debug("journal: ledger read failed: %s", e)
            continue
        if p is None or not p["resolved"]:
            continue
        _write_horizon(d, kind, p, now)
        n += 1
    return n


def _write_horizon(d, kind: str, p, now: datetime) -> None:
    direction = _view_direction(d)
    entry = d["entry_price"]
    ret = p["actual_return"]
    votes = json.loads(d["votes"] or "{}")
    if ret is None or p["price_end"] is None:
        status, gross, net, result = "ungradable", None, None, None
        right = {k: None for k in votes}
    else:
        status = "final"
        gross = round(ret * 100 * direction, 4) if direction else None
        net = (round(gross - round_trip_cost_pct(d["asset_class"]), 4)
               if gross is not None else None)
        result = _result(net)
        right = right_voters(votes, ret, verdict=d["verdict"])
    exc = _excursions(direction, entry, d["track_low"], d["track_high"])
    end = p["resolve_after"] or ""
    stop_hit = (int(bool(d["stop_touched_at"]) and d["stop_touched_at"][:10] <= end)
                if direction else None)
    tgt_hit = (int(bool(d["target_touched_at"]) and d["target_touched_at"][:10] <= end)
               if direction else None)
    with _connect() as conn:
        conn.execute("""INSERT OR IGNORE INTO outcomes
            (decision_id, kind, status, exit_at, exit_price, underlying_return_pct,
             pnl_pct_gross, pnl_pct_net, cost_bps_per_side, mae_pct, mfe_pct,
             mae_price, mfe_price, excursion_basis, stop_hit, target_hit, result,
             right_voters, exit_reason, estimated, note, graded_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
            d["id"], kind, status, end, p["price_end"],
            round(ret * 100, 4) if ret is not None else None, gross, net,
            cost_bps(d["asset_class"]), exc["mae_pct"], exc["mfe_pct"],
            exc["mae_price"], exc["mfe_price"],
            "hourly price samples while tracked — approximate, intraday extremes "
            "between samples are missed",
            stop_hit, tgt_hit, result, json.dumps(right),
            f"horizon {HORIZONS[kind]} trading day(s)", 0,
            ("hypothetical: the view as a position, costs estimated. "
             if d["verdict"] == "NO_TRADE" else "")
            + (f"ledger: {p['outcome_note']}" if p["outcome_note"] else "")
            + ("" if direction else " neutral view — no directional P&L."),
            now.isoformat()))


# ── reading ─────────────────────────────────────────────────────────────────

def decisions(limit: int = 200, ticker: Optional[str] = None) -> list[dict]:
    sql, args = "SELECT * FROM decisions", []
    if ticker:
        sql += " WHERE ticker=?"
        args.append(ticker.upper())
    sql += " ORDER BY created_at DESC LIMIT ?"
    args.append(int(limit))
    with _connect() as conn:
        return [dict(r) for r in conn.execute(sql, args)]


def outcomes(decision_id: Optional[str] = None, kind: Optional[str] = None) -> list[dict]:
    sql, args = "SELECT * FROM outcomes WHERE 1=1", []
    if decision_id:
        sql += " AND decision_id=?"
        args.append(decision_id)
    if kind:
        sql += " AND kind=?"
        args.append(kind)
    with _connect() as conn:
        return [dict(r) for r in conn.execute(sql + " ORDER BY graded_at", args)]
