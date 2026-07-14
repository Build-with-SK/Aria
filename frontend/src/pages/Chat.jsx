import React, { useState, useRef, useEffect } from 'react'
import axios from 'axios'

// ─── Markdown-lite renderer (bold, code, bullets) ────────────────────────────
function RenderText({ text }) {
  const lines = text.split('\n')
  return (
    <div style={{ lineHeight: 1.65 }}>
      {lines.map((line, i) => {
        // Bullet points
        if (line.startsWith('- ') || line.startsWith('• ')) {
          return (
            <div key={i} style={{ display: 'flex', gap: 8, marginBottom: 2 }}>
              <span style={{ color: 'var(--orange)', flexShrink: 0 }}>▸</span>
              <span>{renderInline(line.slice(2))}</span>
            </div>
          )
        }
        // Section headers (##)
        if (line.startsWith('## ')) {
          return (
            <div key={i} style={{
              fontFamily: 'var(--mono)', fontSize: 11, fontWeight: 800,
              color: 'var(--orange)', letterSpacing: 2,
              marginTop: 12, marginBottom: 4, textTransform: 'uppercase',
            }}>{line.slice(3)}</div>
          )
        }
        // Code block lines (──)
        if (line.startsWith('──')) {
          return <div key={i} style={{ color: '#333', fontFamily: 'var(--mono)', fontSize: 11, margin: '2px 0' }}>{line}</div>
        }
        if (line.trim() === '') return <div key={i} style={{ height: 6 }} />
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
      return <strong key={i} style={{ color: '#fff', fontWeight: 700 }}>{part.slice(2, -2)}</strong>
    }
    if (part.startsWith('`') && part.endsWith('`')) {
      return (
        <code key={i} style={{
          fontFamily: 'var(--mono)', fontSize: 11, color: 'var(--orange)',
          background: 'rgba(255,140,0,0.12)', padding: '1px 5px', borderRadius: 3,
        }}>{part.slice(1, -1)}</code>
      )
    }
    return part
  })
}

// ─── Single message bubble ────────────────────────────────────────────────────
function Bubble({ msg }) {
  const isUser = msg.role === 'user'
  const isSystem = msg.role === 'system'

  if (isSystem) {
    return (
      <div style={{
        textAlign: 'center', padding: '8px 0',
        fontFamily: 'var(--mono)', fontSize: 10, color: '#444',
        letterSpacing: 1,
      }}>{msg.content}</div>
    )
  }

  return (
    <div style={{
      display: 'flex',
      flexDirection: isUser ? 'row-reverse' : 'row',
      gap: 10, marginBottom: 16, alignItems: 'flex-start',
    }}>
      {/* Avatar */}
      <div style={{
        width: 28, height: 28, borderRadius: 4, flexShrink: 0,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        background: isUser ? '#1a1a1a' : 'rgba(255,140,0,0.15)',
        border: `1px solid ${isUser ? '#333' : 'rgba(255,140,0,0.4)'}`,
        fontFamily: 'var(--mono)', fontSize: 11, fontWeight: 800,
        color: isUser ? 'var(--text-dim)' : 'var(--orange)',
      }}>
        {isUser ? 'YOU' : 'AR'}
      </div>

      {/* Bubble */}
      <div style={{
        maxWidth: '78%',
        background: isUser ? '#0d0d0d' : '#090909',
        border: `1px solid ${isUser ? '#222' : 'rgba(255,140,0,0.2)'}`,
        borderRadius: isUser ? '8px 2px 8px 8px' : '2px 8px 8px 8px',
        padding: '12px 16px',
        fontFamily: isUser ? 'var(--mono)' : 'inherit',
        fontSize: 13, color: isUser ? '#bbb' : '#ccc',
        lineHeight: 1.6,
      }}>
        {isUser ? msg.content : <RenderText text={msg.content} />}

        <div style={{
          marginTop: 8, fontFamily: 'var(--mono)', fontSize: 9,
          color: '#333', textAlign: isUser ? 'left' : 'right',
        }}>
          {msg.time}
          {msg.tokens && (
            <span style={{ marginLeft: 8 }}>{msg.tokens.in ? `${msg.tokens.in}↑ ` : ''}{msg.tokens.out}↓ tokens</span>
          )}
          {msg.source && (
            <span style={{
              marginLeft: 8,
              color: msg.source === 'cloud' ? 'var(--orange)' : '#00aa44',
            }}>
              [{msg.source === 'cloud' ? 'CLOUD' : `LOCAL · ${msg.model || ''}`}]
            </span>
          )}
        </div>
      </div>
    </div>
  )
}

// ─── Typing indicator ─────────────────────────────────────────────────────────
function Typing() {
  return (
    <div style={{ display: 'flex', gap: 10, alignItems: 'flex-start', marginBottom: 16 }}>
      <div style={{
        width: 28, height: 28, borderRadius: 4, flexShrink: 0,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        background: 'rgba(255,140,0,0.15)', border: '1px solid rgba(255,140,0,0.4)',
        fontFamily: 'var(--mono)', fontSize: 11, fontWeight: 800, color: 'var(--orange)',
      }}>AR</div>
      <div style={{
        background: '#090909', border: '1px solid rgba(255,140,0,0.2)',
        borderRadius: '2px 8px 8px 8px', padding: '12px 16px',
        display: 'flex', alignItems: 'center', gap: 5,
      }}>
        {[0, 1, 2].map(i => (
          <div key={i} style={{
            width: 6, height: 6, borderRadius: '50%',
            background: 'var(--orange)',
            animation: `dot-bounce 1.2s ${i * 0.2}s ease-in-out infinite`,
          }} />
        ))}
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
export default function Chat() {
  const now = () => new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })

  const [messages, setMessages] = useState([
    {
      role: 'system',
      content: `── ARIA INTELLIGENCE INTERFACE  ${new Date().toLocaleDateString()} ──`,
    },
    {
      role: 'assistant',
      content: "I'm **ARIA** — your trading intelligence agent. I have full access to the current signal engine output: 766 tickers, ML ensemble predictions, macro regime analysis, and live execution queue.\n\nAsk me anything about the market, a specific ticker, trade ideas, or the system methodology. What would you like to analyse?",
      time: now(),
    },
  ])

  const [input, setInput]   = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError]   = useState('')
  const [mode, setMode]     = useState('cloud')          // 'cloud' | 'local'
  const [localModel, setLocalModel] = useState('')
  const [ollamaUp, setOllamaUp]     = useState(true)
  const bottomRef = useRef(null)
  const inputRef  = useRef(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
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

    try {
      const history = newMsgs
        .filter(m => m.role === 'user' || m.role === 'assistant')
        .map(m => ({ role: m.role, content: m.content }))

      const r = mode === 'local'
        ? await axios.post('/api/chat/local', { messages: history, model: localModel || 'qwen2.5-coder:7b' })
        : await axios.post('/api/chat', { messages: history })
      const ariaMsg = {
        role: 'assistant',
        content: r.data.content,
        time: now(),
        tokens: r.data.tokens,
        source: mode,
        model: mode === 'local' ? (r.data.model || localModel) : undefined,
      }
      setMessages(prev => [...prev, ariaMsg])
    } catch (e) {
      const detail = e.response?.data?.detail || e.message
      setError(detail)
      setMessages(prev => [...prev, {
        role: 'assistant',
        content: `⚠ Error: ${detail}`,
        time: now(),
      }])
    } finally {
      setLoading(false)
      setTimeout(() => inputRef.current?.focus(), 100)
    }
  }

  const handleKey = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      send()
    }
  }

  return (
    <div style={{
      maxWidth: 900, margin: '0 auto',
      display: 'flex', flexDirection: 'column',
      height: 'calc(100vh - 68px)',
    }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16, flexShrink: 0 }}>
        <div style={{
          width: 36, height: 36, borderRadius: 6,
          background: 'rgba(255,140,0,0.15)', border: '1px solid rgba(255,140,0,0.5)',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          fontFamily: 'var(--mono)', fontSize: 14, fontWeight: 800, color: 'var(--orange)',
        }}>AR</div>
        <div>
          <div style={{ fontFamily: 'var(--mono)', fontSize: 15, fontWeight: 800, color: 'var(--orange)' }}>
            ARIA INTELLIGENCE
          </div>
          <div style={{ fontFamily: 'var(--mono)', fontSize: 10, color: 'var(--text-dim)' }}>
            Adaptive Risk Intelligence Agent · 766-ticker universe · LightGBM ensemble
          </div>
        </div>
        <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 6 }}>
          <div style={{
            width: 7, height: 7, borderRadius: '50%',
            background: 'var(--green)', boxShadow: '0 0 8px var(--green)',
            animation: 'pulse-glow 2s ease-in-out infinite',
          }} />
          <span style={{ fontFamily: 'var(--mono)', fontSize: 10, color: 'var(--green)' }}>ONLINE</span>
        </div>
      </div>

      {/* Message list */}
      <div style={{
        flex: 1, overflowY: 'auto', padding: '4px 0',
        scrollbarWidth: 'thin', scrollbarColor: '#222 transparent',
      }}>
        {messages.map((msg, i) => <Bubble key={i} msg={msg} />)}
        {loading && <Typing />}
        <div ref={bottomRef} />
      </div>

      {/* Quick prompts (only when no real conversation yet) */}
      {messages.filter(m => m.role === 'user').length === 0 && !loading && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 12, flexShrink: 0 }}>
          {QUICK.map(q => (
            <button
              key={q}
              onClick={() => send(q)}
              style={{
                background: 'transparent', border: '1px solid #222',
                borderRadius: 4, padding: '5px 10px',
                fontFamily: 'var(--mono)', fontSize: 10, color: 'var(--text-dim)',
                cursor: 'pointer', transition: 'all 0.15s',
              }}
              onMouseEnter={e => { e.target.style.borderColor = 'var(--orange)'; e.target.style.color = 'var(--orange)' }}
              onMouseLeave={e => { e.target.style.borderColor = '#222'; e.target.style.color = 'var(--text-dim)' }}
            >{q}</button>
          ))}
        </div>
      )}

      {/* Cloud / Local mode toggle */}
      <div style={{ display: 'flex', gap: 8, marginTop: 8, flexShrink: 0, alignItems: 'center' }}>
        <button
          onClick={() => setMode('cloud')}
          style={{
            background: mode === 'cloud' ? 'var(--orange)' : 'transparent',
            color: mode === 'cloud' ? '#000' : 'var(--text-dim)',
            border: `1px solid ${mode === 'cloud' ? 'var(--orange)' : '#222'}`,
            borderRadius: 4, padding: '4px 12px', cursor: 'pointer',
            fontFamily: 'var(--mono)', fontSize: 10, fontWeight: 800, letterSpacing: 1,
          }}
        >☁ CLOUD — Claude API</button>
        <button
          onClick={() => setMode('local')}
          style={{
            background: mode === 'local' ? '#00aa44' : 'transparent',
            color: mode === 'local' ? '#000' : 'var(--text-dim)',
            border: `1px solid ${mode === 'local' ? '#00aa44' : '#222'}`,
            borderRadius: 4, padding: '4px 12px', cursor: 'pointer',
            fontFamily: 'var(--mono)', fontSize: 10, fontWeight: 800, letterSpacing: 1,
          }}
        >⚡ LOCAL — {localModel || 'no model'}</button>
        {mode === 'local' && !ollamaUp && (
          <span style={{
            fontFamily: 'var(--mono)', fontSize: 10, color: '#000',
            background: 'var(--orange)', borderRadius: 3, padding: '3px 10px', fontWeight: 800,
          }}>
            ⚠ OLLAMA OFFLINE — run: ollama serve
          </span>
        )}
      </div>

      {/* Input bar */}
      <div style={{
        flexShrink: 0, display: 'flex', gap: 10, alignItems: 'flex-end',
        background: '#070707', border: '1px solid var(--border)',
        borderRadius: 8, padding: '10px 14px',
        marginTop: 8,
      }}>
        <textarea
          ref={inputRef}
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={handleKey}
          placeholder="Ask ARIA anything about the market…   (Enter to send · Shift+Enter for newline)"
          rows={1}
          style={{
            flex: 1, background: 'transparent', border: 'none', outline: 'none',
            color: '#ddd', fontFamily: 'var(--mono)', fontSize: 13,
            resize: 'none', lineHeight: 1.5,
            maxHeight: 120, overflowY: 'auto',
          }}
          onInput={e => {
            e.target.style.height = 'auto'
            e.target.style.height = Math.min(e.target.scrollHeight, 120) + 'px'
          }}
        />
        <button
          onClick={() => send()}
          disabled={!input.trim() || loading}
          style={{
            background: input.trim() && !loading ? 'var(--orange)' : '#1a1a1a',
            color: input.trim() && !loading ? '#000' : '#444',
            border: 'none', borderRadius: 5,
            padding: '8px 16px', fontFamily: 'var(--mono)',
            fontSize: 12, fontWeight: 800, cursor: input.trim() && !loading ? 'pointer' : 'not-allowed',
            transition: 'all 0.15s', letterSpacing: 1,
            flexShrink: 0,
          }}
        >
          {loading ? '...' : 'SEND ▶'}
        </button>
      </div>

      <div style={{ fontFamily: 'var(--mono)', fontSize: 9, color: '#2a2a2a', textAlign: 'center', marginTop: 6, flexShrink: 0 }}>
        ARIA is not a licensed financial advisor. All outputs are for research purposes only.
      </div>

      <style>{`
        @keyframes dot-bounce {
          0%, 80%, 100% { transform: translateY(0); opacity: 0.4; }
          40% { transform: translateY(-5px); opacity: 1; }
        }
        @keyframes pulse-glow {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.4; }
        }
      `}</style>
    </div>
  )
}
