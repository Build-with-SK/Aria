"""
tests/test_data_freshness_api.py
================================
Legacy generated files must say how old they are.

backtest_results.json, options_data.json, portfolio_analysis.json and
daily_report.json have exactly one writer — main_phase4_backup.py, which
nothing runs — and had not moved since 2026-05-31. /api/backtest,
/api/options, /api/portfolio and /api/report served them with no date at all,
so May's numbers read as today's. They are not deleted (the UI and the
Streamlit dashboard still read them); they are dated.
"""
import json
import os
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient          # noqa: E402

REMOTE = "203.0.113.21"
TOKEN = "test-owner-token-for-freshness"


@pytest.fixture(scope="module")
def app():
    from backend.main import app as fastapi_app
    saved = {k: os.environ.get(k) for k in ("ARIA_OWNER_TOKEN", "ARIA_TRUST_PROXY",
                                            "ARIA_OWNER_TOKEN_HASH")}
    os.environ["ARIA_OWNER_TOKEN"] = TOKEN
    os.environ.pop("ARIA_OWNER_TOKEN_HASH", None)
    os.environ.pop("ARIA_TRUST_PROXY", None)
    yield fastapi_app
    for k, v in saved.items():
        os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


@pytest.fixture
def owner(app, monkeypatch, tmp_path):
    from src.auth import hardening
    from src.auth import session as sess
    monkeypatch.setattr(sess.SESSIONS, "store", tmp_path / "sessions.json")
    monkeypatch.setattr(sess.SESSIONS, "session_key_file", tmp_path / ".key")
    sess.SESSIONS._attempts.clear()
    hardening._limiter._hits.clear()
    client = TestClient(app, client=(REMOTE, 53101))
    assert client.post("/api/auth/login", json={"token": TOKEN}).status_code == 200
    return client


@pytest.fixture
def data_dir(monkeypatch, tmp_path):
    """Point the backend loader and the world model at one fixture directory."""
    import backend.main as bm
    from src.core import world
    monkeypatch.setattr(bm, "ROOT", tmp_path)
    (tmp_path / "data").mkdir()
    monkeypatch.setattr(world, "DATA", tmp_path / "data")

    def write(name: str, body, age_hours: float):
        p = tmp_path / "data" / name
        p.write_text(json.dumps(body), encoding="utf-8")
        t = time.time() - age_hours * 3600
        os.utime(p, (t, t))
    return write


def test_options_are_dated_in_headers_and_body_is_unchanged(owner, data_dir):
    data_dir("options_data.json", {"SPY": {"pcr": 0.9}}, age_hours=24 * 120)
    r = owner.get("/api/options")
    assert r.status_code == 200
    assert r.json() == {"SPY": {"pcr": 0.9}}        # UI iterates this map
    assert r.headers["X-ARIA-Data-Status"] == "STALE"
    assert r.headers["X-ARIA-Data-Legacy"] == "true"
    assert float(r.headers["X-ARIA-Data-Age-Hours"]) > 24 * 100


def test_backtest_carries_freshness_in_the_body(owner, data_dir):
    data_dir("backtest_results.json",
             {"AAPL": {"metrics": {"sharpe_ratio": 1.2}}}, age_hours=24 * 120)
    body = owner.get("/api/backtest").json()
    assert body["count"] == 1
    assert body["freshness"]["status"] == "STALE"
    assert body["freshness"]["legacy"] is True


def test_portfolio_model_is_dated(owner, data_dir):
    data_dir("portfolio_analysis.json", {"x": 1}, age_hours=24 * 120)
    r = owner.get("/api/portfolio")
    assert r.json()["freshness"]["status"] == "STALE"
    assert r.headers["X-ARIA-Data-Status"] == "STALE"


def test_a_fresh_file_says_fresh(owner, data_dir):
    data_dir("ml_predictions.json", {"AAPL": {"p": 0.6}}, age_hours=1)
    r = owner.get("/api/ml")
    assert r.json() == {"AAPL": {"p": 0.6}}
    assert r.headers["X-ARIA-Data-Status"] == "FRESH"


def test_the_registry_lists_every_dataset(owner, data_dir):
    data_dir("signals.json", {}, age_hours=1)
    body = owner.get("/api/data/freshness").json()
    names = {r["dataset"]: r for r in body["datasets"]}
    assert names["signals.json"]["status"] == "UNKNOWN"
    assert names["daily_report.json"]["status"] == "UNKNOWN"     # missing
    assert "daily_report.json" in body["stale"]


def test_the_registry_is_not_public(app):
    from src.auth import hardening
    hardening._limiter._hits.clear()
    anon = TestClient(app, client=(REMOTE, 53102))
    assert anon.get("/api/data/freshness").status_code in (401, 403)


# ── how far the last pipeline run got ────────────────────────────────────────

def _marker(data_dir, **body):
    data_dir("pipeline_run.json", body, age_hours=0)


def test_no_marker_is_unknown_not_fine(data_dir):
    from src.core import world
    data_dir("signals.json", {}, age_hours=1)
    assert world.pipeline_run()["state"] == "UNKNOWN"


def test_a_completed_run_says_so(data_dir):
    from datetime import datetime
    from src.core import world
    now = datetime.now().isoformat(timespec="seconds")
    _marker(data_dir, started_at=now, step="complete", completed=True, finished_at=now)
    assert world.pipeline_run()["state"] == "COMPLETED"


def test_a_young_unfinished_run_is_in_progress(data_dir):
    from datetime import datetime, timedelta
    from src.core import world
    started = (datetime.now() - timedelta(hours=1)).isoformat(timespec="seconds")
    _marker(data_dir, started_at=started, step="ml", completed=False)
    assert world.pipeline_run()["state"] == "IN_PROGRESS"


def test_a_run_killed_mid_training_is_reported_with_its_step(data_dir):
    """The shape of every scheduled run since August: started, reached ML,
    never finished."""
    from datetime import datetime, timedelta
    from src.core import world
    started = (datetime.now() - timedelta(hours=26)).isoformat(timespec="seconds")
    _marker(data_dir, started_at=started, step="ml", completed=False)
    run = world.pipeline_run()
    assert run["state"] == "DID_NOT_FINISH"
    assert "'ml'" in run["why"] and run["step"] == "ml"


def test_the_freshness_endpoint_carries_the_run_state(owner, data_dir):
    from datetime import datetime, timedelta
    started = (datetime.now() - timedelta(hours=26)).isoformat(timespec="seconds")
    _marker(data_dir, started_at=started, step="ml", completed=False)
    body = owner.get("/api/data/freshness").json()
    assert body["pipeline_run"]["state"] == "DID_NOT_FINISH"
    assert {"datasets", "stale", "as_of"} <= set(body), "existing keys must stay"


def test_the_generated_task_lets_a_full_run_finish(tmp_path, monkeypatch):
    """src/scheduler/scheduler.py writes install_scheduler.ps1. Its 2-hour limit
    killed every scheduled run mid-ML; the only full run measured took 6 h."""
    import re
    from src.scheduler import scheduler as S
    monkeypatch.setattr(S, "PROJECT_ROOT", tmp_path)
    # Spoof this module's platform, not the process-wide os.name used by
    # pathlib and the production-state isolation guard on Linux.
    from types import SimpleNamespace
    monkeypatch.setattr(S, "os", SimpleNamespace(name="nt"))
    assert S.install_windows_task() is True
    ps = (tmp_path / "install_scheduler.ps1").read_text()
    hours = int(re.search(r"ExecutionTimeLimit \(New-TimeSpan -Hours (\d+)\)", ps).group(1))
    assert hours >= 6, f"a {hours}h limit kills the pipeline before ML finishes"
    assert "-StartWhenAvailable" in ps, "a run missed during sleep must still happen"


# ── the SQLite signal store is legacy and says so ────────────────────────────

def test_the_legacy_signal_database_is_registered_and_dated(owner, data_dir):
    """Nothing has called src/database/database.py's save_* since 2026-05-31,
    and /api/history, /api/stats and /api/performance served it undated."""
    data_dir("trading_intelligence.db", {}, age_hours=24 * 130)
    r = owner.get("/api/stats")
    assert r.status_code == 200, r.text
    assert r.headers["X-ARIA-Data-Status"] == "STALE"
    assert r.headers["X-ARIA-Data-Legacy"] == "true"
    assert r.json()["freshness"]["legacy"] is True
    names = {d["dataset"] for d in owner.get("/api/data/freshness").json()["datasets"]}
    assert "trading_intelligence.db" in names


@pytest.mark.parametrize("path", ["/api/history/AAPL", "/api/performance/AAPL"])
def test_signal_history_endpoints_carry_freshness(owner, data_dir, path):
    from src.database import database
    database.initialise_database()          # the isolated, empty copy
    data_dir("trading_intelligence.db", {}, age_hours=24 * 130)
    r = owner.get(path)
    assert r.status_code == 200, r.text
    assert r.headers["X-ARIA-Data-Status"] == "STALE"
    assert r.json()["freshness"]["status"] == "STALE"
    assert "records" in r.json(), "existing keys must stay"


# ── which clock an age comes from ────────────────────────────────────────────

def test_an_undated_signals_file_is_unknown_regardless_of_write_time(data_dir):
    """signals.json recorded no date at all, so "market state 56 hours old" was
    the age of a file. The age stays — the execution gate reads it — but the
    basis is now stated and no observation date is invented."""
    from src.core import world
    data_dir("signals.json", {"AAPL": {"composite_score": 12}}, age_hours=56)
    f = world.dataset_freshness("signals.json")
    assert f["basis"] == "row_evidence" and f["status"] == "UNKNOWN"
    assert f["age_hours"] is None
    assert f["observation"]["recorded"] is False
    assert "no data_as_of" in f["observation"]["why"]


def test_recorded_bar_dates_are_reported_beside_the_file_age(data_dir):
    from src.core import world
    data_dir("signals.json", {
        "AAPL": {"data_as_of": "2026-10-07", "generated_at": "2026-10-08T07:39:00"},
        "TSCO.L": {"data_as_of": "2026-10-06", "generated_at": "2026-10-08T07:39:00"},
        "ODD": {"composite_score": 1}}, age_hours=2)
    f = world.dataset_freshness("signals.json")
    obs = f["observation"]
    assert obs == {"recorded": True, "rows": 3, "rows_dated": 2,
                   "earliest_bar": "2026-10-06", "latest_bar": "2026-10-07",
                   "generated_at": "2026-10-08T07:39:00"}
    # Old bars and an undated row hold execution even when the file was rewritten.
    assert f["basis"] == "row_evidence" and f["status"] == "STALE"
    assert f["as_of"].startswith("2026-10-06")


def test_a_stated_data_date_is_labelled_as_such(data_dir):
    from src.core import world
    data_dir("macro_data.json", {"data_date": "2026-05-31", "vix": 15.3}, age_hours=1)
    f = world.dataset_freshness("macro_data.json")
    assert f["basis"] == "stated_date" and f["status"] == "STALE"
    assert "observation" not in f


def test_a_missing_file_has_no_basis_and_no_invented_date(data_dir):
    from src.core import world
    data_dir("macro_data.json", {}, age_hours=1)
    f = world.dataset_freshness("ml_predictions.json")
    assert f["status"] == "UNKNOWN" and f["basis"] is None and f["as_of"] is None
    assert f["observation"]["recorded"] is False


def test_the_execution_gate_requires_valid_row_dates(data_dir):
    """The provenance fields are additive. `signals_freshness()` — the gate on
    auto-execution — must decide exactly as it did: on the file's age against
    the 30-hour budget, and never open on a recorded bar date alone."""
    from src.desk import opinion
    data_dir("signals.json", {"AAPL": {"data_as_of": "2099-01-01"}}, age_hours=31)
    assert opinion.signals_freshness()["fresh"] is False
    data_dir("signals.json", {"AAPL": {"composite_score": 1}}, age_hours=29)
    assert opinion.signals_freshness()["fresh"] is False
    from datetime import datetime
    data_dir("signals.json", {"AAPL": {"generated_at": datetime.now().isoformat()}}, age_hours=29)
    assert opinion.signals_freshness()["fresh"] is False
    data_dir("signals.json", {"AAPL": {"generated_at": datetime.now().isoformat(),
                                         "data_as_of": datetime.now().isoformat()}}, age_hours=29)
    assert opinion.signals_freshness()["fresh"] is True


def test_the_chat_block_says_how_each_age_is_known(data_dir, monkeypatch):
    import backend.main as bm
    data_dir("macro_data.json", {"data_date": "2026-10-08", "regime": "x"}, age_hours=1)
    data_dir("signals.json", {"AAPL": {"composite_score": 1, "ticker": "AAPL"}}, age_hours=50)
    data_dir("ml_predictions.json", {"AAPL": {"overall_signal": "Bullish",
                                               "data_as_of": "2026-05-29"}}, age_hours=24 * 130)
    note = bm._age_basis_note()
    assert "MACRO: dated by the file's own data_date (2026-10-08)" in note
    assert "SIGNALS: age is the oldest row evidence; missing or invalid dates are stale" in note
    assert "ML: age is the time the FILE was written; bars through 2026-05-29" in note
    assert note in bm._build_market_context()
