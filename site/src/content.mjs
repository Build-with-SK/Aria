/**
 * site/src/content.mjs
 * ====================
 * Single source of truth for everything the site asserts.
 *
 * THE RULE THIS FILE EXISTS TO ENFORCE
 * ------------------------------------
 * Every number below is traceable to a file in this repository, and the trace
 * is written next to it. If a claim cannot carry a `src` note, it does not
 * belong here — a portfolio that invents a metric is worth less than one that
 * has none, because the reader cannot tell which half to trust.
 *
 * Counts were taken on 2026-08-27 with:
 *   git ls-files 'src/*.py'   | xargs wc -l      → 58,590
 *   git ls-files 'tests/*.py' | xargs wc -l      → 12,127
 *   ls tests/test_*.py | wc -l                   → 60
 *   grep -cE '@app\.(get|post|put|delete)' backend/main.py → 166
 *   grep -hoE '@module\("[a-z0-9_]+"' src/v5/modules/*.py | sort -u | wc -l → 41
 */

/* ── Identity ─────────────────────────────────────────────────────────────── */

export const SITE = {
  name: 'Soundariyan Karunakaran',
  short: 'Soundariyan',
  role: 'Finance · Quantitative Research · Engineering',
  location: 'London, United Kingdom',
  // Set this to the real domain before the first deploy. It is used for
  // canonical URLs, Open Graph and the sitemap — a wrong value here is the
  // one SEO mistake that is invisible in a browser.
  origin: 'https://example.com', // ← PLACEHOLDER: replace with your domain
  tagline: 'Finance professional who builds the systems.',
  description:
    'MSc International Corporate Finance (Distinction). I build financial ' +
    'research and decision-support systems in Python — market data pipelines, ' +
    'quantitative research engines, evaluation harnesses and AI reasoning layers.',
}

export const LINKS = {
  linkedin: 'https://www.linkedin.com/in/soundariyan-karunakaran-363064205',
  github: 'https://github.com/Build-with-SK',
  repo: 'https://github.com/Build-with-SK/Aria',
  // ← PLACEHOLDER: swap in the address you want publicly listed. Left generic
  //   on purpose so a personal inbox is not published without a decision.
  email: 'your.email@example.com',
}

/** Rendered wherever an unresolved placeholder would otherwise ship silently. */
export const PLACEHOLDERS = ['SITE.origin', 'LINKS.email']

/* ── Credentials — each independently verifiable by the reader ─────────────── */

export const CREDENTIALS = [
  {
    label: 'MSc International Corporate Finance',
    detail: 'Distinction',
    meta: 'United Kingdom',
  },
  {
    label: 'Bloomberg Global Trading Challenge 2025',
    detail: 'Captained The Sharpe Syndicate — 122nd of 2,394 teams globally, 34th in Europe',
    meta: 'Top 5% worldwide',
  },
  {
    label: 'ARIA — Founder & Lead Engineer',
    detail: 'Financial research and decision-support platform, built solo',
    meta: 'Jun 2026 – present',
  },
]

/* ── Verified engineering facts ───────────────────────────────────────────── */
/* `src` is the file a reader can open to check the claim.                     */

export const FACTS = {
  srcLines: { value: '58,590', label: 'lines of Python', src: 'src/' },
  testLines: { value: '12,127', label: 'lines of tests', src: 'tests/' },
  testModules: { value: '60', label: 'test modules', src: 'tests/' },
  endpoints: { value: '166', label: 'HTTP endpoints', src: 'backend/main.py' },
  modules: { value: '41', label: 'research modules', src: 'src/v5/modules/' },
  families: { value: '7', label: 'module families', src: 'src/v5/registry.py' },
  symbols: { value: '21,067', label: 'symbols indexed', src: 'src/core/identity.py' },
  exchanges: { value: '40', label: 'exchanges', src: 'src/data/world_symbols.py' },
  frontendLines: { value: '11,639', label: 'lines of React', src: 'frontend/src/' },
  strategies: { value: '16', label: 'strategy engines', src: 'src/strategies/' },
}

/** The four numbers the home page leads with. */
export const HEADLINE_FACTS = [
  FACTS.srcLines,
  FACTS.testModules,
  FACTS.modules,
  FACTS.symbols,
]

/* ── The hero pipeline ────────────────────────────────────────────────────── */

export const PIPELINE = [
  { id: 'data', label: 'Market data', note: 'OHLCV · fundamentals · macro · news' },
  { id: 'research', label: 'Research', note: '41 modules, 7 families' },
  { id: 'models', label: 'Models', note: 'LightGBM · HMM · Kalman · PCA' },
  { id: 'reasoning', label: 'Reasoning', note: 'LLM synthesis, not forecasting' },
  { id: 'validation', label: 'Validation', note: 'Calibration · baselines · tiers' },
  { id: 'decision', label: 'Decision', note: 'Human approval, enforced in code' },
]

/* ── Projects ─────────────────────────────────────────────────────────────── */

export const PROJECTS = [
  {
    slug: 'aria',
    name: 'ARIA',
    expansion: 'Adaptive Reasoning & Intelligence Architecture',
    kicker: 'Flagship',
    status: 'Active development · self-hosted',
    summary:
      'A financial research and decision-support platform. Market data across ' +
      '21,067 symbols feeds 41 independent research modules; their outputs are ' +
      'combined, scored against naive baselines, and gated behind a code-enforced ' +
      'human-approval step before anything reaches a broker.',
    role: 'Sole architect and engineer',
    stack: ['Python', 'FastAPI', 'React', 'Vite', 'LightGBM', 'ChromaDB', 'Ollama', 'Anthropic API'],
    metrics: [FACTS.modules, FACTS.symbols, FACTS.endpoints, FACTS.testModules],
    href: '/work/aria/',
  },
  {
    slug: 'sentinel',
    name: 'SENTINEL',
    expansion: 'Independent reliability and evaluation architecture',
    kicker: 'Infrastructure',
    status: 'Operational bridge · internal',
    summary:
      'A separate intelligence running in its own process, with its own memory ' +
      'and its own permissions. ARIA consults it to have a thesis attacked before ' +
      'acting on it. The coupling is one authenticated HTTP call that fails soft — ' +
      'if SENTINEL is unreachable, ARIA carries on and records that no second ' +
      'opinion was obtained.',
    role: 'Architecture and bridge contract',
    stack: ['Python', 'HTTP/JSON', 'Token auth', 'Deterministic gating'],
    metrics: [],
    href: '/work/sentinel/',
  },
]

/* ── Services ─────────────────────────────────────────────────────────────── */

export const SERVICES = [
  {
    id: 'analysis',
    n: '01',
    title: 'Financial analysis',
    lede: 'Reading a business from its statements and saying what the numbers mean.',
    items: [
      'Financial statement analysis',
      'Company and sector deep-dives',
      'Ratio and trend analysis',
      'Peer and comparable analysis',
      'Written research notes',
    ],
  },
  {
    id: 'modelling',
    n: '02',
    title: 'Financial modelling',
    lede: 'Models that a third party can audit — assumptions visible, logic traceable.',
    items: [
      'Three-statement and operating models',
      'DCF and relative valuation',
      'Forecasting and driver build-ups',
      'Scenario and sensitivity analysis',
      'Excel dashboards and model review',
    ],
  },
  {
    id: 'python',
    n: '03',
    title: 'Python & data analysis',
    lede: 'The work that stops being manual once it is written down as code.',
    items: [
      'Market and financial data pipelines',
      'Data cleaning and reconciliation',
      'Statistical analysis and backtesting',
      'Reporting automation',
      'Visualisation and analytics tooling',
    ],
  },
  {
    id: 'ai',
    n: '04',
    title: 'AI & automation',
    lede: 'LLM workflows built with evaluation attached, not vibes.',
    items: [
      'Research and document-processing workflows',
      'Retrieval over your own corpus',
      'Internal business process automation',
      'Evaluation harnesses for AI output',
      'Prototype-to-production hardening',
    ],
  },
  {
    id: 'systems',
    n: '05',
    title: 'Research systems',
    lede: 'End-to-end decision-support software, from ingestion to the interface.',
    items: [
      'Financial dashboards and research terminals',
      'Quantitative research infrastructure',
      'Backtesting and evaluation harnesses',
      'FastAPI services and internal APIs',
      'Data architecture and scheduling',
    ],
  },
]

export const ENGAGEMENTS = [
  {
    title: 'Defined deliverable',
    lede: 'A model, an analysis, a dashboard, a pipeline.',
    detail: 'Fixed scope agreed up front, fixed price, a written brief before any work starts.',
  },
  {
    title: 'Build engagement',
    lede: 'A system, delivered in reviewable increments.',
    detail: 'Weekly or milestone-based. You see working software early and often, not at the end.',
  },
  {
    title: 'Advisory',
    lede: 'A second pair of eyes on quantitative or architectural work.',
    detail: 'Model review, research design critique, or a read on whether an approach will hold.',
  },
]

/* ── Technical stack — only what this repository actually contains ────────── */

export const STACK = [
  {
    title: 'Languages',
    items: ['Python', 'JavaScript', 'SQL', 'HTML / CSS', 'Bash · PowerShell'],
  },
  {
    title: 'Data & ML',
    items: ['pandas', 'NumPy', 'LightGBM', 'scikit-learn', 'Hidden Markov models', 'Kalman filters', 'PCA'],
  },
  {
    title: 'AI',
    items: ['Anthropic Claude API', 'Ollama (local inference)', 'ChromaDB', 'sentence-transformers', 'Retrieval pipelines', 'LLM evaluation'],
  },
  {
    title: 'Backend',
    items: ['FastAPI', 'APScheduler', 'SQLite', 'REST API design', 'OAuth · session auth', 'pytest'],
  },
  {
    title: 'Frontend',
    items: ['React', 'Vite', 'Recharts', 'Design systems', 'Accessibility', 'Progressive web apps'],
  },
  {
    title: 'Finance',
    items: ['Valuation & modelling', 'Quantitative research', 'Backtesting & walk-forward', 'Risk and position sizing', 'Options & Black-Scholes', 'Macro and regime analysis'],
  },
]

/* ── Compliance ───────────────────────────────────────────────────────────── */

export const DISCLAIMER =
  'I am not a licensed financial adviser and provide no personalised investment ' +
  'advice, portfolio management or brokerage. ARIA is research and ' +
  'decision-support software; its outputs are probabilistic and are not ' +
  'recommendations to buy or sell any security.'
