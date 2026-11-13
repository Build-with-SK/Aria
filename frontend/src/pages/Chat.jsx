import React, { useState, useRef, useEffect } from 'react'
import axios from 'axios'
import { ArrowUp } from 'lucide-react'
import { streamChat } from '../hooks/streamChat'
import { startThinking, speakReply } from '../core/ariaVoice'
import { setCoreState } from '../core/coreBus'
import { useSystemHealth } from '../hooks/useApi'
import { Mark } from '../components/Navigation'

// ─── Markdown-lite renderer (bold, code, bullets) ────────────────────────────
function RenderText({ text }) {
  const lines = text.split('\n')
  return (
    <div style={{ lineHeight: 1.6 }}>
      {lines.map((line, i) => {
        // Bullet points
        if (line.startsWith('- ') || line.startsWith('• ')) {
          return (
            <div key={i} style={{ display: 'flex', gap: 9, marginBottom: 3 }}>
              <span aria-hidden="true" style={{ color: 'var(--ink-3)', flexShrink: 0 }}>•</span>
              <span>{renderInline(line.slice(2))}</span>
            </div>
          )
        }
        // Section headers (##)
        if (line.startsWith('## ')) {
          return (
            <div key={i} style={{
              fontFamily: 'var(--font-display)', fontSize: 15, fontWeight: 650,
              color: 'var(--ink)', marginTop: 14, marginBottom: 4,
            }}>{line.slice(3)}</div>
          )
        }
        // Rule lines (──)
        if (line.startsWith('──')) {
          return <div key={i} style={{ height: 1, background: 'var(--line)', margin: '8px 0' }} />
        }
        if (line.trim() === '') return <div key={i} style={{ height: 8 }} />
        return <div key={i}>{renderInline(line)}</div>
      })}
    </div>
  )
}

function renderInline(text) {
  // Bold **text**
  const parts = text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g)
  return parts.map((part, i) => {
    if (part.startsWith('**') && part.endsWith('**')) {
      return <strong key={i} style={{ color: 'var(--ink)', fontWeight: 650 }}>{part.slice(2, -2)}</strong>
    }
    if (part.startsWith('`') && part.endsWith('`')) {
      return (
        <code key={i} style={{
          fontSize: 13, color: 'var(--ink)',
          background: 'var(--inset)', padding: '1px 6px', borderRadius: 6,
        }}>{part.slice(1, -1)}</code>
      )
    }
    return part
  })
}

// ─── Single message ───────────────────────────────────────────────────────────
function Bubble({ msg }) {
  const isUser = msg.role === 'user'
  const isSystem = msg.role === 'system'

  if (isSystem) {
    return (
      <div style={{ textAlign: 'center', padding: '4px 0 14px', fontSize: 12.5, color: 'var(--ink-3)' }}>
        {msg.content}
      </div>
    )
  }

  const meta = (
    <div style={{ marginTop: 6, fontSize: 12, color: isUser ? 'inherit' : 'var(--ink-3)', opacity: isUser ? .78 : 1 }}>
      {msg.time}
      {/* Both chat routes return `tokens: {}`, which is truthy — the old
          check rendered a bare "↓ tokens" under every reply. */}
      {msg.tokens?.out != null && (
        <span style={{ marginLeft: 8 }}>{msg.tokens.in ? `${msg.tokens.in}↑ ` : ''}{msg.tokens.out}↓ tokens</span>
      )}
      {msg.source && (
        <span style={{ marginLeft: 8, color: msg.degraded ? 'var(--yellow)' : undefined }}>
          {msg.source === 'cloud' ? 'ARIA' : 'Direct'}{msg.model ? ` · ${msg.model}` : ''}{msg.degraded ? ' · Degraded' : ''}
        </span>
      )}
    </div>
  )

  if (isUser) {
    return (
      <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 16 }}>
        <div style={{
          maxWidth: '80%', background: 'var(--accent-solid)', color: 'var(--on-accent-solid)',
          borderRadius: '20px 20px 6px 20px', padding: '10px 15px',
          fontSize: 15, lineHeight: 1.5, whiteSpace: 'pre-wrap', overflowWrap: 'anywhere',
        }}>
          {msg.content}
          {meta}
        </div>
      </div>
    )
  }

  return (
    <div style={{ display: 'flex', gap: 11, marginBottom: 18, alignItems: 'flex-start' }}>
      <span aria-hidden="true" style={{ color: 'var(--accent)', marginTop: 1 }}><Mark size={26} /></span>
      <div style={{ minWidth: 0, flex: 1, fontSize: 15, color: 'var(--ink)', overflowWrap: 'anywhere' }}>
        <RenderText text={msg.content} />
        {meta}
      </div>
    </div>
  )
}

// ─── Typing indicator ─────────────────────────────────────────────────────────
function Typing({ status }) {
  return (
    <div role="status" style={{ display: 'flex', gap: 11, alignItems: 'center', marginBottom: 18 }}>
      <span aria-hidden="true" style={{ color: 'var(--accent)' }}><Mark size={26} /></span>
      <div style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
        {[0, 1, 2].map(i => (
          <div key={i} style={{
            width: 6, height: 6, borderRadius: '50%',
            background: 'var(--ink-3)',
            animation: `dot-bounce 1.2s ${i * 0.2}s ease-in-out infinite`,
          }} />
        ))}
        {/* What she is actually doing, from the server — never a script. */}
        {status && (
          <span style={{ fontSize: 13.5, color: 'var(--ink-2)', marginLeft: 8 }}>{status}</span>
        )}
      </div>
    </div>
  )
}

// ─── Quick prompt chips ───────────────────────────────────────────────────────
const QUICK = [
  "What are the top 5 buy signals right now?",
  "Summarise the current macro environment",
  "Which tickers have the strongest ML consensus?",
  "What's the current market regime and what does it mean?",
  "Find me a high conviction trade setup",
  "Which sectors look most bearish today?",
]

// ─── Main page ────────────────────────────────────────────────────────────────
/** `embedded` drops the standalone header and full-height sizing so the same
 *  component can live inside the Brain workspace without two ARIA titles
 *  stacked on one screen. */
export default function Chat({ embedded = false }) {
  const now = () => new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  const { data: sysHealth } = useSystemHealth()

  const [messages, setMessages] = useState([
    {
      role: 'system',
      content: new Date().toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'long' }),
    },
    {
      role: 'assistant',
      content: "I'm **ARIA**. I work from the current signal book, the ensemble's predictions, the macro regime and your research notes, and I tell you how old each of them is.\n\nAsk about the market, a specific ticker, a trade idea, or how the system reaches its calls.",
      time: now(),
    },
  ])

  const [input, setInput]   = useState('')
  const [loading, setLoading] = useState(false)
  const [status, setStatus] = useState('')
  const [error, setError]   = useState('')
  const [mode, setMode]     = useState('cloud')          // 'cloud' | 'local'
  const [localModel, setLocalModel] = useState('')
  const [ollamaUp, setOllamaUp]     = useState(true)
  const listRef   = useRef(null)
  const inputRef  = useRef(null)

  // Keep the newest message in view by scrolling the LIST, never the page.
  // scrollIntoView moved the whole document, so opening the Brain landed you
  // halfway down it with the header scrolled away.
  useEffect(() => {
    const el = listRef.current
    if (el) el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' })
  }, [messages, loading])

  useEffect(() => {
    axios.get('/api/brain/status').then(r => {
      setOllamaUp(!!r.data?.ollama_running)
      const names = (r.data?.models || []).map(m => m.name)
      setLocalModel(r.data?.daemon?.model && names.includes(r.data.daemon.model)
        ? r.data.daemon.model
        : names[0] || '')
    }).catch(() => setOllamaUp(false))
  }, [])

  const send = async (text) => {
    const userText = (text || input).trim()
    if (!userText || loading) return

    const userMsg = { role: 'user', content: userText, time: now() }
    const newMsgs = [...messages.filter(m => m.role !== 'system'), userMsg]
    setMessages(prev => [...prev, userMsg])
    setInput('')
    setError('')
    setLoading(true)
    startThinking()   // drive the Living Core (visible on the Brain page)

    try {
      const history = newMsgs
        .filter(m => m.role === 'user' || m.role === 'assistant')
        .map(m => ({ role: m.role, content: m.content }))

      let data
      if (mode === 'local') {
        data = (await axios.post('/api/chat/local', { messages: history, model: localModel || 'qwen2.5-coder:7b' })).data
      } else {
        // STREAMED. A draft message fills in as the answer is written; the
        // status line under the dots says which step is actually running.
        // `done` carries the recorded reply and replaces the draft, so what is
        // left on screen is exactly what /api/chat would have returned.
        const draftTime = now()
        // Pure updaters only: React may run them twice (StrictMode), so the
        // draft is found by its `draft` marker, never by a flag mutated here.
        const setDraft = update => setMessages(prev => {
          const last = prev[prev.length - 1]
          return last?.draft
            ? [...prev.slice(0, -1), update(last)]
            : [...prev, update({ role: 'assistant', content: '', time: draftTime, source: mode, draft: true })]
        })
        const dropDraft = () => setMessages(prev =>
          prev[prev.length - 1]?.draft ? prev.slice(0, -1) : prev)
        try {
          data = await streamChat('/api/chat/stream', { messages: history }, {
            status: d => setStatus(d.text),
            token: d => { setStatus(''); setDraft(m => ({ ...m, content: m.content + d.text })) },
            reset: () => setDraft(m => ({ ...m, content: '' })),
          })
        } catch (e) {
          dropDraft()
          // An answer-shaped refusal is an answer: say it, do not retry it.
          // Only a stream that could not START falls back to the blocking route.
          if (e.abstained || [401, 403, 429].includes(e.status)) {
            throw Object.assign(e, { response: { data: { detail: e.message } } })
          }
          data = (await axios.post('/api/chat', { messages: history })).data
        }
        dropDraft()
      }
      const ariaMsg = {
        role: 'assistant',
        content: data.content,
        time: now(),
        tokens: data.tokens,
        source: mode,
        // The brain path names the model that actually answered, and whether
        // it was a fallback (`degraded`, only under ARIA_DEGRADE=disclose).
        model: data.model || (mode === 'local' ? localModel : undefined),
        degraded: !!data.degraded,
        timing: data.timing,
      }
      setMessages(prev => [...prev, ariaMsg])
      speakReply(data.content, null, { voice: false })   // beat the core through the reply
    } catch (e) {
      const detail = e.response?.data?.detail || e.message
      setError(detail)
      setMessages(prev => [...prev, {
        role: 'assistant',
        content: `⚠ Error: ${detail}`,
        time: now(),
      }])
      setCoreState('idle')
    } finally {
      setLoading(false)
      setStatus('')
      setTimeout(() => inputRef.current?.focus(), 100)
    }
  }

  const handleKey = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      send()
    }
  }

  // The status dot used to be a hardcoded green pulse with the word ONLINE
  // next to it — it said ONLINE while the backend was stopped, which is the
  // §23 failure exactly: a live indicator that cannot report anything but
  // life. It now reads the worker registry, so it goes amber when a daemon
  // behind the conversation has stalled.
  const health = sysHealth
  const wState = health?.workers
  const dot = !health ? { c: 'var(--ink-3)', t: 'Connecting' }
    : wState?.healthy === false ? { c: 'var(--yellow)', t: 'Degraded' }
    : { c: 'var(--green)', t: 'Online' }

  const canSend = input.trim() && !loading
  const modeBtn = on => ({
    height: 30, padding: '0 13px', borderRadius: 999, border: 'none', flexShrink: 0,
    fontSize: 13, fontWeight: on ? 600 : 550, whiteSpace: 'nowrap',
    background: on ? 'var(--sheet)' : 'transparent',
    color: on ? 'var(--ink)' : 'var(--ink-2)',
    boxShadow: on ? '0 1px 4px rgba(10,20,50,.14)' : 'none',
  })

  return (
    <div className="bb-card" style={{
      maxWidth: embedded ? '100%' : 900, margin: '0 auto',
      display: 'flex', flexDirection: 'column',
      height: embedded ? 'min(680px, calc(100vh - 230px))' : 'calc(100vh - 140px)',
      minHeight: 480,
    }}>
      {/* Header. Embedded in the Brain it is one line; standalone it also
          names what ARIA is. */}
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, marginBottom: 12, flexShrink: 0 }}>
        <div className="bb-card-header" style={{ margin: 0 }}>Ask ARIA</div>
        {!embedded && (
          <div style={{ fontSize: 13, color: 'var(--ink-3)' }}>
            Autonomous Research &amp; Investment Architect · self-hosted
          </div>
        )}
        <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 7, fontSize: 12.5, fontWeight: 600, color: dot.c }}>
          <span aria-hidden="true" style={{ width: 7, height: 7, borderRadius: '50%', background: dot.c }} />
          {dot.t}
        </div>
      </div>

      {/* Message list */}
      <div ref={listRef} aria-live="polite" style={{ flex: 1, overflowY: 'auto', padding: '4px 2px', minHeight: 0 }}>
        {messages.map((msg, i) => <Bubble key={i} msg={msg} />)}
        {loading && !messages[messages.length - 1]?.draft && <Typing status={status} />}
      </div>

      {/* Quick prompts (only when no real conversation yet) */}
      {messages.filter(m => m.role === 'user').length === 0 && !loading && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, margin: '10px 0 4px', flexShrink: 0 }}>
          {QUICK.map(q => (
            <button
              key={q}
              onClick={() => send(q)}
              style={{
                background: 'transparent', border: '1px solid var(--line-2)',
                borderRadius: 999, padding: '7px 13px',
                fontSize: 13, color: 'var(--ink)',
              }}
            >{q}</button>
          ))}
        </div>
      )}

      {/* Brain / direct-model toggle. The internal mode is still called
          'cloud' because the state and the reply's `source` field use it, but
          the label said "Claude API" long after /api/chat stopped calling one:
          the router filters vendor providers out of this path unless the owner
          sets ARIA_ALLOW_VENDOR_BRAIN (src/inference/policy.py). */}
      <div style={{ display: 'flex', gap: 10, marginTop: 10, flexShrink: 0, alignItems: 'center', flexWrap: 'wrap' }}>
        <div role="group" aria-label="Who answers" style={{ display: 'inline-flex', gap: 3, padding: 3, borderRadius: 999, background: 'var(--inset)', maxWidth: '100%', overflowX: 'auto' }}>
          <button
            onClick={() => setMode('cloud')} aria-pressed={mode === 'cloud'}
            title="ARIA's own brain, self-hosted. Streams the reply, and can queue a trade idea for your approval."
            style={modeBtn(mode === 'cloud')}
          >ARIA brain</button>
          <button
            onClick={() => setMode('local')} aria-pressed={mode === 'local'}
            title="Ask one local model directly. Same market context; never queues a trade."
            style={modeBtn(mode === 'local')}
          >Direct · {localModel || 'no model'}</button>
        </div>
        {mode === 'local' && !ollamaUp && (
          <span className="aria-pill warn">Local model offline · direct mode unavailable</span>
        )}
      </div>

      {/* Input bar */}
      <div style={{
        flexShrink: 0, display: 'flex', gap: 8, alignItems: 'flex-end',
        background: 'var(--inset)', border: '1px solid var(--line)',
        borderRadius: 24, padding: '6px 6px 6px 18px',
        marginTop: 10,
      }}>
        <textarea
          ref={inputRef}
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={handleKey}
          placeholder="Ask about the market, a ticker or a trade idea"
          aria-label="Message ARIA. Enter sends, Shift+Enter adds a line."
          rows={1}
          style={{
            flex: 1, background: 'transparent', border: 'none', outline: 'none', boxShadow: 'none',
            color: 'var(--ink)', fontSize: 15, padding: '8px 0',
            resize: 'none', lineHeight: 1.45,
            maxHeight: 120, overflowY: 'auto',
          }}
          onInput={e => {
            e.target.style.height = 'auto'
            e.target.style.height = Math.min(e.target.scrollHeight, 120) + 'px'
          }}
        />
        <button
          onClick={() => send()}
          disabled={!canSend}
          aria-label={loading ? 'ARIA is replying' : 'Send'}
          style={{
            width: 38, height: 38, borderRadius: '50%', border: 'none', flexShrink: 0,
            display: 'grid', placeItems: 'center',
            background: canSend ? 'var(--accent-solid)' : 'var(--inset)',
            color: canSend ? 'var(--on-accent-solid)' : 'var(--ink-3)',
          }}
        >
          <ArrowUp size={19} strokeWidth={2.4} aria-hidden="true" />
        </button>
      </div>

      <div style={{ fontSize: 12, color: 'var(--ink-3)', textAlign: 'center', marginTop: 8, flexShrink: 0 }}>
        ARIA is not a licensed financial advisor. All outputs are for research purposes only.
      </div>

      <style>{`
        @keyframes dot-bounce {
          0%, 80%, 100% { transform: translateY(0); opacity: 0.4; }
          40% { transform: translateY(-5px); opacity: 1; }
        }
      `}</style>
    </div>
  )
}
