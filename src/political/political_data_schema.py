"""
political_data_schema.py
========================
Data schema definitions for the Political Portfolio Intelligence Layer.

Trading Intelligence System — Phase 4
Research and educational purposes only.
Uses only publicly available, legally accessible data.
"""

from dataclasses import dataclass, field, asdict
from typing import Optional, List
from datetime import date, datetime


# ─────────────────────────────────────────────
#  Core Political Trade Record
# ─────────────────────────────────────────────

@dataclass
class PoliticalTradeRecord:
    """
    Single political/public official trade disclosure record.
    All fields sourced from public filings only.
    """

    # Source metadata
    source: str = ""                          # e.g. "capitol_trades", "house_disclosure", "manual_csv"
    source_url: str = ""
    last_updated: str = ""

    # Person
    politician_name: str = ""
    role: str = ""                            # e.g. "Representative", "Senator", "Executive Branch Official"
    chamber_or_office: str = ""               # e.g. "House", "Senate", "OGE", "Executive"
    party: str = ""                           # e.g. "Democrat", "Republican", "Independent"
    state: str = ""
    committee: str = ""                       # Committee membership if relevant

    # Asset
    ticker: str = ""
    company_name: str = ""
    asset_type: str = ""                      # e.g. "Stock", "Option", "Bond", "Mutual Fund"
    sector: str = ""

    # Transaction
    transaction_type: str = "unknown"         # buy / sell / exchange / holding / unknown
    transaction_date: str = ""               # ISO date string YYYY-MM-DD
    filing_date: str = ""                    # ISO date string YYYY-MM-DD
    disclosure_delay_days: int = 0           # filing_date - transaction_date in days

    # Amount (STOCK Act uses ranges, not exact amounts)
    amount_min: float = 0.0
    amount_max: float = 0.0
    estimated_amount_midpoint: float = 0.0   # (amount_min + amount_max) / 2

    ownership_type: str = ""                 # e.g. "Self", "Spouse", "Dependent Child", "Joint"

    # Scoring dimensions (0–100 each)
    committee_relevance_score: float = 0.0   # How relevant the committee is to this sector
    influence_score: float = 0.0             # Seniority / committee chair / leadership position
    recency_score: float = 0.0               # How recent is the transaction
    transaction_size_score: float = 0.0      # Normalised transaction size
    source_reliability_score: float = 0.0    # Official source = higher reliability

    # Composite
    political_activity_score: float = 0.0    # –100 to +100 (+ = bullish activity, – = bearish)

    notes: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# ─────────────────────────────────────────────
#  Watchlist Entry (ticker-level aggregation)
# ─────────────────────────────────────────────

@dataclass
class PoliticalWatchlistEntry:
    """
    Aggregated watchlist entry per ticker.
    Combines all political trade records for a single stock.
    """
    ticker: str = ""
    company_name: str = ""

    # Aggregate scores
    political_activity_score: float = 0.0    # –100 to +100
    activity_type: str = "neutral"           # "accumulation", "distribution", "mixed", "neutral"

    # Transaction timing
    latest_transaction_date: str = ""
    latest_filing_date: str = ""
    disclosure_delay_days: int = 0

    # Source metadata
    source_count: int = 0
    top_sources: List[str] = field(default_factory=list)
    politicians_involved: List[str] = field(default_factory=list)

    # Buy / sell breakdown
    buy_count: int = 0
    sell_count: int = 0
    buy_count_30d: int = 0
    sell_count_30d: int = 0
    net_activity_30d: int = 0               # buy_count_30d - sell_count_30d

    # Reliability
    confidence: str = "Low"                  # Low / Medium / High
    warning: str = ""
    reason: str = ""

    # ML feature fields
    disclosure_delay_avg: float = 0.0
    committee_relevance_score: float = 0.0
    politician_influence_score: float = 0.0
    public_official_exposure_flag: int = 0   # 1 if executive branch / OGE source

    last_updated: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# ─────────────────────────────────────────────
#  Trade Candidate (final combined output)
# ─────────────────────────────────────────────

@dataclass
class TradeCandidateRecord:
    """
    Final combined trade candidate after all confirmation signals applied.

    Political activity is ONE input (max 10–15% weight).
    Technical, options, futures, macro, and risk signals drive the final score.

    IMPORTANT: This is a research signal only.
    No automatic execution. No broker connection.
    """
    ticker: str = ""
    asset_class: str = "Equity"
    action: str = "neutral"                  # bullish / bearish / neutral

    # Combined score (0–100)
    final_trade_candidate_score: float = 0.0

    # Component scores (0–100 each)
    political_activity_score: float = 0.0
    technical_score: float = 0.0
    options_score: float = 50.0             # Default neutral
    futures_score: float = 50.0             # Default neutral
    macro_score: float = 50.0               # Default neutral
    risk_score: float = 50.0                # Default neutral (higher = better risk-adjusted)

    # Classification
    confidence: str = "Low"                  # Low / Medium / High
    trade_status: str = "Watchlist Only"     # Watchlist Only / Research Candidate / Trade Candidate / Avoid

    # Human-readable output
    reason: List[str] = field(default_factory=list)
    confirmation_summary: str = ""
    risk_warning: str = ""

    last_updated: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


# ─────────────────────────────────────────────
#  Scoring weight constants
# ─────────────────────────────────────────────

FINAL_SCORE_WEIGHTS = {
    "technical":   0.35,
    "options":     0.15,
    "futures":     0.15,
    "macro":       0.15,
    "political":   0.10,
    "risk":        0.10,
}

# Watchlist scoring rules (from config, kept here as safe defaults)
WATCHLIST_SCORE_WEIGHTS = {
    "committee_relevance": 0.25,
    "transaction_size":    0.20,
    "recency":             0.20,
    "politician_influence":0.20,
    "source_reliability":  0.15,
}

# Trade status thresholds
TRADE_STATUS_THRESHOLDS = {
    "trade_candidate_min":  65.0,
    "research_candidate_min": 50.0,
    "watchlist_only_min":   35.0,
    # Below watchlist_only_min → Avoid
}

# STOCK Act disclosure ranges (USD)
STOCK_ACT_RANGES = {
    "$1,001 - $15,000":         (1001, 15000),
    "$15,001 - $50,000":        (15001, 50000),
    "$50,001 - $100,000":       (50001, 100000),
    "$100,001 - $250,000":      (100001, 250000),
    "$250,001 - $500,000":      (250001, 500000),
    "$500,001 - $1,000,000":    (500001, 1000000),
    "$1,000,001 - $5,000,000":  (1000001, 5000000),
    "Over $5,000,000":          (5000001, 10000000),
}
