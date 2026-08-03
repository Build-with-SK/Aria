/**
 * pages/LabHub.jsx
 * The research lab and its results.
 *
 * QUANT LAB reads arXiv q-fin papers, maps them to strategy templates and
 * backtests them; BACKTEST displayed backtest equity curves. Same subject, and
 * the lab already runs the backtests — so the results are a tab of the lab
 * rather than a separate page that happens to read the same file.
 *
 * Research only: nothing on either tab places a trade.
 */
import React, { useState } from 'react'
import { TabBar } from '../components/UI'
import QuantLab from './QuantLab'
import Backtest from './Backtest'

const TABS = [
  { id: 'lab', label: 'Quant lab', icon: '⚗', C: QuantLab },
  { id: 'backtest', label: 'Backtests', icon: '▷', C: Backtest },
]

export default function LabHub() {
  const [tab, setTab] = useState('lab')
  const Active = (TABS.find(t => t.id === tab) || TABS[0]).C
  return (
    <div>
      <TabBar tabs={TABS} active={tab} onChange={setTab} />
      <Active />
    </div>
  )
}
