import { Slot } from '@radix-ui/react-slot'
import { cva, type VariantProps } from 'class-variance-authority'
import type { ComponentProps } from 'react'

import { cn } from '@/lib/utils'

/**
 * `novel` is its own variant on purpose: UNCLASSIFIED_ANOMALY must be
 * distinguishable at a glance from a high-severity known attack, so it does
 * not share the critical palette.
 */
const badgeVariants = cva(
  'inline-flex w-fit shrink-0 items-center gap-1.5 rounded-md border px-2 py-0.5 text-xs font-medium whitespace-nowrap',
  {
    variants: {
      variant: {
        neutral: 'border-border bg-muted text-muted-foreground',
        ok: 'border-transparent bg-[color-mix(in_oklab,var(--ok)_18%,transparent)] text-[var(--ok)]',
        info: 'border-transparent bg-[color-mix(in_oklab,var(--info)_18%,transparent)] text-[var(--info)]',
        medium:
          'border-transparent bg-[color-mix(in_oklab,var(--medium)_18%,transparent)] text-[var(--medium)]',
        high: 'border-transparent bg-[color-mix(in_oklab,var(--high)_18%,transparent)] text-[var(--high)]',
        critical:
          'border-transparent bg-[color-mix(in_oklab,var(--critical)_20%,transparent)] text-[var(--critical)]',
        novel:
          'border-[color-mix(in_oklab,var(--novel)_45%,transparent)] bg-[color-mix(in_oklab,var(--novel)_16%,transparent)] text-[var(--novel)]',
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
}: ComponentProps<'span'> &
  VariantProps<typeof badgeVariants> & { asChild?: boolean }) {
  const Component = asChild ? Slot : 'span'
  return (
    <Component
      data-slot="badge"
      className={cn(badgeVariants({ variant }), className)}
      {...props}
    />
  )
}

export { badgeVariants }
