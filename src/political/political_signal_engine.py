"""
political_signal_engine.py
==========================
Combines the political watchlist with technical, options, futures, macro,
and risk signals to produce final trade candidates.

Scoring formula:
    final_trade_candidate_score =
        0.35 * technical_score
      + 0.15 * options_score
      + 0.15 * futures_score
      + 0.15 * macro_score
      + 0.10 * political_activity_score   ← max 10–15% weight
      + 0.10 * risk_adjusted_score

Political activity is ONE input among six.
It adds a ticker to the watchlist and provides a soft confirmation signal.
It does NOT trigger trades on its own.

Rule:
    "Public political disclosure activity detected.
     Added to watchlist. Trade candidate only if market confirmation agrees."

Trading Intelligence System — Phase 4
Research and educational purposes only.
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from src.political.political_data_schema import (
    TradeCandidateRecord,
    FINAL_SCORE_WEIGHTS,
    TRADE_STATUS_THRESHOLDS,
)
from src.political.political_watchlist import PoliticalWatchlist, PoliticalWatchlistEntry

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent.parent
SIGNALS_PATH = BASE_DIR / "data" / "political" / "political_signals.json"
TRADE_CANDIDATES_PATH = BASE_DIR / "data" / "trade_candidates.json"


# ─────────────────────────────────────────────
#  Score normalisation helpers
# ─────────────────────────────────────────────

def _normalise_political_score(raw: float) -> float:
    """
    Convert political_activity_score (–100 to +100) to 0–100 range.
    Neutral (0) → 50, bullish (+100) → 100, bearish (–100) → 0.
    """
    return round((raw + 100.0) / 2.0, 2)


def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


# ─────────────────────────────────────────────
#  Trade status classifier
# ─────────────────────────────────────────────

def _classify_trade_status(
    final_score: float,
    political_score_raw: float,
    technical_score: float,
    risk_score: float,
    has_options: bool,
    has_futures: bool,
    has_macro: bool,
) -> str:
    """
    Classify the trade status based on the combined scoring framework.

    Rules:
      - Positive political + weak technical → Watchlist Only
      - Positive political + bullish technical → Research Candidate
      - All confirmations → Trade Candidate
      - High risk → Watchlist Only / Avoid
      - If political is sell-heavy but technical/macro still bullish → risk warning only
    """
    thresholds = TRADE_STATUS_THRESHOLDS

    # Hard-fail: extremely poor risk
    if risk_score < 20.0:
        return "Avoid"

    # Risk warning escalation
    if risk_score < 35.0:
        return "Avoid" if final_score < 55.0 else "Watchlist Only"

    # Political-only trigger with no technical confirmation
    if technical_score < 40.0:
        return "Watchlist Only"

    # Full confirmation path
    if final_score >= thresholds["trade_candidate_min"]:
        # Require at least technical and one other confirmation
        confirmations_met = sum([
            technical_score >= 55.0,
            has_options,
            has_futures,
            has_macro,
        ])
        if confirmations_met >= 2:
            return "Trade Candidate"
        return "Research Candidate"

    if final_score >= thresholds["research_candidate_min"]:
        return "Research Candidate"

    if final_score >= thresholds["watchlist_only_min"]:
        return "Watchlist Only"

    return "Avoid"


# ─────────────────────────────────────────────
#  Reason builder
# ─────────────────────────────────────────────

def _build_reason(
    entry: PoliticalWatchlistEntry,
    technical_score: float,
    options_score: float,
    futures_score: float,
    macro_score: float,
    risk_score: float,
    final_score: float,
    trade_status: str,
) -> List[str]:
    reasons = []

    # Political
    if abs(entry.political_activity_score) > 5:
        direction = "buy" if entry.political_activity_score > 0 else "sell"
        reasons.append(
            f"Political disclosure activity detected from {entry.source_count} public source(s): "
            f"{entry.activity_type} ({direction}-side). "
            f"Politicians involved: {len(entry.politicians_involved)}. "
            "Added to watchlist. RESEARCH SIGNAL ONLY."
        )
    else:
        reasons.append(
            "Minimal or neutral political disclosure activity. "
            "No directional political signal."
        )

    # Technical
    if technical_score >= 65:
        reasons.append(f"Technical trend is bullish (score: {technical_score:.0f}).")
    elif technical_score >= 45:
        reasons.append(f"Technical trend is neutral (score: {technical_score:.0f}). Await clearer signal.")
    else:
        reasons.append(f"Technical trend is weak or bearish (score: {technical_score:.0f}). Caution.")

    # Options
    if options_score >= 60:
        reasons.append(f"Options flow is moderately bullish (score: {options_score:.0f}).")
    elif options_score < 40:
        reasons.append(f"Options flow is bearish (score: {options_score:.0f}).")
    else:
        reasons.append(f"Options flow is neutral (score: {options_score:.0f}).")

    # Futures
    if futures_score >= 60:
        reasons.append(f"Futures confirmation is supportive (score: {futures_score:.0f}).")
    elif futures_score < 40:
        reasons.append(f"Futures are bearish (score: {futures_score:.0f}).")
    else:
        reasons.append(f"Futures are neutral (score: {futures_score:.0f}).")

    # Macro
    if macro_score >= 60:
        reasons.append(f"Macro environment is supportive (score: {macro_score:.0f}).")
    elif macro_score < 40:
        reasons.append(f"Macro environment is headwind (score: {macro_score:.0f}).")
    else:
        reasons.append(f"Macro environment is neutral to cautious (score: {macro_score:.0f}).")

    # Risk
    if risk_score >= 60:
        reasons.append(f"Risk conditions acceptable (score: {risk_score:.0f}).")
    elif risk_score < 40:
        reasons.append(f"Elevated risk — consider reduced position size or pass (score: {risk_score:.0f}).")
    else:
        reasons.append(f"Moderate risk — manage position carefully (score: {risk_score:.0f}).")

    # Status
    reasons.append(
        f"Final classification: {trade_status} "
        f"(combined score: {final_score:.1f}/100)."
    )

    return reasons


def _build_confirmation_summary(
    technical_score: float,
    options_score: float,
    futures_score: float,
    macro_score: float,
    risk_score: float,
    political_score_norm: float,
) -> str:
    parts = [
        f"Technical: {technical_score:.0f}",
        f"Options: {options_score:.0f}",
        f"Futures: {futures_score:.0f}",
        f"Macro: {macro_score:.0f}",
        f"Political: {political_score_norm:.0f}",
        f"Risk: {risk_score:.0f}",
    ]
    return " | ".join(parts)


# ─────────────────────────────────────────────
#  Main engine
# ─────────────────────────────────────────────

class PoliticalSignalEngine:
    """
    Combines the political watchlist with other signal sources to
    produce final trade candidates.

    External signal injection:
        Pass a dict of {ticker: {technical_score, options_score, ...}}
        from your existing signal_engine.py.

    If no external signals are available, defaults to neutral (50).

    Usage:
        engine = PoliticalSignalEngine()
        engine.load_watchlist()
        engine.set_external_signals(signal_dict)
        engine.run()
        candidates = engine.get_trade_candidates()
    """

    def __init__(self, config: Optional[dict] = None):
        self.config = config or {}
        self._watchlist: List[PoliticalWatchlistEntry] = []
        self._external_signals: Dict[str, dict] = {}
        self._trade_candidates: List[TradeCandidateRecord] = []
        self._political_signals: List[dict] = []
        self._run_ts = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    # ── Data loading ──────────────────────────

    def load_watchlist(
        self,
        watchlist: Optional[List[PoliticalWatchlistEntry]] = None,
        watchlist_path: Optional[Path] = None,
    ) -> "PoliticalSignalEngine":
        """
        Load watchlist entries.
        If not provided, tries watchlist.json; if absent, runs PoliticalWatchlist.
        """
        if watchlist is not None:
            self._watchlist = watchlist
            logger.info(f"PoliticalSignalEngine: received {len(watchlist)} watchlist entries")
            return self

        # Try loading from saved JSON
        path = watchlist_path or (BASE_DIR / "data" / "political" / "watchlist.json")
        if path.exists():
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
                raw_entries = data.get("watchlist", [])
                self._watchlist = []
                for d in raw_entries:
                    e = PoliticalWatchlistEntry()
                    for k, v in d.items():
                        if hasattr(e, k):
                            setattr(e, k, v)
                    self._watchlist.append(e)
                logger.info(f"PoliticalSignalEngine: loaded {len(self._watchlist)} entries from {path}")
                return self
            except Exception as ex:
                logger.warning(f"Could not load watchlist.json: {ex} — running watchlist pipeline")

        # Fall back to running the full watchlist pipeline
        wl = PoliticalWatchlist(config=self.config.get("watchlist_rules"))
        self._watchlist = wl.run(save=True)
        return self

    def set_external_signals(self, signals: Dict[str, dict]) -> "PoliticalSignalEngine":
        """
        Inject external signal scores from existing signal_engine.py.

        Expected format:
            {
              "NVDA": {
                "technical_score": 68.0,
                "options_score": 55.0,
                "futures_score": 48.0,
                "macro_score": 50.0,
                "risk_score": 63.0,
              },
              ...
            }
        All values 0–100. Missing keys default to neutral (50).
        """
        self._external_signals = {k.upper(): v for k, v in signals.items()}
        logger.info(f"External signals loaded for {len(self._external_signals)} tickers")
        return self

    # ── Processing ────────────────────────────

    def _get_signal(self, ticker: str, key: str, default: float = 50.0) -> float:
        """Get a signal value from external signals dict with safe default."""
        return float(self._external_signals.get(ticker, {}).get(key, default))

    def process(self) -> "PoliticalSignalEngine":
        """Process all watchlist entries into trade candidates."""
        candidates: List[TradeCandidateRecord] = []
        signals_output: List[dict] = []

        for entry in self._watchlist:
            ticker = entry.ticker

            # Get external signals (or neutral defaults)
            technical_score = self._get_signal(ticker, "technical_score", 50.0)
            options_score = self._get_signal(ticker, "options_score", 50.0)
            futures_score = self._get_signal(ticker, "futures_score", 50.0)
            macro_score = self._get_signal(ticker, "macro_score", 50.0)
            risk_score = self._get_signal(ticker, "risk_score", 50.0)

            # Normalise political score to 0–100 for combined formula
            political_norm = _normalise_political_score(entry.political_activity_score)

            # Determine if confirmations are "meaningful" (>55 threshold)
            has_options = options_score >= 55.0
            has_futures = futures_score >= 55.0
            has_macro = macro_score >= 55.0

            # Combined score
            w = FINAL_SCORE_WEIGHTS
            final_score = round(
                w["technical"] * technical_score
                + w["options"] * options_score
                + w["futures"] * futures_score
                + w["macro"] * macro_score
                + w["political"] * political_norm
                + w["risk"] * risk_score,
                2,
            )
            final_score = _clamp(final_score)

            # Classify
            trade_status = _classify_trade_status(
                final_score=final_score,
                political_score_raw=entry.political_activity_score,
                technical_score=technical_score,
                risk_score=risk_score,
                has_options=has_options,
                has_futures=has_futures,
                has_macro=has_macro,
            )

            # Direction
            action = "neutral"
            if entry.political_activity_score > 10 and technical_score > 50:
                action = "bullish"
            elif entry.political_activity_score < -10 and technical_score < 50:
                action = "bearish"

            # Confidence mapping
            confidence_map = {
                "Trade Candidate": "High" if final_score >= 75 else "Medium",
                "Research Candidate": "Medium",
                "Watchlist Only": "Low",
                "Avoid": "Low",
            }
            confidence = confidence_map.get(trade_status, "Low")

            # Build reasons
            reasons = _build_reason(
                entry, technical_score, options_score, futures_score,
                macro_score, risk_score, final_score, trade_status
            )
            confirmation_summary = _build_confirmation_summary(
                technical_score, options_score, futures_score,
                macro_score, risk_score, political_norm,
            )

            # Risk warning
            risk_warning = (
                "Political disclosures are delayed and should NOT be treated "
                "as real-time insider data. All political signals are research-only. "
                "Full technical, options, futures, macro, and risk confirmation required "
                "before considering any trade."
            )
            if entry.warning:
                risk_warning += " | " + entry.warning

            candidate = TradeCandidateRecord(
                ticker=ticker,
                asset_class="Equity",
                action=action,
                final_trade_candidate_score=final_score,
                political_activity_score=entry.political_activity_score,
                technical_score=technical_score,
                options_score=options_score,
                futures_score=futures_score,
                macro_score=macro_score,
                risk_score=risk_score,
                confidence=confidence,
                trade_status=trade_status,
                reason=reasons,
                confirmation_summary=confirmation_summary,
                risk_warning=risk_warning,
                last_updated=self._run_ts,
            )
            candidates.append(candidate)

            # Political signals entry (intermediate output)
            signals_output.append({
                "ticker": ticker,
                "company_name": entry.company_name,
                "political_activity_score": entry.political_activity_score,
                "activity_type": entry.activity_type,
                "buy_count_30d": entry.buy_count_30d,
                "sell_count_30d": entry.sell_count_30d,
                "net_activity_30d": entry.net_activity_30d,
                "source_count": entry.source_count,
                "politicians_involved": entry.politicians_involved,
                "confidence": entry.confidence,
                "warning": entry.warning,
                "committee_relevance_score": entry.committee_relevance_score,
                "politician_influence_score": entry.politician_influence_score,
                "disclosure_delay_avg": entry.disclosure_delay_avg,
                "watchlist_message": (
                    "Public political disclosure activity detected. "
                    "Added to watchlist. Trade candidate only if market confirmation agrees."
                ),
                "last_updated": self._run_ts,
            })

        # Sort by final score descending
        self._trade_candidates = sorted(
            candidates, key=lambda c: c.final_trade_candidate_score, reverse=True
        )
        self._political_signals = sorted(
            signals_output,
            key=lambda s: abs(s.get("political_activity_score", 0)),
            reverse=True,
        )

        logger.info(
            f"PoliticalSignalEngine: {len(self._trade_candidates)} candidates processed. "
            f"Trade Candidates: {sum(1 for c in self._trade_candidates if c.trade_status == 'Trade Candidate')} | "
            f"Research: {sum(1 for c in self._trade_candidates if c.trade_status == 'Research Candidate')} | "
            f"Watchlist: {sum(1 for c in self._trade_candidates if c.trade_status == 'Watchlist Only')} | "
            f"Avoid: {sum(1 for c in self._trade_candidates if c.trade_status == 'Avoid')}"
        )
        return self

    # ── I/O ───────────────────────────────────

    def save_signals(self, path: Optional[Path] = None) -> Path:
        """Save political_signals.json."""
        out = path or SIGNALS_PATH
        out.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "generated_at": self._run_ts,
            "count": len(self._political_signals),
            "data_policy": (
                "Research only. All signals derived from publicly available legal disclosures. "
                "Political activity weight in final score: 10%. "
                "Do NOT trade based solely on political activity."
            ),
            "signals": self._political_signals,
        }
        with open(out, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, default=str)
        logger.info(f"Political signals saved → {out}")
        return out

    def save_trade_candidates(self, path: Optional[Path] = None) -> Path:
        """Save trade_candidates.json."""
        out = path or TRADE_CANDIDATES_PATH
        out.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "generated_at": self._run_ts,
            "count": len(self._trade_candidates),
            "score_formula": {
                "technical_weight": "35%",
                "options_weight": "15%",
                "futures_weight": "15%",
                "macro_weight": "15%",
                "political_weight": "10%  ← max 10–15% — political does NOT dominate",
                "risk_weight": "10%",
            },
            "important_disclaimer": (
                "This is a RESEARCH OUTPUT ONLY. "
                "No automatic trade execution. "
                "No broker connection. "
                "Political signals are based on publicly available disclosures only. "
                "Past political disclosure activity does not guarantee future price movement. "
                "Always apply your own due diligence."
            ),
            "trade_candidates": [c.to_dict() for c in self._trade_candidates],
        }
        with open(out, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, default=str)
        logger.info(f"Trade candidates saved → {out}")
        return out

    def get_trade_candidates(self) -> List[TradeCandidateRecord]:
        return self._trade_candidates

    def get_political_signals(self) -> List[dict]:
        return self._political_signals

    def get_by_status(self, status: str) -> List[TradeCandidateRecord]:
        return [c for c in self._trade_candidates if c.trade_status == status]

    # ── Full pipeline ─────────────────────────

    def run(
        self,
        watchlist: Optional[List[PoliticalWatchlistEntry]] = None,
        external_signals: Optional[Dict[str, dict]] = None,
        save: bool = True,
    ) -> List[TradeCandidateRecord]:
        """
        Full pipeline: load watchlist → inject signals → process → save.

        Args:
            watchlist: Pre-loaded watchlist entries (optional)
            external_signals: Dict of {ticker: signal_scores} from signal_engine.py
            save: Whether to write output files

        Returns:
            List of TradeCandidateRecord objects
        """
        self.load_watchlist(watchlist=watchlist)

        if external_signals:
            self.set_external_signals(external_signals)

        self.process()

        if save:
            self.save_signals()
            self.save_trade_candidates()

        return self._trade_candidates

    # ── Confirmation matrix ───────────────────

    def confirmation_matrix(self) -> List[dict]:
        """
        Generate confirmation matrix for dashboard display.
        Format: Ticker | Political | Technical | Options | Futures | Macro | Risk | Final Status
        """
        matrix = []
        for c in self._trade_candidates:
            pol_norm = _normalise_political_score(c.political_activity_score)
            matrix.append({
                "ticker": c.ticker,
                "political": f"{pol_norm:.0f}",
                "technical": f"{c.technical_score:.0f}",
                "options": f"{c.options_score:.0f}",
                "futures": f"{c.futures_score:.0f}",
                "macro": f"{c.macro_score:.0f}",
                "risk": f"{c.risk_score:.0f}",
                "final_score": f"{c.final_trade_candidate_score:.1f}",
                "trade_status": c.trade_status,
                "action": c.action,
                "confidence": c.confidence,
            })
        return matrix

    def print_summary(self) -> None:
        """Print a console summary table."""
        print("\n" + "=" * 80)
        print("POLITICAL INTELLIGENCE — CONFIRMATION MATRIX")
        print("=" * 80)
        print(f"{'Ticker':<8} {'Pol':>5} {'Tech':>5} {'Opt':>5} "
              f"{'Fut':>5} {'Mac':>5} {'Risk':>5} {'Score':>6}  {'Status'}")
        print("-" * 80)
        for row in self.confirmation_matrix():
            print(
                f"{row['ticker']:<8} "
                f"{row['political']:>5} {row['technical']:>5} {row['options']:>5} "
                f"{row['futures']:>5} {row['macro']:>5} {row['risk']:>5} "
                f"{row['final_score']:>6}  {row['trade_status']}"
            )
        print("=" * 80)
        print("NOTE: Political signals are 10% of the final score. "
              "Trade status requires market confirmation.\n")


# ─────────────────────────────────────────────
#  Standalone convenience function
# ─────────────────────────────────────────────

def run_political_signal_engine(
    external_signals: Optional[Dict[str, dict]] = None,
    config: Optional[dict] = None,
) -> List[TradeCandidateRecord]:
    """
    Entry point for main.py and other modules.
    Returns trade candidates and writes output files.
    """
    engine = PoliticalSignalEngine(config=config or {})
    return engine.run(external_signals=external_signals, save=True)
