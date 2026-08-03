// ─── ariaVoice — ARIA's speaking voice + the Living Core's beat ───────────────
// The chat endpoint returns the whole reply at once (no token stream), so we
// re-create a "speaking" cadence: reveal the reply word-by-word on screen and
// fire one coreBus beat per word so the core beats in time.
//
// The VOICE is deliberately not a read-out of the screen:
//   · it speaks a digest (see spokenDigest.js) — no `###`, no `---`, no tables
//   · it picks the most natural FEMALE English voice the browser has, ranked
//     toward the neural/"Natural" engines and an Irish/UK accent (FRIDAY)
//   · it speaks sentence by sentence with a short breath between them, which
//     reads far less robotic than one long utterance (and dodges Chrome's
//     ~15s utterance cut-off)
//
// Beats come from real `boundary` events while speaking; the timed word reveal
// is the fallback when speech is off or blocked.

import { setCoreState, pushBeat } from './coreBus'
import { spokenDigest, toPlainSpeech } from './spokenDigest'

const WORD_MS = 45        // on-screen reveal cadence per word
const TAIL_MS = 900       // ease-to-idle hold after everything finishes
const BREATH_MS = 130     // gap between spoken sentences

const STORE_KEY = 'aria.voiceName'

// ── voice ranking ────────────────────────────────────────────────────────────
// Names that are female across Windows / Chrome / macOS / Android engines.
const FEMALE = /\b(orla|aria|jenny|sonia|libby|clara|natasha|michelle|ava|emily|nova|amber|ashley|cora|elizabeth|jane|nancy|sara|monica|samantha|karen|moira|tessa|fiona|serena|allison|susan|victoria|zira|hazel|eva|catherine|linda|heera|kalpana|neerja|female|woman)\b/i
// Old SAPI5 / eSpeak style engines — technically female but unmistakably robotic.
const ROBOTIC = /(espeak|desktop|zira|hazel|sapi|microsoft (david|mark|george|hazel|zira))/i

function rank(v) {
  const name = v.name || ''
  const lang = (v.lang || '').replace('_', '-')
  if (!/^en/i.test(lang)) return -1000
  let s = 0
  if (FEMALE.test(name)) s += 45
  else if (/\b(david|mark|george|guy|ryan|james|alex|daniel|fred|thomas|william|brian|christopher|eric|liam|oliver)\b/i.test(name)) s -= 60
  if (/natural|neural/i.test(name)) s += 34          // Edge / Windows 11 neural voices
  else if (/online/i.test(name)) s += 24
  if (/^google/i.test(name)) s += 22                 // Chrome's cloud voices
  if (/^microsoft/i.test(name) && !/natural|online/i.test(name)) s -= 12
  if (ROBOTIC.test(name)) s -= 40
  if (/^en-IE/i.test(lang)) s += 20                  // FRIDAY is Irish
  else if (/^en-GB/i.test(lang)) s += 12
  else if (/^en-AU/i.test(lang)) s += 7
  else if (/^en-US/i.test(lang)) s += 5
  if (!v.localService) s += 8                        // cloud voices are the smooth ones
  return s
}

let cachedVoices = []

/** All usable English voices, best-sounding first. */
export function listVoices() {
  const synth = typeof window !== 'undefined' ? window.speechSynthesis : null
  if (!synth) return []
  const all = synth.getVoices() || []
  if (all.length) {
    cachedVoices = all
      .map(v => ({ v, s: rank(v) }))
      .filter(x => x.s > -500)
      .sort((a, b) => b.s - a.s)
      .map(x => x.v)
  }
  return cachedVoices
}

/** Voices load asynchronously in Chrome — call this once and re-render on fire. */
export function onVoicesReady(cb) {
  const synth = typeof window !== 'undefined' ? window.speechSynthesis : null
  if (!synth) return () => {}
  const fire = () => { listVoices(); cb(cachedVoices) }
  fire()
  synth.addEventListener?.('voiceschanged', fire)
  return () => synth.removeEventListener?.('voiceschanged', fire)
}

/** The voice ARIA speaks with: the user's pick, else the best-ranked one. */
export function getAriaVoice() {
  const voices = listVoices()
  if (!voices.length) return null
  let saved = null
  try { saved = localStorage.getItem(STORE_KEY) } catch { /* private mode */ }
  return (saved && voices.find(v => v.name === saved)) || voices[0]
}

export function setAriaVoice(name) {
  try { name ? localStorage.setItem(STORE_KEY, name) : localStorage.removeItem(STORE_KEY) } catch { /* ignore */ }
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

/** Split spoken text into sentence-sized utterances for a natural cadence. */
function sentences(text) {
  return String(text || '')
    .split(/(?<=[.!?])\s+/)
    .map(s => s.trim())
    .filter(Boolean)
}

/**
 * Speak a reply (briefing, not a read-out) and beat the core in sync.
 * @param {string} text  full markdown reply
 * @param {(shown:string)=>void} onReveal  progressively revealed FULL text (screen)
 * @param {{voice?:boolean, brief?:boolean}} opts
 * @returns {{cancel:()=>void}} handle to stop early (e.g. a new message is sent)
 */
export function speakReply(text, onReveal, opts = {}) {
  const brief = opts.brief !== false
  const words = splitWords(text)
  const synth = typeof window !== 'undefined' ? window.speechSynthesis : null
  const canSpeak = !!opts.voice && !!synth && typeof SpeechSynthesisUtterance !== 'undefined'

  let cancelled = false
  let timer = null
  let keepAlive = null
  let revealDone = false
  let speechDone = !canSpeak
  let beatsFromVoice = false      // once speech starts, it owns the beat

  const settle = () => {
    if (cancelled || !revealDone || !speechDone) return
    clearInterval(keepAlive)
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

  // ── voice: speak only what matters, sentence by sentence ──
  if (canSpeak) {
    const spoken = brief ? spokenDigest(text) : toPlainSpeech(text).replace(/\n+/g, ' ')
    const parts = sentences(spoken)
    if (!parts.length) {
      speechDone = true
    } else {
      const voice = getAriaVoice()
      let idx = 0

      const speakNext = () => {
        if (cancelled) return
        if (idx >= parts.length) { speechDone = true; settle(); return }
        const u = new SpeechSynthesisUtterance(parts[idx++])
        if (voice) { u.voice = voice; u.lang = voice.lang }
        u.rate = 0.97      // unhurried — the rushed default is what sounds synthetic
        u.pitch = 1.05     // a touch of warmth without going shrill
        u.volume = 1
        u.onboundary = e => {
          if (cancelled || e.name === 'sentence') return
          beatsFromVoice = true
          const s = e.target?.text || ''
          const next = s.indexOf(' ', e.charIndex)
          pushBeat(beatForWord(s.slice(e.charIndex, next === -1 ? undefined : next)))
        }
        u.onend = () => { if (!cancelled) timer = setTimeout(speakNext, BREATH_MS) }
        u.onerror = () => { if (!cancelled) { speechDone = true; settle() } }   // blocked → reveal still runs
        try { synth.speak(u) } catch { speechDone = true; settle() }
      }

      try { synth.cancel() } catch { /* ignore */ }
      speakNext()
      // Chrome silently pauses long queues; nudge it while we're talking
      keepAlive = setInterval(() => { try { if (synth.speaking) { synth.pause(); synth.resume() } } catch { /* ignore */ } }, 9000)
    }
  }

  return {
    cancel: () => {
      cancelled = true
      clearTimeout(timer); clearInterval(keepAlive)
      try { synth?.cancel() } catch { /* ignore */ }
    },
  }
}

/** Signal that the user just sent a message — core goes into "thinking". */
export function startThinking() { setCoreState('thinking') }
