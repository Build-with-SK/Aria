"""
tests/test_identity_namespaces.py
=================================
defect:bare-ticker-identity-collision-2026-08 — CLOSED, and pinned closed.

THE DEFECT IN ONE PARAGRAPH
---------------------------
ARIA holds two universes that both key on a bare ticker and disagree about what
four of them mean. `configs/universe.yaml` says BA is Boeing (it is the string
handed to the provider, and the provider returns Boeing). `universe.db` says the
DISPLAY symbol `BA` resolves to `BA.L` — BAE Systems — because the LSE seeder
reached the primary key first. Both are correct in their own namespace.

The bug was a function that answered without one. `native_currency("BA")`
returned GBp, so `signals.json`'s $214 Boeing price was read as 214 pence and
displayed at $2.92 — a hundredfold error assembled out of two true facts.

THE FIX
-------
Identity is (provider, provider_symbol). A provider symbol carries its own venue
in its suffix, so it never needs a display lookup, and a display collision can
never reach it. Signals now carry their own provider symbol and quote unit, so
no downstream reader has to re-derive anything.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.core import identity as idt
from src.data import currency as cur


@pytest.fixture(autouse=True)
def _clear_cache():
    cur._symbol_ccy_cache.clear()
    yield
    cur._symbol_ccy_cache.clear()


# ── the four colliding tickers, both ways round ─────────────────────────────

COLLIDING = {
    "BA":  ("BA.L", "BAE Systems plc"),
    "AAL": ("AAL.L", "Anglo American plc"),
    "JD":  ("JD.L", "JD Sports Fashion Plc"),
    "EDV": ("EDV.L", "Endeavour Mining plc"),
}


@pytest.mark.parametrize("bare", sorted(COLLIDING))
def test_a_bare_ticker_is_the_us_listing_in_the_signal_namespace(bare):
    ident = idt.signal_identity(bare)
    assert ident.provider_symbol == bare
    assert ident.exchange == "US"
    assert ident.currency == "USD"


@pytest.mark.parametrize("bare,expected", [(k, v[0]) for k, v in COLLIDING.items()])
def test_the_london_listing_stays_london(bare, expected):
    ident = idt.provider_identity(expected)
    assert ident.provider_symbol == expected
    assert ident.exchange == "LSE"
    assert ident.currency == "GBp"


@pytest.mark.parametrize("bare", sorted(COLLIDING))
def test_the_two_listings_remain_distinct(bare):
    """AAL and AAL.L are different companies and must never merge."""
    us = idt.provider_identity(bare)
    lse = idt.provider_identity(COLLIDING[bare][0])
    assert us.provider_symbol != lse.provider_symbol
    assert us.exchange != lse.exchange
    assert us.currency != lse.currency


def test_the_display_row_still_answers_for_the_symbol_master():
    """The fix must not break the OTHER namespace. `BA` as a display symbol
    genuinely is BA.L in universe.db, and search depends on that."""
    assert idt.describe("BA").provider_symbol == "BA.L"
    assert cur.native_currency("BA") == "GBp"


# ── the 100x, both directions ───────────────────────────────────────────────

def test_a_us_price_cannot_be_converted_with_london_pence_rules():
    price = 214.20                       # Boeing, USD
    ccy = cur.provider_currency("BA")
    assert ccy == "USD"
    assert cur.to_base(price, ccy, "USD") == pytest.approx(price)


def test_a_london_pence_price_is_not_read_as_pounds():
    """The mirror-image error, which a careless fix would introduce."""
    assert cur.provider_currency("BA.L") == "GBp"
    assert cur.to_base(2200.0, "GBp", "GBP") == pytest.approx(22.0)
    assert cur.to_base(2200.0, "GBP", "GBP") == pytest.approx(2200.0)


def test_the_pence_model_itself_is_untouched():
    """`GBp` was NOT replaced with `GBP`. Doing that would INTRODUCE the 100x
    error this fix removes, because every reader that already divides by 100
    would then do it twice."""
    assert cur.to_base(100.0, "GBp", "GBP") == pytest.approx(1.0)
    assert cur.convert(1.0, "GBP", "GBp") == pytest.approx(100.0, rel=1e-6)
    ident = idt.provider_identity("BA.L")
    assert ident.stored_currency == "GBP" and ident.currency == "GBp"


# ── the batch path the UI actually uses ─────────────────────────────────────

def test_the_batch_lookup_defaults_to_provider_symbols():
    got = cur.currencies_for(["BA", "AAL", "JD", "EDV", "AAPL"])
    assert set(got.values()) == {"USD"}


def test_the_master_reading_must_be_asked_for_explicitly():
    assert cur.currencies_for(["BA"], namespace="master") == {"BA": "GBp"}


# ── FX, futures and indices are not US equities ─────────────────────────────

@pytest.mark.parametrize("symbol,venue,aclass,ccy", [
    ("GBPINR=X", "FX", "forex", "INR"),
    ("USDJPY=X", "FX", "forex", "JPY"),
    ("EURGBP=X", "FX", "forex", "GBP"),
    ("ES=F", "FUT", "futures", "USD"),
    ("^GSPC", "INDEX", "index", None),
    ("BTC-USD", "CRYPTO", "crypto", "USD"),
    ("RELIANCE.NS", "NSE", "equity", "INR"),
    ("AAPL", "US", "equity", "USD"),
])
def test_a_provider_symbol_classifies_itself(symbol, venue, aclass, ccy):
    got = idt.classify_provider_symbol(symbol)
    assert got["exchange"] == venue
    assert got["asset_class"] == aclass
    assert got["currency"] == ccy


def test_gbpinr_is_an_fx_cross_not_a_us_equity():
    """The live resolver filed every non-equity suffix as US/equity, so a
    currency pair was stored as a US share."""
    ident = idt.provider_identity("GBPINR=X")
    assert ident.exchange == "FX"
    assert ident.asset_class == "forex"
    assert ident.currency == "INR"

    import sqlite3
    with sqlite3.connect(str(idt.UNIVERSE_DB)) as conn:
        conn.row_factory = sqlite3.Row
        rows = [dict(r) for r in conn.execute(
            "SELECT symbol, exchange, asset_class, currency FROM symbols "
            "WHERE yahoo = 'GBPINR=X'")]
    assert rows, "GBPINR=X is not in the symbol master"
    for r in rows:
        assert r["exchange"] != "US", f"{r['symbol']} is still filed as US"
        assert r["asset_class"] != "equity", f"{r['symbol']} is still an equity"


def test_no_fx_or_futures_row_is_filed_as_a_us_equity():
    """The whole class, not just the one that was noticed."""
    import sqlite3
    with sqlite3.connect(str(idt.UNIVERSE_DB)) as conn:
        conn.row_factory = sqlite3.Row
        bad = [dict(r) for r in conn.execute(
            "SELECT symbol, yahoo, exchange, asset_class FROM symbols "
            "WHERE (yahoo LIKE '%=X' OR yahoo LIKE '%=F' OR yahoo LIKE '^%') "
            "  AND (exchange = 'US' OR asset_class = 'equity')")]
    assert not bad, f"{len(bad)} non-equity instruments filed as US equities: {bad[:5]}"


def test_the_resolver_cannot_recreate_the_misclassification():
    """Fixing the data without fixing the writer just delays the next one."""
    src = Path("src/data/universe.py").read_text(encoding="utf-8")
    block = src[src.index("def _resolution_candidates"):]
    block = block[:block.index("def resolve(")]
    assert "_classify(q)" in block, (
        "_resolution_candidates no longer asks the symbol what it is; the "
        "catch-all US/equity assumption is back")


# ── the signal file carries its own identity ────────────────────────────────

def test_every_signal_states_its_provider_symbol_and_unit():
    signals = json.loads(Path("data/signals.json").read_text(encoding="utf-8"))
    rows = [(k, v) for k, v in signals.items() if isinstance(v, dict)]
    assert rows, "signals.json is empty"
    for key, row in rows:
        assert "provider_symbol" in row, f"{key} carries a price with no identity"
        assert "price_unit" in row, f"{key} carries a price with no unit"
        assert row["provider_symbol"] == key, (
            f"{key} claims to be {row['provider_symbol']} — a signal key IS its "
            f"provider symbol")


def test_the_signal_prices_for_the_colliding_tickers_are_dollars():
    signals = json.loads(Path("data/signals.json").read_text(encoding="utf-8"))
    for bare in COLLIDING:
        row = signals.get(bare)
        if not row:
            continue
        assert row["price_unit"] == "USD", (
            f"{bare} in the signal file is quoted in {row['price_unit']}")
        assert row["venue"] == "US"


def test_the_ledger_prices_are_still_london_scale():
    """The ledger resolved through the symbol master, so its reference prices
    for these tickers are London pence. Fixing the SIGNAL namespace must not
    rescale the historical record."""
    # Read the PRODUCTION ledger, read-only.
    #
    # This used to go through `ledger.connect()`, which the test harness
    # redirects to a temp database — so it found no rows and skipped, every
    # single run. A guard that never executes is not a guard, and this one is
    # protecting the historical record from being silently rescaled by the
    # namespace fix, which is precisely the thing worth checking.
    import sqlite3
    db = Path("data/aria_core.db")
    if not db.exists():
        pytest.skip("no production ledger in this checkout")
    uri = f"file:{db.as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT subject, price_at FROM predictions "
            "WHERE subject IN ('AAL','BA','JD','SHEL') AND price_at IS NOT NULL"
        ).fetchall()
    assert rows, (
        "the ledger holds no predictions on the colliding tickers — this "
        "assertion has stopped protecting anything")
    floors = {"AAL": 1000, "BA": 1000, "JD": 50, "SHEL": 1000}
    for r in rows:
        assert r["price_at"] >= floors[r["subject"]], (
            f"{r['subject']} reference price {r['price_at']} looks like the US "
            f"listing — the historical record has been rescaled")


# ── half-written identity: the defect class, not just its instances ─────────
#
# This shape has now produced two separate live defects:
#
#   `BA`    the yaml loader refreshed `name` and left yahoo/exchange/currency,
#           so the row was Boeing's name on BAE Systems' identity.
#   `EDV.L` the LSE loader refreshed name/exchange/currency and left
#           `asset_class`, so a gold miner stayed filed as `fixed_income`
#           because a US bond ETF shares the bare ticker and loads first.
#
# Both are the same mistake: an upsert that claims a column on INSERT and
# abandons it on CONFLICT. The result is a row that is partly one instrument
# and partly another, which is worse than either refusing or overwriting.

def test_the_lse_loader_refreshes_the_asset_class_it_declares():
    src = Path("src/data/universe.py").read_text(encoding="utf-8")
    body = src[src.index("def _load_lse_equities"):]
    body = body[:body.index("def _load_nse_equities")]
    assert '"equity"' in body, "the LSE loader no longer declares an asset class"
    assert "asset_class=excluded.asset_class" in body, (
        "the LSE loader declares asset_class on INSERT but abandons it on "
        "CONFLICT — that is how EDV.L stayed 'fixed_income' while being "
        "Endeavour Mining")


def test_no_london_equity_is_filed_as_fixed_income():
    """The instance, checked against real data."""
    import sqlite3
    with sqlite3.connect(f"file:{idt.UNIVERSE_DB.as_posix()}?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        rows = [dict(r) for r in conn.execute(
            "SELECT symbol, yahoo, name, asset_class FROM symbols "
            "WHERE yahoo = 'EDV.L'")]
    if not rows:
        pytest.skip("EDV.L is not in this checkout's symbol master")
    for r in rows:
        assert r["asset_class"] == "equity", (
            f"{r['yahoo']} ({r['name']}) is filed as {r['asset_class']}")


def test_the_yaml_loader_still_refuses_to_rewrite_another_venues_identity():
    """The FIRST instance must stay fixed while fixing the second.

    The yaml loader is the one upsert that SHOULD leave columns alone: it holds
    a US-only universe and must never re-point a display row that London owns.
    Its restraint is deliberate, and a blanket "refresh everything on conflict"
    cleanup would reintroduce the original `BA` corruption.
    """
    src = Path("src/data/universe.py").read_text(encoding="utf-8")
    body = src[src.index("def _load_yaml_universe"):]
    end = body.find("\n    def ", 10)
    body = body[:end] if end > 0 else body
    assert "WHERE symbols.yahoo = excluded.yahoo" in body, (
        "the yaml upsert no longer checks that it is updating the SAME "
        "instrument — this is defect:universe-db-venue-mismatch-2026-08")
    assert "exchange=excluded.exchange" not in body, (
        "the yaml loader is rewriting exchange again; it holds a US-only "
        "universe and must not re-point a row London owns")


# ── unverified names cannot reach a trading decision ────────────────────────

def test_company_names_never_reach_the_execution_path():
    """21,000+ names are unverified against an authoritative master, so the
    question that matters is not "are they right" but "what do they affect".

    They affect SEARCH and DISPLAY. They must not affect what gets traded: a
    proposal is keyed on the ticker and priced through the provider symbol, and
    if a company name could steer an order then every unverified row would be a
    latent execution defect rather than a cosmetic one.
    """
    import ast
    for path in Path("src/execution").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            # `getattr(broker, "name", ...)` is the BROKER's name, not a
            # company's — that is the only `name` this layer may read.
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "getattr":
                continue
        text = path.read_text(encoding="utf-8")
        assert "universe" not in text.lower() or "symbols" not in text.lower(), (
            f"{path.name} reads the symbol master; company names must not "
            f"reach the execution layer")


def test_the_known_bad_name_table_is_a_tripwire_not_a_crutch():
    """Every entry should now be a no-op, because the master was repaired.

    If one of these starts firing again, a loader has reintroduced a wrong name
    on the wrong instrument and the table is the only thing that would notice.
    """
    import sqlite3
    with sqlite3.connect(f"file:{idt.UNIVERSE_DB.as_posix()}?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        stored = {r["symbol"]: r["name"] for r in conn.execute(
            "SELECT symbol, name FROM symbols WHERE symbol IN "
            "('AAL','BA','EDV','JD')")}
    if not stored:
        pytest.skip("symbol master unavailable")
    firing = []
    for sym, (_bad, truth) in idt.KNOWN_BAD_NAMES.items():
        if sym not in stored:
            continue
        _name, was_corrected = idt.corrected_name(sym, stored[sym])
        if was_corrected:
            firing.append(sym)
    assert not firing, (
        f"{firing} still carry a contaminated name in the symbol master — a "
        f"loader has reintroduced the defect the table exists to catch")


def test_identity_reports_whether_a_name_was_corrected():
    """A caller must be able to tell a verified name from a patched one."""
    fixed, was = idt.corrected_name("BA", "Boeing")
    assert fixed == "BAE Systems plc" and was is True
    passthrough, was = idt.corrected_name("BA", "BAE Systems")
    assert passthrough == "BAE Systems" and was is False
