import { Slot } from '@radix-ui/react-slot'
import { cva, type VariantProps } from 'class-variance-authority'
import type { ComponentProps } from 'react'

import { cn } from '@/lib/utils'

/**
 * A bordered pill: a tinted surface, a border one step stronger, and the text
 * in the channel colour. Monospaced, because what goes in a pill here is a
 * status, a code or a count -- something read off a column, not prose.
 *
 * `novel` is its own variant on purpose: UNCLASSIFIED_ANOMALY must be
 * distinguishable at a glance from a high-severity known attack, so it does
 * not share the critical palette -- and wherever it appears it carries its
 * glyph as well as its colour.
 */
const tint = (token: string) =>
  `border-[color-mix(in_oklab,var(${token})_32%,transparent)] bg-[color-mix(in_oklab,var(${token})_12%,transparent)] text-[var(${token})]`

const badgeVariants = cva(
  'inline-flex w-fit shrink-0 items-center gap-1.5 rounded-md border px-2 py-0.5 font-mono text-[11px] leading-[1.35] font-medium whitespace-nowrap',
  {
    variants: {
      variant: {
        neutral: 'border-raised-border bg-raised text-muted-foreground',
        ok: tint('--ok'),
        info: tint('--info'),
        medium: tint('--medium'),
        high: tint('--high'),
        critical: tint('--critical'),
        novel: tint('--novel'),
        brand: 'border-transparent bg-brand text-white',
      },
    },
    defaultVariants: { variant: 'neutral' },
  },
)

export function Badge({
  className,
  variant,
  asChild = false,
  ...props
}: ComponentProps<'span'> & VariantProps<typeof badgeVariants> & { asChild?: boolean }) {
  const Component = asChild ? Slot : 'span'
  return (
    <Component data-slot="badge" className={cn(badgeVariants({ variant }), className)} {...props} />
  )
}

export { badgeVariants }
