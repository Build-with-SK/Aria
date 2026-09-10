import { SERVICES, ENGAGEMENTS, DISCLAIMER } from '../content.mjs'
import { esc } from '../layout.mjs'
import { section, head, btn, callout, steps, ctaBand } from '../components.mjs'

const intro = `<section class="hero">
  <div class="container">
    <p class="eyebrow">Services</p>
    <h1 class="hero__title" style="font-size:var(--t-h1)">Analysis, models, and the software that produces them.</h1>
    <p class="lede" style="margin-top:var(--s5)">
      Most of this work sits on a spectrum with a spreadsheet at one end and a
      research platform at the other. I work across the whole of it, which means
      the recommendation you get is the right size for the problem rather than the
      size I happen to sell.
    </p>
    <div class="btn-row">
      ${btn('/contact/', 'Discuss a project', { primary: true })}
      ${btn('/work/', 'See the work first')}
    </div>
  </div>
</section>`

const services = section(
  `<div>
    ${SERVICES.map(
      (s) => `<div class="service reveal" id="${s.id}" style="scroll-margin-top:84px">
        <div class="service__n">${s.n}</div>
        <div>
          <h2 style="font-size:var(--t-h2)">${esc(s.title)}</h2>
          <p class="muted" style="margin-top:var(--s3);max-width:44ch">${esc(s.lede)}</p>
        </div>
        <ul class="service__list">
          ${s.items.map((i) => `<li>${esc(i)}</li>`).join('')}
        </ul>
      </div>`
    ).join('')}
  </div>`,
  { id: 'catalogue' }
)

const engagement = section(
  `${head({
    eyebrow: 'How it works',
    title: 'Three ways to engage.',
    lede:
      'Whichever shape it takes, the scope is written down before any work starts ' +
      'and you own everything produced.',
  })}
  <div class="grid grid--3" style="margin-top:var(--s7)">
    ${ENGAGEMENTS.map(
      (e) => `<div class="card reveal">
        <h3>${esc(e.title)}</h3>
        <p class="muted" style="margin-top:var(--s3);font-size:var(--t-sm)">${esc(e.lede)}</p>
        <p class="dim" style="margin-top:var(--s3);font-size:0.8125rem">${esc(e.detail)}</p>
      </div>`
    ).join('')}
  </div>`,
  { id: 'engagement' }
)

const process = section(
  `${head({
    eyebrow: 'Process',
    title: 'What working together looks like.',
  })}
  <div style="margin-top:var(--s7)">
    ${steps([
      {
        title: 'A conversation, not a questionnaire',
        body:
          'Twenty minutes on what you are actually trying to decide. Often the ' +
          'brief that arrives is not the problem worth solving, and it is cheaper ' +
          'to find that out before anyone quotes.',
      },
      {
        title: 'A written scope',
        body:
          'Deliverables, assumptions, data sources, what is explicitly out of ' +
          'scope, timeline and price. If the data required does not exist or is ' +
          'not obtainable, you hear that here rather than at delivery.',
      },
      {
        title: 'Increments you can see',
        body:
          'For anything longer than a week you get working output early — a first ' +
          'model, a running endpoint, a draft analysis — so direction can change ' +
          'while changing it is still cheap.',
      },
      {
        title: 'Delivery with the reasoning attached',
        body:
          'Models arrive documented and auditable; code arrives with tests and a ' +
          'README. You should be able to hand the deliverable to someone else and ' +
          'have them understand it without me.',
      },
      {
        title: 'A stated limitation',
        body:
          'Every deliverable comes with what it does not establish. An analysis ' +
          'that only lists its strengths is not finished.',
      },
    ])}
  </div>`,
  { id: 'process' }
)

const scope = section(
  `<div class="split">
    <div>
      ${head({
        eyebrow: 'Scope of practice',
        title: 'What I do not do.',
        lede:
          'Stated up front, because the boundary matters more in this field than in most.',
      })}
    </div>
    <div class="grid" style="gap:var(--s4)">
      ${callout(
        'Not regulated advice',
        esc(DISCLAIMER),
        'I build analytical tools and produce research. Deciding what to do with ' +
          'the output is yours, or your regulated adviser’s.'
      )}
      ${callout(
        'Not a trading bot for hire',
        'I do not build or operate systems that place trades on someone else’s ' +
          'behalf, and I do not sell signals. Research infrastructure, evaluation ' +
          'harnesses and decision-support tooling — yes.'
      )}
    </div>
  </div>`,
  { id: 'scope' }
)

export default {
  path: '/services/',
  title: 'Services — financial analysis, modelling, Python & AI automation',
  description:
    'Freelance financial analysis, financial modelling, Python and data analysis, ' +
    'AI research automation and quantitative research systems. Written scope ' +
    'before any work starts. London, UK.',
  schema: {
    '@type': 'Service',
    serviceType: 'Financial analysis, financial modelling and research software engineering',
    provider: { '@type': 'Person', name: 'Soundariyan Karunakaran' },
    areaServed: 'Worldwide',
    hasOfferCatalog: {
      '@type': 'OfferCatalog',
      name: 'Services',
      itemListElement: SERVICES.map((s) => ({
        '@type': 'Offer',
        itemOffered: { '@type': 'Service', name: s.title, description: s.lede },
      })),
    },
  },
  body: [
    intro,
    services,
    engagement,
    process,
    scope,
    section(ctaBand({
      eyebrow: 'Next step',
      title: 'Tell me what you are trying to decide.',
      lede: 'A short description of the problem is enough to start. If it is not something I should take on, I will say so and point you somewhere better.',
      primary: ['/contact/', 'Get in touch'],
      secondary: ['/work/aria/', 'See a system I built'],
    }), { cls: 'section--tight' }),
  ].join('\n'),
}
