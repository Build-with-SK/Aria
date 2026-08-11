/**
 * auth/ownerToken.js
 * How the browser proves it is the owner.
 *
 * WHY THIS EXISTS
 * ---------------
 * ARIA used to treat any request arriving from 127.0.0.1 as the owner, so
 * opening localhost simply gave you everything. That fallback was removed
 * because it is unsound behind a tunnel — cloudflared connects from localhost,
 * so every caller on earth arrives looking local — and the moment
 * ARIA_OWNER_TOKEN is set, the fallback switches off entirely.
 *
 * Correct, and it left the browser with no way to say who it was: no OAuth
 * provider is configured, so the sign-in page had nothing to offer, and the
 * owner saw the free-tier deck on his own machine.
 *
 * The backend already accepts `X-ARIA-Token` and treats a match as PROVEN
 * ownership (src/auth/policy.py, BASIS_TOKEN). Nothing on the server needs to
 * change or soften. This is the missing half: the browser holding that token
 * and presenting it on every call.
 *
 * WHY A FETCH PATCH RATHER THAN EDITING CALL SITES
 * ------------------------------------------------
 * Requests leave from `useApi.js`, from axios in several pages, and from a
 * handful of bare `fetch()` calls. Editing each one guarantees missing some,
 * and a half-authenticated app is exactly the confusing state this is meant to
 * end — some panels populate, others silently 401. One interceptor covers
 * every caller, including ones written later.
 *
 * ON STORAGE
 * ----------
 * localStorage, so a reload does not sign you out. That is readable by any
 * script running on this origin, which is an accepted risk here: the app is
 * single-user, served from localhost, and its CSP sets script-src 'self' with
 * no unsafe-eval. If ARIA is ever exposed to other people, this should become
 * a server-issued httpOnly session cookie instead — see clearOwnerToken().
 */

const KEY = 'aria.owner.token'

export function getOwnerToken() {
  try {
    return localStorage.getItem(KEY) || ''
  } catch {
    return ''            // private mode, or storage disabled
  }
}

export function setOwnerToken(token) {
  const t = (token || '').trim()
  try {
    if (t) localStorage.setItem(KEY, t)
    else localStorage.removeItem(KEY)
  } catch {
    /* storage unavailable — the token still applies for this page's lifetime */
  }
  applyToAxios(t)
  return t
}

export function clearOwnerToken() {
  setOwnerToken('')
}

export function hasOwnerToken() {
  return Boolean(getOwnerToken())
}

/** Same-origin API calls only. Never attach the token to a third party. */
function isOurApi(url) {
  try {
    const u = new URL(url, window.location.origin)
    if (u.origin !== window.location.origin) return false
    return u.pathname.startsWith('/api/') || u.pathname === '/health'
  } catch {
    return false
  }
}

function applyToAxios(token) {
  const axios = window.__ariaAxios
  if (!axios) return
  if (token) axios.defaults.headers.common['X-ARIA-Token'] = token
  else delete axios.defaults.headers.common['X-ARIA-Token']
}

/**
 * Attach the token to every same-origin API request, whoever sent it.
 * Idempotent: installing twice does not double-wrap.
 */
export function installOwnerAuth(axios) {
  if (axios && !window.__ariaAxios) {
    window.__ariaAxios = axios
    applyToAxios(getOwnerToken())
  }

  if (window.__ariaFetchPatched) return
  window.__ariaFetchPatched = true

  const original = window.fetch.bind(window)
  window.fetch = (input, init = {}) => {
    const url = typeof input === 'string' ? input : input?.url
    const token = getOwnerToken()
    if (!token || !isOurApi(url)) return original(input, init)

    // Headers can arrive as a Headers instance, a plain object, or an array.
    const headers = new Headers(
      (init && init.headers) || (input instanceof Request ? input.headers : undefined)
    )
    headers.set('X-ARIA-Token', token)
    return original(input, { ...init, headers })
  }
}
