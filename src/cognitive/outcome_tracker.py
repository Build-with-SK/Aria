"""
outcome_tracker.py — ARIA Outcome Tracker
Records actual trade results vs predictions so ARIA can learn from experience.

Flow:
  1. When a signal is issued → record_signal_issued()
  2. N days later          → resolve_pending_outcomes() checks actual price
  3. Results feed          → MistakeAnalyzer + ExperienceJournal
"""

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

try:
    import yfinance as yf
    YF_AVAILABLE = True
except ImportError:
    YF_AVAILABLE = False


class OutcomeTracker:
    """Tracks prediction vs reality for every signal ARIA issues."""

    RESOLVE_AFTER_DAYS = [3, 7, 14, 30]  # Check outcome at multiple horizons

    def __init__(self, base_path: str = None):
        if base_path is None:
            base_path = Path(__file__).parent.parent.parent
        self.base_path = Path(base_path)
        self._path = self.base_path / "data" / "memory" / "outcomes.json"
        self._data: dict = {
            "pending": [],    # Signals issued, not yet resolved
            "resolved": [],   # Signals with actual outcome recorded
        }
        self._load()

    # ── Persistence ───────────────────────────────────────────────────────────

    def _load(self):
        if self._path.exists():
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                self._data.update(loaded)
            except Exception:
                pass

    def _save(self):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2, default=str)
        except Exception as e:
            print(f"[OutcomeTracker] Save failed: {e}")

    # ── Record a new signal ────────────────────────────────────────────────────

    def record_signal_issued(
        self,
        ticker: str,
        direction: str,         # "long" | "short" | "neutral"
        confidence: str,        # "HIGH" | "MEDIUM" | "LOW"
        score: float,
        regime: str,
        reasoning: str = "",
        entry_price: Optional[float] = None,
    ):
        """Called when ARIA issues a signal. Queues outcome resolution."""
        ticker = ticker.upper()

        # Try to get current price if not provided
        if entry_price is None and YF_AVAILABLE:
            try:
                t = yf.Ticker(ticker)
                hist = t.history(period="1d")
                if not hist.empty:
                    entry_price = float(hist["Close"].iloc[-1])
            except Exception:
                pass

        record = {
            "id": f"{ticker}_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            "ticker": ticker,
            "direction": direction,
            "confidence": confidence,
            "score": score,
            "regime": regime,
            "reasoning": reasoning[:400],
            "entry_price": entry_price,
            "issued_at": datetime.now().isoformat(),
            "resolve_dates": [
                (datetime.now() + timedelta(days=d)).strftime("%Y-%m-%d")
                for d in self.RESOLVE_AFTER_DAYS
            ],
            "outcomes": {},  # Will be filled: {"7d": {...}, "14d": {...}}
        }
        self._data["pending"].append(record)
        # Keep last 500 pending
        self._data["pending"] = self._data["pending"][-500:]
        self._save()
        return record["id"]

    # ── Resolve pending outcomes ───────────────────────────────────────────────

    def resolve_pending_outcomes(self) -> list:
        """
        Check if any pending signals have reached a resolution date.
        Fetches current price, calculates P&L, classifies as HIT/MISS/PARTIAL.
        Returns list of newly resolved outcomes.
        """
        if not YF_AVAILABLE:
            return []

        today = datetime.now().strftime("%Y-%m-%d")
        newly_resolved = []
        still_pending = []

        for record in self._data["pending"]:
            updated = False
            remaining_dates = []

            for resolve_date in record.get("resolve_dates", []):
                if resolve_date <= today:
                    # Time to check this horizon
                    days = self._days_between(record["issued_at"][:10], resolve_date)
                    horizon_key = f"{days}d"
                    if horizon_key not in record.get("outcomes", {}):
                        outcome = self._fetch_outcome(record, days)
                        if outcome:
                            record.setdefault("outcomes", {})[horizon_key] = outcome
                            updated = True
                else:
                    remaining_dates.append(resolve_date)

            record["resolve_dates"] = remaining_dates

            if not remaining_dates and record.get("outcomes"):
                # All horizons resolved — move to resolved
                record["final_verdict"] = self._compute_final_verdict(record)
                self._data["resolved"].append(record)
                newly_resolved.append(record)
            else:
                still_pending.append(record)

        self._data["pending"] = still_pending
        # Keep last 2000 resolved
        self._data["resolved"] = self._data["resolved"][-2000:]

        if newly_resolved:
            self._save()

        return newly_resolved

    def _fetch_outcome(self, record: dict, days: int) -> Optional[dict]:
        """Fetch actual price movement and classify vs prediction."""
        ticker = record["ticker"]
        entry_price = record.get("entry_price")
        direction = record["direction"]

        try:
            t = yf.Ticker(ticker)
            hist = t.history(period=f"{days + 5}d")
            if hist.empty or entry_price is None:
                return None

            exit_price = float(hist["Close"].iloc[-1])
            pct_move = ((exit_price - entry_price) / entry_price) * 100

            # Was the signal right?
            if direction == "long":
                correct = pct_move > 1.0
                wrong = pct_move < -1.0
            elif direction == "short":
                correct = pct_move < -1.0
                wrong = pct_move > 1.0
            else:
                correct = abs(pct_move) < 2.0  # neutral → expect sideways
                wrong = abs(pct_move) > 5.0

            verdict = "HIT" if correct else ("MISS" if wrong else "FLAT")

            return {
                "exit_price": round(exit_price, 4),
                "pct_move": round(pct_move, 2),
                "verdict": verdict,
                "checked_at": datetime.now().isoformat(),
            }
        except Exception:
            return None

    def _compute_final_verdict(self, record: dict) -> str:
        """Aggregate across all horizons into a single verdict."""
        outcomes = record.get("outcomes", {})
        if not outcomes:
            return "UNRESOLVED"
        hits = sum(1 for o in outcomes.values() if o.get("verdict") == "HIT")
        misses = sum(1 for o in outcomes.values() if o.get("verdict") == "MISS")
        if hits > misses:
            return "HIT"
        elif misses > hits:
            return "MISS"
        return "MIXED"

    def _days_between(self, date1: str, date2: str) -> int:
        try:
            d1 = datetime.strptime(date1, "%Y-%m-%d")
            d2 = datetime.strptime(date2, "%Y-%m-%d")
            return abs((d2 - d1).days)
        except Exception:
            return 7

    # ── Query ─────────────────────────────────────────────────────────────────

    def recent_resolved(self, n: int = 20) -> list:
        return self._data["resolved"][-n:]

    def pending_count(self) -> int:
        return len(self._data["pending"])

    def ticker_outcomes(self, ticker: str) -> list:
        ticker = ticker.upper()
        return [r for r in self._data["resolved"] if r.get("ticker") == ticker]

    def accuracy_stats(self) -> dict:
        """Overall accuracy breakdown."""
        resolved = self._data["resolved"]
        if not resolved:
            return {"total": 0, "hit_rate": None}

        hits = sum(1 for r in resolved if r.get("final_verdict") == "HIT")
        misses = sum(1 for r in resolved if r.get("final_verdict") == "MISS")
        total = len(resolved)

        # By regime
        by_regime = {}
        for r in resolved:
            regime = r.get("regime", "unknown")
            if regime not in by_regime:
                by_regime[regime] = {"hits": 0, "misses": 0}
            if r.get("final_verdict") == "HIT":
                by_regime[regime]["hits"] += 1
            elif r.get("final_verdict") == "MISS":
                by_regime[regime]["misses"] += 1

        # By confidence
        by_confidence = {}
        for r in resolved:
            conf = r.get("confidence", "unknown")
            if conf not in by_confidence:
                by_confidence[conf] = {"hits": 0, "misses": 0}
            if r.get("final_verdict") == "HIT":
                by_confidence[conf]["hits"] += 1
            elif r.get("final_verdict") == "MISS":
                by_confidence[conf]["misses"] += 1

        return {
            "total": total,
            "hits": hits,
            "misses": misses,
            "hit_rate": round(hits / total * 100, 1) if total > 0 else None,
            "by_regime": by_regime,
            "by_confidence": by_confidence,
        }

    def situation_fingerprint(self, ticker: str, regime: str, score: float) -> list:
        """
        Find past resolved outcomes that are similar to a current setup.
        Used for situation recall: 'last time conditions were like this...'
        """
        similar = []
        for r in self._data["resolved"]:
            regime_match = r.get("regime", "").lower() == regime.lower()
            score_similar = abs(r.get("score", 0) - score) < 15
            if regime_match and score_similar:
                similar.append(r)
        return similar[-5:]  # Last 5 similar situations
