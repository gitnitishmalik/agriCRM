/**
 * Quality tier chip and its colour mapping.
 *
 * These four inks appear on tier chips, freshness meters and tier-keyed
 * strokes, and nowhere else. The brand green next to them is chrome — it says
 * "you can act on this" — so the only thing a tier colour ever says is how
 * much to trust the data it sits on.
 */

import { TIER_META, type Tier } from '../lib/quality'

export const TIER_TEXT: Record<Tier, string> = {
  gold: 'text-gold',
  silver: 'text-silver',
  bronze: 'text-bronze',
  quarantine: 'text-quarantine',
}

export const TIER_STROKE: Record<Tier, string> = {
  gold: 'stroke-gold',
  silver: 'stroke-silver',
  bronze: 'stroke-bronze',
  quarantine: 'stroke-quarantine',
}

const CHIP: Record<Tier, string> = {
  gold: 'bg-gold-soft text-gold ring-gold-line',
  silver: 'bg-silver-soft text-silver ring-silver-line',
  bronze: 'bg-bronze-soft text-bronze ring-bronze-line',
  quarantine: 'bg-quarantine-soft text-quarantine ring-quarantine-line',
}

export function QualityTierChip({
  tier,
  showMeaning = false,
}: {
  tier: Tier
  showMeaning?: boolean
}) {
  const meta = TIER_META[tier]
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-chip px-1.5 py-0.5 text-xs font-medium uppercase ring-1 ring-inset ${CHIP[tier]}`}
      title={showMeaning ? meta.meaning : undefined}
    >
      <span aria-hidden className="h-1.5 w-1.5 rounded-full bg-current" />
      {meta.label}
    </span>
  )
}

/**
 * Completeness 0–100 (Doc 07 §3).
 *
 * Rendered as a segmented bar rather than a smooth one: completeness is a
 * weighted checklist of discrete fields, and a segmented bar says "items
 * present" where a continuous one would imply a measurement.
 */
export function CompletenessBar({ score, className = '' }: { score: number; className?: string }) {
  const segments = 10
  const filled = Math.round((Math.max(0, Math.min(100, score)) / 100) * segments)

  return (
    <div className={`flex items-center gap-2 ${className}`}>
      <div className="flex gap-px" role="img" aria-label={`Completeness ${score} out of 100`}>
        {Array.from({ length: segments }, (_, i) => (
          <span
            key={i}
            className={`h-3 w-1.5 rounded-[1px] ${i < filled ? 'bg-ink' : 'bg-line'}`}
          />
        ))}
      </div>
      <span className="font-mono text-sm tabular-nums text-ink-2">{score}</span>
    </div>
  )
}
