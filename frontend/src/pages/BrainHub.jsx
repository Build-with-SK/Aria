/**
 * pages/BrainHub.jsx
 * The brain, and watching the brain think — one subject, two views.
 *
 * AI BRAIN configured the daemon; LIVE MIND streamed the same daemon's phases.
 * Splitting them across two nav entries meant a visitor had to already know
 * they were the same thing to find the live view.
 */
import React, { useState } from 'react'
import { TabBar } from '../components/UI'
import Brain from './Brain'
import Thinking from './Thinking'

const TABS = [
  { id: 'brain', label: 'Brain', icon: '◈', C: Brain },
  { id: 'live', label: 'Live mind', icon: '✦', C: Thinking },
]

export default function BrainHub() {
  const [tab, setTab] = useState('brain')
  const Active = (TABS.find(t => t.id === tab) || TABS[0]).C
  return (
    <div>
      <TabBar tabs={TABS} active={tab} onChange={setTab} />
      <Active />
    </div>
  )
}
