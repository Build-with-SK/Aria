/**
 * hooks/useSafety.js
 * The four facts the owner has to be able to trust at a glance: which account
 * the desk trades, whether the kill switch is engaged, whether auto-execute is
 * armed, and how many trades are waiting for approval.
 *
 * ONE RULE: a read that failed, timed out or was refused is UNKNOWN. It is
 * never shown as "off", "released", "paper" or "0". A page once told the owner
 * auto-execute was OFF while the desk was armed, because a failed fetch fell
 * through to the falsy branch. `safetyPills` below is a pure function so that
 * rule can be checked offline (scripts/verify-safety-pills.mjs).
 *
 * Nothing here changes anything: these are GETs, and the pills are links.
 */
import { useEffect, useState } from 'react'
import axios from 'axios'

export const UNKNOWN = Object.freeze({ known: false, data: null })

function usePolled(url, ms, enabled) {
  const [state, setState] = useState(UNKNOWN)
  useEffect(() => {
    if (!enabled) { setState(UNKNOWN); return }
    let alive = true
    const load = () => axios.get(url, { timeout: 8000 })
      .then(r => { if (alive) setState({ known: true, data: r.data }) })
      .catch(() => { if (alive) setState(UNKNOWN) })
    load()
    const id = setInterval(load, ms)
    return () => { alive = false; clearInterval(id) }
  }, [url, ms, enabled])
  return state
}

/** Owner only: these routes refuse everyone else, so nobody else polls them. */
export function useSafety(owner) {
  const desk = usePolled('/api/desk/status', 15000, owner)
  const kill = usePolled('/api/execute/kill-switch', 15000, owner)
  const queue = usePolled('/api/execute/queue?status=pending', 5000, owner)
  return { desk, kill, queue }
}

/** Pending approvals as a number, or null when the queue could not be read. */
export function pendingCount(queue) {
  const n = queue?.known ? queue.data?.count : null
  return Number.isInteger(n) && n >= 0 ? n : null
}

/**
 * What to show, in order. tone: info | ok | warn | bad | '' (neutral).
 * Every branch that cannot confirm a fact says "unknown" in the warn tone.
 */
export function safetyPills({ desk = UNKNOWN, kill = UNKNOWN, queue = UNKNOWN } = {}) {
  const pills = []
  const gates = desk.known && desk.data && typeof desk.data.gates === 'object' ? desk.data.gates : null

  // 1. Which account. `account` is what the broker itself reports; `env_paper`
  //    is only the server's setting, so it is worded as a setting.
  if (!gates || !['live', 'paper', 'disconnected'].includes(gates.account)) {
    pills.push({ key: 'account', tone: 'warn', text: 'Account unknown',
      title: 'The desk status could not be read, so paper or live cannot be confirmed.' })
  } else if (gates.account === 'live') {
    pills.push({ key: 'account', tone: 'bad', text: 'Live account',
      title: 'The broker reports a live account. Auto-execute never trades it; every order needs your approval.' })
  } else if (gates.account === 'paper') {
    pills.push({ key: 'account', tone: 'info', text: 'Paper account',
      title: 'The broker reports a paper account.' })
  } else {
    pills.push(gates.env_paper === true
      ? { key: 'account', tone: 'info', text: 'Paper mode',
          title: 'The server is set to paper trading. The broker is not connected, so the account itself is not confirmed.' }
      : gates.env_paper === false
        ? { key: 'account', tone: 'warn', text: 'Paper mode not set',
            title: 'The server is not set to paper trading, and the broker is not connected.' }
        : { key: 'account', tone: 'warn', text: 'Account unknown',
            title: 'The broker is disconnected and the server paper setting is unavailable.' })
    pills.push({ key: 'broker', tone: 'warn', text: 'Broker disconnected',
      title: 'No broker session. Nothing can be sent, and positions cannot be read.' })
  }

  // 2. Kill switch.
  const engaged = kill.known && kill.data ? kill.data.engaged : null
  if (typeof engaged !== 'boolean') {
    pills.push({ key: 'kill', tone: 'warn', text: 'Kill switch unknown',
      title: 'The kill switch state could not be read. The switch itself is read from disk by the broker adapters and still applies.' })
  } else if (engaged) {
    pills.push({ key: 'kill', tone: 'bad', text: 'Kill switch engaged',
      title: `Trading is halted${kill.data.reason ? `: ${kill.data.reason}` : ''}.` })
  } else {
    pills.push({ key: 'kill', tone: '', text: 'Kill switch released', title: 'The emergency stop is not engaged.' })
  }

  // 3. Auto-execute.
  const armed = desk.known && desk.data && desk.data.config ? desk.data.config.auto_execute : null
  if (typeof armed !== 'boolean') {
    pills.push({ key: 'auto', tone: 'warn', text: 'Auto-execute unknown',
      title: 'The desk status could not be read, so whether auto-execute is armed is unknown.' })
  } else if (armed) {
    pills.push({ key: 'auto', tone: 'warn', text: 'Auto-execute armed',
      title: 'The desk may place paper trades on its own. You are told after, not asked.' })
  } else {
    pills.push({ key: 'auto', tone: '', text: 'Auto-execute off', title: 'Every trade waits for your approval.' })
  }

  // 4. Approval queue.
  const n = pendingCount(queue)
  if (n === null) {
    pills.push({ key: 'queue', tone: 'warn', text: 'Approvals unknown',
      title: 'The approval queue could not be read.' })
  } else if (n > 0) {
    pills.push({ key: 'queue', tone: 'warn', text: `${n} awaiting approval`,
      title: 'Trades proposed and waiting for you. Nothing is sent until you approve.' })
  } else {
    pills.push({ key: 'queue', tone: '', text: 'No approvals waiting', title: 'The approval queue is empty.' })
  }
  return pills
}
