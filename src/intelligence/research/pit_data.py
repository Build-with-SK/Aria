"""
src/intelligence/research/pit_data.py
=====================================
What a research query at instant T is allowed to read.

Every record type carries the instant it became KNOWABLE, and the visibility
rules live here, in one place, applied by the snapshot builder — not by each
provider. A provider that hands back a record from the future therefore still
cannot leak it: the builder drops it.

    bars           known once their venue's session closed (MarketData.history
                   already cuts with src/markets/sessions.bar_known_by)
    fundamentals   known from `available_at` (when the snapshot was taken) —
                   the vendor never says which filing a number reflects, so a
                   historical query before the first snapshot sees NOTHING,
                   never today's numbers
    events         an occurrence is public at its event_time; a SCHEDULE is
                   known only from its publication time or, failing that, the
                   time ARIA first observed it; a withdrawn schedule vanishes
                   from the moment it was withdrawn; outcome fields (reported
                   EPS, surprise) exist only once the event has happened
    news           known from max(published, seen): public AND in ARIA's hands
    macro          known from its release time, or failing that the time it
                   was retrieved (BLS publishes no release time)
    model records  calls made by T; an outcome only if graded by T

Two data sources share one interface:

    FrameResearchData   in-memory records (tests, replays) — nothing is fetched
    LiveResearchData    vendors and ARIA's own stores (production). It also
                        ARCHIVES fundamentals snapshots and events with the time
                        they were first observed (data/intelligence/research/),
                        so a point-in-time history accumulates from now on.
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
from dataclasses import asdict, dataclass, field, replace
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent.parent
ARCHIVE_DIR = ROOT / "data" / "intelligence" / "research"
OBSERVATIONS = ROOT / "data" / "research" / "observations.jsonl"
NEWS_LOOKBACK_DAYS = 14
_lock = threading.Lock()


# ── time ────────────────────────────────────────────────────────────────────

def aware(x, *, tz=None) -> Optional[datetime]:
    """An aware UTC datetime from a datetime, date or ISO string; None if not
    parseable. Naive values are read as this machine's local time (how ARIA's
    own stores write them); a bare date as midnight in `tz` (default UTC)."""
    if x is None or x == "":
        return None
    if isinstance(x, pd.Timestamp):
        x = x.to_pydatetime()
    if isinstance(x, str):
        s = x.strip().replace("Z", "+00:00")
        try:
            x = datetime.fromisoformat(s)
        except ValueError:
            try:
                x = date.fromisoformat(s[:10])
            except ValueError:
                return None
    if isinstance(x, datetime):
        if x.tzinfo is None:
            x = x.astimezone()
        return x.astimezone(timezone.utc)
    if isinstance(x, date):
        return datetime.combine(x, time(0), tz or timezone.utc).astimezone(timezone.utc)
    return None


def require_aware(at) -> datetime:
    a = at if isinstance(at, datetime) else None
    if a is None or a.tzinfo is None:
        raise ValueError("a research query needs an aware instant")
    return a.astimezone(timezone.utc)


# ── records ─────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class FundamentalSnapshot:
    symbol: str
    values: dict
    available_at: datetime                 # when ARIA could first see these numbers
    source: str
    filing_time: Optional[datetime] = None  # publication of the accounts, if known


@dataclass(frozen=True)
class Event:
    event_id: str
    kind: str              # EARNINGS | DIVIDEND | SPLIT | ECONOMIC_RELEASE | CENTRAL_BANK | ...
    scope: str             # an instrument, or "MARKET:US"
    event_time: datetime
    source: str
    importance: str        # HIGH | MEDIUM | LOW
    importance_basis: str = ""
    publication_time: Optional[datetime] = None
    observed_at: Optional[datetime] = None
    effective_time: Optional[datetime] = None
    withdrawn_at: Optional[datetime] = None
    relisted_at: Optional[datetime] = None
    schedule: dict = field(default_factory=dict)    # knowable before it happens
    outcome: dict = field(default_factory=dict)     # knowable only once it happened

    def schedule_known_at(self) -> Optional[datetime]:
        return self.publication_time or self.observed_at

    def visible_at(self, at: datetime) -> bool:
        if self.withdrawn_at is not None and self.withdrawn_at <= at and not (
                self.relisted_at is not None and self.relisted_at <= at):
            return False
        if self.event_time <= at:
            return True                          # it happened, publicly
        sk = self.schedule_known_at()
        return sk is not None and sk <= at

    def known_at(self) -> datetime:
        sk = self.schedule_known_at()
        return min(sk, self.event_time) if sk else self.event_time

    def view_at(self, at: datetime) -> dict:
        occurred = self.event_time <= at
        return {"event_id": self.event_id, "kind": self.kind, "scope": self.scope,
                "status": "OCCURRED" if occurred else "SCHEDULED",
                "event_time": self.event_time.isoformat(),
                "effective_time": self.effective_time.isoformat() if self.effective_time else None,
                "publication_time": (self.publication_time.isoformat()
                                     if self.publication_time else None),
                "observed_at": self.observed_at.isoformat() if self.observed_at else None,
                "known_at": self.known_at().isoformat(),
                "source": self.source, "importance": self.importance,
                "importance_basis": self.importance_basis,
                "schedule": dict(self.schedule),
                "outcome": dict(self.outcome) if occurred else {},
                "known_at_rule": ("publication time" if self.publication_time else
                                  "first observed by ARIA" if self.observed_at else
                                  "occurrence (no earlier publication time is known)")}


@dataclass(frozen=True)
class NewsItem:
    item_id: str
    headline: str
    publisher: str
    url: str
    backend: str
    published_at: Optional[datetime]
    seen_at: Optional[datetime]
    instrument_hint: Optional[str] = None
    meta: dict = field(default_factory=dict)

    def known_at(self) -> Optional[datetime]:
        ts = [t for t in (self.published_at, self.seen_at) if t is not None]
        return max(ts) if ts else None


@dataclass(frozen=True)
class MacroObservation:
    series: str
    value: float
    unit: str
    observation_date: date
    release_time: Optional[datetime]
    retrieved_at: Optional[datetime]
    source: str
    frequency: str = "monthly"

    def known_at(self) -> Optional[datetime]:
        return self.release_time or self.retrieved_at


# ── visibility: the only place these rules live ────────────────────────────

def visible_fundamentals(snaps, symbol: str, at: datetime) -> list[FundamentalSnapshot]:
    return sorted((s for s in snaps if s.symbol == symbol and s.available_at <= at),
                  key=lambda s: s.available_at)


def visible_events(events, at: datetime) -> list[Event]:
    return sorted((e for e in events if e.visible_at(at)), key=lambda e: e.event_time)


def visible_news(items, at: datetime, lookback_days: int = NEWS_LOOKBACK_DAYS) -> list[NewsItem]:
    lo = at - timedelta(days=lookback_days)
    out = []
    for n in items:
        k = n.known_at()
        if k is not None and lo <= k <= at:
            out.append(n)
    return sorted(out, key=lambda n: (n.known_at(), n.item_id))


def visible_macro(obs, at: datetime) -> list[MacroObservation]:
    return sorted((o for o in obs if o.known_at() is not None and o.known_at() <= at),
                  key=lambda o: (o.series, o.known_at()))


def visible_model_records(rows, at: datetime) -> list[dict]:
    """Calls made by `at`; an outcome only if graded by `at`."""
    out = []
    for r in rows:
        c = aware(r.get("created_at"))
        if c is None or c > at:
            continue
        row = dict(r)
        ra = aware(row.get("resolved_at"))
        if ra is not None and ra > at:
            row.update(resolved=0, resolved_at=None, correct=None, actual_return=None,
                       price_end=None, outcome_note=None)
        out.append(row)
    return out


# ── the interface ───────────────────────────────────────────────────────────

class ResearchData:
    """Base: every method may raise; the builder records the failure."""
    name = "base"
    version = ""

    def history(self, symbol: str, at: datetime) -> Optional[pd.DataFrame]:
        raise NotImplementedError

    def fundamentals(self, symbol: str) -> list[FundamentalSnapshot]:
        return []

    def events(self, symbol: str) -> list[Event]:
        return []

    def news(self, symbol: str) -> list[NewsItem]:
        return []

    def macro(self) -> list[MacroObservation]:
        return []

    def model_records(self, symbol: str) -> list[dict]:
        return []

    def provider_status(self) -> dict:
        return {}


class FrameResearchData(ResearchData):
    """In-memory records. `frames` are OHLCV DataFrames cut point in time by
    src/risk/market_data.FrameMarketData (the production cut). Lists may be
    mutated by a test after construction; every call re-reads them."""
    name = "frames"

    def __init__(self, frames: Optional[dict] = None, *, fundamentals=None, events=None,
                 news=None, macro=None, model_records=None, failures: Optional[dict] = None,
                 version: str = "test"):
        from src.risk.market_data import FrameMarketData
        self.frames = frames or {}
        self._md = FrameMarketData(self.frames)
        self._fundamentals = fundamentals if fundamentals is not None else []
        self._events = events if events is not None else []
        self._news = news if news is not None else []
        self._macro = macro if macro is not None else []
        self._model_records = model_records if model_records is not None else []
        self.failures = failures or {}           # method -> exception message
        self.version = version

    def _maybe_fail(self, what: str):
        if what in self.failures:
            raise RuntimeError(self.failures[what])

    def history(self, symbol, at):
        self._maybe_fail("history")
        return self._md.history(symbol, at)

    def fundamentals(self, symbol):
        self._maybe_fail("fundamentals")
        return list(self._fundamentals)

    def events(self, symbol):
        self._maybe_fail("events")
        return list(self._events)

    def news(self, symbol):
        self._maybe_fail("news")
        return list(self._news)

    def macro(self):
        self._maybe_fail("macro")
        return list(self._macro)

    def model_records(self, symbol):
        self._maybe_fail("model_records")
        return [r for r in self._model_records
                if (r.get("provider_symbol") or r.get("subject")) == symbol
                or r.get("subject") == symbol]


# ── production ──────────────────────────────────────────────────────────────

def _code_version() -> str:
    try:
        from src.core.versioning import code_fingerprint
        return f"src/intelligence/research@{code_fingerprint('src/intelligence/research')}"
    except Exception:                            # pragma: no cover - defensive
        return "unknown"


def _safe(symbol: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in symbol)


class LiveResearchData(ResearchData):
    """Vendors and ARIA's own stores. Network lives here and only here, and
    this class is never constructed on the order path."""
    name = "live"

    def __init__(self, *, period: str = "5y", refresh: bool = True):
        from src.risk.market_data import MdMarketData
        self._md = MdMarketData(period=period)
        self.refresh = refresh
        self.version = _code_version()
        self._status: dict = {}
        self._memo: dict = {}

    def prefetch(self, symbol: str) -> None:
        """Fetch every provider once, BEFORE a live query fixes its instant.

        A live query's T is the moment its inputs were assembled. Without
        this, a first-ever query would fetch an earnings schedule a few
        seconds after T and — correctly, by the visibility rule — not see it.
        Results are memoised on this instance, so the snapshot that follows
        reads them without a second network round.
        """
        for fn in (self.fundamentals, self.events, self.news):
            try:
                fn(symbol)
            except Exception as e:
                self._status[fn.__name__] = f"FAILED: {e}"[:200]
        try:
            self.macro()
        except Exception as e:
            self._status["macro"] = f"FAILED: {e}"[:200]

    def _memoised(self, key, fn):
        if key not in self._memo:
            self._memo[key] = fn()
        return list(self._memo[key])

    def provider_status(self) -> dict:
        return dict(self._status)

    def history(self, symbol, at):
        return self._md.history(symbol, at)

    # fundamentals: the vendor snapshot, archived with its fetch time
    def fundamentals(self, symbol):
        return self._memoised(("fundamentals", symbol), lambda: self._fundamentals(symbol))

    def _fundamentals(self, symbol):
        path = ARCHIVE_DIR / "fundamentals" / f"{_safe(symbol)}.jsonl"
        if self.refresh:
            try:
                from src.data.universe import get_universe
                d = get_universe().dossier(symbol, prefetch_peers=False)
                if isinstance(d, dict) and d.get("fundamentals") and d.get("fetched_at"):
                    self._append_unique(path, {"symbol": symbol, "values": d["fundamentals"],
                                               "available_at": d["fetched_at"],
                                               "source": "yfinance info via universe dossier"},
                                        key="available_at")
                    self._status["fundamentals"] = "OK"
                else:
                    self._status["fundamentals"] = "NO_DOSSIER"
            except Exception as e:
                self._status["fundamentals"] = f"FAILED: {e}"[:200]
        out = []
        for row in self._read_jsonl(path):
            a = aware(row.get("available_at"))
            if a is not None:
                out.append(FundamentalSnapshot(symbol, row.get("values") or {}, a,
                                               row.get("source") or ""))
        return out

    # events: earnings dates, dividends, splits — archived with first observation
    def events(self, symbol):
        return self._memoised(("events", symbol), lambda: self._events(symbol))

    def _events(self, symbol):
        path = ARCHIVE_DIR / "events" / f"{_safe(symbol)}.json"
        archive = self._read_json(path) or {}
        if self.refresh:
            try:
                fetched = _vendor_events(symbol)
                now = datetime.now(timezone.utc).isoformat()
                seen_ids = set()
                for ev in fetched:
                    seen_ids.add(ev["event_id"])
                    cur = archive.get(ev["event_id"])
                    if cur is None:
                        archive[ev["event_id"]] = {**ev, "observed_at": now}
                    else:                        # keep the FIRST observation; refresh outcome
                        cur["outcome"] = ev.get("outcome") or cur.get("outcome") or {}
                        if cur.get("withdrawn_at") and not cur.get("relisted_at"):
                            # Listed again after a withdrawal: known again from NOW.
                            # The withdrawal stays recorded, so a query for an
                            # instant inside the gap still sees it withdrawn.
                            cur["relisted_at"] = now
                # A future schedule the vendor no longer lists was withdrawn now.
                for eid, cur in archive.items():
                    et = aware(cur.get("event_time"))
                    if (eid not in seen_ids and et and et > datetime.now(timezone.utc)
                            and not cur.get("withdrawn_at")):
                        cur["withdrawn_at"] = now
                self._write_json(path, archive)
                self._status["events"] = "OK"
            except Exception as e:
                self._status["events"] = f"FAILED: {e}"[:200]
        out = []
        for cur in archive.values():
            et = aware(cur.get("event_time"))
            if et is None:
                continue
            out.append(Event(cur["event_id"], cur["kind"], cur.get("scope") or symbol, et,
                             cur.get("source") or "", cur.get("importance") or "LOW",
                             cur.get("importance_basis") or "",
                             aware(cur.get("publication_time")), aware(cur.get("observed_at")),
                             aware(cur.get("effective_time")), aware(cur.get("withdrawn_at")),
                             aware(cur.get("relisted_at")),
                             cur.get("schedule") or {}, cur.get("outcome") or {}))
        return out

    # news: the research eye's archive + NewsAPI headlines (cached by enrichment)
    def news(self, symbol):
        return self._memoised(("news", symbol), lambda: self._news(symbol))

    def _news(self, symbol):
        items = []
        watched = False
        sym = symbol.upper()
        if OBSERVATIONS.exists():
            for line in OBSERVATIONS.read_text(encoding="utf-8").splitlines():
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                watch = str(r.get("watch_id") or "")
                title = r.get("title") or ""
                watched = watched or watch.lower() == f"ticker:{sym.lower()}"
                if not (watch.lower() == f"ticker:{sym.lower()}" or f"${sym}" in title
                        or f"({sym})" in title):
                    continue
                meta = r.get("meta") or {}
                items.append(NewsItem(
                    item_id=str(r.get("content_id") or r.get("url")), headline=title,
                    publisher=str(meta.get("source") or meta.get("company") or r.get("source") or ""),
                    url=r.get("url") or "", backend=r.get("source") or "",
                    published_at=aware(meta.get("published") or meta.get("filed")),
                    seen_at=aware(r.get("seen_at")),
                    instrument_hint=watch.split(":", 1)[1].upper() if ":" in watch else None,
                    meta={"watch_id": watch, **{k: v for k, v in meta.items()
                                                 if isinstance(v, (str, int, float))}}))
        # OK only when the research eye actually watches this instrument: an
        # archive that never looked for it says nothing about its news.
        self._status["news_eye"] = ("OK" if watched or items else
                                    "NOT_WATCHED" if OBSERVATIONS.exists() else "NO_ARCHIVE")
        if self.refresh:
            try:
                import os
                from src.data import enrichment
                e = enrichment.enrich(symbol)
                fetched = aware(e.get("fetched_at"))
                heads = e.get("headlines") or e.get("news") or []
                for h in heads:
                    items.append(NewsItem(
                        item_id=hashlib.sha1((h.get("url") or h.get("title") or "")
                                             .encode()).hexdigest()[:16],
                        headline=h.get("title") or "", publisher=h.get("source") or "",
                        url=h.get("url") or "", backend="newsapi",
                        published_at=aware(h.get("at")), seen_at=fetched,
                        instrument_hint=sym))
                self._status["news_api"] = (
                    "OK" if heads else
                    "NO_KEY" if not os.environ.get("NEWS_API_KEY") else
                    "OK_EMPTY")            # a key, a search, and nothing published
            except Exception as e:
                self._status["news_api"] = f"FAILED: {e}"[:200]
        return items

    def macro(self):
        return self._memoised(("macro",), self._macro)

    def _macro(self):
        try:
            from src.macro import feeds
            blob = json.loads(feeds._cache_path().read_text(encoding="utf-8"))
        except Exception as e:
            self._status["macro"] = f"FAILED: {e}"[:200]
            return []
        out = []
        for key, row in (blob or {}).items():
            if not isinstance(row, dict) or row.get("value") is None:
                continue
            try:
                od = date.fromisoformat(str(row.get("observation_date"))[:10])
            except ValueError:
                continue
            out.append(MacroObservation(key, float(row["value"]), row.get("unit") or "",
                                        od, aware(row.get("release_date")),
                                        aware(row.get("retrieved_at")),
                                        row.get("source") or "", row.get("frequency") or ""))
        self._status["macro"] = "OK"
        return out

    def model_records(self, symbol):
        from src.intelligence.dataset import ledger_rows
        sym = symbol.upper()
        return [r for r in ledger_rows()
                if (r.get("subject") or "").upper() == sym
                or (r.get("provider_symbol") or "").upper() == sym]

    # ── archive files ───────────────────────────────────────────────────────
    @staticmethod
    def _read_jsonl(path: Path) -> list[dict]:
        if not path.exists():
            return []
        out = []
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return out

    def _append_unique(self, path: Path, row: dict, *, key: str) -> None:
        with _lock:
            existing = {r.get(key) for r in self._read_jsonl(path)}
            if row.get(key) in existing:
                return
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, default=str) + "\n")

    @staticmethod
    def _read_json(path: Path) -> Optional[dict]:
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            return None

    @staticmethod
    def _write_json(path: Path, blob: dict) -> None:
        with _lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(blob, indent=1, default=str), encoding="utf-8")
            tmp.replace(path)


def _vendor_events(symbol: str) -> list[dict]:
    """Earnings dates, dividends and splits from the vendor, as event dicts.

    The vendor gives no publication time for any of them: an earnings DATE
    becomes knowable when ARIA first observes it (the archive's observed_at),
    a dividend or split only on its ex-date.
    """
    import yfinance as yf
    t = yf.Ticker(symbol)
    out: list[dict] = []
    try:
        ed = t.get_earnings_dates(limit=12)
    except Exception:
        ed = None
    if ed is not None and len(ed):
        for ts, row in ed.iterrows():
            when = aware(ts)
            if when is None:
                continue
            est = row.get("EPS Estimate")
            rep = row.get("Reported EPS")
            sur = row.get("Surprise(%)")
            out.append({
                "event_id": f"EARNINGS:{symbol}:{when.date().isoformat()}",
                "kind": "EARNINGS", "scope": symbol, "event_time": when.isoformat(),
                "source": "yfinance get_earnings_dates", "importance": "HIGH",
                "importance_basis": "company results reprice the instrument directly",
                "schedule": {"eps_estimate": None if pd.isna(est) else float(est)},
                "outcome": ({} if pd.isna(rep) else
                            {"reported_eps": float(rep),
                             "surprise_pct": None if pd.isna(sur) else float(sur)})})
    acts = t.actions
    if acts is not None and len(acts):
        for ts, row in acts.iterrows():
            when = aware(ts)
            if when is None:
                continue
            if float(row.get("Dividends") or 0) > 0:
                out.append({"event_id": f"DIVIDEND:{symbol}:{when.date().isoformat()}",
                            "kind": "DIVIDEND", "scope": symbol, "event_time": when.isoformat(),
                            "effective_time": when.isoformat(),
                            "source": "yfinance actions (ex-date)", "importance": "LOW",
                            "importance_basis": "mechanical: the price drops by the "
                                                "dividend on the ex-date",
                            "schedule": {}, "outcome": {"amount": float(row["Dividends"])}})
            if float(row.get("Stock Splits") or 0) > 0:
                out.append({"event_id": f"SPLIT:{symbol}:{when.date().isoformat()}",
                            "kind": "SPLIT", "scope": symbol, "event_time": when.isoformat(),
                            "effective_time": when.isoformat(),
                            "source": "yfinance actions (ex-date)", "importance": "MEDIUM",
                            "importance_basis": "a corporate-action boundary: unadjusted "
                                                "prices before and after are not comparable",
                            "schedule": {}, "outcome": {"ratio": float(row["Stock Splits"])}})
    return out
