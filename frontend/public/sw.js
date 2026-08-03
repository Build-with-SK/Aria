/**
 * sw.js — ARIA's service worker.
 *
 * IT CACHES THE SHELL AND NOTHING ELSE.
 *
 * That restriction is the whole design. Two things make caching this app's
 * responses actively dangerous rather than merely wasteful:
 *
 *   1. STALE PRICES ARE WORSE THAN NO PRICES. A cached quote looks exactly like
 *      a live one. Someone reading a five-minute-old price as current, on a
 *      screen that gives no hint of it, is a worse outcome than an error.
 *
 *   2. CACHED API RESPONSES LEAK BETWEEN ACCOUNTS. The Cache API is per-origin,
 *      not per-user. Store one person's portfolio and the next person to sign
 *      in on that device can be served it — the request never reaches the
 *      server, so none of the access control we built gets a say.
 *
 * So: /api/* is network-only, always, with no fallback. If the network is down
 * the request fails, the app says so, and nobody is shown a number that is not
 * true right now.
 */

const VERSION = 'aria-shell-v1'
const SHELL = ['/', '/index.html', '/manifest.webmanifest', '/icons/icon-192.png']

self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(VERSION)
      // addAll is atomic: one 404 discards the whole cache. Individual puts
      // let the shell install even if one optional asset is missing.
      .then(c => Promise.allSettled(SHELL.map(u => c.add(u))))
      .then(() => self.skipWaiting())
  )
})

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(k => k !== VERSION).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  )
})

self.addEventListener('fetch', event => {
  const req = event.request
  const url = new URL(req.url)

  // Anything that is not a plain same-origin GET is none of our business.
  if (req.method !== 'GET' || url.origin !== self.location.origin) return

  // Never touch the API, auth or health. No cache read, no cache write, no
  // offline fallback — see the header comment.
  if (url.pathname.startsWith('/api/') || url.pathname === '/health') return

  // SPA navigations: try the network so a deploy is picked up immediately,
  // fall back to the cached shell only when genuinely offline.
  if (req.mode === 'navigate') {
    event.respondWith(
      fetch(req).catch(() => caches.match('/index.html').then(r => r || Response.error()))
    )
    return
  }

  // Static assets are content-hashed by Vite, so a hit is always correct and a
  // miss is worth caching for next time.
  event.respondWith(
    caches.match(req).then(hit => hit || fetch(req).then(res => {
      if (res && res.ok && res.type === 'basic') {
        const copy = res.clone()
        caches.open(VERSION).then(c => c.put(req, copy)).catch(() => {})
      }
      return res
    }))
  )
})
