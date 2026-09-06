/**
 * useApi.js
 * Central data fetching hooks for the Trading Intelligence System API.
 * All components import from here — never fetch directly.
 */

import { useState, useEffect, useCallback } from 'react'

const BASE = '' // Vite proxy handles /api → localhost:8000

async function fetchJson(url) {
  const res = await fetch(BASE + url)
  if (!res.ok) throw new Error(`${res.status} ${res.statusText} — ${url}`)
  return res.json()
}

function useApiData(url, interval = null) {
  const [data,    setData]    = useState(null)
  const [loading, setLoading] = useState(true)
  const [error,   setError]   = useState(null)

  const load = useCallback(async () => {
    try {
      setLoading(true)
      const d = await fetchJson(url)
      setData(d)
      setError(null)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [url])

  useEffect(() => {
    load()
    if (interval) {
      const id = setInterval(load, interval)
      return () => clearInterval(id)
    }
  }, [load, interval])

  return { data, loading, error, reload: load }
}

// ── Exported hooks ────────────────────────────────────────────────────────

export const useSummary    = () => useApiData('/api/summary',   60_000) // refresh every 60s
export const useSignals    = () => useApiData('/api/signals',   120_000)
export const useFutures    = () => useApiData('/api/futures',   120_000)
export const useOptions    = () => useApiData('/api/options',   300_000)
export const usePortfolio  = () => useApiData('/api/portfolio', 300_000)
// What he ACTUALLY owns, from the broker. `usePortfolio` above returns
// data/portfolio_analysis.json — a fixed model universe last computed in May,
// which is why the page showed twenty-odd names he had never bought.
export const useHoldings   = () => useApiData('/api/portfolio/holdings', 60_000)
export const useRelated    = () => useApiData('/api/portfolio/related?per_holding=4', 300_000)
export const useAlerts     = () => useApiData('/api/alerts',    60_000)
export const useHealth     = () => useApiData('/health',        30_000)


// ── Quant Lab (automated researcher) ─────────────────────────────────────


// ── NEXUS / Universe (on-demand, not polled) ─────────────────────────────
export async function searchUniverse(q, n = 8) {
  if (!q) return []
  return fetchJson(`/api/universe/search?q=${encodeURIComponent(q)}&n=${n}`)
}

export async function fetchNexusResearch(symbol) {
  return fetchJson(`/api/nexus/research/${encodeURIComponent(symbol)}`)
}

// ── THE SPINE (src/core) ─────────────────────────────────────────────────
// The world model, the activity stream, the worker registry and the ledger.
// These four are what make the interface a window onto real system state
// rather than a set of independently-fetched panels: every one of them is
// assembled from a subsystem that already did the work.

export const useWorld        = () => useApiData('/api/aria/world',          60_000)
export const useWorldChanges = (h = 24) => useApiData(`/api/aria/world/changes?hours=${h}`, 120_000)
export const useWorkers      = () => useApiData('/api/aria/workers',        30_000)
// The honest status dot. `/health` returns ok whenever the web server can
// answer, which told you nothing about whether the daemons behind it were
// still running — the exact failure this system already had for a week.
export const useSystemHealth = () => useApiData('/api/system/health',       30_000)
export const useLedger       = (n = 200) => useApiData(`/api/ledger/predictions?limit=${n}`, 120_000)
export const useLedgerDecisions = (n = 200) => useApiData(`/api/ledger/decisions?limit=${n}`, 120_000)
export const useCalibration  = () => useApiData('/api/ledger/calibration',  300_000)

// The rigorous calibration diagnostic (Phase 18). Distinct from
// useCalibration() above, which counts ledger ROWS: this one counts distinct
// EVENTS, keeps traded and non-traded populations apart, and puts a Wilson
// interval on every rate. The first pass over the same data reported
// "confidence is inverted, skill -0.222"; on independent events the honest
// answer is that the sample cannot yet tell.
export const useInvestigation = () => useApiData('/api/ledger/investigate', 300_000)

// The consultation bridge to SENTINEL — a SEPARATE general intelligence in its
// own process, not a part of ARIA. This reports whether a second opinion is
// currently obtainable; it never reports SENTINEL's agreement, because an
// unreachable consultant has not agreed to anything.
export const useConsultStatus = () => useApiData('/api/consult/status', 60_000)

// ── THE CANONICAL SURFACE ────────────────────────────────────────────────
// One route, one purpose. These replace the habit of each panel fetching its
// own slice of ARIA and then looking, on screen, like its own intelligence.

/** THE brain. Status, regime, memory, cognition and world in one read. */
export const useBrain   = () => useApiData('/api/brain',          15_000)
/** The live cognition stream — activity summaries, never chain-of-thought. */
export const useBrainActivity = (n = 60) => useApiData(`/api/brain/activity?limit=${n}`, 10_000)
/** The market regime WITH its evidence and its input coverage. */
export const useRegime  = () => useApiData('/api/regime',        120_000)

/** Memory search from inside the Brain. On-demand — not polled. */
export async function searchMemory(q, limit = 20) {
  return fetchJson(`/api/brain/memory?q=${encodeURIComponent(q || '')}&limit=${limit}`)
}

// ── the daily report — one per day, addressed BY DATE ────────────────────
/** `date` omitted means today. A missing day answers NOT_GENERATED; it is
    never silently replaced by the most recent report. */
export const useDailyReport = (date) =>
  useApiData(date ? `/api/daily-report?date=${encodeURIComponent(date)}`
                  : '/api/daily-report', 300_000)
export const useReportHistory = () => useApiData('/api/daily-report/history', 300_000)

export async function generateDailyReport(date, supersede = false) {
  const qs = new URLSearchParams()
  if (date) qs.set('date', date)
  if (supersede) qs.set('supersede', 'true')
  const res = await fetch(`/api/daily-report/generate?${qs}`, { method: 'POST' })
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || res.statusText)
  return res.json()
}

// ── live news — a cursor, not a poll-and-diff ────────────────────────────
/** Continuous world feed. Distinct from the daily report on purpose: this is
    event-driven and returns NOTHING when nothing happened, which the UI must
    render as silence rather than as a gap to fill. */
export async function fetchLiveNews(since, hours = 6, limit = 40) {
  const qs = new URLSearchParams({ hours: String(hours), limit: String(limit) })
  if (since) qs.set('since', since)
  return fetchJson(`/api/news/live?${qs}`)
}
export const useNewsSources = () => useApiData('/api/news/sources?hours=24', 300_000)

/* Removed 2026-08-29: fourteen exported hooks that nothing imported —
   useSignal, useMacro, useBacktest, useSentiment, useReport, useHistory,
   useStats, useML, useActivity, useQuantStatus, useQuantStrategies,
   useQuantPapers, triggerRun, quantRunNow.

   They were the data layer for pages the workspace consolidation absorbed. The
   endpoints behind them all still exist and still answer; only these unused
   wrappers are gone, and git has them if a page needs one again.

   `useReport` mattered most: it pointed at /api/report, the single-file daily
   report that every run used to overwrite. Anything wiring it up today would
   have rendered a report from May under today's heading — which is the exact
   defect the dated store replaced. Leaving a convenient hook to a superseded
   endpoint is how that comes back. */
