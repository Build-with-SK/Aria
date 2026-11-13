/**
 * components/ConnectionBanner.jsx
 * Tell the user once, clearly, when the backend is unreachable.
 *
 * Without this, a stopped API produces a different failure on every page —
 * a spinner that never resolves here, a red error string there, an empty
 * table somewhere else — and the user has to guess whether ARIA is broken,
 * their internet is down, or the data is genuinely missing.
 *
 * One banner, the actual cause, and the command to fix it. The command used to
 * be the Windows uvicorn line, printed in yellow at the top of every page —
 * wrong on the Mac mini, unusable from a phone, and the first thing anyone saw.
 * It now sits behind a disclosure, naming the restart for each host.
 */
import React, { useEffect, useState } from 'react'
import axios from 'axios'

const RESTART = [
  ['Mac mini', 'scripts/serverctl restart'],
  ['Windows', 'START_ARIA.bat'],
]

export default function ConnectionBanner() {
  const [down, setDown] = useState(false)
  const [checking, setChecking] = useState(false)

  useEffect(() => {
    let alive = true
    const ping = () => {
      axios.get('/health', { timeout: 6000 })
        .then(() => { if (alive) setDown(false) })
        .catch(() => { if (alive) setDown(true) })
    }
    ping()
    const id = setInterval(ping, 15000)
    return () => { alive = false; clearInterval(id) }
  }, [])

  const retry = () => {
    setChecking(true)
    axios.get('/health', { timeout: 6000 })
      .then(() => setDown(false))
      .catch(() => setDown(true))
      .finally(() => setChecking(false))
  }

  if (!down) return null

  return (
    <div role="alert" className="sheet" style={{
      position: 'fixed', top: 'calc(10px + env(safe-area-inset-top))', left: 12, right: 12, zIndex: 400,
      maxWidth: 860, margin: '0 auto', padding: '12px 16px', borderRadius: 18,
      display: 'flex', alignItems: 'center', gap: 14, flexWrap: 'wrap',
    }}>
      <span className="aria-pill bad"><i aria-hidden="true" />Server unreachable</span>
      <span style={{ fontSize: 13.5, color: 'var(--ink-2)', flex: 1, minWidth: 240, lineHeight: 1.5 }}>
        ARIA is not responding. Pages show the last data they loaded, and your settings
        and records are unaffected. Checking again every 15 seconds.
      </span>
      <details style={{ fontSize: 13, color: 'var(--ink-3)' }}>
        <summary style={{ cursor: 'pointer' }}>How to restart</summary>
        {RESTART.map(([host, cmd]) => (
          <div key={host} style={{ marginTop: 6 }}>
            {host}:{' '}
            <code style={{ color: 'var(--ink)', background: 'var(--inset)', padding: '2px 7px', borderRadius: 6, fontSize: 12.5 }}>
              {cmd}
            </code>
          </div>
        ))}
      </details>
      <button onClick={retry} disabled={checking} className="aria-btn">{checking ? 'Checking…' : 'Retry'}</button>
    </div>
  )
}
