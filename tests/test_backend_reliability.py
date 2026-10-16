"""
tests/test_backend_reliability.py
=================================
Three ways the backend stopped doing its job while looking alive, each measured
on the owner's laptop in October 2026 and each fixed in source — none of them
by changing the host.

1. LOOPS THAT ONLY COUNT AWAKE TIME. `time.sleep(21600)` does not advance while
   the machine is suspended, so the six-hour macro refresh needed six awake
   hours: the snapshot written 2026-10-08 23:27 was never refreshed in the 35
   hours that process lived. `_sleep_wallclock` waits against the wall clock.

2. A BRAIN THAT GOT ONE CHANCE. The cognitive daemon looked for Ollama once,
   five seconds after boot. Ollama starts from the same login; when it was not
   answering yet, the brain never started for the life of the process.

3. TWO THINGS STARTING THE BACKEND AT LOGIN. The scheduled task starts a logged
   backend; AUTORUN_ARIA.bat then killed whatever held port 8000 and started an
   unlogged one. The file was also saved with LF line endings, which cmd.exe
   mis-parses (it ran fragments of the comment header as commands).

None of this makes a sleeping laptop a reliable host: nothing runs while it is
asleep. These tests pin only what the code now does.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend import main as bm      # noqa: E402


# ── 1. the wall-clock sleep ──────────────────────────────────────────────────

class _Clock:
    """A fake host: `sleep(n)` advances the wall clock by n, plus any suspend
    that happens to occur during that nap."""

    def __init__(self, suspend_after_naps: int = -1, suspend_for: float = 0.0):
        self.t = 1_000_000.0
        self.naps: list[float] = []
        self._suspend_after = suspend_after_naps
        self._suspend_for = suspend_for

    def now(self) -> float:
        return self.t

    def sleep(self, n: float) -> None:
        self.naps.append(n)
        self.t += n
        if len(self.naps) == self._suspend_after:
            self.t += self._suspend_for          # the lid closed here


def test_an_awake_host_waits_the_full_interval():
    c = _Clock()
    bm._sleep_wallclock(600, step=30, _now=c.now, _sleep=c.sleep)
    assert sum(c.naps) == pytest.approx(600)
    assert max(c.naps) <= 30


def test_a_suspended_host_runs_the_overdue_tick_on_waking():
    """Six-hour interval; the laptop sleeps eight hours after the second nap.
    The old `time.sleep(21600)` would still owe almost six awake hours."""
    c = _Clock(suspend_after_naps=2, suspend_for=8 * 3600)
    bm._sleep_wallclock(21600, step=30, _now=c.now, _sleep=c.sleep)
    assert sum(c.naps) <= 60, "it kept waiting after the interval had passed"


def test_a_short_remainder_is_not_overslept():
    c = _Clock()
    bm._sleep_wallclock(45, step=30, _now=c.now, _sleep=c.sleep)
    assert c.naps == [30, pytest.approx(15)]


def test_every_refresh_loop_uses_the_wall_clock():
    import inspect
    for name in ("_macro_loop", "_world_loop", "_ledger_loop", "_fx_monitor_loop",
                 "_outcome_resolver_loop", "_research_eye_loop", "_daily_report_loop"):
        src = inspect.getsource(getattr(bm, name))
        assert "_sleep_wallclock(" in src, f"{name} went back to an awake-time sleep"


# ── 2. the brain keeps looking for Ollama ────────────────────────────────────

@pytest.fixture(autouse=True)
def _fresh_cancel_flag():
    bm._brain_boot_cancelled.clear()
    yield
    bm._brain_boot_cancelled.clear()


def test_the_brain_starts_when_ollama_arrives_late():
    answers = iter([False, False, False, True])
    started, waits = [], []
    ok = bm._wait_for_ollama_then_start_brain(
        lambda: next(answers), lambda: started.append(1), waits.append)
    assert ok is True and started == [1]
    assert waits == list(bm._BRAIN_RETRY_S[:3]), "one wait per missed look, backing off"


def test_it_starts_immediately_when_ollama_is_already_up():
    started, waits = [], []
    assert bm._wait_for_ollama_then_start_brain(
        lambda: True, lambda: started.append(1), waits.append) is True
    assert started == [1] and waits == []


def test_a_probe_that_raises_counts_as_not_ready():
    calls = {"n": 0}

    def probe():
        calls["n"] += 1
        if calls["n"] < 3:
            raise OSError("connection refused")
        return True
    started = []
    assert bm._wait_for_ollama_then_start_brain(
        probe, lambda: started.append(1), lambda s: None) is True
    assert started == [1]


def test_a_brain_that_cannot_start_is_not_retried_forever():
    attempts = []

    def start():
        attempts.append(1)
        raise RuntimeError("chroma tenant unavailable")
    ok = bm._wait_for_ollama_then_start_brain(lambda: True, start, lambda s: None)
    assert ok is False
    assert len(attempts) == bm._BRAIN_MAX_START_FAILURES


def test_an_explicit_stop_ends_the_boot_time_wait():
    """POST /api/brain/stop sets the flag: a brain the owner stopped must not be
    started by a wait that was still running."""
    started, looks = [], []

    def probe():
        looks.append(1)
        if len(looks) == 2:
            bm._brain_boot_cancelled.set()
        return False
    ok = bm._wait_for_ollama_then_start_brain(
        probe, lambda: started.append(1), lambda s: None, max_checks=50)
    assert ok is False and started == [] and len(looks) == 2


def test_the_stop_endpoint_cancels_the_wait():
    import inspect
    assert "_brain_boot_cancelled.set()" in inspect.getsource(bm.brain_stop)


def test_starting_late_grants_no_trading_authority():
    """The retry only ever calls the brain daemon's own start(). Nothing in it
    reaches the order path."""
    import inspect
    src = (inspect.getsource(bm._wait_for_ollama_then_start_brain)
           + inspect.getsource(bm._start_brain_if_ollama))
    for forbidden in ("approve_and_execute", "submit_order", "OrderManager",
                      "auto_execute", "save_config"):
        assert forbidden not in src


# ── 3. the login launcher ────────────────────────────────────────────────────

LAUNCHER = ROOT / "AUTORUN_ARIA.bat"


def _commands() -> list[str]:
    out = []
    for line in LAUNCHER.read_bytes().decode("utf-8").splitlines():
        s = line.strip()
        if s and not s.upper().startswith(("REM", "::", "ECHO")):
            out.append(s)
    return out


def test_the_launcher_is_saved_with_crlf_line_endings():
    """cmd.exe mis-parses an LF-only batch file with non-ASCII bytes in it: the
    LF copy of this file ran 'AUTORUN_ARIA' and 'at login' out of its own
    comment header as commands (reproduced 2026-10-10)."""
    raw = LAUNCHER.read_bytes()
    assert raw.count(b"\n") == raw.count(b"\r\n") > 0


def test_the_launcher_has_no_labels_to_misparse():
    assert not [c for c in _commands() if c.startswith(":")]


def test_port_8000_is_only_freed_when_no_backend_answers():
    cmds = _commands()
    kills_8000 = [i for i, c in enumerate(cmds) if '":8000 "' in c]
    assert len(kills_8000) == 1
    guard = cmds[kills_8000[0] - 1]
    assert guard.replace(" ", "").lower() == "ifnotdefinedaria_backend_up(", \
        "the port-8000 kill is no longer guarded by the health check"
    assert not [c for c in cmds if "(8000 3000)" in c], "the unconditional kill is back"


def test_a_second_backend_is_started_only_when_none_answers():
    starts = [c for c in _commands() if "backend.main:app" in c]
    assert len(starts) == 1
    assert starts[0].lower().startswith("if not defined aria_backend_up start ")


def test_only_a_200_from_health_counts_as_up():
    probes = [c for c in _commands() if "/health" in c]
    assert probes, "the launcher no longer looks for a running backend"
    for p in probes:
        assert "curl.exe -f " in p, "without -f a 404 or 500 would count as healthy"
        assert "127.0.0.1:8000/health" in p


def test_the_dashboard_port_is_still_freed_and_the_frontend_still_started():
    cmds = _commands()
    assert any('":3000 "' in c for c in cmds)
    assert any("npm.cmd run dev" in c for c in cmds)
