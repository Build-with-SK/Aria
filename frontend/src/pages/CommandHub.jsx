/**
 * pages/CommandHub.jsx
 * The landing deck, with the two feeds that belonged to it.
 *
 * ALERTS (50 lines) and REPORT (51 lines) were read-only feeds sitting behind
 * their own nav entries while the command deck already summarised both. They
 * are tabs of the deck now.
 */
import React, { useState } from 'react'
import { TabBar } from '../components/UI'
import Overview from './Overview'
import Alerts from './Alerts'
import Report from './Report'

const TABS = [
  { id: 'deck', label: 'Command', icon: '⌂', C: Overview },
  { id: 'alerts', label: 'Alerts', icon: '▲', C: Alerts },
  { id: 'report', label: 'Daily report', icon: '≡', C: Report },
]

export default function CommandHub() {
  const [tab, setTab] = useState('deck')
  const Active = (TABS.find(t => t.id === tab) || TABS[0]).C
  return (
    <div>
      <TabBar tabs={TABS} active={tab} onChange={setTab} />
      <Active />
    </div>
  )
}
