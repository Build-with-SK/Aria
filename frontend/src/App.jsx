import React, { lazy, Suspense, useEffect, useState } from 'react'
import { Link, Navigate, Routes, Route, useLocation } from 'react-router-dom'
import Sidebar   from './components/Sidebar'
import ErrorBoundary from './components/ErrorBoundary'
import ConnectionBanner from './components/ConnectionBanner'
import CommandPalette from './components/CommandPalette'
import Login from './pages/Login'
import { useAuth } from './auth/AuthContext'
import OwnerOnly from './auth/OwnerOnly'
/* ── the eight workspaces ── */
import AriaBrain from './pages/AriaBrain'
import { useSummary } from './hooks/useApi'

/* Route-level code splitting. The whole app used to ship as one 864 KB bundle,
   so a phone on mobile data downloaded the desk, the quant lab and every chart
   library before it could render the landing page. Chat and the command deck
   stay eager (they are where people land); the rest arrive when opened. */
const Research = lazy(() => import('./pages/Research'))
const Market = lazy(() => import('./pages/Market'))
const PortfolioWorkspace = lazy(() => import('./pages/PortfolioWorkspace'))
const Strategies = lazy(() => import('./pages/Strategies'))
const TrackRecordLedger = lazy(() => import('./pages/TrackRecordLedger'))
const System = lazy(() => import('./pages/System'))
/* The daily intelligence product. Its own destination, because it is its own
   product: one report per day, kept forever, addressed by date. It used to be
   a card at the bottom of another page rendering a single file that every run
   overwrote — so it showed one report, three months stale, under a heading
   that implied today. */
const DailyReport = lazy(() => import('./pages/DailyReport'))
/* Kept as a destination only for people without owner access: the full Brain
   workspace reads the portfolio and the record, so signed-in non-owners get
   the brain's public pulse instead. It is the SAME brain — a narrower view of
   it, never a second one. */
const BrainPulse = lazy(() => import('./pages/BrainPublic'))

/* ── cinematic boot splash — plays once per browser session ── */
function Boot() {
  const [gone, setGone] = useState(() => sessionStorage.getItem('aria-booted') === '1')
  useEffect(() => {
    if (gone) return
    const t = setTimeout(() => { sessionStorage.setItem('aria-booted', '1'); setGone(true) }, 2750)
    return () => clearTimeout(t)
  }, [gone])
  if (gone) return null
  return (
    <div id="aria-boot">
      <div className="word">ARIA</div>
      <div className="bar"><i /></div>
      <div className="sub">OPEN&nbsp;FINANCE&nbsp;INTELLIGENCE</div>
    </div>
  )
}

/* ── live signal tape across the top ── */
function TickerTape() {
  const { data } = useSummary()
  if (!data?.top_bullish?.length) return null

  const items = [
    ...(data.top_bullish || []).map(s => ({ ticker: s.ticker, score: s.score, dir: 1 })),
    ...(data.top_bearish || []).map(s => ({ ticker: s.ticker, score: s.score, dir: -1 })),
  ]
  const tape = [...items, ...items] // double for seamless loop

  return (
    <div className="aria-tape" style={{
      background: 'linear-gradient(180deg, rgba(255,36,71,0.04), transparent), #060308',
      borderBottom: '1px solid var(--border)',
    }}>
      <div className="ticker-inner" style={{ gap: 0 }}>
        {/* Every ticker on the tape opens its research dossier. The tape keeps
            scrolling; the CSS animation pauses on hover so a moving target can
            actually be clicked. */}
        {tape.map((t, i) => (
          <Link key={i} to={`/research?symbol=${encodeURIComponent(t.ticker)}`}
            title={`Open ${t.ticker} research`}
            style={{
              fontFamily: 'var(--mono)', fontSize: 11, padding: '0 18px',
              color: t.dir > 0 ? 'var(--green)' : 'var(--red)',
              borderRight: '1px solid var(--border)',
              whiteSpace: 'nowrap', textDecoration: 'none',
              textShadow: t.dir > 0 ? '0 0 8px rgba(43,227,139,.35)' : '0 0 8px rgba(255,85,96,.35)',
            }}>
            {t.ticker} <strong>{t.score > 0 ? '+' : ''}{parseFloat(t.score).toFixed(1)}</strong>
          </Link>
        ))}
      </div>
    </div>
  )
}

/* Shown while a route chunk downloads — a calm skeleton, not a spinner. */
function PageLoading() {
  const bar = (w, h = 12, delay = 0) => (
    <div className="aria-skeleton" style={{ width: w, height: h, borderRadius: 4, marginBottom: 10,
      animationDelay: `${delay}s` }} />
  )
  return (
    <div aria-busy="true" aria-live="polite" style={{ padding: '6px 2px' }}>
      <span className="sr-only">Loading page…</span>
      {bar('34%', 16)}
      {bar('58%', 10, .06)}
      <div style={{ display: 'flex', gap: 12, margin: '18px 0' }}>
        {[0, 1, 2, 3].map(i => (
          <div key={i} className="aria-skeleton" style={{ flex: 1, height: 68, borderRadius: 6,
            animationDelay: `${i * .05}s` }} />
        ))}
      </div>
      {bar('100%', 190, .12)}
    </div>
  )
}

export default function App() {
  const loc = useLocation()
  const { ready, authenticated, role, owner } = useAuth()

  // The break-glass path. The backend already grants owner on loopback when no
  // ARIA_OWNER_TOKEN is set, and honouring that here is what stops you being
  // locked out of your own machine before any OAuth provider is configured —
  // a login screen whose every button is greyed out is a locked door. This
  // grants nothing: the server reached the same conclusion independently, and
  // is the thing actually enforcing it.
  const admitted = authenticated || role === 'owner'

  // Nothing renders until the session is known. Showing the deck first and
  // yanking it away a moment later would flash the shape of the app — and on a
  // slow link, briefly imply access that is not there.
  if (!ready) {
    return (
      <div style={{
        minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center',
        background: '#050206', fontFamily: 'var(--mono)', fontSize: 11, color: 'var(--muted)',
        letterSpacing: '0.18em',
      }}>
        <div className="aria-boot-pulse">AUTHENTICATING…</div>
      </div>
    )
  }

  // The gate. This is convenience, not enforcement — every route is refused
  // server-side too, so a client that skips this sees nothing but 401s.
  if (!admitted) {
    const next = loc.pathname + loc.search
    if (loc.pathname !== '/login') {
      return <Navigate to={`/login?next=${encodeURIComponent(next)}`} replace />
    }
    return <Login />
  }

  if (loc.pathname === '/login') return <Navigate to="/" replace />

  return (
    <div style={{ display: 'flex', minHeight: '100vh' }}>
      <Boot />
      {/* CRT scanlines + sweep + drifting grid */}
      <div className="scanline" />

      <ConnectionBanner />
      <Sidebar />
      <CommandPalette />
      <TickerTape />

      <main className="aria-main" style={{ flex: 1, minHeight: '100vh', maxWidth: '100%' }}>
        {/* key on pathname → every page mounts with the rise-in transition */}
        <div key={loc.pathname} className="page-enter">
          <ErrorBoundary resetKey={loc.pathname}>
          <Suspense fallback={<PageLoading />}>
          <Routes>
            {/* ── THE EIGHT WORKSPACES ──
                Was twelve, and before that twenty-two. The rule applied here
                is §25: the rail exposes workspaces, not implementation
                details. Signals, macro, futures and options are capabilities
                of the market engine, not four places to visit; the desk and
                the approval queue are steps in the portfolio's workflow, not
                separate products.

                And chat, the brain, the memory browser and the "live mind"
                are ONE intelligence. That is now literal rather than
                aspirational: there is a single /brain route, a single
                /api/brain state behind it, and LIVE MIND does not exist as a
                destination, a tab or a label anywhere in this app. */}

            {/* 1 — BRAIN. The hero. Owner only in full: it reads the
                portfolio and the track record. Everyone else gets the same
                brain's public pulse — a narrower view, never a second mind. */}
            <Route path="/"             element={owner ? <AriaBrain /> : <BrainPulse />} />
            <Route path="/brain"        element={owner ? <AriaBrain /> : <BrainPulse />} />
            {/* 2 */} <Route path="/research"  element={<Research />} />
            {/* 3 */} <Route path="/market"    element={<Market />} />
            {/* 4 */} <Route path="/portfolio" element={<OwnerOnly what="Portfolio"><PortfolioWorkspace /></OwnerOnly>} />
            {/* 5 */} <Route path="/strategies" element={<Strategies />} />
            {/* 6 */} <Route path="/daily-report" element={<OwnerOnly what="The daily report"><DailyReport /></OwnerOnly>} />
            {/* 7 */} <Route path="/track-record" element={<OwnerOnly what="The owner’s track record"><TrackRecordLedger /></OwnerOnly>} />
            {/* 8 */} <Route path="/system"    element={<System />} />

            {/* ── every earlier path still resolves ──
                Bookmarks, START_ARIA.bat, deep links in the vault and anything
                a user has open keep working. A merged page is not a dead link,
                and this list is the migration: nothing was removed, everything
                was moved somewhere it belongs. */}
            <Route path="/chat"      element={<Navigate to="/brain" replace />} />
            {/* The two paths that used to be "the live mind". They are the
                brain, and now they say so. */}
            <Route path="/thinking"  element={<Navigate to="/brain" replace />} />
            <Route path="/live-mind" element={<Navigate to="/brain" replace />} />
            <Route path="/memory"    element={<Navigate to="/brain" replace />} />
            <Route path="/markets"   element={<Navigate to="/market" replace />} />
            <Route path="/signals"   element={<Navigate to="/market" replace />} />
            <Route path="/macro"     element={<Navigate to="/market" replace />} />
            <Route path="/futures"   element={<Navigate to="/market" replace />} />
            <Route path="/options"   element={<Navigate to="/market" replace />} />
            <Route path="/recommendations" element={<Navigate to="/market" replace />} />
            <Route path="/alerts"    element={<Navigate to="/brain" replace />} />
            <Route path="/report"    element={<Navigate to="/daily-report" replace />} />
            <Route path="/lab"       element={<Navigate to="/strategies" replace />} />
            <Route path="/quantlab"  element={<Navigate to="/strategies" replace />} />
            <Route path="/backtest"  element={<Navigate to="/strategies" replace />} />
            <Route path="/desk"      element={<Navigate to="/portfolio" replace />} />
            <Route path="/execute"   element={<Navigate to="/portfolio" replace />} />
            <Route path="/stress"    element={<Navigate to="/portfolio" replace />} />
            <Route path="/quant"     element={<Navigate to="/portfolio" replace />} />
            {/* Explorer and Nexus were one question asked at two depths. */}
            <Route path="/explorer"  element={<Navigate to="/research" replace />} />
            <Route path="/nexus"     element={<Navigate to="/research?view=deep" replace />} />
            {/* V5 is the deep analysis engine behind research, not a place. */}
            <Route path="/v5"        element={<Navigate to="/research" replace />} />
            <Route path="/compare"   element={<Navigate to="/research" replace />} />
            {/* ML predictions are half a story without outcomes — they live
                with the track record now. */}
            <Route path="/ml"        element={<Navigate to="/track-record" replace />} />

            <Route path="*"          element={<Navigate to="/brain" replace />} />
          </Routes>
          </Suspense>
          </ErrorBoundary>
        </div>
      </main>
    </div>
  )
}
