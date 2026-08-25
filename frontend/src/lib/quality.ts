/**
 * Quality tiers, completeness and confidence decay.
 *
 * This is Doc 07 rendered as code. The numbers here are not UI choices — they
 * are the specification's, and the backend computes the same values from the
 * same rules. If these two ever disagree, the backend is right and this file
 * is the bug.
 */

export const TIERS = ['gold', 'silver', 'bronze', 'quarantine'] as const
export type Tier = (typeof TIERS)[number]

export const TIER_META: Record<
  Tier,
  { label: string; meaning: string; messageable: boolean }
> = {
  gold: {
    label: 'Gold',
    meaning: 'Verified in the last 180 days. You can call this person today.',
    messageable: true,
  },
  silver: {
    label: 'Silver',
    meaning: 'From an authoritative source, not verified by us. Probably true.',
    messageable: true, // only where consent exists independently
  },
  bronze: {
    label: 'Bronze',
    meaning: 'A lead, not a fact. Never messaged, never in a client-facing count.',
    messageable: false,
  },
  quarantine: {
    label: 'Quarantine',
    meaning: 'Excluded from search, campaigns and counts. Never deleted.',
    messageable: false,
  },
}

/**
 * Doc 07 §4. Half-life in days, by what kind of fact the field is.
 *
 * These differ by an order of magnitude for a reason: a phone number in rural
 * India churns 15–20% a year, while a village's location does not move.
 */
export const HALF_LIFE_DAYS = {
  contact: 365, // phones, emails
  role: 540, // who holds which post
  operational: 270, // is this FPO still active, is the mill crushing
  attribute: 1095, // land area, capacity, established year
  static: Infinity, // CIN, registration number, village location
} as const

export type FieldClass = keyof typeof HALF_LIFE_DAYS

/** decay_factor = 0.5 ^ (days_since_verified / half_life) */
export function decayFactor(daysSinceVerified: number, fieldClass: FieldClass): number {
  const halfLife = HALF_LIFE_DAYS[fieldClass]
  if (!Number.isFinite(halfLife)) return 1
  return Math.pow(0.5, Math.max(0, daysSinceVerified) / halfLife)
}

export function effectiveConfidence(
  baseConfidence: number,
  daysSinceVerified: number,
  fieldClass: FieldClass,
): number {
  return baseConfidence * decayFactor(daysSinceVerified, fieldClass)
}

/** Doc 07 §4 automatic tier transitions, by verification age alone. */
export const TIER_THRESHOLD_DAYS = { goldToSilver: 180, silverToBronze: 540 } as const

/**
 * Days until this record drops a tier on verification age.
 * Returns null when age is no longer what is holding the tier up.
 */
export function daysUntilTierDrop(tier: Tier, daysSinceVerified: number): number | null {
  if (tier === 'gold') return Math.max(0, TIER_THRESHOLD_DAYS.goldToSilver - daysSinceVerified)
  if (tier === 'silver') return Math.max(0, TIER_THRESHOLD_DAYS.silverToBronze - daysSinceVerified)
  return null // bronze has no further age-based drop; quarantine is not age-driven
}

export function nextTier(tier: Tier): Tier | null {
  if (tier === 'gold') return 'silver'
  if (tier === 'silver') return 'bronze'
  return null
}

/** Doc 07 §2 target distribution at 12 months, for the health dashboard. */
export const TARGET_DISTRIBUTION: Record<Tier, [number, number]> = {
  gold: [15, 25],
  silver: [40, 50],
  bronze: [25, 35],
  quarantine: [0, 5],
}

export function daysSince(iso: string | null | undefined): number | null {
  if (!iso) return null
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return null
  return Math.floor((Date.now() - then) / 86_400_000)
}

/** "12 days ago" / "never verified by us" — plain, not clever. */
export function verifiedLabel(days: number | null): string {
  if (days === null) return 'Never verified by us'
  if (days === 0) return 'Verified today'
  if (days === 1) return 'Verified yesterday'
  if (days < 60) return `Verified ${days} days ago`
  if (days < 730) return `Verified ${Math.round(days / 30)} months ago`
  return `Verified ${(days / 365).toFixed(1)} years ago`
}
