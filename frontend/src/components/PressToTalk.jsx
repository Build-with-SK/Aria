import React, { useState, useRef, useEffect } from 'react'

// ─── PressToTalk — hold to speak, release to send ────────────────────────────
// The microphone opens when the button is held and closes when it is let go.
// There is no toggle and no "always listening" mode: the backend has no
// ambient path (src/brain/cognitive/hearing.py) and this is the front half of
// the same rule. A hot mic on the machine that holds the vault is a large
// standing risk for a small convenience.
//
// The track is stopped explicitly on release. Leaving it live keeps the
// browser's recording indicator lit, which — correctly — reads to the user as
// "it is still listening".
//
// MediaRecorder is native; nothing new was installed for this. Chrome records
// webm/opus, which PyAV decodes on the server without ffmpeg.

const mono = { fontFamily: 'var(--mono)' }

const btn = (active, disabled) => ({
  ...mono,
  fontSize: 11,
  fontWeight: 800,
  letterSpacing: 1,
  padding: '6px 12px',
  borderRadius: 3,
  cursor: disabled ? 'not-allowed' : 'pointer',
  userSelect: 'none',
  touchAction: 'none',
  flexShrink: 0,
  border: `1px solid ${active ? 'var(--orange)' : '#333'}`,
  background: active ? 'rgba(255,138,0,0.14)' : 'transparent',
  color: disabled ? '#555' : (active ? 'var(--orange)' : '#aaa'),
  transition: 'background 120ms, border-color 120ms, color 120ms',
})

/**
 * @param {(text:string)=>void} onTranscript  called with what she heard
 * @param {boolean} remember  keep the transcript in the spoken corpus
 * @param {string} context    a label stored alongside it
 */
export default function PressToTalk({ onTranscript, remember = true, context = 'chat', disabled = false }) {
  const [recording, setRecording] = useState(false)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [seconds, setSeconds] = useState(0)
  const recRef = useRef(null)
  const chunksRef = useRef([])
  const streamRef = useRef(null)
  const tickRef = useRef(null)

  // A component that unmounts mid-recording must not leave the microphone on.
  useEffect(() => () => stopTracks(), [])

  function stopTracks() {
    clearInterval(tickRef.current)
    try { recRef.current?.state === 'recording' && recRef.current.stop() } catch { /* ignore */ }
    streamRef.current?.getTracks().forEach(t => t.stop())
    streamRef.current = null
  }

  const start = async e => {
    e?.preventDefault?.()
    if (recording || busy || disabled) return
    setErr('')
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true },
      })
      streamRef.current = stream
      chunksRef.current = []
      const rec = new MediaRecorder(stream)
      rec.ondataavailable = ev => { if (ev.data?.size) chunksRef.current.push(ev.data) }
      rec.onstop = () => send(new Blob(chunksRef.current, { type: rec.mimeType || 'audio/webm' }))
      recRef.current = rec
      rec.start()
      setRecording(true)
      setSeconds(0)
      tickRef.current = setInterval(() => setSeconds(s => s + 1), 1000)
    } catch (e2) {
      setErr(e2.name === 'NotAllowedError'
        ? 'microphone permission refused'
        : (e2.message || 'no microphone'))
    }
  }

  const stop = e => {
    e?.preventDefault?.()
    if (!recording) return
    setRecording(false)
    clearInterval(tickRef.current)
    try { recRef.current?.stop() } catch { /* ignore */ }
    // Tracks are released here, not in onstop: the indicator should go out the
    // instant the button is let go.
    streamRef.current?.getTracks().forEach(t => t.stop())
    streamRef.current = null
  }

  async function send(blob) {
    if (!blob || blob.size < 1200) {      // a tap, not speech
      setErr('too short — hold the button while you talk')
      return
    }
    setBusy(true)
    try {
      const form = new FormData()
      form.append('file', blob, 'clip.webm')
      const res = await fetch(
        `/api/aria/listen?remember=${remember ? 'true' : 'false'}&context=${encodeURIComponent(context)}`,
        { method: 'POST', body: form })
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || `listen failed (${res.status})`)
      const text = (data.text || '').trim()
      if (!text) {
        setErr('nothing intelligible — try again closer to the mic')
      } else {
        onTranscript?.(text)
        if (data.dropped) {
          setErr(`heard it, dropped ${data.dropped} unclear segment${data.dropped > 1 ? 's' : ''}`)
        }
      }
    } catch (e) {
      setErr(String(e.message || e))
    } finally {
      setBusy(false)
    }
  }

  const label = busy ? 'HEARING…' : recording ? `● ${seconds}s — RELEASE` : '🎤 HOLD'

  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
      <button
        type="button"
        disabled={disabled || busy}
        style={btn(recording, disabled || busy)}
        onMouseDown={start}
        onMouseUp={stop}
        onMouseLeave={stop}
        onTouchStart={start}
        onTouchEnd={stop}
        onTouchCancel={stop}
        title="Hold to talk. The microphone closes the moment you let go — there is no always-on mode."
      >
        {label}
      </button>
      {err && (
        <span style={{ ...mono, fontSize: 10, color: 'var(--orange)', opacity: 0.85 }}>
          {err}
        </span>
      )}
    </span>
  )
}
