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

const MONO = 'var(--mono)'
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
    <div role="alert" style={{
      position: 'fixed', top: 0, left: 0, right: 0, zIndex: 400,
      background: '#2a0a10', borderBottom: '1px solid var(--red)',
      padding: '9px 18px', display: 'flex', alignItems: 'center', gap: 14, flexWrap: 'wrap',
    }}>
      <span style={{ fontFamily: MONO, fontSize: 11, fontWeight: 800, color: 'var(--red)', letterSpacing: '.1em' }}>
        ⚠ SERVER UNREACHABLE
      </span>
      <span style={{ fontFamily: MONO, fontSize: 11, color: 'var(--text)', flex: 1, minWidth: 240 }}>
        ARIA is not responding. Pages show the last data they loaded, and your settings
        and records are unaffected. Checking again every 15 seconds.
      </span>
      <details style={{ fontFamily: MONO, fontSize: 10, color: 'var(--muted)' }}>
        <summary style={{ cursor: 'pointer' }}>How to restart</summary>
        {RESTART.map(([host, cmd]) => (
          <div key={host} style={{ marginTop: 4 }}>
            {host}:{' '}
            <code style={{ color: 'var(--yellow)', background: 'rgba(0,0,0,.4)', padding: '2px 6px', borderRadius: 3 }}>
              {cmd}
            </code>
          </div>
        ))}
      </details>
      <button onClick={retry} disabled={checking} style={{
        fontFamily: MONO, fontSize: 10, fontWeight: 700, padding: '5px 12px', cursor: 'pointer', minHeight: 26,
        background: 'none', border: '1px solid var(--red)', color: 'var(--red)', borderRadius: 3,
      }}>{checking ? 'CHECKING…' : 'RETRY'}</button>
    </div>
  )
}
