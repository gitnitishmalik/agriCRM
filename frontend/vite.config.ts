import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

import tailwindcss from 'tailwindcss'
import autoprefixer from 'autoprefixer'

export default defineConfig({
  // Vite 8 did not pick up postcss.config.js in this project; wiring the
  // pipeline explicitly removes the ambiguity.
  css: { postcss: { plugins: [tailwindcss(), autoprefixer()] } },
  plugins: [react()],
  server: {
    port: 5173,
    // Same-origin in dev, so the app talks to /api/v1/... exactly as it does
    // in production behind Vercel's rewrite. One code path, no CORS branch.
    proxy: {
      '/api': {
        target: process.env.VITE_DEV_API ?? 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    sourcemap: true,
    rollupOptions: {
      output: {
        // Split the vendor chunk so a UI change does not invalidate React for
        // every field agent on a 2G connection.
        // Vite 8 runs rolldown, which takes the function form only.
        manualChunks(id: string) {
          if (!id.includes('node_modules')) return
          if (/[\\/]node_modules[\\/](react|react-dom|react-router|scheduler)/.test(id)) {
            return 'react'
          }
          if (id.includes('@tanstack')) return 'query'
        },
      },
    },
  },
})
