/**
 * The placeholder shown on routes whose backend does not exist yet.
 *
 * This file used to hold the Overview page as well. That moved to
 * `Dashboard.tsx` when the dashboard gained real invoice data, and its
 * build-phase and Track P panels moved with it — kept in one place rather
 * than two, because two copies of a phase list is two copies to forget to
 * update.
 */

import { PageHeader } from '../layout/AppShell'

/**
 * Shown on routes whose backend does not exist yet.
 *
 * An empty screen is an invitation to act, so this names the phase, what it
 * will hold, and what has to happen first — instead of "Coming soon".
 */
export function NotBuiltYet({
  title,
  phase,
  weeks,
  holds,
  blockedBy,
}: {
  title: string
  phase: string
  weeks: string
  holds: string
  blockedBy?: string
}) {
  return (
    <>
      <PageHeader eyebrow={`${phase} · weeks ${weeks}`} title={title} />
      <div className="p-6">
        <div className="card max-w-2xl p-6">
          <h2 className="text-base font-medium text-ink">Not built yet</h2>
          <p className="mt-2 text-base leading-relaxed text-ink-2">{holds}</p>
          {blockedBy && (
            <p className="mt-4 border-t border-line pt-4 text-sm text-ink-2">
              <span className="label">Blocked by</span>
              <span className="mt-1 block">{blockedBy}</span>
            </p>
          )}
          <p className="mt-4 text-sm text-ink-3">
            Task list, preconditions and exit gate are in{' '}
            <code className="font-mono text-ink-2">agri-crm-docs/15-execution-plan.md</code>.
          </p>
        </div>
      </div>
    </>
  )
}
