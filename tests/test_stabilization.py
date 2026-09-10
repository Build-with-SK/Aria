"""
tests/test_stabilization.py
===========================
Regression tests for the stabilization pass — items A–J of the brief.

Each one pins a property that was found broken, or that a fix could silently
undo later. Where a test asserts a REFUSAL, that refusal is the feature: this
system's central claim is that it does not guess, and a guess is only
detectable by testing the case where guessing would be easy.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from src.core import identity as idt
from src.macro import feeds


# ── C / D: tests cannot reach production state ──────────────────────────────

def test_the_data_root_is_redirected_away_from_production(tmp_data_dir):
    """C — a test must not read the owner's live records.

    The macro-cache leak was found when a test asserting "a dead BLS yields
    None" started returning a real cached 3.365: it had begun reading
    data/cache/ and was passing for the wrong reason.
    """
    real = (Path(__file__).resolve().parent.parent / "data").resolve()
    assert tmp_data_dir.resolve() != real
    assert str(real) not in str(tmp_data_dir.resolve())


def test_the_event_log_under_test_is_not_the_real_one(tmp_data_dir):
    """D — the activity stream must never contain test fixtures.

    It once showed rows reading "Nvidia cuts guidance" and "story 2", which is
    precisely the fabricated liveness the stream exists to make impossible.
    """
    from src.core import bus
    used = bus._db_path().resolve()
    real = (Path(__file__).resolve().parent.parent / "data" / "aria_core.db").resolve()
    assert used != real
    bus.publish("SYSTEM_NOTE", "fixture event", source="test")
    assert any(e["summary"] == "fixture event" for e in bus.recent(limit=5))
    assert not str(used).startswith(str(real.parent))


def test_writing_into_production_data_is_refused():
    """The guard itself. If this stops raising, every other isolation claim in
    this file becomes unverifiable."""
    real_data = (Path(__file__).resolve().parent.parent / "data").resolve()
    with pytest.raises(AssertionError, match="PRODUCTION"):
        (real_data / "should-never-exist.json").write_text("{}", encoding="utf-8")


# ── B: cache-first, no request when a valid cache exists ────────────────────

@pytest.fixture
def cache(tmp_path, monkeypatch):
    path = tmp_path / "feeds.json"
    monkeypatch.setattr(feeds, "_cache_path", lambda: path)
    return path


def test_a_valid_cache_makes_no_network_request(cache, monkeypatch):
    """B — a restart is not a reason to spend quota.

    The BLS keyless API allows ~25 requests/IP/day. Re-fetching a MONTHLY
    statistic every few hours is how that ran out, taking CPI and unemployment
    to None in production.
    """
    feeds._cache_write("cpi_yoy", {
        "series": "CPI-U YoY", "value": 3.365, "unit": "percent",
        "observation_date": "2026-07-31", "release_date": None,
        "retrieved_at": "2026-08-24T17:03:07", "source": "BLS", "url": "u",
        "error": None, "frequency": "monthly"})

    calls = []
    monkeypatch.setattr(feeds, "_get", lambda url: calls.append(url))

    got = feeds.cached_fetch("cpi_yoy", lambda: pytest.fail("fetcher was called"),
                             frequency="monthly")
    assert calls == [], "a valid cache must not trigger a request"
    assert got.value == 3.365
    assert got.last_attempt_status == feeds.FETCH_SKIPPED
    assert got.from_cache is True


def test_a_monthly_series_is_not_refetched_on_a_daily_ttl(cache):
    """Publication frequency drives the TTL. A monthly number cannot change
    between this morning and this afternoon, so asking again is pure waste."""
    assert feeds.FREQUENCY["monthly"]["refetch_after_hours"] >= 24
    assert feeds.FREQUENCY["daily"]["refetch_after_hours"] <= 12
    assert (feeds.FREQUENCY["monthly"]["stale_after_days"]
            > feeds.FREQUENCY["daily"]["stale_after_days"])


def test_a_failed_refresh_does_not_null_a_valid_observation(cache, monkeypatch):
    """A — the headline requirement of §2."""
    feeds._cache_write("unemployment_rate", {
        "series": "U-3", "value": 4.2, "unit": "percent",
        "observation_date": "2026-07-31", "release_date": None,
        "retrieved_at": "2026-08-24T17:03:07", "source": "BLS", "url": "u",
        "error": None, "frequency": "monthly"})
    monkeypatch.setitem(feeds.FREQUENCY["monthly"], "refetch_after_hours", 0)

    def refused():
        return feeds._fail("unemployment_rate", "BLS", "u", "percent",
                           "REQUEST_NOT_PROCESSED")

    got = feeds.cached_fetch("unemployment_rate", refused, frequency="monthly")
    assert got.value == 4.2, "a valid observation was destroyed by a failed refresh"
    assert got.observation_date == "2026-07-31", "the observation was re-dated"
    assert got.last_attempt_status == feeds.FETCH_FAILED
    assert got.last_attempt_error


def test_the_five_freshness_states_are_distinguishable():
    """§2 asks for five states; "value is None" collapses three of them.

    The dates are computed RELATIVE to today. They used to be literals, and a
    literal ages: `2026-08-21` was inside the 5-day daily window when it was
    written and outside it six days later, so the suite went red on a calendar
    change rather than on a behaviour change. What is being asserted is that
    classify() separates recent from ancient — not that any particular date is
    recent.
    """
    recent = (date.today() - timedelta(days=1)).isoformat()
    fresh = feeds.Observation(
        series="s", value=1.0, unit="percent", observation_date=recent,
        release_date=None, retrieved_at="2026-08-24T00:00:00", source="x", url="u",
        frequency="daily")
    assert fresh.classify() == feeds.VALID

    old = feeds.Observation(
        series="s", value=1.0, unit="percent", observation_date="2020-01-01",
        release_date=None, retrieved_at="2020-01-01T00:00:00", source="x", url="u",
        frequency="daily")
    assert old.classify() == feeds.STALE, "an ancient daily reading is not current"

    absent = feeds.Observation(
        series="s", value=None, unit="percent", observation_date=None,
        release_date=None, retrieved_at="2026-08-24T00:00:00", source="x", url="u")
    assert absent.classify() == feeds.MISSING

    down = feeds.Observation(
        series="s", value=None, unit="percent", observation_date=None,
        release_date=None, retrieved_at="2026-08-24T00:00:00", source="x", url="u",
        error="connection refused")
    assert down.classify() == feeds.SOURCE_UNAVAILABLE


# ── H: FRED stays optional ──────────────────────────────────────────────────

def test_fred_is_optional_and_not_retried(monkeypatch):
    """H — FRED_API_KEY is empty and the keyless host times out from here.
    That must degrade one field, never block the macro engine, and never turn
    into a per-cycle retry against a dead endpoint."""
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    assert feeds.fred_available() is False

    calls = []
    monkeypatch.setattr(feeds, "_get", lambda url: calls.append(url))
    obs = feeds.fred_series("FEDFUNDS")
    assert obs.value is None
    assert "FRED_API_KEY" in (obs.error or "")
    assert calls == [], "a missing key must short-circuit before the network"


def test_the_authoritative_fallback_covers_what_fred_would_have_given(cache, monkeypatch):
    """Fed funds, CPI, unemployment and the curve all arrive without FRED."""
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    monkeypatch.setattr(feeds, "_get", lambda url: (_ for _ in ()).throw(
        ConnectionError("no network in tests")))
    out = feeds.fetch_all()
    for series in ("fed_funds_rate", "cpi_yoy", "unemployment_rate",
                   "treasury_10y", "treasury_2y", "yield_spread_10y2y"):
        assert series in out, f"{series} is not covered without FRED"
        assert "source" in out[series]
    assert out["_meta"]["fred_key_configured"] is False


# ── E / F / G: identity before grading ──────────────────────────────────────

def test_an_ambiguous_root_is_reported_not_guessed():
    """E — 93 bare roots exist on more than one venue. `AI` is C3.ai on NYSE
    and Air Liquide in Paris; a bare ticker is not an identity."""
    ident = idt.describe("AI")
    assert ident.ok
    assert ident.candidates, "a cross-venue collision must be surfaced"
    assert any("not an identity" in w for w in ident.warnings)


def test_an_unknown_symbol_is_unresolved_not_assumed():
    """F — the refusal. Guessing here is what graded BAE Systems against
    Boeing's NYSE close."""
    ident = idt.describe("NOT_A_REAL_TICKER_XYZ")
    assert ident.status == idt.UNRESOLVED
    assert ident.provider_symbol is None
    symbol, why = idt.price_symbol("NOT_A_REAL_TICKER_XYZ")
    assert symbol is None and why


def test_a_display_symbol_prices_through_its_provider_symbol():
    """G — the actual defect. `BA` must price as `BA.L` (BAE Systems), never as
    the bare `BA` that a price feed resolves to Boeing."""
    provider, _ = idt.price_symbol("BA")
    assert provider == "BA.L", "the symbol master knew this and was not asked"
    same, why = idt.same_instrument("BA", "BA.L")
    assert same, why


def test_two_different_securities_are_not_the_same_instrument():
    same, why = idt.same_instrument("BA", "AAPL")
    assert not same
    assert "BA.L" in why and "AAPL" in why


def test_the_effective_currency_is_the_unit_the_venue_quotes():
    """LSE rows store GBP while the provider quotes GBp — a 100x difference.
    describe() must never hand back the raw stored value."""
    ident = idt.describe("BA")
    assert ident.stored_currency == "GBP"
    assert ident.currency == "GBp"
    assert any("100x" in w for w in ident.warnings)


def test_the_four_contaminated_names_now_resolve_correctly():
    """A human reads the name before approving a trade, so a wrong one is not
    cosmetic. What matters is that the RIGHT name comes out.

    This test used to require a WARNING on each of these symbols — which only
    made sense while the symbol master held the wrong name and `describe()` had
    to patch it on the way out. A universe refresh running with the repaired
    yaml upsert has since written the correct names into the master, so there
    is nothing left to warn about and the assertion inverted: the defect being
    fixed is what broke the test.

    So it now asserts the outcome rather than the symptom.
    """
    for sym, expected in (("BA", "BAE"), ("AAL", "Anglo American"),
                          ("JD", "JD Sports"), ("EDV", "Endeavour")):
        ident = idt.describe(sym)
        assert ident.ok
        name, _ = idt.corrected_name(sym, ident.name)
        assert expected.lower() in (name or "").lower(), (
            f"{sym} resolves to a row named {name!r}, not {expected!r}")


def test_the_correction_still_fires_if_contamination_returns():
    """The repair is in the DATA, so the guard in the CODE must still work.

    If a future loader writes "Boeing" onto BA.L again, read-time correction is
    the only thing standing between that label and a human approving a trade
    against the wrong company. Exercised directly, because the live data no
    longer triggers it.
    """
    for sym, (contaminated, truth) in idt.KNOWN_BAD_NAMES.items():
        fixed, was_corrected = idt.corrected_name(sym, contaminated)
        assert was_corrected is True, f"{sym}: contamination passed through"
        assert fixed == truth, f"{sym}: corrected to {fixed!r}, expected {truth!r}"

    # And a correct name is passed through untouched — the guard must not
    # rewrite a name that is already right.
    passthrough, was = idt.corrected_name("BA", "BAE Systems")
    assert passthrough == "BAE Systems" and was is False


def test_grading_is_skipped_when_identity_cannot_be_established(tmp_path, monkeypatch):
    """F, on the grading path itself: never force a win, never force a loss."""
    from src.core import ledger
    monkeypatch.setattr(idt, "UNIVERSE_DB", tmp_path / "empty.db")
    provider, why = ledger._resolved_price_symbol("ANYTHING")
    assert provider is None
    assert why, "a refusal must carry its reason"


def test_the_universe_audit_reports_without_repairing():
    """The audit is read-only by construction — the source-of-truth decision
    belongs to the owner, and guessing at 21,067 rows would be a larger version
    of the mistake being fixed."""
    report = idt.audit()
    assert report["symbols"] > 20000
    assert report["duplicate_display_symbols"] == 0
    assert report["venue_mismatches"] == 0
    assert report["currency_mismatches"] == 0
    assert report["ambiguous_roots"] > 0
    assert report["known_bad_names"]
    assert "not verified" in report["known_bad_names_note"].lower()


# ── I / J: regime-aware learning stays blocked until it genuinely is not ────

def _regime_gate(labels):
    """The eligibility rule under test, isolated from its data source."""
    from src.core import attribution
    return attribution.regime_learning_eligible(labels)


def test_one_regime_does_not_unlock_regime_learning():
    """I — do not fabricate a second regime. Every observation ARIA holds is
    from "Expansion (Goldilocks)"; conditional analysis over one condition is
    not conditional analysis."""
    eligible, why = _regime_gate(["Expansion (Goldilocks)"] * 400)
    assert eligible is False
    assert "one regime" in why.lower() or "1 regime" in why.lower()


def test_two_genuine_regimes_make_it_eligible():
    """J — the architecture must unlock automatically when the data arrives,
    with no code change and no backfilled labels."""
    labels = ["Expansion (Goldilocks)"] * 200 + ["Contraction"] * 200
    eligible, why = _regime_gate(labels)
    assert eligible is True, why


def test_a_token_second_regime_is_not_enough():
    """Two labels where one has three observations is not two regimes."""
    labels = ["Expansion (Goldilocks)"] * 400 + ["Contraction"] * 3
    eligible, why = _regime_gate(labels)
    assert eligible is False
    assert "sample" in why.lower() or "too few" in why.lower()
