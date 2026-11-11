import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import App from './App'
import { CurrencyProvider } from './currency/CurrencyContext'
import { AuthProvider } from './auth/AuthContext'
import { installStepUp } from './auth/StepUp'
import { applyA11y, loadA11y } from './components/Accessibility'
import './index.css'

// Apply text size / contrast / motion before the first paint, so the UI
// never flashes at the default size for someone who needs it larger.
applyA11y(loadA11y())

// NOTHING IS INSTALLED HERE ANY MORE, AND THAT IS THE POINT.
//
// This used to call installOwnerAuth(axios), which read the raw
// ARIA_OWNER_TOKEN out of localStorage and monkey-patched window.fetch to
// attach it to every same-origin API call. It worked, and it meant one XSS on
// this origin handed an attacker a permanent credential that opens the broker
// routes — with no expiry and no way to revoke it.
//
// The browser now holds no credential at all. Signing in posts the owner
// credential once to /api/auth/login and gets back an HttpOnly session cookie
// the page cannot read, which the browser attaches by itself. There is nothing
// left for a script on this page to steal.
//
// One thing IS installed: the step-up interceptor. A few actions (approving a
// trade, releasing the kill switch, arming auto-execute) need the credential
// again if it was not presented recently; see auth/StepUp.jsx.
installStepUp()

// Install the service worker in production only. Registering it in dev would
// put a cache in front of Vite's module graph and make HMR lie about what is
// on screen. It caches the app shell and never the API — see public/sw.js.
if ('serviceWorker' in navigator && import.meta.env.PROD) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register(import.meta.env.BASE_URL + 'sw.js', { scope: import.meta.env.BASE_URL }).catch(() => {
      // An unavailable service worker costs offline launch, nothing else.
    })
  })
}

// The app is served under /app (vite `base`, and the backend mounts the build
// there). Without a basename the router compares its route table against the
// FULL pathname — "/app/market" matches nothing, falls to the catch-all, and
// redirects to "/". Combined with the server 404 that used to precede it, deep
// links were broken twice over: the server would not serve them, and the
// router would not have matched them if it had.
//
// import.meta.env.BASE_URL is vite's own base, so this stays correct if the
// mount point ever moves, and collapses to "/" if it is ever served at root.
const BASENAME = import.meta.env.BASE_URL?.replace(/\/$/, '') || '/'

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <BrowserRouter basename={BASENAME}>
      {/* Prices render in the viewer's own currency — detected from their
          region, overridable from the sidebar. */}
      {/* Auth wraps everything: no deck, no data fetches, no currency probe
          until we know who is asking. */}
      <AuthProvider>
        <CurrencyProvider>
          <App />
        </CurrencyProvider>
      </AuthProvider>
    </BrowserRouter>
  </React.StrictMode>
)
