/**
 * pages/Markets.jsx
 * One market surface instead of four one-hook pages.
 *
 * Signals, Macro, Futures and Options were each a single read-only table behind
 * its own nav entry — 39 to 62 lines apiece. They answer the same question
 * ("what does the book look like right now?") at different slices, so they are
 * tabs, not destinations. Each tab renders the original page component
 * unchanged; nothing about their behaviour moved.
 */
import React, { useState } from 'react'
import { TabBar } from '../components/UI'
import Signals from './Signals'
import Macro from './Macro'
import Futures from './Futures'
import Options from './Options'

const TABS = [
  { id: 'signals', label: 'Signals', icon: '∿', C: Signals },
  { id: 'macro', label: 'Macro', icon: '⊕', C: Macro },
  { id: 'futures', label: 'Futures', icon: '◆', C: Futures },
  { id: 'options', label: 'Options', icon: '◇', C: Options },
]

export default function Markets() {
  const [tab, setTab] = useState('signals')
  const Active = (TABS.find(t => t.id === tab) || TABS[0]).C
  return (
    <div>
      <TabBar tabs={TABS} active={tab} onChange={setTab} />
      <Active />
    </div>
  )
}
