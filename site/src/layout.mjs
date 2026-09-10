/**
 * site/src/layout.mjs
 * ===================
 * The page shell: head, navigation, footer, structured data.
 *
 * Every page is composed at build time, so the content a crawler receives is
 * the content a reader receives. There is no client-side render step to wait
 * on and nothing important behind JavaScript.
 */

import { SITE, LINKS, DISCLAIMER } from './content.mjs'

/* Dev-only instrumentation. A production build must never carry it, so the
   switch lives here rather than in a comment someone forgets to uncomment. */
const DEV = process.env.SITE_DEV === '1'

/* ── Small helpers ────────────────────────────────────────────────────────── */

/** Escape for interpolation into HTML text or a double-quoted attribute. */
export const esc = (s) =>
  String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')

export const NAV = [
  { href: '/work/', label: 'Work' },
  { href: '/work/aria/', label: 'ARIA' },
  { href: '/work/sentinel/', label: 'SENTINEL' },
  { href: '/services/', label: 'Services' },
  { href: '/about/', label: 'About' },
]

/* ── Icons (inline, so there is no icon font and no second request) ───────── */

export const ICONS = {
  arrow: '<svg class="arrow" width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M3 8h10M9 4l4 4-4 4" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  mail: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" aria-hidden="true"><rect x="2.75" y="4.75" width="18.5" height="14.5" rx="2" stroke="currentColor" stroke-width="1.5"/><path d="M3.5 6.5l8.5 6 8.5-6" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>',
  linkedin: '<svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M4.98 3.5a2.5 2.5 0 100 5 2.5 2.5 0 000-5zM3 9.75h4V21H3V9.75zM9.5 9.75h3.83v1.54h.05c.53-1 1.84-2.06 3.78-2.06 4.04 0 4.79 2.66 4.79 6.12V21h-4v-4.94c0-1.18-.02-2.7-1.64-2.7-1.65 0-1.9 1.29-1.9 2.61V21h-4V9.75z"/></svg>',
  github: '<svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M12 2C6.48 2 2 6.58 2 12.25c0 4.53 2.87 8.37 6.84 9.73.5.09.68-.22.68-.49l-.01-1.72c-2.78.62-3.37-1.37-3.37-1.37-.46-1.19-1.11-1.5-1.11-1.5-.91-.64.07-.62.07-.62 1 .07 1.53 1.05 1.53 1.05.89 1.570 2.34 1.12 2.91.86.09-.66.35-1.12.63-1.38-2.22-.26-4.56-1.14-4.56-5.07 0-1.12.39-2.03 1.03-2.75-.1-.26-.45-1.3.1-2.71 0 0 .84-.28 2.75 1.05a9.3 9.3 0 015 0c1.91-1.33 2.75-1.05 2.75-1.05.55 1.41.2 2.45.1 2.71.64.72 1.03 1.63 1.03 2.75 0 3.94-2.34 4.81-4.57 5.06.36.32.68.94.68 1.9l-.01 2.82c0 .27.18.59.69.49A10.06 10.06 0 0022 12.25C22 6.58 17.52 2 12 2z"/></svg>',
  sun: '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" aria-hidden="true"><circle cx="12" cy="12" r="4.25" stroke="currentColor" stroke-width="1.6"/><path d="M12 2v2.5M12 19.5V22M2 12h2.5M19.5 12H22M4.9 4.9l1.8 1.8M17.3 17.3l1.8 1.8M19.1 4.9l-1.8 1.8M6.7 17.3l-1.8 1.8" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>',
  moon: '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M20 14.2A8.2 8.2 0 019.8 4a8.5 8.5 0 100 20 8.5 8.5 0 0010.2-9.8z" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/></svg>',
  menu: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M3.5 7h17M3.5 12h17M3.5 17h17" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg>',
  close: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg>',
}

/** The brand mark: a target reticle — measurement, not a robot. */
const MARK = `<svg class="brand__mark" viewBox="0 0 32 32" fill="none" aria-hidden="true">
  <circle cx="16" cy="16" r="13" stroke="currentColor" stroke-width="1.4" opacity=".28"/>
  <circle cx="16" cy="16" r="7.5" stroke="var(--accent)" stroke-width="1.4"/>
  <circle cx="16" cy="16" r="2.6" fill="var(--accent)"/>
  <path d="M16 0v6M16 26v6M0 16h6M26 16h6" stroke="currentColor" stroke-width="1.4" opacity=".28"/>
</svg>`

/* ── Header ───────────────────────────────────────────────────────────────── */

function header(current) {
  const links = NAV.map((n) => {
    const active = current === n.href ? ' aria-current="page"' : ''
    return `<a class="nav__link" href="${n.href}"${active}>${esc(n.label)}</a>`
  }).join('\n            ')

  return `<header class="site-header">
      <div class="container">
        <nav class="nav" aria-label="Primary">
          <a class="brand" href="/" aria-label="${esc(SITE.name)} — home">
            ${MARK}
            <span class="brand__name">${esc(SITE.short)}</span>
            <span class="brand__sub" aria-hidden="true">Karunakaran</span>
          </a>
          <div class="nav__links" id="nav-links" data-open="false">
            ${links}
            <a class="nav__link" href="/contact/">Contact</a>
          </div>
          <div class="nav__actions">
            <a class="btn btn--primary nav__cta" href="/contact/">Work with me ${ICONS.arrow}</a>
            <button class="icon-btn" id="theme-toggle" type="button"
                    aria-label="Switch colour theme" title="Switch colour theme">
              <span data-theme-icon="dark">${ICONS.moon}</span>
              <span data-theme-icon="light" hidden>${ICONS.sun}</span>
            </button>
            <button class="icon-btn nav__toggle" id="nav-toggle" type="button"
                    aria-expanded="false" aria-controls="nav-links" aria-label="Open menu">
              <span data-menu-icon="open">${ICONS.menu}</span>
              <span data-menu-icon="close" hidden>${ICONS.close}</span>
            </button>
          </div>
        </nav>
      </div>
    </header>`
}

/* ── Footer ───────────────────────────────────────────────────────────────── */

function footer() {
  const year = new Date().getFullYear()
  return `<footer class="site-footer">
      <div class="container">
        <div class="footer__grid">
          <div>
            <a class="brand" href="/" style="margin-bottom:var(--s3)">
              ${MARK}<span class="brand__name">${esc(SITE.short)}</span>
            </a>
            <p class="dim" style="font-size:var(--t-sm);max-width:34ch">
              ${esc(SITE.description.split('.')[0])}.
            </p>
          </div>
          <div>
            <p class="footer__title">Work</p>
            <ul class="footer__list">
              <li><a href="/work/">All case studies</a></li>
              <li><a href="/work/aria/">ARIA</a></li>
              <li><a href="/work/sentinel/">SENTINEL</a></li>
            </ul>
          </div>
          <div>
            <p class="footer__title">Engage</p>
            <ul class="footer__list">
              <li><a href="/services/">Services</a></li>
              <li><a href="/about/">About</a></li>
              <li><a href="/about/#stack">Technical stack</a></li>
              <li><a href="/contact/">Contact</a></li>
            </ul>
          </div>
          <div>
            <p class="footer__title">Elsewhere</p>
            <ul class="footer__list">
              <li><a href="${LINKS.linkedin}" rel="me noopener" target="_blank">LinkedIn</a></li>
              <li><a href="${LINKS.github}" rel="me noopener" target="_blank">GitHub</a></li>
              <li><a href="${LINKS.repo}" rel="noopener" target="_blank">ARIA repository</a></li>
            </ul>
          </div>
        </div>
        <p class="disclaimer">${esc(DISCLAIMER)}</p>
        <div class="footer__bottom">
          <span>© ${year} ${esc(SITE.name)}</span>
          <span class="mono">${esc(SITE.location)}</span>
        </div>
      </div>
    </footer>`
}

/* ── Structured data ──────────────────────────────────────────────────────── */

function jsonLd(page) {
  const person = {
    '@type': 'Person',
    '@id': `${SITE.origin}/#person`,
    name: SITE.name,
    jobTitle: 'Financial Analyst & Systems Engineer',
    description: SITE.description,
    url: `${SITE.origin}/`,
    sameAs: [LINKS.linkedin, LINKS.github],
    address: { '@type': 'PostalAddress', addressLocality: 'London', addressCountry: 'GB' },
    alumniOf: {
      '@type': 'EducationalOrganization',
      name: 'MSc International Corporate Finance',
    },
    knowsAbout: [
      'Financial analysis', 'Financial modelling', 'Quantitative research',
      'Python', 'Data analysis', 'Machine learning', 'Financial technology',
      'Market data', 'Research automation',
    ],
  }

  const graph = [person, {
    '@type': 'WebPage',
    '@id': `${SITE.origin}${page.path}#page`,
    url: `${SITE.origin}${page.path}`,
    name: page.title,
    description: page.description,
    isPartOf: { '@type': 'WebSite', url: `${SITE.origin}/`, name: SITE.name },
    about: { '@id': `${SITE.origin}/#person` },
  }]

  if (page.schema) graph.push(page.schema)

  return `<script type="application/ld+json">${JSON.stringify({
    '@context': 'https://schema.org',
    '@graph': graph,
  })}</script>`
}

/* ── Open Graph card, generated as an SVG data URI ────────────────────────── */
/* Kept inline so the site has no binary assets to optimise or forget to ship. */

export function ogCard() {
  return `<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="630" viewBox="0 0 1200 630">
  <rect width="1200" height="630" fill="#08090c"/>
  <g stroke="#1e222b" stroke-width="1">
    ${Array.from({ length: 18 }, (_, i) => `<path d="M${i * 68} 0V630"/>`).join('')}
    ${Array.from({ length: 10 }, (_, i) => `<path d="M0 ${i * 68}H1200"/>`).join('')}
  </g>
  <circle cx="96" cy="92" r="26" fill="none" stroke="#2b313c" stroke-width="2.5"/>
  <circle cx="96" cy="92" r="15" fill="none" stroke="#ff9130" stroke-width="2.5"/>
  <circle cx="96" cy="92" r="5" fill="#ff9130"/>
  <text x="146" y="100" font-family="Inter,Helvetica,Arial,sans-serif" font-size="26" font-weight="600" fill="#e8ebf1">${esc(SITE.name)}</text>
  <text x="96" y="286" font-family="Inter,Helvetica,Arial,sans-serif" font-size="74" font-weight="600" letter-spacing="-2.5" fill="#e8ebf1">Finance. Research.</text>
  <text x="96" y="372" font-family="Inter,Helvetica,Arial,sans-serif" font-size="74" font-weight="600" letter-spacing="-2.5" fill="#ff9130">Engineering.</text>
  <path d="M96 428H1104" stroke="#1e222b" stroke-width="1"/>
  <text x="96" y="478" font-family="monospace" font-size="21" letter-spacing="3" fill="#6d7583">MARKET DATA → RESEARCH → MODELS → REASONING → VALIDATION</text>
  <text x="96" y="546" font-family="Inter,Helvetica,Arial,sans-serif" font-size="24" fill="#a3abb9">MSc International Corporate Finance · London, UK</text>
</svg>`
}

/* ── Page shell ───────────────────────────────────────────────────────────── */

/**
 * @param {object} page
 *   path        canonical path, e.g. '/work/aria/'
 *   title       <title> and og:title
 *   description meta description, ~150-160 chars
 *   body        page HTML
 *   schema      optional extra JSON-LD node
 */
export function render(page) {
  const canonical = `${SITE.origin}${page.path}`
  // PNG, not the SVG it was rendered from: LinkedIn, X and Facebook all decline
  // to render an SVG og:image, and LinkedIn is the surface that matters most
  // here. og.svg stays as the editable source — see the README to regenerate.
  const ogImage = `${SITE.origin}/og.png`

  return `<!doctype html>
<html lang="en-GB" data-page="${esc(page.path)}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>${esc(page.title)}</title>
<meta name="description" content="${esc(page.description)}">
${page.noindex ? '' : `<link rel="canonical" href="${esc(canonical)}">`}
<meta name="author" content="${esc(SITE.name)}">
<meta name="robots" content="${page.noindex ? 'noindex, follow' : 'index, follow, max-image-preview:large'}">
<meta name="theme-color" content="#08090c" media="(prefers-color-scheme: dark)">
<meta name="theme-color" content="#fbfbfc" media="(prefers-color-scheme: light)">

<meta property="og:type" content="${page.path === '/' ? 'website' : 'article'}">
<meta property="og:site_name" content="${esc(SITE.name)}">
<meta property="og:title" content="${esc(page.title)}">
<meta property="og:description" content="${esc(page.description)}">
<meta property="og:url" content="${esc(canonical)}">
<meta property="og:image" content="${esc(ogImage)}">
<meta property="og:locale" content="en_GB">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="${esc(page.title)}">
<meta name="twitter:description" content="${esc(page.description)}">
<meta name="twitter:image" content="${esc(ogImage)}">

<link rel="icon" href="/favicon.svg" type="image/svg+xml">
<link rel="apple-touch-icon" href="/favicon.svg">

<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="/assets/design.css">
<link rel="stylesheet" media="print" onload="this.media='all'"
      href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@400;500;600&display=swap">
<noscript><link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@400;500;600&display=swap"></noscript>

<script>
/* Applied before first paint so a chosen theme never flashes the other one. */
try {
  var t = localStorage.getItem('theme');
  if (t === 'light' || t === 'dark') document.documentElement.dataset.theme = t;
} catch (e) {}
/* Arm the scroll reveal only where it can actually complete, and withdraw it if
   app.js has not reported in — a page that stays blank because a script failed
   is a far worse failure than one that simply does not animate. */
(function () {
  var d = document.documentElement;
  if (window.matchMedia && 'IntersectionObserver' in window &&
      !matchMedia('(prefers-reduced-motion: reduce)').matches) {
    d.classList.add('js-reveal');
    setTimeout(function () {
      if (!window.__revealReady) d.classList.remove('js-reveal');
    }, 1500);
  }
})();
</script>
${jsonLd(page)}
</head>
<body>
<a class="skip-link" href="#main">Skip to content</a>
${header(page.path)}
<main id="main" tabindex="-1">
${page.body}
</main>
${footer()}
<script src="/assets/app.js" defer></script>
${DEV ? '<script src="/__audit.js" defer data-dev-only></script>' : ''}
</body>
</html>
`
}
