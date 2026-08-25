/**
 * Overview.
 *
 * There is no data to summarise yet — the registry lands in Phase 1. Rather
 * than showing zeroed stat tiles, which read as "the database is empty" when
 * the truth is "this is not built yet", this page states where the build has
 * reached and what unblocks the next step.
 *
 * Doc 15's Track P is on it deliberately. Four workstreams were meant to start
 * in week 1 regardless of engineering phase, each blocks a later phase, and
 * each is routinely forgotten until it becomes the reason a phase slips. A
 * dashboard nobody can act on is decoration; this one has four actions.
 */

import { PageHeader } from '../layout/AppShell'
import { useMe } from '../api/auth'

interface Track {
  code: string
  name: string
  leadTime: string
  blocks: string
}

const TRACK_P: Track[] = [
  { code: 'P1', name: 'Data protection lawyer', leadTime: '3–6 weeks', blocks: 'Phase 2 exit, Phase 4 launch' },
  { code: 'P2', name: 'Theta legacy data audit', leadTime: '4–8 weeks', blocks: 'Phase 2 import' },
  { code: 'P3', name: 'Meta business verification', leadTime: '1–3 weeks', blocks: 'Phase 4 entirely' },
  { code: 'P4', name: 'BD partnership outreach', leadTime: 'Continuous', blocks: 'Phase 2 data volume' },
]

const PHASES = [
  { n: 0, name: 'Foundation', state: 'partial' },
  { n: 1, name: 'Organisation registry', state: 'next' },
  { n: 2, name: 'Farmer core & consent', state: 'todo' },
  { n: 3, name: 'Commercial modules', state: 'todo' },
  { n: 4, name: 'Engagement engine', state: 'todo' },
  { n: 5, name: 'Data intelligence', state: 'todo' },
  { n: 6, name: 'Field mobile app', state: 'todo' },
  { n: 7, name: 'Scale & harden', state: 'todo' },
] as const

export function OverviewPage() {
  const { data: me } = useMe()

  return (
    <>
      <PageHeader
        eyebrow="Build status"
        title={me ? `Good to see you, ${me.full_name.split(' ')[0]}` : 'Overview'}
        description="No registry data yet — that arrives in Phase 1. Until then, this is where the build stands."
      />

      <div className="space-y-8 p-6">
        <section>
          <h2 className="text-lg font-semibold text-ink">Build phases</h2>
          <p className="mt-1 text-sm text-ink-2">
            A phase ends when its exit gate passes, not when its weeks run out.
          </p>

          <ol className="mt-4 divide-y divide-line overflow-hidden rounded-card border border-line bg-surface">
            {PHASES.map((phase) => (
              <li key={phase.n} className="flex items-center gap-4 px-4 py-2.5">
                <span className="w-6 shrink-0 font-mono text-sm text-ink-3">{phase.n}</span>
                <span className="flex-1 text-base text-ink">{phase.name}</span>
                <PhaseState state={phase.state} />
              </li>
            ))}
          </ol>

          <p className="mt-3 text-sm text-ink-2">
            Phase 0 is two gate items short: staging deploy and Sentry both need an AWS
            account. The schema, CI and compliance guards are done and green.
          </p>
        </section>

        <section>
          <h2 className="text-lg font-semibold text-ink">Track P — start these now</h2>
          <p className="mt-1 max-w-3xl text-sm text-ink-2">
            These four are not a phase. Each has a multi-week external lead time and each
            blocks a later phase. None has started.
          </p>

          <div className="mt-4 grid gap-3 sm:grid-cols-2">
            {TRACK_P.map((track) => (
              <article key={track.code} className="card p-4">
                <div className="flex items-baseline gap-2">
                  <span className="font-mono text-sm text-ink-3">{track.code}</span>
                  <h3 className="text-base font-medium text-ink">{track.name}</h3>
                </div>
                <dl className="mt-3 space-y-1.5">
                  <div className="flex gap-2 text-sm">
                    <dt className="w-20 shrink-0 text-ink-3">Lead time</dt>
                    <dd className="text-ink-2">{track.leadTime}</dd>
                  </div>
                  <div className="flex gap-2 text-sm">
                    <dt className="w-20 shrink-0 text-ink-3">Blocks</dt>
                    <dd className="text-ink-2">{track.blocks}</dd>
                  </div>
                </dl>
              </article>
            ))}
          </div>
        </section>
      </div>
    </>
  )
}

function PhaseState({ state }: { state: 'partial' | 'next' | 'todo' }) {
  const copy = { partial: 'Gate pending', next: 'Next', todo: 'Not started' }[state]
  const tone =
    state === 'partial'
      ? 'text-bronze bg-bronze-soft ring-bronze-line'
      : state === 'next'
        ? 'text-ink bg-sunken ring-line-strong'
        : 'text-ink-3 bg-sunken ring-line'

  return (
    <span className={`rounded-chip px-1.5 py-0.5 text-xs uppercase ring-1 ring-inset ${tone}`}>
      {copy}
    </span>
  )
}

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
