"""
tests/fixtures/universe_fixture.py
===================================
A minimal, self-contained stand-in for `data/universe.db` — the owner's real
symbol master, ~22,000 rows, never committed to this repo and absent in CI.

WHY THIS EXISTS
---------------
`tests/conftest.py` copies the real `universe.db` into the test's redirected
data directory when the developer running the suite happens to have one
(it is a read-only reference dataset, not runtime state the tests could
corrupt). CI has no such file, so every test that resolves a symbol's
identity — `src/core/identity.py`, `src/data/universe.py`, `src/data/currency.py`
— was finding an EMPTY master and failing closed exactly as designed, which
is correct behaviour but not what those tests are checking.

So this file is the other half: real, publicly known instruments — the exact
ones named in `src/core/identity.py`'s own defect writeups and in the test
docstrings that pin them — with correct venues, currencies and (where the
tests check it) ISINs. Nothing here is fabricated data standing in for a
count; it is a small, true slice of the real universe, not a scaled-down
approximation of it.

WHAT IT DELIBERATELY DOES NOT COVER
------------------------------------
A handful of tests assert facts about the PRODUCTION master's size —
`idt.audit()["symbols"] > 20000` — and no fixture can satisfy that without
claiming a false count. Rather than leave CI permanently failing on an
assertion it can never meet, those tests read `tests/conftest.py`'s
`universe_reference` fixture and check the PRODUCTION claim only when a real
`universe.db` was actually copied in; against this fallback they instead
assert the fixture's own exact, independently known row count (16 — see
`len(ROWS)` above). Same audit contract, two honestly different datasets.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

#: (symbol, yahoo, name, exchange, asset_class, isin, sector, currency)
#: `mcap`, `industry`, `updated_at`, `has_fno` and `lot_size` are not read by
#: any test this fixture serves, so they are left at NULL / 0.
ROWS: list[tuple] = [
    # ── the four display/provider collisions identity.py's own defect
    # writeup names (BA, AAL, JD, EDV): a US ticker the signal pipeline uses,
    # and a London instrument that owns the same display row in the master.
    ("BA", "BA.L", "BAE Systems plc", "LSE", "equity", None, None, "GBP"),
    ("AAL", "AAL.L", "Anglo American plc", "LSE", "equity", None, None, "GBP"),
    ("JD", "JD.L", "JD Sports Fashion Plc", "LSE", "equity", None, None, "GBP"),
    ("EDV", "EDV.L", "Endeavour Mining plc", "LSE", "equity", None, None, "GBP"),

    # ── AAPL: an unambiguous US equity. Sector is real (Yahoo Finance files
    # Apple under "Technology") and is what research/attribution.py maps to
    # the XLK sector-ETF proxy.
    ("AAPL", "AAPL", "Apple Inc", "US", "equity", None, "Technology", None),

    # ── MRK / MRK.DE: Merck & Co (NYSE) and Merck KGaA (Xetra) are two
    # different real companies sharing a bare root — scripts/migrate_identity.py's
    # own comments use this exact pair as its worked example of a genuine
    # ambiguity resolved by price evidence, not by guessing.
    ("MRK", "MRK", "Merck & Co Inc", "US", "equity", None, None, None),
    ("MRK.DE", "MRK.DE", "Merck KGaA", "XETRA", "equity", None, None, "EUR"),

    # ── GBPINR=X: an FX cross, filed as FX/forex — the defect being pinned is
    # a resolver that used to file every non-equity suffix as US/equity.
    # Included twice under the real alias pattern identity.py's own docstring
    # names ("a bare form of a suffixed provider symbol (GBPINR / GBPINR=X)"):
    # one row keyed on the bare pair, one on the suffixed form — the same
    # instrument, not two.
    ("GBPINR", "GBPINR=X", "British Pound / Indian Rupee", "FX", "forex", None, None, "INR"),
    ("GBPINR=X", "GBPINR=X", "GBP/INR", "FX", "forex", None, None, "INR"),

    # ── AI: identity.py's own example of an ambiguous root. C3.ai trades as
    # AI on the NYSE; Air Liquide trades as AI on Euronext Paris. Real
    # companies, real collision.
    ("AI", "AI", "C3.ai Inc", "US", "equity", None, None, None),
    ("AI.PA", "AI.PA", "Air Liquide SA", "EURONEXT_PA", "equity", None, None, "EUR"),

    # ── the two BSE renames identity.py's `provider_symbol_duplicates()`
    # docstring names verbatim, ISINs included: one permanent BSE scrip code
    # carrying two display rows because the company was renamed between
    # bhavcopy loads and the old display symbol was never deleted.
    ("PRATIKSHA", "531257.BO", "PRATIKSHA CHEMICALS LTD.", "BSE", "equity",
     "INE530D01012", None, "INR"),
    ("VELLORA", "531257.BO", "VELLORA IMPACT LIMITED", "BSE", "equity",
     "INE530D01012", None, "INR"),
    ("ARYAVAN", "539455.BO", "ARYAVAN ENTERPRISE LTD", "BSE", "equity",
     "INE360S01012", None, "INR"),
    ("ECOFINITY", "539455.BO", "ECOFINITY ATOMIX LIMITED", "BSE", "equity",
     "INE360S01012", None, "INR"),

    # ── SOL-USD: a crypto pair, for the grading-path test that proves a
    # genuine 3x rally is not mistaken for a venue mismatch.
    ("SOL-USD", "SOL-USD", "Solana USD", "CRYPTO", "crypto", None, None, "USD"),
]

_SCHEMA = """
CREATE TABLE symbols (
    symbol      TEXT PRIMARY KEY,
    yahoo       TEXT NOT NULL,
    name        TEXT,
    exchange    TEXT,
    asset_class TEXT,
    isin        TEXT,
    sector      TEXT,
    industry    TEXT,
    mcap        REAL,
    updated_at  TEXT,
    has_fno     INTEGER DEFAULT 0,
    lot_size    INTEGER,
    currency    TEXT
);
CREATE INDEX idx_symbols_name   ON symbols(name);
CREATE INDEX idx_symbols_sector ON symbols(sector);
"""


def build(path: Path) -> int:
    """Write the fixture symbol master to `path`, overwriting whatever is
    there. `path` is always inside the test session's throwaway data
    directory — never the owner's real `data/universe.db`. Returns the
    number of rows written, so a caller can assert against it rather than
    re-stating the literal count by hand."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    with sqlite3.connect(str(path)) as conn:
        conn.executescript(_SCHEMA)
        conn.executemany(
            """INSERT INTO symbols
               (symbol, yahoo, name, exchange, asset_class, isin, sector, currency)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            ROWS,
        )
        conn.commit()
    return len(ROWS)
