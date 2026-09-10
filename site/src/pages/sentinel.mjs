import { PROJECTS } from '../content.mjs'
import { esc } from '../layout.mjs'
import {
  section, btn, tags, callout, table, code, ctaBand, relationDiagram,
} from '../components.mjs'

const P = PROJECTS.find((p) => p.slug === 'sentinel')

const SECTIONS = [
  {
    id: 'problem',
    n: '01',
    title: 'The problem',
    html: `<div class="prose">
      <p>
        A research system that checks its own work is not being checked. It shares
        the reviewer's priors, the reviewer's data, the reviewer's blind spots and
        the reviewer's incentive to find the answer it already reached. Adding a
        "critic" step inside the same process buys the appearance of scrutiny at
        the price of the real thing.
      </p>
      <p>
        The failures worth catching in a financial research system are not bugs.
        They are <strong>broken assumptions</strong>: a thesis resting on a
        relationship that stopped holding, a signal that is really a proxy for one
        other variable, an anomaly being explained rather than investigated. None
        of those trip an exception. All of them look like normal output.
      </p>
      <p>
        SENTINEL exists to be the thing that does not share ARIA's premises.
      </p>
    </div>`,
  },
  {
    id: 'separation',
    n: '02',
    title: 'The separation principle',
    html: `<div class="split" style="align-items:center">
      <div class="prose">
        <p>
          SENTINEL is a general intelligence running in <strong>its own process,
          with its own memory and its own permissions</strong>. ARIA does not
          import it, does not read its database and does not depend on it.
        </p>
        <p>
          That is an architectural claim, not a description of intent. The entire
          coupling between the two systems is a single module on ARIA's side: one
          authenticated HTTP call with a timeout. If that file were deleted, ARIA
          would lose a capability and nothing else.
        </p>
        <p>
          Independence that is asserted in documentation decays. Independence that
          is enforced by a process boundary does not.
        </p>
      </div>
      ${relationDiagram()}
    </div>`,
  },
  {
    id: 'contract',
    n: '03',
    title: 'The bridge contract',
    html: `<div class="prose"><p>
      Four properties, each chosen to prevent a specific failure that a naive
      integration would have introduced.
    </p></div>
    <div style="margin-top:var(--s6)">
      ${table(
        ['Property', 'The failure it prevents'],
        [
          ['Authenticated', 'A consultation endpoint reachable without a shared secret is an open reasoning service on a machine that holds market credentials.'],
          ['Timed out', 'A slow consultant becomes a hung trading cycle. Every call carries a bound.'],
          ['Never retried in a loop', 'Retry storms turn one unavailable dependency into an outage of the system that depended on it.'],
          ['Never raises into a caller', 'Every function returns a result whose <span class="mono">ok</span> is false rather than throwing. A specialist that stops working because its consultant is offline was never independent to begin with.'],
        ]
      )}
    </div>`,
  },
  {
    id: 'gating',
    n: '04',
    title: 'When it is consulted',
    html: `<div class="prose">
      <p>
        Consultation costs time and tokens, so the decision to ask is made
        deterministically and in one place — rather than by whichever caller
        happens to feel uncertain. The gate encodes the judgement so it is applied
        consistently, and it returns the reason alongside the verdict so the
        decision is auditable after the fact.
      </p>
      <p>
        <strong>Using a language model to decide whether to use a language model
        is a cost with no corresponding gain.</strong> So this is ordinary code:
      </p>
    </div>
    <div style="margin-top:var(--s5)">
      ${code(
        `def should_consult(*, unfamiliar: bool = False, conflicting_evidence: bool = False,
                   anomaly: bool = False, high_impact: bool = False,
                   outside_domain: bool = False,
                   own_confidence: Optional[float] = None) -> tuple[bool, str]:
    if outside_domain:
        return True, "the question is outside ARIA's trading domain"
    if conflicting_evidence:
        return True, "the evidence conflicts and ARIA cannot resolve it alone"
    if anomaly:
        return True, "an anomaly may indicate a broken assumption"
    if high_impact and (own_confidence is None or own_confidence < 0.75):
        return True, "high-impact decision held with less than firm confidence"
    ...
    return False, "ARIA can answer this itself; consultation would cost without informing"`,
        { caption: 'src/consult/sentinel_client.py' }
      )}
    </div>
    <div style="margin-top:var(--s6)">
      ${callout(
        'Note what is not a trigger',
        'Low confidence alone does not open a consultation until it falls below a ' +
          'floor. A system that escalates whenever it is unsure escalates ' +
          'constantly, and the review stops being scarce enough to be taken ' +
          'seriously.'
      )}
    </div>`,
  },
  {
    id: 'output',
    n: '05',
    title: 'Adversarial review, not approval',
    html: `<div class="prose">
      <p>
        The primary mode is not "check this" — it is <em>attempt to break this</em>.
        A thesis expensive to reverse is submitted with its supporting evidence,
        its counter-evidence, what has already been tried and what remains
        uncertain. What comes back is an independent analysis that may disagree.
      </p>
      <p>
        <strong>Disagreement is the point.</strong> The useful outcome of a review
        is never approval; it is the enumerated list of things that would make the
        thesis wrong.
      </p>
    </div>
    <div class="grid grid--2" style="margin-top:var(--s6)">
      ${callout('Returned', 'An independent analysis, explicit counter-arguments by kind, alternative hypotheses, an enumeration of what is <em>not</em> established, and a recommendation — each carried separately rather than blended into a verdict.')}
      ${callout('Not returned', 'A score to average into ARIA’s own. The two systems’ outputs are never combined numerically, because averaging a critique into the thing it criticises destroys exactly the information the critique carried.')}
    </div>`,
  },
  {
    id: 'failure',
    n: '06',
    title: 'Failure behaviour',
    html: `<div class="prose">
      <p>
        The rule the bridge exists to enforce: <strong>if SENTINEL is unavailable,
        ARIA continues working.</strong> Unreachability is logged at info level,
        not as an error, because ARIA operating without a consultant is a normal
        state rather than a fault.
      </p>
      <p>
        One consequence is worth stating on its own, because it is the mistake this
        design most wants to prevent:
      </p>
    </div>
    <div style="margin-top:var(--s5)">
      ${callout(
        'An unavailable reviewer is not an approving reviewer',
        'When a consultation fails, the returned object carries explicit guidance ' +
          'that silence is <strong>not</strong> agreement, and that ARIA must proceed ' +
          'on its own analysis while recording that no second opinion was obtained.',
        'Systems that treat a failed check as a passed check are the reason ' +
          '“fail closed” is a phrase. Here the check does not silently pass — it ' +
          'visibly did not happen.'
      )}
    </div>`,
  },
  {
    id: 'feedback',
    n: '07',
    title: 'Recording disagreement',
    html: `<div class="prose">
      <p>
        ARIA reports back whether it accepted the analysis and what followed. That
        closes the only loop either system has for learning whether the bridge is
        worth its cost.
      </p>
      <p>
        Rejecting a consultation is a legitimate and expected outcome. It is
        recorded rather than silently ignored, which means a reviewer that is
        consistently rejected and consistently right becomes visible — and so does
        one that is consistently accepted and consistently wrong. Without that
        record, an expensive second opinion is indistinguishable from an expensive
        habit.
      </p>
    </div>`,
  },
  {
    id: 'discipline',
    n: '08',
    title: 'What it is deliberately not',
    html: `${table(
      ['Not', 'Because'],
      [
        ['A feature of ARIA', 'A reviewer inside the reviewed process shares its state, its assumptions and its failure modes. The separation is the entire value.'],
        ['A chatbot', 'It is consulted by code under a deterministic gate, not conversed with. There is no interface where a human talks it into agreement.'],
        ['A dependency', 'Nothing in ARIA blocks on it, and no code path requires it to have answered.'],
        ['A source of scores', 'It contributes reasoning, never a number to be averaged into an ensemble.'],
        ['An escalation path for uncertainty', 'It is reserved for broken assumptions and expensive decisions. Consulting it about everything would make it about nothing.'],
      ]
    )}`,
  },
  {
    id: 'status',
    n: '09',
    title: 'Current status',
    html: `<div class="prose">
      <p>
        The bridge is implemented and operational: authentication, the
        deterministic gate, the four consultation modes, fail-soft behaviour and
        outcome reporting are all in place and covered by ARIA's test suite.
        SENTINEL itself runs as a separate system and its internals are not
        published here.
      </p>
      <h3>Next</h3>
      <ul>
        <li><strong>Scheduled adversarial review</strong> of standing theses, rather than consultation only at decision time.</li>
        <li><strong>Disagreement analytics</strong> — measuring, over resolved outcomes, whether accepting or rejecting the second opinion produced better decisions.</li>
        <li><strong>Consultation budget accounting</strong>, so the cost of the bridge is reported next to its measured value rather than assumed to be worth it.</li>
      </ul>
    </div>`,
  },
]

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
    <h1 class="hero__title" style="font-size:var(--t-h1)">SENTINEL</h1>
    <p class="mono" style="font-size:var(--t-label);letter-spacing:0.14em;text-transform:uppercase;color:var(--text-3);margin-top:var(--s3)">
      ${esc(P.expansion)}
    </p>
    <p class="lede" style="margin-top:var(--s5)">${esc(P.summary)}</p>
    <div style="margin-top:var(--s6)">${tags(P.stack, true)}</div>
    <div class="btn-row">
      ${btn('/work/aria/', 'The system it reviews', { primary: true })}
      ${btn('/work/', 'All case studies')}
    </div>
    <p class="dim" style="font-size:0.8125rem;margin-top:var(--s5);max-width:72ch">
      Role: ${esc(P.role)}. This page describes the architecture and the bridge
      contract. SENTINEL's own implementation is a separate system and is not
      documented here.
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
  path: '/work/sentinel/',
  title: 'SENTINEL — an independent reliability architecture | Case study',
  description:
    'Case study: SENTINEL, a reliability architecture running in its own process ' +
    'to adversarially review a research system — coupled by one authenticated ' +
    'call that fails soft.',
  body: [hero, body, section(ctaBand({
    eyebrow: 'Reliability engineering',
    title: 'Building something that has to be trusted?',
    lede: 'Evaluation harnesses, validation layers, failure detection and the architecture discipline that keeps them independent.',
    primary: ['/contact/', 'Discuss a project'],
    secondary: ['/work/aria/', 'Read the ARIA case study'],
  }), { cls: 'section--tight' })].join('\n'),
}
