// Execute the worker in a Node VM; never registers a worker or opens a server.
import assert from 'node:assert/strict'
import { readFileSync, existsSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { runInNewContext } from 'node:vm'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const read = file => readFileSync(join(root, file), 'utf8')
assert.ok(!/fonts\.googleapis\.com|fonts\.gstatic\.com/.test(read('src/index.css') + read('index.html')))
assert.ok(read('src/main.jsx').includes("register(import.meta.env.BASE_URL + 'sw.js', { scope: import.meta.env.BASE_URL })"))
assert.ok(read('index.html').includes('href="%BASE_URL%manifest.webmanifest"'))
const manifest = JSON.parse(read('public/manifest.webmanifest'))
const base = 'https://aria.invalid/app/'
for (const path of [manifest.start_url, manifest.scope, ...manifest.icons.map(icon => icon.src)]) {
  const url = new URL(path, new URL('manifest.webmanifest', base))
  assert.ok(url.href.startsWith(base), `Out-of-scope manifest path: ${path}`)
}
for (const icon of manifest.icons) assert.ok(existsSync(join(root, 'public', icon.src)))

const handlers = {}, added = [], deleted = [], puts = [], reads = []
const shell = { synthetic: 'shell' }, resource = { ok: true, type: 'basic', clone: () => resource }
const cache = { add: async url => added.push(url), put: async (req, res) => puts.push([req.url, res]) }
const context = {
  URL, Response, self: { registration: { scope: base }, location: { origin: new URL(base).origin },
    addEventListener: (name, fn) => { handlers[name] = fn }, skipWaiting: async () => {}, clients: { claim: async () => {} } },
  caches: { open: async () => cache, keys: async () => ['aria-shell-v1', 'aria-shell-v2', 'other-application'],
    delete: async name => deleted.push(name), match: async key => { reads.push(key); return key === `${base}index.html` ? shell : null } },
  fetch: async req => { if (req.mode === 'navigate') throw new Error('Synthetic offline'); return resource },
}
runInNewContext(read('public/sw.js'), context)
let pending
handlers.install({ waitUntil: promise => { pending = promise } }); await pending
assert.deepEqual(added, ['', 'index.html', 'manifest.webmanifest', 'icons/icon-192.png'].map(path => base + path))
handlers.activate({ waitUntil: promise => { pending = promise } }); await pending
assert.deepEqual(deleted, ['aria-shell-v1'])
for (const [url, method] of [['/api/portfolio', 'GET'], ['/api/auth/session', 'GET'], ['/health', 'GET'], ['/ready', 'GET'], ['/auth', 'GET'], ['/app/assets/a.js', 'POST'], ['https://other.invalid/app/a.js', 'GET']]) {
  const before = reads.length
  let intercepted = false
  handlers.fetch({ request: { url: new URL(url, base).href, method }, respondWith: () => { intercepted = true } })
  assert.equal(intercepted, false, `Worker intercepted ${method} ${url}`)
  assert.equal(reads.length, before, `Worker read cache for ${url}`)
}
handlers.fetch({ request: { url: `${base}market`, method: 'GET', mode: 'navigate' }, respondWith: promise => { pending = promise } })
assert.equal(await pending, shell)
handlers.fetch({ request: { url: `${base}assets/synthetic.js`, method: 'GET' }, respondWith: promise => { pending = promise } })
assert.equal(await pending, resource)
await Promise.resolve()
assert.equal(puts.length, 1)
assert.equal(puts[0][0], `${base}assets/synthetic.js`)

// Optionally verify the staging build's concrete public URLs too.
if (process.argv[2]) {
  const staging = resolve(process.argv[2])
  const html = readFileSync(join(staging, 'index.html'), 'utf8')
  assert.ok(html.includes('href="/app/manifest.webmanifest"'))
  assert.ok(html.includes('href="/app/icons/apple-touch-icon.png"'))
  for (const path of ['sw.js', 'manifest.webmanifest', 'icons/icon-192.png']) assert.ok(existsSync(join(staging, path)))
  assert.equal(readFileSync(join(staging, 'sw.js'), 'utf8'), read('public/sw.js'))
}
console.log('PASS: local fonts, /app/ registration + manifest + shell URLs, scoped cache cleanup, API/auth/health bypass, offline navigation and static asset caching.')
