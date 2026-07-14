"""
metacognition.py — ARIA Metacognition Engine
Generates Vertus-style multi-perspective debate prompts.
Each perspective is a standalone prompt that can be sent to the LLM independently
or combined into a single structured debate call.
"""

from typing import Optional


class MetacognitionEngine:
    """Generates cognitive debate prompts for ARIA's five internal analysts."""

    # ── Individual perspective prompts ────────────────────────────────────────

    def bull_analyst_prompt(self, ticker: str, context: str) -> str:
        return f"""You are the BULL ANALYST in ARIA's internal cognitive debate.
Your role: make the strongest possible BULL case for {ticker}.
Be specific, cite the signals provided, quantify where possible.
Do NOT present a balanced view — you are arguing one side.
Focus on: momentum, catalysts, relative strength, upside scenarios, why the market underestimates this.

CURRENT CONTEXT:
{context}

Present the bull case for {ticker} in 3-5 punchy points. Start with your single strongest argument.
Label your response: [BULL ANALYST — {ticker}]"""

    def bear_analyst_prompt(self, ticker: str, context: str) -> str:
        return f"""You are the BEAR ANALYST in ARIA's internal cognitive debate.
Your role: make the strongest possible BEAR case for {ticker}.
Be specific, cite the signals provided, quantify where possible.
Do NOT hedge — you are the devil's advocate. Find every reason this trade fails.
Focus on: valuation risk, macro headwinds, technical weakness, what consensus is missing,
crowded trade risk, and the scenario where this blows up badly.

CURRENT CONTEXT:
{context}

Present the bear case for {ticker} in 3-5 punchy points. Start with the single biggest risk.
Label your response: [BEAR ANALYST — {ticker}]"""

    def risk_officer_prompt(self, ticker: str, context: str) -> str:
        return f"""You are the RISK OFFICER in ARIA's internal cognitive debate.
Your role: assess risk/reward and position sizing for {ticker}.
You are NOT tasked with predicting direction — you are assessing what can go wrong and how badly.
Think like a risk manager: tail risks, liquidity, correlation to existing portfolio,
VaR contribution, what the maximum drawdown could look like, stop-loss levels,
and whether the current VaR budget can absorb this position.

CURRENT CONTEXT:
{context}

Give a risk assessment for {ticker} covering: max downside, correlation risks,
position sizing recommendation, and what would trigger an immediate exit.
Label your response: [RISK OFFICER — {ticker}]"""

    def quant_view_prompt(self, ticker: str, context: str) -> str:
        return f"""You are the QUANT ANALYST in ARIA's internal cognitive debate.
Your role: assess {ticker} purely from a quantitative signals perspective.
Ignore narratives. Focus entirely on: signal scores, ML model predictions and confidence,
factor exposure, statistical patterns, momentum signals, mean reversion probability,
and the quantitative inputs from the 14-strategy TIS engine.
Call out if qualitative narratives conflict with the quant signals.

CURRENT CONTEXT:
{context}

Give a quant-only view on {ticker}: what do the models say, how much do you trust them,
and what is the stat-sig threshold for acting here?
Label your response: [QUANT ANALYST — {ticker}]"""

    def macro_strategist_prompt(self, ticker: str, context: str, regime: str = "Unknown") -> str:
        return f"""You are the MACRO STRATEGIST in ARIA's internal cognitive debate.
Current macro regime: {regime}

Your role: assess whether {ticker} fits the macro backdrop.
Think top-down: rates environment, credit conditions, sector rotation, global macro flows,
USD strength/weakness, commodity cycle, central bank posture, and whether the current regime
is broadly supportive or hostile for this specific ticker's business model.
Consider: is {ticker} a rates-sensitive play? Cyclical or defensive? EM exposure?

CURRENT CONTEXT:
{context}

Give a macro-lens assessment: does the current regime support or challenge the bull case on {ticker}?
What macro shift would most hurt/help this name?
Label your response: [MACRO STRATEGIST — {ticker}]"""

    def synthesis_prompt(
        self,
        ticker: str,
        bull: str,
        bear: str,
        risk: str,
        quant: str,
        macro: str,
        score: float = None,
    ) -> str:
        score_str = f"Current signal score: {score}/100" if score else ""
        return f"""You are the SYNTHESIS LAYER of ARIA's cognitive debate on {ticker}.
You have just received five analyst perspectives. {score_str}

Your job: do NOT average them. Instead, identify the weight of evidence.
Determine which arguments are most compelling and which are weakest.
Call out explicit contradictions and explain why they exist.
Give a final conviction: HIGH / MEDIUM / LOW with direction LONG / SHORT / NEUTRAL.
Explain in 2-3 sentences what would change your view.
Remind at the end that this is for research purposes only.

=== BULL ANALYST ===
{bull}

=== BEAR ANALYST ===
{bear}

=== RISK OFFICER ===
{risk}

=== QUANT ANALYST ===
{quant}

=== MACRO STRATEGIST ===
{macro}

Now synthesise. Start with: "SYNTHESIS — {ticker}:" followed by your conviction verdict."""

    def devil_advocate_prompt(self, thesis: str, ticker: str) -> str:
        return f"""You are ARIA's devil's advocate. The following thesis has been presented for {ticker}:

"{thesis}"

Your job: attack this thesis as aggressively and specifically as you can.
Find the hidden assumptions. Find the scenario where this is completely wrong.
Find what the thesis author is not considering.
Do NOT agree with anything in the thesis.
Be precise, cite specific risks, and give the strongest possible counterargument.

Label: [DEVIL'S ADVOCATE — {ticker}]"""

    def metacognition_summary_prompt(self, debate: str, ticker: str, view: str) -> str:
        return f"""You are ARIA performing metacognitive review of your own analysis on {ticker}.
Current view: {view}

Here is the full debate output:
{debate[:2000]}

Now ask yourself:
1. Where am I most likely to be wrong?
2. What am I overweighting that I shouldn't be?
3. Is there anything I'm systematically missing?
4. How does this compare to recent decisions I've made on similar names?
5. What would an objective outside observer say about my reasoning quality here?

Be honest and self-critical. Label: [METACOGNITION — {ticker}]"""

    def confidence_calibration(
        self,
        bull_score: float,
        bear_score: float,
        quant_signal: float,
        contradiction_count: int = 0,
    ) -> dict:
        """
        Return calibrated confidence level and explanation.
        Inputs are 0-100 scaled scores where bull_score / bear_score represent
        the relative strength of each case and quant_signal is the TIS signal score.
        """
        net_score = bull_score - bear_score
        penalty = contradiction_count * 8  # each contradiction reduces confidence

        effective = net_score - penalty

        if abs(effective) > 40 and contradiction_count <= 1:
            level = "HIGH"
            explanation = (
                f"Strong directional conviction with limited internal contradictions. "
                f"Net debate score: {net_score:.0f}, contradictions: {contradiction_count}."
            )
        elif abs(effective) > 20:
            level = "MEDIUM"
            explanation = (
                f"Moderate conviction — some signal conflicts detected. "
                f"Net debate score: {net_score:.0f}, contradictions: {contradiction_count}. "
                f"Position sizing should reflect uncertainty."
            )
        else:
            level = "LOW"
            explanation = (
                f"Low conviction — bulls and bears are near-balanced or contradictions are high. "
                f"Net: {net_score:.0f}, contradictions: {contradiction_count}. "
                f"Wait for cleaner signal before acting."
            )

        direction = "LONG" if effective > 5 else "SHORT" if effective < -5 else "NEUTRAL"

        return {
            "level": level,
            "direction": direction,
            "effective_score": round(effective, 1),
            "explanation": explanation,
        }

    # ── Convenience: single combined debate prompt ────────────────────────────

    def full_debate_prompt(self, ticker: str, context: str, regime: str = "Unknown") -> str:
        """
        Single prompt that asks Claude to run all five perspectives internally
        and synthesise — efficient single-API-call version of the debate.
        """
        return f"""You are ARIA running an internal five-perspective cognitive debate on {ticker}.
Macro regime: {regime}

Run each of these five analyst roles in sequence, then synthesise:

1. BULL ANALYST: Make the strongest possible bull case. Be specific, cite signals.
2. BEAR ANALYST: Make the strongest possible bear case. Find every flaw.
3. RISK OFFICER: Assess tail risks, max drawdown, position sizing, stop levels.
4. QUANT ANALYST: Focus only on signal scores, ML predictions, statistical patterns.
5. MACRO STRATEGIST: Does the current macro regime support or challenge this ticker?
6. SYNTHESIS: Weigh the arguments. Don't average — identify the weight of evidence.
   Give: CONVICTION [HIGH/MEDIUM/LOW] DIRECTION [LONG/SHORT/NEUTRAL]
   Then: what would change this view?

Important: flag any contradictions between perspectives. If ML says one thing and macro says
another, that's a signal in itself. Speak clearly. Reference the numbers.
This is for research purposes only — no trade execution implied.

TICKER: {ticker}
CONTEXT:
{context}

Begin with: "=== ARIA COGNITIVE DEBATE: {ticker} ===" """
