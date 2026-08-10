"""
universe.py
===========
GTA-style streaming market data layer ("the world never loads all at once").

Three levels of detail (LOD), like an open-world game streams its map:

  Tier 0 — SYMBOL INDEX   : every listed asset ARIA knows about (all NSE
                            equities + the global configs/universe.yaml
                            universe) kept permanently in a tiny SQLite DB.
                            Instant search, zero network.
  Tier 1 — QUOTE CARD     : last price / day change for a symbol, fetched
                            on demand in small chunks and cached with a TTL
                            (the "map tile" the player is standing on).
  Tier 2 — FULL DOSSIER   : 5y price history + fundamentals for ONE symbol,
                            cached on disk for 24h. Requesting a dossier
                            quietly prefetches its sector peers in the
                            background (the "next street over").

Nothing here ever bulk-downloads the whole market. The index is cheap
metadata; heavy data streams in only around where the user is "standing".
"""

from __future__ import annotations

import csv
import io
import json
import logging
import sqlite3
import threading
import time
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent
DB_PATH = ROOT / "data" / "universe.db"
DOSSIER_DIR = ROOT / "data" / "cache" / "dossiers"
UNIVERSE_YAML = ROOT / "configs" / "universe.yaml"

NSE_EQUITY_LIST_URL = "https://archives.nseindia.com/content/equities/EQUITY_L.csv"
# Full US market — NASDAQ Trader public symbol directory (pipe-delimited, no key).
NASDAQ_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
NASDAQ_OTHER_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"  # NYSE/AMEX/Arca/BATS
# Full BSE — official UDiFF equity bhavcopy (dated; we walk back a few days).
BSE_BHAV_URL = "https://www.bseindia.com/download/BhavCopy/Equity/BhavCopy_BSE_CM_0_0_0_{d:%Y%m%d}_F_0000.CSV"

QUOTE_TTL_MINUTES = 15        # tier-1 freshness
DOSSIER_TTL_HOURS = 24        # tier-2 freshness
QUOTE_CHUNK_SIZE = 50         # symbols per yfinance batch ("tile size")
PEER_PREFETCH_LIMIT = 3       # background dossiers warmed per request


class UniverseManager:
    """Owns the symbol index and the tiered caches. Thread-safe singleton."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._prefetch_inflight: set = set()
        DOSSIER_DIR.mkdir(parents=True, exist_ok=True)
        self._init_db()

    # ------------------------------------------------------------------
    # DB plumbing
    # ------------------------------------------------------------------
    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._lock, self._conn() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS symbols (
                    symbol      TEXT PRIMARY KEY,   -- display symbol (RELIANCE, AAPL, BTC-USD)
                    yahoo       TEXT NOT NULL,      -- yfinance ticker (RELIANCE.NS, AAPL)
                    name        TEXT,
                    exchange    TEXT,               -- NSE / US / CRYPTO / FX / FUT / INDEX
                    asset_class TEXT,               -- equity / etf / crypto / forex / futures / index / fixed_income / credit / commodity
                    isin        TEXT,
                    sector      TEXT,               -- filled in progressively as dossiers are fetched
                    industry    TEXT,
                    mcap        REAL,
                    updated_at  TEXT
                );
                CREATE TABLE IF NOT EXISTS quotes (
                    symbol      TEXT PRIMARY KEY,
                    price       REAL,
                    change_pct  REAL,
                    currency    TEXT,
                    fetched_at  TEXT
                );
                CREATE TABLE IF NOT EXISTS meta (
                    key   TEXT PRIMARY KEY,
                    value TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_symbols_name   ON symbols(name);
                CREATE INDEX IF NOT EXISTS idx_symbols_sector ON symbols(sector);
                """
            )
            # additive migrations (older DBs pre-date these columns)
            for ddl in ("ALTER TABLE symbols ADD COLUMN has_fno INTEGER DEFAULT 0",
                        "ALTER TABLE symbols ADD COLUMN lot_size INTEGER"):
                try:
                    conn.execute(ddl)
                except Exception:
                    pass  # column already exists

    def _meta_get(self, key: str) -> Optional[str]:
        with self._conn() as conn:
            row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
            return row["value"] if row else None

    def _meta_set(self, key: str, value: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO meta(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    # ------------------------------------------------------------------
    # Tier 0 — the symbol index
    # ------------------------------------------------------------------
    def refresh_index(self, force: bool = False) -> Dict[str, Any]:
        """(Re)build the symbol index. Cheap: a CSV + a YAML, no price data."""
        last = self._meta_get("index_refreshed_at")
        if last and not force:
            age = datetime.now() - datetime.fromisoformat(last)
            if age < timedelta(days=7):
                return {"status": "fresh", "refreshed_at": last, **self._counts()}

        nse_added = self._load_nse_equities()
        bse_added = self._load_bse_equities()
        us_added = self._load_us_full()
        lse_added = self._load_lse_equities()
        world_added = self._load_world_symbols()
        fx_added = self._load_fx_pairs()
        yaml_added = self._load_yaml_universe()
        fno_marked = self._load_nse_fno()
        self._meta_set("index_refreshed_at", datetime.now().isoformat())
        result = {
            "status": "refreshed",
            "nse_symbols": nse_added,
            "bse_symbols": bse_added,
            "us_symbols": us_added,
            "lse_symbols": lse_added,
            "world_symbols": world_added,
            "yaml_symbols": yaml_added,
            "fno_marked": fno_marked,
            **self._counts(),
        }
        logger.info(f"Universe index refreshed: {result}")
        return result

    # FTSE 100 (+ a few 250) London stocks. yfinance ticker = SYMBOL.L, priced in GBP.
    LSE_SYMBOLS = [
        ("HSBA", "HSBC Holdings"), ("BP", "BP"), ("SHEL", "Shell"), ("AZN", "AstraZeneca"),
        ("GSK", "GSK"), ("ULVR", "Unilever"), ("RIO", "Rio Tinto"), ("GLEN", "Glencore"),
        ("DGE", "Diageo"), ("BATS", "British American Tobacco"), ("REL", "RELX"),
        ("LSEG", "London Stock Exchange Group"), ("NG", "National Grid"), ("VOD", "Vodafone"),
        ("BARC", "Barclays"), ("LLOY", "Lloyds Banking Group"), ("NWG", "NatWest Group"),
        ("STAN", "Standard Chartered"), ("PRU", "Prudential"), ("AV", "Aviva"),
        ("LGEN", "Legal & General"), ("TSCO", "Tesco"), ("SBRY", "Sainsbury's"),
        ("NXT", "Next"), ("BRBY", "Burberry"), ("JD", "JD Sports Fashion"),
        ("IMB", "Imperial Brands"), ("RKT", "Reckitt Benckiser"), ("CPG", "Compass Group"),
        ("EXPN", "Experian"), ("SGE", "Sage Group"), ("AAF", "Airtel Africa"),
        ("ANTO", "Antofagasta"), ("FRES", "Fresnillo"), ("AAL", "Anglo American"),
        ("SSE", "SSE"), ("CNA", "Centrica"), ("BA", "BAE Systems"), ("RR", "Rolls-Royce"),
        ("SMIN", "Smiths Group"), ("MRO", "Melrose Industries"), ("IAG", "IAG (British Airways)"),
        ("EZJ", "easyJet"), ("WTB", "Whitbread"), ("IHG", "InterContinental Hotels"),
        ("HLMA", "Halma"), ("SPX", "Spirax Group"), ("CRDA", "Croda International"),
        ("MNDI", "Mondi"), ("SMDS", "DS Smith"), ("BNZL", "Bunzl"), ("DCC", "DCC"),
        ("FERG", "Ferguson"), ("HWDN", "Howden Joinery"), ("PSN", "Persimmon"),
        ("BDEV", "Barratt Developments"), ("TW", "Taylor Wimpey"), ("LAND", "Land Securities"),
        ("BLND", "British Land"), ("SGRO", "Segro"), ("UU", "United Utilities"),
        ("SVT", "Severn Trent"), ("PSON", "Pearson"), ("ITV", "ITV"), ("WPP", "WPP"),
        ("INF", "Informa"), ("AUTO", "Auto Trader"), ("RMV", "Rightmove"),
        ("OCDO", "Ocado"), ("ABF", "Associated British Foods"), ("ADM", "Admiral Group"),
        ("BEZ", "Beazley"), ("HSX", "Hiscox"), ("PHNX", "Phoenix Group"),
        ("SDR", "Schroders"), ("III", "3i Group"), ("SMT", "Scottish Mortgage Trust"),
        ("ENT", "Entain"), ("FLTR", "Flutter Entertainment"), ("CCH", "Coca-Cola HBC"),
        ("HIK", "Hikma Pharmaceuticals"), ("CTEC", "ConvaTec"), ("SN", "Smith & Nephew"),
        ("KGF", "Kingfisher"), ("MKS", "Marks & Spencer"), ("WEIR", "Weir Group"),
        ("MGGT", "Meggitt"), ("BME", "B&M European Value Retail"), ("FCIT", "F&C Investment Trust"),
        # ── FTSE 250 / wider London ────────────────────────────────────────
        ("BAB", "Babcock International"), ("QQ", "QinetiQ"), ("CHG", "Chemring"),
        ("SXS", "Spectris"), ("RTO", "Rentokil Initial"), ("DPLM", "Diploma"),
        ("IMI", "IMI"), ("ROR", "Rotork"), ("RSW", "Renishaw"), ("VCT", "Victrex"),
        ("ELM", "Elementis"), ("SYNT", "Synthomer"), ("JMAT", "Johnson Matthey"),
        ("TATE", "Tate & Lyle"), ("CRST", "Crest Nicholson"), ("BWY", "Bellway"),
        ("BKG", "Berkeley Group"), ("VTY", "Vistry Group"), ("GFRD", "Galliford Try"),
        ("MSLH", "Marshalls"), ("IBST", "Ibstock"), ("GRI", "Grainger"),
        ("UTG", "Unite Group"), ("BBOX", "Tritax Big Box"), ("SHB", "Shaftesbury Capital"),
        ("DLN", "Derwent London"), ("GPOR", "Great Portland Estates"), ("PHP", "Primary Health Properties"),
        ("HMSO", "Hammerson"), ("LMP", "LondonMetric Property"), ("SAFE", "Safestore"),
        ("BGEO", "Bank of Georgia"), ("TBCG", "TBC Bank"), ("CBG", "Close Brothers"),
        ("OSB", "OSB Group"), ("PAG", "Paragon Banking"), ("IGG", "IG Group"),
        ("CMCX", "CMC Markets"), ("PLUS", "Plus500"), ("ASHM", "Ashmore Group"),
        ("JUP", "Jupiter Fund Management"), ("SDR", "Schroders"), ("LIO", "Liontrust"),
        ("AJB", "AJ Bell"), ("HL", "Hargreaves Lansdown"), ("ICP", "Intermediate Capital"),
        ("BGFD", "Baillie Gifford Japan"), ("PCT", "Polar Capital Technology"),
        ("HICL", "HICL Infrastructure"), ("BBGI", "BBGI Global Infrastructure"),
        ("TRIG", "Renewables Infrastructure"), ("GRID", "Gore Street Energy"),
        ("GCP", "GCP Infrastructure"), ("NESF", "NextEnergy Solar"), ("FGEN", "Foresight Solar"),
        ("HGT", "HgCapital Trust"), ("PIN", "Pantheon International"), ("HVPE", "HarbourVest"),
        ("CTY", "City of London Trust"), ("MRC", "Mercantile Trust"), ("ATT", "Allianz Technology"),
        ("WWH", "Worldwide Healthcare"), ("BRWM", "BlackRock World Mining"), ("MYI", "Murray International"),
        ("GSS", "Genus"), ("DARK", "Darktrace"), ("KNOS", "Kainos"), ("BYIT", "Bytes Technology"),
        ("SPT", "Spirent"), ("GAW", "Games Workshop"), ("MONY", "MONY Group"),
        ("TRN", "Trainline"), ("MTO", "Mitie"), ("SRP", "Serco"), ("CAPC", "Capita"),
        ("PAGE", "PageGroup"), ("HAS", "Hays"), ("SThree", "SThree"), ("RWA", "Robert Walters"),
        ("GNS", "Genus"), ("CWK", "Cranswick"), ("BAG", "AG Barr"), ("GNC", "Greencore"),
        ("PETS", "Pets at Home"), ("WIZZ", "Wizz Air"), ("SSPG", "SSP Group"),
        ("DOM", "Domino's Pizza UK"), ("RTN", "Restaurant Group"), ("MAB", "Mitchells & Butlers"),
        ("GRG", "Greggs"), ("CINE", "Cineworld"), ("FOUR", "4imprint"), ("WOSG", "Watches of Switzerland"),
        ("DNLM", "Dunelm"), ("CURY", "Currys"), ("FRAS", "Frasers Group"), ("TPK", "Travis Perkins"),
        ("GFTU", "Grafton Group"), ("BOOT", "Henry Boot"), ("VSVS", "Vesuvius"),
        ("MTRO", "Metro Bank"), ("VANQ", "Vanquis Banking"), ("PFG", "Provident Financial"),
        ("TCAP", "TP ICAP"), ("RAT", "Rathbones"), ("BRSC", "BlackRock Smaller Cos"),
        ("TEP", "Telecom Plus"), ("HFG", "Hilton Food"), ("BOY", "Bodycote"),
        ("MCRO", "Micro Focus"), ("AVON", "Avon Protection"), ("SNR", "Senior"),
        ("WG", "John Wood Group"), ("PMO", "Premier Oil"), ("HBR", "Harbour Energy"),
        ("ENOG", "Energean"), ("CNE", "Capricorn Energy"), ("TLW", "Tullow Oil"),
        ("WDS", "Woodside"), ("DEC", "Diversified Energy"), ("PMG", "Parkmead"),
        ("TRP", "Tharisa"), ("HOC", "Hochschild Mining"), ("CEY", "Centamin"),
        ("POLY", "Polymetal"), ("KMR", "Kenmare Resources"), ("SXX", "Sirius Minerals"),
        ("EDV", "Endeavour Mining"), ("GEMD", "Gem Diamonds"), ("PDL", "Petra Diamonds"),
    ]

    # FX pairs relevant to a UK↔India remitter (yfinance PAIR=X).
    FX_SYMBOLS = [
        ("GBPINR", "GBPINR=X", "British Pound / Indian Rupee", "GBP/INR"),
        ("INRGBP", "INRGBP=X", "Indian Rupee / British Pound", "INR/GBP"),
        ("GBPUSD", "GBPUSD=X", "British Pound / US Dollar", "GBP/USD"),
        ("USDINR", "USDINR=X", "US Dollar / Indian Rupee", "USD/INR"),
    ]

    def _load_fx_pairs(self) -> int:
        self._ensure_currency_column()
        now = datetime.now().isoformat()
        n = 0
        with self._lock, self._conn() as conn:
            for symbol, yahoo, name, _q in self.FX_SYMBOLS:
                conn.execute(
                    """INSERT INTO symbols(symbol, yahoo, name, exchange, asset_class, currency, updated_at)
                       VALUES(?,?,?,?,?,?,?)
                       ON CONFLICT(symbol) DO UPDATE SET yahoo=excluded.yahoo, name=excluded.name,
                         exchange=excluded.exchange, updated_at=excluded.updated_at""",
                    (symbol, yahoo, name, "FX", "forex", "", now))
                n += 1
        return n

    def _ensure_currency_column(self):
        """Older DBs were created without a currency column — add it if missing."""
        with self._lock, self._conn() as conn:
            cols = [r[1] for r in conn.execute("PRAGMA table_info(symbols)")]
            if "currency" not in cols:
                conn.execute("ALTER TABLE symbols ADD COLUMN currency TEXT")

    def _load_lse_equities(self) -> int:
        """FTSE 100/250 London stocks — priced in GBP (yfinance SYMBOL.L)."""
        self._ensure_currency_column()
        now = datetime.now().isoformat()
        count = 0
        with self._lock, self._conn() as conn:
            for symbol, name in self.LSE_SYMBOLS:
                conn.execute(
                    """INSERT INTO symbols(symbol, yahoo, name, exchange, asset_class, currency, updated_at)
                       VALUES(?,?,?,?,?,?,?)
                       ON CONFLICT(symbol) DO UPDATE SET
                         yahoo=excluded.yahoo, name=excluded.name, exchange=excluded.exchange,
                         currency=excluded.currency, updated_at=excluded.updated_at""",
                    (symbol, f"{symbol}.L", name, "LSE", "equity", "GBP", now),
                )
                count += 1
        logger.info(f"Loaded {count} LSE (London/GBP) symbols")
        return count

    def _load_nse_equities(self) -> int:
        """Official NSE equity master list — every listed NSE stock."""
        try:
            req = urllib.request.Request(
                NSE_EQUITY_LIST_URL,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                    "Accept": "text/csv,*/*",
                },
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                text = resp.read().decode("utf-8", errors="replace")
        except Exception as e:
            logger.warning(f"NSE equity list download failed: {e}")
            return 0

        rows = list(csv.DictReader(io.StringIO(text)))
        count = 0
        now = datetime.now().isoformat()
        with self._lock, self._conn() as conn:
            for r in rows:
                symbol = (r.get("SYMBOL") or "").strip()
                series = (r.get(" SERIES") or r.get("SERIES") or "").strip()
                if not symbol or series not in ("EQ", "BE", "BZ", ""):
                    continue
                name = (r.get("NAME OF COMPANY") or r.get(" NAME OF COMPANY") or "").strip()
                isin = (r.get(" ISIN NUMBER") or r.get("ISIN NUMBER") or "").strip()
                conn.execute(
                    """INSERT INTO symbols(symbol, yahoo, name, exchange, asset_class, isin, updated_at)
                       VALUES(?,?,?,?,?,?,?)
                       ON CONFLICT(symbol) DO UPDATE SET
                         name=excluded.name, isin=excluded.isin, updated_at=excluded.updated_at""",
                    (symbol, f"{symbol}.NS", name, "NSE", "equity", isin, now),
                )
                count += 1
        return count

    def _load_bse_equities(self) -> int:
        """Official BSE equity master via the UDiFF bhavcopy (walk back up to 7
        trading days until a file exists). Display = ticker symbol; yahoo = the
        numeric BSE scrip code + .BO (the form Yahoo Finance uses)."""
        self._ensure_currency_column()
        text = None
        for days_back in range(1, 8):
            day = datetime.now() - timedelta(days=days_back)
            try:
                req = urllib.request.Request(
                    BSE_BHAV_URL.format(d=day),
                    headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                             "Accept": "*/*"})
                with urllib.request.urlopen(req, timeout=30) as resp:
                    text = resp.read().decode("utf-8", errors="replace")
                break
            except Exception:
                continue
        if text is None:
            logger.warning("BSE bhavcopy unavailable for the last 7 days")
            return 0

        rows = list(csv.DictReader(io.StringIO(text)))
        # equity-like series only (skip derivatives / debt / ETF-odd series)
        eq_series = {"A", "B", "T", "X", "XT", "M", "MT", "Z", "E", "G"}
        now = datetime.now().isoformat()
        count = 0
        with self._lock, self._conn() as conn:
            for r in rows:
                if (r.get("FinInstrmTp") or "").strip() not in ("", "EQ"):
                    # UDiFF equity segment rows have blank/EQ instrument type
                    pass
                series = (r.get("SctySrs") or "").strip()
                if series not in eq_series:
                    continue
                sym = (r.get("TckrSymb") or "").strip()
                code = (r.get("FinInstrmId") or "").strip()
                if not sym or not code:
                    continue
                name = (r.get("FinInstrmNm") or "").strip()
                isin = (r.get("ISIN") or "").strip()
                # display uses .BO-suffixed scrip form to avoid NSE symbol collisions
                display = f"{sym}.BO"
                conn.execute(
                    """INSERT INTO symbols(symbol, yahoo, name, exchange, asset_class, isin, currency, updated_at)
                       VALUES(?,?,?,?,?,?,?,?)
                       ON CONFLICT(symbol) DO UPDATE SET
                         yahoo=excluded.yahoo, name=excluded.name, isin=excluded.isin,
                         currency=excluded.currency, updated_at=excluded.updated_at""",
                    (display, f"{code}.BO", name, "BSE", "equity", isin, "INR", now),
                )
                count += 1
        logger.info(f"Loaded {count} BSE symbols")
        return count

    # NASDAQ Trader 'otherlisted' Exchange code → our label
    _US_EXCH = {"N": "NYSE", "A": "AMEX", "P": "ARCA", "Z": "BATS", "V": "IEX"}

    def _load_us_full(self) -> int:
        """Full US market from NASDAQ Trader: nasdaqlisted (NASDAQ) + otherlisted
        (NYSE/AMEX/Arca/BATS). Real, keyless, ~13k symbols. Yahoo uses the bare
        symbol with '.'→'-' for share classes (BRK.A → BRK-A)."""
        def _get(url):
            req = urllib.request.Request(
                url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                              "Accept": "*/*"})
            with urllib.request.urlopen(req, timeout=45) as resp:
                return resp.read().decode("utf-8", errors="replace")

        now = datetime.now().isoformat()
        count = 0

        def _rows(url):
            try:
                return list(csv.DictReader(io.StringIO(_get(url)), delimiter="|"))
            except Exception as e:
                logger.warning(f"US listing download failed ({url}): {e}")
                return []

        with self._lock, self._conn() as conn:
            # NASDAQ-listed
            for r in _rows(NASDAQ_LISTED_URL):
                sym = (r.get("Symbol") or "").strip()
                if not sym or (r.get("Test Issue") or "") == "Y" or "File Creation Time" in sym:
                    continue
                name = (r.get("Security Name") or "").strip()
                etf = (r.get("ETF") or "") == "Y"
                yahoo = sym.replace(".", "-")
                conn.execute(
                    """INSERT INTO symbols(symbol, yahoo, name, exchange, asset_class, currency, updated_at)
                       VALUES(?,?,?,?,?,?,?)
                       ON CONFLICT(symbol) DO UPDATE SET
                         yahoo=excluded.yahoo, name=excluded.name, exchange=excluded.exchange,
                         currency=excluded.currency, updated_at=excluded.updated_at""",
                    (sym, yahoo, name, "NASDAQ", "etf" if etf else "equity", "USD", now))
                count += 1
            # NYSE / AMEX / Arca / BATS
            for r in _rows(NASDAQ_OTHER_URL):
                sym = (r.get("ACT Symbol") or "").strip()
                if not sym or (r.get("Test Issue") or "") == "Y" or "File Creation Time" in sym:
                    continue
                exch = self._US_EXCH.get((r.get("Exchange") or "").strip(), "US")
                name = (r.get("Security Name") or "").strip()
                etf = (r.get("ETF") or "") == "Y"
                yahoo = sym.replace(".", "-")
                conn.execute(
                    """INSERT INTO symbols(symbol, yahoo, name, exchange, asset_class, currency, updated_at)
                       VALUES(?,?,?,?,?,?,?)
                       ON CONFLICT(symbol) DO UPDATE SET
                         yahoo=excluded.yahoo, name=excluded.name, exchange=excluded.exchange,
                         currency=excluded.currency, updated_at=excluded.updated_at""",
                    (sym, yahoo, name, exch, "etf" if etf else "equity", "USD", now))
                count += 1
        logger.info(f"Loaded {count} US (NASDAQ/NYSE/AMEX/Arca/BATS) symbols")
        return count

    def _load_world_symbols(self) -> int:
        """Curated real large-caps across 34 exchanges (major index constituents)
        — geographic breadth alongside the live full listings.
        Source: src/data/world_symbols.py.

        Also PRUNES curated symbols that have left the source list. That is not
        housekeeping, it is correctness: this loader only ever upserted, so a
        symbol deleted or corrected in world_symbols.py stayed in the database
        forever. Fixing a mistyped ticker therefore did nothing on any install
        that had already refreshed once — the bad symbol survived alongside the
        good one, and the bad one is the one that abstains on every request.

        The prune is scoped to exchanges this loader actually owns, so it can
        never touch a US, NSE, BSE or LSE row from the live listings, nor a
        symbol the resolver inserted on demand.
        """
        self._ensure_currency_column()
        try:
            from src.data.world_symbols import all_world_symbols
        except Exception as e:
            logger.warning(f"world_symbols load failed: {e}")
            return 0
        now = datetime.now().isoformat()
        rows = list(all_world_symbols())
        count = 0
        with self._lock, self._conn() as conn:
            for display, yahoo, name, exchange, currency, asset_class in rows:
                conn.execute(
                    """INSERT INTO symbols(symbol, yahoo, name, exchange, asset_class, currency, updated_at)
                       VALUES(?,?,?,?,?,?,?)
                       ON CONFLICT(symbol) DO UPDATE SET
                         yahoo=excluded.yahoo, name=excluded.name, exchange=excluded.exchange,
                         asset_class=excluded.asset_class, currency=excluded.currency,
                         updated_at=excluded.updated_at""",
                    (display, yahoo, name, exchange, asset_class, currency, now))
                count += 1

            live = {r[0] for r in rows}
            owned = sorted({r[3] for r in rows})
            placeholders = ",".join("?" * len(owned))
            stale = [r[0] for r in conn.execute(
                f"SELECT symbol FROM symbols WHERE exchange IN ({placeholders})", owned)
                if r[0] not in live]
            for sym in stale:
                conn.execute("DELETE FROM symbols WHERE symbol=?", (sym,))

        if stale:
            logger.info(f"Pruned {len(stale)} curated world symbols no longer listed: "
                        f"{', '.join(stale[:10])}")
        logger.info(f"Loaded {count} curated world large-cap symbols")
        return count

    def _load_nse_fno(self) -> int:
        """Official NSE F&O bhavcopy (UDiFF) — marks derivative-eligible symbols
        and stores their lot sizes (futures & options coverage for NSE).
        Tries the last few trading days until one file exists."""
        import zipfile

        text = None
        for days_back in range(0, 8):
            day = datetime.now() - timedelta(days=days_back)
            url = ("https://nsearchives.nseindia.com/content/fo/"
                   f"BhavCopy_NSE_FO_0_0_0_{day:%Y%m%d}_F_0000.csv.zip")
            try:
                req = urllib.request.Request(
                    url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
                with urllib.request.urlopen(req, timeout=30) as resp:
                    payload = resp.read()
                with zipfile.ZipFile(io.BytesIO(payload)) as zf:
                    text = zf.read(zf.namelist()[0]).decode("utf-8", errors="replace")
                break
            except Exception:
                continue
        if text is None:
            logger.warning("NSE F&O bhavcopy unavailable for the last 7 days")
            return 0

        rows = list(csv.DictReader(io.StringIO(text)))
        lots: Dict[str, int] = {}
        for r in rows:
            if (r.get("FinInstrmTp") or "").strip() not in ("STF", "STO"):
                continue  # stock futures / stock options only
            sym = (r.get("TckrSymb") or "").strip()
            try:
                lot = int(float(r.get("NewBrdLotQty") or 0))
            except ValueError:
                continue
            if sym and lot > 0 and (sym not in lots or lot < lots[sym]):
                lots[sym] = lot

        count = 0
        with self._lock, self._conn() as conn:
            conn.execute("UPDATE symbols SET has_fno=0 WHERE exchange='NSE'")
            for sym, lot in lots.items():
                cur = conn.execute(
                    "UPDATE symbols SET has_fno=1, lot_size=? WHERE symbol=?", (lot, sym))
                count += cur.rowcount
        return count

    def _load_yaml_universe(self) -> int:
        """Import the existing global universe (US equities, ETFs, crypto, FX, futures)."""
        try:
            import yaml
            cfg = yaml.safe_load(UNIVERSE_YAML.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"universe.yaml load failed: {e}")
            return 0

        sections = {
            "equities": ("US", "equity"),
            "etfs": ("US", "etf"),
            "fixed_income": ("US", "fixed_income"),
            "credit": ("US", "credit"),
            "commodities": ("US", "commodity"),
            "forex": ("FX", "forex"),
            "futures": ("FUT", "futures"),
            "crypto": ("CRYPTO", "crypto"),
            "indices": ("INDEX", "index"),
        }
        universe = (cfg or {}).get("universe", {})
        count = 0
        now = datetime.now().isoformat()
        with self._lock, self._conn() as conn:
            for section, (exchange, asset_class) in sections.items():
                for item in universe.get(section, []) or []:
                    ticker = str(item.get("ticker", "")).strip()
                    if not ticker:
                        continue
                    conn.execute(
                        """INSERT INTO symbols(symbol, yahoo, name, exchange, asset_class, updated_at)
                           VALUES(?,?,?,?,?,?)
                           ON CONFLICT(symbol) DO UPDATE SET
                             name=excluded.name, updated_at=excluded.updated_at""",
                        (ticker, ticker, str(item.get("name", "")), exchange, asset_class, now),
                    )
                    count += 1
        return count

    def _counts(self) -> Dict[str, Any]:
        with self._conn() as conn:
            total = conn.execute("SELECT COUNT(*) c FROM symbols").fetchone()["c"]
            by_ex = {
                row["exchange"]: row["c"]
                for row in conn.execute(
                    "SELECT exchange, COUNT(*) c FROM symbols GROUP BY exchange"
                )
            }
        return {"total_symbols": total, "by_exchange": by_ex}

    def status(self) -> Dict[str, Any]:
        with self._conn() as conn:
            quotes_cached = conn.execute("SELECT COUNT(*) c FROM quotes").fetchone()["c"]
        dossiers = len(list(DOSSIER_DIR.glob("*.json")))
        return {
            "index_refreshed_at": self._meta_get("index_refreshed_at"),
            "tier1_quotes_cached": quotes_cached,
            "tier2_dossiers_cached": dossiers,
            **self._counts(),
        }

    def search(self, q: str, n: int = 20) -> List[Dict[str, Any]]:
        """Instant tier-0 search — never touches the network."""
        like = f"%{q.upper()}%"
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT symbol, yahoo, name, exchange, asset_class, sector, has_fno, lot_size
                   FROM symbols
                   WHERE UPPER(symbol) LIKE ? OR UPPER(name) LIKE ?
                   ORDER BY
                     CASE WHEN UPPER(symbol) = ? THEN 0
                          WHEN UPPER(symbol) LIKE ? THEN 1
                          ELSE 2 END,
                     LENGTH(symbol)
                   LIMIT ?""",
                (like, like, q.upper(), f"{q.upper()}%", n),
            ).fetchall()
        return [dict(r) for r in rows]

    def index(self, exchange: Optional[str] = None, asset_class: Optional[str] = None,
              limit: int = 40000) -> Dict[str, Any]:
        """Bulk tier-0 dump for the 3D universe map: lightweight rows only
        (symbol, name, exchange, asset_class), never any network. Optionally
        filter by exchange / asset_class."""
        where, params = [], []
        if exchange:
            where.append("exchange=?"); params.append(exchange)
        if asset_class:
            where.append("asset_class=?"); params.append(asset_class)
        clause = ("WHERE " + " AND ".join(where)) if where else ""
        params.append(int(limit))
        with self._conn() as conn:
            rows = conn.execute(
                f"""SELECT symbol, name, exchange, asset_class FROM symbols
                    {clause} ORDER BY exchange, symbol LIMIT ?""", params,
            ).fetchall()
            by_ex = {r["exchange"]: r["c"] for r in conn.execute(
                "SELECT exchange, COUNT(*) c FROM symbols GROUP BY exchange")}
        return {"count": len(rows), "by_exchange": by_ex,
                "symbols": [dict(r) for r in rows]}

    def _lookup(self, symbol: str) -> Optional[Dict[str, Any]]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM symbols WHERE UPPER(symbol)=?", (symbol.upper(),)
            ).fetchone()
        return dict(row) if row else None

    # ------------------------------------------------------------------
    # On-demand resolution — fetch ANY global ticker live, add it to the
    # index (progressive world-building). No more preloading 766 names:
    # you ask for SAIL, it's pulled from Yahoo and remembered.
    # ------------------------------------------------------------------
    _EXCHANGE_SUFFIX = {"NSE": ".NS", "BSE": ".BO", "LSE": ".L"}

    def _resolution_candidates(self, q: str, prefer: Optional[str] = None):
        """Ordered (display, yahoo, exchange, asset_class) guesses for a query."""
        q = q.strip().upper()
        cands: list = []
        if any(s in q for s in (".NS", ".BO", ".L", "-USD", "=X", "=F", "^")):
            if q.endswith(".NS"):   cands.append((q[:-3], q, "NSE", "equity"))
            elif q.endswith(".BO"): cands.append((q[:-3], q, "BSE", "equity"))
            elif q.endswith(".L"):  cands.append((q[:-2], q, "LSE", "equity"))
            elif q.endswith("-USD"): cands.append((q, q, "CRYPTO", "crypto"))
            else:                    cands.append((q, q, "US", "equity"))
        else:
            cands.append((q, q, "US", "equity"))
            cands.append((q, f"{q}.NS", "NSE", "equity"))
            cands.append((q, f"{q}.BO", "BSE", "equity"))
            cands.append((q, f"{q}.L", "LSE", "equity"))
            cands.append((q, f"{q}-USD", "CRYPTO", "crypto"))
        if prefer:
            p = prefer.upper()
            cands.sort(key=lambda c: 0 if c[2] == p else 1)
        return cands

    def resolve(self, query: str, prefer: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Return the index row for a symbol, fetching it LIVE from Yahoo and
        adding it if unknown. `prefer` biases the market (NSE/BSE/US/LSE) so a
        bare 'SAIL' resolves to the Indian listing, not a US namesake."""
        q = (query or "").strip().upper()
        if not q:
            return None
        hit = self._lookup(q)
        if hit:
            return hit
        import yfinance as yf  # lazy
        for disp, yahoo, exch, aclass in self._resolution_candidates(q, prefer):
            existing = self._lookup(disp)
            if existing:
                return existing
            try:
                t = yf.Ticker(yahoo)
                hist = t.history(period="5d")
                if hist is None or hist.empty:
                    continue
                raw = {}
                try:
                    raw = t.info or {}
                except Exception:
                    pass
                name = raw.get("longName") or raw.get("shortName") or disp
                self._insert_resolved(disp, yahoo, name, exch, aclass)
                logger.info(f"resolved new ticker {disp} → {yahoo} ({exch})")
                return self._lookup(disp)
            except Exception:
                continue
        return None

    def _insert_resolved(self, symbol, yahoo, name, exchange, asset_class):
        now = datetime.now().isoformat()
        with self._lock, self._conn() as conn:
            conn.execute(
                """INSERT INTO symbols(symbol, yahoo, name, exchange, asset_class, updated_at)
                   VALUES(?,?,?,?,?,?)
                   ON CONFLICT(symbol) DO UPDATE SET
                     yahoo=excluded.yahoo, name=excluded.name,
                     exchange=excluded.exchange, asset_class=excluded.asset_class,
                     updated_at=excluded.updated_at""",
                (symbol, yahoo, name, exchange, asset_class, now))

    def explore(self, query: str, prefer: Optional[str] = None,
                n_peers: int = 5) -> Dict[str, Any]:
        """Search ANY global ticker (streaming/LOD): resolve+fetch it live if
        unknown, return its full dossier, and warm the related names (sector
        peers) in the background — the tile you asked for plus the next street.
        Prunes the least-used cached dossiers so the cache stays bounded."""
        info = self.resolve(query, prefer)
        if not info:
            return {"error": f"could not resolve '{query}' on US/NSE/BSE/LSE — "
                             f"try an explicit ticker like {query.strip().upper()}.NS"}
        dossier = self.dossier(info["symbol"], prefetch_peers=True)

        # Curated competitor/supplier graph (data/relationships.json) — the
        # named relations price data can't give. Each is resolved live so it's
        # added + cached too; then progressive sector peers fill the rest.
        from src.data.relationships import related_symbols
        rel = related_symbols(info["symbol"], info.get("yahoo"))
        competitors = self._resolve_related(rel["competitors"])
        suppliers = self._resolve_related(rel["suppliers"])
        named = {c["symbol"] for c in competitors} | {s["symbol"] for s in suppliers}
        sector_peers = [p for p in self.peers(info["symbol"], n_peers)
                        if p["symbol"] not in named]
        self._prune_dossiers()

        total = len(competitors) + len(suppliers) + len(sector_peers)
        return {
            "resolved": {"symbol": info["symbol"], "yahoo": info["yahoo"],
                         "exchange": info.get("exchange"), "name": info.get("name")},
            "dossier": dossier,
            "related": {"competitors": competitors, "suppliers": suppliers,
                        "sector_peers": sector_peers},
            "note": f"resolved as {info['yahoo']} on {info.get('exchange')}; "
                    f"{total} related names (curated + sector) warming in background",
        }

    def _resolve_related(self, symbols: list, cap: int = 6) -> list:
        """Resolve a list of related yahoo-ready symbols to light index rows
        (adding + caching each on demand), skipping any that don't resolve."""
        out = []
        for s in symbols[:cap]:
            try:
                info = self.resolve(s)
                if info:
                    out.append({"symbol": info["symbol"], "yahoo": info["yahoo"],
                                "name": info.get("name"),
                                "exchange": info.get("exchange")})
            except Exception:
                continue
        return out

    def _prune_dossiers(self, cap: int = 800) -> None:
        """Forget the unnecessary: delete the least-recently-fetched dossiers
        beyond the cap so the on-demand cache never grows without bound."""
        try:
            files = sorted(DOSSIER_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime)
            for old in files[:-cap]:
                old.unlink()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Tier 1 — quote cards
    # ------------------------------------------------------------------
    # Currency by exchange — so a searched symbol always shows the right units.
    CURRENCY_BY_EXCHANGE = {
        "NSE": "INR", "BSE": "INR", "LSE": "GBP", "US": "USD",
        "NASDAQ": "USD", "NYSE": "USD", "AMEX": "USD", "ARCA": "USD", "BATS": "USD", "IEX": "USD",
        "XETRA": "EUR", "EURONEXT_PA": "EUR", "EURONEXT_AS": "EUR", "BORSA_IT": "EUR",
        "BME": "EUR", "SIX": "CHF", "OMX_STO": "SEK", "EURONEXT_BR": "EUR",
        "EURONEXT_LS": "EUR", "OMX_HEL": "EUR", "OSLO": "NOK", "OMX_CPH": "DKK",
        "WIENER_BORSE": "EUR", "EURONEXT_DUB": "EUR", "ATHEX": "EUR",
        "HKEX": "HKD", "SSE": "CNY", "SZSE": "CNY", "TSE": "JPY", "TSX": "CAD", "ASX": "AUD",
        "CRYPTO": "USD", "FX": "", "FUT": "USD", "INDEX": "",
    }

    def _currency_for(self, info: Dict[str, Any]) -> str:
        if info.get("currency"):
            return info["currency"]
        y = (info.get("yahoo") or "")
        if y.endswith(".NS") or y.endswith(".BO"):
            return "INR"
        if y.endswith(".L"):
            return "GBP"
        return self.CURRENCY_BY_EXCHANGE.get(info.get("exchange", ""), "USD")

    def quote(self, symbol: str) -> Dict[str, Any]:
        info = self._lookup(symbol)
        if not info:
            return {"error": f"'{symbol}' not in universe index"}
        info = {**info, "currency": self._currency_for(info)}

        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM quotes WHERE symbol=?", (info["symbol"],)
            ).fetchone()
        if row:
            age = datetime.now() - datetime.fromisoformat(row["fetched_at"])
            if age < timedelta(minutes=QUOTE_TTL_MINUTES):
                return {**info, **dict(row), "cache": "hit"}

        fetched = self._fetch_quotes([info])
        q = fetched.get(info["symbol"], {})
        return {**info, **q, "cache": "miss"}

    def quotes(self, symbols: List[str]) -> Dict[str, Any]:
        """Batch tier-1: serve fresh ones from cache, fetch the rest in chunks."""
        infos, missing, out = [], [], {}
        for s in symbols:
            info = self._lookup(s)
            if info:
                infos.append(info)
        with self._conn() as conn:
            for info in infos:
                row = conn.execute(
                    "SELECT * FROM quotes WHERE symbol=?", (info["symbol"],)
                ).fetchone()
                fresh = False
                if row:
                    age = datetime.now() - datetime.fromisoformat(row["fetched_at"])
                    fresh = age < timedelta(minutes=QUOTE_TTL_MINUTES)
                if fresh:
                    out[info["symbol"]] = {**info, **dict(row), "cache": "hit"}
                else:
                    missing.append(info)
        if missing:
            fetched = self._fetch_quotes(missing)
            for info in missing:
                q = fetched.get(info["symbol"], {})
                out[info["symbol"]] = {**info, **q, "cache": "miss"}
        return out

    def _fetch_quotes(self, infos: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        """Chunked yfinance download — 2 daily closes per symbol, nothing more."""
        import yfinance as yf  # lazy: heavy import stays off the startup path

        results: Dict[str, Dict[str, Any]] = {}
        now = datetime.now().isoformat()
        for i in range(0, len(infos), QUOTE_CHUNK_SIZE):
            chunk = infos[i : i + QUOTE_CHUNK_SIZE]
            tickers = [c["yahoo"] for c in chunk]
            try:
                df = yf.download(
                    tickers, period="5d", interval="1d",
                    progress=False, threads=True, group_by="ticker",
                )
            except Exception as e:
                logger.warning(f"quote chunk failed: {e}")
                continue
            for c in chunk:
                try:
                    # group_by="ticker" keys columns by ticker even for a single symbol
                    closes = (
                        df[c["yahoo"]]["Close"]
                        if c["yahoo"] in df.columns.get_level_values(0)
                        else df["Close"]
                    ).dropna()
                    if closes.empty:
                        continue
                    price = float(closes.iloc[-1])
                    change = (
                        (price / float(closes.iloc[-2]) - 1.0) * 100.0
                        if len(closes) > 1 else None
                    )
                    results[c["symbol"]] = {
                        "price": round(price, 4),
                        "change_pct": round(change, 3) if change is not None else None,
                        "fetched_at": now,
                    }
                except Exception:
                    continue
        if results:
            with self._lock, self._conn() as conn:
                for sym, q in results.items():
                    conn.execute(
                        """INSERT INTO quotes(symbol, price, change_pct, fetched_at)
                           VALUES(?,?,?,?)
                           ON CONFLICT(symbol) DO UPDATE SET
                             price=excluded.price, change_pct=excluded.change_pct,
                             fetched_at=excluded.fetched_at""",
                        (sym, q["price"], q["change_pct"], q["fetched_at"]),
                    )
        return results

    # ------------------------------------------------------------------
    # Tier 2 — full dossier
    # ------------------------------------------------------------------
    def dossier(self, symbol: str, prefetch_peers: bool = True) -> Dict[str, Any]:
        info = self._lookup(symbol)
        if not info:
            return {"error": f"'{symbol}' not in universe index"}

        path = DOSSIER_DIR / f"{info['symbol'].replace('=','_').replace('^','_').replace('/','_')}.json"
        if path.exists():
            try:
                cached = json.loads(path.read_text(encoding="utf-8"))
                age = datetime.now() - datetime.fromisoformat(cached["fetched_at"])
                if age < timedelta(hours=DOSSIER_TTL_HOURS):
                    cached["cache"] = "hit"
                    if prefetch_peers:
                        self._prefetch_peers_async(info)
                    return cached
            except Exception:
                pass

        dossier = self._build_dossier(info)
        if "error" not in dossier:
            path.write_text(json.dumps(dossier, default=str), encoding="utf-8")
            if prefetch_peers:
                self._prefetch_peers_async(info)
        dossier["cache"] = "miss"
        return dossier

    def _build_dossier(self, info: Dict[str, Any]) -> Dict[str, Any]:
        import yfinance as yf  # lazy

        try:
            t = yf.Ticker(info["yahoo"])
            hist = t.history(period="5y", interval="1wk", auto_adjust=True)
            raw = t.info or {}
        except Exception as e:
            return {"error": f"fetch failed for {info['symbol']}: {e}"}

        if hist is None or hist.empty:
            return {"error": f"no price history for {info['symbol']}"}

        closes = hist["Close"].dropna()
        price = float(closes.iloc[-1])

        def _ret(weeks: int) -> Optional[float]:
            if len(closes) <= weeks:
                return None
            return round((price / float(closes.iloc[-weeks - 1]) - 1.0) * 100.0, 2)

        keys = [
            "longName", "sector", "industry", "marketCap", "trailingPE", "forwardPE",
            "priceToBook", "bookValue", "dividendYield", "dividendRate", "returnOnEquity",
            "debtToEquity", "profitMargins", "operatingMargins", "revenueGrowth",
            "earningsGrowth", "totalRevenue", "freeCashflow", "operatingCashflow",
            "totalDebt", "totalCash", "beta", "fiftyTwoWeekHigh", "fiftyTwoWeekLow",
            "currency", "heldPercentInsiders", "heldPercentInstitutions",
        ]
        fundamentals = {k: raw.get(k) for k in keys if raw.get(k) is not None}

        # Progressive world-building: enrich the tier-0 index as we explore.
        now = datetime.now().isoformat()
        with self._lock, self._conn() as conn:
            conn.execute(
                """UPDATE symbols SET sector=?, industry=?, mcap=?, updated_at=?
                   WHERE symbol=?""",
                (
                    raw.get("sector"), raw.get("industry"),
                    raw.get("marketCap"), now, info["symbol"],
                ),
            )

        weekly = {
            str(idx.date()): round(float(v), 4)
            for idx, v in closes.items()
        }
        return {
            "symbol": info["symbol"],
            "yahoo": info["yahoo"],
            "name": raw.get("longName") or info.get("name"),
            "exchange": info.get("exchange"),
            "asset_class": info.get("asset_class"),
            "price": round(price, 4),
            "returns": {"1M": _ret(4), "6M": _ret(26), "1Y": _ret(52), "5Y": _ret(260)},
            "fundamentals": fundamentals,
            "weekly_closes_5y": weekly,
            "sources": {
                "prices": "Yahoo Finance (5y weekly, returns computed locally)",
                "fundamentals": "Yahoo Finance",
            },
            "fetched_at": now,
        }

    # ------------------------------------------------------------------
    # Options chains (tier-2 detail, on demand only)
    # ------------------------------------------------------------------
    def options_chain(self, symbol: str) -> Dict[str, Any]:
        info = self._lookup(symbol)
        if not info:
            return {"error": f"'{symbol}' not in universe index"}

        import yfinance as yf  # lazy
        t = yf.Ticker(info["yahoo"])
        try:
            expirations = list(t.options or [])
        except Exception:
            expirations = []

        base = {
            "symbol": info["symbol"],
            "exchange": info.get("exchange"),
            "has_fno": bool(info.get("has_fno")),
            "lot_size": info.get("lot_size"),
        }
        if not expirations:
            note = ("NSE derivative — listed in F&O with the lot size shown; "
                    "per-strike NSE chains are not served by Yahoo Finance."
                    if info.get("has_fno")
                    else "No listed options found for this symbol on Yahoo Finance.")
            return {**base, "expirations": [], "note": note,
                    "sources": {"chain": "Yahoo Finance", "lots": "NSE fo_mktlots.csv"}}

        try:
            chain = t.option_chain(expirations[0])
            spot = None
            with self._conn() as conn:
                row = conn.execute("SELECT price FROM quotes WHERE symbol=?",
                                   (info["symbol"],)).fetchone()
                if row:
                    spot = row["price"]
            if spot is None:
                hist = t.history(period="1d")
                spot = float(hist["Close"].iloc[-1]) if not hist.empty else None

            def trim(df):
                cols = ["strike", "lastPrice", "bid", "ask", "impliedVolatility",
                        "openInterest", "volume", "inTheMoney"]
                df = df[[c for c in cols if c in df.columns]]
                if spot is not None and len(df) > 12:
                    df = df.iloc[(df["strike"] - spot).abs().sort_values().index[:12]]
                    df = df.sort_values("strike")
                return json.loads(df.to_json(orient="records"))

            return {
                **base, "spot": spot,
                "expirations": expirations[:12],
                "nearest_expiry": expirations[0],
                "calls": trim(chain.calls),
                "puts": trim(chain.puts),
                "sources": {"chain": "Yahoo Finance", "spot": "tier-1 cache / Yahoo Finance"},
            }
        except Exception as e:
            return {**base, "expirations": expirations[:12],
                    "error_detail": f"chain fetch failed: {e}"}

    # ------------------------------------------------------------------
    # Background prefetch — warm the next street over
    # ------------------------------------------------------------------
    def peers(self, symbol: str, n: int = 5) -> List[Dict[str, Any]]:
        info = self._lookup(symbol)
        if not info or not info.get("sector"):
            return []
        with self._conn() as conn:
            # 1) closest peers: same sector, same exchange
            rows = list(conn.execute(
                """SELECT symbol, yahoo, name, sector, industry, mcap
                   FROM symbols
                   WHERE sector=? AND exchange=? AND symbol != ? AND mcap IS NOT NULL
                   ORDER BY mcap DESC LIMIT ?""",
                (info["sector"], info["exchange"], info["symbol"], n),
            ).fetchall())
            # 2) widen to same INDUSTRY across ANY market (global competitors),
            #    then same SECTOR anywhere — fills in as the universe is explored.
            if len(rows) < n:
                have = {r["symbol"] for r in rows} | {info["symbol"]}
                for col, val in (("industry", info.get("industry")),
                                 ("sector", info.get("sector"))):
                    if not val or len(rows) >= n:
                        continue
                    extra = conn.execute(
                        f"""SELECT symbol, yahoo, name, sector, industry, mcap
                            FROM symbols
                            WHERE {col}=? AND symbol != ? AND mcap IS NOT NULL
                            ORDER BY mcap DESC LIMIT ?""",
                        (val, info["symbol"], n * 3),
                    ).fetchall()
                    for r in extra:
                        if r["symbol"] not in have:
                            rows.append(r); have.add(r["symbol"])
                            if len(rows) >= n:
                                break
        return [dict(r) for r in rows]

    def _prefetch_peers_async(self, info: Dict[str, Any]) -> None:
        def _run() -> None:
            try:
                for peer in self.peers(info["symbol"], PEER_PREFETCH_LIMIT):
                    sym = peer["symbol"]
                    if sym in self._prefetch_inflight:
                        continue
                    path = DOSSIER_DIR / f"{sym}.json"
                    if path.exists():
                        try:
                            cached = json.loads(path.read_text(encoding="utf-8"))
                            age = datetime.now() - datetime.fromisoformat(cached["fetched_at"])
                            if age < timedelta(hours=DOSSIER_TTL_HOURS):
                                continue
                        except Exception:
                            pass
                    self._prefetch_inflight.add(sym)
                    try:
                        self.dossier(sym, prefetch_peers=False)
                        time.sleep(2)  # be gentle with the data source
                    finally:
                        self._prefetch_inflight.discard(sym)
            except Exception as e:
                logger.debug(f"peer prefetch skipped: {e}")

        threading.Thread(target=_run, daemon=True, name="universe-prefetch").start()


# ---------------------------------------------------------------------------
# Singleton accessor (mirrors the pattern used by src/brain/vault.py)
# ---------------------------------------------------------------------------
_universe: Optional[UniverseManager] = None
_universe_lock = threading.Lock()


def get_universe() -> UniverseManager:
    global _universe
    if _universe is None:
        with _universe_lock:
            if _universe is None:
                _universe = UniverseManager()
    return _universe
