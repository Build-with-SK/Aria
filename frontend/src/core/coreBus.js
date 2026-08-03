// ─── coreBus — the decoupled controller for the Living Core ───────────────────
// A tiny module-level store any page can drive; the <LivingCore> canvas reads it
// every animation frame WITHOUT going through React (no re-renders on each beat).
//
//   coreState : 'idle' | 'thinking' | 'speaking'
//   intensity : 0..1  — sustained energy (drives glow + swirl while speaking)
//   beat      : transient impulse — the canvas consumes & decays it each frame
//
// Drive it from a chat send/stream handler:
//   setCoreState('thinking')        // user hit send — cloud tightens, core dims
//   pushBeat(0.8)                   // each streamed chunk / spoken word — a beat
//   setIntensity(rms)               // optional: drive from TTS AnalyserNode RMS
//   setCoreState('idle')            // reply finished — ease back to resting

const bus = {
  state: 'idle',
  intensity: 0,   // 0..1 sustained
  beat: 0,        // additive impulse, consumed by the render loop
  // live "flavour" data (optional) — real vault/memory counts for the HUD
  meta: { notes: null, memories: null },
}

const listeners = new Set()
function emit() { for (const fn of listeners) { try { fn(bus) } catch { /* ignore */ } } }

/** Subscribe to state changes (for React HUD text, not for the canvas). */
export function subscribeCore(fn) { listeners.add(fn); return () => listeners.delete(fn) }

/** Set the coarse mode. Going idle also drains sustained intensity. */
export function setCoreState(next) {
  if (bus.state === next) return
  bus.state = next
  if (next === 'idle') bus.intensity = 0
  if (next === 'thinking') bus.intensity = 0
  emit()
}

/** Sustained energy 0..1 (e.g. from a voice AnalyserNode RMS). */
export function setIntensity(v) {
  bus.intensity = v < 0 ? 0 : v > 1 ? 1 : v
}

/** A single beat — sharp bloom + outward particle push. amp scales the punch. */
export function pushBeat(amp = 1) {
  if (bus.state !== 'speaking') { bus.state = 'speaking'; emit() }
  bus.beat = Math.min(3, bus.beat + amp)
  // keep a floor of sustained glow through the reply so it stays lit between beats
  bus.intensity = Math.max(bus.intensity, 0.45)
}

/** Attach real data for the HUD / warmer-recent-particle flavour. */
export function setCoreMeta(meta) { bus.meta = { ...bus.meta, ...meta }; emit() }

/** The live object the canvas reads each frame. Do not reassign — mutate via setters. */
export function getCore() { return bus }
