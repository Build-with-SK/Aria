"""
tests/test_launch_check.py
==========================
src/core/launch_check measures the launch checklist from a repository's live
data/ and the running backend. These fixtures are small repositories written
to tmp_path; nothing reads production data, and the one HTTP case is a local
stub server.
"""
import json
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from src.core import launch_check as lc

NOW = datetime(2026, 10, 17, 9, 0, tzinfo=timezone.utc)


def _w(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body if isinstance(body, str) else json.dumps(body), encoding="utf-8")


def _iso(hours_ago):
    return (NOW - timedelta(hours=hours_ago)).isoformat()


@pytest.fixture
def repo(tmp_path):
    d = tmp_path / "data"
    _w(d / "signals.json", {
        "AAPL": {"data_as_of": "2026-10-16", "generated_at": _iso(2)},
        "MSFT": {"data_as_of": "2026-10-16", "generated_at": _iso(2)},
        "GONE": {"data_as_of": "2026-08-21", "generated_at": _iso(2)}})
    _w(d / "ml_predictions.json", {"AAPL": {"generated_at": _iso(5)}})
    _w(d / "pipeline_run.json", {"started_at": _iso(6), "step": "complete", "pid": 1,
                                  "completed": True, "finished_at": _iso(1)})
    _w(d / "desk" / "heartbeat.json", {"at": _iso(0.05), "tick_count": 9, "errors": []})
    _w(d / "desk" / "equity_curve.jsonl",
       "\n".join(json.dumps({"at": _iso(h), "equity": 10_000.0}) for h in (3, 2.5, 2, 1.5, 1, 0.2)))
    _w(d / "desk" / "two_day_test.json", {"started_at": _iso(3), "ends_at": _iso(-45),
                                          "start_equity_usd": 10_000.0})
    _w(d / "desk" / "playbooks.json", [{"ticker": "AAPL", "status": "armed"}])
    _w(d / "desk" / "executions.jsonl", "")
    _w(d / "desk_config.json", {"risk_profile": "MODERATE", "auto_execute": True,
                                 "daily_notional_budget": 5000, "max_trades_per_day": 5})
    _w(d / "kill_switch.json", {"engaged": False})
    _w(d / "trading" / "mode.json", {"mode": "PAPER", "since": "2026-09-23"})
    _w(d / "execution" / "approval_queue.json", [{"status": "executed"}])
    _w(tmp_path / "frontend" / "dist" / "index.html",
       '<script type="module" src="/app/assets/index-abc.js"></script>')
    _w(tmp_path / "frontend" / "dist" / "assets" / "index-abc.js", "//")
    _w(tmp_path / "tis_run.log",
       "2026-10-17 07:30:00,000 [INFO] TIS:   TRADING INTELLIGENCE SYSTEM — Phase 5 / 6\n"
       "2026-10-17 07:41:00,000 [INFO] TIS: Sentiment analysis complete for 3 tickers: "
       "2 with news, 1 unavailable (no articles or the fetch failed — not scored as neutral).\n")
    return tmp_path


def by_name(results):
    return {r["check"]: r for r in results}


def test_a_healthy_repository_has_no_failure_and_names_what_it_cannot_see(repo):
    res = by_name(lc.run(repo, None, None, now=NOW))
    assert not [r for r in res.values() if r["status"] == lc.FAIL], res
    # never a PASS for what was not measured
    for name in ("runtime", "owner endpoints refuse without a session", "SENTINEL",
                 "pages render owner data", "full-suite regression"):
        assert res[name]["status"] == lc.NOT_MEASURED
    assert res["sentiment"]["status"] == lc.PASS
    assert res["paper-test continuity"]["status"] == lc.PASS


def test_a_stale_row_inside_a_fresh_signal_file_is_named(repo):
    sig = by_name(lc.run(repo, None, None, now=NOW))["signals freshness"]
    assert sig["status"] == lc.WARN and sig["stale_rows"] == ["GONE (2026-08-21)"]


@pytest.mark.parametrize("change,check", [
    (lambda r: _w(r / "data" / "trading" / "mode.json", {"mode": "LIVE_AUTONOMOUS"}),
     "PAPER-mode enforcement"),
    (lambda r: _w(r / "data" / "ml_predictions.json", {"AAPL": {"generated_at": _iso(72)}}),
     "ML predictions"),
    (lambda r: _w(r / "data" / "signals.json", {"AAPL": {"data_as_of": "2026-10-14",
                                                        "generated_at": _iso(40)}}),
     "signals freshness"),
    (lambda r: _w(r / "tis_run.log",
                  "2026-10-17 07:30:00,000 [INFO] TIS:   TRADING INTELLIGENCE SYSTEM\n"
                  "2026-10-17 07:41:00,000 [WARNING] TIS: Sentiment analysis failed: "
                  "'list' object has no attribute 'get'\n"), "sentiment"),
    (lambda r: _w(r / "data" / "pipeline_run.json",
                  {"started_at": _iso(3), "step": "ml", "step_at": _iso(2.9),
                   "pid": 999_999_991, "completed": False}), "pipeline run"),
    (lambda r: _w(r / "data" / "desk" / "heartbeat.json", {"at": _iso(2), "errors": []}),
     "desk + broker connectivity"),
    (lambda r: (r / "frontend" / "dist" / "assets" / "index-abc.js").unlink(),
     "frontend build"),
], ids=["live-mode", "ml-stale", "signals-stale", "sentiment-failed", "pipeline-dead",
        "desk-silent", "asset-missing"])
def test_each_failure_is_reported_as_a_failure(repo, change, check):
    change(repo)
    res = by_name(lc.run(repo, None, None, now=NOW))
    assert res[check]["status"] == lc.FAIL, res[check]


@pytest.mark.parametrize("change,check", [
    (lambda r: _w(r / "data" / "desk" / "playbooks.json", [{"status": "unknown"}]),
     "reflex observability"),
    (lambda r: _w(r / "data" / "execution" / "approval_queue.json", [{"status": "unknown"}]),
     "order reconciliation"),
    (lambda r: _w(r / "data" / "kill_switch.json", {"engaged": True}),
     "kill switch + risk controls"),
    (lambda r: _w(r / "data" / "desk" / "equity_curve.jsonl", "\n".join(
        json.dumps({"at": _iso(h), "equity": 1.0}) for h in (2.9, 0.2))),
     "paper-test continuity"),
], ids=["unknown-playbook", "unknown-order", "kill-switch-engaged", "equity-gap"])
def test_what_needs_a_look_is_a_warning(repo, change, check):
    change(repo)
    assert by_name(lc.run(repo, None, None, now=NOW))[check]["status"] == lc.WARN


def test_it_reads_and_never_writes(repo, tmp_path_factory):
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in repo.rglob("*") if p.is_file()}
    out = tmp_path_factory.mktemp("out") / "r.json"
    code = lc.main(["--root", str(repo), "--json", str(out)])
    after = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in repo.rglob("*") if p.is_file()}
    assert before == after
    assert code == 0 and json.loads(out.read_text())["results"]
    assert lc.main(["--root", str(repo), "--json", str(repo / "x.json")]) == 2
    assert not (repo / "x.json").exists()


def test_an_owner_endpoint_answering_without_a_session_fails(repo):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            leak = self.path == "/api/desk/executions"
            self.send_response(200 if leak or self.path in ("/health", "/ready") else 401)
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{srv.server_port}"
        res = by_name(lc.run(repo, base, None, now=NOW))
    finally:
        srv.shutdown()
    assert res["runtime"]["status"] == lc.PASS
    auth = res["owner endpoints refuse without a session"]
    assert auth["status"] == lc.FAIL and "/api/desk/executions 200" in auth["evidence"]


def test_pid_liveness_is_read_without_touching_the_process():
    import os
    assert lc._pid_alive(os.getpid()) is True
    assert lc._pid_alive(999_999_991) is False
    assert lc._pid_alive("not a pid") is None
