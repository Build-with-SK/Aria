/**
 * auth/StepUp.jsx
 * "Confirm it's you" — for the few actions that move money or loosen a
 * safety control.
 *
 * WHY
 * ---
 * A session that has sat open on a phone for days proves somebody signed in
 * days ago, not that the owner is holding the phone when a trade is approved.
 * So approving a trade, releasing the kill switch, arming auto-execute and
 * changing the desk config answer 403 `step_up_required` unless the credential
 * was presented in the last few minutes (src/auth/guard.py).
 *
 * HOW
 * ---
 * One axios interceptor, installed once, catches that answer from ANY caller,
 * asks for the credential, posts it to /api/auth/confirm, and retries the
 * original request exactly once. Once, not in a loop: a server that keeps
 * refusing must surface as an error, never as a prompt that will not go away.
 *
 * The credential goes straight to /api/auth/confirm and is cleared from state
 * the moment it is sent. Nothing is stored.
 *
 * Actions that REDUCE risk (engaging the kill switch, cancelling, disarming)
 * never trigger this — the server never asks for them.
 */
import React, { useEffect, useRef, useState } from 'react'
import axios from 'axios'

const mono = { fontFamily: 'var(--mono)' }

// The open prompt, if any. Concurrent refusals share one prompt rather than
// stacking three dialogs over each other.
let pending = null
let openPrompt = null            // set by the mounted <StepUpPrompt/>

function askForConfirmation() {
  if (pending) return pending
  if (!openPrompt) return Promise.resolve(false)   // nothing mounted to ask with
  pending = new Promise(resolve => openPrompt(resolve)).finally(() => { pending = null })
  return pending
}

let installed = false
export function installStepUp() {
  if (installed) return
  installed = true
  axios.interceptors.response.use(r => r, async err => {
    const r = err?.response
    const cfg = err?.config
    const detail = r?.data?.detail
    const needed = r?.status === 403 && detail && typeof detail === 'object'
      && detail.error === 'step_up_required'
    if (!needed || !cfg || cfg.__steppedUp) return Promise.reject(err)
    const ok = await askForConfirmation()
    if (!ok) return Promise.reject(err)
    return axios({ ...cfg, __steppedUp: true })
  })
}

export default function StepUpPrompt() {
  const [resolver, setResolver] = useState(null)
  const [value, setValue] = useState('')
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const input = useRef(null)

  useEffect(() => {
    openPrompt = resolve => { setErr(''); setValue(''); setResolver(() => resolve) }
    return () => { openPrompt = null }
  }, [])

  useEffect(() => { if (resolver) input.current?.focus() }, [resolver])

  if (!resolver) return null

  const finish = ok => { const r = resolver; setResolver(null); setValue(''); r(ok) }

  const submit = async e => {
    e.preventDefault()
    if (!value) return
    setBusy(true); setErr('')
    const token = value
    setValue('')                                  // do not keep it in state
    try {
      await axios.post('/api/auth/confirm', { token })
      finish(true)
    } catch (e) {
      const s = e?.response?.status
      setErr(s === 429 ? `Too many attempts. Try again in ${e.response.data?.retry_after || 60}s.`
        : s === 401 ? 'That credential was not accepted.'
        : 'Could not confirm. Try again.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div role="dialog" aria-modal="true" aria-labelledby="stepup-title" style={{
      position: 'fixed', inset: 0, zIndex: 10000, display: 'flex',
      alignItems: 'center', justifyContent: 'center',
      background: 'rgba(5,2,6,0.86)', padding: 16,
    }}>
      <form onSubmit={submit} style={{
        width: '100%', maxWidth: 380, background: '#0b080c',
        border: '1px solid var(--orange)', borderRadius: 5, padding: '20px 18px',
        boxShadow: '0 0 24px rgba(255,36,71,.18)',
      }}>
        <div id="stepup-title" style={{ ...mono, fontSize: 12, fontWeight: 700,
          color: 'var(--orange)', letterSpacing: '0.12em' }}>
          CONFIRM IT'S YOU
        </div>
        <p style={{ ...mono, fontSize: 11, color: 'var(--muted)', lineHeight: 1.6, margin: '10px 0 14px' }}>
          This moves money or loosens a safety control, so it needs your owner
          credential again. You won't be asked again for a few minutes.
        </p>
        <input ref={input} type="password" autoComplete="current-password"
          value={value} onChange={e => setValue(e.target.value)} disabled={busy}
          aria-label="Owner credential"
          style={{ ...mono, width: '100%', boxSizing: 'border-box', padding: '10px 11px',
            fontSize: 12, color: '#e8e8e8', background: '#121016',
            border: '1px solid #2a2630', borderRadius: 4 }} />
        {err && <div role="alert" style={{ ...mono, fontSize: 10.5, color: 'var(--red)', marginTop: 8 }}>{err}</div>}
        <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
          <button type="submit" disabled={busy || !value} style={{
            ...mono, flex: 1, padding: '10px 12px', border: 'none', borderRadius: 4,
            fontSize: 11.5, fontWeight: 700, letterSpacing: '0.06em',
            cursor: busy ? 'default' : 'pointer',
            background: busy ? '#1a1620' : 'var(--orange)', color: busy ? 'var(--muted)' : '#140407',
          }}>{busy ? 'CHECKING…' : 'CONFIRM'}</button>
          <button type="button" onClick={() => finish(false)} disabled={busy} style={{
            ...mono, padding: '10px 12px', borderRadius: 4, fontSize: 11.5,
            background: 'transparent', color: 'var(--muted)', border: '1px solid #2a2630',
            cursor: 'pointer',
          }}>CANCEL</button>
        </div>
      </form>
    </div>
  )
}
