"""
senate_efts.py
==============
Auto-fetcher for the US Senate Electronic Filing & Tracking System (eFTS).

Source: https://efts.senate.gov  (official US Senate public disclosure portal)
Auth:   None required — this is a public government endpoint.
Data:   Periodic Transaction Reports (PTR) — senator stock trades.
        Senators must file within 45 days of a transaction (STOCK Act).

What this fetches automatically:
  - All PTR filings from the last N days
  - For each filing: senator name, state, party (from roster)
  - Transaction detail from the XML attachment:
      ticker, company name, buy/sell/exchange, amount range, date

What it cannot get automatically (limitations of this free source):
  - House trades (different system — house uses disclosures-clerk.house.gov PDFs)
  - Committee assignments (ProPublica fetcher handles this)
  - Executive branch trades (OGE — separate system)

Caching:
  Already-fetched filing IDs are stored in:
    data/political/raw/senate_cache/fetched_ids.json
  So re-running does not re-download old filings.

Trading Intelligence System — Phase 4/5
Research and educational purposes only.
"""

import json
import logging
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import re

try:
    import requests
    _REQUESTS_OK = True
except ImportError:
    _REQUESTS_OK = False

from src.political.political_data_schema import PoliticalTradeRecord
from src.political.fetchers.ticker_mapper import company_to_ticker

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
#  Paths
# ─────────────────────────────────────────────

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
CACHE_DIR = BASE_DIR / "data" / "political" / "raw" / "senate_cache"
FETCHED_IDS_FILE = CACHE_DIR / "fetched_ids.json"
PARTY_ROSTER_FILE = CACHE_DIR / "senator_roster.json"

# ─────────────────────────────────────────────
#  Senate eFTS endpoints
# ─────────────────────────────────────────────

SEARCH_URL = "https://efts.senate.gov/LATEST/search-index"
FILING_URL = "https://efts.senate.gov/LATEST/search-index"   # ?id={filing_id}

HEADERS = {
    "User-Agent": (
        "TradingIntelligenceSystem/4.0 "
        "(Research and educational use; public data only; "
        "contact: educational-research-tool)"
    ),
    "Accept": "application/json, text/xml, */*",
    "Accept-Language": "en-US,en;q=0.9",
}

REQUEST_DELAY = 1.2   # seconds between requests — be a good citizen
TIMEOUT = 15          # seconds


# ─────────────────────────────────────────────
#  Known senator party lookup (fallback static map)
#  Updated as needed — ProPublica fetcher provides the live version.
# ─────────────────────────────────────────────

_STATIC_PARTY_MAP: Dict[str, str] = {
    # Key = "lastname_state" lowercase — expands over time via ProPublica
    # This is just a fallback seed so the system works before ProPublica key is set
}


def _get_party(last_name: str, state: str, roster: dict) -> str:
    """Look up senator party from roster or static map."""
    key = f"{last_name.lower()}_{state.lower()}"
    if roster and key in roster:
        return roster[key]
    return _STATIC_PARTY_MAP.get(key, "Unknown")


# ─────────────────────────────────────────────
#  Cache helpers
# ─────────────────────────────────────────────

def _load_fetched_ids() -> set:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if FETCHED_IDS_FILE.exists():
        try:
            with open(FETCHED_IDS_FILE, encoding="utf-8") as f:
                return set(json.load(f))
        except Exception:
            pass
    return set()


def _save_fetched_ids(ids: set) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with open(FETCHED_IDS_FILE, "w", encoding="utf-8") as f:
        json.dump(list(ids), f)


def _load_senator_roster() -> dict:
    """Load party/state roster saved by ProPublica fetcher."""
    if PARTY_ROSTER_FILE.exists():
        try:
            with open(PARTY_ROSTER_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _cache_xml(filing_id: str, xml_content: str) -> None:
    path = CACHE_DIR / "xml" / f"{filing_id}.xml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(xml_content, encoding="utf-8")


def _load_cached_xml(filing_id: str) -> Optional[str]:
    path = CACHE_DIR / "xml" / f"{filing_id}.xml"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return None


# ─────────────────────────────────────────────
#  Senate eFTS search
# ─────────────────────────────────────────────

def _search_ptrs(from_date: str, to_date: str, page_size: int = 100, offset: int = 0) -> dict:
    """
    Query the Senate eFTS search endpoint for PTR filings.
    Returns the raw JSON response dict.

    API format confirmed from: https://efts.senate.gov
    Uses Elasticsearch JSON response format.
    """
    params = {
        "q": "",
        "report_types": "PTR",
        "dateRange": "custom",
        "fromDate": from_date,
        "toDate": to_date,
        "limit": page_size,
        "start": offset,
    }
    try:
        resp = requests.get(
            SEARCH_URL, params=params, headers=HEADERS, timeout=TIMEOUT
        )
        resp.raise_for_status()
        return resp.json()
    except requests.Timeout:
        logger.error("Senate eFTS search timed out")
        return {}
    except requests.HTTPError as e:
        logger.error(f"Senate eFTS search HTTP error: {e.response.status_code}")
        return {}
    except Exception as e:
        logger.error(f"Senate eFTS search failed: {e}")
        return {}


def _parse_search_results(data: dict) -> List[dict]:
    """
    Extract filing metadata list from Elasticsearch JSON response.

    Senate eFTS returns standard Elasticsearch format:
    {
      "hits": {
        "total": {"value": N},
        "hits": [
          {
            "_id": "uuid",
            "_source": {
              "first_name": ..., "last_name": ...,
              "date_received": "YYYY-MM-DD",
              "report_type": "PTR",
              "senator_name": "Last, First",
              "file_type": "XML",
              ...
            }
          }
        ]
      }
    }
    """
    filings = []
    try:
        hits = data.get("hits", {}).get("hits", [])
        for hit in hits:
            source = hit.get("_source", {})
            filing = {
                "id": hit.get("_id", ""),
                "first_name": source.get("first_name", ""),
                "last_name": source.get("last_name", ""),
                "senator_name": source.get("senator_name", ""),
                "date_received": source.get("date_received", ""),
                "report_type": source.get("report_type", ""),
                "report_title": source.get("report_title", ""),
                "file_type": source.get("file_type", ""),
                "state": source.get("state_or_territory", source.get("state", "")),
            }
            if filing["id"]:
                filings.append(filing)
    except Exception as e:
        logger.error(f"Failed to parse eFTS search results: {e}")
    return filings


def _get_total_results(data: dict) -> int:
    try:
        return data.get("hits", {}).get("total", {}).get("value", 0)
    except Exception:
        return 0


# ─────────────────────────────────────────────
#  Fetch individual PTR XML
# ─────────────────────────────────────────────

def _fetch_filing_xml(filing_id: str) -> Optional[str]:
    """
    Fetch the XML content for a specific PTR filing.

    The Senate stores PTR XML at:
      https://efts.senate.gov/LATEST/search-index?id={filing_id}

    Falls back to the annual XML zip pattern if direct fetch fails.
    """
    # Check cache first
    cached = _load_cached_xml(filing_id)
    if cached:
        return cached

    # Try primary endpoint
    try:
        time.sleep(REQUEST_DELAY)
        resp = requests.get(
            FILING_URL,
            params={"id": filing_id},
            headers={**HEADERS, "Accept": "text/xml, application/xml, */*"},
            timeout=TIMEOUT,
            allow_redirects=True,
        )

        content_type = resp.headers.get("Content-Type", "")

        # If the response is XML directly
        if resp.status_code == 200 and (
            "xml" in content_type.lower()
            or resp.text.strip().startswith("<?xml")
            or resp.text.strip().startswith("<Financial")
        ):
            _cache_xml(filing_id, resp.text)
            return resp.text

        # If it returned JSON with embedded content
        if resp.status_code == 200 and "json" in content_type.lower():
            data = resp.json()
            # Some Elasticsearch responses embed the doc in _source
            source = data.get("_source", {})
            if "content" in source:
                _cache_xml(filing_id, source["content"])
                return source["content"]

        logger.debug(f"Filing {filing_id}: unexpected response type ({content_type})")
        return None

    except requests.Timeout:
        logger.warning(f"Timeout fetching filing {filing_id}")
        return None
    except Exception as e:
        logger.debug(f"Could not fetch filing {filing_id}: {e}")
        return None


# ─────────────────────────────────────────────
#  Parse PTR XML → PoliticalTradeRecord list
# ─────────────────────────────────────────────

def _safe_xml_text(elem, tag: str, default: str = "") -> str:
    """Safely get text from an XML child element."""
    try:
        child = elem.find(tag)
        if child is not None and child.text:
            return child.text.strip()
    except Exception:
        pass
    return default


# XML namespace variants the Senate uses
_NS_VARIANTS = [
    "",            # no namespace
    "{http://www.senate.gov/financial-disclosure}",
    "{urn:senate.gov:financial-disclosure}",
]


def _find_with_ns(parent, tag: str):
    """Try to find an XML element with or without namespace."""
    for ns in _NS_VARIANTS:
        elem = parent.find(f"{ns}{tag}")
        if elem is not None:
            return elem
    # Try a wildcard search
    for child in parent.iter():
        if child.tag.endswith(tag) or child.tag == tag:
            return child
    return None


def _find_all_with_ns(parent, tag: str):
    """Find all XML elements with or without namespace."""
    results = []
    for ns in _NS_VARIANTS:
        found = parent.findall(f"{ns}{tag}")
        if found:
            return found
    # Wildcard fallback
    for child in parent.iter():
        if child.tag.endswith(tag) or child.tag == tag:
            results.append(child)
    return results


def _parse_amount_range_str(amount_str: str) -> Tuple[float, float, float]:
    """
    Parse STOCK Act amount range strings.
    e.g. "$15,001 - $50,000" → (15001, 50000, 32500)
    """
    amount_str = amount_str.strip().replace(",", "")
    # Extract all numbers from the string
    numbers = re.findall(r"\$?(\d+)", amount_str)
    if len(numbers) >= 2:
        lo, hi = float(numbers[0]), float(numbers[1])
        return lo, hi, (lo + hi) / 2
    if len(numbers) == 1:
        val = float(numbers[0])
        return val, val, val
    return 0.0, 0.0, 0.0


def _parse_ptr_xml(
    xml_content: str,
    filing_meta: dict,
    roster: dict,
) -> List[PoliticalTradeRecord]:
    """
    Parse a Senate PTR XML file into PoliticalTradeRecord objects.

    Senate PTR XML structure (documented format):
    <FinancialDisclosure>
      <Member>
        <LastName/> <FirstName/> <StateOrTerritory/>
        <FilingDate/> <ReportTitle/>
      </Member>
      <Transactions>
        <Transaction>
          <TransactionDate/>  <Owner/>      <Ticker/>
          <AssetName/>        <AssetType/>  <Type/>
          <Amount/>           <Comment/>
        </Transaction>
      </Transactions>
    </FinancialDisclosure>
    """
    records = []
    try:
        root = ET.fromstring(xml_content)
    except ET.ParseError as e:
        logger.warning(f"XML parse error for filing {filing_meta.get('id', '?')}: {e}")
        return []

    # ── Member info ──────────────────────────
    member = _find_with_ns(root, "Member")
    if member is None:
        # Try the root itself as member context
        member = root

    last_name = _safe_xml_text(member, "LastName") or filing_meta.get("last_name", "")
    first_name = _safe_xml_text(member, "FirstName") or filing_meta.get("first_name", "")
    # Try namespace variants
    if not last_name:
        for ns in _NS_VARIANTS:
            e = member.find(f"{ns}LastName")
            if e is not None and e.text:
                last_name = e.text.strip()
                break

    state = (
        _safe_xml_text(member, "StateOrTerritory")
        or _safe_xml_text(member, "State")
        or filing_meta.get("state", "")
    )
    filing_date_raw = (
        _safe_xml_text(member, "FilingDate")
        or filing_meta.get("date_received", "")
    )

    # Normalise filing date
    filing_date = ""
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y"):
        try:
            filing_date = datetime.strptime(filing_date_raw, fmt).strftime("%Y-%m-%d")
            break
        except ValueError:
            continue
    if not filing_date:
        filing_date = filing_date_raw

    politician_name = f"{first_name} {last_name}".strip()
    party = _get_party(last_name, state, roster)

    # ── Transactions ─────────────────────────
    transactions_container = _find_with_ns(root, "Transactions")
    if transactions_container is None:
        transactions_container = root  # fallback: search whole tree

    transaction_elems = _find_all_with_ns(transactions_container, "Transaction")

    for txn in transaction_elems:
        def txt(tag):
            # Try with namespace variants, then direct text
            for ns in _NS_VARIANTS:
                e = txn.find(f"{ns}{tag}")
                if e is not None and e.text:
                    return e.text.strip()
            return ""

        txn_date_raw = txt("TransactionDate")
        # Normalise transaction date
        txn_date = ""
        for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y"):
            try:
                txn_date = datetime.strptime(txn_date_raw, fmt).strftime("%Y-%m-%d")
                break
            except ValueError:
                continue
        if not txn_date:
            txn_date = txn_date_raw

        ticker_raw = txt("Ticker").upper().strip()
        asset_name = txt("AssetName")
        asset_type = txt("AssetType")
        txn_type_raw = txt("Type").lower()
        amount_raw = txt("Amount")
        owner = txt("Owner")
        comment = txt("Comment")

        # Skip non-stock assets unless specifically included
        skip_types = {"mutual fund", "etf - mutual fund", "excepted investment fund"}
        if asset_type.lower() in skip_types and not ticker_raw:
            continue

        # Normalise transaction type
        if "purchase" in txn_type_raw or "buy" in txn_type_raw:
            txn_type = "buy"
        elif "sale" in txn_type_raw or "sell" in txn_type_raw:
            txn_type = "sell"
        elif "exchange" in txn_type_raw:
            txn_type = "exchange"
        else:
            txn_type = txn_type_raw or "unknown"

        # Resolve ticker
        ticker = ticker_raw
        if not ticker and asset_name:
            ticker = company_to_ticker(asset_name)

        # Skip if we still have no ticker and it's not a recognisable stock
        if not ticker:
            logger.debug(f"No ticker for: '{asset_name}' — skipping")
            continue

        # Parse amount
        amt_min, amt_max, amt_mid = _parse_amount_range_str(amount_raw)

        # Disclosure delay
        delay = 0
        if txn_date and filing_date:
            try:
                t = datetime.strptime(txn_date, "%Y-%m-%d")
                f = datetime.strptime(filing_date, "%Y-%m-%d")
                delay = max(0, (f - t).days)
            except Exception:
                pass

        record = PoliticalTradeRecord(
            source="senate_efts",
            source_url=f"https://efts.senate.gov/LATEST/search-index?id={filing_meta.get('id', '')}",
            last_updated=datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            politician_name=politician_name,
            role="Senator",
            chamber_or_office="Senate",
            party=party,
            state=state,
            committee="",          # enriched later by ProPublica fetcher
            ticker=ticker,
            company_name=asset_name,
            asset_type=asset_type or "Stock",
            sector="",             # enriched by ticker_mapper
            transaction_type=txn_type,
            transaction_date=txn_date,
            filing_date=filing_date,
            disclosure_delay_days=delay,
            amount_min=amt_min,
            amount_max=amt_max,
            estimated_amount_midpoint=amt_mid,
            ownership_type=owner,
            notes=comment,
            source_reliability_score=95.0,  # Official Senate government source
        )
        records.append(record)

    return records


# ─────────────────────────────────────────────
#  Main fetcher class
# ─────────────────────────────────────────────

class SenatEFTSFetcher:
    """
    Automatically fetches Senate Periodic Transaction Reports (PTR)
    from the official US Senate eFTS disclosure system.

    No API key required. Public government data.

    Usage:
        fetcher = SenatEFTSFetcher(days_back=90)
        records = fetcher.fetch()
    """

    SOURCE_ID = "senate_efts"
    MAX_PAGES = 20          # max 20 pages × 100 results = 2000 filings per run
    PAGE_SIZE = 100

    def __init__(self, days_back: int = 90):
        self.days_back = days_back
        self._fetched_ids = _load_fetched_ids()
        self._roster = _load_senator_roster()

    def available(self) -> bool:
        return _REQUESTS_OK

    def fetch(self) -> List[PoliticalTradeRecord]:
        if not self.available():
            logger.warning("Senate eFTS: requests library not available")
            return []

        today = datetime.utcnow().date()
        from_date = (today - timedelta(days=self.days_back)).strftime("%Y-%m-%d")
        to_date = today.strftime("%Y-%m-%d")

        logger.info(f"Senate eFTS: fetching PTR filings from {from_date} to {to_date}")

        all_records: List[PoliticalTradeRecord] = []
        new_ids: set = set()
        filings_processed = 0
        filings_skipped_cached = 0

        # ── Paginate through search results ──
        for page in range(self.MAX_PAGES):
            offset = page * self.PAGE_SIZE
            logger.debug(f"Senate eFTS: page {page + 1}, offset {offset}")

            data = _search_ptrs(from_date, to_date, self.PAGE_SIZE, offset)
            if not data:
                break

            total = _get_total_results(data)
            filings = _parse_search_results(data)

            if not filings:
                break

            for filing in filings:
                filing_id = filing["id"]

                if filing_id in self._fetched_ids:
                    filings_skipped_cached += 1
                    continue

                if filing.get("file_type", "").upper() != "XML":
                    # PDF-only filing — cannot auto-parse, skip
                    logger.debug(f"Skipping non-XML filing {filing_id}")
                    new_ids.add(filing_id)
                    continue

                # Fetch and parse XML
                xml_content = _fetch_filing_xml(filing_id)
                if xml_content:
                    records = _parse_ptr_xml(xml_content, filing, self._roster)
                    all_records.extend(records)
                    filings_processed += 1
                    new_ids.add(filing_id)
                    logger.debug(
                        f"  ✓ {filing.get('senator_name', '?')}: "
                        f"{len(records)} transaction(s)"
                    )
                else:
                    logger.debug(f"  ✗ Could not fetch XML for {filing_id}")
                    new_ids.add(filing_id)  # Mark so we don't keep retrying

                time.sleep(REQUEST_DELAY)

            # Check if we've fetched all pages
            if offset + self.PAGE_SIZE >= total:
                break

        # Save updated ID cache
        self._fetched_ids.update(new_ids)
        _save_fetched_ids(self._fetched_ids)

        logger.info(
            f"Senate eFTS complete: "
            f"{filings_processed} new filings parsed, "
            f"{filings_skipped_cached} already cached, "
            f"{len(all_records)} total transactions extracted."
        )
        return all_records

    def clear_cache(self) -> None:
        """Clear fetched IDs cache — forces re-fetch of all filings."""
        self._fetched_ids = set()
        _save_fetched_ids(self._fetched_ids)
        logger.info("Senate eFTS cache cleared.")
