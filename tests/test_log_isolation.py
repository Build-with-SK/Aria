"""
tests/test_log_isolation.py
===========================
The test suite may not write the production audit log.

data/aria.log is where kill-switch engagements, trading-mode changes and
strategy promotions are recorded. Until 2026-10-06 every test run appended to
it — "KILL SWITCH ENGAGED by test", "strategy test-strategy promoted
DRAFT -> RESEARCH by owner" — because test modules import backend.main at
collection time and that import attached the file handler to the real path.
An audit trail that contains fiction is not an audit trail.
"""
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

REPO = Path(__file__).resolve().parent.parent
PRODUCTION_LOG = (REPO / "data" / "aria.log").resolve()


def _file_handlers():
    return [h for h in logging.getLogger().handlers
            if getattr(h, "baseFilename", None)]


def test_no_handler_points_at_the_production_log():
    import backend.main  # noqa: F401 — attaches the handlers
    for h in _file_handlers():
        assert Path(h.baseFilename).resolve() != PRODUCTION_LOG, \
            f"a log handler writes the production audit log: {h.baseFilename}"


def test_the_backend_handler_goes_to_the_test_log():
    import backend.main  # noqa: F401
    TEST_LOG_FILE = Path(os.environ["ARIA_LOG_FILE"])
    targets = {Path(h.baseFilename).resolve() for h in _file_handlers()
               if getattr(h, "_aria", False)}
    assert TEST_LOG_FILE.resolve() in targets


def test_a_logged_audit_line_does_not_reach_production():
    import backend.main  # noqa: F401
    # A running backend may be appending to the real log at the same moment,
    # so the check is for this probe's text, not for an unchanged file size.
    logging.getLogger("src.execution.kill_switch").critical(
        "KILL SWITCH ENGAGED by test: isolation probe")
    for h in logging.getLogger().handlers:
        h.flush()
    assert not _probe_in(PRODUCTION_LOG)
    TEST_LOG_FILE = Path(os.environ["ARIA_LOG_FILE"])
    assert _probe_in(TEST_LOG_FILE)


def _probe_in(path: Path) -> bool:
    try:
        tail = path.read_bytes()[-20000:].decode("utf-8", "ignore")
    except OSError:
        return False
    return "isolation probe" in tail


def test_the_backend_refuses_the_production_path_under_pytest(monkeypatch):
    """The backstop: even with the override removed, a pytest process gets no
    file handler on data/aria.log rather than a polluted audit trail."""
    from backend import main
    monkeypatch.delenv("ARIA_LOG_FILE", raising=False)
    assert main._log_file_path() is None
    monkeypatch.setenv("ARIA_LOG_FILE", str(PRODUCTION_LOG))
    assert main._log_file_path() is None


def test_production_still_logs_to_data_aria_log(monkeypatch):
    """Outside pytest the default must be unchanged — the owner's log lives
    where the runbooks and the Mac mini preflight expect it."""
    from backend import main
    monkeypatch.delenv("ARIA_LOG_FILE", raising=False)
    monkeypatch.delitem(sys.modules, "pytest")
    assert main._log_file_path().resolve() == PRODUCTION_LOG
