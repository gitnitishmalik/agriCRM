/**
 * Tier colour vocabulary.
 *
 * Split out from `components/QualityTier.tsx` so that file exports only
 * components and Fast Refresh keeps working — a module mixing the two reloads
 * by remounting, which drops the state of every component below it.
 *
 * Not in `lib/quality.ts`: that file is Doc 07 rendered as code and says so.
 * These are Tailwind classes, which are a rendering choice; the specification
 * has no opinion about them.
 *
 * Written as full class strings rather than composed from the tier name
 * because Tailwind scans source text — `text-${tier}` produces no CSS.
 */

import type { Tier } from './quality'

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
