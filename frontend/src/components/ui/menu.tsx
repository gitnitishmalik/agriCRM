/**
 * Dropdown menu, over Radix.
 *
 * Radix rather than a hand-rolled popover because the parts that are tedious
 * to get right are the parts that matter: focus returns to the trigger on
 * close, Escape and outside-click dismiss, arrow keys move through items, and
 * the whole thing is announced as a menu. A `<div>` with an `onClick` does
 * none of that and looks identical in a screenshot.
 */

import * as DropdownMenu from '@radix-ui/react-dropdown-menu'

import { cn } from '../../lib/cn'

export const Menu = DropdownMenu.Root
export const MenuTrigger = DropdownMenu.Trigger

export function MenuContent({
  className,
  align = 'end',
  sideOffset = 6,
  ...props
}: React.ComponentProps<typeof DropdownMenu.Content>) {
  return (
    <DropdownMenu.Portal>
      <DropdownMenu.Content
        align={align}
        sideOffset={sideOffset}
        className={cn(
          'z-50 min-w-44 overflow-hidden rounded-card border border-line bg-surface p-1',
          'shadow-[0_4px_16px_rgba(28,26,21,0.10)]',
          className,
        )}
        {...props}
      />
    </DropdownMenu.Portal>
  )
}

export function MenuItem({
  className,
  ...props
}: React.ComponentProps<typeof DropdownMenu.Item>) {
  return (
    <DropdownMenu.Item
      className={cn(
        'flex cursor-pointer select-none items-center gap-2 rounded-chip px-2 py-1.5 text-base',
        'text-ink outline-none data-[highlighted]:bg-sunken',
        'data-[disabled]:pointer-events-none data-[disabled]:opacity-50',
        '[&_svg]:size-4 [&_svg]:shrink-0 [&_svg]:text-ink-3',
        className,
      )}
      {...props}
    />
  )
}

export function MenuLabel({
  className,
  ...props
}: React.ComponentProps<typeof DropdownMenu.Label>) {
  return (
    <DropdownMenu.Label
      className={cn('px-2 py-1.5 text-xs uppercase tracking-wide text-ink-3', className)}
      {...props}
    />
  )
}

export function MenuSeparator({
  className,
  ...props
}: React.ComponentProps<typeof DropdownMenu.Separator>) {
  return <DropdownMenu.Separator className={cn('-mx-1 my-1 h-px bg-line', className)} {...props} />
}
