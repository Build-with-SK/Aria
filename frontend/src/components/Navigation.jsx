import React, { useEffect, useState } from 'react'
import { NavLink, Link, useLocation } from 'react-router-dom'
import {
  Sparkles, Search, TrendingUp, PieChart, FlaskConical, FileText, Target,
  SlidersHorizontal, Settings as SettingsIcon, MoreHorizontal,
} from 'lucide-react'
import { useHealth, useSystemHealth } from '../hooks/useApi'
import { useSafety, safetyPills, pendingCount } from '../hooks/useSafety'
import Settings from './Settings'
import { useAuth } from '../auth/AuthContext'

/* ── navigation — EIGHT workspaces ──
   Was twelve, and twenty-two before that. The rule (§25) is that navigation
   names workspaces, not implementation. There is no Signals entry because
   signals are something Market knows; no Approval queue because approving is
   a step Portfolio contains; and no Live mind, because watching ARIA think is
   ARIA: Brain is the one destination, and chat, memory, the reasoning stream
   and vault knowledge are sections inside it.

   Daily report is a destination and Market is a destination, and they are
   deliberately not the same one: the report is the slow daily product, the
   live world feed is continuous.

   WHERE IT SITS. On a wide screen the eight live in one glass capsule in the
   top bar. On a phone the first four sit in a floating tab bar and the rest
   open from More, so the thumb reaches every workspace.                      */
/* `owner: true` means the destination is not merely refused for other people —
   it is not shown to them at all. A bar full of doors that answer 403 is a
   worse experience than a shorter bar, and it also stops advertising what
   exists on the other side. The server refuses these regardless; hiding them
   is courtesy, never the control. */
export const GROUPS = [
  {
    label: '',
    items: [
      { path: '/brain',        label: 'Brain',        icon: Sparkles },
      { path: '/research',     label: 'Research',     icon: Search },
      { path: '/market',       label: 'Market',       icon: TrendingUp },
      { path: '/portfolio',    label: 'Portfolio',    icon: PieChart, badge: true, owner: true },
      { path: '/strategies',   label: 'Strategies',   icon: FlaskConical },
      { path: '/daily-report', label: 'Daily report', icon: FileText, owner: true },
      { path: '/track-record', label: 'Track record', icon: Target, owner: true },
      { path: '/system',       label: 'System',       icon: SlidersHorizontal },
    ],
  },
]

/** Navigation as a given role should see it — groups that empty out disappear. */
export function groupsFor(isOwner) {
  if (isOwner) return GROUPS
  return GROUPS
    .map(g => ({ ...g, items: g.items.filter(i => !i.owner) }))
    .filter(g => g.items.length > 0)
}

/** ARIA's mark: three concentric rings. Inherits the text colour. */
export function Mark({ size = 24 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" fill="none" stroke="currentColor" aria-hidden="true"
      style={{ display: 'block', flex: 'none' }}>
      <circle cx="16" cy="16" r="13" strokeWidth="2.2" />
      <circle cx="16" cy="16" r="7.2" strokeWidth="1.8" opacity=".55" />
      <circle cx="16" cy="16" r="3" fill="currentColor" stroke="none" />
    </svg>
  )
}

/* The safety bar. Owner only: the routes behind it refuse everyone else.
   Each pill states one fact from the route that owns it, and says "unknown"
   when that route could not be read (hooks/useSafety.js). They are links to
   Portfolio, where the desk and the approval queue live; nothing here acts. */
function SafetyBar({ safety }) {
  return (
    <div className="aria-safety" role="status" aria-label="Trading safety state">
      {safetyPills(safety).map(p => (
        <Link key={p.key} to="/portfolio" className={`aria-pill ${p.tone}`} title={p.title}>
          <i aria-hidden="true" />{p.text}
        </Link>
      ))}
    </div>
  )
}

export default function Navigation() {
  const { data: health, loading: healthLoading } = useHealth()
  const { data: sysHealth } = useSystemHealth()
  const { owner } = useAuth()
  const loc = useLocation()
  const safety = useSafety(owner)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [moreOpen, setMoreOpen] = useState(false)
  // A navigation closes the More sheet.
  useEffect(() => { setMoreOpen(false) }, [loc.pathname])

  const items = groupsFor(owner).flatMap(g => g.items)
  const pending = pendingCount(safety.queue)
  // `/` and `/brain` are one destination, so the bar must not go dark on the
  // landing page.
  const isActive = path => loc.pathname === path || (path === '/brain' && loc.pathname === '/')

  // `/health` answers ok whenever the web server can answer, which says nothing
  // about whether the daemons behind it are running — this system already went
  // a week with a dead prediction loop and a green dot. So the dot reads the
  // worker registry, and says which of the two it actually knows.
  const apiUp = health?.status === 'ok'
  const workersOk = sysHealth?.workers?.healthy
  const status = !health && healthLoading ? { c: 'var(--ink-3)', t: 'Checking' }
    : !apiUp ? { c: 'var(--red)', t: 'Offline' }
    : workersOk === false ? { c: 'var(--yellow)', t: 'Degraded' }
    : workersOk === true ? { c: 'var(--green)', t: 'All systems running' }
    : { c: 'var(--green)', t: 'Online' }
  const statusTitle = workersOk === false ? `Degraded: ${(sysHealth?.workers?.degraded || []).join(', ')}`
    : workersOk === true ? 'The API answers and every worker is reporting.'
    : apiUp ? 'The API answers. Worker health was not read.' : undefined

  const openPalette = () => window.dispatchEvent(new CustomEvent('aria:palette'))
  const badgeFor = item => item.badge && pending > 0
    ? <span className="aria-badge" aria-label={`${pending} awaiting approval`}>{pending}</span> : null

  // Phone: four tabs and More, or every tab when five or fewer exist.
  const tabs = items.length <= 5 ? items : items.slice(0, 4)
  const overflow = items.length <= 5 ? [] : items.slice(4)
  const overflowActive = overflow.some(i => isActive(i.path))

  return (
    <>
      <header className="aria-top">
        <Link to="/brain" className="aria-brand" aria-label="ARIA home"><Mark size={24} />ARIA</Link>

        <nav className="aria-cap glass" aria-label="Workspaces">
          {items.map(item => {
            const Icon = item.icon
            return (
              <NavLink key={item.path} to={item.path} className={isActive(item.path) ? 'on' : ''} title={item.label}>
                <Icon size={16} strokeWidth={2} aria-hidden="true" />
                <span className="l">{item.label}</span>
                {badgeFor(item)}
              </NavLink>
            )
          })}
        </nav>

        <div className="aria-tools">
          <NavLink to="/system" className="aria-status" style={{ color: status.c }} title={statusTitle}>
            <i aria-hidden="true" /><span>{status.t}</span>
          </NavLink>
          {/* A shortcut nobody knows about is worth very little, so the bar
              shows the button; Ctrl K or ⌘K opens the same palette. */}
          <button className="aria-icon-btn" onClick={openPalette} aria-label="Search or jump to a ticker" title="Search (Ctrl K)">
            <Search size={19} strokeWidth={2} aria-hidden="true" />
          </button>
          <button className="aria-icon-btn" onClick={() => setSettingsOpen(true)} aria-label="Open settings" title="Settings">
            <SettingsIcon size={19} strokeWidth={2} aria-hidden="true" />
          </button>
        </div>
      </header>

      {owner && <SafetyBar safety={safety} />}

      {/* ── phone: floating tab bar ── */}
      <nav className="aria-tabbar glass" aria-label="Workspaces">
        {tabs.map(item => {
          const Icon = item.icon
          return (
            <NavLink key={item.path} to={item.path} className={isActive(item.path) ? 'on' : ''}>
              <Icon size={22} strokeWidth={1.9} aria-hidden="true" />
              {item.label}
              {badgeFor(item)}
            </NavLink>
          )
        })}
        {overflow.length > 0 && (
          <button onClick={() => setMoreOpen(o => !o)} className={overflowActive || moreOpen ? 'on' : ''}
            aria-expanded={moreOpen} aria-label="More workspaces">
            <MoreHorizontal size={22} strokeWidth={1.9} aria-hidden="true" />
            More
          </button>
        )}
      </nav>
      {moreOpen && (
        <>
          <div className="aria-scrim" onClick={() => setMoreOpen(false)} />
          <div className="aria-more sheet" role="dialog" aria-label="More workspaces">
            {overflow.map(item => {
              const Icon = item.icon
              return (
                <NavLink key={item.path} to={item.path} className={isActive(item.path) ? 'on' : ''}>
                  <Icon size={20} strokeWidth={1.9} aria-hidden="true" />{item.label}{badgeFor(item)}
                </NavLink>
              )
            })}
            <button onClick={() => { setMoreOpen(false); setSettingsOpen(true) }}>
              <SettingsIcon size={20} strokeWidth={1.9} aria-hidden="true" />Settings
            </button>
          </div>
        </>
      )}

      <Settings open={settingsOpen} onClose={() => setSettingsOpen(false)} />
    </>
  )
}
