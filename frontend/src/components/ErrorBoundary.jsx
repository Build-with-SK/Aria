/**
 * components/ErrorBoundary.jsx
 * One broken component must not take the whole app down.
 *
 * React unmounts the entire tree when a render throws and nothing catches it —
 * the user gets a blank white page with no explanation and no way back. That
 * happened in this app for real: a `useEffect` that returned a promise made
 * React call it as a cleanup function, and the Track Record page went white.
 *
 * This catches the throw, keeps the navigation alive, and gives the
 * user something to do: retry, go home, or copy the error to report it. The
 * technical detail is present but folded away — a stack trace as a landing page
 * helps nobody.
 */
import React from 'react'
import { appUrl } from '../core/appPath'


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

    return (
      <div className="bb-card" role="alert" style={{ maxWidth: 680, margin: '40px auto', padding: 26 }}>
        <span className="aria-pill bad">Error</span>
        <h1 style={{ fontSize: 22, marginTop: 12 }}>This page hit an error</h1>
        <p style={{ color: 'var(--ink-2)', fontSize: 14.5, lineHeight: 1.6, marginTop: 8 }}>
          Something in this page broke while rendering. The rest of ARIA is still running — your
          data is untouched and nothing was sent anywhere. Try again, or move to another page.
        </p>

        <div style={{ display: 'flex', gap: 8, marginTop: 18, flexWrap: 'wrap' }}>
          <button onClick={() => this.setState({ error: null, info: null })} className="aria-btn primary">
            Try again
          </button>
          <button onClick={() => { window.location.href = appUrl('/') }} className="aria-btn">Go to Brain</button>
          <button onClick={this.copy} className="aria-btn">{copied ? 'Copied ✓' : 'Copy error detail'}</button>
        </div>

        <details style={{ marginTop: 18 }}>
          <summary style={{ fontSize: 13, color: 'var(--ink-3)', cursor: 'pointer' }}>
            Technical detail
          </summary>
          <pre style={{
            fontSize: 12, color: 'var(--ink-2)', marginTop: 8, background: 'var(--inset)', borderRadius: 12, padding: 12,
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
