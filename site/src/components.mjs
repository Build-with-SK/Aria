/**
 * site/src/components.mjs
 * ======================
 * HTML builders. Every one returns a string; nothing here runs in the browser.
 */

import { esc, ICONS } from './layout.mjs'
import { PIPELINE } from './content.mjs'

/* ── Primitives ───────────────────────────────────────────────────────────── */

export const section = (inner, { id = '', cls = '', container = 'container' } = {}) =>
  `<section class="section ${cls}"${id ? ` id="${id}"` : ''}>
    <div class="${container}">${inner}</div>
  </section>`

export const head = ({ eyebrow, title, lede, level = 'h2' }) => `
  ${eyebrow ? `<p class="eyebrow reveal">${esc(eyebrow)}</p>` : ''}
  <${level} class="reveal" style="margin-top:var(--s4)">${title}</${level}>
  ${lede ? `<p class="lede reveal" style="margin-top:var(--s4)">${lede}</p>` : ''}`

export const btn = (href, label, { primary = false, external = false } = {}) =>
  `<a class="btn${primary ? ' btn--primary' : ' btn--ghost'}" href="${esc(href)}"${
    external ? ' target="_blank" rel="noopener"' : ''
  }>${esc(label)} ${ICONS.arrow}</a>`

/** A measured number with the file a reader can open to check it. */
export const metric = (f, { showSrc = true } = {}) => `
  <div class="metric">
    <div class="metric__value">${esc(f.value)}</div>
    <div class="metric__label">${esc(f.label)}</div>
    ${showSrc && f.src ? `<div class="metric__src">${esc(f.src)}</div>` : ''}
  </div>`

export const tags = (list, accentFirst = false) =>
  `<div class="tags">${list
    .map((t, i) => `<span class="tag${accentFirst && i === 0 ? ' tag--accent' : ''}">${esc(t)}</span>`)
    .join('')}</div>`

export const callout = (title, ...paras) => `
  <div class="callout reveal">
    <p class="callout__title">${esc(title)}</p>
    ${paras.map((p) => `<p>${p}</p>`).join('')}
  </div>`

export const steps = (items) => `
  <div class="steps">
    ${items
      .map(
        (s, i) => `<div class="step reveal">
        <div class="step__n">${String(i + 1).padStart(2, '0')}</div>
        <div>
          <h3 class="step__title">${esc(s.title)}</h3>
          <div class="step__body">${s.body}</div>
        </div>
      </div>`
      )
      .join('')}
  </div>`

export const table = (cols, rows) => `
  <div class="table-wrap reveal">
    <table>
      <thead><tr>${cols.map((c) => `<th scope="col">${esc(c)}</th>`).join('')}</tr></thead>
      <tbody>${rows
        .map((r) => `<tr>${r.map((c) => `<td>${c}</td>`).join('')}</tr>`)
        .join('')}</tbody>
    </table>
  </div>`

/* ── Code, highlighted at build time ──────────────────────────────────────── */

const PY_TOKENS =
  /(&quot;[^&]*?&quot;|&#39;[^&]*?&#39;|'[^']*?')|(\b(?:def|class|return|if|elif|else|for|while|import|from|try|except|raise|not|in|is|and|or|None|True|False|async|await|with|as|lambda|yield|assert)\b)/g

export function code(src, { lang = 'python', caption = '' } = {}) {
  const html = src
    .trim()
    .split('\n')
    .map((line) => {
      const e = esc(line)
      const hash = e.indexOf('#')
      // A '#' only opens a comment if no quote is still open before it.
      const before = hash >= 0 ? e.slice(0, hash) : e
      const quoted = (before.match(/&quot;|&#39;|'/g) || []).length % 2 === 1
      const cut = hash >= 0 && !quoted ? hash : -1
      const codePart = cut >= 0 ? e.slice(0, cut) : e
      const comment = cut >= 0 ? e.slice(cut) : ''
      const painted = codePart.replace(PY_TOKENS, (m, str, kw) =>
        str ? `<span class="c-str">${str}</span>` : `<span class="c-key">${kw}</span>`
      )
      return painted + (comment ? `<span class="c-com">${comment}</span>` : '')
    })
    .join('\n')

  return `<div class="reveal">
    <pre class="code"><code data-lang="${esc(lang)}">${html}</code></pre>
    ${caption ? `<p class="dim mono" style="font-size:0.75rem;margin-top:var(--s2);letter-spacing:0.04em">${esc(caption)}</p>` : ''}
  </div>`
}

/* ── The hero pipeline ────────────────────────────────────────────────────── */
/*
 * Six stages down a spine, with one pulse travelling it. Built as static SVG:
 * the geometry is computed here at build time, the motion is two CSS keyframes,
 * and under prefers-reduced-motion it settles into a legible static diagram.
 */

export function pipeline() {
  // Deliberately narrow. The SVG scales to its container, so a smaller viewBox
  // means every label lands larger in real pixels on the same screen.
  const W = 400
  const NODE_H = 56
  const STEP = 72
  const TOP = 10
  const SPINE_X = 24
  const NODE_X = 50
  const H = TOP + PIPELINE.length * STEP - (STEP - NODE_H) + 10

  const centre = (i) => TOP + i * STEP + NODE_H / 2
  const spineTop = centre(0)
  const spineBottom = centre(PIPELINE.length - 1)

  const nodes = PIPELINE.map((p, i) => {
    const y = TOP + i * STEP
    const cy = centre(i)
    const delay = (i * 0.95).toFixed(2)
    return `<g class="pipe-node">
      <path class="pipe-spine" d="M${SPINE_X} ${cy}H${NODE_X}"/>
      <rect x="${NODE_X}" y="${y}" width="${W - NODE_X}" height="${NODE_H}" rx="4"/>
      <rect class="pipe-glint" x="${NODE_X}" y="${y}" width="${W - NODE_X}" height="${NODE_H}" rx="4"
            style="animation-delay:${delay}s"/>
      <text class="pipe-index" x="${NODE_X + 13}" y="${y + 23}">${String(i + 1).padStart(2, '0')}</text>
      <text class="pipe-label" x="${NODE_X + 44}" y="${y + 24}">${esc(p.label)}</text>
      <text class="pipe-note" x="${NODE_X + 44}" y="${y + 41}">${esc(p.note.toUpperCase())}</text>
    </g>`
  }).join('\n    ')

  return `<div class="pipeline reveal">
    <svg viewBox="0 0 ${W} ${H}" role="img"
         aria-label="System pipeline: ${PIPELINE.map((p) => p.label).join(', then ')}.">
      <path class="pipe-spine" d="M${SPINE_X} ${spineTop}V${spineBottom}"/>
      <path class="pipe-pulse" d="M${SPINE_X} ${spineTop}V${spineBottom}"/>
      <circle cx="${SPINE_X}" cy="${spineTop}" r="2.5" fill="var(--accent)"/>
      <circle cx="${SPINE_X}" cy="${spineBottom}" r="2.5" fill="var(--accent)"/>
      ${nodes}
    </svg>
  </div>`
}

/* ── ARIA / SENTINEL relationship ─────────────────────────────────────────── */

export function relationDiagram() {
  return `<div class="relation reveal">
    <svg viewBox="0 0 400 268" role="img"
         aria-label="ARIA, the user-facing intelligence system, consults SENTINEL, a separate reliability and evaluation architecture running in its own process, over one authenticated call that fails soft.">
      <rect class="rel-box rel-box--accent" x="0" y="8" width="400" height="74" rx="5"/>
      <text class="rel-title" x="200" y="42" text-anchor="middle">ARIA</text>
      <text class="rel-sub" x="200" y="62" text-anchor="middle">USER-FACING INTELLIGENCE &amp; RESEARCH</text>

      <path class="rel-line rel-line--dashed" d="M178 82V186"/>
      <path class="rel-line rel-line--dashed" d="M222 186V82"/>
      <path d="M178 186l-4-7h8z" fill="var(--accent)" opacity=".75"/>
      <path d="M222 82l-4 7h8z" fill="var(--accent)" opacity=".75"/>

      <rect x="34" y="110" width="332" height="48" rx="4" fill="var(--bg-1)" stroke="var(--line)"/>
      <text class="rel-edge-label" x="200" y="129" text-anchor="middle">ONE AUTHENTICATED CALL</text>
      <text class="rel-edge-label" x="200" y="146" text-anchor="middle">TIMEOUT · FAILS SOFT · NO SHARED STATE</text>

      <rect class="rel-box" x="0" y="186" width="400" height="74" rx="5"/>
      <text class="rel-title" x="200" y="220" text-anchor="middle">SENTINEL</text>
      <text class="rel-sub" x="200" y="240" text-anchor="middle">RELIABILITY · EVALUATION · ADVERSARIAL REVIEW</text>

    </svg>
  </div>`
}

/* ── Project card ─────────────────────────────────────────────────────────── */

export function projectCard(p, { featured = false, level = 'h3' } = {}) {
  return `<a class="card card--link reveal" href="${p.href}"
     style="${featured ? 'padding:clamp(1.5rem,4vw,2.5rem)' : ''}">
    <div style="display:flex;align-items:center;justify-content:space-between;gap:var(--s4);flex-wrap:wrap">
      <span class="eyebrow">${esc(p.kicker)}</span>
      <span class="status"><span class="status__dot"></span>${esc(p.status)}</span>
    </div>
    <${level} style="font-size:${featured ? 'var(--t-h1)' : 'var(--t-h2)'};margin-top:var(--s4);letter-spacing:-0.03em">${esc(p.name)}</${level}>
    <p class="mono" style="font-size:var(--t-label);letter-spacing:0.1em;text-transform:uppercase;color:var(--text-3);margin-top:var(--s2)">${esc(p.expansion)}</p>
    <p class="muted" style="margin-top:var(--s4);max-width:64ch;font-size:var(--t-sm)">${esc(p.summary)}</p>
    ${
      p.metrics.length
        ? `<div class="grid grid--4" style="margin-top:var(--s5)">${p.metrics
            .map((m) => metric(m, { showSrc: false }))
            .join('')}</div>`
        : ''
    }
    <div style="margin-top:var(--s5)">${tags(p.stack)}</div>
    <span class="btn btn--ghost" style="margin-top:var(--s5);pointer-events:none">Read the case study ${ICONS.arrow}</span>
  </a>`
}

/* ── CTA band ─────────────────────────────────────────────────────────────── */

export const ctaBand = ({
  eyebrow = 'Available for work',
  title = 'Have something that needs building?',
  lede = 'Financial analysis, modelling, Python and data work, AI automation, or a research system built end to end.',
  primary = ['/contact/', 'Start a conversation'],
  secondary = ['/services/', 'See what I do'],
} = {}) => `
  <div class="cta-band reveal">
    <p class="eyebrow" style="justify-content:center">${esc(eyebrow)}</p>
    <h2 style="margin-top:var(--s4)">${esc(title)}</h2>
    <p class="lede" style="margin:var(--s4) auto 0">${esc(lede)}</p>
    <div class="btn-row">
      ${btn(primary[0], primary[1], { primary: true })}
      ${btn(secondary[0], secondary[1])}
    </div>
  </div>`
