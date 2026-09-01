/**
 * Badge.
 *
 * Small status chips. The tier variants are the four Doc 07 inks and exist so
 * that quality tier renders identically everywhere; `QualityTier.tsx` remains
 * the component to reach for when the thing being labelled *is* a tier, since
 * it also carries the label text and the completeness bar.
 *
 * Invoice status is deliberately not mapped to a tier ink. An invoice being
 * "paid" and an organisation being "Gold" are unrelated facts, and painting
 * them the same colour would teach staff a relationship that does not exist.
 */

import { cva, type VariantProps } from 'class-variance-authority'

import { cn } from '../../lib/cn'

const badge = cva(
  'inline-flex items-center gap-1 whitespace-nowrap rounded-chip border px-1.5 py-0.5 ' +
    'text-xs font-medium uppercase tracking-wide',
  {
    variants: {
      variant: {
        neutral: 'border-line bg-sunken text-ink-2',
        brand: 'border-brand-line bg-brand-soft text-brand',
        gold: 'border-gold-line bg-gold-soft text-gold',
        silver: 'border-silver-line bg-silver-soft text-silver',
        bronze: 'border-bronze-line bg-bronze-soft text-bronze',
        quarantine: 'border-quarantine-line bg-quarantine-soft text-quarantine',
        outline: 'border-line-strong bg-transparent text-ink-2',
      },
    },
    defaultVariants: { variant: 'neutral' },
  },
)

export function Badge({
  className,
  variant,
  ...props
}: React.ComponentProps<'span'> & VariantProps<typeof badge>) {
  return <span className={cn(badge({ variant }), className)} {...props} />
}
