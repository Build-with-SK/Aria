"""
tests/test_migration_freeze.py
==============================
The ledger is LIVE. A plan computed against it goes stale while you read it.

The prediction ledger grew from 279 to 339 rows during the audit that produced
`scripts/migrate_identity.py` — daemons write to it continuously. So a plan
built at 13:00 may describe a database that no longer exists at 13:05, and
applying it anyway would write identity decisions taken about rows that have
since changed while skipping rows that appeared in between.

FAIL CLOSED. Re-running a dry run costs seconds; writing the wrong identity onto
a historical prediction is permanent.

Also pinned here: the EXACT_PRICE_MATCH tiebreak that resolved the two MRK rows.
The 5% band could not distinguish "the recorded price IS this candidate's close"
from "this candidate is nearby", so it called a 0.00% match and a 2.91% match a
tie and refused both. That was the right instinct with the wrong resolution.
"""
from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "migrate_identity.py"


@pytest.fixture
def mod():
    spec = importlib.util.spec_from_file_location("migrate_identity", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    sys.modules["migrate_identity"] = m
    spec.loader.exec_module(m)
    return m


@pytest.fixture
def ledger(tmp_path, mod, monkeypatch):
    """A small stand-in ledger with the columns the plan reads."""
    db = tmp_path / "aria_core.db"
    with sqlite3.connect(str(db)) as conn:
        conn.execute("""
            CREATE TABLE predictions(
                id TEXT PRIMARY KEY, created_at TEXT, source TEXT, subject TEXT,
                price_at REAL, resolved INTEGER DEFAULT 0, correct INTEGER,
                provider_symbol TEXT, identity_basis TEXT)""")
        conn.execute("CREATE TABLE decisions(id TEXT PRIMARY KEY)")
        conn.executemany(
            "INSERT INTO predictions(id, created_at, source, subject, price_at, "
            "resolved) VALUES(?,?,?,?,?,?)",
            [("a", "2026-08-13T00:00:00", "v5", "AAPL", 200.0, 1),
             ("b", "2026-08-17T00:00:00", "v5", "MSFT", 400.0, 0)])
        conn.commit()
    monkeypatch.setattr(mod, "_db_path", lambda: db)
    return db


# ── the freeze ──────────────────────────────────────────────────────────────

def test_a_plan_carries_the_snapshot_it_was_built_against(mod, ledger):
    plan = mod.build_plan()
    assert plan["snapshot"]["rows"] == 2
    assert plan["snapshot"]["fingerprint"]
    assert plan["snapshot"]["taken_at"]
    assert plan["plan_hash"]


def test_the_same_ledger_fingerprints_the_same_way(mod, ledger):
    assert mod.snapshot()["fingerprint"] == mod.snapshot()["fingerprint"]


def test_a_new_prediction_changes_the_fingerprint(mod, ledger):
    before = mod.snapshot()["fingerprint"]
    with sqlite3.connect(str(ledger)) as conn:
        conn.execute("INSERT INTO predictions(id, created_at, source, subject, "
                     "price_at, resolved) VALUES('c','2026-08-20','v5','NVDA',100.0,0)")
        conn.commit()
    assert mod.snapshot()["fingerprint"] != before


def test_a_changed_reference_price_changes_the_fingerprint(mod, ledger):
    """price_at is an input to the plan, so moving it must invalidate it."""
    before = mod.snapshot()["fingerprint"]
    with sqlite3.connect(str(ledger)) as conn:
        conn.execute("UPDATE predictions SET price_at = 999.0 WHERE id='a'")
        conn.commit()
    assert mod.snapshot()["fingerprint"] != before


def test_grading_a_prediction_does_not_invalidate_the_plan(mod, ledger):
    """The fingerprint must cover what the plan READS and nothing else.

    A daemon filling in price_end or `correct` does not change any identity
    decision, and forcing a re-plan for it would make the migration
    impossible to land on a live system.
    """
    before = mod.snapshot()["fingerprint"]
    with sqlite3.connect(str(ledger)) as conn:
        conn.execute("UPDATE predictions SET resolved=1, correct=1 WHERE id='b'")
        conn.commit()
    assert mod.snapshot()["fingerprint"] == before


def test_apply_refuses_when_the_ledger_moved_underneath_the_plan(mod, ledger):
    """THE point of the freeze."""
    plan = mod.build_plan()
    with sqlite3.connect(str(ledger)) as conn:
        conn.execute("INSERT INTO predictions(id, created_at, source, subject, "
                     "price_at, resolved) VALUES('c','2026-08-20','v5','NVDA',100.0,0)")
        conn.commit()

    with pytest.raises(SystemExit) as e:
        mod.apply(plan)
    msg = str(e.value)
    assert "REFUSING" in msg
    assert "changed between the plan and the apply" in msg


def test_a_refused_apply_writes_nothing(mod, ledger):
    plan = mod.build_plan()
    with sqlite3.connect(str(ledger)) as conn:
        conn.execute("UPDATE predictions SET price_at = 1.0 WHERE id='a'")
        conn.commit()
    with pytest.raises(SystemExit):
        mod.apply(plan)
    with sqlite3.connect(str(ledger)) as conn:
        written = conn.execute(
            "SELECT COUNT(*) FROM predictions WHERE provider_symbol IS NOT NULL"
        ).fetchone()[0]
    assert written == 0, "a refused migration still wrote rows"


def test_the_plan_hash_changes_when_a_decision_changes(mod):
    a = mod.plan_hash([{"id": "1", "status": "UNAMBIGUOUS", "provider_symbol": "BA"}])
    b = mod.plan_hash([{"id": "1", "status": "UNAMBIGUOUS", "provider_symbol": "BA.L"}])
    assert a != b


def test_the_plan_hash_is_order_independent(mod):
    rows = [{"id": "1", "status": "UNAMBIGUOUS", "provider_symbol": "BA"},
            {"id": "2", "status": "UNAMBIGUOUS", "provider_symbol": "MSFT"}]
    assert mod.plan_hash(rows) == mod.plan_hash(list(reversed(rows)))


# ── the exact-match tiebreak ────────────────────────────────────────────────

def test_an_exact_match_beats_a_merely_close_one(mod, monkeypatch):
    """The two MRK rows, in miniature.

    Both candidates sit inside the 5% band, so the old plan called it ambiguous
    and refused. But 0.00% and 2.91% are not a tie: a price recorded to the cent
    by the subsystem that fetched it does not "approximately" match its source.
    """
    monkeypatch.setattr(mod, "_candidates", lambda s: ["MRK", "MRK.DE"])
    monkeypatch.setattr(mod, "_close_on",
                        lambda sym, day: 135.55 if sym == "MRK" else 139.50)
    monkeypatch.setattr(mod.idt, "describe", lambda s, **k: type(
        "I", (), {"ok": True, "provider_symbol": "MRK.DE", "candidates": [{}],
                  "reason": "x"})())

    got = mod.plan_row({"id": "x", "subject": "MRK", "source": "v5",
                        "price_at": 135.55, "created_at": "2026-08-13T00:00:00"})
    assert got["status"] == mod.EXACT_PRICE_MATCH
    assert got["provider_symbol"] == "MRK"
    assert "EXACTLY" in got["reason"]


def test_two_exact_matches_are_still_a_refusal(mod, monkeypatch):
    """The tiebreak must only break GENUINE ties, never manufacture a winner."""
    monkeypatch.setattr(mod, "_candidates", lambda s: ["MRK", "MRK.DE"])
    monkeypatch.setattr(mod, "_close_on", lambda sym, day: 135.55)
    monkeypatch.setattr(mod.idt, "describe", lambda s, **k: type(
        "I", (), {"ok": True, "provider_symbol": "MRK.DE", "candidates": [{}],
                  "reason": "x"})())

    got = mod.plan_row({"id": "x", "subject": "MRK", "source": "v5",
                        "price_at": 135.55, "created_at": "2026-08-13T00:00:00"})
    assert got["status"] == mod.UNRESOLVED
    assert got["provider_symbol"] is None


def test_two_near_matches_with_no_exact_one_are_still_a_refusal(mod, monkeypatch):
    monkeypatch.setattr(mod, "_candidates", lambda s: ["MRK", "MRK.DE"])
    monkeypatch.setattr(mod, "_close_on",
                        lambda sym, day: 137.0 if sym == "MRK" else 138.0)
    monkeypatch.setattr(mod.idt, "describe", lambda s, **k: type(
        "I", (), {"ok": True, "provider_symbol": "MRK.DE", "candidates": [{}],
                  "reason": "x"})())

    got = mod.plan_row({"id": "x", "subject": "MRK", "source": "v5",
                        "price_at": 135.55, "created_at": "2026-08-13T00:00:00"})
    assert got["status"] == mod.UNRESOLVED


def test_an_unresolvable_row_is_never_filled_with_a_guess(mod, monkeypatch):
    monkeypatch.setattr(mod, "_candidates", lambda s: ["MRK", "MRK.DE"])
    monkeypatch.setattr(mod, "_close_on", lambda sym, day: None)
    monkeypatch.setattr(mod.idt, "describe", lambda s, **k: type(
        "I", (), {"ok": True, "provider_symbol": "MRK.DE", "candidates": [{}],
                  "reason": "x"})())

    got = mod.plan_row({"id": "x", "subject": "MRK", "source": "v5",
                        "price_at": 135.55, "created_at": "2026-08-13T00:00:00"})
    assert got["status"] == mod.UNRESOLVED
    assert got["provider_symbol"] is None


def test_the_source_resolver_is_corroboration_not_the_basis(mod):
    """It must never decide on its own — the price evidence has to agree."""
    got = mod._source_resolves_to("MRK", "v5")
    assert got in (None, "MRK"), got
    # An unknown source contributes nothing rather than inventing a mapping.
    assert mod._source_resolves_to("MRK", "some_other_subsystem") is None


# ── the production ledger, after the fact ───────────────────────────────────

def test_the_production_ledger_carries_an_identity_for_every_prediction():
    db = ROOT / "data" / "aria_core.db"
    if not db.exists():
        pytest.skip("no production ledger present")
    # READ ONLY. tests/conftest.py refuses any writable connection to the
    # owner's live record, and it is right to — this assertion only needs to
    # look, so it opens the file in the one mode the guard permits.
    uri = f"file:{db.as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(predictions)")]
        if "provider_symbol" not in cols:
            pytest.skip("ledger not migrated in this checkout")
        total, identified = conn.execute(
            "SELECT COUNT(*), COUNT(provider_symbol) FROM predictions").fetchone()
    assert total > 0
    # NOT `total == identified`. The migration fails closed on genuinely
    # undecidable rows, and refusing is the correct behaviour — one MRK
    # prediction whose recorded price matches neither candidate is a refusal,
    # not a gap. What must not exist is a PILE of them; that is covered by
    # test_the_live_ledger_has_not_accumulated_unidentified_predictions below.
    missing = total - identified
    assert missing <= 5, (
        f"{missing} of {total} predictions carry no provider identity — a few "
        f"refusals is the system working, a pile is the write path failing")


def test_the_migration_only_ever_added_columns():
    """The historical record is IMMUTABLE. Only the two additive columns may
    differ from what was there before."""
    db = ROOT / "data" / "aria_core.db"
    if not db.exists():
        pytest.skip("no production ledger present")
    backups = sorted((ROOT / "data" / "backups").glob("identity-migration-*"))
    if not backups:
        pytest.skip("no migration backup to compare against")
    import json
    latest = backups[-1]
    before_f, after_f = latest / "integrity_before.json", latest / "integrity_after.json"
    if not (before_f.exists() and after_f.exists()):
        pytest.skip("backup has no integrity snapshots")
    before = json.loads(before_f.read_text(encoding="utf-8"))
    after = json.loads(after_f.read_text(encoding="utf-8"))
    for key in ("predictions", "graded", "scored", "decisions",
                "subject_digest", "price_digest", "outcome_digest"):
        assert before[key] == after[key], (
            f"the migration changed {key} — it is only allowed to ADD identity")


# ── identity must not decay ────────────────────────────────────────────────
#
# The migration is a BACKFILL. It fixed history and then stopped, so every
# prediction written afterwards carried no identity at all — 88 of them
# accumulated in one week, and the only reason anyone noticed was a docs test
# comparing a README figure against the live ledger.
#
# A guarantee that has to be re-established by hand is not a guarantee. These
# pin the write path instead.

@pytest.fixture
def fresh_ledger(tmp_path, monkeypatch):
    """A throwaway ledger. ARIA_CORE_DB, not a patched attribute — `_db_path()`
    reads the environment first, so patching the module is silently ineffective
    once conftest has set the variable session-wide."""
    monkeypatch.setenv("ARIA_CORE_DB", str(tmp_path / "spine.db"))
    from src.core import bus as bus_mod, ledger as ledger_mod
    monkeypatch.setattr(bus_mod, "_initialised", False)
    monkeypatch.setattr(bus_mod, "_subscribers", [])
    monkeypatch.setattr(ledger_mod, "_ready", False)
    return ledger_mod


def test_a_new_prediction_is_stamped_with_its_identity(fresh_ledger):
    ledger = fresh_ledger
    pid = ledger.record_prediction(
        subject="AAPL", claim="bull over 21 days", source="test",
        direction="bull", confidence=0.6, price_at=200.0, announce=False)
    assert pid, "the prediction was not recorded at all"

    with ledger.connect() as conn:
        row = dict(conn.execute(
            "SELECT subject, provider_symbol, identity_basis FROM predictions "
            "WHERE id = ?", (pid,)).fetchone())

    assert row["subject"] == "AAPL"
    assert row["provider_symbol"], (
        "a prediction was written with no provider identity — the migration "
        "backfills history, the write path is what keeps it true")
    assert row["identity_basis"], "identity was stamped with no stated basis"


def test_an_unresolvable_subject_still_records_the_claim(fresh_ledger):
    """Fail OPEN here, deliberately.

    Refusing to write an unidentifiable prediction would lose the falsifiable
    claim entirely, which is strictly worse than storing it with a null identity
    that the migration can resolve later on price evidence. Failing closed is
    right when a WRONG answer would be written; here the alternative is no
    answer at all.
    """
    ledger = fresh_ledger
    pid = ledger.record_prediction(
        subject="ZZZZ-NOT-A-REAL-INSTRUMENT", claim="bull", source="test",
        direction="bull", confidence=0.6, price_at=1.0, announce=False)
    assert pid, "an unidentifiable subject stopped the claim being recorded"

    with ledger.connect() as conn:
        row = dict(conn.execute(
            "SELECT provider_symbol FROM predictions WHERE id = ?", (pid,)).fetchone())
    assert row["provider_symbol"] is None, (
        "an identity was invented for a subject nothing could resolve")


def test_the_live_ledger_has_not_accumulated_unidentified_predictions():
    """The canary. If this goes red, the write path has stopped stamping and
    identity is decaying again at roughly a hundred rows a week."""
    db = ROOT / "data" / "aria_core.db"
    if not db.exists():
        pytest.skip("no production ledger present")
    uri = f"file:{db.as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(predictions)")]
        if "provider_symbol" not in cols:
            pytest.skip("ledger not migrated in this checkout")
        total, identified = conn.execute(
            "SELECT COUNT(*), COUNT(provider_symbol) FROM predictions").fetchone()
        unresolved = conn.execute(
            "SELECT COUNT(*) FROM predictions WHERE provider_symbol IS NULL "
            "AND identity_basis IS NULL").fetchone()[0]

    # A small number of genuinely undecidable rows is correct — the migration
    # fails closed rather than guessing. What must not happen is a growing pile.
    assert unresolved <= 5, (
        f"{unresolved} of {total} predictions carry no identity. A handful of "
        f"refusals is the system working; a pile means the write path stopped "
        f"stamping and only a manual migration is holding the line.")
