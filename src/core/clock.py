"""
src/core/clock.py
=================
How long ago one of ARIA's stamps was, in elapsed time.

ARIA's stores stamp naive local wall time: `datetime.now().isoformat()`, and
`datetime.fromtimestamp(mtime)` for files. Subtracting two such readings
measures how far the wall clock moved, not how much time passed, and across a
daylight-saving change the two differ by an hour. On 2026-10-27 a
signals.json written 56 hours earlier read 55.0 hours old, because the
interval crossed the 2026-10-25 UK clock change. The auto-execution gate and
the UI decide FRESH or STALE from these ages, so an hour near a threshold
flips the verdict.

Here ages are taken between instants. `now()` is timezone-aware, and a naive
stamp is read as this machine's local time, because that is what wrote it.
File times are not ambiguous: `from_mtime` keeps the instant.

A naive stamp from the hour that repeats when clocks go back names two
instants. A data stamp is read as the first of them, so its age errs older,
toward STALE. A heartbeat is the latest tick of a loop that runs every few
minutes, so it is read as the later instant unless that is still in the future
(`recent_instant`). Read as the earlier instant, every worker that ticks
normally through that hour would look an hour behind, and so stalled.
"""
from __future__ import annotations

from datetime import datetime, timezone


def now() -> datetime:
    """The current instant, timezone-aware. Tests pin this."""
    return datetime.now(timezone.utc)


def instant(when: datetime) -> datetime:
    """`when` as an aware instant. A naive value is this machine's local time."""
    if when.tzinfo is not None:
        return when
    try:
        return when.astimezone()
    except (OSError, OverflowError, ValueError):
        # Windows cannot convert local times before 1970 (or far in the
        # future). At that distance an hour is noise, and raising would take
        # down every reader that ages the stamp.
        return when.replace(tzinfo=timezone.utc)


def recent_instant(when: datetime, at: datetime) -> datetime:
    """`when` as an instant, for a heartbeat stamp: the latest tick of a
    frequent loop. In the repeated hour a naive local time is read as the
    later of its two instants, unless that is after `at`."""
    first = instant(when)
    if when.tzinfo is not None:
        return first
    try:
        second = when.replace(fold=1).astimezone()
    except (OSError, OverflowError, ValueError):
        return first
    return second if second <= instant(at) else first


def from_mtime(mtime: float) -> datetime:
    """A file's modification time (epoch seconds) as an aware instant."""
    return datetime.fromtimestamp(mtime, timezone.utc)


def local_wall(when: datetime) -> datetime:
    """The naive local wall-clock reading of an instant: the form `as_of`
    fields have always been written in, so they stay unchanged."""
    return instant(when).astimezone().replace(tzinfo=None)


def seconds_since(when: datetime, *, recent: bool = False) -> float:
    """Elapsed seconds from `when` to now. `recent=True` for a heartbeat."""
    at = now()
    then = recent_instant(when, at) if recent else instant(when)
    return (at - then).total_seconds()


def hours_since(when: datetime, *, recent: bool = False) -> float:
    """Elapsed hours from `when` to now. `recent=True` for a heartbeat."""
    return seconds_since(when, recent=recent) / 3600
