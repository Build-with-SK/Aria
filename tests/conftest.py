"""
tests/conftest.py
=================
One global isolation mechanism: the test suite may not read or write ARIA's
production state, and may not spend ARIA's external quotas.

WHY THIS IS A CORRECTNESS FIXTURE, NOT HYGIENE
----------------------------------------------
Three leaks have already happened, each the same shape and each found only by
looking:

  * the event bus — tests exercising the research eye wrote their fixtures into
    data/aria_core.db, so the owner's activity stream showed rows reading
    "Nvidia cuts guidance" and "story 2" as things that had really happened;
  * the macro feed cache — a test asserting that a dead BLS returns None began
    passing back a real cached 3.365, because fetch_all() had started reading
    data/cache/;
  * the daemons — an mtime diff across a full run showed the suite writing
    THIRTEEN production files (brain_state.json, desk/heartbeat.json,
    desk/positions.json, quant_lab/*, fx_state.json, v5/loop_heartbeat.json,
    model_registry.db, macro_data.json …) plus seven .wav files, because
    several tests legitimately call daemon.start() and the daemons then
    persisted into the real data directory.

The first two were patched one variable at a time, and the third proves why
that does not scale: there are 95 module-level constants under src/ pointing
into data/. So the mechanism below is general — it redirects the data ROOT for
every loaded ARIA module — rather than a list somebody has to remember to
extend.

WHAT IT DOES NOT DO
-------------------
It does not fake anything. Tests that want fixture data still create it. The
redirect points at an empty temporary tree, so a test that silently depended on
production data now FAILS rather than passing for the wrong reason — which is
the point, and is how the macro-cache leak was caught.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
REAL_DATA = (REPO_ROOT / "data").resolve()

#: Hosts a test must never reach. These are metered: the BLS keyless API allows
#: roughly 25 requests per IP per day, and a single unguarded suite run
#: exhausted it, which is what made CPI and unemployment go None in production.
METERED_HOSTS = ("api.bls.gov", "home.treasury.gov", "markets.newyorkfed.org",
                 "stlouisfed.org")

_TMP_DATA: Path | None = None
_patched_modules: set[str] = set()
#: Every module name `_sweep` needs never look at again: ARIA modules fully
#: redirected, and every non-ARIA module.
_examined_modules: set[str] = set()

# ── the audit log ────────────────────────────────────────────────────────────
# Set at IMPORT, not in a fixture: pytest imports this file before it collects
# test modules, and test modules import backend.main at collection time, which
# attaches the rotating file handler there and then. A session fixture runs too
# late — that is how data/aria.log came to hold "KILL SWITCH ENGAGED by test"
# and promotions of strategies that only exist in fixtures.
_TEST_LOG_DIR = Path(tempfile.mkdtemp(prefix="aria-test-logs-"))
os.environ["ARIA_LOG_FILE"] = str(_TEST_LOG_DIR / "aria_test.log")
TEST_LOG_FILE = Path(os.environ["ARIA_LOG_FILE"])


def _redirect_module(mod, tmp_data: Path) -> int:
    """Repoint every Path constant in `mod` that lives under the real data dir.

    Handles module-level names and class attributes. Returns how many were
    moved, which the summary at the end of the run reports.
    """
    moved = 0
    try:
        members = list(vars(mod).items())
    except Exception:
        return 0

    def remap(value):
        if not isinstance(value, Path):
            return None
        try:
            rel = value.resolve().relative_to(REAL_DATA)
        except (ValueError, OSError):
            return None
        return tmp_data / rel

    for name, value in members:
        new = remap(value)
        if new is not None:
            try:
                setattr(mod, name, new)
                moved += 1
            except Exception:
                pass
        elif isinstance(value, type):                 # class attributes
            for attr, aval in list(vars(value).items()):
                new_a = remap(aval)
                if new_a is not None:
                    try:
                        setattr(value, attr, new_a)
                        moved += 1
                    except Exception:
                        pass
        else:
            # INSTANCE attributes, for a module-level object that holds its own
            # paths. `src.auth.session.SESSIONS` is one: an OwnerAuth built at
            # import time with the session store and the signing key baked in.
            # Redirecting only the module constants left it pointing at the
            # real data directory, so running the suite signed the owner out of
            # his own browser — a leak with a symptom nobody would connect to
            # pytest.
            #
            # General rather than a named exception, for the reason the note at
            # the top of this file gives: a list somebody has to remember to
            # extend is a list that will be short by one. Only paths that
            # actually resolve under the real data directory are touched.
            try:
                attrs = list(vars(value).items())
            except TypeError:
                attrs = []                            # no __dict__, nothing to do
            for attr, aval in attrs:
                new_a = remap(aval)
                if new_a is not None:
                    try:
                        setattr(value, attr, new_a)
                        moved += 1
                    except Exception:
                        pass
    return moved


def _sweep(tmp_data: Path) -> int:
    """Redirect every ARIA module imported so far. Cheap and idempotent.

    Modules are imported lazily all over this codebase (deliberately — heavy
    deps stay off the startup path), so one sweep at session start is not
    enough. The autouse fixture below re-sweeps only modules it has not seen.
    """
    moved = 0
    # Only names not examined before. The set difference runs in C, so a sweep
    # costs time in proportion to what is NEW, not to everything ever imported.
    # The import hook below runs this on every executed `from src... import`,
    # including those inside hot functions (src/markets/sessions.resolve);
    # once scipy and sklearn are loaded sys.modules holds ~3,400 entries, and
    # looping over all of them in Python made a ~50 us session() cost ~1.4 ms
    # under pytest — failing test_global_calendars' latency check on harness
    # overhead, not on the code it measures. Semantics are unchanged: a
    # non-ARIA name can never become an ARIA name, a patched name was already
    # skipped, and a module still initializing is not marked, so it is looked
    # at again exactly as before.
    for name in list(sys.modules.keys() - _examined_modules):
        mod = sys.modules.get(name)
        if mod is None:
            continue                     # a failed-import placeholder: look again later
        if name in _patched_modules or not (
                name == "src" or name.startswith("src.") or name.startswith("backend")):
            _examined_modules.add(name)
            continue
        moved += _redirect_module(mod, tmp_data)
        # A module still executing its own body is swept for what exists so
        # far but NOT marked done. backend/main.py imports src.* near its top,
        # so the import hook below used to sweep it half-built, mark it, and
        # never look again — every data path defined further down the file
        # (CHAT_STREAM_METRICS was the one that showed it) kept its REAL value
        # and tests wrote into the owner's data directory.
        if not getattr(getattr(mod, "__spec__", None), "_initializing", False):
            _patched_modules.add(name)
            _examined_modules.add(name)
    return moved


@pytest.fixture(scope="session", autouse=True)
def isolate_production_state():
    """Point every writable store at a throwaway tree for the whole session."""
    global _TMP_DATA
    tmp_root = Path(tempfile.mkdtemp(prefix="aria-test-data-"))
    tmp_data = tmp_root / "data"
    tmp_data.mkdir(parents=True, exist_ok=True)
    _TMP_DATA = tmp_data

    # Env-var overrides, for the stores that already read their path at call
    # time. Set before any sweep so a module reading them at import is correct.
    os.environ["ARIA_TESTING"] = "1"
    os.environ["ARIA_CORE_DB"] = str(tmp_data / "aria_core_test.db")
    os.environ["ARIA_MACRO_CACHE"] = str(tmp_data / "macro_feeds_test.json")
    os.environ["ARIA_DATA_DIR"] = str(tmp_data)
    # Never start Ollama-backed reasoning or pull models during a test run.
    os.environ.setdefault("ARIA_RUN_BRAIN", "false")

    # The symbol master is a READ-ONLY reference dataset, not runtime state, and
    # identity resolution is meaningless without it — a redirected empty tree
    # would make every symbol IDENTITY_UNRESOLVED and the grading tests would
    # pass for the wrong reason. Copy it so tests get the real mappings while
    # remaining unable to corrupt the original.
    real_universe = REAL_DATA / "universe.db"
    if real_universe.exists():
        try:
            shutil.copy2(real_universe, tmp_data / "universe.db")
        except OSError:                      # pragma: no cover - best effort
            pass

    _sweep(tmp_data)

    # ── close the lazy-import race ─────────────────────────────────────────
    # This codebase imports heavy modules inside functions on purpose, so a
    # module can first appear halfway through a test — after that test's sweep
    # has already run. It then keeps the REAL data paths until the next test
    # starts, and anything it writes in between lands in production. That is
    # exactly how src/core/identity.py was still opening the real universe.db,
    # and how the desk heartbeat kept leaking after the backend guard.
    #
    # Wrapping __import__ redirects a module the moment it is created, so there
    # is no window. It is a test-only hook and is removed at session end.
    import builtins
    real_import = builtins.__import__

    def sweeping_import(name, globals=None, locals=None, fromlist=(), level=0):
        module = real_import(name, globals, locals, fromlist, level)
        if name == "src" or name.startswith(("src.", "backend")):
            _sweep(tmp_data)
        return module

    builtins.__import__ = sweeping_import
    try:
        yield
    finally:
        builtins.__import__ = real_import
        for key in ("ARIA_TESTING", "ARIA_CORE_DB", "ARIA_MACRO_CACHE",
                    "ARIA_DATA_DIR"):
            os.environ.pop(key, None)
        shutil.rmtree(tmp_root, ignore_errors=True)
        # Windows will not delete a file a handler still holds open.
        import logging
        for h in list(logging.getLogger().handlers):
            if str(_TEST_LOG_DIR) in str(getattr(h, "baseFilename", "")):
                logging.getLogger().removeHandler(h)
                h.close()
        shutil.rmtree(_TEST_LOG_DIR, ignore_errors=True)


@pytest.fixture(autouse=True)
def _redirect_late_imports(isolate_production_state):
    """Catch modules imported partway through the run.

    Only unseen modules are touched, so the common case is a set-difference
    over sys.modules and nothing else.
    """
    if _TMP_DATA is not None:
        _sweep(_TMP_DATA)
    yield


@pytest.fixture(autouse=True)
def _block_metered_hosts(monkeypatch):
    """Refuse network calls to metered data sources.

    A test that wants to exercise a feed must inject its own fake transport —
    which every macro test already does. This is the backstop that makes
    "no test consumes the BLS quota" a property of the suite rather than a
    promise, and it fails LOUDLY so an accidental live call is a visible error
    rather than a silent 25-request bill.
    """
    import urllib.request

    real_urlopen = urllib.request.urlopen

    def guarded(req, *a, **k):
        url = req if isinstance(req, str) else getattr(req, "full_url", "")
        if any(h in str(url) for h in METERED_HOSTS):
            raise AssertionError(
                f"test attempted a live request to a metered source: {url}. "
                f"Inject a fake transport instead — the BLS keyless API allows "
                f"~25 requests/IP/day and the suite would exhaust it.")
        return real_urlopen(req, *a, **k)

    monkeypatch.setattr(urllib.request, "urlopen", guarded)

    try:
        import requests
        real_get = requests.get

        def guarded_get(url, *a, **k):
            if any(h in str(url) for h in METERED_HOSTS):
                raise AssertionError(
                    f"test attempted a live request to a metered source: {url}")
            return real_get(url, *a, **k)

        monkeypatch.setattr(requests, "get", guarded_get)
    except ImportError:
        pass
    yield


@pytest.fixture(scope="session", autouse=True)
def forbid_production_writes(isolate_production_state):
    """Make a write into the real data directory a LOUD failure.

    The redirect above is the mechanism; this is the proof. Three leaks were
    found by diffing file mtimes across a run, which only works if somebody
    thinks to look. With this guard a leak fails the test that caused it, names
    the path, and points at the fixture — so the next one is found in seconds
    rather than months.

    Writes only. Reads of the real tree stay legal: several tests legitimately
    read committed fixtures and the repository's own source.
    """
    import builtins
    import sqlite3

    real_open, real_wt, real_wb = builtins.open, Path.write_text, Path.write_bytes
    real_connect = sqlite3.connect

    def _offending(target) -> Path | None:
        try:
            p = Path(target).resolve()
        except (TypeError, ValueError, OSError):
            return None
        try:
            p.relative_to(REAL_DATA)
        except ValueError:
            return None
        return p

    def _boom(p: Path, how: str):
        raise AssertionError(
            f"test wrote PRODUCTION state via {how}: {p}\n"
            f"Everything under {REAL_DATA} is the owner's live record. Use the "
            f"`tmp_data_dir` fixture, or add the module's path constant to the "
            f"redirect sweep in tests/conftest.py.")

    def guarded_open(file, mode="r", *a, **k):
        if any(m in str(mode) for m in ("w", "a", "x", "+")):
            p = _offending(file)
            if p:
                _boom(p, "open()")
        return real_open(file, mode, *a, **k)

    def guarded_wt(self, *a, **k):
        p = _offending(self)
        if p:
            _boom(p, "Path.write_text")
        return real_wt(self, *a, **k)

    def guarded_wb(self, *a, **k):
        p = _offending(self)
        if p:
            _boom(p, "Path.write_bytes")
        return real_wb(self, *a, **k)

    def guarded_connect(database, *a, **k):
        # sqlite opens read-write by default, so any connection to a production
        # database is a potential write.
        if not str(database).startswith("file:") or "mode=ro" not in str(database):
            p = _offending(database)
            if p:
                _boom(p, "sqlite3.connect")
        return real_connect(database, *a, **k)

    builtins.open = guarded_open
    Path.write_text, Path.write_bytes = guarded_wt, guarded_wb
    sqlite3.connect = guarded_connect
    try:
        yield
    finally:
        builtins.open = real_open
        Path.write_text, Path.write_bytes = real_wt, real_wb
        sqlite3.connect = real_connect


@pytest.fixture
def tmp_data_dir() -> Path:
    """The session's throwaway data root, for tests that want to seed it."""
    assert _TMP_DATA is not None, "isolation fixture did not run"
    return _TMP_DATA


@pytest.fixture
def clean_trading_state(isolate_production_state, monkeypatch):
    """An empty src/trading world: no mode, no limits, no journal, no halt.

    Every store under data/trading/ is shared process state, so a test that
    left a strategy registered or the order engine halted would decide the
    next test's result. The live switch and the kill switch are cleared from
    the environment for the same reason — and because a test that passes only
    because a variable happened to be set proves nothing.
    """
    monkeypatch.delenv("ARIA_KILL_SWITCH", raising=False)
    monkeypatch.delenv("ARIA_LIVE_TRADING_ENABLED", raising=False)
    from src.execution import kill_switch
    from src.trading import modes

    trading = modes.STATE_FILE.parent
    shutil.rmtree(trading, ignore_errors=True)
    trading.mkdir(parents=True, exist_ok=True)
    kill_switch.STATE_FILE.unlink(missing_ok=True)
    try:
        yield trading
    finally:
        shutil.rmtree(trading, ignore_errors=True)
        kill_switch.STATE_FILE.unlink(missing_ok=True)


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    """State the isolation result, so a regression is visible in the output."""
    terminalreporter.write_sep(
        "-", f"isolation: {len(_patched_modules)} ARIA modules redirected away "
             f"from {REAL_DATA}")
