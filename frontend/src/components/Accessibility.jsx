/**
 * components/Accessibility.jsx
 * Display preferences: appearance, text size, contrast and motion.
 *
 * Saved on the device and applied to <html> as data attributes, which the
 * tokens in index.css read. The controls themselves live in Settings.
 *
 *   Appearance   light, dark, or whatever the device is set to
 *   Text size    scales the entire interface (zoom, so inline px scales too)
 *   Contrast     darkens secondary text and outlines, makes panels solid
 *   Motion       stops the signal strip, pulses and transitions
 *
 * Defaults follow the operating system where it says something — a viewer who
 * has already asked their OS for reduced motion or more contrast should not
 * have to ask again here.
 *
 * (The control that used to sit in the sidebar was removed with the sidebar;
 * nothing imported it once Settings took the controls over.)
 */
import { useEffect } from 'react'

const KEY = 'aria-a11y'

function osDefaults() {
  const q = (m) => { try { return window.matchMedia(m).matches } catch { return false } }
  return {
    theme: 'system',
    textSize: 'normal',
    contrast: q('(prefers-contrast: more)') ? 'high' : 'normal',
    motion: q('(prefers-reduced-motion: reduce)') ? 'reduced' : 'normal',
  }
}

export function loadA11y() {
  const base = osDefaults()
  try {
    const saved = JSON.parse(localStorage.getItem(KEY) || '{}')
    return { ...base, ...saved }
  } catch {
    return base
  }
}

export function applyA11y(s) {
  const el = document.documentElement
  // No attribute means "follow the device": the dark tokens are then chosen by
  // the prefers-color-scheme rule alone.
  if (s.theme === 'light' || s.theme === 'dark') el.setAttribute('data-theme', s.theme)
  else el.removeAttribute('data-theme')
  el.setAttribute('data-text-size', s.textSize || 'normal')
  el.setAttribute('data-contrast', s.contrast || 'normal')
  el.setAttribute('data-motion', s.motion || 'normal')
  const dark = s.theme === 'dark' || (s.theme !== 'light' && window.matchMedia('(prefers-color-scheme: dark)').matches)
  document.querySelector('meta[name="theme-color"]')?.setAttribute('content', dark ? '#0c101a' : '#f3f6fb')
}

/** Applies saved settings on first paint, before anything renders. */
export function useA11yBoot() {
  useEffect(() => { applyA11y(loadA11y()) }, [])
}
