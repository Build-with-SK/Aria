#!/usr/bin/env node
/**
 * site/build.mjs
 * ==============
 * A static site generator with no dependencies.
 *
 * WHY NOT A FRAMEWORK
 * -------------------
 * This site is nine pages of content and three interactions. Shipping React to
 * render it would cost ~45 KB of JavaScript before a single word appeared, and
 * would move the content out of the HTML that crawlers and no-JS readers get.
 * Node's standard library does the whole job in one file, and the output is a
 * plain folder of HTML that any static host will serve.
 *
 *   node build.mjs            build once into dist/
 *   node build.mjs --serve    build, serve on :4321, rebuild on change
 *   node build.mjs --check    build into memory and fail on broken links
 */

import { readFile, writeFile, mkdir, rm, readdir, stat } from 'node:fs/promises'
import { createReadStream, watch } from 'node:fs'
import { spawn } from 'node:child_process'
import { createServer } from 'node:http'
import { dirname, extname, join, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
const SRC = join(HERE, 'src')
const DIST = join(HERE, 'dist')
const PUBLIC = join(HERE, 'public')

const args = new Set(process.argv.slice(2))
const SERVE = args.has('--serve')
const CHECK = args.has('--check')
// Set before any page module is imported, because layout.mjs reads it at load.
if (SERVE) process.env.SITE_DEV = '1'
const DEV = process.env.SITE_DEV === '1'

const PAGE_FILES = [
  'home.mjs',
  'work.mjs',
  'aria.mjs',
  'sentinel.mjs',
  'services.mjs',
  'about.mjs',
  'contact.mjs',
]

/* ── Assets generated rather than stored ──────────────────────────────────── */

const FAVICON = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">
  <rect width="32" height="32" rx="6" fill="#08090c"/>
  <circle cx="16" cy="16" r="10.5" fill="none" stroke="#2b313c" stroke-width="1.6"/>
  <circle cx="16" cy="16" r="6" fill="none" stroke="#ff9130" stroke-width="1.8"/>
  <circle cx="16" cy="16" r="2.2" fill="#ff9130"/>
</svg>`

const ROBOTS = (origin) => `User-agent: *
Allow: /

Sitemap: ${origin}/sitemap.xml
`

const sitemap = (origin, paths) => `<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
${paths
  .map(
    (p) => `  <url>
    <loc>${origin}${p}</loc>
    <changefreq>monthly</changefreq>
    <priority>${p === '/' ? '1.0' : p.split('/').length > 3 ? '0.7' : '0.8'}</priority>
  </url>`
  )
  .join('\n')}
</urlset>
`

/* Long-cache the fingerprint-free assets modestly; HTML must always revalidate
   or a redeploy leaves visitors on the previous copy. */
const HEADERS = `/assets/*
  Cache-Control: public, max-age=3600, must-revalidate
  X-Content-Type-Options: nosniff

/*
  X-Content-Type-Options: nosniff
  Referrer-Policy: strict-origin-when-cross-origin
  X-Frame-Options: DENY
  Permissions-Policy: geolocation=(), microphone=(), camera=(), interest-cohort=()
`

/* ── Build ────────────────────────────────────────────────────────────────── */

async function loadPages() {
  const pages = []
  for (const file of PAGE_FILES) {
    const mod = await import(pathToFileURL(join(SRC, 'pages', file)).href)
    pages.push(mod.default)
  }
  return pages
}

async function copyDir(from, to) {
  let entries
  try {
    entries = await readdir(from, { withFileTypes: true })
  } catch {
    return // public/ is optional
  }
  await mkdir(to, { recursive: true })
  for (const e of entries) {
    const src = join(from, e.name)
    const dst = join(to, e.name)
    if (e.name === '__audit.js' && !DEV) continue // dev instrumentation only
    if (e.isDirectory()) await copyDir(src, dst)
    else await writeFile(dst, await readFile(src))
  }
}

async function build() {
  const started = Date.now()

  const { render, ogCard } = await import(pathToFileURL(join(SRC, 'layout.mjs')).href)
  const { SITE, PLACEHOLDERS } = await import(pathToFileURL(join(SRC, 'content.mjs')).href)

  const pages = await loadPages()

  await rm(DIST, { recursive: true, force: true })
  await mkdir(join(DIST, 'assets'), { recursive: true })

  const written = []
  for (const page of pages) {
    const out =
      page.path === '/'
        ? join(DIST, 'index.html')
        : join(DIST, page.path.replace(/^\/|\/$/g, ''), 'index.html')
    await mkdir(dirname(out), { recursive: true })
    const html = render(page)
    await writeFile(out, html, 'utf8')
    written.push({ page, html, out })
  }

  // 404 reuses the home shell so a mistyped URL still gets navigation.
  const notFound = render({
    path: '/404.html',
    noindex: true,
    title: 'Page not found — ' + SITE.name,
    description: 'That page does not exist.',
    body: `<section class="section"><div class="container container--narrow" style="text-align:center">
      <p class="eyebrow" style="justify-content:center">404</p>
      <h1 style="margin-top:var(--s4)">That page does not exist.</h1>
      <p class="lede" style="margin:var(--s4) auto 0">The link may be out of date. The work is all reachable from here.</p>
      <div class="btn-row" style="justify-content:center">
        <a class="btn btn--primary" href="/">Home</a>
        <a class="btn btn--ghost" href="/work/">Case studies</a>
      </div>
    </div></section>`,
  })
  await writeFile(join(DIST, '404.html'), notFound, 'utf8')

  await writeFile(join(DIST, 'assets', 'design.css'), await readFile(join(SRC, 'design.css')))
  await writeFile(join(DIST, 'assets', 'app.js'), await readFile(join(SRC, 'app.js')))
  await writeFile(join(DIST, 'favicon.svg'), FAVICON, 'utf8')
  await writeFile(join(DIST, 'og.svg'), ogCard(), 'utf8')
  await writeFile(join(DIST, 'robots.txt'), ROBOTS(SITE.origin), 'utf8')
  await writeFile(
    join(DIST, 'sitemap.xml'),
    sitemap(SITE.origin, pages.map((p) => p.path)),
    'utf8'
  )
  await writeFile(join(DIST, '_headers'), HEADERS, 'utf8')
  await copyDir(PUBLIC, DIST)

  const report = audit(written, pages)
  const ms = Date.now() - started

  console.log(`\n  built ${written.length} pages in ${ms}ms → site/dist`)
  for (const { page, html } of written) {
    console.log(
      `    ${page.path.padEnd(18)} ${String(Math.round(html.length / 1024)).padStart(3)} KB  ${page.title.slice(0, 52)}`
    )
  }

  if (SITE.origin.includes('example.com') || PLACEHOLDERS.length) {
    console.log(`\n  placeholders still to fill: ${PLACEHOLDERS.join(', ')}`)
  }

  if (report.length) {
    console.log('\n  problems:')
    report.forEach((p) => console.log('    ! ' + p))
    if (CHECK) process.exitCode = 1
  } else {
    console.log('\n  link check: clean\n')
  }

  return written
}

/* ── Link + content audit ─────────────────────────────────────────────────── */

function audit(written, pages) {
  const problems = []
  const known = new Set(pages.map((p) => p.path))
  known.add('/404.html')

  const anchors = new Map()
  for (const { page, html } of written) {
    const ids = [...html.matchAll(/\sid="([^"]+)"/g)].map((m) => m[1])
    anchors.set(page.path, new Set(ids))
  }

  for (const { page, html } of written) {
    for (const m of html.matchAll(/href="([^"]+)"/g)) {
      const href = m[1]
      if (/^(https?:|mailto:|tel:|#|data:)/.test(href)) {
        if (href.startsWith('#')) {
          const id = href.slice(1)
          if (id && !anchors.get(page.path).has(id)) {
            problems.push(`${page.path} → missing anchor ${href}`)
          }
        }
        continue
      }
      const [path, hash] = href.split('#')
      if (path.startsWith('/assets/') || /\.(svg|xml|txt|png|ico|pdf)$/.test(path)) continue
      if (!known.has(path)) {
        problems.push(`${page.path} → dead internal link ${href}`)
      } else if (hash && !anchors.get(path)?.has(hash)) {
        problems.push(`${page.path} → ${path} has no anchor #${hash}`)
      }
    }

    // Metadata guards. These are the mistakes that are invisible in a browser.
    const desc = html.match(/<meta name="description" content="([^"]*)"/)?.[1] || ''
    // The 404 is noindex, so its description is never a search snippet.
    if (!page.noindex && (desc.length < 70 || desc.length > 185)) {
      problems.push(`${page.path} → description is ${desc.length} chars (want 70–185)`)
    }
    const h1s = [...html.matchAll(/<h1[\s>]/g)].length
    if (h1s !== 1) problems.push(`${page.path} → ${h1s} <h1> elements (want exactly 1)`)
    if (/PLACEHOLDER/.test(html)) problems.push(`${page.path} → the word PLACEHOLDER is rendered`)
    if (!DEV && /__audit/.test(html)) problems.push(`${page.path} → dev instrumentation leaked into a production build`)
  }
  return problems
}

/* ── Dev server ───────────────────────────────────────────────────────────── */

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.xml': 'application/xml',
  '.txt': 'text/plain; charset=utf-8',
  '.json': 'application/json',
  '.png': 'image/png',
  '.pdf': 'application/pdf',
}

async function serve(port = 4321) {
  const server = createServer(async (req, res) => {
    const url = decodeURIComponent((req.url || '/').split('?')[0])
    let file = join(DIST, url)
    try {
      const s = await stat(file)
      if (s.isDirectory()) file = join(file, 'index.html')
    } catch {
      try {
        await stat(join(file, 'index.html'))
        file = join(file, 'index.html')
      } catch {
        file = join(DIST, '404.html')
        res.statusCode = 404
      }
    }
    if (!resolve(file).startsWith(resolve(DIST))) {
      res.statusCode = 403
      return res.end('forbidden')
    }
    res.setHeader('Content-Type', MIME[extname(file)] || 'application/octet-stream')
    res.setHeader('Cache-Control', 'no-store')
    createReadStream(file)
      .on('error', () => {
        res.statusCode = 404
        res.end('not found')
      })
      .pipe(res)
  })

  server.listen(port, () => console.log(`\n  serving  http://localhost:${port}\n`))

  // Rebuild in a fresh process. Node's ESM module cache cannot be invalidated,
  // and query-string busting only reloads the leaf module — an edit to
  // components.mjs would keep serving the previously imported copy.
  let pending = null
  let running = false
  watch(SRC, { recursive: true }, () => {
    clearTimeout(pending)
    pending = setTimeout(() => {
      if (running) return
      running = true
      const child = spawn(process.execPath, [fileURLToPath(import.meta.url)], {
        stdio: 'inherit',
        env: { ...process.env, SITE_DEV: '1' },
      })
      child.on('exit', () => { running = false })
    }, 90)
  })
}

/* ── Entry ────────────────────────────────────────────────────────────────── */

await build()
if (SERVE) await serve()
