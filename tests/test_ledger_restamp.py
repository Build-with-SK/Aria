"""
tests/test_ledger_restamp.py
============================
The one-off repair for ML rows stamped with the London listing on a US price
(src/core/ledger_restamp.py).

The bad rows are made the way the live ones were: an ML call recorded without
a stated symbol, so the symbol master stamps `AAL` as AAL.L on XLON. Everything
here writes to a temp ledger and a temp symbol master.
"""
from __future__ import annotations

import hashlib
import importlib.util
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pytest

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "universe_fixture.py"
MADE = "2026-10-17T23:40:28"             # the first bad ML pass
ASOF = "2026-10-16"                      # the last US bar that pass saw
#: The grader runs on a pinned clock. On the real one the 20-day "pending"
#: row falls due on 2026-11-14 and the fixture would skip it too.
GRADED_ON = datetime(2026, 10, 30, 12, 0)


@pytest.fixture
def spine(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("restamp_universe", FIXTURE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.build(tmp_path / "universe.db")
    from src.core import identity
    monkeypatch.setattr(identity, "UNIVERSE_DB", tmp_path / "universe.db")
    monkeypatch.setenv("ARIA_CORE_DB", str(tmp_path / "spine.db"))
    from src.core import bus as bus_mod
    monkeypatch.setattr(bus_mod, "_initialised", False)
    monkeypatch.setattr(bus_mod, "_subscribers", [])
    from src.core import ledger as ledger_mod
    monkeypatch.setattr(ledger_mod, "_ready", False)
    return ledger_mod


def _db(tmp_path) -> Path:
    return tmp_path / "spine.db"


#: Closes on any day. London lines in pence, US lines in dollars.
CLOSES = {"AAL": 11.90, "AAL.L": 4000.0, "BA": 190.0, "BA.L": 2200.0}


def prices(table=None):
    table = CLOSES if table is None else table
    return lambda symbol, on: table.get(symbol)


def _bad(spine, subject, price, *, horizon=5, created=MADE):
    """An ML row as the old recorder wrote it: no stated symbol."""
    pid = spine.record_prediction(
        subject=subject, claim=f"{subject} down over {horizon}d", source="ml-ensemble",
        direction="bear", horizon_days=horizon, price_at=price, created_at=created,
        data_as_of=ASOF, information_session=ASOF, announce=False)
    assert pid
    return pid


def _row(spine, pid) -> dict:
    with spine.connect() as conn:
        conn.row_factory = sqlite3.Row
        return dict(conn.execute("SELECT * FROM predictions WHERE id=?", (pid,)).fetchone())


def _skip_by_grading(spine, monkeypatch):
    """Let the grader meet the bad stamp, as the live grader did."""
    monkeypatch.setattr(spine, "_price_on", prices())
    spine.resolve_due(now=GRADED_ON)


@pytest.fixture
def ledger_with_bad_rows(spine, monkeypatch):
    """A pending bad row, a skipped bad row, and three rows that must not move."""
    aal = _bad(spine, "AAL", 11.88, horizon=20)          # due 2026-11: still pending
    ba = _bad(spine, "BA", 190.21, horizon=1)            # due long ago: gets skipped
    from src.intelligence.recorders import record_ml_predictions
    record_ml_predictions({"AAL": {"horizons": {"5": {"horizon_days": 5, "bullish_prob": .3}}}},
                          data_as_of={"AAL": "2026-10-28"}, last_close={"AAL": 12.58},
                          trainer_version="v6.0", created_at="2026-10-29T08:27:21")
    london = spine.record_prediction(subject="AAL", claim="technical", source="technical",
                                     direction="bull", horizon_days=10, price_at=3913.0,
                                     created_at=MADE, announce=False)
    _skip_by_grading(spine, monkeypatch)
    return {"aal": aal, "ba": ba, "london": london}


def _dump(db: Path) -> str:
    with sqlite3.connect(str(db)) as conn:
        rows = conn.execute("SELECT * FROM predictions ORDER BY id").fetchall()
    return hashlib.sha256(repr(rows).encode()).hexdigest()


# ── the plan ───────────────────────────────────────────────────────────────

def test_the_trap_is_reproduced(spine, ledger_with_bad_rows):
    ids = ledger_with_bad_rows
    aal, ba = _row(spine, ids["aal"]), _row(spine, ids["ba"])
    assert (aal["provider_symbol"], aal["venue"], aal["resolved"]) == ("AAL.L", "XLON", 0)
    assert (ba["provider_symbol"], ba["resolved"], ba["correct"]) == ("BA.L", 1, None)
    assert ba["outcome_note"].startswith("GRADING_SKIPPED / IDENTITY_UNRESOLVED")


def test_only_the_mis_stamped_ml_rows_are_planned(spine, tmp_path, ledger_with_bad_rows):
    from src.core import ledger_restamp as rs
    ids = ledger_with_bad_rows
    plan = rs.build_plan(_db(tmp_path), prices())
    by_id = {p["id"]: p for p in plan["plans"]}
    # The correct ML row (stamped AAL by the fixed recorder) and the genuine
    # London technical row are not candidates at all.
    assert set(by_id) == {ids["aal"], ids["ba"]}
    assert by_id[ids["aal"]]["action"] == rs.RESTAMP
    assert by_id[ids["ba"]]["action"] == rs.RESTAMP_AND_RESET
    for p in by_id.values():
        assert p["new"]["provider_symbol"] == p["subject"]
        assert p["new"]["identity_basis"] == "SOURCE_STATED"
        assert p["new"]["venue"] == "XNYS" and p["old"]["venue"] == "XLON"
        assert p["ref_day"] == ASOF           # the grader's own anchor
    assert plan["counts"] == {rs.RESTAMP: 1, rs.RESTAMP_AND_RESET: 1}


def test_a_mis_stamp_written_after_the_fix_is_not_a_repair_target(spine, tmp_path):
    """After FIXED_AT the recorder states the identity. A disagreement there is a
    new defect to investigate, not something to paper over."""
    from src.core import ledger_restamp as rs
    _bad(spine, "AAL", 11.88, created="2026-10-28T08:00:00")
    assert rs.build_plan(_db(tmp_path), prices())["candidates"] == 0


@pytest.mark.parametrize("table,expect", [
    ({"AAL.L": 4000.0}, "UNVERIFIED: no AAL close"),             # no US close that day
    ({"AAL": 40.0, "AAL.L": 4000.0}, "UNVERIFIED: AAL closed"),  # US close disagrees
    ({"AAL": 11.90, "AAL.L": 11.95}, "AMBIGUOUS"),               # both lines match
])
def test_a_row_the_price_check_cannot_confirm_is_left_alone(spine, tmp_path, table, expect):
    from src.core import ledger_restamp as rs
    _bad(spine, "AAL", 11.88)
    (p,) = rs.build_plan(_db(tmp_path), prices(table))["plans"]
    assert p["action"] == rs.LEAVE and p["reason"].startswith(expect)


def test_a_graded_row_is_never_rewritten(spine, tmp_path):
    from src.core import ledger_restamp as rs
    pid = _bad(spine, "AAL", 11.88)
    spine.grade(pid, correct=True, price_end=11.0, actual_return=-0.07, note="t",
                announce=False)
    (p,) = rs.build_plan(_db(tmp_path), prices())["plans"]
    assert p["action"] == rs.LEAVE and "never rewrites an outcome" in p["reason"]


def test_the_dry_run_writes_nothing(spine, tmp_path, ledger_with_bad_rows):
    from src.core import ledger_restamp as rs
    before = _dump(_db(tmp_path))
    rs.build_plan(_db(tmp_path), prices())
    assert _dump(_db(tmp_path)) == before
    assert not (tmp_path / "backups").exists()


# ── the write ──────────────────────────────────────────────────────────────

def test_apply_writes_exactly_the_plan_after_a_backup(spine, tmp_path, ledger_with_bad_rows):
    from src.core import ledger_restamp as rs
    ids = ledger_with_bad_rows
    london_before = _row(spine, ids["london"])
    before = {k: _row(spine, ids[k]) for k in ("aal", "ba")}
    plan = rs.build_plan(_db(tmp_path), prices())
    out = rs.apply(_db(tmp_path), plan["plan_hash"], price_on=prices(), grader_stopped=True)

    assert (out["written"], out["reset"], out["left"]) == (2, 1, 0)
    assert out["rows_not_as_planned"] == [] and out["integrity_after"] == "ok"
    aal, ba = _row(spine, ids["aal"]), _row(spine, ids["ba"])
    assert (aal["provider_symbol"], aal["identity_basis"], aal["venue"]) == \
        ("AAL", "SOURCE_STATED", "XNYS")
    assert aal["resolved"] == 0 and aal["price_at"] == pytest.approx(11.88)
    # The skipped row is pending again, with no trace of the wrong grading.
    assert (ba["provider_symbol"], ba["resolved"], ba["correct"]) == ("BA", 0, None)
    assert ba["resolved_at"] is None and ba["outcome_note"] is None
    # Every column the apply does not write is exactly as it was.
    for key, row in (("aal", aal), ("ba", ba)):
        for col, was in before[key].items():
            if col not in rs._WRITTEN_COLS:
                assert row[col] == was, f"{key}.{col} changed"
    assert _row(spine, ids["london"]) == london_before
    # The backup holds the ledger as it was.
    with sqlite3.connect(out["backup"]) as b:
        assert b.execute("SELECT provider_symbol FROM predictions WHERE id=?",
                         (ids["aal"],)).fetchone()[0] == "AAL.L"


def test_apply_refuses_a_hash_it_was_not_shown(spine, tmp_path, ledger_with_bad_rows):
    from src.core import ledger_restamp as rs
    before = _dump(_db(tmp_path))
    with pytest.raises(SystemExit, match="does not match the approved hash"):
        rs.apply(_db(tmp_path), "0" * 64, price_on=prices(), grader_stopped=True)
    assert _dump(_db(tmp_path)) == before
    assert not (tmp_path / "backups").exists()


def test_apply_refuses_when_a_planned_row_moved(spine, tmp_path, ledger_with_bad_rows):
    """The grader skipped the pending row after the owner reviewed the plan:
    the plan no longer describes the ledger, so nothing is written."""
    from src.core import ledger_restamp as rs
    ids = ledger_with_bad_rows
    plan = rs.build_plan(_db(tmp_path), prices())
    spine.grade(ids["aal"], correct=None, note="GRADING_SKIPPED / IDENTITY_UNRESOLVED — t",
                announce=False)
    before = _dump(_db(tmp_path))
    with pytest.raises(SystemExit, match="does not match the approved hash"):
        rs.apply(_db(tmp_path), plan["plan_hash"], price_on=prices(), grader_stopped=True)
    assert _dump(_db(tmp_path)) == before


def _heartbeat(db: Path, *, last_start: datetime, last_ok: datetime) -> None:
    with sqlite3.connect(str(db)) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS workers (
            name TEXT PRIMARY KEY, label TEXT NOT NULL, loop TEXT NOT NULL DEFAULT 'medium',
            interval_s INTEGER, registered_at TEXT NOT NULL, last_start TEXT, last_ok TEXT,
            last_error_at TEXT, last_error TEXT, ticks INTEGER NOT NULL DEFAULT 0,
            failures INTEGER NOT NULL DEFAULT 0, last_result TEXT,
            enabled INTEGER NOT NULL DEFAULT 1)""")
        conn.execute("INSERT OR REPLACE INTO workers(name, label, interval_s, registered_at, "
                     "last_start, last_ok) VALUES('ledger','Ledger',1800,?,?,?)",
                     (last_ok.isoformat(), last_start.isoformat(), last_ok.isoformat()))


@pytest.mark.parametrize("start_ago,ok_ago,clear", [
    (timedelta(minutes=3), timedelta(minutes=2), True),     # just finished a pass
    (timedelta(seconds=10), timedelta(minutes=30), False),  # a pass is running
    (timedelta(minutes=27), timedelta(minutes=27), False),  # the next pass is minutes away
])
def test_apply_waits_for_a_quiet_moment_in_the_grading_loop(spine, tmp_path, start_ago,
                                                            ok_ago, clear):
    from src.core import ledger_restamp as rs
    _bad(spine, "AAL", 11.88)
    now = datetime.now()
    _heartbeat(_db(tmp_path), last_start=now - start_ago, last_ok=now - ok_ago)
    assert rs.grader_clear(_db(tmp_path), now)[0] is clear
    if not clear:
        plan = rs.build_plan(_db(tmp_path), prices())
        with pytest.raises(SystemExit, match="REFUSING"):
            rs.apply(_db(tmp_path), plan["plan_hash"], price_on=prices(), now=now)
        assert _row(spine, plan["plans"][0]["id"])["provider_symbol"] == "AAL.L"


def test_after_the_repair_the_grader_grades_on_the_us_listing(spine, tmp_path, monkeypatch,
                                                             ledger_with_bad_rows):
    from src.core import ledger_restamp as rs
    ids = ledger_with_bad_rows
    plan = rs.build_plan(_db(tmp_path), prices())
    rs.apply(_db(tmp_path), plan["plan_hash"], price_on=prices(), grader_stopped=True)
    asked = []

    def closes(symbol, on):
        asked.append(symbol)
        return {"BA": 190.0 if on == ASOF else 185.0}.get(symbol, CLOSES.get(symbol))
    monkeypatch.setattr(spine, "_price_on", closes)
    spine.resolve_due(now=GRADED_ON)
    ba = _row(spine, ids["ba"])
    assert ba["resolved"] == 1 and ba["correct"] == 1        # a bear call, and BA fell
    assert ba["actual_return"] == pytest.approx((185.0 - 190.0) / 190.0, abs=1e-4)
    assert "BA.L" not in asked


def test_the_command_line_dry_run(spine, tmp_path, monkeypatch, capsys, ledger_with_bad_rows):
    from src.core import ledger, ledger_restamp as rs
    monkeypatch.setattr(ledger, "_price_on", prices())
    out_csv = tmp_path / "plan.csv"
    assert rs.main(["--db", str(_db(tmp_path)), "--csv", str(out_csv)]) == 0
    text = capsys.readouterr().out
    assert "NO DATA WAS MODIFIED" in text and "plan hash" in text
    lines = out_csv.read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith("id,subject,created_at") and len(lines) == 3
    with pytest.raises(SystemExit, match="needs --plan-hash"):
        rs.main(["--db", str(_db(tmp_path)), "--apply"])


# ── the last lines of defence, inside the write transaction ────────────────

def _stamps(spine, ids) -> dict:
    return {k: _row(spine, ids[k])["provider_symbol"] for k in ("aal", "ba")}


def test_a_row_graded_after_the_plan_was_rebuilt_stops_the_write(spine, tmp_path, monkeypatch,
                                                                  ledger_with_bad_rows):
    """A grade lands between the hash check and BEGIN: the in-transaction
    fingerprint refuses, and nothing is written."""
    from src.core import ledger_restamp as rs
    ids = ledger_with_bad_rows
    plan = rs.build_plan(_db(tmp_path), prices())

    def grade_meanwhile(db, now=None):
        spine.grade(ids["aal"], correct=None,
                    note="GRADING_SKIPPED / IDENTITY_UNRESOLVED — t", announce=False)
        return True, "clear"
    monkeypatch.setattr(rs, "grader_clear", grade_meanwhile)
    monkeypatch.setattr(rs, "_grader_state", lambda conn, now=None: (True, "clear"))
    with pytest.raises(SystemExit, match="changed after the plan was built"):
        rs.apply(_db(tmp_path), plan["plan_hash"], price_on=prices())
    assert _stamps(spine, ids) == {"aal": "AAL.L", "ba": "BA.L"}


def test_a_grading_pass_that_starts_after_the_first_check_stops_the_write(
        spine, tmp_path, monkeypatch, ledger_with_bad_rows):
    """The heartbeat says a pass began after the early check: the check
    inside the transaction refuses."""
    from src.core import ledger_restamp as rs
    ids = ledger_with_bad_rows
    now = datetime.now()
    _heartbeat(_db(tmp_path), last_start=now - timedelta(seconds=5),
               last_ok=now - timedelta(minutes=10))
    plan = rs.build_plan(_db(tmp_path), prices())
    monkeypatch.setattr(rs, "grader_clear", lambda db, now=None: (True, "looked clear"))
    with pytest.raises(SystemExit, match="a grading pass is running"):
        rs.apply(_db(tmp_path), plan["plan_hash"], price_on=prices(), now=now)
    assert _stamps(spine, ids) == {"aal": "AAL.L", "ba": "BA.L"}


def test_a_failed_compare_and_set_rolls_back_every_earlier_write(spine, tmp_path, monkeypatch,
                                                                ledger_with_bad_rows):
    """Behind the hash and the fingerprint, each UPDATE still checks the row
    is as planned. If the last one is not, the earlier ones are rolled back."""
    from src.core import ledger_restamp as rs
    ids = ledger_with_bad_rows
    plan = rs.build_plan(_db(tmp_path), prices())
    last = [p for p in plan["plans"] if p["action"] in rs.WRITES][-1]["id"]
    with spine.connect() as conn:          # whichever row sorts last, pending or skipped
        conn.execute("UPDATE predictions SET correct=1 WHERE id=?", (last,))
    # Blind the two earlier checks: a stale plan and a fingerprint that always agrees.
    monkeypatch.setattr(rs, "build_plan", lambda db, price_on=None: plan)
    monkeypatch.setattr(rs, "_fingerprint", lambda rows: plan["fingerprint"])
    with pytest.raises(SystemExit, match="was not in its planned state"):
        rs.apply(_db(tmp_path), plan["plan_hash"], price_on=prices(), grader_stopped=True)
    first = next(k for k in ("aal", "ba") if ids[k] != last)
    was = next(p["old"]["provider_symbol"] for p in plan["plans"] if p["id"] == ids[first])
    assert _row(spine, ids[first])["provider_symbol"] == was


# ── undo ───────────────────────────────────────────────────────────────────

def test_revert_puts_back_exactly_what_the_apply_wrote(spine, tmp_path, monkeypatch,
                                                       ledger_with_bad_rows):
    from src.core import ledger_restamp as rs
    ids = ledger_with_bad_rows
    before = {k: _row(spine, ids[k]) for k in ids}
    plan = rs.build_plan(_db(tmp_path), prices())
    out = rs.apply(_db(tmp_path), plan["plan_hash"], price_on=prices(), grader_stopped=True)
    # Later writes the undo must keep: a new prediction, and a regrade of the
    # repaired BA row on the US listing.
    later = spine.record_prediction(subject="AAPL", claim="later", source="technical",
                                    direction="bull", horizon_days=5, price_at=150.0,
                                    announce=False)
    monkeypatch.setattr(spine, "_price_on",
                        lambda s, on: {"BA": 190.0 if on == ASOF else 185.0}.get(s))
    spine.resolve_due(now=GRADED_ON)
    assert _row(spine, ids["ba"])["correct"] == 1

    res = rs.revert(_db(tmp_path), Path(out["backup"]).parent, grader_stopped=True)
    assert (res["reverted"], res["not_reverted"], res["rows_not_as_backup"]) == (2, [], [])
    assert res["integrity_after"] == "ok"
    for k in ("aal", "ba", "london"):
        assert _row(spine, ids[k]) == before[k], k
    assert _row(spine, later)["claim"] == "later"


def test_revert_leaves_a_row_someone_changed_since(spine, tmp_path, ledger_with_bad_rows):
    from src.core import ledger_restamp as rs
    ids = ledger_with_bad_rows
    plan = rs.build_plan(_db(tmp_path), prices())
    out = rs.apply(_db(tmp_path), plan["plan_hash"], price_on=prices(), grader_stopped=True)
    with spine.connect() as conn:
        conn.execute("UPDATE predictions SET provider_symbol='AAL.X' WHERE id=?", (ids["aal"],))
    res = rs.revert(_db(tmp_path), Path(out["backup"]).parent, grader_stopped=True)
    assert res["reverted"] == 1 and res["not_reverted"] == [ids["aal"]]
    assert _row(spine, ids["aal"])["provider_symbol"] == "AAL.X"
    assert _row(spine, ids["ba"])["provider_symbol"] == "BA.L"


# ── where it runs ──────────────────────────────────────────────────────────

def test_creation_times_are_read_as_uk_time_whatever_the_host_zone(spine, tmp_path,
                                                                   monkeypatch):
    """The rows were written as naive UK-local times on the laptop; the cloud
    VM runs in UTC. The session state is derived at the UK time."""
    from src.core import ledger_restamp as rs
    from src.intelligence import provenance
    _bad(spine, "AAL", 11.88, created="2026-10-21T09:26:00")
    seen = []
    real = provenance.derive_market_context

    def spy(symbol, at):
        seen.append(at)
        return real(symbol, at)
    monkeypatch.setattr(provenance, "derive_market_context", spy)
    rs.build_plan(_db(tmp_path), prices())
    assert seen and seen[-1].utcoffset() == timedelta(hours=1)     # BST on 21 Oct
    assert seen[-1].hour == 9 and seen[-1].minute == 26


def test_revert_refuses_a_folder_from_an_apply_that_never_committed(spine, tmp_path,
                                                                    ledger_with_bad_rows):
    from src.core import ledger_restamp as rs
    plan = rs.build_plan(_db(tmp_path), prices())
    out = rs.apply(_db(tmp_path), plan["plan_hash"], price_on=prices(), grader_stopped=True)
    folder = Path(out["backup"]).parent
    (folder / "result.json").unlink()
    with pytest.raises(SystemExit, match="no committed apply"):
        rs.revert(_db(tmp_path), folder, grader_stopped=True)


@pytest.mark.parametrize("early", [True, False])
def test_revert_waits_for_a_quiet_moment_too(spine, tmp_path, monkeypatch, early,
                                            ledger_with_bad_rows):
    from src.core import ledger_restamp as rs
    ids = ledger_with_bad_rows
    plan = rs.build_plan(_db(tmp_path), prices())
    out = rs.apply(_db(tmp_path), plan["plan_hash"], price_on=prices(), grader_stopped=True)
    now = datetime.now()
    _heartbeat(_db(tmp_path), last_start=now - timedelta(seconds=5),
               last_ok=now - timedelta(minutes=10))
    if not early:          # the early check is fooled; the one inside the transaction is not
        monkeypatch.setattr(rs, "grader_clear", lambda db, now=None: (True, "looked clear"))
    with pytest.raises(SystemExit, match="a grading pass is running"):
        rs.revert(_db(tmp_path), Path(out["backup"]).parent, now=now)
    assert _stamps(spine, ids) == {"aal": "AAL", "ba": "BA"}
