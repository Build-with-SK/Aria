import React, { useState, useRef, useEffect } from 'react'
import axios from 'axios'
import { startThinking, speakReply, onVoicesReady, getAriaVoice, setAriaVoice, preview } from '../core/ariaVoice'
import { setCoreState } from '../core/coreBus'
import PressToTalk from './PressToTalk'

// ─── CoreTalk — a compact "talk to ARIA" bar that drives the Living Core ───────
// Sits under the core on the Brain page. Sends to the existing chat endpoint,
// puts the core into `thinking` on send, then `speaking` (beating word-by-word)
// as the reply is revealed. The screen shows the whole reply; the VOICE speaks
// only the briefing (BRIEF) or the clean prose (FULL) — never the markup.
// Fully decoupled — it only talks to the coreBus.

const mono = { fontFamily: 'var(--mono)' }

export default function CoreTalk() {
  const [input, setInput] = useState('')
  const [reply, setReply] = useState('')
  const [busy, setBusy] = useState(false)
  const [voice, setVoice] = useState(false)
  const [brief, setBrief] = useState(true)
  const [voices, setVoices] = useState([])
  const [voiceName, setVoiceName] = useState('')
  const [err, setErr] = useState('')
  const handleRef = useRef(null)   // active speakReply handle

  useEffect(() => () => { handleRef.current?.cancel(); setCoreState('idle') }, [])

  // browsers load voices asynchronously — keep the picker in sync
  useEffect(() => onVoicesReady(vs => {
    setVoices(vs.slice(0, 12))
    setVoiceName(getAriaVoice()?.name || '')
  }), [])

  const pickVoice = name => {
    setAriaVoice(name); setVoiceName(name)
    // Preview through Kokoro itself — the point of choosing is hearing HER.
    preview(name).catch(e => setErr(String(e.message || e)))
  }

  const send = async () => {
    const text = input.trim()
    if (!text || busy) return
    handleRef.current?.cancel()
    setBusy(true); setErr(''); setReply(''); setInput('')
    startThinking()
    try {
      const r = await axios.post('/api/chat', { messages: [{ role: 'user', content: text }] })
      const content = r.data?.content || '(no response)'
      // hand the full reply to the voice/beat driver; it reveals + beats the core
      handleRef.current = speakReply(content, shown => setReply(shown),
        { voice, brief, onError: msg => setErr(`voice: ${msg}`) })
    } catch (e) {
      setErr(e.response?.data?.detail || e.message)
      setCoreState('idle')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div style={{ marginTop: 12, marginBottom: 14 }}>
      {/* Wraps, because it has to. A label, a flex-1 input and three buttons
          that all refuse to shrink came to more than a phone is wide, and SEND
          was the one pushed off the edge — the row was unusable on mobile. */}
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
        <span style={{ ...mono, fontSize: 11, fontWeight: 800, color: 'var(--orange)', letterSpacing: 1, flexShrink: 0 }}>
          ▸ TALK
        </span>
        <input
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') send() }}
          placeholder="Speak to ARIA and watch the core beat…  (Enter to send)"
          disabled={busy}
          style={{
            // minWidth: 0 — an input's intrinsic minimum ignores `flex: 1` and
            // is what stops the row collapsing to fit.
            ...mono, flex: '1 1 180px', minWidth: 0,
            background: '#0d060a', border: '1px solid var(--border)',
            color: '#ddd', fontSize: 12, padding: '9px 12px', borderRadius: 4, outline: 'none',
          }}
        />
        <PressToTalk
          disabled={busy}
          context="core-talk"
          onTranscript={text => setInput(prev => (prev ? `${prev} ${text}` : text))}
        />
        <button
          onClick={() => setVoice(v => !v)}
          title="Speak replies aloud — Kokoro, running on this machine. The core beats to her actual amplitude."
          style={{
            ...mono, background: voice ? 'var(--orange)' : 'transparent',
            color: voice ? '#000' : 'var(--text-dim)',
            border: `1px solid ${voice ? 'var(--orange)' : 'var(--border)'}`,
            borderRadius: 4, padding: '8px 12px', fontSize: 11, fontWeight: 800,
            letterSpacing: 1, cursor: 'pointer', flexShrink: 0,
          }}
        >
          {voice ? '🔊 VOICE' : '🔇 VOICE'}
        </button>
        <button
          onClick={() => setBrief(b => !b)}
          title={brief
            ? 'BRIEF — she says only what matters; the full text stays on screen'
            : 'FULL — she reads the whole reply (markup still stripped)'}
          style={{
            ...mono, background: 'transparent',
            color: brief ? 'var(--green)' : 'var(--text-dim)',
            border: `1px solid ${brief ? 'var(--green)' : 'var(--border)'}`,
            borderRadius: 4, padding: '8px 10px', fontSize: 11, fontWeight: 800,
            letterSpacing: 1, cursor: 'pointer', flexShrink: 0,
          }}
        >{brief ? 'BRIEF' : 'FULL'}</button>
        <button
          onClick={send}
          disabled={busy || !input.trim()}
          style={{
            ...mono, background: busy || !input.trim() ? '#1a0a10' : 'var(--orange)',
            color: busy || !input.trim() ? '#553' : '#000',
            border: 'none', borderRadius: 4, padding: '8px 16px', fontSize: 12,
            fontWeight: 800, letterSpacing: 1, cursor: busy || !input.trim() ? 'not-allowed' : 'pointer',
            flexShrink: 0,
          }}
        >{busy ? '···' : 'SEND ▶'}</button>
      </div>

      {voice && voices.length > 0 && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 6 }}>
          <span style={{ ...mono, fontSize: 9, color: 'var(--muted)', letterSpacing: 1 }}>HER VOICE</span>
          <select
            value={voiceName}
            onChange={e => pickVoice(e.target.value)}
            style={{
              ...mono, background: '#0d060a', color: 'var(--text-dim)',
              border: '1px solid var(--border)', borderRadius: 4, fontSize: 10,
              padding: '4px 8px', outline: 'none', maxWidth: 340, cursor: 'pointer',
            }}
          >
            {voices.map(v => (
              <option key={v.name} value={v.name}>{v.name}</option>
            ))}
          </select>
          <span style={{ ...mono, fontSize: 9, color: 'var(--muted)' }}>
            kokoro · local · female voices first · pick to hear her
          </span>
        </div>
      )}
      {(reply || err) && (
        <div style={{
          ...mono, fontSize: 12, lineHeight: 1.7, color: err ? 'var(--red)' : '#cbb',
          background: '#060306', border: '1px solid var(--border)', borderRadius: 4,
          padding: '10px 14px', marginTop: 8, whiteSpace: 'pre-wrap', maxHeight: 220, overflowY: 'auto',
        }}>
          {err ? `⚠ ${err}` : reply}
        </div>
      )}
    </div>
  )
}
