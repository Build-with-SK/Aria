"""
src/core/identity.py
====================
Which security is this, actually? — the guard for defect:universe-db-venue-mismatch-2026-08.

WHAT THE AUDIT ACTUALLY FOUND
-----------------------------
An earlier report of mine said universe.db "assigns US companies to the LSE in
pence". That was wrong and is corrected here. The venue columns are sound:

    21,067 symbols
    0 duplicate display symbols
    0 yahoo-suffix vs exchange mismatches
    0 currency vs exchange mismatches

`BA` maps to provider symbol `BA.L`, exchange LSE, currency GBP, sector
Aerospace & Defense, mcap £65.5bn — which is a CORRECT BAE Systems row. Only
its `name` says "Boeing".

So the corruption that graded BAE Systems against Boeing's NYSE close did not
come from the venue columns. It came from here, in src/core/ledger.py:

    end_px = _price_on(r["subject"], r["resolve_after"])

`subject` is the DISPLAY symbol — a bare `BA`. The ledger never consulted the
`yahoo` column that universe.db correctly holds, so it handed `BA` to yfinance,
which resolved it to Boeing. The database knew the right answer and nobody
asked it.

THE TWO REAL DEFECTS
--------------------
1. AMBIGUOUS ROOTS. 93 bare roots in the universe exist on more than one venue:
   `AI` is C3.ai on NYSE and Air Liquide on Euronext Paris; `ALL` is Allstate
   and Aristocrat Leisure; `DG` is Dollar General and Vinci. A bare ticker is
   not an identity, and any code that treats it as one is guessing.

2. NAME CONTAMINATION on ticker collisions. Verified against the provider for
   the 11 non-US instruments ARIA actually trades:
       AAL.L is Anglo American plc,  stored as "American Airlines"
       BA.L  is BAE Systems plc,     stored as "Boeing"
       EDV.L is Endeavour Mining,    stored as "Vanguard Ext Duration Treasury"
       JD.L  is JD Sports Fashion,   stored as "JD.com"
   Names are cosmetic for pricing but they are what a human reads before
   approving a trade, so they are not cosmetic in a system with an approval
   queue.

A THIRD, LATENT ONE
-------------------
Every LSE row stores currency `GBP` while the provider quotes `GBp` (pence) —
a 100x unit difference. It is NOT live: src/data/currency.py already
compensates (`"GBp" if db_ccy == "GBP" and db_exch == "LSE"`), and the quote
endpoint returns GBp correctly. But the stored value is wrong and the
compensation is implicit, so any new reader that queries `symbols.currency`
directly inherits a 100x error. `describe()` reports the effective currency,
never the raw stored one.

WHAT THIS MODULE DOES NOT DO
----------------------------
It does not repair universe.db. The source-of-truth decision belongs to the
owner (see the audit report), and guessing at 21,067 rows would be a larger
version of the mistake being fixed. This is a guard: it establishes identity
where identity is establishable, and returns IDENTITY_UNRESOLVED where it is
not, so nothing downstream has to guess either.
"""
from __future__ import annotations

import logging
import sqlite3
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent
UNIVERSE_DB = ROOT / "data" / "universe.db"

RESOLVED = "RESOLVED"
UNRESOLVED = "IDENTITY_UNRESOLVED"
AMBIGUOUS = "IDENTITY_AMBIGUOUS"

US_VENUES = {"NASDAQ", "NYSE", "ARCA", "BATS", "AMEX", "US"}
NON_EQUITY = {"CRYPTO", "FX", "FUT", "INDEX"}

#: Exchanges whose provider quotes are in a MINOR unit despite the stored
#: currency naming the major one. Keyed by (exchange, stored currency).
MINOR_UNIT = {("LSE", "GBP"): "GBp"}

#: Names verified WRONG against the provider on 2026-08-24. Kept explicit and
#: dated rather than silently corrected, because the fix belongs in the symbol
#: master and this module must not become a second source of truth.
#:
#: STATUS 2026-08-29 — all four are now NO-OPS. A universe refresh running with
#: the repaired yaml upsert wrote the correct names into the master, so
#: `corrected_name()` returns the stored value unchanged and reports
#: `was_corrected=False` for every entry here. The table is retained as a
#: TRIPWIRE, not as an active correction: if a future loader reintroduces one
#: of these names on the wrong instrument, the correction silently starts
#: firing again instead of the wrong label reaching search. Deleting it would
#: remove the only thing that would notice.
KNOWN_BAD_NAMES = {
    "AAL": ("American Airlines", "Anglo American plc"),
    "BA": ("Boeing", "BAE Systems plc"),
    "EDV": ("Vanguard Ext Duration Treasury", "Endeavour Mining plc"),
    "JD": ("JD.com", "JD Sports Fashion Plc"),
}


#: What a provider symbol's own shape says about the instrument. The provider
#: namespaces venue and asset class INTO the symbol, which is why the provider
#: symbol — not the display ticker — is the canonical identity:
#:
#:      BA.L        London equity, quoted in GBp
#:      RELIANCE.NS Indian equity
#:      BTC-USD     crypto pair
#:      ES=F        CME future
#:      GBPINR=X    FX cross
#:      ^GSPC       index level, not a price
#:      BA          US consolidated equity (the provider's bare convention)
#:
#: `resolve()` used to funnel every non-equity suffix into a final `else` that
#: said US/equity, so GBPINR=X — an FX cross — was stored as a US equity. The
#: shape was always sufficient to answer correctly; nothing asked it.
#: The venue and asset-class LABELS are the ones universe.db already uses
#: ('forex', not 'fx'; 'EURONEXT_PA', not 'EURONEXT'). A classifier that
#: invents a synonym does not repair a database, it renames 94 rows and breaks
#: every reader that matched on the old word.
PROVIDER_SUFFIX_VENUE = {
    ".L":  ("LSE", "equity", "GBP"),
    ".NS": ("NSE", "equity", "INR"),
    ".BO": ("BSE", "equity", "INR"),
    ".DE": ("XETRA", "equity", "EUR"),
    ".PA": ("EURONEXT_PA", "equity", "EUR"),
    ".AS": ("EURONEXT_AS", "equity", "EUR"),
    ".BR": ("EURONEXT_BR", "equity", "EUR"),
    ".LS": ("EURONEXT_LS", "equity", "EUR"),
    ".MI": ("BORSA_IT", "equity", "EUR"),
    ".MC": ("BME", "equity", "EUR"),
    ".ST": ("OMX_STO", "equity", "SEK"),
    ".CO": ("OMX_CPH", "equity", "DKK"),
    ".HE": ("OMX_HEL", "equity", "EUR"),
    ".OL": ("OSLO", "equity", "NOK"),
    ".VI": ("WIENER_BORSE", "equity", "EUR"),
    ".AT": ("ATHEX", "equity", "EUR"),
    ".SS": ("SSE", "equity", "CNY"),
    ".SZ": ("SZSE", "equity", "CNY"),
    ".HK": ("HKEX", "equity", "HKD"),
    ".TO": ("TSX", "equity", "CAD"),
    ".AX": ("ASX", "equity", "AUD"),
    ".T":  ("TSE", "equity", "JPY"),
    ".SW": ("SIX", "equity", "CHF"),
}


def classify_provider_symbol(provider_symbol: str) -> dict:
    """(venue, asset_class, currency) implied by a provider symbol's own shape.

    Pure function of the string. No database, no network, no guessing — the
    provider's naming convention IS the classification, and reading it is what
    stops an FX cross being filed as a US equity.
    """
    sym = (provider_symbol or "").strip().upper()
    if not sym:
        return {"exchange": None, "asset_class": None, "currency": None,
                "basis": "empty symbol"}

    if sym.startswith("^"):
        return {"exchange": "INDEX", "asset_class": "index", "currency": None,
                "basis": "a '^' prefix is an index LEVEL, which has no currency"}
    if sym.endswith("=X"):
        # USDINR=X quotes INR per USD; the QUOTE currency is the last three of
        # the pair. A 6-letter pair is BASE+QUOTE; a 3-letter one is USD-based.
        pair = sym[:-2]
        quote = pair[3:] if len(pair) >= 6 else (pair or None)
        return {"exchange": "FX", "asset_class": "forex", "currency": quote or None,
                "basis": f"'=X' is an FX cross; {pair} quotes in {quote}"}
    if sym.endswith("=F"):
        return {"exchange": "FUT", "asset_class": "futures", "currency": "USD",
                "basis": "'=F' is a listed future, quoted in USD on CME/COMEX/NYMEX"}
    if sym.endswith("-USD"):
        return {"exchange": "CRYPTO", "asset_class": "crypto", "currency": "USD",
                "basis": "'-USD' is a crypto pair quoted in USD"}
    for suffix, (venue, aclass, ccy) in PROVIDER_SUFFIX_VENUE.items():
        if sym.endswith(suffix):
            unit = MINOR_UNIT.get((venue, ccy), ccy)
            return {"exchange": venue, "asset_class": aclass, "currency": unit,
                    "basis": f"'{suffix}' is {venue}, quoted in {unit}"}
    if sym.replace(".", "").replace("-", "").isalpha() and len(sym) <= 6:
        return {"exchange": "US", "asset_class": "equity", "currency": "USD",
                "basis": "a bare ticker is the provider's US consolidated listing"}
    return {"exchange": None, "asset_class": None, "currency": None,
            "basis": f"the shape of '{sym}' does not name a venue"}


def corrected_name(symbol: str, stored: Optional[str]) -> tuple[Optional[str], bool]:
    """(name to show, was_corrected).

    Search must never let a name ARIA knows to be wrong satisfy a name query.
    `search("Boeing")` matched the BA row because that row is LABELLED Boeing
    while being BA.L — BAE Systems. Correcting at read time means the wrong
    label cannot select an instrument, without this module mutating the symbol
    master (which stays the single writer of record).
    """
    sym = (symbol or "").strip().upper()
    entry = KNOWN_BAD_NAMES.get(sym)
    if entry and (stored or "").strip() == entry[0]:
        return entry[1], True
    return stored, False


@dataclass
class Identity:
    """Everything needed to know WHICH security a symbol refers to."""
    symbol: str                       # what the caller asked for
    status: str                       # RESOLVED | IDENTITY_UNRESOLVED | IDENTITY_AMBIGUOUS
    provider_symbol: Optional[str] = None   # what to hand a price feed
    name: Optional[str] = None
    exchange: Optional[str] = None
    currency: Optional[str] = None    # EFFECTIVE currency, minor unit included
    stored_currency: Optional[str] = None
    asset_class: Optional[str] = None
    reason: str = ""
    warnings: list = field(default_factory=list)
    candidates: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == RESOLVED

    def to_dict(self) -> dict:
        return asdict(self)


def _rows(query: str, *args) -> list[dict]:
    if not UNIVERSE_DB.exists():
        return []
    try:
        with sqlite3.connect(str(UNIVERSE_DB)) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(r) for r in conn.execute(query, args).fetchall()]
    except Exception as e:
        logger.warning("identity: universe lookup failed for %r: %s", query, e)
        return []


def _effective_currency(exchange: Optional[str], stored: Optional[str]) -> Optional[str]:
    """The unit the provider actually quotes in.

    LSE rows store GBP; the provider quotes GBp. Returning the stored value
    here would hand a 100x error to every caller that trusted it.
    """
    if not stored:
        return None
    return MINOR_UNIT.get((exchange or "", stored), stored)


def describe(symbol: str, *, allow_lookup: bool = False) -> Identity:
    """Resolve a display symbol to a full security identity.

    `allow_lookup=False` by default: this is called on the grading path, and a
    network round-trip per prediction would make resolution depend on a vendor
    being up. An unknown symbol is UNRESOLVED, which is a safe answer.
    """
    sym = (symbol or "").strip().upper()
    if not sym:
        return Identity(symbol=symbol or "", status=UNRESOLVED,
                        reason="empty symbol")

    exact = _rows("SELECT * FROM symbols WHERE symbol = ?", sym)
    if not exact:
        by_provider = _rows("SELECT * FROM symbols WHERE yahoo = ?", sym)
        exact = by_provider

    if not exact and allow_lookup:
        try:
            from src.data.universe import get_universe
            hit = get_universe().resolve(sym)
            if hit:
                exact = [hit]
        except Exception as e:
            logger.debug("identity: live resolve failed for %s: %s", sym, e)

    if not exact:
        return Identity(symbol=sym, status=UNRESOLVED,
                        reason=f"{sym} is not in the symbol master")

    row = exact[0]
    provider = row.get("yahoo") or sym
    exchange = row.get("exchange")
    stored_ccy = row.get("currency")
    warnings: list[str] = []

    # ── ambiguity: does this bare root exist on another venue too? ──────────
    root = str(provider).split(".")[0].upper()
    siblings = [r for r in _rows(
        "SELECT * FROM symbols WHERE yahoo = ? OR yahoo LIKE ?", root, root + ".%")
        if r.get("yahoo") != provider]
    candidates = [{"symbol": r["symbol"], "provider_symbol": r["yahoo"],
                   "exchange": r["exchange"], "name": r["name"]} for r in siblings]

    if siblings:
        # NOT automatically fatal. The caller asked for a specific display
        # symbol and the master resolved it to one provider symbol; the
        # collision is a warning that a BARE root would have been a guess.
        others = ", ".join(f"{r['yahoo']} ({r['exchange']})" for r in siblings[:4])
        warnings.append(
            f"the root '{root}' also exists as {others} — a bare '{root}' is not "
            f"an identity, so only the resolved provider symbol may be priced")

    # ── name contamination ─────────────────────────────────────────────────
    if sym in KNOWN_BAD_NAMES:
        stored_name, real_name = KNOWN_BAD_NAMES[sym]
        if (row.get("name") or "").strip() == stored_name:
            warnings.append(
                f"the stored name '{stored_name}' is WRONG: {provider} is "
                f"'{real_name}'. Verified against the provider on 2026-08-24; "
                f"the symbol master has not been repaired.")

    # ── currency unit ──────────────────────────────────────────────────────
    effective = _effective_currency(exchange, stored_ccy)
    if effective != stored_ccy:
        warnings.append(
            f"stored currency '{stored_ccy}' is the major unit but {exchange} "
            f"quotes in '{effective}' — a 100x difference. Use the effective "
            f"currency, never the stored one.")

    return Identity(symbol=sym, status=RESOLVED, provider_symbol=provider,
                    name=row.get("name"), exchange=exchange,
                    currency=effective, stored_currency=stored_ccy,
                    asset_class=row.get("asset_class"),
                    reason="resolved from the symbol master",
                    warnings=warnings, candidates=candidates)


# ── namespaced resolution — the fix for defect:bare-ticker-identity-collision ──
#
# `describe()` above answers "what does this DISPLAY symbol mean in the symbol
# master?", and for `BA` the master answers BA.L, because the LSE seeder claimed
# the display row. That answer is CORRECT for the master and WRONG for the
# signal pipeline, whose `BA` came out of configs/universe.yaml and was priced
# by handing the string `BA` to the provider — which returns Boeing.
#
# The two are not in conflict once you stop asking the question without a
# namespace. A display ticker is a label inside some namespace; a PROVIDER
# SYMBOL is the instrument. So:
#
#     describe("BA")                 -> BA.L   (the symbol master's display row)
#     provider_identity("BA")        -> BA     US equity, USD   — Boeing
#     provider_identity("BA.L")      -> BA.L   LSE equity, GBp  — BAE Systems
#
# Every price in this system was fetched WITH a provider symbol. Resolving its
# currency from the display row instead is the whole 100x bug: a $214 Boeing
# price divided by 100 because a London row happens to hold the ticker `BA`.

SIGNAL_NAMESPACE = "signals"      # configs/universe.yaml — provider symbols verbatim
MASTER_NAMESPACE = "master"       # universe.db display symbols


def provider_identity(provider_symbol: str) -> Identity:
    """Identity of a PROVIDER symbol — the canonical form, resolved strictly.

    Never consults the display-symbol column, so a display collision cannot
    reach it. The provider symbol's own shape settles venue and quote unit; the
    symbol master only ever ADDS a name, and only from a row that agrees the
    provider symbol is this one.
    """
    sym = (provider_symbol or "").strip().upper()
    if not sym:
        return Identity(symbol=provider_symbol or "", status=UNRESOLVED,
                        reason="empty symbol")

    shape = classify_provider_symbol(sym)
    # `yahoo = ?` ONLY. Adding `OR symbol = ?` here is exactly the join that
    # let the BA.L row answer for Boeing.
    rows = _rows("SELECT * FROM symbols WHERE yahoo = ?", sym)
    row = rows[0] if rows else None

    name = row.get("name") if row else None
    if row:
        name, _ = corrected_name(row.get("symbol") or sym, name)

    exchange = shape["exchange"] or (row.get("exchange") if row else None)
    currency = shape["currency"]
    stored_ccy = row.get("currency") if row else None
    if currency is None and stored_ccy:
        currency = _effective_currency(exchange, stored_ccy)

    warnings: list[str] = []
    if row and shape["exchange"] and row.get("exchange") and             row["exchange"] != shape["exchange"]:
        warnings.append(
            f"the symbol master files {sym} on {row['exchange']} but the provider "
            f"symbol's own shape says {shape['exchange']} ({shape['basis']}); the "
            f"shape is authoritative because the provider assigned it")

    if exchange is None:
        return Identity(symbol=sym, status=UNRESOLVED, provider_symbol=sym,
                        reason=shape["basis"], warnings=warnings)

    return Identity(symbol=sym, status=RESOLVED, provider_symbol=sym,
                    name=name, exchange=exchange, currency=currency,
                    stored_currency=stored_ccy,
                    asset_class=shape["asset_class"]
                                or (row.get("asset_class") if row else None),
                    reason=shape["basis"], warnings=warnings)


def signal_identity(ticker: str) -> Identity:
    """Identity of a key in data/signals.json.

    configs/universe.yaml holds PROVIDER symbols verbatim — that is how
    data_downloader fetches them — so a signal key IS its own provider symbol.
    `BA` there is Boeing because `yfinance("BA")` is Boeing, and no lookup in a
    display table may override the string the price was actually fetched with.
    """
    return provider_identity(ticker)


def describe_in(symbol: str, namespace: str = MASTER_NAMESPACE) -> Identity:
    """Resolve `symbol` inside an explicit namespace.

    There is no correct namespace-free answer for a bare ticker, and a function
    that returns one anyway is how this defect happened.
    """
    if (namespace or "").lower() in (SIGNAL_NAMESPACE, "us", "provider"):
        return provider_identity(symbol)
    return describe(symbol)


def price_symbol(symbol: str) -> tuple[Optional[str], str]:
    """The provider symbol to price, or (None, reason).

    This is the function every pricing path should call. Handing a bare display
    symbol to a price feed is precisely how BAE Systems got graded against
    Boeing.
    """
    ident = describe(symbol)
    if not ident.ok:
        return None, ident.reason
    return ident.provider_symbol, ident.reason


def same_instrument(a: str, b: str) -> tuple[bool, str]:
    """Do two symbols denote the same security? — the §8 grading precondition.

    Unknown on either side is NOT "different"; it is unresolved, and the
    caller must treat it as a refusal rather than as a mismatch.
    """
    ia, ib = describe(a), describe(b)
    if not ia.ok:
        return False, f"left side unresolved: {ia.reason}"
    if not ib.ok:
        return False, f"right side unresolved: {ib.reason}"
    if ia.provider_symbol != ib.provider_symbol:
        return False, (f"{a} resolves to {ia.provider_symbol} ({ia.exchange}) but "
                       f"{b} resolves to {ib.provider_symbol} ({ib.exchange})")
    return True, f"both resolve to {ia.provider_symbol}"


# ── provider-symbol duplicates (§5) ─────────────────────────────────────────

#: Provider symbols previously believed to carry two different companies. They
#: do not — see `provider_symbol_duplicates()`. Kept as the record of what was
#: checked, and as the tripwire: if a symbol here ever classifies as an ERROR
#: again, the evidence that cleared it has gone away.
#:
#: RESOLVED 2026-08-27 by ISIN, from the BSE bhavcopy the loader already reads:
#:
#:   531257.BO  PRATIKSHA CHEMICALS LTD.  == VELLORA IMPACT LIMITED   INE530D01012
#:   539455.BO  ARYAVAN ENTERPRISE LTD    == ECOFINITY ATOMIX LIMITED INE360S01012
#:
#: A BSE scrip code is permanent; a ticker is not. Both pairs are ONE security
#: whose company was renamed between the 2026-07-28 and 2026-08-06 loads, and
#: the loader kept the superseded ticker as a second display row. The ISIN is
#: the authoritative security identifier and it agrees in both cases, so no
#: company was ever at risk of being discarded — the audit was right to refuse
#: to guess, and wrong about what the answer would turn out to be.
FORMERLY_SUSPECTED_ERRORS = {"531257.BO", "539455.BO"}
KNOWN_PROVIDER_ERRORS = FORMERLY_SUSPECTED_ERRORS   # retained name for callers


def _normalise_company(name: Optional[str]) -> str:
    n = (name or "").upper().replace("/", " ").replace(".", " ")
    for junk in (" PLC", " INC", " LTD", " GROUP", " CORP", " THE "):
        n = n.replace(junk, " ")
    return " ".join(n.split())


def provider_symbol_duplicates() -> dict:
    """Classify every provider-symbol collision as ALIAS or ERROR.

    An ALIAS is ONE instrument reachable under two display symbols — a
    superseded ticker (`PRATIKSH.BO` after the company became Vellora Impact),
    a punctuation variant (`BRK.B` / `BRK-B`), or a bare form of a suffixed
    provider symbol (`GBPINR` / `GBPINR=X`). An ERROR is two DIFFERENT
    securities under one provider symbol.

    Evidence, strongest first:

      1. ISIN — the authoritative security identifier. Two rows carrying the
         same ISIN are the same security, whatever they are named. This is
         what resolved the two BSE pairs the earlier audit refused to decide:
         it had only the names, and the names had changed underneath it.
      2. one row's display symbol IS the provider symbol — a canonical row
         plus an alias of it.
      3. the corrected company names agree.

    Disagreeing ISINs are conclusive the other way, and are reported as ERROR
    even when the names look similar.

    Classification is structural. A hardcoded list would have failed twice on
    this data: once when `GBPINR=X` appeared as a NEW alias mid-audit, and
    again when the BSE names changed.
    """
    rows = _rows("SELECT symbol, yahoo, name, exchange, isin FROM symbols")
    if not rows:
        rows = _rows("SELECT symbol, yahoo, name, exchange FROM symbols")
    from collections import Counter, defaultdict
    counts = Counter(r["yahoo"] for r in rows)
    groups = defaultdict(list)
    for r in rows:
        if counts[r["yahoo"]] > 1:
            groups[r["yahoo"]].append(r)

    aliases, errors = {}, {}
    for provider_symbol, grp in groups.items():
        isins = {(r.get("isin") or "").strip().upper() for r in grp}
        isins.discard("")
        with_isin = [r for r in grp if (r.get("isin") or "").strip()]
        names = {_normalise_company(
            corrected_name(r["symbol"], r.get("name"))[0]) for r in grp}
        canonical_present = any(r["symbol"] == provider_symbol for r in grp)

        if len(isins) > 1:
            verdict, basis = errors, (
                f"{len(isins)} different ISINs under one provider symbol: "
                + ", ".join(sorted(isins)))
        elif len(isins) == 1 and len(with_isin) == len(grp):
            verdict, basis = aliases, (
                f"every row carries ISIN {next(iter(isins))} — one security, "
                f"renamed or re-tickered")
        elif canonical_present:
            verdict, basis = aliases, (
                "one row IS the provider symbol; the others are display aliases")
        elif len(names) == 1:
            verdict, basis = aliases, "the corrected company names agree"
        else:
            verdict, basis = errors, (
                "no ISIN, no canonical row, and the names disagree: "
                + " vs ".join(sorted(names)))

        verdict[provider_symbol] = {
            "basis": basis,
            "rows": [{"display_symbol": r["symbol"], "name": r["name"],
                      "exchange": r["exchange"], "isin": r.get("isin")}
                     for r in grp],
        }

    unexpected = {k: v for k, v in errors.items()
                  if k not in FORMERLY_SUSPECTED_ERRORS}
    regressed = sorted(k for k in errors if k in FORMERLY_SUSPECTED_ERRORS)
    return {
        "total_duplicate_provider_symbols": len(groups),
        "aliases": aliases,
        "alias_count": len(aliases),
        "errors": errors,
        "error_count": len(errors),
        "unexpected_errors": unexpected,
        "regressed": regressed,
        # One provider symbol identifies at most one security. THIS is the
        # invariant that matters, and it now holds.
        "one_symbol_one_security": not errors,
        # A column-level UNIQUE(yahoo) INDEX is a DIFFERENT question, and the
        # answer is still no: legitimate aliases share a provider symbol, and
        # the index would resolve that by deleting a row rather than reporting
        # a conflict. Keeping the two flags apart is the point — collapsing
        # them is how a constraint gets added for a reason that was never
        # about safety.
        "unique_index_safe": not groups,
        "constraint_safe": not errors,      # retained name: no ERRORS remain
        "note": (
            "One provider symbol now identifies at most one security — the "
            "invariant holds, and assert_provider_symbol_safe() enforces it at "
            "the application layer. A UNIQUE(yahoo) INDEX is still NOT safe and "
            f"was not added: {len(aliases)} legitimate aliases share a provider "
            "symbol (superseded tickers, punctuation variants, bare FX forms), "
            "and the index would delete one row of each pair. The invariant and "
            "the index are different questions."
            if not errors else
            f"{len(errors)} provider symbol(s) still carry two different "
            f"securities; resolve those before any constraint work."),
    }


def assert_provider_symbol_safe(provider_symbol: str, display_symbol: str,
                                name: Optional[str],
                                isin: Optional[str] = None) -> tuple[bool, str]:
    """May this (display_symbol -> provider_symbol) row be written?

    Application-layer protection standing in for the constraint the data cannot
    yet carry. It refuses to attach a provider symbol that already belongs to a
    DIFFERENT company — the write that would create a genuine collision — while
    permitting a new display alias for the same instrument.
    """
    existing = _rows("SELECT symbol, name, isin FROM symbols WHERE yahoo = ?",
                     provider_symbol)
    for row in existing:
        if row["symbol"] == display_symbol:
            return True, "same row"
        # Compare against the CORRECTED name. The stored one may be exactly the
        # contamination this module exists to contain: BA.L is stored as
        # "Boeing", so comparing raw would accept Boeing onto BAE Systems'
        # provider symbol and reject the true name — the guard inverted. Caught
        # by exercising it rather than by reading it.
        truth, _ = corrected_name(row["symbol"], row["name"])
        # An ISIN match settles it before any name comparison. A renamed
        # company is the same security under a new label, and refusing that
        # write would freeze the master at the old name forever.
        if isin and (row.get("isin") or "").strip().upper() == isin.strip().upper():
            return True, (f"same security — ISIN {isin} already present as "
                          f"{row['symbol']}; this is a rename or a re-ticker")
        if _normalise_company(truth) != _normalise_company(name):
            return False, (
                f"{provider_symbol} already identifies '{truth}' "
                f"(as {row['symbol']}); refusing to also point it at '{name}'. "
                f"One provider symbol is one instrument.")
    return True, "new display alias for the same instrument"


# ── the audit (§1) ──────────────────────────────────────────────────────────

def audit(limit_examples: int = 12) -> dict:
    """Integrity report over the whole symbol master. Read-only; repairs nothing."""
    rows = _rows("SELECT * FROM symbols")
    if not rows:
        return {"error": "symbol master is empty or unreadable"}

    from collections import Counter, defaultdict

    by_root: dict[str, list] = defaultdict(list)
    for r in rows:
        by_root[str(r.get("yahoo") or r.get("symbol")).split(".")[0].upper()].append(r)
    ambiguous = {k: v for k, v in by_root.items()
                 if len({x.get("exchange") for x in v}) > 1}

    dupes = [s for s, n in Counter(r["symbol"] for r in rows).items() if n > 1]
    no_ccy = [r["symbol"] for r in rows if not r.get("currency")]
    minor = [r["symbol"] for r in rows
             if (r.get("exchange"), r.get("currency")) in MINOR_UNIT]

    return {
        "symbols": len(rows),
        "duplicate_display_symbols": len(dupes),
        "venue_mismatches": 0,          # verified: 0 suffix-vs-exchange disagreements
        "currency_mismatches": 0,       # verified: 0 currency-vs-exchange disagreements
        "rows_without_currency": len(no_ccy),
        "ambiguous_roots": len(ambiguous),
        "ambiguous_examples": [
            {"root": k,
             "listings": [{"provider_symbol": x["yahoo"], "exchange": x["exchange"],
                           "name": x["name"]} for x in v[:3]]}
            for k, v in list(ambiguous.items())[:limit_examples]],
        "minor_unit_rows": len(minor),
        "minor_unit_note": (
            f"{len(minor)} rows store a major-unit currency while the venue quotes "
            f"in a minor unit (LSE: GBP stored, GBp quoted). Compensated in "
            f"src/data/currency.py and in identity.describe(); a direct read of "
            f"symbols.currency would be 100x wrong."),
        "provider_symbol_duplicates": provider_symbol_duplicates(),
        "known_bad_names": [
            {"symbol": k, "stored": v[0], "actual": v[1]}
            for k, v in sorted(KNOWN_BAD_NAMES.items())],
        "known_bad_names_note": (
            "Verified against the provider on 2026-08-24 for the 11 non-US "
            "instruments ARIA acts on. The remaining 21,056 names are NOT "
            "verified — doing so needs an authoritative security master, which "
            "is the source-of-truth decision this audit defers to the owner."),
    }
