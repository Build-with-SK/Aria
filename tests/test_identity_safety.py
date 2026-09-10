"""
tests/test_identity_safety.py
=============================
Regression tests for the identity-safety phase — items 1–18 of the brief.

THE PRINCIPLE UNDER TEST
------------------------
    ONE INSTRUMENT -> ONE CANONICAL PROVIDER IDENTITY
                   -> ONE CORRECT PRICE UNIT
                   -> ONE REPRODUCIBLE HISTORICAL RECORD

Several of these assert a REFUSAL. That is deliberate: the defect being fixed
was a system that guessed an identity from ticker text, and a guess is only
detectable by testing the case where guessing would be easy and wrong.
"""
from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from src.core import identity as idt
from src.data import currency as cur

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _clear_caches():
    cur._symbol_ccy_cache.clear()
    yield
    cur._symbol_ccy_cache.clear()


@pytest.fixture
def universe():
    from src.data.universe import get_universe
    return get_universe()


# ── 1-3: BA and BA.L coexist; the YAML refresh cannot corrupt the LSE row ───

def test_ba_and_ba_l_coexist_without_identity_corruption():
    """1 — the display ticker is a namespace label; the provider symbol is the
    identity. They may collide without the instruments merging."""
    ident = idt.describe("BA")
    assert ident.ok
    assert ident.provider_symbol == "BA.L"
    assert ident.exchange == "LSE"
    # The row still knows it is a London instrument, whatever it is labelled.
    assert ident.currency == "GBp"


def test_the_yaml_upsert_refuses_to_rename_another_venues_instrument(tmp_path):
    """2 + 3 — the root cause, pinned.

    The old clause was `ON CONFLICT(symbol) DO UPDATE SET name=excluded.name`,
    which let a US YAML entry overwrite an LSE instrument's name while leaving
    its provider symbol, venue and currency London. This builds that exact
    collision in a throwaway database and proves the guarded upsert leaves the
    London identity — including its NAME — untouched.
    """
    db = tmp_path / "u.db"
    with sqlite3.connect(str(db)) as conn:
        conn.execute("""CREATE TABLE symbols(
            symbol TEXT PRIMARY KEY, yahoo TEXT NOT NULL, name TEXT,
            exchange TEXT, asset_class TEXT, updated_at TEXT)""")
        conn.execute("INSERT INTO symbols VALUES('BA','BA.L','BAE Systems',"
                     "'LSE','equity','t0')")

        # The guarded upsert, verbatim from _load_yaml_universe.
        existing = conn.execute("SELECT yahoo FROM symbols WHERE symbol=?",
                                ("BA",)).fetchone()
        collided = existing and existing[0] != "BA"
        if not collided:                       # pragma: no cover - guard intent
            conn.execute(
                """INSERT INTO symbols(symbol,yahoo,name,exchange,asset_class,updated_at)
                   VALUES(?,?,?,?,?,?)
                   ON CONFLICT(symbol) DO UPDATE SET name=excluded.name
                   WHERE symbols.yahoo = excluded.yahoo""",
                ("BA", "BA", "Boeing", "US", "equity", "t1"))

        row = conn.execute("SELECT yahoo,name,exchange FROM symbols "
                           "WHERE symbol='BA'").fetchone()
    assert collided, "the collision must be detected, not silently written through"
    assert row == ("BA.L", "BAE Systems", "LSE"), (
        "the US entry overwrote a London instrument's identity")


def test_the_guarded_upsert_still_updates_a_genuine_same_instrument_match(tmp_path):
    """The guard must not freeze the universe: when the provider symbol agrees,
    it IS the same instrument and the name should refresh."""
    db = tmp_path / "u.db"
    with sqlite3.connect(str(db)) as conn:
        conn.execute("""CREATE TABLE symbols(
            symbol TEXT PRIMARY KEY, yahoo TEXT NOT NULL, name TEXT,
            exchange TEXT, asset_class TEXT, updated_at TEXT)""")
        conn.execute("INSERT INTO symbols VALUES('AAPL','AAPL','Apple Inc',"
                     "'NASDAQ','equity','t0')")
        conn.execute(
            """INSERT INTO symbols(symbol,yahoo,name,exchange,asset_class,updated_at)
               VALUES(?,?,?,?,?,?)
               ON CONFLICT(symbol) DO UPDATE SET name=excluded.name
               WHERE symbols.yahoo = excluded.yahoo""",
            ("AAPL", "AAPL", "Apple", "US", "equity", "t1"))
        name = conn.execute("SELECT name FROM symbols WHERE symbol='AAPL'").fetchone()[0]
    assert name == "Apple", "a same-instrument refresh was blocked"


# ── 4-7: search identity safety ─────────────────────────────────────────────

def test_search_boeing_cannot_return_bae_systems(universe):
    """4 — the live bug. `search("Boeing")` returned BA -> BA.L, which is BAE
    Systems, because that row is LABELLED Boeing."""
    for row in universe.search("Boeing", n=20):
        assert row["provider_symbol"] != "BA.L", (
            "a search for Boeing returned BAE Systems' London listing")
        assert "BAE" not in (row["name"] or "").upper()


def test_boeing_absence_is_reported_not_fabricated(universe):
    """§6 — if there is no valid Boeing common listing, do not invent one and
    do not offer a different company in its place."""
    rows = universe.search("Boeing", n=20)
    commons = [r for r in rows if r["venue"] in ("NYSE", "NASDAQ")
               and "DEPOSITARY" not in (r["name"] or "").upper()]
    assert commons == [], (
        "a Boeing common line appeared; the universe genuinely has only the "
        "preferred share, and inventing one would be worse than the gap")


def test_search_bae_systems_returns_the_london_instrument(universe):
    """5 — and the mirror image: a corrected name must be SEARCHABLE, not just
    displayable, or the fix simply inverts the bug."""
    rows = universe.search("BAE Systems", n=5)
    assert rows, "BAE Systems is in the universe and must be findable"
    top = rows[0]
    assert top["provider_symbol"] == "BA.L"
    assert top["venue"] == "LSE"

    # What must hold is that the row IDENTIFIES BAE Systems — not that the
    # stored name happened to need correcting on the way out.
    #
    # This used to assert `name_corrected is True`, which encoded the broken
    # state as the expected one: BA.L was stored as "Boeing", so every correct
    # answer arrived via the read-time correction. A universe refresh running
    # with the repaired yaml upsert has since written the right name into the
    # master, so nothing needs correcting and the flag is now False — the
    # defect being fixed made the test fail.
    name = (top.get("name") or "").upper()
    assert "BAE" in name, f"the top hit is named {top.get('name')!r}"
    assert top["name_corrected"] in (True, False)


def test_search_by_provider_symbol_returns_that_instrument(universe):
    """6 — the provider symbol is the identity, so it must be a first-class
    query."""
    rows = universe.search("BA.L", n=5)
    assert rows and rows[0]["provider_symbol"] == "BA.L"
    assert rows[0]["venue"] == "LSE"


def test_a_bare_ticker_never_silently_picks_a_venue(universe):
    """7 — every result must carry enough identity that a human can see which
    venue they are looking at."""
    for row in universe.search("BA", n=10):
        assert row["provider_symbol"], "a result without a provider symbol"
        assert row["venue"], "a result without a venue"
        assert "price_unit" in row


def test_ranking_puts_an_exact_provider_symbol_first(universe):
    rows = universe.search("AAPL", n=5)
    assert rows[0]["provider_symbol"] == "AAPL"


def test_a_name_match_is_not_satisfied_by_a_symbol_collision(universe):
    """§7 — a company-name query must resolve by name, never by a ticker that
    happens to contain the same letters."""
    rows = universe.search("Anglo American", n=5)
    assert rows, "Anglo American must be findable"
    assert rows[0]["provider_symbol"] == "AAL.L"
    assert rows[0]["venue"] == "LSE"


# ── 8: provider identity uniqueness ─────────────────────────────────────────

def test_provider_symbol_uniqueness_holds_except_for_known_exceptions():
    """8 — `(provider, provider_symbol)` is the canonical key. Before it can be
    enforced as a constraint, the exceptions must be classified rather than
    hidden: 3 display aliases for one instrument, 2 genuine BSE data errors."""
    from collections import Counter
    db = idt.UNIVERSE_DB
    if not db.exists():
        pytest.skip("symbol master unavailable")
    with sqlite3.connect(str(db)) as conn:
        conn.row_factory = sqlite3.Row
        rows = [dict(r) for r in conn.execute(
            "SELECT symbol, yahoo, name FROM symbols")]

    dupes = {y for y, n in Counter(r["yahoo"] for r in rows).items() if n > 1}
    assert dupes, "expected at least the known display aliases"

    # Classified by the PRODUCTION classifier, on ISIN.
    #
    # This test used to re-implement the classification locally, comparing
    # normalised company NAMES and holding a hardcoded set of two BSE codes it
    # expected to be genuine collisions. Both halves of that were wrong:
    #
    #  * Names were the weakest available evidence and the thing that had
    #    actually changed. The two codes it named are one security each, whose
    #    company was renamed between bhavcopy loads — proven by identical
    #    ISINs, which the loader was already storing and nobody was reading.
    #  * The hardcoded set could not survive a refresh. A real universe refresh
    #    added FOUR more renamed BSE listings, and a snapshot of "the two we
    #    know about" went red for six instruments that were all fine.
    #
    # So this asserts the INVARIANT — one provider symbol identifies at most
    # one security — against the same classifier the application uses, and lets
    # the number of legitimate renames grow.
    report = idt.provider_symbol_duplicates()
    assert report["one_symbol_one_security"] is True, (
        f"a provider symbol now carries two different securities: "
        f"{sorted(report['errors'])}. Resolve that before any constraint work.")
    assert report["alias_count"] == len(dupes), (
        "every duplicate provider symbol should classify as an alias")

    # Every alias must have a stated basis. An unexplained merge is a guess.
    for code, entry in report["aliases"].items():
        assert entry["basis"], f"{code} was classified with no reason given"


# ── 9-10: price unit ────────────────────────────────────────────────────────

def test_one_hundred_pence_is_one_pound():
    """9 — the arithmetic everything else rests on."""
    assert cur.to_base(100.0, "GBp", "GBP") == pytest.approx(1.0)
    assert cur.to_base(2100.0, "GBp", "GBP") == pytest.approx(21.0)
    assert cur.convert(1.0, "GBP", "GBp") == pytest.approx(100.0, rel=1e-6)


def test_every_london_instrument_reports_pence_not_pounds():
    """The stored currency says GBP; the venue quotes GBp. `describe()` must
    never hand back the raw stored value."""
    for sym in ("BA", "AAL", "SHEL", "AZN", "RIO"):
        ident = idt.describe(sym)
        if not ident.ok:
            continue
        assert ident.stored_currency == "GBP"
        assert ident.currency == "GBp", f"{sym} would be 100x wrong"


def test_raw_pence_cannot_reach_a_base_currency_unnormalised():
    """10 — the normalisation is not optional. A GBp amount converted to USD
    must pass through the /100 step; if it ever equals the naive conversion,
    the pence handling has been bypassed."""
    amount = 2100.0
    normalised = cur.to_base(amount, "GBp", "USD")
    naive = cur.to_base(amount, "GBP", "USD")
    assert normalised is not None and naive is not None
    assert normalised == pytest.approx(naive / 100.0, rel=1e-6), (
        "GBp reached the base currency without the pence step")


def test_an_unknown_currency_refuses_to_convert():
    assert cur.to_base(100.0, None) is None
    assert cur.native_currency("^GSPC") is None


# ── 11-18: the migration ────────────────────────────────────────────────────

@pytest.fixture
def seeded_ledger(tmp_data_dir, monkeypatch):
    """A throwaway ledger holding one unambiguous and one London prediction."""
    from src.core import ledger
    ledger.record_prediction(subject="AAPL", claim="c", direction="bull",
                             horizon_days=5, price_at=200.0, source="test",
                             external_ref="t-aapl", announce=False)
    ledger.record_prediction(subject="BA", claim="c", direction="bull",
                             horizon_days=5, price_at=2200.0, source="test",
                             external_ref="t-ba", announce=False)
    return ledger


def test_dry_run_makes_zero_production_mutations():
    """17 — the dry run is the safety mechanism; it must be provably read-only.

    Run as a subprocess against the REAL database, exactly as an operator would,
    and assert the schema and counts are untouched. The production-write guard
    in conftest cannot see a subprocess, so this checks the effect directly.
    """
    real = REPO / "data" / "aria_core.db"
    if not real.exists():
        pytest.skip("no production ledger present")

    def snapshot():
        with sqlite3.connect(f"file:{real}?mode=ro", uri=True) as conn:
            cols = [r[1] for r in conn.execute("PRAGMA table_info(predictions)")]
            n = conn.execute("SELECT COUNT(*) FROM predictions").fetchone()[0]
            g = conn.execute("SELECT COUNT(*) FROM predictions "
                             "WHERE resolved=1").fetchone()[0]
        return cols, n, g

    before = snapshot()
    # The subprocess must see the REAL ledger, not the test redirect — the
    # whole point is to prove the operator's command is read-only.
    import os
    env = {k: v for k, v in os.environ.items()
           if k not in ("ARIA_CORE_DB", "ARIA_DATA_DIR", "ARIA_TESTING")}
    proc = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "migrate_identity.py"),
         "--dry-run", "--json"],
        cwd=str(REPO), capture_output=True, text=True, timeout=600, env=env)
    assert proc.returncode == 0, proc.stderr[-500:]
    assert snapshot() == before, "the dry run mutated production state"


def test_ambiguous_identities_fail_closed(monkeypatch):
    """18 — when two candidates both match the recorded price, the migration
    must refuse the row rather than pick one."""
    sys.path.insert(0, str(REPO))
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "migrate_identity", REPO / "scripts" / "migrate_identity.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    # Both candidates return the recorded price — genuinely undecidable.
    monkeypatch.setattr(mod, "_candidates", lambda s: ["MRK", "MRK.DE"])
    monkeypatch.setattr(mod, "_close_on", lambda sym, on: 100.0)
    plan = mod.plan_row({"id": "x", "subject": "MRK", "price_at": 100.0,
                         "created_at": "2026-08-01T00:00:00"})
    assert plan["status"] == mod.UNRESOLVED
    assert plan["provider_symbol"] is None
    assert "AMBIGUOUS" in plan["reason"]


def test_a_row_with_no_reference_price_is_not_guessed(monkeypatch):
    sys.path.insert(0, str(REPO))
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "migrate_identity", REPO / "scripts" / "migrate_identity.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "_candidates", lambda s: ["BA.L", "BA"])
    plan = mod.plan_row({"id": "x", "subject": "BA", "price_at": None,
                         "created_at": "2026-08-01T00:00:00"})
    assert plan["status"] == mod.UNRESOLVED


def test_historical_subject_is_never_rewritten(seeded_ledger):
    """11 — HISTORICAL FACTS ARE IMMUTABLE. `subject` records what ARIA called
    the thing; `provider_symbol` records what was evaluated. Both are kept."""
    from src.core.ledger import connect
    with connect() as conn:
        subjects = {r["subject"] for r in
                    conn.execute("SELECT subject FROM predictions")}
    assert "BA" in subjects, "the historical subject was rewritten"
    assert "BA.L" not in subjects, (
        "a provider symbol leaked into the subject column; history must record "
        "what ARIA originally called it")


def test_the_migration_plan_is_deterministic(monkeypatch):
    """16 — idempotence starts with a stable plan. Two runs over identical
    inputs must produce identical decisions."""
    sys.path.insert(0, str(REPO))
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "migrate_identity", REPO / "scripts" / "migrate_identity.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "_candidates", lambda s: ["BA.L", "BA"])
    monkeypatch.setattr(mod, "_close_on",
                        lambda sym, on: 2200.0 if sym == "BA.L" else 214.0)
    row = {"id": "x", "subject": "BA", "price_at": 2200.0,
           "created_at": "2026-08-06T00:00:00"}
    first, second = mod.plan_row(dict(row)), mod.plan_row(dict(row))
    assert first == second
    assert first["provider_symbol"] == "BA.L"
    assert first["status"] == mod.PRICE_VERIFIED


def test_an_already_identified_row_is_skipped(monkeypatch):
    """16 — re-running must not rewrite rows that already carry an identity."""
    sys.path.insert(0, str(REPO))
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "migrate_identity", REPO / "scripts" / "migrate_identity.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    plan = mod.plan_row({"id": "x", "subject": "BA", "price_at": 2200.0,
                         "created_at": "2026-08-06T00:00:00",
                         "provider_symbol": "BA.L"})
    assert plan["status"] == mod.ALREADY_SET
    assert plan["provider_symbol"] == "BA.L"


# ── §5: the constraint cannot be added yet, and that must be explicit ───────

def test_no_provider_symbol_carries_two_different_securities():
    """§5 — the invariant that matters, and it now holds.

    This test previously asserted the OPPOSITE, as a deliberate tripwire: two
    BSE rows appeared to map different companies onto one scrip code, and the
    test was written to fail the moment that changed so the constraint decision
    would be taken rather than drifted into. It fired. Updated deliberately, on
    evidence:

        531257.BO  PRATIKSHA CHEMICALS LTD.  == VELLORA IMPACT LIMITED
                   both ISIN INE530D01012
        539455.BO  ARYAVAN ENTERPRISE LTD    == ECOFINITY ATOMIX LIMITED
                   both ISIN INE360S01012

    A BSE scrip code is permanent; a ticker is not. Both pairs are ONE security
    renamed between the 2026-07-28 and 2026-08-06 bhavcopy loads, and the ISIN
    — which the loader was already reading and storing — says so. The earlier
    audit had only the names, and the names were exactly what had changed.
    """
    d = idt.provider_symbol_duplicates()
    assert d["alias_count"] >= 3, "the known display aliases vanished"
    assert d["one_symbol_one_security"] is True, (
        f"a provider symbol carries two securities again: {sorted(d['errors'])}")
    assert d["error_count"] == 0
    assert not d["unexpected_errors"]
    assert not d["regressed"], (
        f"{d['regressed']} was cleared by ISIN and has re-broken — the evidence "
        f"that resolved it has gone away")


def test_the_bse_renames_are_classified_by_isin_not_by_name():
    """The evidence has to be the ISIN, not a hardcoded pair of tickers.

    A list would have failed twice on this data: once when `GBPINR=X` appeared
    as a NEW alias mid-audit, and again here, where the company NAMES are the
    thing that changed.
    """
    d = idt.provider_symbol_duplicates()
    for code in ("531257.BO", "539455.BO"):
        entry = d["aliases"].get(code)
        assert entry, f"{code} is no longer classified as an alias"
        assert "ISIN" in entry["basis"], (
            f"{code} was cleared on something weaker than an ISIN: "
            f"{entry['basis']}")
        isins = {r["isin"] for r in entry["rows"]}
        assert len(isins) == 1 and next(iter(isins)), (
            f"{code} rows do not share one ISIN: {isins}")


def test_a_unique_index_is_still_not_safe_and_was_not_added():
    """Two different questions, kept apart.

    "Does one provider symbol identify one security?" is now yes. "Can a
    UNIQUE(yahoo) index be added?" is still no, and for a completely benign
    reason: legitimate aliases share a provider symbol — a superseded ticker, a
    punctuation variant, the bare form of an FX pair — and the index would
    resolve each pair by deleting a row instead of reporting a conflict.
    Collapsing the two questions is how a constraint gets added for a reason
    that was never about safety.
    """
    d = idt.provider_symbol_duplicates()
    assert d["unique_index_safe"] is False
    assert d["total_duplicate_provider_symbols"] >= 3
    import sqlite3
    with sqlite3.connect(str(idt.UNIVERSE_DB)) as conn:
        indexes = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'").fetchall()]
    for name in indexes:
        with sqlite3.connect(str(idt.UNIVERSE_DB)) as conn:
            info = conn.execute(f"PRAGMA index_info({name!r})").fetchall()
            cols = {r[2] for r in info}
        is_unique = any(r[2] for r in sqlite3.connect(
            str(idt.UNIVERSE_DB)).execute("PRAGMA index_list(symbols)")
            if r[1] == name)
        assert not (is_unique and cols == {"yahoo"}), (
            f"a UNIQUE index on yahoo exists ({name}) — it would have deleted "
            f"one row of every legitimate alias pair")


def test_a_provider_symbol_cannot_be_pointed_at_a_second_company():
    """The application-layer stand-in for the constraint."""
    ok, why = idt.assert_provider_symbol_safe("BA.L", "BAX", "Boeing")
    assert ok is False and "one instrument" in why.lower()
    ok, _ = idt.assert_provider_symbol_safe("BA.L", "BAES", "BAE Systems plc")
    assert ok is True, "a genuine display alias was refused"


def test_the_write_guard_judges_the_corrected_name_not_the_stored_one():
    """Regression on a bug in the guard itself: comparing the STORED name meant
    BA.L (stored 'Boeing') accepted Boeing and rejected BAE Systems — exactly
    backwards. Found by exercising it, not by reading it."""
    refuse, _ = idt.assert_provider_symbol_safe("AAL.L", "AALUS", "American Airlines")
    allow, _ = idt.assert_provider_symbol_safe("AAL.L", "ANGLO", "Anglo American plc")
    assert refuse is False and allow is True


# ── §9: raw GBp must not reach a downstream sink un-normalised ─────────────

def test_no_downstream_sink_consumes_a_price_without_its_currency():
    """§9 — the orphaned pages are not a safety mechanism.

    Every route from provider market data into signals / recommendations /
    portfolio / predictions must pair a price with the currency of the SAME
    instrument. This asserts the structural precondition: the currency helper
    resolves from the provider symbol, so a caller that passes one gets a unit
    that matches its price.
    """
    for provider_symbol, expected in (("BA.L", "GBp"), ("AAL.L", "GBp"),
                                      ("AAPL", "USD"), ("BTC-USD", "USD")):
        cur._symbol_ccy_cache.clear()
        assert cur.native_currency(provider_symbol) == expected, (
            f"{provider_symbol} resolves to the wrong unit; a price fetched "
            f"with it would be scaled wrongly downstream")


def test_a_pence_price_is_never_equal_to_its_pound_interpretation():
    """The 100x, stated as an invariant rather than a hope."""
    for pence in (100.0, 2100.0, 3966.0):
        as_pence = cur.to_base(pence, "GBp", "GBP")
        as_pounds = cur.to_base(pence, "GBP", "GBP")
        assert as_pence != as_pounds
        assert as_pence == pytest.approx(as_pounds / 100.0)


# ── §11 items 12-16: post-migration invariants, proven on a simulation ─────

def _load_migrator():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "migrate_identity", REPO / "scripts" / "migrate_identity.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_migration_preserves_every_historical_field(tmp_path, monkeypatch):
    """12-15 — run the real `apply()` against a throwaway ledger and prove that
    ONLY provider_symbol changed.

    This cannot wait for the production migration: the invariants have to be
    demonstrated BEFORE it runs, which is the whole point of proving them here.
    """
    db = tmp_path / "ledger.db"
    monkeypatch.setenv("ARIA_CORE_DB", str(db))
    from src.core import bus, ledger
    monkeypatch.setattr(bus, "_initialised", False)
    monkeypatch.setattr(ledger, "_ready", False)

    ledger.record_prediction(subject="BA", claim="c", direction="bull",
                             horizon_days=5, price_at=2200.0, source="technical",
                             external_ref="m-ba", announce=False)
    ledger.record_prediction(subject="AAPL", claim="c", direction="bull",
                             horizon_days=5, price_at=200.0, source="v5",
                             external_ref="m-aapl", announce=False)
    pid = ledger.predictions(limit=10)[0]["id"]
    ledger.grade(pid, correct=True, price_end=2300.0, actual_return=0.045,
                 announce=False)

    def snapshot():
        with sqlite3.connect(str(db)) as conn:
            conn.row_factory = sqlite3.Row
            return [{k: r[k] for k in r.keys() if k != "provider_symbol"
                     and k != "identity_basis"}
                    for r in conn.execute("SELECT * FROM predictions ORDER BY id")]

    before = snapshot()

    mod = _load_migrator()
    monkeypatch.setattr(mod, "_db_path", lambda: db)
    # apply() backs up to ROOT/data/backups — real production. The write guard
    # caught that, correctly; the simulation gets its own root.
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    monkeypatch.setattr(mod, "_close_on",
                        lambda sym, on: {"BA.L": 2200.0, "BA": 214.0}.get(sym))
    plan = mod.build_plan()
    result = mod.apply(plan)

    after = snapshot()
    assert after == before, "a historical field other than the identity changed"
    assert result["invariants_held"] is True, result["drift"]

    with sqlite3.connect(str(db)) as conn:
        conn.row_factory = sqlite3.Row
        rows = {r["subject"]: r["provider_symbol"]
                for r in conn.execute("SELECT subject, provider_symbol FROM predictions")}
    assert rows["BA"] == "BA.L", "the London identity was not attached"
    assert rows["AAPL"] == "AAPL"
    assert "BA.L" not in rows, "a provider symbol leaked into `subject`"


def test_the_migration_is_idempotent(tmp_path, monkeypatch):
    """16 — a second run must write nothing and change nothing."""
    db = tmp_path / "ledger2.db"
    monkeypatch.setenv("ARIA_CORE_DB", str(db))
    from src.core import bus, ledger
    monkeypatch.setattr(bus, "_initialised", False)
    monkeypatch.setattr(ledger, "_ready", False)
    pid = ledger.record_prediction(subject="AAPL", claim="c", direction="bull",
                                   horizon_days=5, price_at=200.0, source="v5",
                                   external_ref="i-aapl", announce=False)

    # Strip the identity the WRITE PATH now stamps, so the migration has real
    # work to do.
    #
    # This test used to rely on `record_prediction` leaving identity null — true
    # when the migration was the only thing that ever wrote those columns, and
    # false since the write path started stamping them itself. With nothing to
    # backfill, `first["written"]` was 0 and the assertion failed because the
    # underlying defect had been FIXED. Simulating a pre-fix row keeps the
    # property this test is actually about — that a second run is a no-op —
    # meaningful rather than vacuous.
    import sqlite3 as _sq
    with _sq.connect(str(db)) as _c:
        _c.execute("UPDATE predictions SET provider_symbol=NULL, "
                   "identity_basis=NULL WHERE id=?", (pid,))
        _c.commit()

    mod = _load_migrator()
    monkeypatch.setattr(mod, "_db_path", lambda: db)
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    first = mod.apply(mod.build_plan())
    second = mod.apply(mod.build_plan())
    assert first["written"] >= 1, "the migration found nothing to backfill"
    assert second["written"] == 0, "the second run rewrote rows"
    assert second["invariants_held"] is True
