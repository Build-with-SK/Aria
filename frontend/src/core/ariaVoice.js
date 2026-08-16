// ─── ariaVoice — ARIA's speaking voice + the Living Core's beat ───────────────
// She speaks with KOKORO, served from /api/aria/speak. Not `speechSynthesis`:
// on Windows that is a SAPI formant synthesiser reading characters, and no
// amount of rate/pitch tuning stops it sounding like a computer. Kokoro is a
// neural model running locally — nothing she says leaves the machine.
//
// The old browser path is gone rather than kept as a fallback. A silent
// downgrade to the robotic voice would be the exact thing the owner objected
// to, arriving unannounced; if Kokoro is unreachable she says nothing and the
// caller is told why. Same rule as her reasoning brain: abstain, do not
// substitute.
//
// Two things still run off the voice, and both are kept:
//   · the word-by-word screen reveal (independent of audio, so a muted or
//     failed voice never freezes the transcript)
//   · the Living Core's beat — now driven by her ACTUAL amplitude through a
//     WebAudio analyser, so the core pulses on what she is saying rather than
//     on a timer pretending to be speech
//
// The server strips markup and normalises numbers (src/brain/cognitive/voice.py),
// so this file no longer needs to decide what is speakable — it sends the
// digest and plays what comes back.

import { setCoreState, pushBeat } from './coreBus'
import { spokenDigest, toPlainSpeech } from './spokenDigest'

const WORD_MS = 45        // on-screen reveal cadence per word
const TAIL_MS = 900       // ease-to-idle hold after everything finishes

const STORE_KEY = 'aria.kokoroVoice'
const DEFAULT_VOICE = 'af_heart'

// ── which voice she uses ─────────────────────────────────────────────────────

let cachedVoices = []

/** Female voices first — the register asked for. Kokoro names them by prefix:
 *  af_/bf_ are female (American/British), am_/bm_ male.
 *
 *  DEFAULT_VOICE ranks above every other female voice so the app opens on the
 *  same voice the backend uses. Ranked purely by prefix, the list sorted
 *  alphabetically and the UI silently adopted `af_alloy` while the server's
 *  default stayed `af_heart` — two voices for one assistant, depending on
 *  which side you asked. */
function rank(name) {
  if (name === DEFAULT_VOICE) return 4
  if (/^af_/.test(name)) return 3
  if (/^bf_/.test(name)) return 2
  if (/^(am|bm)_/.test(name)) return 0
  return 1
}

/** Ask the backend which voices exist. Returns [{name}] so callers can keep
 *  using `.name`, as they did with SpeechSynthesisVoice. */
export async function fetchVoices() {
  try {
    const res = await fetch('/api/aria/voice')
    if (!res.ok) return []
    const data = await res.json()
    cachedVoices = (data.voices || [])
      .filter(n => /^[ab][fm]_/.test(n))
      .sort((a, b) => rank(b) - rank(a) || a.localeCompare(b))
      .map(name => ({ name }))
    return cachedVoices
  } catch {
    return []
  }
}

export function listVoices() { return cachedVoices }

/** Voices arrive over the network — call this once and re-render on fire. */
export function onVoicesReady(cb) {
  let live = true
  fetchVoices().then(vs => { if (live) cb(vs) })
  return () => { live = false }
}

export function getAriaVoice() {
  let saved = null
  try { saved = localStorage.getItem(STORE_KEY) } catch { /* private mode */ }
  if (saved) return { name: saved }
  // Her default, not merely the first name in a sorted list.
  if (cachedVoices.some(v => v.name === DEFAULT_VOICE)) return { name: DEFAULT_VOICE }
  return cachedVoices[0] || { name: DEFAULT_VOICE }
}

export function setAriaVoice(name) {
  try {
    if (name) localStorage.setItem(STORE_KEY, name)
    else localStorage.removeItem(STORE_KEY)
  } catch { /* ignore */ }
}

/** Synthesise one line and return a playable URL, or null. */
export async function synthesise(text, voice) {
  const res = await fetch('/api/aria/speak', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text, voice: voice || getAriaVoice().name }),
  })
  if (!res.ok) {
    let detail = `speak failed (${res.status})`
    try { detail = (await res.json()).detail || detail } catch { /* not json */ }
    throw new Error(detail)
  }
  const data = await res.json()
  if (!data.name) throw new Error('no audio came back')
  return { url: `/api/aria/audio/${encodeURIComponent(data.name)}`, ...data }
}

/** Speak one line now — used by the voice picker to preview a choice. */
export async function preview(voiceName, text = 'Online, and listening.') {
  const { url } = await synthesise(text, voiceName)
  const el = new Audio(url)
  await el.play()
  return el
}

// ── beat shaping ─────────────────────────────────────────────────────────────

function splitWords(text) {
  return (text || '').split(/(\s+)/).filter(w => w.length > 0)
}

function beatForWord(w) {
  const len = (w || '').trim().length
  let amp = 0.5 + Math.min(len, 12) * 0.055
  if (/[.!?]$/.test(w || '')) amp += 0.35
  return Math.min(1.6, amp)
}

/**
 * Pulse the core from the audio's real amplitude.
 * Returns a stop function. Failure here is never fatal — if WebAudio is
 * blocked the reveal timer keeps beating, and she still speaks.
 */
function beatFromAudio(el) {
  let ctx = null
  let raf = 0
  try {
    const Ctx = window.AudioContext || window.webkitAudioContext
    if (!Ctx) return () => {}
    ctx = new Ctx()
    const source = ctx.createMediaElementSource(el)
    const analyser = ctx.createAnalyser()
    analyser.fftSize = 512
    source.connect(analyser)
    // Through the context to the speakers — an element routed into an
    // analyser and not reconnected plays silently.
    analyser.connect(ctx.destination)

    const buf = new Uint8Array(analyser.frequencyBinCount)
    const tick = () => {
      analyser.getByteTimeDomainData(buf)
      let peak = 0
      for (let i = 0; i < buf.length; i++) {
        const v = Math.abs(buf[i] - 128) / 128
        if (v > peak) peak = v
      }
      if (peak > 0.02) pushBeat(Math.min(1.6, 0.35 + peak * 2.2))
      raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
  } catch {
    return () => {}
  }
  return () => {
    cancelAnimationFrame(raf)
    try { ctx?.close() } catch { /* ignore */ }
  }
}

/**
 * Speak a reply (briefing, not a read-out) and beat the core in sync.
 * @param {string} text  full markdown reply
 * @param {(shown:string)=>void} onReveal  progressively revealed FULL text
 * @param {{voice?:boolean, brief?:boolean, onError?:(msg:string)=>void}} opts
 * @returns {{cancel:()=>void}} handle to stop early (barge-in)
 */
export function speakReply(text, onReveal, opts = {}) {
  const brief = opts.brief !== false
  const words = splitWords(text)

  let cancelled = false
  let timer = null
  let revealDone = false
  let speechDone = !opts.voice
  let beatsFromVoice = false
  let audioEl = null
  let stopBeat = () => {}

  const settle = () => {
    if (cancelled || !revealDone || !speechDone) return
    timer = setTimeout(() => { if (!cancelled) setCoreState('idle') }, TAIL_MS)
  }

  // ── screen: reveal the FULL reply on its own timer, independent of speech ──
  let i = 0
  function runReveal() {
    if (cancelled) return
    if (i >= words.length) { onReveal?.(text); revealDone = true; settle(); return }
    i += 1
    onReveal?.(words.slice(0, i).join(''))
    const tok = words[i - 1]
    if (!beatsFromVoice && tok.trim().length) pushBeat(beatForWord(tok))
    timer = setTimeout(runReveal, WORD_MS + Math.random() * 25)
  }
  runReveal()

  // ── voice: one synthesis for the whole digest, played as one clip ──
  if (opts.voice) {
    const spoken = brief ? spokenDigest(text) : toPlainSpeech(text).replace(/\n+/g, ' ')
    if (!spoken.trim()) {
      speechDone = true
    } else {
      synthesise(spoken)
        .then(({ url }) => {
          if (cancelled) return
          audioEl = new Audio(url)
          audioEl.preload = 'auto'
          stopBeat = beatFromAudio(audioEl)
          beatsFromVoice = true
          audioEl.onended = () => { speechDone = true; stopBeat(); settle() }
          audioEl.onerror = () => {
            speechDone = true; stopBeat()
            opts.onError?.('the clip could not be played')
            settle()
          }
          return audioEl.play()
        })
        .catch(e => {
          // No silent downgrade to the robotic browser voice. She simply does
          // not speak, and the surface says why.
          speechDone = true
          beatsFromVoice = false
          opts.onError?.(String(e.message || e))
          settle()
        })
    }
  }

  return {
    cancel: () => {
      cancelled = true
      clearTimeout(timer)
      stopBeat()
      if (audioEl) { try { audioEl.pause(); audioEl.src = '' } catch { /* ignore */ } }
    },
  }
}

/** Signal that the user just sent a message — core goes into "thinking". */
export function startThinking() { setCoreState('thinking') }
