/**
 * Card.
 *
 * The surface every dashboard panel sits on. White on the warm canvas, with
 * the default border rather than a shadow — the elevation model here is one
 * step, because a dashboard of twelve floating panels reads as clutter and
 * the tokens deliberately ship no shadow scale.
 */

import { cn } from '../../lib/cn'

export function Card({ className, ...props }: React.ComponentProps<'div'>) {
  return (
    <div
      className={cn('rounded-card border border-line bg-surface', className)}
      {...props}
    />
  )
}

export function CardHeader({ className, ...props }: React.ComponentProps<'div'>) {
  return (
    <div
      className={cn(
        'flex flex-wrap items-start justify-between gap-3 px-5 pb-4 pt-4',
        className,
      )}
      {...props}
    />
  )
}

export function CardTitle({ className, ...props }: React.ComponentProps<'h3'>) {
  return <h3 className={cn('text-lg font-semibold text-ink', className)} {...props} />
}

export function CardDescription({ className, ...props }: React.ComponentProps<'p'>) {
  return <p className={cn('mt-0.5 text-sm text-ink-2', className)} {...props} />
}

export function CardContent({ className, ...props }: React.ComponentProps<'div'>) {
  return <div className={cn('px-5 pb-5', className)} {...props} />
}

export function CardFooter({ className, ...props }: React.ComponentProps<'div'>) {
  return (
    <div
      className={cn('flex items-center gap-2 border-t border-line px-5 py-3', className)}
      {...props}
    />
  )
}
