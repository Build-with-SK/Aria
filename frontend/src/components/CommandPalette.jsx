/**
 * components/CommandPalette.jsx
 * Ctrl/⌘+K — type a destination or a ticker, press Enter.
 *
 * Eight destinations plus per-symbol research is exactly the shape that wants a
 * palette: the bar can only ever show the eight, but the thing you actually
 * want most of the time is one specific stock's dossier, and there is no rail
 * long enough for every ticker on earth.
 *
 * So the palette answers two questions with one input:
 *   "where is X"      → the twelve destinations, matched on name, path and the
 *                       old pre-restructure names people still think in
 *   "what about AAPL" → straight to /research?symbol=AAPL
 *
 * The ticker route is offered whenever the query could plausibly be a symbol,
 * and is ranked first once nothing named matches — typing "aapl" should not
 * make you scroll past a page called "Markets" to reach Apple.
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Search } from 'lucide-react'
import { groupsFor } from './Navigation'
import { useAuth } from '../auth/AuthContext'

const mono = { fontFamily: 'var(--font)' }

/* Aliases matter more than they look. The restructure renamed nine things, and
   muscle memory outlives it — someone who wants Nexus should not have to know
   it became a tab inside Research. */
const EXTRA_KEYWORDS = {
  '/chat':        ['talk', 'ask', 'aria', 'conversation'],
  '/v5':          ['analysis', 'analyse', 'analyze', 'ensemble', 'modules', 'compare', 'deep'],
  '/research':    ['explorer', 'nexus', 'dossier', 'symbol', 'ticker', 'stock', 'fundamentals'],
  '/lab':         ['quantlab', 'backtest', 'strategies', 'papers', 'sharpe'],
  '/brain':       ['thinking', 'memory', 'vault', 'reasoning', 'obsidian'],
  '/':            ['home', 'overview', 'alerts', 'report', 'dashboard', 'summary'],
  '/markets':     ['signals', 'macro', 'futures', 'options', 'movers', 'indices'],
  '/recommendations': ['recommend', 'ideas', 'picks', 'consensus', 'buy', 'sell'],
  '/portfolio':   ['holdings', 'positions', 'allocation', 'weights'],
  '/stress':      ['quant', 'scenario', 'derivatives', 'payoff', 'greeks', 'risk'],
  '/track-record': ['ml', 'calibration', 'brier', 'accuracy', 'hit rate', 'honesty'],
  '/desk':        ['execute', 'execution', 'orders', 'debate', 'agents', 'trading'],
}

const buildDestinations = isOwner => groupsFor(isOwner).flatMap(g =>
  g.items.map(it => ({
    kind: 'page',
    path: it.path,
    icon: it.icon,
    label: it.label,
    group: g.label,
    haystack: [it.label, it.path, g.label, ...(EXTRA_KEYWORDS[it.path] || [])]
      .join(' ').toLowerCase(),
  }))
)

/* Looks like it could be a ticker: 1–6 letters, optionally an exchange suffix
   (.NS, .L, .TO). Deliberately permissive — the research page resolves the
   symbol properly and says so if it does not exist, which is a better failure
   than the palette silently refusing to offer it. */
const TICKER_RE = /^[A-Za-z]{1,6}(\.[A-Za-z]{1,3})?$/

function score(item, q) {
  const label = item.label.toLowerCase()
  if (label === q) return 0
  if (label.startsWith(q)) return 1
  if (item.haystack.includes(` ${q}`) || item.haystack.startsWith(q)) return 2
  if (item.haystack.includes(q)) return 3
  return -1
}

export default function CommandPalette() {
  const { owner } = useAuth()
  const [open, setOpen] = useState(false)
  const [q, setQ] = useState('')
  const [sel, setSel] = useState(0)
  const inputRef = useRef(null)
  const listRef = useRef(null)
  const restoreRef = useRef(null)
  const navigate = useNavigate()

  const close = useCallback(() => {
    setOpen(false)
    setQ('')
    setSel(0)
    // Send focus back where it came from, or the palette silently strands
    // keyboard users at the top of the document every time they escape.
    const el = restoreRef.current
    if (el && document.contains(el)) el.focus()
  }, [])

  useEffect(() => {
    const onKey = e => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        // Dismissing with the same chord has to go through close(), or the old
        // query survives into the next open and you are searching yesterday's
        // text without realising it.
        if (open) { close(); return }
        restoreRef.current = document.activeElement
        setOpen(true)
      } else if (e.key === 'Escape' && open) {
        e.preventDefault()
        close()
      }
    }
    // The rail's search button opens the same palette without duplicating any
    // of this state.
    const onAsk = () => {
      if (open) return
      restoreRef.current = document.activeElement
      setOpen(true)
    }
    window.addEventListener('keydown', onKey)
    window.addEventListener('aria:palette', onAsk)
    return () => {
      window.removeEventListener('keydown', onKey)
      window.removeEventListener('aria:palette', onAsk)
    }
  }, [open, close])

  useEffect(() => { if (open) inputRef.current?.focus() }, [open])

  // Same list the rail shows. Leaving owner-only pages searchable here would
  // undo the hiding — typing "portfolio" would still name a door that answers
  // 403, which is the thing we set out to stop.
  const DESTINATIONS = useMemo(() => buildDestinations(owner), [owner])

  const results = useMemo(() => {
    const query = q.trim().toLowerCase()
    const pages = query
      ? DESTINATIONS.map(d => ({ d, s: score(d, query) }))
          .filter(x => x.s >= 0)
          .sort((a, b) => a.s - b.s)
          .map(x => x.d)
      : DESTINATIONS

    const out = [...pages]
    const raw = q.trim()
    if (raw && TICKER_RE.test(raw)) {
      const sym = raw.toUpperCase()
      const ticker = {
        kind: 'ticker',
        path: `/research?symbol=${encodeURIComponent(sym)}`,
        icon: Search,
        label: `Research ${sym}`,
        group: 'Symbol',
      }
      // Lead with the ticker only when nothing in the app is named after the
      // query. Most aliases — nexus, desk, macro, signals — are short enough to
      // look like symbols, and burying the page they name under a speculative
      // symbol lookup would make the alias table useless. Otherwise it sits at
      // second place, always one arrow key away.
      out.splice(pages.length === 0 ? 0 : 1, 0, ticker)
    }
    return out.slice(0, 20)
  }, [q, DESTINATIONS])

  useEffect(() => { setSel(0) }, [q])

  // Keep the highlighted row visible when arrowing past the fold.
  useEffect(() => {
    const el = listRef.current?.querySelector(`[data-idx="${sel}"]`)
    el?.scrollIntoView({ block: 'nearest' })
  }, [sel, results])

  const run = item => {
    if (!item) return
    close()
    navigate(item.path)
  }

  const onInputKey = e => {
    if (e.key === 'ArrowDown') { e.preventDefault(); setSel(i => Math.min(i + 1, results.length - 1)) }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setSel(i => Math.max(i - 1, 0)) }
    else if (e.key === 'Enter') { e.preventDefault(); run(results[sel]) }
  }

  if (!open) return null

  return (
    <div
      onMouseDown={e => { if (e.target === e.currentTarget) close() }}
      style={{
        position: 'fixed', inset: 0, zIndex: 9000,
        background: 'var(--scrim)',
        display: 'flex', alignItems: 'flex-start', justifyContent: 'center',
        padding: '10vh 16px 16px',
      }}>
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Command palette"
        className="sheet"
        style={{ ...mono, width: '100%', maxWidth: 580, overflow: 'hidden', animation: 'sheetUp .2s cubic-bezier(.22,.7,.3,1) both' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '14px 16px', borderBottom: '1px solid var(--line)' }}>
          <Search size={18} strokeWidth={2} aria-hidden="true" style={{ color: 'var(--ink-3)', flex: 'none' }} />
          <input
            ref={inputRef}
            value={q}
            onChange={e => setQ(e.target.value)}
            onKeyDown={onInputKey}
            placeholder="Go to a page, or type a ticker…"
            aria-label="Search destinations or enter a ticker"
            aria-controls="aria-palette-list"
            style={{
              ...mono, flex: 1, background: 'transparent', border: 'none', outline: 'none', boxShadow: 'none',
              color: 'var(--ink)', fontSize: 17, padding: '2px 0',
            }} />
          <kbd style={{ ...mono, fontSize: 12, color: 'var(--ink-3)', background: 'var(--inset)', borderRadius: 6, padding: '3px 7px' }}>Esc</kbd>
        </div>

        <div id="aria-palette-list" ref={listRef} role="listbox" aria-label="Results"
          style={{ maxHeight: '46vh', overflowY: 'auto', padding: 6 }}>
          {results.length === 0 && (
            <div style={{ ...mono, fontSize: 14, color: 'var(--ink-3)', padding: '14px 12px' }}>
              Nothing matches “{q}”.
            </div>
          )}
          {results.map((r, i) => (
            <div
              key={`${r.kind}:${r.path}`}
              data-idx={i}
              role="option"
              aria-selected={i === sel}
              onMouseEnter={() => setSel(i)}
              onMouseDown={e => { e.preventDefault(); run(r) }}
              style={{
                display: 'flex', alignItems: 'center', gap: 12,
                padding: '10px 12px', cursor: 'pointer', borderRadius: 12,
                background: i === sel ? 'var(--accent-bg)' : 'transparent',
                color: i === sel ? 'var(--accent)' : 'var(--ink-2)',
              }}>
              <r.icon size={18} strokeWidth={2} aria-hidden="true" style={{ flex: 'none' }} />
              <span style={{ ...mono, fontSize: 15, fontWeight: 550, color: 'var(--ink)', flex: 1 }}>{r.label}</span>
              <span style={{ ...mono, fontSize: 12.5, color: 'var(--ink-3)' }}>{r.group}</span>
            </div>
          ))}
        </div>

        <div style={{ ...mono, fontSize: 12.5, color: 'var(--ink-3)', padding: '10px 16px', borderTop: '1px solid var(--line)', display: 'flex', gap: 16, flexWrap: 'wrap' }}>
          <span>↑↓ move</span><span>⏎ open</span><span>a ticker goes straight to research</span>
        </div>
      </div>
    </div>
  )
}
