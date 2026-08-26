/**
 * Sign in.
 *
 * The only screen in the app with room to state what the system is for, so it
 * does — quietly, on the left, with the decay curve as an ambient mark. The
 * curve is the same function the freshness meter draws; using it here as
 * atmosphere and there as instrumentation is the one motif this app repeats.
 *
 * The left half is the field green of the rail, so the first thing anyone
 * sees of this system is the thing it is about. The right half is paper,
 * because the half you have to act on should come forward.
 */

import { type FormEvent, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ApiError } from '../api/client'
import { useLogin } from '../api/auth'
import { decayFactor } from '../lib/quality'

export function LoginPage() {
  const navigate = useNavigate()
  const login = useLogin()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')

  async function onSubmit(event: FormEvent) {
    event.preventDefault()
    const result = await login.mutateAsync({ email, password }).catch(() => null)
    if (!result) return
    navigate(result.mfa_required ? '/mfa' : '/', { replace: true })
  }

  const error = login.error instanceof ApiError ? login.error : null

  return (
    <div className="grid min-h-screen lg:grid-cols-[1.1fr_1fr]">
      {/* Left: what this is. Hidden on small screens — an agent signing in on
          a phone in a village wants the form, not the thesis. */}
      <section className="relative hidden flex-col justify-between overflow-hidden bg-rail p-12 lg:flex">
        <div>
          <div className="text-xl font-semibold tracking-tight text-rail-ink">AgriCRM</div>
          <div className="text-2xs uppercase tracking-wide text-rail-ink-2">Theta Analytics</div>
        </div>

        <div className="relative z-10 max-w-md">
          <h1 className="text-3xl font-semibold leading-tight text-rail-ink">
            Every record can answer&nbsp;one question: how do you know?
          </h1>
          <p className="mt-4 text-lg leading-relaxed text-rail-ink-2">
            Farmers, FPOs, cooperative societies and sugar mills — with the source, the
            verification date and the confidence behind every field.
          </p>

          <dl className="mt-8 grid grid-cols-3 gap-6 border-t border-rail-line pt-6">
            {[
              ['Provenance', 'Where it came from'],
              ['Verification', 'Who confirmed it'],
              ['Freshness', 'Whether it still holds'],
            ].map(([term, gloss]) => (
              <div key={term}>
                <dt className="label text-rail-ink-2">{term}</dt>
                <dd className="mt-1 text-sm text-rail-ink-2">{gloss}</dd>
              </div>
            ))}
          </dl>
        </div>

        <DecayBackdrop />
      </section>

      {/* Right: the form. Paper against the field, so the side you have to
          act on is the one that comes forward. */}
      <section className="flex items-center justify-center bg-surface p-6">
        <div className="w-full max-w-sm">
          <h2 className="text-2xl font-semibold text-ink">Sign in</h2>
          <p className="mt-1.5 text-base text-ink-2">Use your Theta Analytics account.</p>

          <form onSubmit={onSubmit} className="mt-8 space-y-4" noValidate>
            <div>
              <label htmlFor="email" className="label mb-1.5 block">
                Email
              </label>
              <input
                id="email"
                type="email"
                autoComplete="username"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className="input"
              />
            </div>

            <div>
              <label htmlFor="password" className="label mb-1.5 block">
                Password
              </label>
              <input
                id="password"
                type="password"
                autoComplete="current-password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="input"
              />
            </div>

            {error && (
              <p role="alert" className="text-sm text-quarantine">
                {error.status === 401
                  ? 'That email and password do not match an account.'
                  : error.message}
              </p>
            )}

            <button type="submit" disabled={login.isPending} className="btn-primary w-full">
              {login.isPending ? 'Signing in' : 'Sign in'}
            </button>
          </form>

          <p className="mt-6 text-xs leading-relaxed text-ink-3">
            Data Ops, Campaign Manager, Compliance and Admin accounts complete a second step
            after signing in.
          </p>
        </div>
      </section>
    </div>
  )
}

/**
 * The decay curve at wall scale: one line per field class, each with its own
 * half-life, drawn faintly. It is the same maths the freshness meter uses —
 * atmosphere here, instrumentation there.
 */
function DecayBackdrop() {
  const W = 720
  const H = 420
  const HORIZON = 1460 // four years

  const classes = ['operational', 'contact', 'role', 'attribute'] as const

  return (
    <svg
      aria-hidden
      viewBox={`0 0 ${W} ${H}`}
      // Anchored left and oversized so the point where the four curves start
      // sits off-canvas. Inside the panel it read as a scratch origin rather
      // than as four lines that share a beginning.
      className="pointer-events-none absolute -bottom-20 -left-28 h-[540px] w-[1000px] text-rail-ink"
      preserveAspectRatio="none"
    >
      {classes.map((fieldClass, index) => {
        const points = Array.from({ length: 97 }, (_, i) => {
          const d = (i / 96) * HORIZON
          const x = (d / HORIZON) * W
          const y = H - decayFactor(d, fieldClass) * H
          return `${x.toFixed(1)},${y.toFixed(1)}`
        })
        return (
          <path
            key={fieldClass}
            d={`M ${points.join(' L ')}`}
            fill="none"
            stroke="currentColor"
            strokeWidth={1.25}
            // Lighter ink on a dark ground needs more of itself to register
            // at the same weight it had on paper.
            opacity={0.08 + index * 0.03}
          />
        )
      })}
    </svg>
  )
}
