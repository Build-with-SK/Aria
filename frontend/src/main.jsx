import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import App from './App'
import { CurrencyProvider } from './currency/CurrencyContext'
import { AuthProvider } from './auth/AuthContext'
import { applyA11y, loadA11y } from './components/Accessibility'
import './index.css'

// Apply text size / contrast / motion before the first paint, so the UI
// never flashes at the default size for someone who needs it larger.
applyA11y(loadA11y())

// Install the service worker in production only. Registering it in dev would
// put a cache in front of Vite's module graph and make HMR lie about what is
// on screen. It caches the app shell and never the API — see public/sw.js.
if ('serviceWorker' in navigator && import.meta.env.PROD) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch(() => {
      // An unavailable service worker costs offline launch, nothing else.
    })
  })
}

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <BrowserRouter>
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
