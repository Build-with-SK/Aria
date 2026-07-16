import React, { useEffect, useState } from 'react'
import { Routes, Route } from 'react-router-dom'
import Sidebar   from './components/Sidebar'
import Compare   from './pages/Compare'
import Chat      from './pages/Chat'
import Nexus     from './pages/Nexus'
import QuantLab  from './pages/QuantLab'
import Brain     from './pages/Brain'
import Thinking  from './pages/Thinking'
import SignalMap from './pages/SignalMap'
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
      position: 'fixed', top: 0, left: 180, right: 0, height: 28,
      background: '#050505', borderBottom: '1px solid var(--border)',
      overflow: 'hidden', zIndex: 99, display: 'flex', alignItems: 'center',
    }}>
      <div className="ticker-inner" style={{ gap: 0 }}>
        {tape.map((t, i) => (
          <span key={i} style={{
            fontFamily: 'var(--mono)', fontSize: 11, padding: '0 18px',
            color: t.dir > 0 ? 'var(--green)' : 'var(--red)',
            borderRight: '1px solid var(--border)',
            whiteSpace: 'nowrap',
          }}>
            {t.ticker} <strong>{t.score > 0 ? '+' : ''}{parseFloat(t.score).toFixed(1)}</strong>
          </span>
        ))}
      </div>
    </div>
  )
}

export default function App() {
  return (
    <div style={{ display: 'flex', minHeight: '100vh', background: 'var(--bg)' }}>
      {/* Subtle scanline overlay */}
      <div className="scanline" />

      <Sidebar />
      <TickerTape />

      <main style={{ marginLeft: 180, marginTop: 28, flex: 1, padding: '20px 24px', minHeight: '100vh', maxWidth: '100%' }}>
        <Routes>
          <Route path="/chat"      element={<Chat      />} />
          <Route path="/nexus"     element={<Nexus     />} />
          <Route path="/quantlab"  element={<QuantLab  />} />
          <Route path="/brain"     element={<Brain     />} />
          <Route path="/thinking"  element={<Thinking  />} />
          <Route path="/map"       element={<SignalMap />} />
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
      </main>
    </div>
  )
}
