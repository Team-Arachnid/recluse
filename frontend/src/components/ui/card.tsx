import type { ComponentProps } from 'react'

import { cn } from '@/lib/utils'

/**
 * A matte panel: one step up from the page, a hairline border, no glow.
 *
 * Everything on a screen sits in one of these, so the console reads as a set of
 * instruments rather than as a page of floating text.
 */
export function Card({ className, ...props }: ComponentProps<'div'>) {
  return (
    <div
      data-slot="card"
      className={cn(
        'bg-card text-card-foreground border-border rounded-[var(--radius-card)] border',
        className,
      )}
      {...props}
    />
  )
}

export function CardHeader({ className, ...props }: ComponentProps<'div'>) {
  return (
    <div
      data-slot="card-header"
      className={cn('flex flex-col gap-1.5 px-5 pt-4 pb-3.5', className)}
      {...props}
    />
  )
}

/**
 * The panel title, on the console's crimson rule.
 *
 * The rule is a separate decorative span, so the heading's text is exactly its
 * words -- searchable, read by a screen reader as written, and stable for the
 * tests that look panels up by their title.
 */
export function CardTitle({ className, children, ...props }: ComponentProps<'h3'>) {
  return (
    <h3
      data-slot="card-title"
      className={cn(
        'text-foreground-strong flex items-center gap-2.5 text-[15px] leading-snug font-semibold tracking-tight',
        className,
      )}
      {...props}
    >
      <span aria-hidden="true" className="bg-brand-bright h-4 w-[3px] shrink-0 rounded-full" />
      <span className="flex min-w-0 items-center gap-2">{children}</span>
    </h3>
  )
}

export function CardDescription({ className, ...props }: ComponentProps<'p'>) {
  return (
    <p
      data-slot="card-description"
      className={cn('text-muted-foreground text-xs leading-relaxed', className)}
      {...props}
    />
  )
}

export function CardContent({ className, ...props }: ComponentProps<'div'>) {
  return <div data-slot="card-content" className={cn('px-5 pb-5', className)} {...props} />
}

export function CardFooter({ className, ...props }: ComponentProps<'div'>) {
  return (
    <div
      data-slot="card-footer"
      className={cn('border-divider flex items-center border-t px-5 py-3', className)}
      {...props}
    />
  )
}
