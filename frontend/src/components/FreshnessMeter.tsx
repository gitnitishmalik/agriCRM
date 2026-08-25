/**
 * The freshness meter — this application's signature element.
 *
 * Doc 07 §4 defines confidence decay as a real function:
 *
 *     decay_factor = 0.5 ^ (days_since_verified / half_life)
 *
 * Most CRMs show a "last updated" date and leave the reader to guess what it
 * means. This draws the actual curve, marks where the record sits on it, and
 * says when it drops a tier. The shape does the work a date cannot: a record
 * halfway down a steep slope reads as urgent without anyone reading a number.
 *
 * It appears at two scales — inline on a record header, and enlarged on the
 * data-health dashboard as the decay forecast. Same curve, same meaning.
 */

import { useId } from 'react'
import {
  daysUntilTierDrop,
  decayFactor,
  nextTier,
  verifiedLabel,
  type FieldClass,
  type Tier,
} from '../lib/quality'
import { TIER_TEXT, TIER_STROKE } from './QualityTier'

interface Props {
  tier: Tier
  daysSinceVerified: number | null
  fieldClass?: FieldClass
  /** 'inline' sits in a record header; 'panel' is the dashboard scale. */
  size?: 'inline' | 'panel'
  className?: string
}

const DIMS = {
  inline: { w: 104, h: 30, pad: 3, horizonDays: 730 },
  panel: { w: 300, h: 96, pad: 8, horizonDays: 1095 },
} as const

export function FreshnessMeter({
  tier,
  daysSinceVerified,
  fieldClass = 'contact',
  size = 'inline',
  className = '',
}: Props) {
  const gradientId = useId()
  const { w, h, pad, horizonDays } = DIMS[size]

  // A never-verified record has no position on a decay curve — it never
  // started decaying from a verification. Say so rather than drawing a lie.
  const unverified = daysSinceVerified === null
  const days = daysSinceVerified ?? 0

  const plotW = w - pad * 2
  const plotH = h - pad * 2

  const x = (d: number) => pad + (Math.min(d, horizonDays) / horizonDays) * plotW
  const y = (factor: number) => pad + (1 - factor) * plotH

  // Sample the curve. 48 points is smooth at both scales and cheap.
  const points = Array.from({ length: 49 }, (_, i) => {
    const d = (i / 48) * horizonDays
    return `${x(d).toFixed(2)},${y(decayFactor(d, fieldClass)).toFixed(2)}`
  })
  const curve = `M ${points.join(' L ')}`
  const area = `${curve} L ${x(horizonDays).toFixed(2)},${(h - pad).toFixed(2)} L ${pad},${(h - pad).toFixed(2)} Z`

  const nowX = x(days)
  const nowY = y(decayFactor(days, fieldClass))

  const dropIn = daysUntilTierDrop(tier, days)
  const falls = nextTier(tier)

  const caption = unverified
    ? 'No verification on record'
    : dropIn !== null && falls
      ? dropIn === 0
        ? `Due to drop to ${falls}`
        : `Drops to ${falls} in ${dropIn} days`
      : verifiedLabel(daysSinceVerified)

  return (
    <div className={`flex items-center gap-2.5 ${className}`}>
      <svg
        width={w}
        height={h}
        viewBox={`0 0 ${w} ${h}`}
        role="img"
        aria-label={`${verifiedLabel(daysSinceVerified)}. ${caption}.`}
        className={unverified ? 'opacity-40' : undefined}
      >
        <defs>
          <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="currentColor" stopOpacity="0.16" />
            <stop offset="100%" stopColor="currentColor" stopOpacity="0" />
          </linearGradient>
        </defs>

        <g className={TIER_TEXT[tier]}>
          <path d={area} fill={`url(#${gradientId})`} />
          <path
            d={curve}
            fill="none"
            stroke="currentColor"
            strokeWidth={size === 'panel' ? 2 : 1.5}
            strokeLinecap="round"
            className={TIER_STROKE[tier]}
            style={{ strokeDasharray: 120, animation: 'meter-draw 700ms ease-out both' }}
          />

          {/* Half-life marker: where confidence has halved. */}
          {Number.isFinite(HALF_LIFE_X(fieldClass, horizonDays)) && (
            <line
              x1={x(HALF_LIFE_X(fieldClass, horizonDays))}
              y1={pad}
              x2={x(HALF_LIFE_X(fieldClass, horizonDays))}
              y2={h - pad}
              stroke="currentColor"
              strokeWidth="1"
              strokeDasharray="2 3"
              opacity="0.28"
            />
          )}

          {!unverified && (
            <>
              <line
                x1={nowX}
                y1={nowY}
                x2={nowX}
                y2={h - pad}
                stroke="currentColor"
                strokeWidth="1"
                opacity="0.35"
              />
              <circle cx={nowX} cy={nowY} r={size === 'panel' ? 4 : 3} fill="currentColor" />
              <circle
                cx={nowX}
                cy={nowY}
                r={size === 'panel' ? 4 : 3}
                fill="none"
                stroke="var(--surface)"
                strokeWidth="1.5"
              />
            </>
          )}
        </g>
      </svg>

      <div className="min-w-0">
        <div className="truncate text-sm text-ink">{verifiedLabel(daysSinceVerified)}</div>
        <div className="truncate text-xs text-ink-3">{caption}</div>
      </div>
    </div>
  )
}

/** x-position of the half-life, clamped to the drawn horizon. */
function HALF_LIFE_X(fieldClass: FieldClass, horizon: number): number {
  const halfLives = {
    contact: 365,
    role: 540,
    operational: 270,
    attribute: 1095,
    static: Infinity,
  } as const
  const hl = halfLives[fieldClass]
  return Number.isFinite(hl) ? Math.min(hl, horizon) : Infinity
}
