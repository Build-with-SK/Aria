"""
ticker_mapper.py
================
Maps company names to stock tickers.

Needed because Senate PTR XML filings often include company names
("Apple Inc") rather than ticker symbols ("AAPL").

Resolution order:
  1. Static dictionary (instant, top ~200 most commonly disclosed stocks)
  2. Normalisation tricks (strip Inc/Corp/Ltd/LLC suffixes, fuzzy match)
  3. yfinance search (slow, ~1 sec per lookup, used as last resort)
  4. Returns "" if unresolvable (record still saved, just without ticker)

Cache: data/political/raw/ticker_cache.json
       Successful lookups are cached so yfinance is only called once per company.

Trading Intelligence System — Phase 4/5
Research and educational purposes only.
"""

import json
import logging
import re
import time
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
TICKER_CACHE_FILE = BASE_DIR / "data" / "political" / "raw" / "ticker_cache.json"

# ─────────────────────────────────────────────
#  Static lookup — top ~200 most commonly
#  disclosed stocks in congressional filings
# ─────────────────────────────────────────────

STATIC_MAP: Dict[str, str] = {
    # Technology
    "apple inc": "AAPL",
    "apple": "AAPL",
    "microsoft corporation": "MSFT",
    "microsoft corp": "MSFT",
    "microsoft": "MSFT",
    "nvidia corporation": "NVDA",
    "nvidia corp": "NVDA",
    "nvidia": "NVDA",
    "alphabet inc": "GOOGL",
    "alphabet": "GOOGL",
    "google": "GOOGL",
    "amazon.com inc": "AMZN",
    "amazon.com": "AMZN",
    "amazon": "AMZN",
    "meta platforms inc": "META",
    "meta platforms": "META",
    "facebook inc": "META",
    "facebook": "META",
    "tesla inc": "TSLA",
    "tesla": "TSLA",
    "netflix inc": "NFLX",
    "netflix": "NFLX",
    "salesforce inc": "CRM",
    "salesforce.com inc": "CRM",
    "salesforce": "CRM",
    "intel corporation": "INTC",
    "intel corp": "INTC",
    "intel": "INTC",
    "advanced micro devices inc": "AMD",
    "advanced micro devices": "AMD",
    "amd": "AMD",
    "qualcomm incorporated": "QCOM",
    "qualcomm inc": "QCOM",
    "qualcomm": "QCOM",
    "broadcom inc": "AVGO",
    "broadcom": "AVGO",
    "texas instruments incorporated": "TXN",
    "texas instruments inc": "TXN",
    "texas instruments": "TXN",
    "applied materials inc": "AMAT",
    "applied materials": "AMAT",
    "lam research corporation": "LRCX",
    "lam research": "LRCX",
    "micron technology inc": "MU",
    "micron technology": "MU",
    "micron": "MU",
    "western digital corporation": "WDC",
    "western digital": "WDC",
    "seagate technology holdings": "STX",
    "seagate technology": "STX",
    "adobe inc": "ADBE",
    "adobe": "ADBE",
    "oracle corporation": "ORCL",
    "oracle corp": "ORCL",
    "oracle": "ORCL",
    "servicenow inc": "NOW",
    "servicenow": "NOW",
    "workday inc": "WDAY",
    "workday": "WDAY",
    "snowflake inc": "SNOW",
    "snowflake": "SNOW",
    "palantir technologies inc": "PLTR",
    "palantir technologies": "PLTR",
    "palantir": "PLTR",
    "crowdstrike holdings inc": "CRWD",
    "crowdstrike holdings": "CRWD",
    "crowdstrike": "CRWD",
    "palo alto networks inc": "PANW",
    "palo alto networks": "PANW",
    "fortinet inc": "FTNT",
    "fortinet": "FTNT",
    "cloudflare inc": "NET",
    "cloudflare": "NET",
    "datadog inc": "DDOG",
    "datadog": "DDOG",
    "twilio inc": "TWLO",
    "twilio": "TWLO",
    "zoom video communications inc": "ZM",
    "zoom video communications": "ZM",
    "zoom": "ZM",
    "spotify technology": "SPOT",
    "spotify": "SPOT",
    "uber technologies inc": "UBER",
    "uber technologies": "UBER",
    "uber": "UBER",
    "lyft inc": "LYFT",
    "lyft": "LYFT",
    "airbnb inc": "ABNB",
    "airbnb": "ABNB",
    "doordash inc": "DASH",
    "doordash": "DASH",
    "coinbase global inc": "COIN",
    "coinbase global": "COIN",
    "coinbase": "COIN",
    "ibm": "IBM",
    "international business machines corporation": "IBM",
    "international business machines": "IBM",
    "dell technologies inc": "DELL",
    "dell technologies": "DELL",
    "dell": "DELL",
    "hp inc": "HPQ",
    "hewlett packard enterprise company": "HPE",
    "hewlett packard enterprise": "HPE",
    "accenture plc": "ACN",
    "accenture": "ACN",

    # Finance & Banks
    "jpmorgan chase & co": "JPM",
    "jpmorgan chase": "JPM",
    "jp morgan chase": "JPM",
    "bank of america corporation": "BAC",
    "bank of america corp": "BAC",
    "bank of america": "BAC",
    "wells fargo & company": "WFC",
    "wells fargo": "WFC",
    "citigroup inc": "C",
    "citigroup": "C",
    "citibank": "C",
    "goldman sachs group inc": "GS",
    "goldman sachs group": "GS",
    "goldman sachs": "GS",
    "morgan stanley": "MS",
    "charles schwab corporation": "SCHW",
    "charles schwab": "SCHW",
    "blackrock inc": "BLK",
    "blackrock": "BLK",
    "berkshire hathaway inc": "BRK-B",
    "berkshire hathaway": "BRK-B",
    "american express company": "AXP",
    "american express": "AXP",
    "visa inc": "V",
    "visa": "V",
    "mastercard incorporated": "MA",
    "mastercard inc": "MA",
    "mastercard": "MA",
    "paypal holdings inc": "PYPL",
    "paypal holdings": "PYPL",
    "paypal": "PYPL",
    "fidelity national information services": "FIS",
    "fiserv inc": "FISV",
    "fiserv": "FISV",
    "coinbase": "COIN",

    # Healthcare & Pharma
    "unitedhealth group incorporated": "UNH",
    "unitedhealth group inc": "UNH",
    "unitedhealth group": "UNH",
    "johnson & johnson": "JNJ",
    "johnson and johnson": "JNJ",
    "pfizer inc": "PFE",
    "pfizer": "PFE",
    "moderna inc": "MRNA",
    "moderna": "MRNA",
    "merck & co inc": "MRK",
    "merck & co": "MRK",
    "merck": "MRK",
    "abbvie inc": "ABBV",
    "abbvie": "ABBV",
    "bristol-myers squibb company": "BMY",
    "bristol myers squibb": "BMY",
    "eli lilly and company": "LLY",
    "eli lilly": "LLY",
    "amgen inc": "AMGN",
    "amgen": "AMGN",
    "gilead sciences inc": "GILD",
    "gilead sciences": "GILD",
    "biogen inc": "BIIB",
    "biogen": "BIIB",
    "cvs health corporation": "CVS",
    "cvs health": "CVS",
    "cigna corporation": "CI",
    "cigna": "CI",
    "humana inc": "HUM",
    "humana": "HUM",
    "intuitive surgical inc": "ISRG",
    "intuitive surgical": "ISRG",
    "boston scientific corporation": "BSX",
    "boston scientific": "BSX",
    "thermo fisher scientific inc": "TMO",
    "thermo fisher scientific": "TMO",

    # Energy
    "exxon mobil corporation": "XOM",
    "exxon mobil": "XOM",
    "exxonmobil": "XOM",
    "chevron corporation": "CVX",
    "chevron": "CVX",
    "conocophillips": "COP",
    "schlumberger limited": "SLB",
    "schlumberger": "SLB",
    "halliburton company": "HAL",
    "halliburton": "HAL",
    "pioneer natural resources company": "PXD",
    "pioneer natural resources": "PXD",
    "nextera energy inc": "NEE",
    "nextera energy": "NEE",

    # Defense & Aerospace
    "lockheed martin corporation": "LMT",
    "lockheed martin": "LMT",
    "raytheon technologies corporation": "RTX",
    "raytheon technologies": "RTX",
    "rtx corporation": "RTX",
    "northrop grumman corporation": "NOC",
    "northrop grumman": "NOC",
    "general dynamics corporation": "GD",
    "general dynamics": "GD",
    "boeing company": "BA",
    "boeing": "BA",
    "l3harris technologies inc": "LHX",
    "l3harris technologies": "LHX",
    "leidos holdings inc": "LDOS",
    "leidos holdings": "LDOS",
    "leidos": "LDOS",

    # Consumer
    "walmart inc": "WMT",
    "walmart": "WMT",
    "target corporation": "TGT",
    "target corp": "TGT",
    "target": "TGT",
    "costco wholesale corporation": "COST",
    "costco wholesale": "COST",
    "costco": "COST",
    "home depot inc": "HD",
    "home depot": "HD",
    "mcdonalds corporation": "MCD",
    "mcdonald's corporation": "MCD",
    "mcdonald's": "MCD",
    "starbucks corporation": "SBUX",
    "starbucks": "SBUX",
    "nike inc": "NKE",
    "nike": "NKE",
    "disney": "DIS",
    "walt disney company": "DIS",
    "walt disney": "DIS",
    "comcast corporation": "CMCSA",
    "comcast": "CMCSA",
    "at&t inc": "T",
    "at&t": "T",
    "verizon communications inc": "VZ",
    "verizon communications": "VZ",
    "verizon": "VZ",

    # ETFs / Index products most commonly disclosed
    "spdr s&p 500 etf trust": "SPY",
    "spdr s&p 500": "SPY",
    "spy": "SPY",
    "invesco qqq trust": "QQQ",
    "qqq": "QQQ",
    "ishares msci emerging markets etf": "EEM",
    "vanguard total stock market etf": "VTI",
    "vanguard s&p 500 etf": "VOO",
    "ishares core s&p 500 etf": "IVV",
    "ark innovation etf": "ARKK",
}


# ─────────────────────────────────────────────
#  Normalisation helpers
# ─────────────────────────────────────────────

def _normalise(name: str) -> str:
    """Strip legal suffixes and normalise spacing."""
    name = name.lower().strip()
    # Remove legal suffixes
    suffixes = [
        r"\s+inc\.?$", r"\s+corp\.?$", r"\s+corporation$", r"\s+co\.?$",
        r"\s+ltd\.?$", r"\s+llc\.?$", r"\s+plc\.?$", r"\s+holdings?$",
        r"\s+group$", r"\s+companies$", r"\s+company$", r"\s+international$",
        r",\s*inc\.?$", r",\s*corp\.?$", r",\s*ltd\.?$",
    ]
    for suffix in suffixes:
        name = re.sub(suffix, "", name, flags=re.IGNORECASE).strip()
    # Remove punctuation noise
    name = re.sub(r"[&,\.]", " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


def _lookup_static(company_name: str) -> Optional[str]:
    """Direct and normalised lookup in the static dictionary."""
    name_lower = company_name.lower().strip()

    # Direct match
    if name_lower in STATIC_MAP:
        return STATIC_MAP[name_lower]

    # Normalised match
    normalised = _normalise(name_lower)
    if normalised in STATIC_MAP:
        return STATIC_MAP[normalised]

    # Partial match — company name starts with a known key or vice versa
    for key, ticker in STATIC_MAP.items():
        if name_lower.startswith(key) or key.startswith(name_lower):
            return ticker
        if normalised and (normalised in key or key in normalised):
            return ticker

    return None


# ─────────────────────────────────────────────
#  Cache
# ─────────────────────────────────────────────

_cache: Dict[str, str] = {}
_cache_dirty: bool = False


def _load_cache() -> None:
    global _cache
    if TICKER_CACHE_FILE.exists():
        try:
            with open(TICKER_CACHE_FILE, encoding="utf-8") as f:
                _cache = json.load(f)
        except Exception:
            _cache = {}


def _save_cache() -> None:
    global _cache_dirty
    if not _cache_dirty:
        return
    TICKER_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(TICKER_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(_cache, f, indent=2, sort_keys=True)
        _cache_dirty = False
    except Exception as e:
        logger.debug(f"Could not save ticker cache: {e}")


# Initialise cache on import
_load_cache()


# ─────────────────────────────────────────────
#  yfinance fallback
# ─────────────────────────────────────────────

def _yfinance_search(company_name: str) -> Optional[str]:
    """
    Search yfinance for a company name and return the best matching ticker.
    Slow (~1s per call) — only used when static lookup fails.
    """
    try:
        import yfinance as yf
        results = yf.Search(company_name, max_results=3)
        quotes = results.quotes if hasattr(results, "quotes") else []
        if quotes:
            # Prefer results where the quote type is EQUITY
            for q in quotes:
                if q.get("quoteType") in ("EQUITY", "ETF"):
                    return q.get("symbol", "").upper()
            # Fallback: return first result
            return quotes[0].get("symbol", "").upper()
    except Exception as e:
        logger.debug(f"yfinance search failed for '{company_name}': {e}")
    return None


# ─────────────────────────────────────────────
#  Public API
# ─────────────────────────────────────────────

def company_to_ticker(company_name: str, use_yfinance: bool = True) -> str:
    """
    Convert a company name to a ticker symbol.

    Returns:
        Ticker string (e.g. "AAPL") or "" if unresolvable.
    """
    if not company_name or not company_name.strip():
        return ""

    company_clean = company_name.strip()
    cache_key = company_clean.lower()

    # Check runtime cache
    global _cache_dirty
    if cache_key in _cache:
        return _cache[cache_key]

    # 1. Static lookup
    ticker = _lookup_static(company_clean)
    if ticker:
        _cache[cache_key] = ticker
        _cache_dirty = True
        _save_cache()
        return ticker

    # 2. yfinance search (slow path)
    if use_yfinance:
        try:
            ticker = _yfinance_search(company_clean)
            if ticker:
                logger.debug(f"yfinance resolved '{company_clean}' → {ticker}")
                _cache[cache_key] = ticker
                _cache_dirty = True
                _save_cache()
                return ticker
        except Exception:
            pass

    # Store negative result so we don't retry forever
    _cache[cache_key] = ""
    _cache_dirty = True
    _save_cache()
    return ""


def add_static_mapping(company_name: str, ticker: str) -> None:
    """
    Manually add a company → ticker mapping to the static dictionary.
    Persists to ticker_cache.json for future runs.
    """
    global _cache_dirty
    key = company_name.lower().strip()
    STATIC_MAP[key] = ticker.upper()
    _cache[key] = ticker.upper()
    _cache_dirty = True
    _save_cache()
    logger.info(f"Added ticker mapping: '{company_name}' → {ticker.upper()}")
