/**
 * Table.
 *
 * Sticky header on a sunken ground, because the surfaces this system is built
 * for are read by scrolling — a header that leaves the viewport turns column
 * six into a guess.
 *
 * `tabular-nums` on every cell rather than only on the numeric ones: a column
 * of proportional figures does not line up at the decimal point, and which
 * columns are numeric is a decision each caller would otherwise re-make.
 */

import { cn } from '../../lib/cn'

export function Table({ className, ...props }: React.ComponentProps<'table'>) {
  return (
    // The wrapper scrolls, not the page. A 30-column grid must never make the
    // whole document scroll sideways.
    <div className="w-full overflow-x-auto">
      <table
        className={cn('w-full caption-bottom border-collapse text-base', className)}
        {...props}
      />
    </div>
  )
}

export function TableHeader({ className, ...props }: React.ComponentProps<'thead'>) {
  return <thead className={cn('sticky top-0 z-10 bg-sunken', className)} {...props} />
}

export function TableBody({ className, ...props }: React.ComponentProps<'tbody'>) {
  return <tbody className={className} {...props} />
}

export function TableRow({ className, ...props }: React.ComponentProps<'tr'>) {
  return (
    <tr
      className={cn(
        'border-b border-line transition-colors last:border-0 hover:bg-brand-soft/40',
        className,
      )}
      {...props}
    />
  )
}

export function TableHead({ className, ...props }: React.ComponentProps<'th'>) {
  return (
    <th
      className={cn(
        'h-8 whitespace-nowrap border-b border-line px-3 text-left align-middle ' +
          'text-xs font-semibold uppercase tracking-wide text-ink-2',
        className,
      )}
      {...props}
    />
  )
}

export function TableCell({ className, ...props }: React.ComponentProps<'td'>) {
  return (
    <td className={cn('px-3 py-2 align-middle tabular-nums text-ink', className)} {...props} />
  )
}

export function TableEmpty({
  colSpan,
  children,
}: {
  colSpan: number
  children: React.ReactNode
}) {
  return (
    <tr>
      <td colSpan={colSpan} className="px-3 py-10 text-center text-base text-ink-3">
        {children}
      </td>
    </tr>
  )
}
