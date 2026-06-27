/// <reference types="vitest" />
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// Backend proxy target — defaults to the standard local FastAPI port, but
// can be overridden so a second git worktree can run its own backend on a
// different port without colliding with the primary checkout's :8000.
// Usage: `VITE_API_PROXY_TARGET=http://localhost:8001 npm run dev -- --port 5174`
const PROXY_TARGET = process.env.VITE_API_PROXY_TARGET || 'http://localhost:8000'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    // Keep coverage off the dev-time critical path; run on demand.
    coverage: { enabled: false },
  },
  server: {
    proxy: {
      '/api': {
        target: PROXY_TARGET,
        changeOrigin: true,
        configure: (proxy) => {
          // Prevent unhandled 'error' events from crashing the Vite process
          // when the FastAPI backend is unavailable (ECONNREFUSED).
          proxy.on('error', (err, _req, res) => {
            console.warn('[vite proxy] /api error:', err.message)
            if ('writeHead' in res && typeof res.writeHead === 'function') {
              res.writeHead(502, { 'Content-Type': 'application/json' })
              res.end(JSON.stringify({ detail: 'Backend unavailable' }))
            }
          })
        },
      },
      '/icons': {
        target: PROXY_TARGET,
        changeOrigin: true,
        configure: (proxy) => {
          proxy.on('error', (err) => { console.warn('[vite proxy] /icons error:', err.message) })
        },
      },
      '/aa-assets': {
        target: PROXY_TARGET,
        changeOrigin: true,
        configure: (proxy) => {
          proxy.on('error', (err) => { console.warn('[vite proxy] /aa-assets error:', err.message) })
        },
      },
      '/spell-icons': {
        target: PROXY_TARGET,
        changeOrigin: true,
        configure: (proxy) => {
          proxy.on('error', (err) => { console.warn('[vite proxy] /spell-icons error:', err.message) })
        },
      },
    },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    rollupOptions: {
      output: {
        // Function form (rolldown/vite 8 only accepts a function, not the
        // object map). Returns the vendor chunk name for a node_modules id.
        manualChunks(id) {
          if (!id.includes('node_modules')) return undefined
          if (id.includes('@dnd-kit')) return 'vendor-dnd'
          if (id.includes('react-markdown') || id.includes('remark')) return 'vendor-markdown'
          if (id.includes('react-router') || id.includes('react-dom') || /[\\/]react[\\/]/.test(id)) {
            return 'vendor-react'
          }
          return undefined
        },
      },
    },
  },
})
