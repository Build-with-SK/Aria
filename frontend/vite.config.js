import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Backend origin. Defaults to the usual local API; set ARIA_API to point a dev
// UI at a backend on another port (or another machine, e.g. the Mac mini).
const API = process.env.ARIA_API || 'http://localhost:8000'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 3000,
    // Proxy all /api calls to the FastAPI backend
    proxy: {
      '/api': {
        target: API,
        changeOrigin: true,
      },
      '/health': {
        target: API,
        changeOrigin: true,
      }
    }
  }
})
