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

const mono = { fontFamily: 'var(--font)' }

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
        <span style={{ ...mono, fontSize: 13, fontWeight: 600, color: 'var(--accent)', flexShrink: 0 }}>
          ▸ Talk
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
            background: 'var(--inset)', border: '1px solid var(--line)',
            color: 'var(--ink)', fontSize: 14, padding: '9px 12px', borderRadius: 10, outline: 'none',
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
            ...mono, background: voice ? 'var(--accent)' : 'transparent',
            color: voice ? 'var(--inset)' : 'var(--ink-2)',
            border: `1px solid ${voice ? 'var(--accent)' : 'var(--line)'}`,
            borderRadius: 10, padding: '8px 12px', fontSize: 13, fontWeight: 600,
            cursor: 'pointer', flexShrink: 0,
          }}
        >
          {voice ? '🔊 Voice' : '🔇 Voice'}
        </button>
        <button
          onClick={() => setBrief(b => !b)}
          title={brief
            ? 'BRIEF — she says only what matters; the full text stays on screen'
            : 'FULL — she reads the whole reply (markup still stripped)'}
          style={{
            ...mono, background: 'transparent',
            color: brief ? 'var(--green)' : 'var(--ink-2)',
            border: `1px solid ${brief ? 'var(--green)' : 'var(--line)'}`,
            borderRadius: 10, padding: '8px 10px', fontSize: 13, fontWeight: 600,
            cursor: 'pointer', flexShrink: 0,
          }}
        >{brief ? 'Brief' : 'Full'}</button>
        <button
          onClick={send}
          disabled={busy || !input.trim()}
          style={{
            ...mono, background: busy || !input.trim() ? 'var(--inset)' : 'var(--accent)',
            color: busy || !input.trim() ? 'var(--line)' : 'var(--inset)',
            border: 'none', borderRadius: 10, padding: '8px 16px', fontSize: 14,
            fontWeight: 600, cursor: busy || !input.trim() ? 'not-allowed' : 'pointer',
            flexShrink: 0,
          }}
        >{busy ? '···' : 'Send ▶'}</button>
      </div>

      {voice && voices.length > 0 && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 6 }}>
          <span style={{ ...mono, fontSize: 12, color: 'var(--ink-3)', }}>Her voice</span>
          <select
            value={voiceName}
            onChange={e => pickVoice(e.target.value)}
            style={{
              ...mono, background: 'var(--inset)', color: 'var(--ink-2)',
              border: '1px solid var(--line)', borderRadius: 10, fontSize: 12,
              padding: '4px 8px', outline: 'none', maxWidth: 340, cursor: 'pointer',
            }}
          >
            {voices.map(v => (
              <option key={v.name} value={v.name}>{v.name}</option>
            ))}
          </select>
          <span style={{ ...mono, fontSize: 12, color: 'var(--ink-3)' }}>
            kokoro · local · female voices first · pick to hear her
          </span>
        </div>
      )}
      {(reply || err) && (
        <div style={{
          ...mono, fontSize: 14, lineHeight: 1.7, color: err ? 'var(--red)' : 'var(--ink)',
          background: 'var(--inset)', border: '1px solid var(--line)', borderRadius: 10,
          padding: '10px 14px', marginTop: 8, whiteSpace: 'pre-wrap', maxHeight: 220, overflowY: 'auto',
        }}>
          {err ? `⚠ ${err}` : reply}
        </div>
      )}
    </div>
  )
}
