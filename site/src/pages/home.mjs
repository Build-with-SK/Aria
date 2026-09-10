import { SITE, HEADLINE_FACTS, PROJECTS, SERVICES } from '../content.mjs'
import {
  section, head, btn, metric, pipeline, projectCard, ctaBand, callout,
} from '../components.mjs'

const aria = PROJECTS.find((p) => p.slug === 'aria')
const sentinel = PROJECTS.find((p) => p.slug === 'sentinel')

/* ── Hero ─────────────────────────────────────────────────────────────────── */

const hero = `<section class="hero">
  <div class="container">
    <div class="hero__grid">
      <div>
        <p class="eyebrow">${SITE.location} · Open to work &amp; freelance</p>
        <h1 class="hero__title">Financial research,<br><em>built as software.</em></h1>
        <p class="lede hero__lede">
          I hold an MSc in International Corporate Finance and write the code that
          does the analysis. Market data pipelines, quantitative research engines,
          evaluation harnesses and the reasoning layers on top — designed, built
          and measured end to end.
        </p>
        <div class="btn-row">
          ${btn('/contact/', 'Work with me', { primary: true })}
          ${btn('/work/', 'Explore the work')}
        </div>
        <div class="hero__facts">
          ${HEADLINE_FACTS.map((f) => metric(f, { showSrc: false })).join('')}
        </div>
        <p class="dim mono" style="font-size:var(--t-label);letter-spacing:0.1em;margin-top:var(--s4)">
          MEASURED FROM ONE REPOSITORY · EVERY FIGURE TRACEABLE TO A FILE
        </p>
      </div>
      <div>
        ${pipeline()}
        <p class="dim" style="font-size:0.8125rem;margin-top:var(--s4);text-align:center">
          The shape of every system I build: data in, evidence out, and a
          validation stage that is allowed to say no.
        </p>
      </div>
    </div>
  </div>
</section>`

/* ── What the combination buys ────────────────────────────────────────────── */

const PILLARS = [
  {
    n: 'Finance',
    body:
      'MSc International Corporate Finance, Distinction. Valuation, modelling, ' +
      'derivatives and risk. I understand the domain before I write a line of ' +
      'code — which is why the models answer the question that was actually asked.',
  },
  {
    n: 'Research',
    body:
      'Backtesting, purged walk-forward validation, calibration and baseline ' +
      'comparison. I build the machinery that decides whether an idea works, and ' +
      'reports honestly when the sample is too small to say.',
  },
  {
    n: 'Engineering',
    body:
      'Python, FastAPI and React, in production shape rather than notebook shape: ' +
      '60 test modules, continuous integration on every push, and failure ' +
      'behaviour that is designed rather than discovered.',
  },
]

const pillars = section(
  `<div class="split split--wide-left">
    <div>
      ${head({
        eyebrow: 'Positioning',
        title: 'Three skills that are common on their own.<br>The overlap is not.',
        lede:
          'Analysts who can specify a system usually cannot build it. Engineers who ' +
          'can build one rarely know which question matters. I do both, which ' +
          'removes the translation layer where most financial software goes wrong.',
      })}
    </div>
    <div class="grid" style="gap:var(--s5)">
      ${PILLARS.map(
        (p) => `<div class="reveal">
          <p class="eyebrow eyebrow--plain">${p.n}</p>
          <p class="muted" style="margin-top:var(--s3);font-size:var(--t-sm)">${p.body}</p>
        </div>`
      ).join('')}
    </div>
  </div>`,
  { id: 'positioning' }
)

/* ── Selected work ────────────────────────────────────────────────────────── */

const work = section(
  `${head({
    eyebrow: 'Selected work',
    title: 'Two systems, deliberately kept apart.',
    lede:
      'ARIA is the intelligence people use. SENTINEL is the independent architecture ' +
      'that checks it. Merging them would have made the second one useless.',
  })}
  <div style="margin-top:var(--s7);display:grid;gap:var(--s4)">
    ${projectCard(aria, { featured: true })}
    ${projectCard(sentinel)}
  </div>
  <div class="btn-row">${btn('/work/', 'All case studies')}</div>`,
  { id: 'work' }
)

/* ── Credibility ──────────────────────────────────────────────────────────── */

const evidence = section(
  `<div class="split">
    <div>
      ${head({
        eyebrow: 'How I work',
        title: 'Evidence, not adjectives.',
        lede:
          'The most useful thing a research system can do is tell you when it does ' +
          'not know. That principle is not marketing here — it is enforced in code.',
      })}
      <div class="btn-row">${btn('/work/aria/', 'See how it is measured')}</div>
    </div>
    <div class="grid" style="gap:var(--s4)">
      ${callout(
        'The claim I lead with',
        'ARIA reports <strong>no demonstrated edge</strong>. It ships the machinery to ' +
          'measure whether it has one — Brier score, skill against naive baselines, ' +
          'per-module tiering — and that machinery currently returns ' +
          '<span class="mono">measurable: false</span>, because not enough predictions ' +
          'have resolved.',
        'Saying so is the point. A system that cannot report its own failure is not a ' +
          'research system; it is a demo. Any client hiring me for analysis is buying ' +
          'that instinct as much as the code.'
      )}
      ${callout(
        'What that looks like in practice',
        'Confidence intervals are Wilson intervals on real observation counts, not ' +
          'stated priors. Baseline comparisons use McNemar’s test on discordant ' +
          'pairs. Testing 41 modules at once gets a Benjamini–Hochberg correction, ' +
          'because roughly two would clear p&lt;0.05 by chance alone.'
      )}
    </div>
  </div>`,
  { id: 'evidence' }
)

/* ── Services preview ─────────────────────────────────────────────────────── */

const services = section(
  `${head({
    eyebrow: 'Services',
    title: 'What I can build for you.',
    lede:
      'From a single valuation model to a research platform. Scope agreed in writing ' +
      'before any work starts.',
  })}
  <div class="grid grid--3" style="margin-top:var(--s7)">
    ${SERVICES.map(
      (s) => `<a class="card card--link reveal" href="/services/#${s.id}">
        <p class="mono" style="font-size:var(--t-label);letter-spacing:0.14em;color:var(--accent-text)">${s.n}</p>
        <h3 style="margin-top:var(--s3)">${s.title}</h3>
        <p class="muted" style="margin-top:var(--s3);font-size:var(--t-sm)">${s.lede}</p>
      </a>`
    ).join('')}
  </div>
  <div class="btn-row">${btn('/services/', 'Services and engagement models')}</div>`,
  { id: 'services' }
)

/* ── Export ───────────────────────────────────────────────────────────────── */

export default {
  path: '/',
  title: 'Soundariyan Karunakaran — Financial Analysis & Research Systems',
  description:
    'MSc International Corporate Finance. I build financial research and ' +
    'decision-support systems in Python — data pipelines, quantitative research ' +
    'and AI reasoning layers. London, UK.',
  body: [
    hero,
    pillars,
    work,
    evidence,
    services,
    section(ctaBand(), { cls: 'section--tight' }),
  ].join('\n'),
}
