"""
src/core/durable.py
===================
Small, boring persistence primitives for the JSON state files the order path
depends on (approval queue, reflex playbooks, desk positions, day state).

Why this exists. Every one of those stores was written with
`Path.write_text` and read back with `json.loads(...)` inside
`except Exception: return []`. Two failures follow from that pair:

  * a crash, a power cut or a full disk in the middle of `write_text` leaves a
    truncated file — write_text truncates first, then writes;
  * the next read turns the truncated file into an EMPTY store, and the next
    write saves that emptiness over it.

For the approval queue that erases every "submitting"/"unknown" row — the
records that stop a second order for an instrument whose first order may be at
the broker. For the day state it resets the day's notional and trade counters.
Both are the system reading "unknown" as "nothing", which the rest of ARIA is
built never to do.

What the primitives guarantee:

  atomic_write_text / atomic_write_json
      The file is either the old content or the new content, never a mix:
      write a sibling temp file, flush + fsync, os.replace. Windows refuses
      os.replace while another handle has the target open (a reader), so the
      replace is retried briefly before giving up.

  read_json_state
      Missing file -> the caller's default. A file that exists but cannot be
      parsed, or parses to the wrong shape -> a copy is quarantined next to it
      (`<name>.corrupt-<UTC stamp>`) and StateUnreadable is raised. The bad
      file is LEFT IN PLACE, so nothing silently writes a fresh empty store
      over it; the caller decides how to fail closed. A transient OS error
      (sharing violation) is retried and, if it persists, raised as
      StateUnreadable WITHOUT quarantine — the file is probably fine.

  ProcessSharedLock
      A re-entrant lock that also excludes other PROCESSES (msvcrt on Windows,
      fcntl on POSIX), so two backend processes — the orphan-uvicorn case —
      cannot interleave a read-modify-write. The OS drops the lock if the
      holder dies. Acquisition times out (StateLockTimeout) rather than hang.

  SingleInstance
      A non-blocking, process-lifetime lock: "only one process may run this".

Nothing here imports anything from ARIA; it is safe to use from any layer.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

#: How long a write keeps retrying a Windows sharing violation on replace, and
#: how long a read keeps retrying one on open.
REPLACE_RETRY_S = 2.0
READ_RETRY_S = 1.0


class StateUnreadable(RuntimeError):
    """A state file exists but could not be read as valid state. Fail closed."""

    def __init__(self, path: Path, why: str, quarantined: Optional[Path] = None):
        self.path = Path(path)
        self.why = why
        self.quarantined = quarantined
        where = f"; a copy was kept at {quarantined}" if quarantined else ""
        super().__init__(f"{self.path.name} is unreadable ({why}){where}. It was left "
                         f"in place and nothing will overwrite it until it is repaired.")


class StateLockTimeout(RuntimeError):
    """Another thread or process held the state lock for too long."""


# ── writing ─────────────────────────────────────────────────────────────────

def atomic_write_text(path: Path, text: str, encoding: str = "utf-8") -> None:
    """Replace `path` with `text` atomically: readers see the old file or the
    new one, never a truncated mix. Raises on failure (the old file stays)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        with open(tmp, "w", encoding=encoding, newline="") as f:
            f.write(text)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:                                   # pragma: no cover
                pass
        deadline = time.monotonic() + REPLACE_RETRY_S
        while True:
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                # Windows: the target is open in another handle (a reader).
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.02)
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:                                       # pragma: no cover
            pass


def atomic_write_json(path: Path, obj: Any, **dumps_kwargs) -> None:
    dumps_kwargs.setdefault("indent", 2)
    dumps_kwargs.setdefault("default", str)
    atomic_write_text(path, json.dumps(obj, **dumps_kwargs))


# ── reading ─────────────────────────────────────────────────────────────────

def quarantine(path: Path) -> Optional[Path]:
    """Copy a damaged state file aside for the owner; returns the copy's path
    (None if even copying failed). The original stays where it is."""
    path = Path(path)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    dest = path.with_name(f"{path.name}.corrupt-{stamp}")
    try:
        shutil.copy2(path, dest)
        return dest
    except OSError as e:
        logger.error("could not quarantine %s: %s", path, e)
        return None


def read_json_state(path: Path, default: Callable[[], Any], *,
                    expect: type | tuple = (list, dict),
                    validate: Optional[Callable[[Any], Optional[str]]] = None) -> Any:
    """The parsed content of a JSON state file.

    `default()` when the file does not exist. When it exists but is empty,
    unparseable, the wrong type (`expect`), or `validate(obj)` returns a
    reason: quarantine a copy and raise StateUnreadable. Never returns an
    empty store in place of a damaged one."""
    path = Path(path)
    deadline = time.monotonic() + READ_RETRY_S
    while True:
        try:
            raw = path.read_text(encoding="utf-8")
            break
        except FileNotFoundError:
            return default()
        except PermissionError as e:
            # A sharing violation while a writer replaces the file. Not damage.
            if time.monotonic() >= deadline:
                raise StateUnreadable(path, f"could not open: {e}") from e
            time.sleep(0.02)
        except (OSError, UnicodeDecodeError) as e:
            q = quarantine(path)
            raise StateUnreadable(path, f"{type(e).__name__}: {e}", q) from e
    why = None
    obj = None
    if not raw.strip():
        why = "empty file"
    else:
        try:
            obj = json.loads(raw)
        except ValueError as e:
            why = f"not valid JSON: {e}"
    if why is None and not isinstance(obj, expect):
        why = f"expected {getattr(expect, '__name__', expect)}, found {type(obj).__name__}"
    if why is None and validate is not None:
        why = validate(obj)
    if why is not None:
        q = quarantine(path)
        logger.critical("state file %s unreadable (%s) — copy at %s; failing closed",
                        path, why, q)
        raise StateUnreadable(path, why, q)
    return obj


def rows_with(*keys: str) -> Callable[[Any], Optional[str]]:
    """A validator for a list of dicts that each carry `keys`."""
    def check(obj):
        for i, row in enumerate(obj):
            if not isinstance(row, dict):
                return f"row {i} is {type(row).__name__}, not an object"
            missing = [k for k in keys if k not in row]
            if missing:
                return f"row {i} lacks {', '.join(missing)}"
        return None
    return check


# ── cross-process locking ───────────────────────────────────────────────────

#: Windows locks a byte RANGE, and a locked byte cannot be read by another
#: process. Lock one byte far past anything written, so a lock file's text
#: (the holder's pid) stays readable while the lock is held.
_WIN_LOCK_OFFSET = 1 << 30


def _try_lock(fh) -> bool:
    try:
        if os.name == "nt":
            import msvcrt
            fh.seek(_WIN_LOCK_OFFSET)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _unlock(fh) -> None:
    try:
        if os.name == "nt":
            import msvcrt
            fh.seek(_WIN_LOCK_OFFSET)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    except OSError:                                           # pragma: no cover
        pass


def _open_lock_file(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    return open(path, "a+b")


class ProcessSharedLock:
    """Re-entrant within a thread; exclusive across threads AND processes.

    `lock_path` is a callable so a module whose data paths are redirected at
    runtime (tests/conftest.py) locks the redirected location."""

    def __init__(self, lock_path: Callable[[], Path], timeout_s: float = 15.0):
        self._lock_path = lock_path
        self.timeout_s = timeout_s
        self._rlock = threading.RLock()
        self._depth = 0
        self._fh = None

    def acquire(self) -> None:
        if not self._rlock.acquire(timeout=self.timeout_s):
            raise StateLockTimeout(f"{self._lock_path()} held by another thread "
                                   f"for over {self.timeout_s:g}s")
        if self._depth == 0:
            try:
                fh = _open_lock_file(Path(self._lock_path()))
                deadline = time.monotonic() + self.timeout_s
                while not _try_lock(fh):
                    if time.monotonic() >= deadline:
                        fh.close()
                        raise StateLockTimeout(f"{self._lock_path()} held by another "
                                               f"process for over {self.timeout_s:g}s")
                    time.sleep(0.02)
                self._fh = fh
            except BaseException:
                self._rlock.release()
                raise
        self._depth += 1

    def release(self) -> None:
        self._depth -= 1
        if self._depth == 0 and self._fh is not None:
            _unlock(self._fh)
            self._fh.close()
            self._fh = None
        self._rlock.release()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc):
        self.release()
        return False


class SingleInstance:
    """A lock held for the life of the process: only one process may hold it.

    `acquire()` never blocks; it returns True when this process now holds (or
    already held) the lock and False when another process does."""

    def __init__(self, lock_path: Callable[[], Path]):
        self._lock_path = lock_path
        self._fh = None
        self._guard = threading.Lock()

    @property
    def held(self) -> bool:
        return self._fh is not None

    def acquire(self) -> bool:
        with self._guard:
            if self._fh is not None:
                return True
            path = Path(self._lock_path())
            fh = _open_lock_file(path)
            if not _try_lock(fh):
                fh.close()
                return False
            try:
                fh.seek(0)
                fh.truncate()
                fh.write(f"{os.getpid()} {datetime.now(timezone.utc).isoformat()}\n".encode())
                fh.flush()
            except OSError:                                   # pragma: no cover
                pass
            self._fh = fh
            return True

    def holder(self) -> str:
        """Who holds it, as written by the holder (best effort, for messages)."""
        try:
            return Path(self._lock_path()).read_text(encoding="utf-8").strip()
        except OSError:
            return "unknown"

    def release(self) -> None:
        with self._guard:
            if self._fh is not None:
                _unlock(self._fh)
                self._fh.close()
                self._fh = None
