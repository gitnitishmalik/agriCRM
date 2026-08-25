/**
 * Second factor.
 *
 * 🔴 Doc 12 §1: mandatory for Data Ops, Campaign Manager, Compliance and
 * Admin. The backend re-issues the token pair on success — without that the
 * client keeps a token whose claim still says MFA is outstanding, and every
 * protected call keeps failing.
 */

import { type FormEvent, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useEnrolMfa, useMe, useVerifyMfa } from '../api/auth'

export function MfaPage() {
  const navigate = useNavigate()
  const { data: me } = useMe()
  const verify = useVerifyMfa()
  const enrol = useEnrolMfa()
  const [code, setCode] = useState('')

  async function onSubmit(event: FormEvent) {
    event.preventDefault()
    const ok = await verify.mutateAsync(code).catch(() => null)
    if (ok) navigate('/', { replace: true })
  }

  return (
    <div className="flex min-h-screen items-center justify-center p-6">
      <div className="w-full max-w-sm">
        <div className="label">Second step</div>
        <h1 className="mt-1 text-2xl font-semibold text-ink">Enter your code</h1>
        <p className="mt-1.5 text-base text-ink-2">
          {me ? `${roleName(me.role)} accounts` : 'This account'} require a second factor at
          every sign-in.
        </p>

        <form onSubmit={onSubmit} className="mt-8 space-y-4" noValidate>
          <div>
            <label htmlFor="code" className="label mb-1.5 block">
              Six-digit code
            </label>
            <input
              id="code"
              inputMode="numeric"
              autoComplete="one-time-code"
              maxLength={8}
              required
              value={code}
              onChange={(e) => setCode(e.target.value.replace(/\D/g, ''))}
              className="input text-center font-mono text-xl tracking-[0.35em]"
            />
          </div>

          {verify.isError && (
            <p role="alert" className="text-sm text-quarantine">
              That code did not match. Codes expire every 30 seconds — try the current one.
            </p>
          )}

          <button type="submit" disabled={verify.isPending || code.length < 6} className="btn-primary w-full">
            {verify.isPending ? 'Checking' : 'Continue'}
          </button>
        </form>

        <div className="mt-8 border-t border-line pt-5">
          <p className="text-sm text-ink-2">No authenticator set up yet?</p>
          <button
            type="button"
            onClick={() => enrol.mutate()}
            className="mt-1.5 text-sm text-ink underline decoration-line underline-offset-2 hover:decoration-ink"
          >
            Set up an authenticator app
          </button>

          {enrol.data && (
            <div className="mt-4 rounded-card border border-line bg-surface p-4">
              <div className="label mb-2">1. Scan this with your authenticator</div>

              {/* The SVG comes from our own backend, built by the qrcode
                  library from a URI we generated — not from user input. */}
              <div
                className="mx-auto w-40 [&>svg]:h-full [&>svg]:w-full"
                aria-label="QR code for authenticator enrolment"
                dangerouslySetInnerHTML={{ __html: enrol.data.qr_svg }}
              />

              <div className="mt-4 border-t border-line pt-3">
                <div className="label mb-1.5">Or enter this key by hand</div>
                <code className="block break-all rounded-chip bg-sunken px-2 py-1.5 font-mono text-sm text-ink">
                  {enrol.data.secret}
                </code>
              </div>

              <p className="mt-3 text-sm text-ink-2">
                2. Enter the six-digit code it shows in the box above.
              </p>
              <p className="mt-2 text-xs text-ink-3">
                Google Authenticator, Microsoft Authenticator, Authy, 1Password and
                Bitwarden all work.
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function roleName(role: string): string {
  return role.split('_').map((w) => w[0].toUpperCase() + w.slice(1)).join(' ')
}
