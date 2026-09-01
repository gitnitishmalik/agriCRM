/**
 * Sign in.
 *
 * The only screen in the app with room to state what the system is for, so it
 * does — over a photograph, on the left, with the form on paper to the right.
 *
 * The photograph earns its place rather than decorating: a field from above
 * and the same soil in section, one plant with its root system exposed. That
 * is the argument this product makes. Anyone can hold the row above ground —
 * a name, a village, a phone number. What this system holds is what is under
 * it: where the record came from, who last stood in front of it, and how much
 * of it still holds. The three terms along the bottom are that claim written
 * out, and they sit on the soil half of the image on purpose.
 *
 * The right half is unbleached paper, because the half you have to act on
 * should come forward. Its line art is the only decoration in the product and
 * it stops at the sign-in boundary: past here every surface is a grid over a
 * hundred thousand rows, and there is no such thing as a decorative pixel.
 */

import { type FormEvent, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ApiError } from '../api/client'
import { useLogin } from '../api/auth'
import { AuthAside, AuthDecor, AuthMobileBrand } from '../layout/AuthLayout'

export function LoginPage() {
  const navigate = useNavigate()
  const login = useLogin()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [reveal, setReveal] = useState(false)

  async function onSubmit(event: FormEvent) {
    event.preventDefault()
    const result = await login.mutateAsync({ email, password }).catch(() => null)
    if (!result) return
    navigate(result.mfa_required ? '/mfa' : '/', { replace: true })
  }

  const error = login.error instanceof ApiError ? login.error : null

  return (
    <div className="grid min-h-dvh lg:grid-cols-[1.05fr_1fr]">
      <AuthAside />

      <section className="relative flex items-center justify-center overflow-hidden bg-canvas px-6 py-12">
        <AuthDecor />

        <div className="relative z-10 w-full max-w-sm">
          <AuthMobileBrand />

          <h1 className="text-2xl font-semibold text-ink">Sign in</h1>
          <p className="mt-1.5 text-base text-ink-2">Use your Theta Analytics account.</p>

          <form onSubmit={onSubmit} className="mt-8 space-y-4" noValidate>
            <div>
              <label htmlFor="email" className="label mb-1.5 block">
                Email
              </label>
              <input
                id="email"
                type="email"
                inputMode="email"
                autoComplete="username"
                autoCapitalize="off"
                spellCheck={false}
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                aria-invalid={error?.status === 401 || undefined}
                className="input"
              />
            </div>

            <div>
              <div className="mb-1.5 flex items-baseline justify-between gap-3">
                <label htmlFor="password" className="label">
                  Password
                </label>
                {/* A shared workstation in a district office is the normal
                    case here, so the reveal is opt-in and never sticky. */}
                <button
                  type="button"
                  onClick={() => setReveal((v) => !v)}
                  className="text-xs text-ink-3 underline decoration-line underline-offset-2 transition-colors hover:text-ink-2"
                >
                  {reveal ? 'Hide' : 'Show'}
                </button>
              </div>
              <input
                id="password"
                type={reveal ? 'text' : 'password'}
                autoComplete="current-password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                aria-invalid={error?.status === 401 || undefined}
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
