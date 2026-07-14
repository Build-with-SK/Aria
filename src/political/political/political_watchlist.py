"""
political_watchlist.py
======================
Watchlist builder for the Political Portfolio Intelligence Layer.

Aggregates political trade records by ticker, calculates a
political_activity_score (–100 to +100), and produces watchlist.json.

Political activity is a WATCHLIST TRIGGER, not a trade signal.
Every ticker in the watchlist must be confirmed by:
  - Technical analysis
  - Options signals (optional)
  - Futures signals (optional)
  - Macro environment
  - Risk engine

Trading Intelligence System — Phase 4
Research and educational purposes only.
"""

import json
import logging
import os
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from src.political.political_data_schema import (
    PoliticalTradeRecord,
    PoliticalWatchlistEntry,
    WATCHLIST_SCORE_WEIGHTS,
)
from src.political.political_sources import load_all_sources

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent.parent
WATCHLIST_PATH = BASE_DIR / "data" / "political" / "watchlist.json"
DATA_POLITICAL = BASE_DIR / "data" / "political"

# ─────────────────────────────────────────────
#  Configuration defaults (can be overridden via YAML)
# ─────────────────────────────────────────────

DEFAULT_CONFIG = {
    "max_disclosure_age_days": 90,
    "high_priority_age_days": 30,
    "min_transaction_value_estimate": 15000,
    "committee_relevance_weight": 0.25,
    "transaction_size_weight": 0.20,
    "recency_weight": 0.20,
    "politician_influence_weight": 0.20,
    "source_reliability_weight": 0.15,
}

# Committee → sector relevance map (partial; extend as needed)
COMMITTEE_SECTOR_MAP = {
    "house financial services committee": ["Finance", "Banking", "Insurance"],
    "senate banking committee": ["Finance", "Banking"],
    "senate commerce committee": ["Technology", "Telecom", "Consumer"],
    "house energy and commerce committee": ["Energy", "Healthcare", "Technology"],
    "house intelligence committee": ["Defense", "Technology", "Cybersecurity"],
    "senate armed services committee": ["Defense", "Aerospace"],
    "house ways and means committee": ["Finance", "Tax Policy"],
    "senate finance committee": ["Finance", "Tax Policy"],
    "senate health committee": ["Healthcare", "Pharma"],
    "house science committee": ["Technology", "Space"],
    "senate environment and public works": ["Energy", "Utilities"],
}

# Influence tier (override with real seniority/leadership data)
INFLUENCE_TIER = {
    "committee chair": 90,
    "ranking member": 80,
    "subcommittee chair": 70,
    "majority leader": 85,
    "minority leader": 82,
    "speaker": 95,
    "senator": 65,
    "representative": 55,
    "executive": 75,
}


# ─────────────────────────────────────────────
#  Scoring helpers
# ─────────────────────────────────────────────

def _recency_score(transaction_date_str: str, max_age_days: int = 90) -> float:
    """
    Score 0–100 based on how recent the transaction is.
    Transactions older than max_age_days → 0.
    """
    if not transaction_date_str:
        return 0.0
    try:
        txn = datetime.strptime(transaction_date_str, "%Y-%m-%d").date()
        today = datetime.utcnow().date()
        age_days = (today - txn).days
        if age_days < 0:
            age_days = 0
        if age_days >= max_age_days:
            return 0.0
        return round(100.0 * (1.0 - age_days / max_age_days), 2)
    except Exception:
        return 0.0


def _transaction_size_score(midpoint: float, min_value: float = 15000) -> float:
    """
    Score 0–100 based on transaction size (midpoint estimate).
    $15k → 10, $50k → 30, $250k → 65, $1M → 90, $5M+ → 100.
    """
    if midpoint < min_value:
        return 0.0
    # Log scale scoring
    import math
    try:
        # Normalise: log10(midpoint) vs log10($1M) as reference
        score = 10.0 * math.log10(midpoint / min_value)
        return round(min(score, 100.0), 2)
    except Exception:
        return 0.0


def _committee_relevance_score(committee: str, sector: str) -> float:
    """
    Score 0–100 based on how relevant a politician's committee is
    to the sector of the traded stock.
    """
    if not committee:
        return 20.0  # Small base score for any disclosed trade

    committee_lower = committee.lower()
    sector_lower = sector.lower() if sector else ""

    for committee_key, sectors in COMMITTEE_SECTOR_MAP.items():
        if committee_key in committee_lower:
            for s in sectors:
                if s.lower() in sector_lower:
                    return 90.0
            return 60.0  # Committee match but sector mismatch
    return 25.0  # No known committee match


def _influence_score(role: str, committee: str) -> float:
    """
    Score 0–100 based on politician seniority and role.
    """
    role_lower = (role + " " + committee).lower()
    for tier_key, score in sorted(INFLUENCE_TIER.items(), key=lambda x: -x[1]):
        if tier_key in role_lower:
            return float(score)

    # Fallback by role
    if "senator" in role_lower:
        return 65.0
    if "representative" in role_lower or "congressman" in role_lower:
        return 55.0
    if "executive" in role_lower or "official" in role_lower:
        return 70.0
    return 40.0


def _direction_multiplier(transaction_type: str) -> int:
    """
    +1 for bullish activity (buys/accumulation),
    –1 for bearish activity (sells/disposals),
     0 for neutral/unknown.
    """
    t = transaction_type.lower().strip()
    if t in ("buy", "purchase", "bought", "long", "acquired", "acquisition"):
        return 1
    if t in ("sell", "sale", "sold", "disposed", "disposal", "short"):
        return -1
    if t in ("exchange", "transfer", "holding"):
        return 0
    return 0


def _score_single_record(
    record: PoliticalTradeRecord,
    config: dict,
) -> PoliticalTradeRecord:
    """
    Calculate all scoring dimensions for a single record.
    Returns updated record with scores populated.
    """
    max_age = config.get("max_disclosure_age_days", 90)
    min_value = config.get("min_transaction_value_estimate", 15000)

    record.recency_score = _recency_score(record.transaction_date, max_age)
    record.transaction_size_score = _transaction_size_score(
        record.estimated_amount_midpoint or record.amount_max or record.amount_min,
        min_value,
    )
    record.committee_relevance_score = _committee_relevance_score(
        record.committee, record.sector
    )
    record.influence_score = _influence_score(record.role, record.committee)

    # source_reliability_score already set in parser; default if missing
    if not record.source_reliability_score:
        record.source_reliability_score = 50.0

    w = config
    weighted_score = (
        w.get("committee_relevance_weight", 0.25) * record.committee_relevance_score
        + w.get("transaction_size_weight", 0.20) * record.transaction_size_score
        + w.get("recency_weight", 0.20) * record.recency_score
        + w.get("politician_influence_weight", 0.20) * record.influence_score
        + w.get("source_reliability_weight", 0.15) * record.source_reliability_score
    )

    # Apply direction: positive for buys, negative for sells
    direction = _direction_multiplier(record.transaction_type)
    if direction == 0:
        record.political_activity_score = 0.0
    else:
        record.political_activity_score = round(direction * weighted_score, 2)

    return record


# ─────────────────────────────────────────────
#  Aggregation
# ─────────────────────────────────────────────

def _activity_type(buy_count: int, sell_count: int) -> str:
    total = buy_count + sell_count
    if total == 0:
        return "neutral"
    buy_ratio = buy_count / total
    if buy_ratio >= 0.75:
        return "accumulation"
    if buy_ratio <= 0.25:
        return "distribution"
    return "mixed"


def _confidence_level(source_count: int, record_count: int, score_abs: float) -> str:
    if source_count >= 2 and record_count >= 3 and score_abs >= 50:
        return "High"
    if source_count >= 1 and record_count >= 2 and score_abs >= 25:
        return "Medium"
    return "Low"


def _build_warning(entry: PoliticalWatchlistEntry) -> str:
    warnings = []

    if entry.disclosure_delay_days > 45:
        warnings.append(
            f"Long disclosure delay ({entry.disclosure_delay_days}d): "
            "data may be stale — stock may have already moved."
        )
    if entry.disclosure_delay_days > 90:
        warnings.append("Very late filing: treat as historical reference only.")
    if entry.activity_type == "mixed":
        warnings.append("Mixed activity (both buys and sells): no clear direction.")
    if entry.source_count == 1 and entry.confidence == "Low":
        warnings.append("Single source only: cross-reference before acting.")
    if entry.political_activity_score > 0:
        warnings.append(
            "IMPORTANT: Political disclosures are delayed and must NOT be treated "
            "as real-time data. Always confirm with technical, options, futures, "
            "macro, and risk signals before considering a trade."
        )
    return " | ".join(warnings) if warnings else ""


# ─────────────────────────────────────────────
#  Main PoliticalWatchlist class
# ─────────────────────────────────────────────

class PoliticalWatchlist:
    """
    Builds and manages the political disclosure watchlist.

    Usage:
        wl = PoliticalWatchlist()
        wl.run()
        entries = wl.get_watchlist()
    """

    def __init__(self, config: Optional[dict] = None, records: Optional[List[PoliticalTradeRecord]] = None):
        self.config = {**DEFAULT_CONFIG, **(config or {})}
        self._raw_records: Optional[List[PoliticalTradeRecord]] = records
        self._scored_records: List[PoliticalTradeRecord] = []
        self._watchlist: List[PoliticalWatchlistEntry] = []
        self._run_ts: str = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    # ── Data loading ──────────────────────────

    def load(self) -> "PoliticalWatchlist":
        """Load raw records from all available sources."""
        if self._raw_records is None:
            self._raw_records = load_all_sources()
        logger.info(f"PoliticalWatchlist: loaded {len(self._raw_records)} raw records")
        return self

    # ── Scoring ───────────────────────────────

    def score(self) -> "PoliticalWatchlist":
        """Score all raw records."""
        self._scored_records = [
            _score_single_record(r, self.config)
            for r in (self._raw_records or [])
            if r.ticker  # Skip records without a ticker
        ]
        logger.info(f"PoliticalWatchlist: scored {len(self._scored_records)} records")
        return self

    # ── Aggregation ───────────────────────────

    def aggregate(self) -> "PoliticalWatchlist":
        """Aggregate scored records by ticker into watchlist entries."""
        by_ticker: Dict[str, List[PoliticalTradeRecord]] = defaultdict(list)
        for r in self._scored_records:
            by_ticker[r.ticker.upper()].append(r)

        entries: List[PoliticalWatchlistEntry] = []
        today = datetime.utcnow().date()
        cutoff_30d = today - timedelta(days=30)

        for ticker, recs in by_ticker.items():
            # Sorted most recent first
            recs_sorted = sorted(recs, key=lambda r: r.transaction_date or "", reverse=True)
            latest = recs_sorted[0]

            # Aggregate scores (weighted average by recency)
            total_score = sum(r.political_activity_score for r in recs)
            avg_score = round(total_score / len(recs), 2)
            # Clamp to –100 / +100
            avg_score = max(-100.0, min(100.0, avg_score))

            buy_count = sum(1 for r in recs if _direction_multiplier(r.transaction_type) > 0)
            sell_count = sum(1 for r in recs if _direction_multiplier(r.transaction_type) < 0)

            # 30-day activity
            recs_30d = [
                r for r in recs
                if r.transaction_date
                and datetime.strptime(r.transaction_date, "%Y-%m-%d").date() >= cutoff_30d
            ]
            buy_30d = sum(1 for r in recs_30d if _direction_multiplier(r.transaction_type) > 0)
            sell_30d = sum(1 for r in recs_30d if _direction_multiplier(r.transaction_type) < 0)

            sources = list(set(r.source for r in recs))
            politicians = list(set(r.politician_name for r in recs if r.politician_name))
            avg_delay = round(sum(r.disclosure_delay_days for r in recs) / len(recs), 1)
            avg_committee = round(sum(r.committee_relevance_score for r in recs) / len(recs), 2)
            avg_influence = round(sum(r.influence_score for r in recs) / len(recs), 2)

            # Public official exposure flag
            oge_flag = int(any(
                "oge" in r.source.lower() or "executive" in r.chamber_or_office.lower()
                for r in recs
            ))

            activity = _activity_type(buy_count, sell_count)
            confidence = _confidence_level(len(sources), len(recs), abs(avg_score))

            entry = PoliticalWatchlistEntry(
                ticker=ticker,
                company_name=latest.company_name or "",
                political_activity_score=avg_score,
                activity_type=activity,
                latest_transaction_date=latest.transaction_date or "",
                latest_filing_date=latest.filing_date or "",
                disclosure_delay_days=int(avg_delay),
                source_count=len(sources),
                top_sources=sources[:5],
                politicians_involved=politicians[:10],
                buy_count=buy_count,
                sell_count=sell_count,
                buy_count_30d=buy_30d,
                sell_count_30d=sell_30d,
                net_activity_30d=buy_30d - sell_30d,
                confidence=confidence,
                reason=(
                    f"{len(recs)} disclosures from {len(sources)} source(s). "
                    f"Activity: {activity}. "
                    f"Avg score: {avg_score:.1f}."
                ),
                disclosure_delay_avg=avg_delay,
                committee_relevance_score=avg_committee,
                politician_influence_score=avg_influence,
                public_official_exposure_flag=oge_flag,
                last_updated=self._run_ts,
            )
            entry.warning = _build_warning(entry)
            entries.append(entry)

        # Sort by absolute score descending (highest signal first)
        self._watchlist = sorted(entries, key=lambda e: abs(e.political_activity_score), reverse=True)
        logger.info(f"PoliticalWatchlist: {len(self._watchlist)} tickers in watchlist")
        return self

    # ── I/O ───────────────────────────────────

    def save(self, path: Optional[Path] = None) -> Path:
        """Persist watchlist.json."""
        out_path = path or WATCHLIST_PATH
        out_path.parent.mkdir(parents=True, exist_ok=True)

        payload = {
            "generated_at": self._run_ts,
            "record_count": len(self._watchlist),
            "data_policy": (
                "Research only. Uses publicly available legal disclosures. "
                "Political activity is a watchlist trigger, not a trade signal. "
                "Confirm with technical, options, futures, macro, and risk signals before trading."
            ),
            "watchlist": [e.to_dict() for e in self._watchlist],
        }

        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, default=str)

        logger.info(f"PoliticalWatchlist saved → {out_path}")
        return out_path

    def get_watchlist(self) -> List[PoliticalWatchlistEntry]:
        return self._watchlist

    def get_ticker_entry(self, ticker: str) -> Optional[PoliticalWatchlistEntry]:
        ticker = ticker.upper()
        for e in self._watchlist:
            if e.ticker == ticker:
                return e
        return None

    # ── Full pipeline ─────────────────────────

    def run(self, save: bool = True) -> List[PoliticalWatchlistEntry]:
        """Run the full pipeline: load → score → aggregate → save."""
        self.load().score().aggregate()
        if save:
            self.save()
        return self._watchlist

    # ── ML feature export ─────────────────────

    def to_ml_features(self) -> List[dict]:
        """
        Export watchlist as ML feature rows (one per ticker).
        Missing values are filled with safe neutral defaults.
        """
        rows = []
        for e in self._watchlist:
            rows.append({
                "ticker": e.ticker,
                "political_activity_score": e.political_activity_score,
                "political_buy_count_30d": e.buy_count_30d,
                "political_sell_count_30d": e.sell_count_30d,
                "political_net_activity_30d": e.net_activity_30d,
                "disclosure_delay_avg": e.disclosure_delay_avg,
                "committee_relevance_score": e.committee_relevance_score,
                "politician_influence_score": e.politician_influence_score,
                "source_count": e.source_count,
                "public_official_exposure_flag": e.public_official_exposure_flag,
            })
        return rows

    @staticmethod
    def safe_ml_features(ticker: str) -> dict:
        """
        Return safe neutral ML feature values for a ticker
        not found in the political watchlist.
        Prevents missing political data from crashing model training.
        """
        return {
            "ticker": ticker,
            "political_activity_score": 0.0,
            "political_buy_count_30d": 0,
            "political_sell_count_30d": 0,
            "political_net_activity_30d": 0,
            "disclosure_delay_avg": 0.0,
            "committee_relevance_score": 0.0,
            "politician_influence_score": 0.0,
            "source_count": 0,
            "public_official_exposure_flag": 0,
        }


# ─────────────────────────────────────────────
#  Standalone execution helper
# ─────────────────────────────────────────────

def run_political_watchlist(config: Optional[dict] = None) -> List[PoliticalWatchlistEntry]:
    """
    Convenience function for use from main.py or other modules.
    Returns the watchlist entries and writes watchlist.json.
    """
    wl = PoliticalWatchlist(config=config)
    return wl.run(save=True)
