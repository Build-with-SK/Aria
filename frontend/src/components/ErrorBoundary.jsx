/**
 * components/ErrorBoundary.jsx
 * One broken component must not take the whole app down.
 *
 * React unmounts the entire tree when a render throws and nothing catches it —
 * the user gets a blank white page with no explanation and no way back. That
 * happened in this app for real: a `useEffect` that returned a promise made
 * React call it as a cleanup function, and the Track Record page went white.
 *
 * This catches the throw, keeps the sidebar and navigation alive, and gives the
 * user something to do: retry, go home, or copy the error to report it. The
 * technical detail is present but folded away — a stack trace as a landing page
 * helps nobody.
 */
import React from 'react'

const MONO = 'var(--mono)'

export default class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props)
    this.state = { error: null, info: null, copied: false }
  }

  static getDerivedStateFromError(error) {
    return { error }
  }

  componentDidCatch(error, info) {
    this.setState({ info })
    // Keep it in the console for anyone debugging with devtools open.
    console.error('ARIA caught a render error:', error, info)
  }

  componentDidUpdate(prev) {
    // A navigation is an implicit "try something else" — clear the error so the
    // new page gets a chance instead of showing the old failure forever.
    if (this.state.error && prev.resetKey !== this.props.resetKey) {
      this.setState({ error: null, info: null, copied: false })
    }
  }

  copy = () => {
    const { error, info } = this.state
    const text = `ARIA error\n${error?.toString()}\n${info?.componentStack || ''}`
    navigator.clipboard?.writeText(text).then(() => {
      this.setState({ copied: true })
      setTimeout(() => this.setState({ copied: false }), 2000)
    }).catch(() => {})
  }

  render() {
    const { error, info, copied } = this.state
    if (!error) return this.props.children

    const btn = {
      fontFamily: MONO, fontSize: 11, fontWeight: 700, padding: '8px 16px',
      cursor: 'pointer', borderRadius: 4, minHeight: 32,
      background: 'none', border: '1px solid var(--border-2)', color: 'var(--text-dim)',
    }

    return (
      <div style={{ maxWidth: 680, margin: '40px auto', padding: 22,
                    border: '1px solid var(--red)', borderRadius: 8, background: 'rgba(255,85,96,.04)' }}>
        <div style={{ fontFamily: MONO, fontSize: 14, fontWeight: 800, color: 'var(--red)', letterSpacing: '.1em' }}>
          THIS PAGE HIT AN ERROR
        </div>
        <p style={{ color: 'var(--text)', fontSize: 13, lineHeight: 1.7, marginTop: 10 }}>
          Something in this page broke while rendering. The rest of ARIA is still running — your
          data is untouched and nothing was sent anywhere. Try again, or move to another page.
        </p>

        <div style={{ display: 'flex', gap: 8, marginTop: 14, flexWrap: 'wrap' }}>
          <button onClick={() => this.setState({ error: null, info: null })}
            style={{ ...btn, borderColor: 'var(--orange)', color: 'var(--orange)' }}>
            TRY AGAIN
          </button>
          <button onClick={() => { window.location.href = '/' }} style={btn}>GO TO COMMAND DECK</button>
          <button onClick={this.copy} style={btn}>{copied ? 'COPIED ✓' : 'COPY ERROR DETAIL'}</button>
        </div>

        <details style={{ marginTop: 16 }}>
          <summary style={{ fontFamily: MONO, fontSize: 10, color: 'var(--muted)', cursor: 'pointer' }}>
            Technical detail
          </summary>
          <pre style={{
            fontFamily: MONO, fontSize: 10, color: 'var(--muted)', marginTop: 8,
            whiteSpace: 'pre-wrap', wordBreak: 'break-word', maxHeight: 260, overflow: 'auto',
          }}>
            {error?.toString()}
            {info?.componentStack}
          </pre>
        </details>
      </div>
    )
  }
}
