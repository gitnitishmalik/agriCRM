/**
 * PII display. 🔴 R9 / Doc 12 §4.
 *
 * Masked by default. Revealing needs the `contact.view_full` permission, is
 * one record at a time, and writes audit.data_access_log server-side.
 *
 * Three things here are deliberate friction, not oversights:
 *
 *   1. There is no "reveal all" and this component takes no bulk variant.
 *      Doc 12 §4: "Bulk reveal is not offered; the UI reveals one contact at
 *      a time." Bulk export is how contact databases leave companies.
 *   2. The mask is produced by the server. This component never receives the
 *      full value until a reveal is granted, so a React DevTools session
 *      cannot read what the user is not cleared to see.
 *   3. Revealing is a request, not a local toggle — the audit entry is the
 *      point, and a client-side toggle would not write one.
 */

import { useState } from 'react'

interface Props {
  /** Server-masked form, e.g. "+91 98XXX XX210". Never the full value. */
  masked: string
  /** Asks the server for the full value; it writes the access log. */
  onReveal?: () => Promise<string>
  /** Whether this user holds contact.view_full. */
  canReveal?: boolean
  kind?: 'phone' | 'email' | 'other'
  className?: string
}

export function MaskedValue({
  masked,
  onReveal,
  canReveal = false,
  kind = 'other',
  className = '',
}: Props) {
  const [revealed, setRevealed] = useState<string | null>(null)
  const [pending, setPending] = useState(false)
  const [failed, setFailed] = useState<string | null>(null)

  async function reveal() {
    if (!onReveal || pending) return
    setPending(true)
    setFailed(null)
    try {
      setRevealed(await onReveal())
    } catch {
      setFailed('Could not reveal. Your access attempt was logged.')
    } finally {
      setPending(false)
    }
  }

  return (
    <span className={`inline-flex items-center gap-2 ${className}`}>
      <span className="font-mono text-sm tabular-nums text-ink">{revealed ?? masked}</span>

      {!revealed && canReveal && onReveal && (
        <button
          type="button"
          onClick={reveal}
          disabled={pending}
          className="rounded-chip px-1.5 py-0.5 text-xs uppercase text-ink-3 underline decoration-line underline-offset-2 transition-colors hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus disabled:opacity-50"
          aria-label={`Reveal full ${kind}. This is recorded in the access log.`}
        >
          {pending ? 'Revealing' : 'Reveal'}
        </button>
      )}

      {revealed && (
        <span className="text-2xs uppercase tracking-wide text-ink-3" title="Written to audit.data_access_log">
          Logged
        </span>
      )}

      {failed && <span className="text-xs text-quarantine">{failed}</span>}
    </span>
  )
}
