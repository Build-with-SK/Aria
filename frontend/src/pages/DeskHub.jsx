/**
 * pages/DeskHub.jsx
 * The desk and its approval queue.
 *
 * Approval is a STEP in the desk's workflow, not a separate destination: the
 * desk proposes, the human approves, the broker fills. Having them in different
 * places made it possible to run the desk without ever noticing the queue.
 *
 * The safety contract is unchanged and still enforced in code, not here:
 * auto-execute is paper-only behind the env gate, and nothing on either tab can
 * place an order that a human has not approved.
 */
import React, { useEffect, useState } from 'react'
import axios from 'axios'
import { TabBar } from '../components/UI'
import Desk from './Desk'
import Execution from './Execution'

export default function DeskHub() {
  const [tab, setTab] = useState('desk')
  const [pending, setPending] = useState(0)

  // Surface the queue depth on the tab itself — a trade waiting on a human is
  // the one thing on this page that is time-sensitive.
  useEffect(() => {
    const poll = () => axios.get('/api/execute/queue?status=pending')
      .then(r => setPending(r.data?.count || 0)).catch(() => {})
    poll()
    const id = setInterval(poll, 20000)
    return () => clearInterval(id)
  }, [])

  const tabs = [
    { id: 'desk', label: 'The desk', icon: '▦' },
    { id: 'queue', label: pending ? `Approval queue (${pending})` : 'Approval queue', icon: '▶' },
  ]

  return (
    <div>
      <TabBar tabs={tabs} active={tab} onChange={setTab} />
      {tab === 'desk' ? <Desk /> : <Execution />}
    </div>
  )
}
