import React, { useEffect, useRef, useState } from 'react'
import axios from 'axios'
import { getCore, subscribeCore } from '../core/coreBus'

// ─── LivingCore — ARIA's known universe as a MILKY WAY ─────────────────────────
// Every listed symbol ARIA knows (~20k: full US, NSE, BSE, LSE, Europe, China,
// HK, Japan, …) is a star in a spiral galaxy you can orbit (drag) and zoom
// (scroll):
//   structure = 4 logarithmic spiral arms + a warm galactic bulge + a faint halo
//   colour    = market/region (US, India, Europe, China, HK, Japan, …)
//   position  = each region owns one arm segment, so regions stay findable
//   dust lanes= dark bands hugging the inner edge of every arm
//   haze      = soft nebula clouds along the arms — the "milky" in Milky Way
//   size/glow = brighter & bigger if ARIA has a live signal on that ticker
// Hover a star → tooltip; click → pinned card. Still talk-reactive: the galactic
// core beats while ARIA speaks. Pure Canvas 2D, additive, no libs, auto-throttled.

const TAU = Math.PI * 2

function makeSprite(size, stops) {
  const c = document.createElement('canvas'); c.width = c.height = size
  const g = c.getContext('2d')
  const grd = g.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2)
  for (const [o, col] of stops) grd.addColorStop(o, col)
  g.fillStyle = grd; g.fillRect(0, 0, size, size); return c
}
function dot(size, r, g, b) {
  return makeSprite(size, [[0, `rgba(${r},${g},${b},0.98)`], [0.3, `rgba(${r},${g},${b},0.5)`],
    [0.65, `rgba(${r},${g},${b},0.14)`], [1, `rgba(${r},${g},${b},0)`]])
}
function cloud(size, r, g, b) {
  return makeSprite(size, [[0, `rgba(${r},${g},${b},0.30)`], [0.35, `rgba(${r},${g},${b},0.13)`],
    [0.7, `rgba(${r},${g},${b},0.035)`], [1, `rgba(${r},${g},${b},0)`]])
}
function hash(s) { let h = 2166136261; for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619) } return h >>> 0 }

// deterministic per-symbol randomness — same ticker lands in the same place always
function rnd(h, k) {
  let x = Math.imul(h ^ Math.imul(k + 1, 0x9e3779b1), 0x85ebca6b)
  x ^= x >>> 13; x = Math.imul(x, 0xc2b2ae35); x ^= x >>> 16
  return (x >>> 0) / 4294967296
}
function gauss(h, k) { return (rnd(h, k) + rnd(h, k + 31) + rnd(h, k + 67)) / 1.5 - 1 }  // ≈ -1..1, bell
function mulberry32(a) {
  return function () {
    a |= 0; a = (a + 0x6D2B79F5) | 0
    let t = Math.imul(a ^ (a >>> 15), 1 | a)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

// ── regions: label · colour · which exchanges map here ──
const REGIONS = [
  { key: 'US',        label: 'United States', rgb: [56, 200, 255],  ex: ['NASDAQ', 'NYSE', 'AMEX', 'ARCA', 'BATS', 'IEX', 'US'] },
  { key: 'India',     label: 'India',         rgb: [255, 158, 44],  ex: ['NSE', 'BSE'] },
  { key: 'UK',        label: 'United Kingdom', rgb: [255, 77, 148], ex: ['LSE'] },
  { key: 'Europe',    label: 'Europe',        rgb: [43, 227, 139],  ex: ['XETRA', 'EURONEXT_PA', 'EURONEXT_AS', 'BORSA_IT', 'BME', 'SIX', 'OMX_STO', 'EURONEXT_BR', 'EURONEXT_LS', 'OMX_HEL', 'OSLO', 'OMX_CPH', 'WIENER_BORSE', 'EURONEXT_DUB', 'ATHEX'] },
  { key: 'China',     label: 'China',         rgb: [255, 59, 71],   ex: ['SSE', 'SZSE'] },
  { key: 'HongKong',  label: 'Hong Kong',     rgb: [255, 210, 59],  ex: ['HKEX'] },
  { key: 'Japan',     label: 'Japan',         rgb: [179, 102, 255], ex: ['TSE'] },
  { key: 'Canada',    label: 'Canada',        rgb: [43, 227, 192],  ex: ['TSX'] },
  { key: 'Australia', label: 'Australia',     rgb: [255, 119, 200], ex: ['ASX'] },
  { key: 'Crypto',    label: 'Crypto',        rgb: [255, 204, 0],   ex: ['CRYPTO'] },
  { key: 'Macro',     label: 'FX / Futures / Index', rgb: [143, 160, 176], ex: ['FX', 'FUT', 'INDEX'] },
]
const EXCH_REGION = {}
REGIONS.forEach((r, i) => r.ex.forEach(e => { EXCH_REGION[e] = i }))
function regionIdx(exch) { return EXCH_REGION[exch] ?? REGIONS.length - 1 }

// ── galaxy shape ──
const ARMS = 4                                   // the Milky Way's four major arms
const WIND = 2.35                                // how tightly the arms coil
const R_IN = 0.40, R_OUT = 2.95                  // disc extent
function armOf(ri) { return ri % ARMS }

// Regions sharing an arm are laid end to end along it, each getting a radial
// stretch weighted by how many symbols it holds — so the US sweeps most of its
// arm while Crypto is a short bright segment, instead of every market being
// squeezed into an identical stub.
function radialSpans(counts) {
  const spans = new Array(REGIONS.length)
  for (let a = 0; a < ARMS; a++) {
    const mem = REGIONS.map((_, i) => i).filter(i => armOf(i) === a)
    const wts = mem.map(i => Math.sqrt(Math.max(1, counts[i] || 0)))
    const tot = wts.reduce((x, y) => x + y, 0) || 1
    let cursor = R_IN
    mem.forEach((i, k) => {
      const w = (R_OUT - R_IN) * (wts[k] / tot)
      spans[i] = [cursor, cursor + w]
      cursor += w
    })
  }
  return spans
}

export default function LivingCore({ height = 640 }) {
  const wrapRef = useRef(null)
  const canvasRef = useRef(null)
  const [hud, setHud] = useState({ state: 'idle', meta: getCore().meta })
  const [hover, setHover] = useState(null)
  const [selected, setSelected] = useState(null)
  const [stats, setStats] = useState({ total: 0, signalled: 0 })

  useEffect(() => subscribeCore(b => setHud({ state: b.state, meta: b.meta })), [])

  const cam = useRef({ yaw: 0.5, pitch: 0.92, dist: 5.4 })   // tilted, so the disc reads as a disc
  const mouse = useRef({ x: -1, y: -1, inside: false, downX: 0, downY: 0, dragging: false, moved: false })
  const nodesRef = useRef([])
  const hoverRef = useRef(null)
  const selRef = useRef(null)

  // ── load the whole universe + overlay signals, laid out on the spiral ──
  useEffect(() => {
    let alive = true
    Promise.all([
      axios.get('/api/universe/index', { params: { limit: 60000 } }),
      axios.get('/api/signals').catch(() => ({ data: {} })),
    ]).then(([ui, si]) => {
      if (!alive) return
      const sigs = si.data?.signals || {}
      const sigList = Array.isArray(sigs) ? sigs : Object.values(sigs)
      const sigBy = {}
      sigList.forEach(s => { if (s.ticker) sigBy[s.ticker.toUpperCase()] = s })
      const syms = ui.data?.symbols || []
      let signalled = 0
      const counts = new Array(REGIONS.length).fill(0)
      syms.forEach(s => { counts[regionIdx(s.exchange)]++ })
      const spans = radialSpans(counts)
      const nodes = syms.map(s => {
        const sym = s.symbol
        const ri = regionIdx(s.exchange)
        const h = hash(sym)
        const [lo, hi] = spans[ri]
        const r = lo + Math.pow(rnd(h, 1), 0.85) * (hi - lo)       // denser toward the segment's inner edge
        const off = gauss(h, 2)                                    // where across the arm this star sits
        const theta = armOf(ri) * (TAU / ARMS) + r * WIND + off * 0.13
        const spread = 0.045 + r * 0.05
        // a dark dust lane hugs the inner edge of every arm
        const dust = 1 - 0.55 * Math.exp(-Math.pow((off + 0.45) / 0.30, 2))
        const base = sym.replace(/\.[A-Z]+$/, '').toUpperCase()
        const sig = sigBy[sym.toUpperCase()] || sigBy[base]
        if (sig) signalled++
        const score = sig ? (sig.composite_score || 0) : null
        const conv = sig ? Math.min(1, Math.abs(score) / 55) : 0
        return {
          sym, name: s.name, exch: s.exchange, region: ri,
          score, action: sig?.action, price: sig?.current_price,
          x: Math.cos(theta) * r + gauss(h, 3) * spread,
          y: gauss(h, 5) * (0.050 + 0.11 * Math.exp(-r * 1.2)),    // thin disc, thicker near the hub
          z: Math.sin(theta) * r + gauss(h, 4) * spread,
          dim: sig ? 1 : dust,                                     // signals shine through the dust
          hasSig: !!sig,
          size: 0.010 + (sig ? 0.020 + conv * 0.05 : 0.0035),
        }
      })
      nodesRef.current = nodes
      setStats({ total: nodes.length, signalled })
    }).catch(() => {})
    return () => { alive = false }
  }, [])

  useEffect(() => {
    const canvas = canvasRef.current, wrap = wrapRef.current
    if (!canvas || !wrap) return
    const ctx = canvas.getContext('2d', { alpha: false })
    const bus = getCore()

    const sprites = REGIONS.map(r => dot(48, r.rgb[0], r.rgb[1], r.rgb[2]))
    // galactic centre: white-gold, not a fireball
    const coreSprite = makeSprite(256, [
      [0, 'rgba(255,255,255,1)'], [0.10, 'rgba(255,250,228,0.98)'],
      [0.26, 'rgba(255,216,140,0.80)'], [0.5, 'rgba(255,160,70,0.30)'],
      [0.78, 'rgba(210,80,40,0.08)'], [1, 'rgba(210,80,40,0)'],
    ])
    const bulgeSprite = dot(32, 255, 226, 170)
    const hazeWarm = cloud(128, 255, 214, 150)
    const hazeCool = cloud(128, 130, 175, 255)
    const hazeRose = cloud(128, 255, 118, 168)

    // ── procedural populations: bulge, halo, nebula haze ──
    const rng = mulberry32(0xA71A5)
    const bulge = []
    for (let i = 0; i < 2200; i++) {
      const r = 0.52 * Math.pow(rng(), 0.55)
      const th = rng() * TAU, ph = Math.acos(2 * rng() - 1)
      bulge.push({
        x: Math.sin(ph) * Math.cos(th) * r,
        y: Math.cos(ph) * r * 0.52,
        z: Math.sin(ph) * Math.sin(th) * r,
        s: 0.006 + rng() * 0.010, a: 0.25 + rng() * 0.45,
      })
    }
    const halo = []
    for (let i = 0; i < 900; i++) {
      const r = 3.2 + Math.pow(rng(), 0.7) * 3.6
      const th = rng() * TAU, ph = Math.acos(2 * rng() - 1)
      halo.push({
        x: Math.sin(ph) * Math.cos(th) * r,
        y: Math.cos(ph) * r * 0.8,
        z: Math.sin(ph) * Math.sin(th) * r,
        a: 0.10 + rng() * 0.30,
      })
    }
    const haze = []
    for (let i = 0; i < 720; i++) {
      const arm = i % ARMS
      const r = R_IN + Math.sqrt(rng()) * (R_OUT - R_IN)          // more cloud where there's more arm
      const th = arm * (TAU / ARMS) + r * WIND + (rng() - 0.5) * 0.30
      const rose = rng() < 0.08                                   // sparse star-forming knots
      haze.push({
        x: Math.cos(th) * r + (rng() - 0.5) * 0.16,
        y: (rng() - 0.5) * 0.09,
        z: Math.sin(th) * r + (rng() - 0.5) * 0.16,
        s: 0.08 + Math.pow(rng(), 1.6) * 0.22,                    // small + many reads as cloud, not blobs
        img: rose ? hazeRose : (r < 1.25 ? hazeWarm : hazeCool),
        a: 0.4 + rng() * 0.5,
      })
    }

    let dpr = Math.min(window.devicePixelRatio || 1, 1.5)
    let W = 0, H = 0, cx = 0, cy = 0, scale = 1, vignette = null
    function resize() {
      const rect = wrap.getBoundingClientRect()
      W = Math.max(1, Math.floor(rect.width)); H = Math.max(1, Math.floor(rect.height))
      canvas.width = Math.floor(W * dpr); canvas.height = Math.floor(H * dpr)
      canvas.style.width = W + 'px'; canvas.style.height = H + 'px'
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
      cx = W / 2; cy = H / 2; scale = Math.min(W, H) * 0.3
      const vg = ctx.createRadialGradient(cx, cy, Math.min(W, H) * 0.16, cx, cy, Math.max(W, H) * 0.62)
      vg.addColorStop(0, 'rgba(0,0,0,0)'); vg.addColorStop(0.7, 'rgba(0,0,0,0)'); vg.addColorStop(1, 'rgba(3,1,3,0.9)')
      vignette = vg
    }
    resize()
    const ro = new ResizeObserver(resize); ro.observe(wrap)

    const s = { contract: 1, coreBright: 1, beat: 0, bloom: true }
    const FOV = 3.0
    let last = performance.now(), fpsEMA = 60, lowF = 0, highF = 0
    let raf = 0, running = true
    let maxDraw = 60000       // adaptive cap; throttle lowers it on weak GPUs

    function frame(now) {
      const dt = Math.min(0.05, (now - last) / 1000); last = now
      const fps = 1 / Math.max(dt, 1e-3); fpsEMA += (fps - fpsEMA) * 0.08
      const nodes = nodesRef.current
      if (fpsEMA < 42) { lowF++; highF = 0; if (lowF > 25) { maxDraw = Math.max(3000, (maxDraw * 0.8) | 0); s.bloom = false; lowF = 0 } }
      else if (fpsEMA > 56) { highF++; lowF = 0; if (highF > 120 && maxDraw < nodes.length) { maxDraw = Math.min(60000, (maxDraw * 1.1 | 0) + 500); if (maxDraw > nodes.length * 0.9) s.bloom = true; highF = 0 } }

      const c = cam.current
      if (!mouse.current.dragging && bus.state === 'idle') c.yaw += dt * 0.035   // galactic rotation
      const cosY = Math.cos(c.yaw), sinY = Math.sin(c.yaw)
      const cosP = Math.cos(c.pitch), sinP = Math.sin(c.pitch)
      const camDist = c.dist

      let tgtC = 1, tgtCore = 1
      if (bus.state === 'thinking') { tgtC = 0.85; tgtCore = 0.62 }
      else if (bus.state === 'speaking') { tgtC = 1.04; tgtCore = 1.15 + bus.intensity * 0.5 }
      s.contract += (tgtC - s.contract) * Math.min(1, dt * 6)
      s.coreBright += (tgtCore - s.coreBright) * Math.min(1, dt * 8)
      s.beat *= Math.pow(0.0025, dt)
      if (bus.beat > 0) { s.beat = Math.min(3, s.beat + bus.beat); bus.beat = 0 }
      const breathe = 1 + Math.sin(now / 1000 * (TAU / 3.6)) * 0.05 + bus.intensity * 0.04
      const push = s.contract * (1 + s.beat * 0.05)

      function project(x, y, z) {
        const x1 = x * cosY + z * sinY, z1 = -x * sinY + z * cosY
        const y2 = y * cosP - z1 * sinP, z2 = y * sinP + z1 * cosP
        const zc = z2 + camDist
        if (zc <= 0.1) return null
        const p = FOV / zc
        return { sx: cx + x1 * p * scale, sy: cy - y2 * p * scale, p, zc }
      }

      ctx.globalCompositeOperation = 'source-over'
      ctx.fillStyle = 'rgba(3,1,3,0.42)'; ctx.fillRect(0, 0, W, H)
      ctx.globalCompositeOperation = 'lighter'

      // deep-field halo stars (behind everything, barely there)
      ctx.fillStyle = '#cfe0ff'
      for (let i = 0; i < halo.length; i++) {
        const q = halo[i]
        const pr = project(q.x, q.y, q.z); if (!pr) continue
        ctx.globalAlpha = q.a * 0.5
        ctx.fillRect(pr.sx, pr.sy, 1.1, 1.1)
      }

      // a single faint disc glow binds the arms into one galaxy
      const gc = project(0, 0, 0)
      if (gc) {
        const gp = gc.p * scale
        ctx.globalAlpha = 0.10
        ctx.drawImage(hazeCool, gc.sx - 2.5 * gp, gc.sy - 2.5 * gp, 5.0 * gp, 5.0 * gp)
        ctx.globalAlpha = 0.16
        ctx.drawImage(hazeWarm, gc.sx - 1.3 * gp, gc.sy - 1.3 * gp, 2.6 * gp, 2.6 * gp)
      }

      // nebula haze along the arms — this is what makes it read as "milky"
      const hazeN = s.bloom ? haze.length : (haze.length * 0.45) | 0
      for (let i = 0; i < hazeN; i++) {
        const q = haze[i]
        const pr = project(q.x * push, q.y * push, q.z * push); if (!pr) continue
        const d = q.s * pr.p * scale * 2.2
        ctx.globalAlpha = Math.min(0.5, q.a * (0.32 + bus.intensity * 0.22 + s.beat * 0.05))
        ctx.drawImage(q.img, pr.sx - d / 2, pr.sy - d / 2, d, d)
      }

      // galactic bulge
      const bulgeN = s.bloom ? bulge.length : (bulge.length * 0.5) | 0
      for (let i = 0; i < bulgeN; i++) {
        const q = bulge[i]
        const pr = project(q.x * push, q.y * push, q.z * push); if (!pr) continue
        const d = Math.max(1.2, q.s * pr.p * scale * (1 + s.beat * 0.12))
        ctx.globalAlpha = Math.min(0.9, q.a * (0.7 + s.coreBright * 0.3))
        ctx.drawImage(bulgeSprite, pr.sx - d / 2, pr.sy - d / 2, d, d)
      }

      // the hot core (beats while ARIA speaks)
      const coreScale = breathe * s.coreBright * (1 + s.beat * 0.16)
      const coreBase = scale * 0.60 * (FOV / camDist)
      const layers = s.bloom ? [[1.0, 0.7], [0.5, 0.8], [0.24, 1.0]] : [[0.8, 0.85]]
      const cc = project(0, 0, 0)
      if (cc) for (const [sz, al] of layers) { const d = coreBase * sz * coreScale; ctx.globalAlpha = Math.min(1, al * (0.5 + s.coreBright * 0.4)); ctx.drawImage(coreSprite, cc.sx - d / 2, cc.sy - d / 2, d, d) }

      // the symbols themselves
      const mx = mouse.current.x, my = mouse.current.y, picking = mouse.current.inside
      let best = null, bestD = 15 * 15
      const invP = FOV / camDist
      const n = Math.min(nodes.length, maxDraw)
      for (let i = 0; i < n; i++) {
        const nd = nodes[i]
        const pr = project(nd.x * push, nd.y * push, nd.z * push); if (!pr) continue
        nd._sx = pr.sx; nd._sy = pr.sy; nd._zc = pr.zc
        const depth = Math.min(1, pr.p / invP)
        const sz = Math.max(nd.hasSig ? 3 : 1.25, nd.size * pr.p * scale * (1 + s.beat * 0.1))
        let a = nd.hasSig ? (0.42 + depth * 0.5) : (0.20 + depth * 0.34) * nd.dim
        a *= (0.85 + s.beat * 0.1 + bus.intensity * 0.15)
        ctx.globalAlpha = Math.min(0.95, a)
        ctx.drawImage(sprites[nd.region], pr.sx - sz / 2, pr.sy - sz / 2, sz, sz)
        if (picking) {
          const dd = (pr.sx - mx) ** 2 + (pr.sy - my) ** 2
          if (dd < bestD && pr.zc < camDist + 2.4) { bestD = dd; best = nd }
        }
      }

      // hover / select markers
      ctx.globalCompositeOperation = 'source-over'
      const mark = (nd, col, big) => {
        if (!nd || nd._sx == null) return
        ctx.beginPath(); ctx.arc(nd._sx, nd._sy, big ? 12 : 9, 0, TAU)
        ctx.strokeStyle = col; ctx.lineWidth = 1.5; ctx.globalAlpha = 0.95; ctx.stroke()
        ctx.globalAlpha = 1; ctx.font = '700 11px var(--mono, monospace)'; ctx.fillStyle = col
        ctx.fillText(nd.sym, nd._sx + 14, nd._sy + 3)
      }
      const sel = selRef.current
      if (sel) mark(sel, '#ffb324', true)
      if (best && best !== sel) mark(best, '#fff', false)

      if (best !== hoverRef.current) {
        hoverRef.current = best
        setHover(best ? { sym: best.sym, name: best.name, exch: best.exch, region: best.region, score: best.score, action: best.action, price: best.price, x: best._sx, y: best._sy } : null)
      }
      canvas.style.cursor = best ? 'pointer' : (mouse.current.dragging ? 'grabbing' : 'grab')

      ctx.globalCompositeOperation = 'source-over'; ctx.globalAlpha = 1
      ctx.fillStyle = vignette; ctx.fillRect(0, 0, W, H)
      if (running) raf = requestAnimationFrame(frame)
    }
    raf = requestAnimationFrame(frame)

    function pos(e) { const r = canvas.getBoundingClientRect(); return { x: e.clientX - r.left, y: e.clientY - r.top } }
    function onDown(e) { const p = pos(e), m = mouse.current; m.dragging = true; m.moved = false; m.downX = p.x; m.downY = p.y; m._yaw = cam.current.yaw; m._pitch = cam.current.pitch; canvas.setPointerCapture?.(e.pointerId) }
    function onMove(e) {
      const p = pos(e), m = mouse.current; m.x = p.x; m.y = p.y; m.inside = true
      if (m.dragging) {
        const dx = p.x - m.downX, dy = p.y - m.downY
        if (Math.abs(dx) + Math.abs(dy) > 4) m.moved = true
        cam.current.yaw = m._yaw + dx * 0.006
        cam.current.pitch = Math.max(-1.45, Math.min(1.45, m._pitch + dy * 0.006))
      }
    }
    function onUp() { const m = mouse.current; if (m.dragging && !m.moved) { selRef.current = hoverRef.current; setSelected(hoverRef.current ? { ...hoverRef.current } : null) } m.dragging = false }
    function onLeave() { mouse.current.inside = false; mouse.current.x = -1; mouse.current.y = -1 }
    function onWheel(e) { e.preventDefault(); cam.current.dist = Math.max(2.0, Math.min(12, cam.current.dist * (1 + e.deltaY * 0.0012))) }
    canvas.addEventListener('pointerdown', onDown); canvas.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp); canvas.addEventListener('pointerleave', onLeave)
    canvas.addEventListener('wheel', onWheel, { passive: false })
    function onVis() { if (document.hidden) { running = false; cancelAnimationFrame(raf) } else if (!running) { running = true; last = performance.now(); raf = requestAnimationFrame(frame) } }
    document.addEventListener('visibilitychange', onVis)

    return () => {
      running = false; cancelAnimationFrame(raf); ro.disconnect()
      canvas.removeEventListener('pointerdown', onDown); canvas.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp); canvas.removeEventListener('pointerleave', onLeave)
      canvas.removeEventListener('wheel', onWheel); document.removeEventListener('visibilitychange', onVis)
    }
  }, [])

  const mono = { fontFamily: 'var(--mono)' }
  const rgbCss = i => `rgb(${REGIONS[i].rgb.join(',')})`
  const stateColor = hud.state === 'speaking' ? 'var(--orange)' : hud.state === 'thinking' ? 'var(--blue)' : 'var(--muted)'
  const stateLabel = hud.state === 'speaking' ? 'SPEAKING' : hud.state === 'thinking' ? 'THINKING' : 'RESTING'

  return (
    <div ref={wrapRef} style={{ position: 'relative', width: '100%', height, background: '#030103', border: '1px solid var(--border)', borderRadius: 8, overflow: 'hidden', touchAction: 'none' }}>
      <canvas ref={canvasRef} style={{ display: 'block', width: '100%', height: '100%' }} />

      <div style={{ position: 'absolute', top: 12, left: 14, pointerEvents: 'none' }}>
        <div style={{ ...mono, fontSize: 11, fontWeight: 800, color: 'var(--orange)', letterSpacing: 2, textShadow: '0 0 10px rgba(255,36,71,.5)' }}>◈ LIVING BRAIN · MILKY WAY</div>
        <div style={{ ...mono, fontSize: 9, color: 'var(--text-dim)', letterSpacing: 1, marginTop: 3 }}>
          {stats.total.toLocaleString()} symbols · {stats.signalled} with live signals · drag to orbit · scroll to zoom
        </div>
      </div>

      <div style={{ position: 'absolute', top: 12, right: 14, pointerEvents: 'none', display: 'flex', alignItems: 'center', gap: 6 }}>
        <span style={{ width: 7, height: 7, borderRadius: '50%', background: stateColor, boxShadow: `0 0 8px ${stateColor}`, animation: hud.state === 'speaking' ? 'pulseGlow 0.6s ease-in-out infinite' : 'none' }} />
        <span style={{ ...mono, fontSize: 10, fontWeight: 800, letterSpacing: 1, color: stateColor }}>{stateLabel}</span>
      </div>

      {/* region legend — bottom-right */}
      <div style={{ position: 'absolute', bottom: 12, right: 14, pointerEvents: 'none', ...mono, fontSize: 8.5, lineHeight: 1.6, textAlign: 'right' }}>
        {REGIONS.slice(0, 9).map((r, i) => (
          <span key={r.key} style={{ marginLeft: 8, whiteSpace: 'nowrap' }}>
            <span style={{ color: rgbCss(i) }}>●</span> <span style={{ color: 'var(--text-dim)' }}>{r.label}</span>
          </span>
        ))}
        <div style={{ color: 'var(--muted)', marginTop: 2 }}>each market owns an arm · bright + large = live ARIA signal</div>
      </div>

      {hover && !selected && (
        <div style={{ position: 'absolute', left: hover.x + 18, top: hover.y - 10, pointerEvents: 'none', ...mono, fontSize: 10, background: 'rgba(6,3,6,0.92)', border: '1px solid var(--border-2)', borderRadius: 4, padding: '6px 9px', maxWidth: 240 }}>
          <span style={{ color: rgbCss(hover.region), fontWeight: 800 }}>{hover.sym}</span>
          <span style={{ color: 'var(--text-dim)', marginLeft: 6 }}>{hover.exch}</span>
          <div style={{ color: 'var(--muted)', marginTop: 3 }}>{hover.name}</div>
          {hover.score != null && <div style={{ color: hover.score > 8 ? 'var(--green)' : hover.score < -8 ? 'var(--red)' : 'var(--blue)', marginTop: 3 }}>{hover.action} · score {hover.score > 0 ? '+' : ''}{hover.score.toFixed(1)}{hover.price ? ` · ${hover.price.toFixed(2)}` : ''}</div>}
        </div>
      )}

      {selected && (
        <div style={{ position: 'absolute', bottom: 44, right: 14, ...mono, fontSize: 10, background: 'rgba(6,3,6,0.95)', border: '1px solid var(--border-2)', borderRadius: 6, padding: '10px 12px', minWidth: 210, boxShadow: 'var(--glow-soft)' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <span style={{ color: rgbCss(selected.region), fontWeight: 800, fontSize: 13, letterSpacing: 1 }}>{selected.sym}</span>
            <span onClick={() => { selRef.current = null; setSelected(null) }} style={{ cursor: 'pointer', color: 'var(--muted)', fontSize: 12 }}>✕</span>
          </div>
          <div style={{ color: 'var(--text-dim)', marginBottom: 6 }}>{selected.name}</div>
          {[['Exchange', selected.exch], ['Region', REGIONS[selected.region].label],
            ...(selected.score != null ? [['Action', selected.action], ['Score', `${selected.score > 0 ? '+' : ''}${selected.score.toFixed(1)}`], ['Price', selected.price?.toFixed(2)]] : [['Signal', 'none — index only']])].map(([k, v]) => (
            <div key={k} style={{ display: 'flex', justifyContent: 'space-between', gap: 14, lineHeight: 1.7 }}>
              <span style={{ color: 'var(--muted)' }}>{k}</span>
              <span style={{ color: '#cbb' }}>{v ?? '—'}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
