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
A naive stamp from the repeated hour when clocks go back is ambiguous, and is
read as the first of the two. File times are not ambiguous: `from_mtime`
keeps the instant.
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


def from_mtime(mtime: float) -> datetime:
    """A file's modification time (epoch seconds) as an aware instant."""
    return datetime.fromtimestamp(mtime, timezone.utc)


def local_wall(when: datetime) -> datetime:
    """The naive local wall-clock reading of an instant: the form `as_of`
    fields have always been written in, so they stay unchanged."""
    return instant(when).astimezone().replace(tzinfo=None)


def seconds_since(when: datetime) -> float:
    """Elapsed seconds from `when` to now."""
    return (now() - instant(when)).total_seconds()


def hours_since(when: datetime) -> float:
    """Elapsed hours from `when` to now."""
    return seconds_since(when) / 3600
