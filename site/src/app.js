/**
 * site/src/app.js
 * ===============
 * Progressive enhancement only. Every page is complete without this file —
 * it adds a theme toggle, a mobile menu and a scroll reveal, and nothing else.
 * If it fails to load, the site still reads correctly.
 */
;(function () {
  'use strict'

  var root = document.documentElement
  var reduced = window.matchMedia('(prefers-reduced-motion: reduce)')

  /* ── Theme ──────────────────────────────────────────────────────────────
     Three states, not two: 'light', 'dark', and no stored preference at all,
     which lets the OS decide. Cycling only between the two explicit values
     would strand a visitor who had never chosen. */

  var toggle = document.getElementById('theme-toggle')

  function systemIsDark() {
    return window.matchMedia('(prefers-color-scheme: dark)').matches
  }

  function paintToggle() {
    if (!toggle) return
    var dark = root.dataset.theme ? root.dataset.theme === 'dark' : systemIsDark()
    var moon = toggle.querySelector('[data-theme-icon="dark"]')
    var sun = toggle.querySelector('[data-theme-icon="light"]')
    if (moon) moon.hidden = !dark
    if (sun) sun.hidden = dark
    toggle.setAttribute(
      'aria-label',
      dark ? 'Switch to light theme' : 'Switch to dark theme'
    )
  }

  if (toggle) {
    toggle.addEventListener('click', function () {
      var dark = root.dataset.theme ? root.dataset.theme === 'dark' : systemIsDark()
      var next = dark ? 'light' : 'dark'
      root.dataset.theme = next
      try {
        localStorage.setItem('theme', next)
      } catch (e) {
        /* private browsing, blocked storage — the choice just will not persist */
      }
      paintToggle()
    })
    paintToggle()
  }

  /* ── Mobile navigation ─────────────────────────────────────────────────── */

  var navToggle = document.getElementById('nav-toggle')
  var navLinks = document.getElementById('nav-links')

  function setNav(open) {
    if (!navToggle || !navLinks) return
    navLinks.dataset.open = String(open)
    navToggle.setAttribute('aria-expanded', String(open))
    navToggle.setAttribute('aria-label', open ? 'Close menu' : 'Open menu')
    var openIcon = navToggle.querySelector('[data-menu-icon="open"]')
    var closeIcon = navToggle.querySelector('[data-menu-icon="close"]')
    if (openIcon) openIcon.hidden = open
    if (closeIcon) closeIcon.hidden = !open
  }

  if (navToggle && navLinks) {
    navToggle.addEventListener('click', function () {
      setNav(navLinks.dataset.open !== 'true')
    })
    // Escape closes it and returns focus to the control that opened it — the
    // menu is a fixed overlay, so leaving it open with focus inside traps a
    // keyboard user behind content they cannot see.
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && navLinks.dataset.open === 'true') {
        setNav(false)
        navToggle.focus()
      }
    })
    navLinks.addEventListener('click', function (e) {
      if (e.target.closest('a')) setNav(false)
    })
    window.addEventListener('resize', function () {
      if (window.innerWidth > 880 && navLinks.dataset.open === 'true') setNav(false)
    })
  }

  /* ── Reveal on scroll ───────────────────────────────────────────────────
     One observer, unobserved after firing. No scroll listener, no layout
     thrash, and nothing running once the page has settled. */

  var targets = document.querySelectorAll('.reveal')
  window.__revealReady = true

  // A document that is hidden at load gets no reveal at all. Transitions are
  // suspended in a background tab, so the elements marked visible here would
  // sit at opacity 0 until something forces a repaint — the one failure mode
  // this whole arrangement exists to avoid.
  if (
    reduced.matches ||
    !('IntersectionObserver' in window) ||
    document.visibilityState === 'hidden'
  ) {
    root.classList.remove('js-reveal')
  } else {
    var io = new IntersectionObserver(
      function (entries) {
        for (var i = 0; i < entries.length; i++) {
          if (entries[i].isIntersecting) {
            entries[i].target.classList.add('is-in')
            io.unobserve(entries[i].target)
          }
        }
      },
      { rootMargin: '0px 0px -8% 0px', threshold: 0.08 }
    )
    for (var j = 0; j < targets.length; j++) {
      // Anything already on screen at load appears immediately rather than
      // animating in behind the fold-line the user is already looking at.
      var r = targets[j].getBoundingClientRect()
      if (r.top < window.innerHeight * 0.92) targets[j].classList.add('is-in')
      else io.observe(targets[j])
    }
  }

  /* ── Pause the hero animation when it is not on screen ──────────────────
     stroke-dashoffset is not compositor-accelerated, so the pulse repaints its
     region every frame for as long as it runs. Cheap, but there is no reason to
     spend it while the diagram is scrolled out of view. */

  var pipe = document.querySelector('.pipeline')
  if (pipe && 'IntersectionObserver' in window && !reduced.matches) {
    var animated = pipe.querySelectorAll('.pipe-pulse, .pipe-glint')
    var pause = new IntersectionObserver(
      function (entries) {
        var on = entries[0].isIntersecting ? 'running' : 'paused'
        for (var i = 0; i < animated.length; i++) {
          animated[i].style.animationPlayState = on
        }
      },
      { threshold: 0 }
    )
    pause.observe(pipe)
  }

  /* ── Section highlighting inside a long case study ──────────────────────
     Only runs where a contents rail exists. */

  var rail = document.querySelector('[data-toc]')
  if (rail && 'IntersectionObserver' in window) {
    var railLinks = Array.prototype.slice.call(rail.querySelectorAll('a[href^="#"]'))
    var sections = railLinks
      .map(function (a) {
        return document.getElementById(a.getAttribute('href').slice(1))
      })
      .filter(Boolean)

    var spy = new IntersectionObserver(
      function (entries) {
        entries.forEach(function (entry) {
          if (!entry.isIntersecting) return
          railLinks.forEach(function (a) {
            var on = a.getAttribute('href') === '#' + entry.target.id
            a.setAttribute('aria-current', on ? 'true' : 'false')
          })
        })
      },
      { rootMargin: '-12% 0px -70% 0px' }
    )
    sections.forEach(function (s) {
      spy.observe(s)
    })
  }
})()
