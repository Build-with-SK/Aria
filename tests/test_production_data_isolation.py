"""
tests/test_production_data_isolation.py
=======================================
A test run cannot write the owner's production data — enforced, not cleaned up.

On 2026-10-08 a bare `pytest` from the repository root collected a root-level
script (test_political_imports.py) that runs the political pipeline at import
time, outside tests/conftest.py's isolation, and rewrote data/political/* and
data/trade_candidates.json. Three things now stop that class of leak:

  1. pytest.ini restricts collection to tests/;
  2. the conftest guard refuses every write path into the real data directory
     — open for writing, Path.write_*, os.replace/rename/remove/unlink,
     Path.unlink/rename/replace/touch, shutil.move/rmtree, a writable sqlite
     connection (including a `file:` URI);
  3. every refused attempt is recorded, and the run FAILS if any happened
     outside a deliberate probe — even one swallowed by a broad `except`.

Every probe here is non-destructive even if the guard were missing: writes
target files that do not exist, appends write nothing, deletions target
files that do not exist, and protected files are fingerprinted before and
after.
"""
import hashlib
import os
import shutil
import sqlite3
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
REAL_DATA = (ROOT / "data").resolve()

PROTECTED = ["signals.json", "macro_data.json", "political/political_signals.json",
             "political/watchlist.json", "political/raw/auto_fetch_state.json",
             "trade_candidates.json", "aria_core.db", "trading/mode.json",
             "kill_switch.json", "desk_config.json"]


def _fingerprint(p: Path):
    if not p.exists():
        return None
    st = p.stat()
    return (st.st_size, st.st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest()
            if st.st_size < 50_000_000 else None)


def test_collection_is_restricted_to_tests(request):
    assert request.config.getini("testpaths") == ["tests"]
    ini = (ROOT / "pytest.ini").read_text(encoding="utf-8")
    assert "testpaths = tests" in ini


@pytest.mark.parametrize("rel", PROTECTED)
def test_appending_to_a_protected_file_is_refused(rel, expect_production_write_blocked):
    p = REAL_DATA / rel
    before = _fingerprint(p)
    with pytest.raises(AssertionError, match="PRODUCTION"):
        with open(p, "a"):                 # append mode: writes nothing even if allowed
            pass
    assert _fingerprint(p) == before


def _probe() -> Path:
    return REAL_DATA / f"__isolation_probe_{uuid.uuid4().hex}.json"


@pytest.mark.parametrize("how", ["open_w", "write_text", "write_bytes", "touch",
                                 "os_replace", "os_rename", "path_rename", "path_replace",
                                 "shutil_move"])
def test_every_write_path_into_production_is_refused(how, tmp_path,
                                                     expect_production_write_blocked):
    target = _probe()
    src = tmp_path / "src.json"
    src.write_text("{}", encoding="utf-8")
    try:
        with pytest.raises(AssertionError, match="PRODUCTION"):
            if how == "open_w":
                open(target, "w").close()
            elif how == "write_text":
                target.write_text("x", encoding="utf-8")
            elif how == "write_bytes":
                target.write_bytes(b"x")
            elif how == "touch":
                target.touch()
            elif how == "os_replace":
                os.replace(src, target)
            elif how == "os_rename":
                os.rename(src, target)
            elif how == "path_rename":
                src.rename(target)
            elif how == "path_replace":
                src.replace(target)
            elif how == "shutil_move":
                shutil.move(str(src), str(target))
    finally:
        # Only a broken guard could leave this file; the test then fails and the
        # probe is plainly named for removal.
        assert not target.exists(), f"{how} reached the production directory: {target}"


@pytest.mark.parametrize("how", ["os_remove", "os_unlink", "path_unlink", "rmtree"])
def test_deleting_from_production_is_refused(how, expect_production_write_blocked):
    target = _probe()                       # does not exist: harmless even unguarded
    with pytest.raises(AssertionError, match="PRODUCTION"):
        if how == "os_remove":
            os.remove(target)
        elif how == "os_unlink":
            os.unlink(target)
        elif how == "path_unlink":
            target.unlink()
        elif how == "rmtree":
            shutil.rmtree(REAL_DATA / f"__isolation_probe_dir_{uuid.uuid4().hex}")


def test_a_writable_connection_to_the_ledger_is_refused(expect_production_write_blocked):
    db = REAL_DATA / "aria_core.db"
    before = _fingerprint(db)
    with pytest.raises(AssertionError, match="PRODUCTION"):
        sqlite3.connect(str(db))
    with pytest.raises(AssertionError, match="PRODUCTION"):
        sqlite3.connect(f"file:{db.as_posix()}?mode=rw", uri=True)
    if db.exists():
        ro = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)   # reads stay legal
        ro.close()
    assert _fingerprint(db) == before


def test_a_refused_write_that_code_swallows_is_still_recorded():
    import conftest
    n = len(conftest.BLOCKED_PRODUCTION_WRITES)
    try:
        (REAL_DATA / f"__isolation_probe_{uuid.uuid4().hex}.json").write_text("x")
    except Exception:
        pass                                 # what careless production code does
    assert len(conftest.BLOCKED_PRODUCTION_WRITES) == n + 1
    del conftest.BLOCKED_PRODUCTION_WRITES[n:]   # this one was deliberate


def test_blocked_writes_are_reported_and_still_fail_the_session(monkeypatch):
    """The two terminal-summary hooks were once defined twice, so the second
    definition silently replaced the first and the blocked-write section never
    printed. Drives both hooks directly with a fake blocked write, via
    monkeypatch so the real BLOCKED_PRODUCTION_WRITES list — and this test's
    own session — is untouched once the test ends."""
    import types

    import conftest

    monkeypatch.setattr(conftest, "BLOCKED_PRODUCTION_WRITES",
                         ["PRODUCTION WRITE BLOCKED: fake/probe.json"])

    session = types.SimpleNamespace(exitstatus=pytest.ExitCode.OK)
    conftest.pytest_sessionfinish(session, pytest.ExitCode.OK)
    assert session.exitstatus == pytest.ExitCode.TESTS_FAILED

    calls = []
    reporter = types.SimpleNamespace(
        write_sep=lambda *a, **k: calls.append(("write_sep", a, k)),
        section=lambda *a, **k: calls.append(("section", a, k)),
        write_line=lambda *a, **k: calls.append(("write_line", a, k)),
    )
    conftest.pytest_terminal_summary(reporter, pytest.ExitCode.TESTS_FAILED, None)

    assert any(kind == "write_sep" and "isolation" in str(args) for kind, args, _ in calls), \
        "isolation summary line went missing when the blocked-write section was added"
    assert any(kind == "section" and "PRODUCTION WRITES BLOCKED" in args[0] for kind, args, _ in calls)
    assert any(kind == "write_line" and "fake/probe.json" in args[0] for kind, args, _ in calls)


def test_research_archives_are_redirected_away_from_production():
    from src.intelligence.research import engine, pit_data
    for p in (pit_data.ARCHIVE_DIR, pit_data.OBSERVATIONS, engine.LATEST_DIR):
        assert REAL_DATA not in Path(p).resolve().parents, p
