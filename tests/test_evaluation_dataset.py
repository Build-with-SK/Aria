"""
tests/test_evaluation_dataset.py
================================
The canonical evaluation dataset: every graded row is either included or
excluded with a reason, nothing from after a call is used, duplicates count
once, and a defective measurement is replaced only by a same-series regrade.

Both defects regraded here were found in the real ledger on 2026-10-08:
318 technical and 9 V5 grades made ON the horizon date before its close
existed, and every pre-2026-10-06 grade measured from a raw recorded price to
an adjusted close.
"""
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from intel_helpers import row  # noqa: E402
from src.intelligence import dataset as D  # noqa: E402


def _reasons(ds):
    return Counter(r for e in ds.excluded for r in e["reasons"])


def test_graded_is_always_included_plus_excluded():
    rows = [row(i) for i in range(5)] + [row(10, correct=None), row(11, resolved=False)]
    ds = D.build(rows)
    s = ds.summary()["by_model"]["technical"]
    assert s["graded"] == s["included"] + s["excluded"] == 6
    assert s["pending"] == 1 and s["recorded"] == 7


def test_an_undecidable_grade_is_excluded_not_counted_wrong():
    ds = D.build([row(1, correct=None)])
    assert _reasons(ds) == {"UNRESOLVABLE": 1}


def test_a_grade_before_the_call_is_excluded():
    ds = D.build([row(1, created="2026-08-10T22:00:00", resolve_after="2026-08-24",
                      resolved_at="2026-08-09T10:00:00")])
    assert "CREATED_AFTER_RESOLUTION" in _reasons(ds)


def test_a_grade_on_the_horizon_date_before_its_close_is_excluded():
    """resolved 11:01 on the horizon date: that day's close did not exist."""
    r = row(1, created="2026-08-11T21:30:05", resolve_after="2026-08-25",
            resolved_at="2026-08-25T11:01:01")
    assert _reasons(D.build([r])) == {"GRADED_BEFORE_HORIZON": 1}


def test_a_horizon_that_does_not_follow_from_the_call_is_excluded():
    r = row(1, created="2026-08-03T22:00:00", horizon=10, resolve_after="2026-09-30",
            resolved_at="2026-10-01T10:00:00")
    assert "HORIZON_INCONSISTENT" in _reasons(D.build([r]))


def test_data_dated_after_the_call_is_excluded():
    r = row(1, created="2026-08-03T22:00:00", data_as_of="2026-08-05")
    assert _reasons(D.build([r])) == {"DATA_AFTER_CREATION": 1}


def test_a_raw_to_adjusted_return_on_an_equity_is_excluded():
    r = row(1, note="graded on the close of 2026-08-17")          # the pre-2026-10-06 grader
    assert _reasons(D.build([r])) == {"MIXED_PRICE_BASIS": 1}


def test_crypto_has_no_distributions_so_its_recorded_reference_is_one_basis():
    r = row(1, subject="BTC-USD", note="graded on the close of 2026-08-17")
    ds = D.build([r])
    assert not ds.excluded and ds.rows[0]["asset_class"] == "crypto"


def test_a_regrade_cures_measurement_defects_and_counts_changed_outcomes():
    bad = row(1, created="2026-08-11T21:30:05", resolve_after="2026-08-25",
              resolved_at="2026-08-25T11:01:01", note="graded on the close of 2026-08-25",
              correct=1)
    ds = D.build([bad], regrades={"p00001": {"correct": False, "actual_return": -0.004}})
    assert not ds.excluded
    r = ds.rows[0]
    assert r["outcome"] == 0 and r["grade_source"] == "regraded_same_series"
    assert set(r["cured_defects"]) == {"GRADED_BEFORE_HORIZON", "MIXED_PRICE_BASIS"}
    assert ds.regrade == {"regraded": 1, "outcome_changed": 1}


def test_a_regrade_never_overrides_a_valid_grade_or_cures_a_claim_defect():
    good = row(1, correct=1)
    lookahead = row(2, data_as_of="2026-09-30", note="graded on the close of 2026-08-17")
    ds = D.build([good, lookahead],
                 regrades={"p00001": {"correct": False}, "p00002": {"correct": True}})
    assert [r["outcome"] for r in ds.rows] == [1]
    assert "DATA_AFTER_CREATION" in _reasons(ds)


def test_the_same_event_counts_once_and_the_first_assertion_is_kept():
    a = row(1, created="2026-08-03T22:00:00")
    b = row(2, created="2026-08-03T22:05:00")                    # same info date, same call
    ds = D.build([b, a])
    assert [r["prediction_id"] for r in ds.rows] == ["p00001"]
    assert _reasons(ds) == {"DUPLICATE_EVENT": 1}


def test_repeated_desk_debates_of_one_move_collapse():
    rows = [row(i, source="desk", horizon=1, kind="mapped", p_correct=None,
                note="graded by the desk at the time; verdict was NO_TRADE.",
                created=f"2026-07-2{i}T10:00:00", information_session=None,
                price_at=3e-06, price_end=3e-06, subject="BONK-USD") for i in range(1, 4)]
    for r in rows:
        r["price_at"], r["price_end"] = 3e-06, 3e-06
    ds = D.build(rows)
    assert len(ds.rows) == 1 and _reasons(ds) == {"DUPLICATE_EVENT": 2}


def test_as_of_hides_later_calls_and_later_grades():
    early = row(1, created="2026-08-03T22:00:00")                # graded 2026-08-18
    late_call = row(2, created="2026-09-10T22:00:00")
    T = datetime(2026, 8, 10, 12, tzinfo=timezone.utc)
    ds = D.build([early, late_call], as_of=T)
    assert ds.rows == [] and ds.pending["technical"] == 1 and ds.total["technical"] == 1


def test_mutating_an_outcome_graded_after_as_of_changes_nothing_at_as_of():
    rows = [row(i, created=f"2026-08-0{i}T22:00:00") for i in range(3, 8)]
    T = datetime(2026, 8, 30, tzinfo=timezone.utc)
    before = D.build(rows, as_of=T).dataset_hash
    mutated = [dict(r, correct=1 - r["correct"]) if r["resolved_at"] > "2026-08-30" else r
               for r in rows]
    assert D.build(mutated, as_of=T).dataset_hash == before


def test_probability_exclusions_are_counted_by_reason():
    rows = [row(1, source="v5", kind="stated", p_correct=0.6, horizon=45),
            row(2, source="v5", kind="stated", p_correct=None, direction="neutral", horizon=45,
                subject="MSFT"),
            row(3, source="v5", kind=None, p_correct=None, horizon=45, subject="IBM")]
    ds = D.build(rows)
    assert [r["p"] for r in ds.rows if r["prediction_id"] == "p00001"] == [0.6]
    assert ds.probability_exclusions["v5"] == {"NEUTRAL_CALL_STATES_NO_PROBABILITY": 1,
                                               "PROBABILITY_KIND_UNRECORDED": 1}


def test_a_macro_label_from_a_snapshot_after_the_call_is_lookahead():
    r = row(1, created="2026-09-02T20:00:00",
            regime_basis="world_snapshot 2026-09-02T20:24:47 (taken before the call)")
    assert D.macro_regime(r) == (None, "LOOKAHEAD")
    ok = row(2, created="2026-09-02T21:00:00",
             regime_basis="world_snapshot 2026-09-02T20:24:47 (taken before the call)")
    assert D.macro_regime(ok)[1] == "VERIFIED_PIT"
    assert D.macro_regime(row(3, regime_at=None))[1] == "ABSENT"


def test_price_basis_reads_the_grade_note():
    assert D.price_basis(row(1)) == D.SAME_SERIES
    assert D.price_basis(row(1, note="graded by the desk at the time; x")) == D.SAME_SERIES
    assert D.price_basis(row(1, note="next-close basis on one adjusted series: …")) == D.SAME_SERIES
    assert D.price_basis(row(1, note="grade carried over from the V5 learning loop"),
                         "series close 2026-08-01") == D.SAME_SERIES
    assert D.price_basis(row(1, note="grade carried over from the V5 learning loop"),
                         "recorded price (intraday or no session)") == D.RECORDED_REFERENCE


def test_the_dataset_hash_is_stable_and_outcome_sensitive():
    rows = [row(i, created=f"2026-08-0{i}T22:00:00") for i in range(3, 7)]
    h = D.build(rows).dataset_hash
    assert D.build(list(reversed(rows))).dataset_hash == h
    assert D.build([dict(rows[0], correct=0)] + rows[1:]).dataset_hash != h


@pytest.mark.parametrize("h,bucket", [(1, "1D"), (5, "1W"), (10, "1M"), (45, "3M"),
                                      (252, ">3M"), (None, "UNKNOWN")])
def test_horizon_buckets(h, bucket):
    assert D.horizon_bucket(h) == bucket


def test_ledger_rows_never_migrates_the_database(tmp_path, monkeypatch):
    """Evaluating the record must never be what alters it."""
    import sqlite3
    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE predictions (id TEXT PRIMARY KEY, source TEXT)")
    conn.execute("INSERT INTO predictions VALUES ('a','v5')")
    conn.commit()
    conn.close()
    monkeypatch.setenv("ARIA_CORE_DB", str(db))
    assert D.ledger_rows() == [{"id": "a", "source": "v5"}]
    cols = [r[1] for r in sqlite3.connect(db).execute("PRAGMA table_info(predictions)")]
    assert cols == ["id", "source"]
