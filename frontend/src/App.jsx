import React, { lazy, Suspense, useEffect, useState } from 'react'
import { Link, Navigate, Routes, Route, useLocation } from 'react-router-dom'
import Sidebar   from './components/Sidebar'
import ErrorBoundary from './components/ErrorBoundary'
import ConnectionBanner from './components/ConnectionBanner'
import CommandPalette from './components/CommandPalette'
import Login from './pages/Login'
import { useAuth } from './auth/AuthContext'
import OwnerOnly from './auth/OwnerOnly'
/* ── the twelve destinations ── */
import Chat        from './pages/Chat'
import CommandHub  from './pages/CommandHub'
import { useSummary } from './hooks/useApi'

/* Route-level code splitting. The whole app used to ship as one 864 KB bundle,
   so a phone on mobile data downloaded the desk, the quant lab and every chart
   library before it could render the landing page. Chat and the command deck
   stay eager (they are where people land); the rest arrive when opened. */
const V5 = lazy(() => import('./pages/V5'))
const Research = lazy(() => import('./pages/Research'))
const LabHub = lazy(() => import('./pages/LabHub'))
const BrainHub = lazy(() => import('./pages/BrainHub'))
const Markets = lazy(() => import('./pages/Markets'))
const Recommendations = lazy(() => import('./pages/Recommendations'))
const Portfolio = lazy(() => import('./pages/Portfolio'))
const Quant = lazy(() => import('./pages/Quant'))
const TrackRecord = lazy(() => import('./pages/TrackRecord'))
const DeskHub = lazy(() => import('./pages/DeskHub'))

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
  const { ready, authenticated, role } = useAuth()

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
            {/* ── the twelve destinations ── */}
            <Route path="/"             element={<CommandHub  />} />
            <Route path="/chat"         element={<Chat        />} />
            <Route path="/v5"           element={<V5          />} />
            <Route path="/research"     element={<Research    />} />
            <Route path="/lab"          element={<LabHub      />} />
            <Route path="/brain"        element={<OwnerOnly what="ARIA's memory"><BrainHub    /></OwnerOnly>} />
            <Route path="/markets"      element={<Markets     />} />
            <Route path="/recommendations" element={<Recommendations />} />
            <Route path="/portfolio"    element={<OwnerOnly what="Portfolio"><Portfolio   /></OwnerOnly>} />
            <Route path="/stress"       element={<Quant       />} />
            <Route path="/track-record" element={<OwnerOnly what="The owner’s track record"><TrackRecord /></OwnerOnly>} />
            <Route path="/desk"         element={<OwnerOnly what="The trading desk"><DeskHub     /></OwnerOnly>} />

            {/* ── every pre-restructure path still resolves ──
                Bookmarks, START_ARIA.bat and anything a user has open keep
                working; a merged page is not a dead link. */}
            <Route path="/signals"   element={<Navigate to="/markets" replace />} />
            <Route path="/macro"     element={<Navigate to="/markets" replace />} />
            <Route path="/futures"   element={<Navigate to="/markets" replace />} />
            <Route path="/options"   element={<Navigate to="/markets" replace />} />
            <Route path="/alerts"    element={<Navigate to="/" replace />} />
            <Route path="/report"    element={<Navigate to="/" replace />} />
            <Route path="/thinking"  element={<Navigate to="/brain" replace />} />
            <Route path="/quantlab"  element={<Navigate to="/lab" replace />} />
            <Route path="/backtest"  element={<Navigate to="/lab" replace />} />
            <Route path="/execute"   element={<Navigate to="/desk" replace />} />
            <Route path="/compare"   element={<Navigate to="/v5" replace />} />
            <Route path="/quant"     element={<Navigate to="/stress" replace />} />
            {/* Explorer and Nexus were one question asked at two depths. */}
            <Route path="/explorer"  element={<Navigate to="/research" replace />} />
            <Route path="/nexus"     element={<Navigate to="/research?view=deep" replace />} />
            {/* ML predictions are half a story without outcomes — they live
                with the track record now. */}
            <Route path="/ml"        element={<Navigate to="/track-record" replace />} />

            <Route path="*"          element={<Navigate to="/" replace />} />
          </Routes>
          </Suspense>
          </ErrorBoundary>
        </div>
      </main>
    </div>
  )
}
