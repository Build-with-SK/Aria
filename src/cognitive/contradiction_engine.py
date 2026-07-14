"""
contradiction_engine.py — ARIA Contradiction Engine
Detects when TIS signals disagree with each other and produces structured reports.
ARIA uses these to give more nuanced, intellectually honest analysis.
"""

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Contradiction:
    severity: str          # 'HIGH' | 'MEDIUM' | 'LOW'
    type: str              # e.g. 'ML_VS_TECHNICAL', 'MACRO_VS_STRATEGY'
    description: str       # Plain English explanation
    ticker: Optional[str] = None
    bull_side: str = ""    # What's pointing up
    bear_side: str = ""    # What's pointing down
    implication: str = ""  # What this means for conviction


@dataclass
class ContradictionReport:
    ticker: Optional[str]
    contradictions: List[Contradiction] = field(default_factory=list)
    overall_severity: str = "NONE"
    summary: str = ""

    def has_contradictions(self) -> bool:
        return len(self.contradictions) > 0

    def to_context(self) -> str:
        if not self.contradictions:
            return "[No material contradictions detected in current signals]"
        lines = [f"=== CONTRADICTION REPORT {'for ' + self.ticker if self.ticker else '(Portfolio-wide)'} ==="]
        lines.append(f"Overall severity: {self.overall_severity} | {len(self.contradictions)} conflict(s) found")
        lines.append(f"Summary: {self.summary}\n")
        for i, c in enumerate(self.contradictions, 1):
            lines.append(f"[{i}] [{c.severity}] {c.type}")
            lines.append(f"    {c.description}")
            if c.bull_side:
                lines.append(f"    Bullish signal: {c.bull_side}")
            if c.bear_side:
                lines.append(f"    Bearish signal: {c.bear_side}")
            if c.implication:
                lines.append(f"    Implication: {c.implication}")
        return "\n".join(lines)


class ContradictionEngine:
    """
    Scans TIS signals for logical conflicts.
    Contradictions are valuable — they indicate uncertainty that should lower conviction.
    """

    # ── Main entry points ────────────────────────────────────────────────────

    def analyse_ticker(self, ticker: str, working_memory) -> ContradictionReport:
        """Run contradiction checks for a single ticker."""
        ctx = working_memory.get_ticker_context(ticker)
        signal = ctx.get("signal", {})
        political = ctx.get("political", {})

        contradictions = []
        contradictions.extend(self._ml_vs_direction(ticker, signal))
        contradictions.extend(self._political_vs_signal(ticker, signal, political))
        contradictions.extend(self._confidence_vs_score(ticker, signal))
        contradictions.extend(self._strategy_vs_ml(ticker, signal))

        return self._build_report(ticker, contradictions)

    def analyse_portfolio(self, working_memory) -> ContradictionReport:
        """Run portfolio-wide contradiction checks."""
        contradictions = []
        meta = working_memory.get_meta()
        all_signals = working_memory.all_signals()

        contradictions.extend(self._macro_vs_strategy_consensus(meta, all_signals))
        contradictions.extend(self._var_vs_positioning(meta, all_signals))
        contradictions.extend(self._regime_vs_longs(meta, working_memory.top_longs(10)))
        contradictions.extend(self._ml_consensus_vs_technical(all_signals))

        return self._build_report(None, contradictions)

    # ── Ticker-level checks ──────────────────────────────────────────────────

    def _ml_vs_direction(self, ticker: str, signal: dict) -> List[Contradiction]:
        out = []
        if not signal:
            return out

        direction = signal.get("direction", "")
        ml_pred = signal.get("ml_prediction", "")
        ml_conf = signal.get("ml_confidence", 0)

        if not direction or not ml_pred:
            return out

        # ML and strategy direction are opposite with decent confidence
        long_up = direction == "long" and ml_pred in ("down", "bearish", "negative")
        short_up = direction == "short" and ml_pred in ("up", "bullish", "positive")

        if (long_up or short_up) and ml_conf > 0.5:
            severity = "HIGH" if ml_conf > 0.65 else "MEDIUM"
            out.append(Contradiction(
                severity=severity,
                type="ML_VS_TECHNICAL",
                ticker=ticker,
                description=(
                    f"The strategy engine is {direction.upper()} on {ticker} but the ML ensemble "
                    f"predicts {ml_pred} with {ml_conf:.0%} confidence."
                ),
                bull_side="Strategy/technical signals" if direction == "long" else f"ML ({ml_pred})",
                bear_side=f"ML ensemble ({ml_pred}, {ml_conf:.0%} conf)" if direction == "long" else "Strategy engine",
                implication=(
                    "ML vs technical disagreement at this confidence level historically signals "
                    "mean-reversion or trend exhaustion. Reduce sizing, widen stops."
                ),
            ))
        return out

    def _political_vs_signal(self, ticker: str, signal: dict, political: dict) -> List[Contradiction]:
        out = []
        if not signal or not political:
            return out

        direction = signal.get("direction", "")
        score = signal.get("score", 50)
        flag = political.get("flag") or political.get("risk_flag")
        pol_sentiment = political.get("sentiment", "neutral")

        if direction == "long" and flag and score > 60:
            out.append(Contradiction(
                severity="MEDIUM",
                type="POLITICAL_RISK_VS_BULLISH_SIGNAL",
                ticker=ticker,
                description=(
                    f"Strong buy signal (score={score}) on {ticker} but political intelligence "
                    f"has flagged this name: {political.get('reason', 'political risk flagged')}."
                ),
                bull_side=f"Signal score: {score}",
                bear_side=f"Political flag: {political.get('reason', 'flagged')}",
                implication="Congressional disclosure data or regulatory risk may not be priced in.",
            ))

        if direction == "long" and pol_sentiment in ("bearish", "negative", "sell"):
            out.append(Contradiction(
                severity="LOW",
                type="POLITICAL_SENTIMENT_VS_SIGNAL",
                ticker=ticker,
                description=f"Political sentiment for {ticker} is {pol_sentiment} but strategy is LONG.",
                bull_side="Technical/quant signal",
                bear_side=f"Political sentiment: {pol_sentiment}",
                implication="Monitor for news flow — political headwinds can override technical signals.",
            ))

        return out

    def _confidence_vs_score(self, ticker: str, signal: dict) -> List[Contradiction]:
        """Flag when a ticker has a high signal score but low ML confidence — hollow conviction."""
        out = []
        score = signal.get("score", 0)
        ml_conf = signal.get("ml_confidence")

        if score and ml_conf is not None and isinstance(score, (int, float)):
            if score > 70 and ml_conf < 0.45:
                out.append(Contradiction(
                    severity="LOW",
                    type="HIGH_SCORE_LOW_ML_CONFIDENCE",
                    ticker=ticker,
                    description=(
                        f"{ticker} has a high signal score ({score}) but ML confidence is only {ml_conf:.0%}. "
                        f"The technical thesis is not supported by the ensemble."
                    ),
                    bull_side=f"Signal score: {score}",
                    bear_side=f"ML confidence: {ml_conf:.0%}",
                    implication="Score may be driven by a few factors — check strategy breakdown.",
                ))
        return out

    def _strategy_vs_ml(self, ticker: str, signal: dict) -> List[Contradiction]:
        """Check if individual strategy signals conflict with ML."""
        out = []
        strategies = signal.get("strategies", {})
        ml_pred = signal.get("ml_prediction", "")

        if not strategies or not ml_pred:
            return out

        bullish_strats = [s for s, d in strategies.items() if isinstance(d, dict) and d.get("signal") == "long"]
        bearish_strats = [s for s, d in strategies.items() if isinstance(d, dict) and d.get("signal") == "short"]

        if len(bullish_strats) >= 3 and ml_pred in ("down", "bearish", "negative"):
            out.append(Contradiction(
                severity="MEDIUM",
                type="MULTI_STRATEGY_VS_ML",
                ticker=ticker,
                description=(
                    f"{len(bullish_strats)} strategies are bullish on {ticker} "
                    f"({', '.join(bullish_strats[:3])}) but ML predicts {ml_pred}."
                ),
                bull_side=f"{len(bullish_strats)} strategy signals",
                bear_side=f"ML ensemble: {ml_pred}",
                implication=(
                    "Strategy consensus vs ML disagreement. ML may be picking up on "
                    "factor dynamics the rule-based strategies miss."
                ),
            ))
        return out

    # ── Portfolio-level checks ────────────────────────────────────────────────

    def _macro_vs_strategy_consensus(self, meta: dict, all_signals: dict) -> List[Contradiction]:
        out = []
        regime = meta.get("regime", "")
        combined_score = meta.get("combined_score")

        if not regime or combined_score is None:
            return out

        defensive_regimes = ("recession", "contraction", "bear", "crisis")
        if any(r in regime.lower() for r in defensive_regimes):
            longs = sum(1 for d in all_signals.values() if isinstance(d, dict) and d.get("direction") == "long")
            if longs > 8 and isinstance(combined_score, (int, float)) and combined_score > 50:
                out.append(Contradiction(
                    severity="HIGH",
                    type="MACRO_REGIME_VS_LONG_BIAS",
                    description=(
                        f"Macro regime is '{regime}' (defensive/bearish) but the portfolio has "
                        f"{longs} long positions with a bullish combined score of {combined_score}."
                    ),
                    bull_side=f"Strategy engine: {combined_score} combined score, {longs} longs",
                    bear_side=f"Macro regime: {regime}",
                    implication=(
                        "This is a significant regime-positioning mismatch. Either the macro "
                        "regime read is lagging or the strategies are overfit to recent momentum."
                    ),
                ))
        return out

    def _var_vs_positioning(self, meta: dict, all_signals: dict) -> List[Contradiction]:
        out = []
        var = meta.get("var_1d")
        n_positions = len([d for d in all_signals.values() if isinstance(d, dict) and d.get("direction")])

        if var and isinstance(var, (int, float)) and n_positions > 0:
            var_per_position = var / n_positions
            if var_per_position > 500:
                out.append(Contradiction(
                    severity="LOW",
                    type="HIGH_VAR_PER_POSITION",
                    description=(
                        f"Average VaR per position is ${var_per_position:,.0f} "
                        f"(Total VaR: ${var:,.0f} across {n_positions} positions)."
                    ),
                    implication="Portfolio is concentrated. Diversification would reduce per-position risk.",
                ))
        return out

    def _regime_vs_longs(self, meta: dict, top_longs: list) -> List[Contradiction]:
        out = []
        regime = meta.get("regime", "").lower()
        if not regime or not top_longs:
            return out

        # In risk-off regime, flag if top longs are cyclicals
        cyclicals = {"tsla", "nvda", "amzn", "meta", "googl", "msft", "aapl"}
        if "recovery" in regime or "expansion" in regime:
            return out  # This is fine

        top_tickers = {t.lower() for t, _ in top_longs[:5]}
        overlap = top_tickers & cyclicals
        if overlap and "recession" in regime:
            out.append(Contradiction(
                severity="MEDIUM",
                type="CYCLICAL_LONGS_IN_DEFENSIVE_REGIME",
                description=(
                    f"Regime is '{regime}' but top longs include cyclicals: "
                    f"{', '.join(t.upper() for t in overlap)}."
                ),
                implication="Consider rotating into defensives or reducing gross exposure.",
            ))
        return out

    def _ml_consensus_vs_technical(self, all_signals: dict) -> List[Contradiction]:
        out = []
        ml_down = sum(
            1 for d in all_signals.values()
            if isinstance(d, dict) and d.get("ml_prediction") in ("down", "bearish", "negative")
        )
        longs = sum(1 for d in all_signals.values() if isinstance(d, dict) and d.get("direction") == "long")
        total = len(all_signals)

        if total == 0:
            return out

        ml_bearish_pct = ml_down / total
        longs_pct = longs / total

        if ml_bearish_pct > 0.55 and longs_pct > 0.45:
            out.append(Contradiction(
                severity="HIGH",
                type="ML_CONSENSUS_VS_LONG_BIAS",
                description=(
                    f"ML ensemble is bearish on {ml_bearish_pct:.0%} of the universe but the "
                    f"portfolio has long bias ({longs_pct:.0%} longs)."
                ),
                bull_side=f"Technical strategies: {longs_pct:.0%} of universe as longs",
                bear_side=f"ML ensemble: bearish on {ml_bearish_pct:.0%} of universe",
                implication=(
                    "The most important portfolio-level contradiction: ML and strategy engine "
                    "disagree on market direction. This typically signals choppiness ahead."
                ),
            ))
        return out

    # ── Report builder ────────────────────────────────────────────────────────

    def _build_report(self, ticker: Optional[str], contradictions: List[Contradiction]) -> ContradictionReport:
        if not contradictions:
            return ContradictionReport(ticker=ticker, summary="No material contradictions detected.")

        severity_rank = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}
        top_severity = max(severity_rank.get(c.severity, 0) for c in contradictions)
        overall = {3: "HIGH", 2: "MEDIUM", 1: "LOW"}.get(top_severity, "NONE")

        # Sort by severity
        contradictions.sort(key=lambda c: severity_rank.get(c.severity, 0), reverse=True)

        n = len(contradictions)
        high = sum(1 for c in contradictions if c.severity == "HIGH")
        med = sum(1 for c in contradictions if c.severity == "MEDIUM")
        summary = (
            f"{n} contradiction(s) found ({high} high, {med} medium). "
            f"Dominant type: {contradictions[0].type}. "
            f"Confidence should be reduced accordingly."
        )

        return ContradictionReport(
            ticker=ticker,
            contradictions=contradictions,
            overall_severity=overall,
            summary=summary,
        )
