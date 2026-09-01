import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

import tailwindcss from 'tailwindcss'
import autoprefixer from 'autoprefixer'

/**
 * 🔴 A production build must never compile the auth bypass in.
 *
 * `VITE_NO_AUTH=1` is a compile-time constant: Vite substitutes it into the
 * bundle, so a build that saw it ships a UI with no login page at all. The
 * documented rule is to keep the flag in `.env.development`, which Vite loads
 * only in dev mode — but `.env` is loaded for *every* mode including
 * `npm run build`, and the two filenames differ by one word.
 *
 * A rule that depends on remembering which file is not a control. This turns
 * it into a failed build with a sentence saying what to do, which is the only
 * version of it that survives a rushed deploy.
 */
function refuseBypassInProductionBuilds(command: string, mode: string) {
  if (command !== 'build' || mode === 'development') return
  if (loadEnv(mode, process.cwd(), 'VITE_').VITE_NO_AUTH === undefined) return

  throw new Error(
    'VITE_NO_AUTH is set for a production build. This would ship a bundle ' +
      'with authentication compiled out and no login page in it. Move the ' +
      'flag to frontend/.env.development, which is never loaded by a build.',
  )
}

export default defineConfig(({ command, mode }) => {
  refuseBypassInProductionBuilds(command, mode)

  return {
    // Vite 8 did not pick up postcss.config.js in this project; wiring the
    // pipeline explicitly removes the ambiguity.
    css: { postcss: { plugins: [tailwindcss(), autoprefixer()] } },
    plugins: [react()],
    server: {
      port: 5173,
      // Same-origin in dev, so the app talks to /api/v1/... exactly as it does
      // in production behind Vercel's rewrite. One code path, no CORS branch.
      proxy: {
        // 🔴 8001 is the FastAPI service. Every endpoint the frontend calls is
        // served by the flat `backend/` package.
        '/api': {
          target: process.env.VITE_DEV_API ?? 'http://127.0.0.1:8001',
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
            // Recharts is ~380 kB — five times the whole application before
            // it arrived. Its own chunk so a UI change does not re-download
            // it, and `Dashboard.tsx` loads it lazily so a register with
            // nothing to plot never pays for it at all.
            if (id.includes('recharts') || id.includes('d3-')) return 'charts'
          },
        },
      },
    },
  }
})
