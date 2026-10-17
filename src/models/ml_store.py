"""
src/models/ml_store.py
======================
data/ml_predictions.json, written a batch at a time, and aged row by row.

Why this exists. The pipeline's ML step trains one ensemble per ticker — about
2.2 tickers a minute, ~4 h for the universe — and used to write the file once,
after the LAST ticker. Any interruption lost the whole step: a closed console
(0xC000013A), a battery stop, the old 2 h task limit. One run in ten completed
between 2026-07-22 and 2026-10-17, and the file stayed dated 2026-05-31
(docs/ARIA_COMPLETION_AUDIT.md, O-2).

The file keeps the `{ticker: row}` shape every reader depends on. What changes
is how it is written:

  merge_rows    after each batch, the batch's rows replace their tickers' rows
                atomically (src/core/durable.atomic_write_json): a reader — or a
                run killed mid-write — sees the old file or the new one, never
                a torn one. Each row carries its own `generated_at`.
  fresh_tickers which tickers already hold a row younger than the resume
                budget, so a rerun trains only the rest.
  keep_only     at the end of a pass that FINISHED, drop the rows that pass did
                not produce or keep (tickers that left the universe or failed
                this time). The file then means what it meant when it was
                written in one piece: the last completed pass.

And how it must be read. While a pass is under way the file holds this pass's
rows next to the previous pass's, so its modification time says only when the
last batch landed. Aging a May row by that time would present it as today's.
`made_at` and `dataset_age` age rows by their own stamp; a file in which no row
is stamped was written in one piece by the old code, and its mtime still stands
in for every row, as it always did.

Standard library only (plus src/core/durable, itself standard-library only), so
the backend, the world model and the desk can import it without loading the
training stack.
"""
from __future__ import annotations

import logging
from functools import lru_cache
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable, Optional

from src.core.durable import ProcessSharedLock, StateUnreadable, atomic_write_json, read_json_state

logger = logging.getLogger(__name__)

#: A rerun skips tickers whose row is younger than this, unless forced.
DEFAULT_RESUME_MAX_AGE_HOURS = 20.0
#: Rows merged into the file after every this-many finished tickers.
DEFAULT_BATCH_SIZE = 25


# ── reading rows ────────────────────────────────────────────────────────────

def stamped(row) -> Optional[datetime]:
    """A row's own `generated_at` as naive local time; None when the row has
    none, or one that cannot be read. Never a guess."""
    if not isinstance(row, dict) or not row.get("generated_at"):
        return None
    try:
        t = datetime.fromisoformat(str(row["generated_at"]).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t.astimezone().replace(tzinfo=None) if t.tzinfo else t


def made_at(body: dict, ticker: str, file_mtime: Optional[datetime]) -> Optional[datetime]:
    """The latest instant `ticker`'s prediction could have been made.

    Its row's own `generated_at` when it has one. A file in which NO row is
    stamped was written in one piece, so its mtime bounds every row and stands
    in for them. In a file where some rows are stamped, an unstamped row
    predates the stamps by an unknown margin: None, because unknown is never
    current."""
    if not isinstance(body, dict):
        return None
    t = stamped(body.get(ticker))
    if t is not None:
        return t
    if any(stamped(r) is not None for r in body.values()):
        return None
    return file_mtime


def dataset_age(body, file_mtime: Optional[datetime], now: datetime,
                budget_hours: float) -> dict:
    """How old the predictions are, judged by their rows.

    The dataset is as old as its OLDEST row: while a pass is half done most
    rows still belong to the previous one. Returns `basis`
    ("row_generated_at", "file_mtime", or None when there is nothing to age),
    `as_of` (oldest row), `newest_at`, `age_hours`, `stale`, `rows`,
    `rows_over_budget`, `rows_unstamped` and a plain `why` when stale."""
    rows = [r for r in body.values() if isinstance(r, dict)] if isinstance(body, dict) else []
    dated = [t for t in map(stamped, rows) if t is not None]
    unstamped = len(rows) - len(dated)
    out = {"rows": len(rows), "rows_unstamped": unstamped, "rows_over_budget": 0,
           "newest_at": None}
    if not dated:
        if file_mtime is None:
            return {**out, "basis": None, "as_of": None, "age_hours": None,
                    "stale": True, "why": "ml_predictions.json is missing"}
        age = (now - file_mtime).total_seconds() / 3600
        stale = age > budget_hours
        return {**out, "basis": "file_mtime", "as_of": file_mtime, "newest_at": file_mtime,
                "age_hours": round(age, 1), "stale": stale,
                "rows_over_budget": len(rows) if stale else 0,
                "why": (f"ml_predictions.json is {round(age / 24, 1)} days old (fresh under "
                        f"{budget_hours:g}h)") if stale else None}
    oldest, newest = min(dated), max(dated)
    age = (now - oldest).total_seconds() / 3600
    over = sum(1 for t in dated if (now - t).total_seconds() / 3600 > budget_hours)
    stale = age > budget_hours or unstamped > 0
    why = None
    if stale:
        parts = []
        if over:
            parts.append(f"{over} of {len(rows)} rows older than {budget_hours:g}h "
                         f"(oldest {round(age / 24, 1)} days)")
        if unstamped:
            parts.append(f"{unstamped} of {len(rows)} rows carry no generated_at "
                         f"(written before per-row stamps; age unknown)")
        newest_h = round((now - newest).total_seconds() / 3600, 1)
        why = (f"ml_predictions.json: {'; '.join(parts)}; newest row {newest_h}h old — "
               f"no pass has finished since")
    return {**out, "basis": "row_generated_at", "as_of": oldest, "newest_at": newest,
            "age_hours": round(age, 1), "stale": stale, "rows_over_budget": over,
            "why": why}


def read_rows(path: Path) -> dict:
    """The file's rows; {} when it does not exist.

    A damaged file is copied aside by durable.read_json_state and treated as
    empty: predictions are regenerated by the next batch, and the copy keeps
    what was there. A file that could not be OPENED (a Windows sharing
    violation) re-raises: that says nothing about its content, and treating it
    as empty would drop every row on the next write."""
    try:
        return read_json_state(Path(path), dict, expect=dict)
    except StateUnreadable as e:
        if e.quarantined is None:
            raise
        logger.error("ml_store: %s — starting from an empty file", e)
        return {}


def fresh_tickers(path: Path, max_age_hours: float,
                  now: Optional[datetime] = None) -> set:
    """Tickers whose row was generated within `max_age_hours` of `now`.

    A row that cannot be dated, or is dated in the future, is not fresh: the
    rerun trains it again rather than skip it on a guess."""
    if max_age_hours <= 0:
        return set()
    try:
        rows = read_rows(path)
    except StateUnreadable as e:
        logger.warning("ml_store: resume disabled for this run — %s", e)
        return set()
    now = now or datetime.now()
    budget = timedelta(hours=max_age_hours)
    out = set()
    for ticker, row in rows.items():
        t = stamped(row)
        if t is not None and timedelta(0) <= now - t <= budget:
            out.add(ticker)
    return out


# ── writing rows ────────────────────────────────────────────────────────────

def _write(path: Path, rows: dict) -> None:
    atomic_write_json(Path(path), rows, indent=1)


@lru_cache(maxsize=128)
def _writer_lock(path: str) -> ProcessSharedLock:
    return ProcessSharedLock(lambda: Path(path + ".lock"))


def merge_rows(path: Path, rows: dict) -> int:
    """Replace `rows`' tickers in the file, atomically. Returns the file's
    row count. Raises when the file cannot be read or written; the file is
    then exactly as it was."""
    with _writer_lock(str(Path(path).resolve())):
        current = read_rows(path)
        current.update(rows)
        _write(path, current)
        return len(current)


def keep_only(path: Path, tickers: Iterable[str]) -> int:
    """Drop every row whose ticker is not in `tickers`. Returns how many were
    dropped. Only for the end of a pass that finished."""
    keep = set(tickers)
    with _writer_lock(str(Path(path).resolve())):
        current = read_rows(path)
        kept = {t: r for t, r in current.items() if t in keep}
        if len(kept) != len(current):
            _write(path, kept)
        return len(current) - len(kept)
