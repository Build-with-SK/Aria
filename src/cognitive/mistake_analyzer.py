"""
mistake_analyzer.py — ARIA Mistake Analyzer
Finds systematic error patterns in ARIA's resolved outcomes.

A human trader reviews their trade journal and asks: "Why do I keep losing
in this specific situation?" This module does that automatically.
"""

from collections import defaultdict
from typing import Optional


class MistakeAnalyzer:
    """
    Reads resolved outcomes and surfaces systematic blind spots.
    Produces plain-English summaries ARIA can internalize as lessons.
    """

    MIN_SAMPLES = 3  # Minimum outcomes before drawing a pattern

    def __init__(self, outcome_tracker):
        self.tracker = outcome_tracker

    # ── Main analysis ─────────────────────────────────────────────────────────

    def analyze(self) -> dict:
        """
        Full mistake analysis across all resolved outcomes.
        Returns structured dict with all detected patterns and lessons.
        """
        resolved = self.tracker.recent_resolved(200)
        if len(resolved) < self.MIN_SAMPLES:
            return {"status": "insufficient_data", "lessons": [], "patterns": []}

        patterns = []
        lessons = []

        patterns.extend(self._regime_accuracy(resolved))
        patterns.extend(self._confidence_calibration(resolved))
        patterns.extend(self._direction_bias(resolved))
        patterns.extend(self._worst_tickers(resolved))
        patterns.extend(self._score_vs_outcome(resolved))

        for p in patterns:
            if p.get("severity") in ("HIGH", "MEDIUM"):
                lessons.append(p["lesson"])

        return {
            "status": "ok",
            "total_analyzed": len(resolved),
            "patterns": patterns,
            "lessons": lessons,
            "critical_lessons": [l for p in patterns if p.get("severity") == "HIGH" for l in [p["lesson"]]],
        }

    # ── Pattern detectors ─────────────────────────────────────────────────────

    def _regime_accuracy(self, resolved: list) -> list:
        """Find regimes where ARIA systematically underperforms."""
        by_regime = defaultdict(lambda: {"hits": 0, "misses": 0, "total": 0})
        for r in resolved:
            regime = r.get("regime", "unknown")
            verdict = r.get("final_verdict")
            by_regime[regime]["total"] += 1
            if verdict == "HIT":
                by_regime[regime]["hits"] += 1
            elif verdict == "MISS":
                by_regime[regime]["misses"] += 1

        patterns = []
        for regime, stats in by_regime.items():
            if stats["total"] < self.MIN_SAMPLES:
                continue
            miss_rate = stats["misses"] / stats["total"]
            hit_rate = stats["hits"] / stats["total"]
            if miss_rate > 0.6:
                patterns.append({
                    "type": "REGIME_BLIND_SPOT",
                    "severity": "HIGH",
                    "regime": regime,
                    "miss_rate": round(miss_rate * 100, 1),
                    "sample_size": stats["total"],
                    "lesson": (
                        f"In '{regime}' regimes, I am wrong {miss_rate*100:.0f}% of the time "
                        f"(sample: {stats['total']}). I should be more cautious and require higher "
                        f"conviction before acting in this regime."
                    ),
                })
            elif hit_rate > 0.7:
                patterns.append({
                    "type": "REGIME_STRENGTH",
                    "severity": "INFO",
                    "regime": regime,
                    "hit_rate": round(hit_rate * 100, 1),
                    "sample_size": stats["total"],
                    "lesson": (
                        f"In '{regime}' regimes, I am right {hit_rate*100:.0f}% of the time. "
                        f"I can apply higher conviction to signals generated in this regime."
                    ),
                })
        return patterns

    def _confidence_calibration(self, resolved: list) -> list:
        """Check if HIGH confidence calls are actually more accurate than LOW."""
        by_conf = defaultdict(lambda: {"hits": 0, "misses": 0, "total": 0})
        for r in resolved:
            conf = r.get("confidence", "UNKNOWN")
            verdict = r.get("final_verdict")
            by_conf[conf]["total"] += 1
            if verdict == "HIT":
                by_conf[conf]["hits"] += 1
            elif verdict == "MISS":
                by_conf[conf]["misses"] += 1

        patterns = []
        high = by_conf.get("HIGH", {})
        low = by_conf.get("LOW", {})

        if high.get("total", 0) >= self.MIN_SAMPLES and low.get("total", 0) >= self.MIN_SAMPLES:
            high_acc = high["hits"] / high["total"]
            low_acc = low["hits"] / low["total"] if low["total"] > 0 else 0

            if high_acc < low_acc:
                patterns.append({
                    "type": "OVERCONFIDENCE_BIAS",
                    "severity": "HIGH",
                    "high_accuracy": round(high_acc * 100, 1),
                    "low_accuracy": round(low_acc * 100, 1),
                    "lesson": (
                        f"CRITICAL: My HIGH confidence calls ({high_acc*100:.0f}% accuracy) are LESS "
                        f"accurate than my LOW confidence calls ({low_acc*100:.0f}% accuracy). "
                        f"I am overconfident. I should treat HIGH confidence as MEDIUM until calibration improves."
                    ),
                })
            elif high_acc - low_acc < 0.1 and high.get("total", 0) > 10:
                patterns.append({
                    "type": "POOR_CALIBRATION",
                    "severity": "MEDIUM",
                    "lesson": (
                        f"My confidence levels are not meaningfully predictive of outcomes. "
                        f"HIGH ({high_acc*100:.0f}%) vs LOW ({low_acc*100:.0f}%) accuracy is nearly identical. "
                        f"The confidence scoring needs recalibration."
                    ),
                })
        return patterns

    def _direction_bias(self, resolved: list) -> list:
        """Detect if I'm systematically biased long or short."""
        long_calls = [r for r in resolved if r.get("direction") == "long"]
        short_calls = [r for r in resolved if r.get("direction") == "short"]
        total = len(resolved)
        if total < self.MIN_SAMPLES:
            return []

        long_pct = len(long_calls) / total
        patterns = []

        if long_pct > 0.80:
            long_hit_rate = sum(1 for r in long_calls if r.get("final_verdict") == "HIT") / len(long_calls) if long_calls else 0
            patterns.append({
                "type": "LONG_BIAS",
                "severity": "MEDIUM",
                "long_pct": round(long_pct * 100, 1),
                "long_hit_rate": round(long_hit_rate * 100, 1),
                "lesson": (
                    f"I have a strong long bias — {long_pct*100:.0f}% of my calls are LONG. "
                    f"This may cause me to miss short opportunities or under-weight bear scenarios. "
                    f"I should consciously challenge every bullish setup with an equally forceful bear case."
                ),
            })
        return patterns

    def _worst_tickers(self, resolved: list) -> list:
        """Find tickers where I consistently get it wrong."""
        by_ticker = defaultdict(lambda: {"hits": 0, "misses": 0})
        for r in resolved:
            ticker = r.get("ticker", "UNKNOWN")
            verdict = r.get("final_verdict")
            if verdict == "HIT":
                by_ticker[ticker]["hits"] += 1
            elif verdict == "MISS":
                by_ticker[ticker]["misses"] += 1

        patterns = []
        for ticker, stats in by_ticker.items():
            total = stats["hits"] + stats["misses"]
            if total < self.MIN_SAMPLES:
                continue
            miss_rate = stats["misses"] / total
            if miss_rate > 0.65:
                patterns.append({
                    "type": "TICKER_BLIND_SPOT",
                    "severity": "MEDIUM",
                    "ticker": ticker,
                    "miss_rate": round(miss_rate * 100, 1),
                    "sample_size": total,
                    "lesson": (
                        f"I am consistently wrong on {ticker} ({miss_rate*100:.0f}% miss rate, "
                        f"n={total}). This ticker may have characteristics my models don't handle well "
                        f"(e.g. event-driven, illiquid, or macro-sensitive beyond my signals). "
                        f"Apply extra skepticism to {ticker} signals."
                    ),
                })
        return patterns

    def _score_vs_outcome(self, resolved: list) -> list:
        """Check if high signal scores actually lead to better outcomes."""
        high_score = [r for r in resolved if r.get("score", 0) > 70]
        low_score = [r for r in resolved if r.get("score", 0) < 40]

        patterns = []
        if len(high_score) >= self.MIN_SAMPLES and len(low_score) >= self.MIN_SAMPLES:
            high_acc = sum(1 for r in high_score if r.get("final_verdict") == "HIT") / len(high_score)
            low_acc = sum(1 for r in low_score if r.get("final_verdict") == "HIT") / len(low_score)

            if high_acc < low_acc:
                patterns.append({
                    "type": "SIGNAL_SCORE_UNRELIABLE",
                    "severity": "HIGH",
                    "lesson": (
                        f"High signal scores (>70) produce LOWER accuracy ({high_acc*100:.0f}%) than "
                        f"low scores (<40) ({low_acc*100:.0f}%). The composite scoring model is mis-calibrated "
                        f"or being gamed by correlated inputs. Review the scoring weights."
                    ),
                })
        return patterns

    # ── Context for ARIA system prompt ────────────────────────────────────────

    def to_context(self) -> str:
        """Format mistake analysis for injection into ARIA's reasoning."""
        analysis = self.analyze()
        if analysis["status"] == "insufficient_data":
            return "[ARIA Mistake Analysis: Insufficient trade history — keep logging decisions to build experience]"

        lines = ["=== ARIA MISTAKE ANALYSIS & LEARNED LESSONS ==="]
        lines.append(f"Based on {analysis['total_analyzed']} resolved trade outcomes:\n")

        if analysis["critical_lessons"]:
            lines.append("CRITICAL LESSONS (HIGH priority — internalize these):")
            for lesson in analysis["critical_lessons"]:
                lines.append(f"  ⚠ {lesson}")
            lines.append("")

        medium_patterns = [p for p in analysis["patterns"] if p.get("severity") == "MEDIUM"]
        if medium_patterns:
            lines.append("MEDIUM PRIORITY PATTERNS:")
            for p in medium_patterns:
                lines.append(f"  • {p['lesson']}")
            lines.append("")

        strengths = [p for p in analysis["patterns"] if p.get("severity") == "INFO"]
        if strengths:
            lines.append("WHERE I PERFORM WELL:")
            for p in strengths:
                lines.append(f"  ✓ {p['lesson']}")

        return "\n".join(lines)
