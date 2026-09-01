/**
 * Button.
 *
 * 🔴 Every variant here is brand green or neutral, never a tier colour. Green
 * means "you can click this" in the token vocabulary and the tier inks mean
 * "this is the state of a record" — a gold button would collapse the one
 * distinction the palette is built around.
 *
 * `asChild` renders the caller's element with these classes instead of a
 * `<button>`, which is how a NavLink or an anchor gets button styling without
 * nesting an interactive element inside another one.
 */

import { Slot } from '@radix-ui/react-slot'
import { cva, type VariantProps } from 'class-variance-authority'

import { cn } from '../../lib/cn'

const button = cva(
  'inline-flex shrink-0 items-center justify-center gap-1.5 whitespace-nowrap rounded-card ' +
    'text-base font-medium transition-colors ' +
    'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus focus-visible:ring-offset-1 ' +
    'focus-visible:ring-offset-canvas disabled:pointer-events-none disabled:opacity-50 ' +
    '[&_svg]:size-4 [&_svg]:shrink-0',
  {
    variants: {
      variant: {
        primary: 'bg-brand text-white hover:bg-brand-hover',
        outline: 'border border-line-strong bg-surface text-ink hover:bg-sunken',
        ghost: 'text-ink-2 hover:bg-sunken hover:text-ink',
        // Destructive borrows the quarantine ink because it is the one place a
        // status colour and an action agree on meaning: both say "this record
        // is going out of use".
        destructive: 'bg-quarantine text-white hover:brightness-110',
        link: 'text-brand underline-offset-2 hover:underline',
      },
      size: {
        sm: 'h-7 px-2 text-sm',
        md: 'h-8 px-3',
        lg: 'h-9 px-4',
        icon: 'size-8',
      },
    },
    defaultVariants: { variant: 'outline', size: 'md' },
  },
)

export interface ButtonProps
  extends React.ComponentProps<'button'>,
    VariantProps<typeof button> {
  asChild?: boolean
}

export function Button({ className, variant, size, asChild, ...props }: ButtonProps) {
  const Comp = asChild ? Slot : 'button'
  return <Comp className={cn(button({ variant, size }), className)} {...props} />
}

// `buttonVariants` is deliberately not exported. shadcn ships it so an
// anchor can borrow button styling, but `asChild` already does that
// without the extra export — and a module that exports both a component
// and a value stops Fast Refresh from hot-reloading it, which remounts
// every component below and drops their state.
