"""
auto_fetch_manager.py
=====================
Orchestrates all free auto-fetchers for the Political Intelligence Layer.

Free sources managed here:
  ✅ Senate eFTS      — official US Senate PTR disclosures (no key needed)
  ✅ ProPublica       — committee & party data enrichment (free key via email)
  📋 Manual CSV       — fallback for anything not auto-fetchable

Paid sources NOT managed here (you'll know when it's time):
  🔒 Quiver API       — House + Senate + analytics ($50–100/mo)
  🔒 Unusual Whales   — live alerts + options flow ($30–50/mo)

When to upgrade (I'll tell you):
  This file tracks a simple metric: UPGRADE_SIGNAL.
  When it triggers, run_auto_fetch() logs a clear recommendation.
  You don't need to check this manually.

Trading Intelligence System — Phase 4/5
Research and educational purposes only.
"""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from src.political.political_data_schema import PoliticalTradeRecord
from src.political.political_sources import ManualCSVSource, create_sample_csv

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
AUTO_FETCH_STATE = BASE_DIR / "data" / "political" / "raw" / "auto_fetch_state.json"


# ─────────────────────────────────────────────
#  Upgrade signal thresholds
# ─────────────────────────────────────────────
#
#  When these thresholds are consistently hit over 30 days,
#  the system will recommend upgrading to a paid source.
#
#  You don't need to monitor this — run_auto_fetch() will tell you.

UPGRADE_THRESHOLDS = {
    # If Senate-only gives you more than this many tickers on the watchlist
    # consistently, House data (from Quiver) would add significant value.
    "watchlist_tickers_for_upgrade": 15,

    # If you're finding the same tickers repeatedly and want more context
    # (options flow data, historical patterns), Unusual Whales adds that.
    "repeated_tickers_for_upgrade": 8,

    # Days of consecutive high-signal output before recommending upgrade
    "consecutive_high_signal_days": 7,
}

UPGRADE_RECOMMENDATIONS = {
    "quiver": {
        "name": "Quiver Quantitative",
        "why": (
            "Your Senate watchlist has 15+ tickers consistently. "
            "Quiver adds House trading data (435 more politicians), "
            "historical patterns, and portfolio analytics. "
            "This would roughly triple your data coverage."
        ),
        "cost": "~$50–100/month",
        "url": "https://www.quiverquant.com/api",
        "what_to_do": (
            "1. Sign up at quiverquant.com/api\n"
            "2. Get your API key\n"
            "3. Add to configs/political_sources.yaml → quiver_quantitative.api_key\n"
            "4. Tell me you're ready and I'll build the Quiver fetcher"
        ),
    },
    "unusual_whales": {
        "name": "Unusual Whales",
        "why": (
            "You're getting strong political signals but want to cross-reference "
            "with options flow data to confirm institutional positioning. "
            "Unusual Whales ties political disclosures directly to options activity."
        ),
        "cost": "~$30–50/month",
        "url": "https://unusualwhales.com/api",
        "what_to_do": (
            "1. Sign up at unusualwhales.com\n"
            "2. Get API access\n"
            "3. Add to configs/political_sources.yaml → unusual_whales.api_key\n"
            "4. Tell me and I'll build the fetcher"
        ),
    },
}


# ─────────────────────────────────────────────
#  Fetch state tracker
# ─────────────────────────────────────────────

def _load_state() -> dict:
    if AUTO_FETCH_STATE.exists():
        try:
            with open(AUTO_FETCH_STATE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "last_senate_fetch": None,
        "last_propublica_fetch": None,
        "total_records_fetched": 0,
        "watchlist_ticker_history": [],   # list of {date, count} for upgrade tracking
        "upgrade_recommended": [],
    }


def _save_state(state: dict) -> None:
    AUTO_FETCH_STATE.parent.mkdir(parents=True, exist_ok=True)
    with open(AUTO_FETCH_STATE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def _check_upgrade_signals(state: dict, new_ticker_count: int) -> List[str]:
    """
    Check if upgrade thresholds have been hit.
    Returns list of upgrade recommendations to log.
    """
    history = state.get("watchlist_ticker_history", [])
    today = datetime.utcnow().strftime("%Y-%m-%d")

    # Add today's count
    history.append({"date": today, "count": new_ticker_count})
    # Keep only last 30 days
    history = history[-30:]
    state["watchlist_ticker_history"] = history

    recommendations = []

    if len(history) < 5:
        return recommendations   # Not enough data yet

    recent_counts = [h["count"] for h in history[-7:]]
    avg_recent = sum(recent_counts) / len(recent_counts)

    threshold = UPGRADE_THRESHOLDS["watchlist_tickers_for_upgrade"]
    consec_days = UPGRADE_THRESHOLDS["consecutive_high_signal_days"]

    # Check if consistently above threshold
    above_threshold_days = sum(1 for c in recent_counts if c >= threshold)
    if above_threshold_days >= consec_days and "quiver" not in state.get("upgrade_recommended", []):
        recommendations.append("quiver")
        state.setdefault("upgrade_recommended", []).append("quiver")

    return recommendations


# ─────────────────────────────────────────────
#  Main auto-fetch runner
# ─────────────────────────────────────────────

class AutoFetchManager:
    """
    Orchestrates all free political data sources.

    Usage from political_sources.py:
        manager = AutoFetchManager()
        records = manager.fetch_all()

    Runs:
      1. Senate eFTS (always, no key needed)
      2. ProPublica enrichment (if api_key configured)
      3. Manual CSV (always, as a supplement)
      4. Deduplication
      5. Upgrade signal check
    """

    def __init__(self, config: Optional[dict] = None):
        self.config = config or {}
        self._state = _load_state()

    def _run_senate_efts(self, days_back: int = 90) -> List[PoliticalTradeRecord]:
        """Run Senate eFTS fetcher."""
        try:
            from src.political.fetchers.senate_efts import SenatEFTSFetcher
            fetcher = SenatEFTSFetcher(days_back=days_back)
            if not fetcher.available():
                logger.warning("Senate eFTS: requests library not installed. Run: pip install requests")
                return []
            records = fetcher.fetch()
            self._state["last_senate_fetch"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
            return records
        except Exception as e:
            logger.error(f"Senate eFTS fetcher failed: {e}")
            return []

    def _run_propublica_enrichment(self, records: List[PoliticalTradeRecord]) -> List[PoliticalTradeRecord]:
        """Enrich records with ProPublica committee/party data."""
        try:
            from src.political.fetchers.propublica import (
                ProPublicaFetcher, load_propublica_config
            )
            api_key = (
                self.config.get("sources", {}).get("propublica", {}).get("api_key")
                or load_propublica_config()
            )

            fetcher = ProPublicaFetcher(api_key=api_key)

            if not fetcher.available():
                logger.info(
                    "ProPublica: no API key configured. "
                    "Get a FREE key (takes 60 seconds) at: "
                    "propublica.org/datastore/api/propublica-congress-api\n"
                    "Then add it to: configs/political_sources.yaml → "
                    "political_sources.sources.propublica.api_key"
                )
                return records

            roster = fetcher.get_full_roster()
            committee_map = fetcher.enrich_records_with_committees(roster)
            enriched = fetcher.enrich_trade_records(records, roster, committee_map)

            self._state["last_propublica_fetch"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
            logger.info(f"ProPublica: enriched {len(enriched)} records")
            return enriched

        except Exception as e:
            logger.error(f"ProPublica enrichment failed: {e}")
            return records  # Return original records unchanged

    def _run_manual_csv(self) -> List[PoliticalTradeRecord]:
        """Load manual CSV supplement."""
        loader = ManualCSVSource()
        if loader.available():
            records = loader.load()
            logger.info(f"Manual CSV: {len(records)} records loaded")
            return records
        return []

    def _deduplicate(self, records: List[PoliticalTradeRecord]) -> List[PoliticalTradeRecord]:
        """
        Remove duplicate records based on key fields.
        Keeps the record with the more complete data.
        """
        seen = set()
        unique = []
        for r in records:
            # Dedup key: same politician + ticker + transaction date + type
            key = (
                r.politician_name.lower(),
                r.ticker.upper(),
                r.transaction_date,
                r.transaction_type,
            )
            if key not in seen and r.ticker:
                seen.add(key)
                unique.append(r)

        removed = len(records) - len(unique)
        if removed:
            logger.info(f"Deduplication: removed {removed} duplicate records")
        return unique

    def _check_and_log_upgrades(self, ticker_count: int) -> None:
        """Check upgrade signals and log recommendations."""
        upgrades = _check_upgrade_signals(self._state, ticker_count)
        for upgrade_id in upgrades:
            rec = UPGRADE_RECOMMENDATIONS.get(upgrade_id, {})
            if rec:
                logger.warning(
                    f"\n{'─'*60}\n"
                    f"⬆  UPGRADE RECOMMENDATION: {rec['name']}\n"
                    f"   Why now: {rec['why']}\n"
                    f"   Cost: {rec['cost']}\n"
                    f"   URL: {rec['url']}\n"
                    f"   How to upgrade:\n"
                    + "\n".join(f"   {line}" for line in rec['what_to_do'].split("\n"))
                    + f"\n{'─'*60}"
                )

    def fetch_all(self, days_back: int = 90) -> List[PoliticalTradeRecord]:
        """
        Run all free sources and return a deduplicated record list.

        Steps:
          1. Senate eFTS auto-fetch (free, no key)
          2. ProPublica enrichment (free key optional)
          3. Manual CSV supplement
          4. Deduplication
          5. Upgrade signal check
        """
        logger.info("AutoFetchManager: starting free source pipeline...")
        all_records: List[PoliticalTradeRecord] = []

        # 1. Senate eFTS
        logger.info("  [1/3] Senate eFTS auto-fetch...")
        senate_records = self._run_senate_efts(days_back=days_back)
        all_records.extend(senate_records)
        logger.info(f"  Senate eFTS: {len(senate_records)} transactions")

        # 2. ProPublica enrichment
        logger.info("  [2/3] ProPublica enrichment...")
        all_records = self._run_propublica_enrichment(all_records)

        # 3. Manual CSV
        logger.info("  [3/3] Manual CSV supplement...")
        csv_records = self._run_manual_csv()
        all_records.extend(csv_records)

        # 4. Deduplicate
        unique_records = self._deduplicate(all_records)

        # 5. Update state and check upgrade signals
        self._state["total_records_fetched"] = (
            self._state.get("total_records_fetched", 0) + len(unique_records)
        )
        unique_tickers = len(set(r.ticker for r in unique_records if r.ticker))
        self._check_and_log_upgrades(unique_tickers)
        _save_state(self._state)

        # Ensure sample CSV exists for reference
        create_sample_csv()

        logger.info(
            f"AutoFetchManager complete: "
            f"{len(unique_records)} records | "
            f"{unique_tickers} unique tickers | "
            f"Sources: Senate eFTS + Manual CSV"
            + (" + ProPublica" if self._state.get("last_propublica_fetch") else "")
        )
        return unique_records

    def status(self) -> dict:
        """Return current fetch status for display."""
        return {
            "last_senate_fetch": self._state.get("last_senate_fetch"),
            "last_propublica_fetch": self._state.get("last_propublica_fetch"),
            "total_records_fetched": self._state.get("total_records_fetched", 0),
            "upgrade_recommended": self._state.get("upgrade_recommended", []),
            "free_sources": ["senate_efts", "manual_csv"],
            "paid_sources_available": ["quiver_quantitative", "unusual_whales"],
        }


# ─────────────────────────────────────────────
#  Convenience function for political_sources.py
# ─────────────────────────────────────────────

def run_auto_fetch(config: Optional[dict] = None, days_back: int = 90) -> List[PoliticalTradeRecord]:
    """
    Entry point called by political_sources.load_all_sources().
    Returns all records from all free sources.
    """
    manager = AutoFetchManager(config=config)
    return manager.fetch_all(days_back=days_back)
