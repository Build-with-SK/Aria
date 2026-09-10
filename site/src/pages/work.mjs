import { PROJECTS, FACTS } from '../content.mjs'
import { section, head, btn, metric, projectCard, ctaBand, relationDiagram } from '../components.mjs'

const intro = `<section class="section">
  <div class="container">
    ${head({
      eyebrow: 'Case studies',
      title: 'Two systems, and why they are separate.',
      lede:
        'Both are parts of one body of work, but they answer different questions. ' +
        'ARIA produces analysis. SENTINEL exists to attack it. A reviewer that ' +
        'shares a process, a memory and an incentive with the thing it reviews is ' +
        'not a reviewer.',
      level: 'h1',
    })}
    <div class="split" style="margin-top:var(--s7)">
      <div class="prose">
        <p>
          The separation is architectural, not presentational. ARIA cannot import
          SENTINEL, cannot read its database and does not depend on it. The entire
          coupling is one authenticated HTTP call with a timeout, and if that call
          fails, ARIA proceeds on its own analysis and records that no second
          opinion was obtained.
        </p>
        <p>
          <strong>An unavailable consultant is never treated as agreement.</strong>
          That single rule is why the two are worth building as two.
        </p>
      </div>
      ${relationDiagram()}
    </div>
  </div>
</section>`

const cards = section(
  `<div style="display:grid;gap:var(--s4)">
    ${PROJECTS.map((p, i) => projectCard(p, { featured: i === 0, level: 'h2' })).join('')}
  </div>`
)

const scale = section(
  `${head({
    eyebrow: 'Scale',
    title: 'What one person built.',
    lede:
      'Every figure below was counted from the repository on 27 August 2026, and the ' +
      'file to check it against is printed underneath.',
  })}
  <div class="grid grid--3" style="margin-top:var(--s7)">
    ${[
      FACTS.srcLines,
      FACTS.testLines,
      FACTS.testModules,
      FACTS.endpoints,
      FACTS.modules,
      FACTS.symbols,
      FACTS.strategies,
      FACTS.frontendLines,
      FACTS.exchanges,
    ]
      .map((f) => `<div class="reveal">${metric(f)}</div>`)
      .join('')}
  </div>
  <p class="dim" style="font-size:0.8125rem;margin-top:var(--s6);max-width:70ch">
    Line counts describe effort, not quality, and they are reported here for scale
    rather than as an achievement. The number on this page that carries weight is
    the test-module count, because it is the one that constrains the others.
  </p>`,
  { id: 'scale' }
)

export default {
  path: '/work/',
  title: 'Case studies — ARIA & SENTINEL | Soundariyan Karunakaran',
  description:
    'Two engineering case studies: ARIA, a financial research and decision-support ' +
    'platform, and SENTINEL, the independent reliability and evaluation architecture ' +
    'that reviews it.',
  body: [intro, cards, scale, section(ctaBand(), { cls: 'section--tight' })].join('\n'),
}
