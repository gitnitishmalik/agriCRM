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

/**
 * Where the API is. One binding, used by the proxy and by the startup check,
 * so the two cannot disagree about what they are talking to.
 */
const apiTarget = process.env.VITE_DEV_API ?? 'http://127.0.0.1:8001'

/**
 * 🔴 Say at startup that the API is down, rather than at the first click.
 *
 * The dev server starts happily with no backend, so the first sign that
 * anything is wrong is a failed sign-in — by which point the natural reading is
 * "my password is wrong" or "the app is broken", not "I did not start the other
 * process". One probe at boot moves that discovery to the moment it is cheap.
 *
 * Never fatal. Working on the UI against a stopped API is a legitimate thing to
 * be doing, and a dev server that refuses to start would be worse than the
 * problem it reports.
 */
function warnIfApiIsDown() {
  const url = `${apiTarget}/api/v1/healthz/`
  const timeout = AbortSignal.timeout(2500)

  fetch(url, { signal: timeout }).catch(() => {
    console.warn(
      `\n  Note: nothing is answering at ${apiTarget}.\n` +
        '  Every /api request will fail until you start it:\n' +
        '      python -m backend.run          (or: make run)\n' +
        '  uvicorn started by hand defaults to port 8000 — pass --port 8001.\n',
    )
  })
}

export default defineConfig(({ command, mode }) => {
  refuseBypassInProductionBuilds(command, mode)

  if (command === 'serve') warnIfApiIsDown()

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
          target: apiTarget,
          changeOrigin: true,

          /**
           * 🔴 Turn "the API is not running" into a sentence that says so.
           *
           * Without this, a stopped backend surfaces as `ECONNREFUSED` repeated
           * once per request in the terminal, and as a bare **Bad Gateway** in
           * the UI. Neither names the cause, neither names the fix, and the
           * most common cause by far is the most boring one: uvicorn started on
           * its default port 8000 while this proxy points at 8001.
           *
           * So: answer with a JSON 503 in the shape the client already parses,
           * carrying the command that fixes it — and log the explanation once
           * rather than a stack trace per attempt.
           */
          configure: (proxy) => {
            let explained = false

            proxy.on('error', (_error, _request, response) => {
              if (!explained) {
                explained = true
                console.error(
                  `\n  The API is not answering on ${apiTarget}.\n\n` +
                    '  Start it from the repository root:\n' +
                    '      python -m backend.run          (or: make run)\n\n' +
                    '  If you started uvicorn by hand it defaults to port 8000,\n' +
                    '  which this proxy does not use. Pass --port 8001.\n' +
                    `  To point the UI elsewhere: VITE_DEV_API=http://127.0.0.1:8000\n`,
                )
              }

              // `response` is a ServerResponse for a request, and a Socket when
              // the failure was on an upgrade. Only the former can be answered.
              if (!('writeHead' in response) || response.headersSent) return

              response.writeHead(503, { 'Content-Type': 'application/json' })
              response.end(
                JSON.stringify({
                  error: {
                    code: 'api_unreachable',
                    message:
                      `The API is not running at ${apiTarget}. Start it with ` +
                      '`python -m backend.run` from the repository root. If you ' +
                      'started uvicorn manually, it defaults to port 8000 — pass ' +
                      '--port 8001.',
                    details: {},
                    request_id: null,
                  },
                }),
              )
            })
          },
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
