/**
 * components/ConnectionBanner.jsx
 * Tell the user once, clearly, when the backend is unreachable.
 *
 * Without this, a stopped API produces a different failure on every page —
 * a spinner that never resolves here, a red error string there, an empty
 * table somewhere else — and the user has to guess whether ARIA is broken,
 * their internet is down, or the data is genuinely missing.
 *
 * One banner, the actual cause, and the exact command to fix it.
 */
import React, { useEffect, useState } from 'react'
import axios from 'axios'

const MONO = 'var(--mono)'
const CMD = 'venv\\Scripts\\python.exe -m uvicorn backend.main:app --port 8000'

export default function ConnectionBanner() {
  const [down, setDown] = useState(false)
  const [checking, setChecking] = useState(false)
  const [copied, setCopied] = useState(false)

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
        ⚠ BACKEND UNREACHABLE
      </span>
      <span style={{ fontFamily: MONO, fontSize: 11, color: 'var(--text)', flex: 1, minWidth: 240 }}>
        ARIA&apos;s API is not responding, so every page will show stale or missing data. Your
        settings and data are safe — only the connection is down.
      </span>
      <code style={{
        fontFamily: MONO, fontSize: 10, color: 'var(--yellow)',
        background: 'rgba(0,0,0,.4)', padding: '4px 8px', borderRadius: 3,
      }}>{CMD}</code>
      <button onClick={() => {
        navigator.clipboard?.writeText(CMD).then(() => {
          setCopied(true); setTimeout(() => setCopied(false), 2000)
        }).catch(() => {})
      }} style={{
        fontFamily: MONO, fontSize: 10, padding: '5px 10px', cursor: 'pointer', minHeight: 26,
        background: 'none', border: '1px solid var(--border-2)', color: 'var(--muted)', borderRadius: 3,
      }}>{copied ? 'COPIED ✓' : 'COPY'}</button>
      <button onClick={retry} disabled={checking} style={{
        fontFamily: MONO, fontSize: 10, fontWeight: 700, padding: '5px 12px', cursor: 'pointer', minHeight: 26,
        background: 'none', border: '1px solid var(--red)', color: 'var(--red)', borderRadius: 3,
      }}>{checking ? 'CHECKING…' : 'RETRY'}</button>
    </div>
  )
}
