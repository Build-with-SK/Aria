import { SITE, LINKS, DISCLAIMER } from '../content.mjs'
import { esc, ICONS } from '../layout.mjs'
import { section, head, callout } from '../components.mjs'

const CHANNELS = [
  {
    icon: ICONS.mail,
    label: 'Email',
    value: LINKS.email,
    href: `mailto:${LINKS.email}`,
    note: 'Best for project enquiries. A paragraph is plenty to start.',
  },
  {
    icon: ICONS.linkedin,
    label: 'LinkedIn',
    value: 'soundariyan-karunakaran',
    href: LINKS.linkedin,
    note: 'Best for roles, recruiters and introductions.',
    external: true,
  },
  {
    icon: ICONS.github,
    label: 'GitHub',
    value: 'Build-with-SK',
    href: LINKS.github,
    note: 'The code behind the case studies.',
    external: true,
  },
]

const intro = `<section class="hero">
  <div class="container">
    <div class="split split--wide-left">
      <div>
        <p class="eyebrow">Contact</p>
        <h1 class="hero__title" style="font-size:var(--t-h1)">Let’s talk about what you need built.</h1>
        <p class="lede" style="margin-top:var(--s5)">
          Freelance analysis and engineering work, and open to analyst and
          quantitative roles. Based in ${esc(SITE.location)}, working with clients
          anywhere.
        </p>
        <p class="muted" style="margin-top:var(--s5);max-width:60ch">
          If it is not something I should take on, I will tell you that quickly
          rather than quote for it — which is usually the more useful reply.
        </p>
      </div>
      <div class="grid" style="gap:var(--s3)">
        ${CHANNELS.map(
          (c) => `<a class="channel reveal" href="${esc(c.href)}"${
            c.external ? ' target="_blank" rel="noopener"' : ''
          }>
            <span class="channel__icon">${c.icon}</span>
            <span>
              <span class="channel__label">${esc(c.label)}</span><br>
              <span class="channel__value">${esc(c.value)}</span>
            </span>
            <span class="channel__arrow">${ICONS.arrow}</span>
          </a>`
        ).join('')}
        <p class="dim" style="font-size:0.8125rem;margin-top:var(--s2)">
          ${CHANNELS.map((c) => esc(c.note)).join(' ')}
        </p>
      </div>
    </div>
  </div>
</section>`

const brief = section(
  `<div class="split">
    <div>
      ${head({
        eyebrow: 'Making the first message useful',
        title: 'Four lines is enough.',
        lede:
          'You do not need a specification. These are just the things that let me ' +
          'give you a real answer instead of a discovery call.',
      })}
    </div>
    <div class="panel reveal">
      <div class="panel__head"><span>What to include</span><span>04</span></div>
      <div class="panel__body">
        <ul class="service__list">
          <li><strong>The decision.</strong> What will you do differently once this exists?</li>
          <li><strong>The data.</strong> What you have, where it lives, or that you do not have any yet.</li>
          <li><strong>The deadline.</strong> Even approximate — it changes the right approach more than the budget does.</li>
          <li><strong>The constraint.</strong> Budget, tooling you must stay inside, or an existing system it has to fit.</li>
        </ul>
      </div>
    </div>
  </div>`,
  { id: 'brief' }
)

const availability = section(
  `${head({
    eyebrow: 'Availability',
    title: 'What I am open to.',
  })}
  <div class="grid grid--3" style="margin-top:var(--s6)">
    ${[
      ['Freelance projects', 'Financial analysis, modelling, Python and data work, AI automation, research tooling. Fixed-scope or milestone-based.'],
      ['Roles', 'Equity research, quantitative research and analyst positions, and engineering roles at the finance–technology boundary. London, hybrid or remote.'],
      ['Collaboration', 'Technical collaborators working on research infrastructure, evaluation methodology or financial data systems.'],
    ]
      .map(
        ([t, b]) => `<div class="card reveal">
          <h3>${esc(t)}</h3>
          <p class="muted" style="margin-top:var(--s3);font-size:var(--t-sm)">${esc(b)}</p>
        </div>`
      )
      .join('')}
  </div>
  <div style="margin-top:var(--s7)">
    ${callout('Before you write', esc(DISCLAIMER))}
  </div>`,
  { id: 'availability' }
)

export default {
  path: '/contact/',
  title: 'Contact — work with Soundariyan Karunakaran',
  description:
    'Get in touch about freelance financial analysis, financial modelling, Python ' +
    'and data work, AI automation or research systems — or about analyst and ' +
    'quantitative roles. London, UK.',
  body: [intro, brief, availability].join('\n'),
}
