import { SITE, CREDENTIALS, STACK, LINKS } from '../content.mjs'
import { esc } from '../layout.mjs'
import { section, head, btn, callout, ctaBand } from '../components.mjs'

const intro = `<section class="hero">
  <div class="container">
    <div class="split split--wide-left">
      <div>
        <p class="eyebrow">About</p>
        <h1 class="hero__title" style="font-size:var(--t-h1)">
          I came to engineering through finance,<br>not the other way round.
        </h1>
        <p class="lede" style="margin-top:var(--s5)">
          ${esc(SITE.name)} — ${esc(SITE.location)}. MSc International Corporate
          Finance, Distinction. I write software because the analysis I wanted to
          do could not be done by hand.
        </p>
        <div class="btn-row">
          ${btn('/contact/', 'Work with me', { primary: true })}
          ${btn('/work/', 'See the work')}
        </div>
      </div>
      <div class="grid" style="gap:var(--s4)">
        ${CREDENTIALS.map(
          (c) => `<div class="card reveal">
            <p class="mono" style="font-size:var(--t-label);letter-spacing:0.1em;text-transform:uppercase;color:var(--text-3)">${esc(c.meta)}</p>
            <h2 style="margin-top:var(--s2);font-size:var(--t-h3)">${esc(c.label)}</h2>
            <p class="muted" style="margin-top:var(--s2);font-size:var(--t-sm)">${esc(c.detail)}</p>
          </div>`
        ).join('')}
      </div>
    </div>
  </div>
</section>`

const story = section(
  `<div class="split split--wide-left">
    <div class="prose">
      <p class="eyebrow">The short version</p>
      <h2 style="margin-top:var(--s4)">Finance first. Then the systems question.</h2>
      <p style="margin-top:var(--s5)">
        The MSc taught me to value a business and read a set of accounts. What it
        also taught me — less deliberately — was how much of financial analysis is
        the same procedure repeated with different inputs, and how much judgement
        gets spent on work a machine should have done.
      </p>
      <p>
        So I learned to program. Not as a career pivot, but because the questions I
        wanted to ask needed more data than a spreadsheet holds and more repetitions
        than a person will do carefully. That order matters: I did not learn finance
        to have something to build software about.
      </p>
      <h3>Where quantitative research came in</h3>
      <p>
        Automating an analysis makes an uncomfortable thing obvious — you can now run
        it a thousand times, and a thousand runs will produce something impressive
        whether or not there is anything there. Learning to tell those apart is what
        turned scripting into quantitative research: out-of-sample testing,
        calibration, multiple-testing correction, and the discipline of comparing
        against a baseline stupid enough to be honest.
      </p>
      <h3>And then AI</h3>
      <p>
        Language models arrived as an obvious accelerant and a serious hazard. They
        are extremely good at synthesis, retrieval and explanation, and produce
        confident market views with no predictive basis whatsoever. Most of my
        interesting engineering decisions come from taking both halves of that
        sentence seriously at once — using the capability while structurally
        preventing the failure.
      </p>
      <h3>What that adds up to</h3>
      <p>
        A finance professional who specifies the right question, builds the system
        that answers it, and then builds the machinery that checks whether the answer
        was any good. Those are usually three different people, and most of what goes
        wrong in financial software happens in the gaps between them.
      </p>
    </div>
    <div style="display:grid;gap:var(--s4)">
      ${callout(
        'Bloomberg Global Trading Challenge 2025',
        'Captained <strong>The Sharpe Syndicate</strong> to 122nd of 2,394 teams ' +
          'globally and 34th in Europe — the top 5% worldwide. Real instruments, ' +
          'real prices, a fixed window and a leaderboard that does not care about ' +
          'your reasoning.'
      )}
      ${callout(
        'Also',
        'I write and produce music, and run <span class="mono">@that_finance_guy</span>, ' +
          'explaining finance to a general audience. Both are the same skill as the ' +
          'day job: taking something structurally complicated and making it land ' +
          'without lying about it.'
      )}
    </div>
  </div>`,
  { id: 'story' }
)

const stack = section(
  `${head({
    eyebrow: 'Technical stack',
    title: 'What I actually use.',
    lede:
      'Everything listed here appears in work I have built and can talk through ' +
      'line by line. Nothing is here because it looked good in a list.',
  })}
  <div class="grid grid--3" style="margin-top:var(--s7)">
    ${STACK.map(
      (g) => `<div class="panel reveal">
        <div class="panel__head"><span>${esc(g.title)}</span><span>${String(g.items.length).padStart(2, '0')}</span></div>
        <div class="panel__body">
          <ul class="service__list">
            ${g.items.map((i) => `<li>${esc(i)}</li>`).join('')}
          </ul>
        </div>
      </div>`
    ).join('')}
  </div>
  <p class="dim" style="font-size:0.8125rem;margin-top:var(--s6);max-width:72ch">
    There are no proficiency percentages on this page. A bar chart claiming
    “Python 92%” is a number with no definition, no measurement and no way to be
    wrong — which is the opposite of how I would like my work assessed. The
    <a class="link" href="/work/">case studies</a> and the
    <a class="link" href="${LINKS.repo}" target="_blank" rel="noopener">repository</a>
    are the evidence.
  </p>`,
  { id: 'stack' }
)

const principles = section(
  `${head({
    eyebrow: 'How I work',
    title: 'Four things I hold to.',
  })}
  <div class="grid grid--2" style="margin-top:var(--s7)">
    ${[
      ['State the limitation', 'Every analysis I deliver says what it does not establish. Work that only lists its strengths is unfinished, and a client who is surprised later was under-informed earlier.'],
      ['Make abstention cheap', '“The sample is too small to say” has to be an easy, respectable answer, in code and in conversation. When not knowing is expensive, people guess.'],
      ['Measure before believing', 'Out-of-sample, against a baseline, with the multiple-testing correction applied. An impressive backtest is a report on how hard someone looked.'],
      ['Design the failure', 'What happens when the data is stale, the vendor is down or the check cannot run should be a decision, not a discovery. Gates fail closed.'],
    ]
      .map(
        ([t, b]) => `<div class="card reveal">
          <h3>${esc(t)}</h3>
          <p class="muted" style="margin-top:var(--s3);font-size:var(--t-sm)">${esc(b)}</p>
        </div>`
      )
      .join('')}
  </div>`,
  { id: 'principles' }
)

export default {
  path: '/about/',
  title: 'About — Soundariyan Karunakaran, financial analyst & engineer',
  description:
    'MSc International Corporate Finance (Distinction), London. Financial analysis, ' +
    'quantitative research and software engineering — and the technical stack ' +
    'behind the work.',
  body: [
    intro,
    story,
    stack,
    principles,
    section(ctaBand({
      eyebrow: 'Get in touch',
      title: 'Hiring, or have something to build?',
      lede: 'Open to analyst and quantitative roles, and taking on freelance analysis, modelling and engineering work.',
      primary: ['/contact/', 'Start a conversation'],
      secondary: ['/services/', 'What I can build'],
    }), { cls: 'section--tight' }),
  ].join('\n'),
}
