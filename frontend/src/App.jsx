import React, { useEffect, useState } from 'react'
import { Routes, Route, useLocation } from 'react-router-dom'
import Sidebar   from './components/Sidebar'
import Compare   from './pages/Compare'
import Chat      from './pages/Chat'
import Nexus     from './pages/Nexus'
import QuantLab  from './pages/QuantLab'
import Brain     from './pages/Brain'
import Thinking  from './pages/Thinking'
import Explorer  from './pages/Explorer'
import Quant     from './pages/Quant'
import Execution from './pages/Execution'
import Overview  from './pages/Overview'
import Signals   from './pages/Signals'
import Futures   from './pages/Futures'
import Options   from './pages/Options'
import ML        from './pages/ML'
import Backtest  from './pages/Backtest'
import Portfolio from './pages/Portfolio'
import Macro     from './pages/Macro'
import Alerts    from './pages/Alerts'
import Report    from './pages/Report'
import { useSummary } from './hooks/useApi'

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
    <div style={{
      position: 'fixed', top: 0, left: 200, right: 0, height: 30,
      background: 'linear-gradient(180deg, rgba(255,36,71,0.04), transparent), #060308',
      borderBottom: '1px solid var(--border)',
      overflow: 'hidden', zIndex: 99, display: 'flex', alignItems: 'center',
    }}>
      <div className="ticker-inner" style={{ gap: 0 }}>
        {tape.map((t, i) => (
          <span key={i} style={{
            fontFamily: 'var(--mono)', fontSize: 11, padding: '0 18px',
            color: t.dir > 0 ? 'var(--green)' : 'var(--red)',
            borderRight: '1px solid var(--border)',
            whiteSpace: 'nowrap',
            textShadow: t.dir > 0 ? '0 0 8px rgba(43,227,139,.35)' : '0 0 8px rgba(255,85,96,.35)',
          }}>
            {t.ticker} <strong>{t.score > 0 ? '+' : ''}{parseFloat(t.score).toFixed(1)}</strong>
          </span>
        ))}
      </div>
    </div>
  )
}

export default function App() {
  const loc = useLocation()
  return (
    <div style={{ display: 'flex', minHeight: '100vh' }}>
      <Boot />
      {/* CRT scanlines + sweep + drifting grid */}
      <div className="scanline" />

      <Sidebar />
      <TickerTape />

      <main style={{ marginLeft: 200, marginTop: 30, flex: 1, padding: '20px 24px', minHeight: '100vh', maxWidth: '100%' }}>
        {/* key on pathname → every page mounts with the rise-in transition */}
        <div key={loc.pathname} className="page-enter">
          <Routes>
            <Route path="/chat"      element={<Chat      />} />
            <Route path="/nexus"     element={<Nexus     />} />
            <Route path="/quantlab"  element={<QuantLab  />} />
            <Route path="/brain"     element={<Brain     />} />
            <Route path="/thinking"  element={<Thinking  />} />
            <Route path="/explorer"  element={<Explorer  />} />
            <Route path="/quant"     element={<Quant     />} />
            <Route path="/execute"   element={<Execution />} />
            <Route path="/compare"   element={<Compare   />} />
            <Route path="/"          element={<Overview  />} />
            <Route path="/signals"   element={<Signals   />} />
            <Route path="/futures"   element={<Futures   />} />
            <Route path="/options"   element={<Options   />} />
            <Route path="/ml"        element={<ML        />} />
            <Route path="/backtest"  element={<Backtest  />} />
            <Route path="/portfolio" element={<Portfolio />} />
            <Route path="/macro"     element={<Macro     />} />
            <Route path="/alerts"    element={<Alerts    />} />
            <Route path="/report"    element={<Report    />} />
          </Routes>
        </div>
      </main>
    </div>
  )
}
