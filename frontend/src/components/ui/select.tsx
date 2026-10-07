import { ChevronDown } from 'lucide-react'
import type { ComponentProps } from 'react'

import { cn } from '@/lib/utils'

/**
 * A styled native `<select>`.
 *
 * Native rather than a custom listbox, and that is a choice rather than a
 * shortcut: the filter row is the one control an analyst uses dozens of times a
 * shift, and the platform widget already has keyboard navigation, type-ahead,
 * screen-reader semantics and sensible behaviour on a phone. A hand-built
 * popover would be more markup for strictly less.
 */
export function Select({ className, children, ...props }: ComponentProps<'select'>) {
  return (
    <div className="relative inline-flex">
      <select
        data-slot="select"
        className={cn(
          'border-border bg-card text-foreground h-8 cursor-pointer appearance-none rounded-lg border pr-8 pl-3 text-xs font-medium',
          'hover:border-border-strong hover:bg-hover focus-visible:ring-2 focus-visible:ring-[var(--ring)]',
          'outline-none transition-colors disabled:pointer-events-none disabled:opacity-50',
          className,
        )}
        {...props}
      >
        {children}
      </select>
      <ChevronDown
        className="text-muted-foreground pointer-events-none absolute top-1/2 right-2.5 size-3.5 -translate-y-1/2"
        aria-hidden="true"
      />
    </div>
  )
}

/** A select plus its label, as the filter row uses it. */
export function LabelledSelect({
  label,
  className,
  ...props
}: ComponentProps<'select'> & { label: string }) {
  return (
    <label className={cn('inline-flex items-center gap-2', className)}>
      <span className="text-muted-foreground text-xs">{label}</span>
      <Select {...props} />
    </label>
  )
}
