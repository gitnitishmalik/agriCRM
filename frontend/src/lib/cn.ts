/**
 * Class name merge.
 *
 * `clsx` resolves the conditionals, `tailwind-merge` resolves the conflicts:
 * a variant's `px-3` and a caller's `px-6` are the same property, and without
 * the merge both end up in the attribute with the winner decided by
 * stylesheet order rather than by the caller. That is the whole reason
 * component libraries built on Tailwind need this and plain template code
 * does not.
 */

import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}
