import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import App from './App'
import { CurrencyProvider } from './currency/CurrencyContext'
import { applyA11y, loadA11y } from './components/Accessibility'
import './index.css'

// Apply text size / contrast / motion before the first paint, so the UI
// never flashes at the default size for someone who needs it larger.
applyA11y(loadA11y())

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <BrowserRouter>
      {/* Prices render in the viewer's own currency — detected from their
          region, overridable from the sidebar. */}
      <CurrencyProvider>
        <App />
      </CurrencyProvider>
    </BrowserRouter>
  </React.StrictMode>
)
