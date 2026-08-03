/**
 * components/Term.jsx
 * Plain English behind every piece of jargon.
 *
 * Making the text bigger made ARIA legible; it did not make it understandable.
 * "ECE 6.3%", "net +16.6", "2×ATR" and "Brier 0.252" are precise and correct
 * and mean nothing to most people — including plenty of people who trade.
 *
 * The rule here: never replace the technical term (it is the correct word, and
 * users who know it should keep seeing it) — attach the explanation to it.
 * Hover or focus on any dotted term gives a sentence in ordinary language.
 *
 * Definitions are written for someone who has never read a quant paper, and
 * they say what the number MEANS for a decision, not just what it is.
 */
import React from 'react'

export const GLOSSARY = {
  // ── confidence and calibration ──
  'brier score': 'How wrong the confidence numbers are, on average. 0 is perfect, 0.25 is what you score by always saying "50/50". Lower is better.',
  skill: 'Whether the confidence numbers beat simply saying "50/50" every time. Above zero means they carry real information; below zero means they are worse than useless.',
  ece: 'Expected Calibration Error — the average gap between what ARIA claimed and what actually happened. If it says 60% and it happens 60% of the time, this is zero.',
  calibration: 'Whether "60% confident" actually comes true about 60% of the time. A system can pick the right direction often and still be badly calibrated.',
  'confidence interval': 'The range the true answer probably sits in. A wide range means the engine genuinely does not know — that is honest, not a defect.',
  'calibration gap': 'How much more confident ARIA sounded than it turned out to be. Positive means overconfident.',
  'hit rate': 'The share of resolved calls that pointed the right way.',
  'p-value': 'The chance a result this good could happen by luck alone. Under 0.05 is the usual bar for "probably not luck".',

  // ── the ensemble ──
  'net score': 'One number from −100 (strongly negative) to +100 (strongly positive), combining every engine that reported.',
  dispersion: 'How much the engines disagree with each other. High disagreement automatically lowers confidence.',
  abstention: 'An engine reporting that it does not have enough data to have a view. It counts for neither side.',
  ensemble: 'The whole panel of independent engines, combined. No single model decides anything on its own.',
  module: 'One independent analysis engine — momentum, value, volatility and so on. There are 41.',

  // ── risk and levels ──
  atr: 'Average True Range — how far this stock typically moves in a day. Stops are set in multiples of it, so a calm stock gets a tight stop and a volatile one gets room.',
  'stop loss': 'The price at which the idea is wrong and the position should be closed, set before entering.',
  'take profit': 'The price at which the idea has played out and profit is taken.',
  target: 'The price the idea is aiming at if it works.',
  'reward : risk': 'How much you stand to make versus what you would lose if the stop is hit. 2:1 means twice as much up as down.',
  'r:r': 'Reward against risk. 2:1 means the target is twice as far away as the stop.',
  invalidation: 'The specific thing that would prove this idea wrong — decided in advance, so the exit is not an in-the-moment judgment.',
  drawdown: 'The fall from a previous peak — how much of your money was underwater at the worst point.',
  'position size': 'How much of your total money this idea would use, scaled to how volatile the stock is and how confident ARIA is.',
  'risk gate': 'The final safety check. It can shrink or refuse any idea, regardless of how good the analysis looks.',
  veto: 'The risk layer refused this idea outright — nothing above it can override that.',
  beta: 'How much this moves when the wider market moves. Above 1 means it swings harder than the market.',
  volatility: 'How much the price jumps around. Higher volatility means a wider stop and a smaller position.',
  liquidity: 'How easily you could get out at a fair price. Thin liquidity means an exit costs you.',
  sharpe: 'Return earned per unit of risk taken. Higher is better; above 1 is respectable.',

  // ── technical ──
  rsi: 'A 0–100 gauge of whether a stock has run hot or cold recently. Above 70 is often called overbought, below 30 oversold.',
  macd: 'A momentum indicator comparing a fast and a slow average — used to spot shifts in direction.',
  'moving average': 'The average price over a window (say 50 days), used to see the trend through the noise.',
  'base rate': 'How often this exact situation was followed by a rise, historically. Evidence rather than opinion.',
  momentum: 'The tendency of a stock that has been rising to keep rising — and the reverse.',
  'mean reversion': 'The tendency of a price stretched far from its average to snap back toward it.',
  breadth: 'How many parts of the market are participating. A rally carried by a handful of stocks is fragile.',

  // ── fundamentals ──
  'p/e': 'Price divided by yearly earnings — roughly how many years of profit you are paying for.',
  'p/b': 'Price against the accounting value of what the company owns.',
  roe: 'Return on equity — the profit generated on shareholders money. Higher usually means a better business.',
  peg: 'The P/E compared with the growth rate. Around 1 or below is often considered reasonable value.',
  'free cash flow': 'Cash left after the company pays for running and investing in itself. Real money, harder to massage than profit.',
  'dividend yield': 'The yearly dividend as a percentage of the share price.',
}

/**
 * `<Term>ECE</Term>` — dotted underline, explanation on hover and on focus.
 * Falls back to plain text when a term has no definition, so it is always safe
 * to wrap something.
 */
export default function Term({ children, k, style }) {
  const key = String(k || children || '').toLowerCase().trim()
  const def = GLOSSARY[key]
  if (!def) return <>{children}</>
  return (
    <abbr
      className="aria-term"
      title={def}
      tabIndex={0}
      style={{ textDecoration: 'none', ...style }}>
      {children}
    </abbr>
  )
}

/** A labelled figure whose label carries its own explanation. */
export function TermLabel({ term, label, style }) {
  return <Term k={term} style={style}>{label ?? term}</Term>
}
