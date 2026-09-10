import { PROJECTS, FACTS, LINKS } from '../content.mjs'
import { esc } from '../layout.mjs'
import { section, btn, metric, tags, callout, table, code, ctaBand } from '../components.mjs'

const P = PROJECTS.find((p) => p.slug === 'aria')

/* ── Layered architecture diagram ─────────────────────────────────────────── */

const LAYERS = [
  { layer: 'INTERFACE', title: 'Research terminal', note: 'React · Vite · 11,639 lines · installable PWA' },
  { layer: 'API', title: 'FastAPI service', note: '166 endpoints · APScheduler · session + OAuth auth' },
  { layer: 'REASONING', title: 'Cognitive loop & inference router', note: 'ORIENT → FOCUS → RECALL → ANALYSE → DECIDE → REFLECT' },
  { layer: 'RESEARCH', title: '41 modules · 7 families → ensemble → meta', note: 'each returns bull/bear/neutral, an interval, evidence — or abstains', accent: true },
  { layer: 'EVALUATION', title: 'Calibration · baselines · tiers · walk-forward', note: 'Brier · McNemar · Benjamini–Hochberg · purged CV', accent: true },
  { layer: 'DATA', title: 'Market data spine', note: '21,067 symbols · OHLCV · fundamentals · macro · news' },
]

function archDiagram() {
  // Narrow viewBox on purpose: the SVG scales to its container, so smaller
  // here means larger type in real pixels wherever it lands.
  const W = 520
  const BAND_H = 54
  const GAP = 10
  const X = 92
  const H = LAYERS.length * (BAND_H + GAP) + 8

  const bands = LAYERS.map((l, i) => {
    const y = 4 + i * (BAND_H + GAP)
    const cy = y + BAND_H / 2
    return `<g>
      <text class="arch-layer" x="${X - 12}" y="${cy + 4}" text-anchor="end">${l.layer}</text>
      <rect class="arch-band${l.accent ? ' arch-band--accent' : ''}" x="${X}" y="${y}" width="${W - X}" height="${BAND_H}" rx="4"/>
      <text class="arch-title" x="${X + 14}" y="${y + 23}">${esc(l.title)}</text>
      <text class="arch-note" x="${X + 14}" y="${y + 40}">${esc(l.note.toUpperCase())}</text>
    </g>`
  }).join('\n    ')

  // The gate sits beside the stack because it is a constraint on every layer,
  // not a step inside one.
  return `<div class="arch reveal">
    <svg viewBox="0 0 ${W} ${H}" role="img"
         aria-label="ARIA architecture in six layers, from the market data spine at the base up through evaluation, research, reasoning, the API and the interface. Research and evaluation are the load-bearing layers.">
      <defs>
        <marker id="arrowhead" markerWidth="6" markerHeight="6" refX="5" refY="3" orient="auto">
          <path d="M0 0.6L5 3 0 5.4z" fill="var(--line-2)"/>
        </marker>
      </defs>
      ${bands}
    </svg>
  </div>`
}

/* ── Data pipeline strip ──────────────────────────────────────────────────── */

const FLOW = [
  { t: 'Ingest', d: 'Vendor listings for the US, India and the UK; curated index constituents for 34 further markets.' },
  { t: 'Validate', d: 'Every curated symbol is priced against the vendor. Unpriceable names are removed, not left to abstain silently.' },
  { t: 'Truncate', d: 'as_of cuts every series to the evaluation date in the data layer, so no module can physically read a future bar.' },
  { t: 'Age', d: 'Each block carries as_of and stale. A three-month-old macro reading is reported as stale, never as today.' },
]

const flowStrip = `<div class="flow reveal">
  ${FLOW.map(
    (f, i) => `<div class="flow__step">
      <span class="flow__n">${String(i + 1).padStart(2, '0')}</span>
      <h3>${f.t}</h3>
      <p>${f.d}</p>
    </div>`
  ).join('')}
</div>`

/* ── Sections ─────────────────────────────────────────────────────────────── */

const SECTIONS = [
  {
    id: 'problem',
    n: '01',
    title: 'The problem',
    html: `<div class="prose">
      <p>
        Retail financial research tooling has a structural flaw: it is built to
        produce conviction. A screener returns a list, a backtest returns a
        Sharpe ratio, a chatbot returns a paragraph — and none of them carries
        the information a decision actually needs, which is <strong>how often
        this kind of call has been right before, and how sure we are allowed to be.</strong>
      </p>
      <p>
        I wanted to answer a narrower question than "what should I buy". I wanted
        a system that could hold a view, state a numeric confidence in it, and
        then be held to that number afterwards — including when the honest answer
        was that the sample was too small to say anything at all.
      </p>
      <p>
        That reframes the engineering problem. The hard part is not generating
        signals; it is building the accounting that makes a signal falsifiable.
      </p>
    </div>`,
  },
  {
    id: 'approaches',
    n: '02',
    title: 'Why the obvious approaches fell short',
    html: `${table(
      ['Approach', 'Where it breaks'],
      [
        [
          'Single model, single score',
          'One estimator is one hypothesis. When it is wrong there is nothing to compare it against and no way to attribute the failure to a cause.',
        ],
        [
          'LLM as forecaster',
          'Language models are fluent about markets and have no demonstrated ability to predict returns. Fluency reads as confidence, which is the most expensive possible failure mode in this domain.',
        ],
        [
          'Backtest-and-ship',
          'A backtest is a description of a past that has already been searched. Without out-of-sample separation and multiple-testing correction, a good Sharpe is a report on how hard you looked.',
        ],
        [
          'Accuracy as the metric',
          'Predict "up" on every equity at 55% confidence in a market that rises 55% of the time and you are perfectly calibrated and perfectly worthless. Accuracy and skill come apart.',
        ],
      ]
    )}
    <div style="margin-top:var(--s6)">
      ${callout(
        'The design consequence',
        'Every one of those failures is a measurement failure, not a modelling ' +
          'failure. So the measurement layer was built first and given authority ' +
          'over the modelling layer — modules cannot promote themselves, and the ' +
          'evaluation code has no list an author can edit.'
      )}
    </div>`,
  },
  {
    id: 'architecture',
    n: '03',
    title: 'Architecture',
    html: `<div class="prose"><p>
      Six layers. The two highlighted in the middle are the load-bearing ones —
      the interface and the API exist to expose them, and the data layer exists to
      feed them under a leakage constraint.
    </p></div>
    <div style="margin-top:var(--s6)">${archDiagram()}</div>
    <div class="grid grid--2" style="margin-top:var(--s6)">
      ${callout(
        'Isolation boundary',
        'A research module that raises does not break a scan. The registry catches ' +
          'it, returns an <span class="mono">insufficient</span> report naming the ' +
          'exception, and the ensemble proceeds with 40 opinions instead of 41. ' +
          'Partial results with a stated gap beat a 500.'
      )}
      ${callout(
        'Contract validation',
        'Every module return is validated against a shared contract before it is ' +
          'allowed into the ensemble — direction weights that sum to 100, an ' +
          'interval, a horizon, declared weaknesses. A violation is downgraded to ' +
          'an abstention rather than silently averaged in.'
      )}
    </div>`,
  },
  {
    id: 'data',
    n: '04',
    title: 'Data pipeline',
    html: `<div class="prose"><p>
      21,067 instruments across roughly 40 exchanges: live full listings for the
      US, India and the UK, and curated index constituents for 34 further markets
      spanning Europe, Asia, the Americas, Africa and the Middle East. Emerging
      markets are in there deliberately — the macro and cross-asset modules read
      "global" conditions off this list, and a universe that stops at North
      America produces a regional read wearing a global label.
    </p></div>
    <div style="margin-top:var(--s6)">${flowStrip}</div>
    <div style="margin-top:var(--s6)">
      ${callout(
        'The bug this pipeline exists to prevent',
        'An audit found a symbol whose venue, currency and market cap were all ' +
          'correct for a London listing while its <span class="mono">name</span> ' +
          'field said an American company. Nothing crashed — the analysis was ' +
          'simply graded against the wrong instrument’s close. Identity is now ' +
          'resolved and asserted at the data layer: 0 duplicate display symbols, ' +
          '0 suffix-versus-exchange mismatches, 0 currency mismatches.'
      )}
    </div>`,
  },
  {
    id: 'research',
    n: '05',
    title: 'Research pipeline',
    html: `<div class="prose">
      <p>
        41 independently callable modules across seven families — fundamental,
        price, quant, macro, volatility, machine and behavioural. Each takes a
        ticker and returns a bull/bear/neutral split summing to 100, a confidence
        interval, the evidence it used, and its own declared weaknesses. Or it
        abstains, which is a first-class outcome rather than an error.
      </p>
      <p>
        Most price and quant modules do not state a prior. They state a
        <em>condition</em> and ask the instrument's own history how often that
        condition preceded a positive return, so the interval is a Wilson interval
        on a genuine observation count. Confidence is defined as
        <span class="mono">P(direction is correct)</span> and therefore lives in
        [0.5, 1.0].
      </p>
    </div>
    <div style="margin-top:var(--s6)">
      ${table(
        ['Provenance', 'What produced the number', 'What it is worth'],
        [
          ['statistical', 'A condition tested against the instrument’s own history.', 'Confidence is a measured frequency.'],
          ['model', 'A fitted estimator — LightGBM, HMM, Kalman, PCA.', 'Worth its out-of-sample validation. A model can be sure and wrong.'],
          ['narrative', 'A language model produced or shaped the score.', 'Not evidence. Interval mechanically widened so it cannot outvote a measured module.'],
        ]
      )}
    </div>
    <p class="dim" style="font-size:0.8125rem;margin-top:var(--s3)">
      All 41 shipped modules are <span class="mono">statistical</span> or
      <span class="mono">model</span>. The floor exists so that adding a narrative
      module later cannot quietly launder LLM fluency into statistical confidence.
    </p>`,
  },
  {
    id: 'reasoning',
    n: '06',
    title: 'The reasoning layer',
    html: `<div class="prose">
      <p>
        A cognitive loop — <span class="mono">ORIENT → FOCUS → RECALL → ANALYSE →
        DECIDE → REFLECT</span> — runs on a local model through Ollama, with vector
        memory in ChromaDB and an optional, budget-capped frontier-model consult
        that is off by default. An inference router chooses the provider and
        degrades to a deterministic baseline when none is reachable.
      </p>
      <p>
        <strong>This layer explains and retrieves. It does not forecast.</strong>
        That boundary is the single most important design decision in the system,
        and it is enforced in the registry rather than requested in a docstring:
      </p>
    </div>
    <div style="margin-top:var(--s5)">
      ${code(
        `# The narrative floor, enforced here rather than asked for in a docstring.
# A language model has no observation count, so it may not present a narrow
# interval; the conviction it loses moves into \`neutral\`.
if spec.provenance == "narrative":
    report = report.widened_to(NARRATIVE_MIN_CI_WIDTH)

problems = validate(report)
if problems:
    logger.warning(f"v5 contract violation: {problems}")
    if strict:
        raise AssertionError(problems)
    return insufficient(name, spec.family, ticker,
                        "output violated the module contract: " + "; ".join(problems[:3]))`,
        { caption: 'src/v5/registry.py' }
      )}
    </div>`,
  },
  {
    id: 'ml',
    n: '07',
    title: 'The machine-learning layer',
    html: `<div class="prose">
      <p>
        A LightGBM ensemble with an explicit feature engine, target definitions and
        a walk-forward harness, alongside statistical estimators used where they
        suit the question better than a tree: hidden Markov models for regime,
        Kalman filters for state, PCA for factor structure, and clustering and
        cointegration tests for relative value.
      </p>
      <p>
        Fitted models carry <span class="mono">provenance="model"</span>, which
        buys them nothing automatically. Their interval reflects fit; their
        credibility comes from surviving the evaluation described below. Those two
        are reported separately on purpose.
      </p>
      <h3>Leakage control</h3>
      <p>
        Folds come from a purged walk-forward cross-validator (López de Prado) with
        each module's own horizon as the purge parameter, so an evaluation window
        never overlaps the forward window of the one before it. The truncation that
        makes this real lives in the data layer, not in 41 separate
        implementations — a module physically cannot read a bar from the future.
      </p>
      <p>
        An honest caveat, stated because the alternative implies more rigour than
        exists: most modules are rule-based rather than fitted. For those,
        "walk-forward" means evaluated out-of-sample across time, not trained here
        and tested there. That is still the number that matters, but it is a weaker
        claim than a fitted model surviving the same procedure, and the two are not
        quoted as if they were the same thing.
      </p>
    </div>`,
  },
  {
    id: 'evaluation',
    n: '08',
    title: 'Evaluation',
    html: `<div class="prose"><p>
      Three questions with three different answers, reported separately.
      Conflating them is how a system with no edge comes to look validated.
    </p></div>
    <div style="margin-top:var(--s6)">
      ${table(
        ['Question', 'Method', 'Refusal rule'],
        [
          [
            'Are the confidence numbers honest?',
            'Brier score, skill against a coin flip, expected calibration error, bucketed by stated confidence.',
            'Reports nothing below 20 resolved calls, and no bucket below 5.',
          ],
          [
            'Is it better than a napkin?',
            'Buy-and-hold, 20/50 SMA cross and 60-day momentum re-run over exactly the calls ARIA made — same tickers, dates and horizons — compared with McNemar’s test on the discordant pairs.',
            'Two systems that agree 39 times in 40 are not distinguishable; the report says <span class="mono">undetermined</span> rather than quoting a gap.',
          ],
          [
            'Which modules have earned a vote?',
            'Per-module tiers from resolved outcomes only: experimental → provisional → core → benched.',
            'With 41 modules tested at p&lt;0.05, ~2 clear the bar by chance. A Benjamini–Hochberg adjusted verdict is the field to read.',
          ],
        ]
      )}
    </div>
    <div style="margin-top:var(--s6)">
      ${callout(
        'The headline reports the weakest result, not the best',
        'Beating one baseline out of three is not edge. The summary figure is the ' +
          'worst of the three comparisons, because a system that gets to pick which ' +
          'benchmark it beat is grading its own homework.'
      )}
    </div>`,
  },
  {
    id: 'reliability',
    n: '09',
    title: 'Reliability mechanisms',
    html: `<div class="prose"><p>
      Five constraints that hold whatever the analysis says. Each is code, not policy.
    </p></div>
    <div style="margin-top:var(--s6)">
      ${table(
        ['Mechanism', 'What it guarantees'],
        [
          ['Human-approval gate', 'A live brokerage account always routes to the manual approval queue. There is no override flag. Auto-execution can only ever be armed on a paper account.'],
          ['Fail-closed gates', 'Every safety gate returns "no" when its inputs are missing. An unavailable check is not a passed check.'],
          ['Staleness propagation', 'Every world-model block carries <span class="mono">as_of</span> and <span class="mono">stale</span>. A stale block is reported as stale at the top level rather than folded into a headline.'],
          ['Outcome attribution', 'Results are graded four ways — right and decisive, right inside the noise band, wrong inside the noise band, wrong and decisive — because a wrong call on a 0.04% move is a coin landing on its edge, not a thesis failure.'],
          ['Non-independence handling', 'Repeated debates about one event are collapsed to distinct events before calibration. Pooling 306 rows that were 61 events once made every interval roughly √5 too narrow.'],
        ]
      )}
    </div>
    <div style="margin-top:var(--s6)">
      ${callout(
        'Independent review',
        'For decisions expensive to reverse, ARIA can ask <a class="link" href="/work/sentinel/">SENTINEL</a> — a separate ' +
          'process with its own memory and permissions — to try to break the thesis ' +
          'before it acts. The useful output is not approval; it is the list of ' +
          'things that would make the thesis wrong.'
      )}
    </div>`,
  },
  {
    id: 'outcomes',
    n: '10',
    title: 'Engineering outcomes',
    html: `<div class="prose"><p>
      What can be measured about the build, as opposed to about the markets. These
      are engineering facts, each traceable to a file in the repository.
    </p></div>
    <div class="grid grid--3" style="margin-top:var(--s6)">
      ${[FACTS.srcLines, FACTS.testModules, FACTS.endpoints, FACTS.modules, FACTS.symbols, FACTS.frontendLines]
        .map((f) => `<div class="reveal">${metric(f)}</div>`)
        .join('')}
    </div>
    <div style="margin-top:var(--s6)">
      ${callout(
        'What is deliberately absent',
        'There is no performance figure on this page, because the system reports ' +
          '<span class="mono">measurable: false</span> — not enough predictions have ' +
          'resolved to say anything. Architecture is not evidence, and 41 modules ' +
          'describe how the system is built, not whether it works.',
        'Publishing a return number here would be the easiest thing on the site to ' +
          'do and the fastest way to make everything else on it untrustworthy.'
      )}
    </div>`,
  },
  {
    id: 'lessons',
    n: '11',
    title: 'What I learned building it',
    html: `<div class="prose">
      <h3>Measurement has to be built before the thing being measured</h3>
      <p>
        The track record read zero for two months, and not because the system was
        young. Predictions carried a 45-trading-day horizon while the ledger had
        only just opened, and outcomes that had already been measured elsewhere —
        18 closed trades with realised P&amp;L, 140 dated recommendations with entry
        prices — never reached the track record because it only knew how to read
        one file. A flywheel with a two-month cold start and no shorter loop to
        turn in the meantime is a design error, not a waiting period.
      </p>
      <h3>A statistic can be right and its conclusion still wrong</h3>
      <p>
        The first calibration pass reported a Brier score of 0.3055 and concluded
        conviction was inverted. It was wrong twice over: the rows were not
        independent, and it had mixed two populations whose mean conviction differed
        by 53 points. The arithmetic was fine. The population definition was the
        bug — which is the failure mode I now look for first.
      </p>
      <h3>Abstention needs to be cheap, or it never happens</h3>
      <p>
        Making "insufficient evidence" a first-class return value rather than an
        error changed module behaviour more than any modelling decision. When
        abstaining is expensive, code guesses.
      </p>
      <h3>Fluency is the most expensive bug in an AI system</h3>
      <p>
        A language model will produce a confident, well-argued, entirely
        unsupported market view on request. Keeping that output structurally
        incapable of outvoting a measured one was worth more than any prompt.
      </p>
    </div>`,
  },
  {
    id: 'limitations',
    n: '12',
    title: 'Current limitations',
    html: `<div class="prose"><p>
      Stated plainly, because a case study that lists only strengths tells a reader
      nothing they can use.
    </p>
      <ul>
        <li><strong>No demonstrated edge.</strong> Every measurement surface currently reports <span class="mono">measurable: false</span>. This is the honest state of the project, not an oversight.</li>
        <li><strong>No published live or paper track record.</strong></li>
        <li><strong>Backtests are in-sample until proven otherwise.</strong> The walk-forward harness exists but has not been run across every module. Treat any backtested Sharpe as a hypothesis.</li>
        <li><strong>41 modules on one horizon is a lot of correlated tests.</strong> The tier system corrects for it; the ensemble's family weights are reasoned rather than fitted and have not themselves been validated out-of-sample.</li>
        <li><strong>Data quality is vendor-dependent.</strong> Survivorship bias and adjustment errors flow through to results. The system falls back to a stale cache rather than abstaining, and labels it when it does.</li>
        <li><strong>Scope is wide for one maintainer.</strong> Depth per feature varies, and the newest parts have the least test coverage.</li>
        <li><strong>The LLM layer has not been evaluated for anything.</strong> It is a synthesis and interface layer.</li>
      </ul>
    </div>`,
  },
  {
    id: 'roadmap',
    n: '13',
    title: 'Where it goes next',
    html: `<div class="prose">
      <ul>
        <li><strong>Resolve enough predictions to make the measurement surfaces report.</strong> Everything else is secondary to crossing the reporting threshold honestly rather than lowering it.</li>
        <li><strong>Run the walk-forward harness across all 41 modules</strong> so <span class="mono">walk_forward_validated</span> stops being a flag that is mostly false.</li>
        <li><strong>Fit the ensemble's family weights out-of-sample</strong> rather than reasoning about them.</li>
        <li><strong>Shorten the feedback loop</strong> with additional horizons, so the system learns something before 45 trading days have passed.</li>
        <li><strong>Widen the SENTINEL bridge</strong> from consultation on demand to scheduled adversarial review of standing theses.</li>
      </ul>
    </div>`,
  },
]

/* ── Page ─────────────────────────────────────────────────────────────────── */

const toc = `<nav class="toc" data-toc aria-label="Case study contents">
  <p class="toc__title">Contents</p>
  <ul class="toc__list">
    ${SECTIONS.map(
      (s) => `<li><a href="#${s.id}"><span>${s.n}</span><span>${esc(s.title)}</span></a></li>`
    ).join('')}
  </ul>
</nav>`

const hero = `<section class="hero">
  <div class="container">
    <p class="eyebrow">Case study · ${esc(P.kicker)}</p>
    <h1 class="hero__title" style="font-size:var(--t-h1)">ARIA</h1>
    <p class="mono" style="font-size:var(--t-label);letter-spacing:0.14em;text-transform:uppercase;color:var(--text-3);margin-top:var(--s3)">
      ${esc(P.expansion)}
    </p>
    <p class="lede" style="margin-top:var(--s5)">${esc(P.summary)}</p>
    <div style="margin-top:var(--s6)">${tags(P.stack, true)}</div>
    <div class="hero__facts">
      ${P.metrics.map((m) => metric(m, { showSrc: false })).join('')}
    </div>
    <div class="btn-row">
      ${btn(LINKS.repo, 'View the repository', { external: true })}
      ${btn('/work/sentinel/', 'The reliability layer')}
    </div>
    <p class="dim" style="font-size:0.8125rem;margin-top:var(--s5);max-width:70ch">
      Role: ${esc(P.role)}. Architecture, data layer, research modules, evaluation
      harness, API and interface — designed and written by me.
    </p>
  </div>
</section>`

const body = `<section class="section">
  <div class="container">
    <div class="study">
      ${toc}
      <div>
        ${SECTIONS.map(
          (s) => `<article class="study__section" id="${s.id}">
            <p class="eyebrow">${s.n} — ${esc(s.title)}</p>
            <h2>${esc(s.title)}</h2>
            <div style="margin-top:var(--s5)">${s.html}</div>
          </article>`
        ).join('')}
      </div>
    </div>
  </div>
</section>`

export default {
  path: '/work/aria/',
  title: 'ARIA — a financial research platform, measured honestly',
  description:
    'Case study: ARIA, a financial research platform. 41 research modules over ' +
    '21,067 symbols, a purged walk-forward evaluation harness, and a ' +
    'code-enforced human-approval gate.',
  schema: {
    '@type': 'SoftwareApplication',
    name: 'ARIA',
    alternateName: 'Adaptive Reasoning & Intelligence Architecture',
    applicationCategory: 'FinanceApplication',
    description: P.summary,
    operatingSystem: 'Cross-platform (self-hosted)',
    url: LINKS.repo,
  },
  body: [hero, body, section(ctaBand({
    eyebrow: 'Build something like this',
    title: 'Need a research system of your own?',
    lede: 'Data pipelines, quantitative research infrastructure, evaluation harnesses and the dashboards on top.',
    primary: ['/contact/', 'Discuss a project'],
    secondary: ['/services/', 'See the services'],
  }), { cls: 'section--tight' })].join('\n'),
}
