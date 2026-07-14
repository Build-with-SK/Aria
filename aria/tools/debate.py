"""
ARIA Tool: run_cognitive_debate
Runs a structured multi-agent debate using Claude as each agent.
Agents: Bull, Bear, Risk Manager, Quant, Macro Strategist.
"""

import asyncio
from tools.ticker import get_ticker_data


AGENT_PROMPTS = {
    "Bull": (
        "You are the Bull analyst. Your job is to construct the strongest possible bullish case "
        "for this ticker based on the data provided. Find every positive catalyst, technical "
        "setup, sentiment tailwind, and valuation argument. Do not hedge. Make the case. "
        "3-4 paragraphs. Be specific about price targets and catalysts."
    ),
    "Bear": (
        "You are the Bear analyst. Your job is to construct the strongest possible bearish case "
        "for this ticker. Find every risk, headwind, overvaluation argument, and technical "
        "breakdown scenario. Do not hedge. Make the case. "
        "3-4 paragraphs. Be specific about downside targets and what breaks the bull thesis."
    ),
    "Risk": (
        "You are the Risk Manager. Your job is not to have a directional view but to identify "
        "every risk that both the bull and bear have missed or understated. Focus on: tail risks, "
        "liquidity risks, correlation risks, event risks (earnings, macro, regulatory). "
        "3-4 paragraphs. What would cause a catastrophic outcome on either side?"
    ),
    "Quant": (
        "You are the Quant analyst. You trust signals, data, and models — not narratives. "
        "Evaluate the technical indicators and signal score. What does the data actually say? "
        "Are the signals clean or contradictory? What does the RSI/MACD/Bollinger setup historically "
        "mean for forward returns? 3-4 paragraphs. Be precise."
    ),
    "Macro": (
        "You are the Macro Strategist. Your job is to situate this ticker within the broader macro "
        "environment: interest rate trajectory, USD strength, sector rotation dynamics, "
        "credit conditions, global risk appetite. Is the macro tailwind or headwind for this name? "
        "3-4 paragraphs. Cite specific macro factors."
    ),
}

SYNTHESIS_PROMPT = (
    "You have just read five distinct analyst perspectives on a ticker: Bull, Bear, Risk, Quant, and Macro. "
    "Your job now is to synthesise them into a single calibrated view. "
    "Where do they agree? Where are the genuine disagreements? Which argument has the best evidence? "
    "Give a final verdict: direction, confidence (High/Medium/Low), key catalysts to watch, "
    "and what would change your view. 4-5 paragraphs. No sycophancy — be honest."
)


async def run_cognitive_debate(ticker: str, context: str = "", client=None) -> dict:
    """
    Run a multi-agent debate on a ticker.
    Uses the passed client (anthropic.Anthropic) to call Claude for each agent.
    """
    if client is None:
        return {"error": "No API client provided to debate tool"}

    # Get ticker data first
    data = await get_ticker_data(ticker, period="6mo")
    if "error" in data:
        return {"error": data["error"], "ticker": ticker}

    # Build shared context block
    price = data.get("price", {})
    ind   = data.get("indicators", {})
    sig   = data.get("signal", {})
    fund  = data.get("fundamentals", {})
    ma    = data.get("moving_averages", {})

    data_context = f"""
TICKER: {ticker}
Price: ${price.get('current')} ({price.get('day_change_pct', 0):+.2f}% today)
52w range: ${price.get('52w_low')} — ${price.get('52w_high')}

SIGNAL: {sig.get('score')} / 100 ({sig.get('direction')})

TECHNICALS:
- RSI(14): {ind.get('rsi_14')}
- MACD histogram: {ind.get('macd_histogram')}
- Bollinger %B: {ind.get('bb_pct_b')}
- ATR(14): {ind.get('atr_14')}
- Volume ratio (vs 20d avg): {ind.get('volume_ratio_20d')}

MOVING AVERAGES:
- 20MA: {ma.get('ma20')} | 50MA: {ma.get('ma50')} | 200MA: {ma.get('ma200')}

FUNDAMENTALS:
- Beta: {fund.get('beta')} | P/E: {fund.get('pe_trailing')} | Sector: {fund.get('sector')}
- Short float: {fund.get('short_float')}

ADDITIONAL CONTEXT: {context if context else 'None provided'}
"""

    debate_results = {}

    # Run each agent
    for agent_name, agent_instruction in AGENT_PROMPTS.items():
        try:
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda a=agent_instruction, d=data_context: client.messages.create(
                    model="claude-sonnet-4-6",
                    max_tokens=600,
                    messages=[{
                        "role": "user",
                        "content": f"{a}\n\nHere is the data:\n{d}\n\nGive your analyst view now."
                    }]
                )
            )
            debate_results[agent_name] = response.content[0].text
        except Exception as e:
            debate_results[agent_name] = f"[Agent failed: {e}]"

    # Synthesis
    all_views = "\n\n".join(
        f"=== {agent} ===\n{view}" for agent, view in debate_results.items()
    )

    try:
        loop = asyncio.get_event_loop()
        synth_response = await loop.run_in_executor(
            None,
            lambda: client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=700,
                messages=[{
                    "role": "user",
                    "content": f"{SYNTHESIS_PROMPT}\n\nHere are the five perspectives on {ticker}:\n\n{all_views}"
                }]
            )
        )
        synthesis = synth_response.content[0].text
    except Exception as e:
        synthesis = f"[Synthesis failed: {e}]"

    return {
        "ticker": ticker,
        "agents": debate_results,
        "synthesis": synthesis,
        "data_used": {
            "signal_score": sig.get("score"),
            "direction": sig.get("direction"),
            "price": price.get("current"),
        },
    }
