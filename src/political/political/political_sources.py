"""
political_sources.py
====================
Source registry, data loaders, and API/manual import handlers for
the Political Portfolio Intelligence Layer.

Data policy:
  - Only legally available public disclosures are used.
  - No private, leaked, hacked, or non-public information.
  - Websites/APIs that do not permit scraping are listed as
    REFERENCE placeholders only — manual CSV import is provided instead.
  - Always respect rate limits and terms of service.

Trading Intelligence System — Phase 4
Research and educational purposes only.
"""

import os
import csv
import json
import logging
from datetime import datetime, date, timedelta
from typing import List, Dict, Optional, Tuple
from pathlib import Path

from src.political.political_data_schema import (
    PoliticalTradeRecord,
    STOCK_ACT_RANGES,
)

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
#  Path helpers
# ─────────────────────────────────────────────

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DATA_POLITICAL = BASE_DIR / "data" / "political"
RAW_DIR = DATA_POLITICAL / "raw"
MANUAL_CSV = DATA_POLITICAL / "manual_political_trades.csv"
SAMPLE_CSV = DATA_POLITICAL / "manual_political_trades_sample.csv"


# ─────────────────────────────────────────────
#  Source Registry
# ─────────────────────────────────────────────

SOURCE_REGISTRY: Dict[str, dict] = {
    "capitol_trades": {
        "name": "Capitol Trades",
        "url": "https://www.capitoltrades.com",
        "type": "api_or_manual",
        "reliability": 90,
        "description": "US politician trade tracker. Search by politician, party, chamber, committee, state, ticker.",
        "api_available": False,   # Set True when you have an API key
        "scraping_allowed": False,
        "manual_import_supported": True,
        "note": "Export or copy data to manual_political_trades.csv",
    },
    "quiver_quantitative": {
        "name": "Quiver Quantitative",
        "url": "https://www.quiverquant.com",
        "type": "api_or_manual",
        "reliability": 88,
        "description": "Congress trading dashboard, politician portfolios, estimated holdings, trade history.",
        "api_available": False,   # Quiver has a paid API — set True with API key
        "scraping_allowed": False,
        "manual_import_supported": True,
        "note": "Free tier available at quiverquant.com/congresstrading",
    },
    "unusual_whales": {
        "name": "Unusual Whales Politics",
        "url": "https://unusualwhales.com/politics",
        "type": "api_or_manual",
        "reliability": 85,
        "description": "Congress trades, House/Senate activity, SCOTUS investing, lobbying and political-market activity.",
        "api_available": False,   # Paid API available — set True with key
        "scraping_allowed": False,
        "manual_import_supported": True,
        "note": "Future API integration planned. Manual export supported.",
    },
    "insider_finance": {
        "name": "InsiderFinance Congress Trades",
        "url": "https://insiderfinance.io",
        "type": "manual",
        "reliability": 80,
        "description": "Congressional trade summaries, recent disclosures, trade alerts.",
        "api_available": False,
        "scraping_allowed": False,
        "manual_import_supported": True,
        "note": "Manual import via manual_political_trades.csv",
    },
    "house_disclosures": {
        "name": "House Financial Disclosure Portal",
        "url": "https://disclosures-clerk.house.gov",
        "type": "official_reference",
        "reliability": 100,
        "description": "Authoritative House member financial disclosures. Official government source.",
        "api_available": False,
        "scraping_allowed": False,
        "manual_import_supported": True,
        "note": "Official source. Download PDFs and enter manually.",
    },
    "senate_disclosures": {
        "name": "Senate Financial Disclosure Portal",
        "url": "https://efts.senate.gov/LATEST/search-index",
        "type": "official_reference",
        "reliability": 100,
        "description": "Authoritative Senate member financial disclosures. Official government source.",
        "api_available": False,
        "scraping_allowed": False,
        "manual_import_supported": True,
        "note": "Official source. Periodic reports and PTR (Periodic Transaction Reports).",
    },
    "oge_disclosures": {
        "name": "Office of Government Ethics",
        "url": "https://www.oge.gov/web/oge.nsf/Financial+Disclosure",
        "type": "official_reference",
        "reliability": 100,
        "description": "Executive branch disclosures. Presidential appointees, senior executive officials.",
        "api_available": False,
        "scraping_allowed": False,
        "manual_import_supported": True,
        "note": "OGE 278 and 450 forms available for download.",
    },
    "propublica": {
        "name": "ProPublica Financial Disclosures",
        "url": "https://projects.propublica.org/represent/finances",
        "type": "research_reference",
        "reliability": 85,
        "description": "Searchable public official disclosure database. Easier research interface.",
        "api_available": True,   # ProPublica Congress API is free with registration
        "scraping_allowed": False,
        "manual_import_supported": True,
        "note": "ProPublica Congress API available. Request key at propublica.org/datastore/api/propublica-congress-api",
    },
    "crew": {
        "name": "CREW (Citizens for Responsibility and Ethics)",
        "url": "https://www.citizensforethics.org",
        "type": "research_reference",
        "reliability": 78,
        "description": "Ethics and conflict-of-interest analysis. Public official financial disclosure summaries.",
        "api_available": False,
        "scraping_allowed": False,
        "manual_import_supported": True,
        "note": "Research reference. Review reports for context only.",
    },
    "major_financial_news": {
        "name": "Reuters / Bloomberg / FT (News Reference)",
        "url": "",
        "type": "contextual_news",
        "reliability": 75,
        "description": "News interpretation of large political/public official disclosures. Contextual only.",
        "api_available": False,
        "scraping_allowed": False,
        "manual_import_supported": False,
        "note": "Not primary data. Use only for contextual confirmation of known disclosures.",
    },
}


# ─────────────────────────────────────────────
#  Amount range parser
# ─────────────────────────────────────────────

def parse_amount_range(amount_str: str) -> Tuple[float, float, float]:
    """
    Parse a STOCK Act disclosure range string into (min, max, midpoint).
    Falls back to numeric parse if raw number is provided.
    """
    amount_str = str(amount_str).strip()

    # Try direct numeric first
    try:
        val = float(amount_str.replace(",", "").replace("$", ""))
        return val, val, val
    except ValueError:
        pass

    # Try known range strings
    for label, (lo, hi) in STOCK_ACT_RANGES.items():
        if label.lower() in amount_str.lower():
            mid = (lo + hi) / 2.0
            return float(lo), float(hi), mid

    # Fallback for partial matches
    for label, (lo, hi) in STOCK_ACT_RANGES.items():
        clean_label = label.replace("$", "").replace(",", "").lower()
        clean_input = amount_str.replace("$", "").replace(",", "").lower()
        if clean_input in clean_label or clean_label in clean_input:
            mid = (lo + hi) / 2.0
            return float(lo), float(hi), mid

    return 0.0, 0.0, 0.0


def _safe_date_str(val: str) -> str:
    """Normalise date strings; return empty string if unparseable."""
    val = str(val).strip()
    if not val:
        return ""
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%Y/%m/%d", "%m-%d-%Y"):
        try:
            return datetime.strptime(val, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return val  # Return as-is if we cannot parse


def _disclosure_delay(transaction_date: str, filing_date: str) -> int:
    """Calculate disclosure delay in days. Returns 0 if dates invalid."""
    try:
        t = datetime.strptime(transaction_date, "%Y-%m-%d").date()
        f = datetime.strptime(filing_date, "%Y-%m-%d").date()
        delta = (f - t).days
        return max(0, delta)
    except Exception:
        return 0


# ─────────────────────────────────────────────
#  CSV column normaliser
# ─────────────────────────────────────────────

REQUIRED_CSV_COLUMNS = [
    "source", "politician_name", "role", "party", "state",
    "committee", "ticker", "company_name", "transaction_type",
    "transaction_date", "filing_date", "amount_min", "amount_max",
    "notes", "source_url",
]


def _normalise_row(row: dict) -> dict:
    """Lowercase all keys and strip whitespace from values."""
    return {k.strip().lower().replace(" ", "_"): str(v).strip() for k, v in row.items()}


# ─────────────────────────────────────────────
#  Manual CSV loader
# ─────────────────────────────────────────────

class ManualCSVSource:
    """
    Load political trade records from a manually maintained CSV file.

    This is the primary import path when direct API access is unavailable.
    Users populate the CSV by exporting or manually copying data from:
      - Capitol Trades, Quiver Quant, Unusual Whales, InsiderFinance,
        House/Senate/OGE official portals, ProPublica, CREW.

    All data in this CSV must be publicly available legal disclosures.
    Do NOT enter private, leaked, or non-public information.
    """

    SOURCE_ID = "manual_csv"
    SOURCE_RELIABILITY = 70  # Manual entry has lower automatic reliability

    def __init__(self, csv_path: Optional[Path] = None):
        self.csv_path = csv_path or MANUAL_CSV

    def available(self) -> bool:
        return self.csv_path.exists() and self.csv_path.stat().st_size > 0

    def load(self) -> List[PoliticalTradeRecord]:
        if not self.available():
            logger.info(f"Manual CSV not found or empty: {self.csv_path}")
            return []

        records: List[PoliticalTradeRecord] = []
        try:
            with open(self.csv_path, newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for i, raw_row in enumerate(reader):
                    row = _normalise_row(raw_row)
                    try:
                        records.append(self._parse_row(row, line=i + 2))
                    except Exception as e:
                        logger.warning(f"Skipping CSV row {i+2}: {e}")
        except Exception as e:
            logger.error(f"Failed to read manual CSV: {e}")

        logger.info(f"ManualCSVSource loaded {len(records)} records from {self.csv_path.name}")
        return records

    def _parse_row(self, row: dict, line: int = 0) -> PoliticalTradeRecord:
        amount_raw_min = row.get("amount_min", "")
        amount_raw_max = row.get("amount_max", "")

        # If a combined range string is in amount_min, parse it
        if not amount_raw_max and amount_raw_min:
            amt_min, amt_max, amt_mid = parse_amount_range(amount_raw_min)
        else:
            try:
                amt_min = float(str(amount_raw_min).replace(",", "").replace("$", "")) if amount_raw_min else 0.0
            except ValueError:
                amt_min, _, _ = parse_amount_range(amount_raw_min)
            try:
                amt_max = float(str(amount_raw_max).replace(",", "").replace("$", "")) if amount_raw_max else 0.0
            except ValueError:
                _, amt_max, _ = parse_amount_range(amount_raw_max)
            amt_mid = (amt_min + amt_max) / 2.0 if amt_min and amt_max else max(amt_min, amt_max)

        txn_date = _safe_date_str(row.get("transaction_date", ""))
        filing_date = _safe_date_str(row.get("filing_date", ""))
        delay = _disclosure_delay(txn_date, filing_date)

        # Determine source reliability from source registry if source is known
        source_id = row.get("source", self.SOURCE_ID).lower().replace(" ", "_")
        reliability = SOURCE_REGISTRY.get(source_id, {}).get("reliability", self.SOURCE_RELIABILITY)

        rec = PoliticalTradeRecord(
            source=row.get("source", self.SOURCE_ID),
            source_url=row.get("source_url", ""),
            last_updated=datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            politician_name=row.get("politician_name", ""),
            role=row.get("role", ""),
            chamber_or_office=row.get("chamber_or_office", row.get("chamber", "")),
            party=row.get("party", ""),
            state=row.get("state", ""),
            committee=row.get("committee", ""),
            ticker=row.get("ticker", "").upper().strip(),
            company_name=row.get("company_name", ""),
            asset_type=row.get("asset_type", "Stock"),
            sector=row.get("sector", ""),
            transaction_type=row.get("transaction_type", "unknown").lower().strip(),
            transaction_date=txn_date,
            filing_date=filing_date,
            disclosure_delay_days=delay,
            amount_min=amt_min,
            amount_max=amt_max,
            estimated_amount_midpoint=amt_mid,
            ownership_type=row.get("ownership_type", ""),
            notes=row.get("notes", ""),
            source_reliability_score=float(reliability),
        )
        return rec


# ─────────────────────────────────────────────
#  Placeholder API Source (for future integration)
# ─────────────────────────────────────────────

class APISourcePlaceholder:
    """
    Placeholder for future API integrations.

    When a real API key is configured, override the `fetch()` method
    to pull live data. Until then, returns empty list with a log message.

    Example future integrations:
      - Quiver Quantitative paid API
      - Unusual Whales API
      - ProPublica Congress API (free, requires registration)
    """

    def __init__(self, source_id: str, api_key: Optional[str] = None):
        self.source_id = source_id
        self.api_key = api_key
        self.source_info = SOURCE_REGISTRY.get(source_id, {})

    def available(self) -> bool:
        return bool(self.api_key) and self.source_info.get("api_available", False)

    def fetch(self) -> List[PoliticalTradeRecord]:
        if not self.available():
            name = self.source_info.get("name", self.source_id)
            note = self.source_info.get("note", "")
            logger.info(
                f"[PLACEHOLDER] {name}: API not configured. "
                f"Use manual CSV import instead. {note}"
            )
            return []
        # Override this in future subclasses when API is integrated
        logger.warning(f"APISourcePlaceholder.fetch() called but no implementation for {self.source_id}")
        return []


# ─────────────────────────────────────────────
#  Sample CSV generator
# ─────────────────────────────────────────────

SAMPLE_CSV_ROWS = [
    {
        "source": "capitol_trades",
        "politician_name": "SAMPLE POLITICIAN A",
        "role": "Representative",
        "party": "Democrat",
        "state": "CA",
        "committee": "House Financial Services Committee",
        "ticker": "NVDA",
        "company_name": "NVIDIA Corporation",
        "transaction_type": "buy",
        "transaction_date": "2025-01-15",
        "filing_date": "2025-02-01",
        "amount_min": "15001",
        "amount_max": "50000",
        "notes": "SAMPLE DATA — Replace with real public disclosure",
        "source_url": "https://www.capitoltrades.com",
    },
    {
        "source": "senate_disclosures",
        "politician_name": "SAMPLE SENATOR B",
        "role": "Senator",
        "party": "Republican",
        "state": "TX",
        "committee": "Senate Commerce Committee",
        "ticker": "MSFT",
        "company_name": "Microsoft Corporation",
        "transaction_type": "buy",
        "transaction_date": "2025-01-20",
        "filing_date": "2025-02-10",
        "amount_min": "50001",
        "amount_max": "100000",
        "notes": "SAMPLE DATA — Replace with real public disclosure",
        "source_url": "https://efts.senate.gov",
    },
    {
        "source": "quiver_quantitative",
        "politician_name": "SAMPLE REPRESENTATIVE C",
        "role": "Representative",
        "party": "Republican",
        "state": "FL",
        "committee": "House Intelligence Committee",
        "ticker": "PLTR",
        "company_name": "Palantir Technologies",
        "transaction_type": "buy",
        "transaction_date": "2025-01-05",
        "filing_date": "2025-01-28",
        "amount_min": "100001",
        "amount_max": "250000",
        "notes": "SAMPLE DATA — Replace with real public disclosure",
        "source_url": "https://www.quiverquant.com/congresstrading/",
    },
    {
        "source": "house_disclosures",
        "politician_name": "SAMPLE REPRESENTATIVE D",
        "role": "Representative",
        "party": "Democrat",
        "state": "NY",
        "committee": "House Energy and Commerce Committee",
        "ticker": "AMZN",
        "company_name": "Amazon.com Inc",
        "transaction_type": "sell",
        "transaction_date": "2024-12-10",
        "filing_date": "2025-01-15",
        "amount_min": "250001",
        "amount_max": "500000",
        "notes": "SAMPLE DATA — Replace with real public disclosure",
        "source_url": "https://disclosures-clerk.house.gov",
    },
]


def create_sample_csv() -> Path:
    """Create a sample CSV template with example rows."""
    DATA_POLITICAL.mkdir(parents=True, exist_ok=True)
    SAMPLE_CSV.parent.mkdir(parents=True, exist_ok=True)

    with open(SAMPLE_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=REQUIRED_CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(SAMPLE_CSV_ROWS)

    logger.info(f"Sample CSV created: {SAMPLE_CSV}")
    return SAMPLE_CSV


# ─────────────────────────────────────────────
#  Master source loader
# ─────────────────────────────────────────────

def load_all_sources(config: Optional[dict] = None) -> List[PoliticalTradeRecord]:
    """
    Load political trade records from all available sources.

    Priority order:
      1. Auto-fetchers  — Senate eFTS (free, no key) + ProPublica enrichment
                          (free key via email). Runs automatically.
      2. Manual CSV     — Supplement: anything you copy-paste manually
                          from Capitol Trades / Quiver / Unusual Whales / etc.
      3. Paid API stubs — Logs placeholders for future Quiver / Unusual Whales
                          integration when you're ready to upgrade.

    Returns deduplicated list of PoliticalTradeRecord objects.
    """
    all_records: List[PoliticalTradeRecord] = []

    # 1. Auto-fetchers (Senate eFTS + ProPublica enrichment + Manual CSV)
    try:
        from src.political.fetchers.auto_fetch_manager import run_auto_fetch
        sources_config = (config or {}).get("political_sources", {}).get("sources", {})
        auto_records = run_auto_fetch(config=sources_config)
        all_records.extend(auto_records)
        logger.info(f"Auto-fetch pipeline: {len(auto_records)} records")
    except Exception as e:
        logger.warning(f"Auto-fetch pipeline failed ({e}) — falling back to manual CSV only")
        # Fallback: manual CSV directly
        csv_loader = ManualCSVSource()
        all_records.extend(csv_loader.load())

    # 2. Paid API placeholders (logs info when not configured, never crashes)
    for source_id in ["capitol_trades", "quiver_quantitative", "unusual_whales"]:
        sources_config = (config or {}).get("political_sources", {}).get("sources", {})
        api_key = sources_config.get(source_id, {}).get("api_key")
        placeholder = APISourcePlaceholder(source_id=source_id, api_key=api_key)
        all_records.extend(placeholder.fetch())

    # 3. Ensure sample CSV exists for reference
    if not SAMPLE_CSV.exists():
        create_sample_csv()

    logger.info(f"Total political records loaded: {len(all_records)}")
    return all_records


def list_sources_status() -> List[dict]:
    """Return status of all political data sources."""
    status = []
    for sid, info in SOURCE_REGISTRY.items():
        csv_loader = ManualCSVSource()
        status.append({
            "source_id": sid,
            "name": info["name"],
            "url": info["url"],
            "type": info["type"],
            "reliability": info["reliability"],
            "api_available": info.get("api_available", False),
            "manual_import_supported": info.get("manual_import_supported", False),
            "has_manual_csv": csv_loader.available(),
            "note": info.get("note", ""),
        })
    return status
