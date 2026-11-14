// The safety bar never turns a failed or refused read into a reassuring word.
// Pure function, no server, no browser.  Run: node scripts/verify-safety-pills.mjs
import assert from 'node:assert/strict'
import { safetyPills, pendingCount, UNKNOWN } from '../src/hooks/useSafety.js'

const known = data => ({ known: true, data })
const text = pills => pills.map(p => p.text).join(' | ')
const pill = (pills, key) => pills.find(p => p.key === key)
// Words that claim a fact. None may appear when the fact was not read.
const REASSURING = /\b(off|released|paper|live|armed|engaged|no approvals|awaiting)\b/i

// 1. Nothing could be read (network down, 401, 403, timeout, first paint).
for (const state of [{}, { desk: UNKNOWN, kill: UNKNOWN, queue: UNKNOWN }, undefined]) {
  const pills = safetyPills(state)
  assert.deepEqual(pills.map(p => p.key), ['account', 'kill', 'auto', 'queue'])
  for (const p of pills) {
    assert.equal(p.tone, 'warn', `${p.key}: an unread fact is a warning`)
    assert.match(p.text, /unknown/i, `${p.key}: an unread fact says unknown`)
    assert.ok(!REASSURING.test(p.text), `${p.key}: "${p.text}" claims a fact that was not read`)
    assert.ok(p.title && p.title.length > 20, `${p.key}: the pill explains itself`)
  }
}

// 2. A reply that arrived but does not carry the field is still unknown.
const hollow = safetyPills({ desk: known({}), kill: known({}), queue: known({}) })
assert.match(text(hollow), /Account unknown.*Kill switch unknown.*Auto-execute unknown.*Approvals unknown/)
for (const bad of [null, [], 'ok', { gates: null }, { config: {} }, { config: { auto_execute: 'false' } }, { config: { auto_execute: 0 } }]) {
  assert.equal(pill(safetyPills({ desk: known(bad) }), 'auto').text, 'Auto-execute unknown', JSON.stringify(bad))
}
for (const bad of [null, {}, { engaged: 'false' }, { engaged: 0 }, { engaged: null }]) {
  assert.equal(pill(safetyPills({ kill: known(bad) }), 'kill').text, 'Kill switch unknown', JSON.stringify(bad))
}
for (const bad of [null, {}, { count: '0' }, { count: -1 }, { count: 1.5 }, { count: null }, { trades: [] }]) {
  assert.equal(pendingCount(known(bad)), null, JSON.stringify(bad))
  assert.equal(pill(safetyPills({ queue: known(bad) }), 'queue').text, 'Approvals unknown', JSON.stringify(bad))
}

// 3. Auto-execute: armed and off only from a real boolean.
const desk = (auto, gates) => known({ config: { auto_execute: auto }, gates })
assert.deepEqual([pill(safetyPills({ desk: desk(true, {}) }), 'auto').text, pill(safetyPills({ desk: desk(true, {}) }), 'auto').tone],
  ['Auto-execute armed', 'warn'])
assert.deepEqual([pill(safetyPills({ desk: desk(false, {}) }), 'auto').text, pill(safetyPills({ desk: desk(false, {}) }), 'auto').tone],
  ['Auto-execute off', ''])

// 4. Account: the broker's own word wins; the server setting is worded as a setting.
const account = gates => safetyPills({ desk: desk(false, gates) })
assert.deepEqual([pill(account({ account: 'paper', env_paper: true }), 'account').text, pill(account({ account: 'paper', env_paper: true }), 'account').tone],
  ['Paper account', 'info'])
// A live broker is never softened by a paper setting.
for (const env of [true, false, undefined]) {
  const p = pill(account({ account: 'live', env_paper: env }), 'account')
  assert.deepEqual([p.text, p.tone], ['Live account', 'bad'])
}
const disconnected = account({ account: 'disconnected', env_paper: true, broker_connected: false })
assert.deepEqual([pill(disconnected, 'account').text, pill(disconnected, 'broker').text, pill(disconnected, 'broker').tone],
  ['Paper mode', 'Broker disconnected', 'warn'])
assert.ok(!/Paper account/.test(text(disconnected)), 'a setting is not a confirmed account')
for (const env of [false, undefined, null, 'true']) {
  const p = pill(account({ account: 'disconnected', env_paper: env }), 'account')
  assert.deepEqual([p.text, p.tone], [env === false ? 'Paper mode not set' : 'Account unknown', 'warn'], String(env))
}

// An account value the page does not know is not treated as "disconnected".
for (const odd of [{}, { account: 'sandbox' }, { account: null, env_paper: true }]) {
  const pills = account(odd)
  assert.equal(pill(pills, 'account').text, 'Account unknown', JSON.stringify(odd))
  assert.equal(pill(pills, 'broker'), undefined, 'no broker claim without the broker field')
}

// 5. Kill switch.
const engaged = pill(safetyPills({ kill: known({ engaged: true, reason: 'drawdown limit' }) }), 'kill')
assert.deepEqual([engaged.text, engaged.tone], ['Kill switch engaged', 'bad'])
assert.match(engaged.title, /drawdown limit/)
const released = pill(safetyPills({ kill: known({ engaged: false }) }), 'kill')
assert.deepEqual([released.text, released.tone], ['Kill switch released', ''])

// 6. Approval queue.
assert.equal(pendingCount(known({ count: 0 })), 0)
assert.deepEqual([pill(safetyPills({ queue: known({ count: 0 }) }), 'queue').text, pill(safetyPills({ queue: known({ count: 0 }) }), 'queue').tone],
  ['No approvals waiting', ''])
assert.deepEqual([pill(safetyPills({ queue: known({ count: 3 }) }), 'queue').text, pill(safetyPills({ queue: known({ count: 3 }) }), 'queue').tone],
  ['3 awaiting approval', 'warn'])

// 7. One fact failing never hides or changes the others.
const mixed = safetyPills({ desk: UNKNOWN, kill: known({ engaged: true }), queue: known({ count: 2 }) })
assert.match(text(mixed), /Account unknown \| Kill switch engaged \| Auto-execute unknown \| 2 awaiting approval/)

console.log('PASS: safety pills say "unknown" for every unread, refused or malformed fact; a live broker is never softened; armed, engaged and waiting approvals are flagged.')
