// ─── spokenDigest — turn a markdown reply into something worth SAYING ─────────
// ARIA writes for the screen: headings, `###`, `---` rules, tables, bullets,
// emoji, code. None of that should be read aloud, and neither should every
// sentence. This module does two jobs:
//
//   toPlainSpeech(md)  → the same reply with all markup/symbols spoken naturally
//   spokenDigest(md)   → ONLY the lines that carry information (numbers, calls,
//                        risk, warnings), capped to a few sentences
//
// The full text is still shown on screen — the voice is a briefing, not a
// read-out. If the digest drops a lot, it tells you detail is available.

const EMOJI = /[\u{1F000}-\u{1FAFF}\u{2190}-\u{21FF}\u{2300}-\u{27BF}\u{2B00}-\u{2BFF}\u{FE0F}\u{2500}-\u{257F}]/gu

const KEY = /\b(buy|sell|hold|long|short|entry|exit|stop|target|risk|reward|breakout|breakdown|oversold|overbought|bullish|bearish|alert|avoid|caution|careful|watch|signal|score|conviction|gain|loss|profit|drawdown|surge|rally|drop|fall|spike|earnings|support|resistance|volume|trend|recommend|position|size|exposure|rupees|dollars|percent)\b/i

const WORD_BUDGET = 58     // roughly 20 seconds of speech
const UNIT_BUDGET = 4      // at most this many spoken sentences
const SHORT_ENOUGH = 45    // replies this short are spoken in full

/** Remove every piece of markup that exists only for the eye. */
function stripMarkup(md) {
  let t = String(md || '')
  t = t.replace(/```[\s\S]*?```/g, ' ')                  // fenced code
  t = t.replace(/`([^`]+)`/g, '$1')                      // inline code
  t = t.split('\n').filter(l => !/^\s*\|/.test(l)).join('\n')   // tables
  t = t.replace(/^\s*[-*_=~#+.·•▸●◈─━═:|]{2,}\s*$/gm, '\n')     // rules / dividers
  t = t.replace(/^\s{0,3}#{1,6}\s*/gm, '')               // heading hashes
  t = t.replace(/!\[[^\]]*\]\([^)]*\)/g, ' ')            // images
  t = t.replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')          // links
  t = t.replace(/(\*\*|__|\*|_)(?=\S)([\s\S]*?\S)\1/g, '$2')    // bold / italic
  t = t.replace(/^\s*(?:[-*+•▸●·»]|\d+[.)])\s+/gm, '')   // bullet markers
  t = t.replace(/^\s*>\s?/gm, '')                        // quotes
  t = t.replace(EMOJI, ' ')
  return t
}

/** Say symbols the way a person would say them. */
function humanise(t) {
  return t
    .replace(/([+\-−])\s?(\d[\d,]*(?:\.\d+)?)\s?%/g, (m, s, n) => `${s === '+' ? 'up' : 'down'} ${n} percent`)
    .replace(/(\d[\d,]*(?:\.\d+)?)\s?%/g, '$1 percent')
    .replace(/(\d)\s*[-–—]\s*(?=[₹$]?\d)/g, '$1 to ')      // ranges, before currency splits them up
    .replace(/\$\s?(\d[\d,]*(?:\.\d+)?)/g, '$1 dollars')
    .replace(/₹\s?(\d[\d,]*(?:\.\d+)?)/g, '$1 rupees')
    .replace(/(->|=>|→)/g, ' to ')
    .replace(/\bR\s*:\s*R\b/gi, 'risk to reward')
    .replace(/\brisk\s*:\s*reward\b/gi, 'risk to reward')
    .replace(/\bP\/L\b/gi, 'P and L')
    .replace(/\bvs\.?\b/gi, 'versus')
    .replace(/\be\.g\.\b/gi, 'for example')
    .replace(/\bi\.e\.\b/gi, 'that is')
    .replace(/[ \t]{2,}/g, ' ')
    .replace(/\n{3,}/g, '\n\n')
    .trim()
}

/** Clean, fully-spoken version of the reply (used by FULL mode). */
export function toPlainSpeech(md) {
  return humanise(stripMarkup(md))
}

/** Break cleaned text into speakable units (a line, or a sentence within it). */
function units(text) {
  const out = []
  for (const rawLine of text.split('\n')) {
    const line = rawLine.trim()
    if (!line) continue
    const parts = line.split(/(?<=[.!?])\s+(?=[A-Z0-9"'(])/)
    for (const p of parts) {
      const u = p.trim()
      if (u.length > 1) out.push(u)
    }
  }
  return out
}

function wordCount(s) { return (s.match(/\S+/g) || []).length }

// the line that actually answers the question — never let this get crowded out
const VERDICT = /^(verdict|call|bottom line|summary|action|recommendation|takeaway|answer|conclusion|my read|short answer)\b/i

function score(u, i) {
  const w = wordCount(u)
  let s = 0
  if (i === 0) s += 2.5                              // the lead usually is the answer
  if (VERDICT.test(u)) s += 4.5
  if (KEY.test(u)) s += 2.2
  if (/\d/.test(u)) s += 1.6
  if (/\b[A-Z]{2,6}\b/.test(u)) s += 0.9             // a ticker was named
  if (/\b(but|however|caution|risk|avoid|careful|warning|do not|don't)\b/i.test(u)) s += 1.2
  if (/:$/.test(u)) s -= 3                           // a section label, not a statement
  if (w < 4 && !/\d/.test(u)) s -= 2.5               // stray fragment
  if (w > 45) s -= 1                                 // rambling
  return s
}

/**
 * The briefing version: only the units worth hearing, in original order.
 * @param {string} md  the raw markdown reply
 * @returns {string} text to speak (never markup, never dividers)
 */
export function spokenDigest(md) {
  const clean = toPlainSpeech(md)
  if (!clean) return ''
  if (wordCount(clean) <= SHORT_ENOUGH) return clean.replace(/\n+/g, ' ')

  const us = units(clean)
  if (!us.length) return clean.slice(0, 400)

  const ranked = us.map((u, i) => ({ u, i, s: score(u, i) }))
    .sort((a, b) => b.s - a.s)

  const picked = []
  let words = 0
  for (const r of ranked) {
    if (picked.length >= UNIT_BUDGET || words >= WORD_BUDGET) break
    const w = wordCount(r.u)
    if (words + w > WORD_BUDGET + 12) continue
    picked.push(r); words += w
  }
  if (!picked.length) picked.push(ranked[0])
  picked.sort((a, b) => a.i - b.i)

  let text = picked.map(p => p.u.replace(/[:;,]$/, '')).join('. ').replace(/\.{2,}/g, '.')
  if (!/[.!?]$/.test(text)) text += '.'

  // if most of the reply was left out, say so — the detail is on screen
  const kept = words / Math.max(1, wordCount(clean))
  if (kept < 0.45) text += ' The rest is on screen if you want the detail.'
  return text
}
