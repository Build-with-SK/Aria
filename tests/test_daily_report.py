"""
tests/test_daily_report.py
==========================
A daily report that can be overwritten is not a record, it is a cache.

WHAT WAS WRONG
--------------
`data/daily_report.json` was one file that every run replaced. So there was
exactly one report; its date said 2026-05-31 while the UI rendered it under a
heading implying today; and a day with no run silently showed the previous
day's conclusions as current.

WHAT IS PINNED HERE
-------------------
  * date IS the identity — one file per day
  * a report is never silently overwritten
  * superseding ARCHIVES the previous version rather than destroying it
  * a missing day answers NOT_GENERATED and STAYS saying it — it never falls
    back to the most recent report
  * the date is also the path, so it must reject anything that is not one
"""
from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from src.report import daily


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """Never touch the real report store."""
    monkeypatch.setattr(daily, "REPORTS", tmp_path / "reports")
    monkeypatch.setattr(daily, "ARCHIVE", tmp_path / "reports" / "superseded")
    yield


def _stub(monkeypatch, regime="Expansion (Goldilocks)"):
    """Keep `build()` off the network and off real subsystem state."""
    monkeypatch.setattr(daily, "_macro_section", lambda: {
        "regime": regime, "regime_meaning": "m", "regime_confidence": 1.0,
        "regime_coverage": 1.0, "regime_evidence": [], "regime_caveats": [],
        "would_flip_to": [], "as_of": "2026-08-29", "stale": False,
        "vix": {"value": 15.0, "status": "OBSERVED", "label": "VIX"},
        "cpi_yoy": {}, "spread_10y2y": {}, "unemployment": {},
        "dxy": None, "treasury_2y": None, "treasury_10y": None,
        "macro_score": None})
    monkeypatch.setattr(daily, "_market_section", lambda w: {
        "breadth": {"bullish": 1, "bearish": 2, "tracked": 3},
        "volatility": {"vix": 15.0}, "top_bullish": [], "top_bearish": [],
        "signals_note": ""})
    monkeypatch.setattr(daily, "_news_section", lambda hours=24: {"items": []})
    monkeypatch.setattr(daily, "_record_section", lambda w: {
        "stats": {}, "recent_resolved": [], "calibration": {},
        "limitation": "x"})


# ── date is identity ────────────────────────────────────────────────────────

def test_each_day_gets_its_own_file(monkeypatch):
    _stub(monkeypatch)
    a = daily.generate("2026-08-27")
    b = daily.generate("2026-08-28")
    assert a["date"] == "2026-08-27" and b["date"] == "2026-08-28"
    assert (daily.REPORTS / "2026-08-27.json").exists()
    assert (daily.REPORTS / "2026-08-28.json").exists()


def test_a_report_is_never_silently_overwritten(monkeypatch):
    _stub(monkeypatch)
    daily.generate("2026-08-27")
    with pytest.raises(FileExistsError):
        daily.generate("2026-08-27")


def test_superseding_archives_the_previous_version(monkeypatch):
    """Replacing a day must not destroy what was said that day."""
    _stub(monkeypatch, regime="Expansion (Goldilocks)")
    first = daily.generate("2026-08-27")
    _stub(monkeypatch, regime="Recession Risk")
    second = daily.generate("2026-08-27", supersede=True)

    assert second["market_regime"] == "Recession Risk"
    assert second["superseded"]["previous_generated_at"] == first["generated_at"]

    archived = list(daily.ARCHIVE.glob("2026-08-27.*.json"))
    assert len(archived) == 1
    old = json.loads(archived[0].read_text(encoding="utf-8"))
    assert old["market_regime"] == "Expansion (Goldilocks)", (
        "the superseded report was rewritten instead of preserved")


def test_yesterdays_report_is_never_shown_as_todays(monkeypatch):
    """THE failure this module exists to make impossible."""
    _stub(monkeypatch)
    daily.generate("2026-08-27")

    got = daily.get("2026-08-28")
    assert got["status"] == daily.NOT_GENERATED
    assert got["date"] == "2026-08-28"
    assert "executive_summary" not in got, (
        "a missing day returned report CONTENT — it fell back to another day")
    # The most recent one is offered as a LINK, never as a substitute.
    assert got["most_recent_available"] == "2026-08-27"


def test_a_missing_day_keeps_saying_it_is_missing(monkeypatch):
    _stub(monkeypatch)
    for _ in range(3):
        assert daily.get("2030-01-01")["status"] == daily.NOT_GENERATED


def test_history_lists_every_day_newest_first(monkeypatch):
    _stub(monkeypatch)
    for d in ("2026-08-25", "2026-08-27", "2026-08-26"):
        daily.generate(d)
    dates = [r["date"] for r in daily.history()]
    assert dates == ["2026-08-27", "2026-08-26", "2026-08-25"]


def test_ensure_today_is_idempotent(monkeypatch):
    _stub(monkeypatch)
    first = daily.ensure_today()
    second = daily.ensure_today()
    assert first["generated_at"] == second["generated_at"], (
        "ensure_today regenerated an existing report")


# ── the date is the path ────────────────────────────────────────────────────

@pytest.mark.parametrize("bad", [
    "../../etc/passwd", "2026-13-45", "today", "2026-08-27/../x", "..",
    "2026-08-27.json", "2026/08/27",
])
def test_a_non_date_is_refused(bad):
    """The date is the filename, so this is the traversal guard too."""
    with pytest.raises(ValueError):
        daily.get(bad)
    with pytest.raises(ValueError):
        daily.generate(bad)


@pytest.mark.parametrize("empty", [None, ""])
def test_an_unspecified_date_means_today(empty, monkeypatch):
    """Not a traversal case — `?date=` with nothing after it is the same
    request as omitting it, and both mean today."""
    _stub(monkeypatch)
    assert daily.get(empty)["date"] == daily.today_key()


# ── content ─────────────────────────────────────────────────────────────────

def test_the_report_carries_every_required_section(monkeypatch):
    _stub(monkeypatch)
    r = daily.generate("2026-08-27")
    for section in ("executive_summary", "macro", "market", "news",
                    "assessment", "track_record", "blind_spots"):
        assert section in r, f"missing section: {section}"
    for key in ("what_matters", "what_changed", "watching",
                "what_would_invalidate"):
        assert key in r["assessment"]


def test_an_empty_news_section_is_reported_not_filled(monkeypatch):
    """A quiet day is accurate. An invented headline is worthless every day."""
    _stub(monkeypatch)
    r = daily.generate("2026-08-27")
    assert r["news"]["items"] == []
    assert any("absence of observation" in b for b in r["blind_spots"])


def test_the_legacy_single_file_is_imported_under_its_own_date(
        monkeypatch, tmp_path):
    """The one surviving pre-dated-store report must keep ITS date.

    Filing it under today would be exactly the lie the old design told.
    """
    root = tmp_path / "root"
    (root / "data").mkdir(parents=True)
    (root / "data" / "daily_report.json").write_text(json.dumps(
        {"date": "2026-05-31", "market_regime": "Mildly Bullish"}),
        encoding="utf-8")
    monkeypatch.setattr(daily, "ROOT", root)

    got = daily.backfill_from_legacy()
    assert got["date"] == "2026-05-31" != daily.today_key()
    assert got["imported_from"] == "data/daily_report.json"
    assert (daily.REPORTS / "2026-05-31.json").exists()


def test_the_legacy_import_never_clobbers_a_real_report(monkeypatch, tmp_path):
    _stub(monkeypatch)
    daily.generate("2026-05-31")
    root = tmp_path / "root"
    (root / "data").mkdir(parents=True)
    (root / "data" / "daily_report.json").write_text(json.dumps(
        {"date": "2026-05-31", "market_regime": "Overwritten"}), encoding="utf-8")
    monkeypatch.setattr(daily, "ROOT", root)

    assert daily.backfill_from_legacy() is None
    kept = json.loads((daily.REPORTS / "2026-05-31.json").read_text(encoding="utf-8"))
    assert kept["market_regime"] != "Overwritten"


def test_a_new_report_is_produced_each_day(monkeypatch):
    """Consecutive days are independent documents, not one mutated one."""
    _stub(monkeypatch)
    today = date.today()
    days = [(today - timedelta(days=n)).isoformat() for n in (2, 1, 0)]
    for d in days:
        daily.generate(d)
    stored = {r["date"] for r in daily.history()}
    assert stored == set(days)
    for d in days:
        assert daily.get(d)["date"] == d


# ── calibration must actually be present, and must carry its interval ───────

def test_the_calibration_section_is_populated_not_silently_empty():
    """A `hasattr` guard around a function that never existed made this section
    permanently `{}` while the report looked complete.

    This calls the real builder against the real ledger, so a renamed or
    removed calibration API fails here instead of quietly blanking the section.
    """
    from src.core import calibration as C
    section = daily._record_section({})
    calib = section["calibration"]
    assert calib, "the calibration section is empty"
    assert "error" not in calib, calib.get("error")
    assert calib.get("headline"), "no calibration headline"
    for key in ("distinct_events", "overall"):
        assert key in calib, f"calibration is missing {key}"


def test_a_hit_rate_is_never_quoted_without_its_sample_and_interval():
    """The invariant: either the headline declines to measure, or it carries
    BOTH the sample size and the interval.

    A bare percentage is the failure mode — "49.7% correct" reads as a measured
    edge, while "49.7% over 171 events, 95% CI 42.3-57.1%, indistinguishable
    from chance" reads as what it is. On an empty ledger the honest answer is
    "not measurable", and that must also be allowed.
    """
    from src.core import calibration as C
    line = C.summary_line()
    assert line, "calibration produced no summary at all"
    lowered = line.lower()

    declines = ("not measurable" in lowered or "no resolved" in lowered)
    if declines:
        return

    assert "event" in lowered or "prediction" in lowered, (
        f"a rate was quoted with no sample size: {line}")
    assert "ci" in lowered or "interval" in lowered, (
        f"a rate was quoted with no interval: {line}")


def test_the_live_calibration_declines_to_claim_skill_it_cannot_show():
    """Against the REAL ledger. 171 events at 49.7% is not an edge, and the
    system must say so rather than presenting the number bare."""
    from src.core import calibration as C
    try:
        line = C.summary_line()
    except Exception as e:
        pytest.skip(f"calibration unavailable: {e}")
    lowered = line.lower()
    if "not measurable" in lowered or "no resolved" in lowered:
        return
    # If it IS reporting a rate, it must qualify it.
    assert any(w in lowered for w in
               ("indistinguishable", "chance", "ci", "interval", "insufficient")), (
        f"a hit rate was reported with no qualification: {line}")


def test_track_record_carries_its_own_limitation():
    section = daily._record_section({})
    assert section["limitation"], "the track record makes no statement of limits"
