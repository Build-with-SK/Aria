// Full-page addresses keep the /app base, and sign-in never redirects off-site.
// Run: node scripts/verify-app-path.mjs
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { appUrl, nextAfterLogin } from '../src/core/appPath.js'

const B = '/app/'
assert.equal(appUrl('/', B), '/app/')
assert.equal(appUrl('/login', B), '/app/login')
assert.equal(appUrl('/brain?x=1', B), '/app/brain?x=1')
assert.equal(appUrl('login', B), '/app/login')
assert.equal(appUrl('/app', B), '/app/')
assert.equal(appUrl('/app/brain', B), '/app/brain')
assert.equal(appUrl('/application', B), '/app/application')
assert.equal(appUrl('/login', '/'), '/login')        // served at root
assert.equal(appUrl('/', '/'), '/')

assert.equal(nextAfterLogin('/', B), '/app/')         // the bug: was '/', the server root
assert.equal(nextAfterLogin('/brain', B), '/app/brain')
assert.equal(nextAfterLogin('/market?t=AAPL', B), '/app/market?t=AAPL')
for (const bad of ['https://evil.example', '//evil.example', '/\\evil.example', 'javascript:alert(1)',
                   'evil.example', '/x\n//evil', '', null, undefined, 42]) {
  assert.equal(nextAfterLogin(bad, B), '/app/', `refused: ${String(bad)}`)
}

// No full-page jump inside the app may name a bare root path again.
const src = resolve(dirname(fileURLToPath(import.meta.url)), '..', 'src')
for (const rel of ['pages/Login.jsx', 'auth/AuthContext.jsx', 'components/ErrorBoundary.jsx']) {
  const text = readFileSync(join(src, rel), 'utf8')
  assert.ok(!/location\.href\s*=\s*['"`]\/(?!app\/)/.test(text), `${rel} jumps to a path without /app`)
}
console.log('PASS: appUrl keeps the /app base; nextAfterLogin refuses off-site and malformed targets')
