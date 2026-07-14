"""
src/political/fetchers/__init__.py
===================================
Free auto-fetchers for the Political Intelligence Layer.

Active sources:
  - SenatEFTSFetcher   : Official US Senate PTR disclosures (no key needed)
  - ProPublicaFetcher  : Committee & party enrichment (free key via email)
  - AutoFetchManager   : Orchestrates all sources + upgrade tracking

Paid sources (not active yet):
  - QuiverFetcher      : Built when you're ready to upgrade
  - UnusualWhalesFetcher: Built when you're ready to upgrade
"""

from src.political.fetchers.senate_efts import SenatEFTSFetcher
from src.political.fetchers.propublica import ProPublicaFetcher
from src.political.fetchers.ticker_mapper import company_to_ticker, add_static_mapping
from src.political.fetchers.auto_fetch_manager import AutoFetchManager, run_auto_fetch

__all__ = [
    "SenatEFTSFetcher",
    "ProPublicaFetcher",
    "company_to_ticker",
    "add_static_mapping",
    "AutoFetchManager",
    "run_auto_fetch",
]
