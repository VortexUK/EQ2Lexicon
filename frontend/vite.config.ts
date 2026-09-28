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
        //
        // Naming matters: rolldown emits React's CommonJS body (which every
        // chunk requires) into whichever manual chunk sorts FIRST by name,
        // whatever this function returns for react itself. With the old
        // 'vendor-react' name that was vendor-dnd (so every page preloaded
        // dnd-kit), and adding 'vendor-charts' would have made it the 377 kB
        // charts chunk. 'core-react' sorts before every 'vendor-*' name, so
        // React stays in its own chunk and index.html preloads only that.
        // Verify after touching this: `grep modulepreload dist/index.html`.
        manualChunks(id) {
          if (!id.includes('node_modules')) return undefined
          if (id.includes('@dnd-kit')) return 'vendor-dnd'
          // Recharts and everything it drags in — only the guild History
          // tab imports it, and that tab is lazy, so this chunk stays off
          // the first paint for every other page.
          if (/[\\/](recharts|victory-vendor|d3-[a-z-]+|react-redux|@reduxjs|immer|reselect|es-toolkit|internmap|use-sync-external-store|redux)[\\/]/.test(id)) {
            return 'vendor-charts'
          }
          if (id.includes('react-markdown') || id.includes('remark')) return 'vendor-markdown'
          if (id.includes('react-router') || id.includes('react-dom') || /[\\/]react[\\/]/.test(id)) {
            return 'core-react'
          }
          return undefined
        },
      },
    },
  },
})
