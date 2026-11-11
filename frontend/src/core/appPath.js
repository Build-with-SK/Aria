/**
 * core/appPath.js
 * Full-page addresses inside the app.
 *
 * The app is served under vite's base (/app/). The router adds that prefix for
 * its own links, but `window.location.href = ...` is a full page load and does
 * not: '/login' or '/' lands on the server root, which has no page. Every
 * full-page jump inside the app goes through appUrl().
 */

const viteBase = () => (typeof import.meta !== 'undefined' && import.meta.env?.BASE_URL) || '/'

const trim = base => (base || '/').replace(/\/+$/, '')

/** '/brain' -> '/app/brain'; '/' -> '/app/'. */
export function appUrl(path = '/', base = viteBase()) {
  const prefix = trim(base)
  const p = path.startsWith('/') ? path : `/${path}`
  // A path that already carries the prefix is not prefixed twice.
  if (prefix && (p === prefix || p.startsWith(`${prefix}/`))) return p === prefix ? `${p}/` : p
  return `${prefix}${p}`
}

/**
 * The page to open after sign-in, from the login page's ?next=.
 * Only a path inside this app is accepted. Anything else (another site,
 * '//host', '/\host', a scheme) becomes the command deck, so a crafted link
 * cannot send the owner somewhere else straight after they sign in.
 */
export function nextAfterLogin(raw, base = viteBase()) {
  const next = typeof raw === 'string' ? raw : ''
  const inside = next.startsWith('/') && !next.startsWith('//') && !next.startsWith('/\\')
    && !/[\u0000-\u001f]/.test(next)
  return appUrl(inside ? next : '/', base)
}
