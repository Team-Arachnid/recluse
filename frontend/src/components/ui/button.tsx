import { Slot } from '@radix-ui/react-slot'
import { cva, type VariantProps } from 'class-variance-authority'
import type { ComponentProps } from 'react'

import { cn } from '@/lib/utils'

/**
 * Buttons, matte.
 *
 * `default` is the crimson primary and there is at most one per panel: the
 * action the panel exists for. `success` is for the one place a decision is
 * "this was benign" -- a verdict, not a containment action; nothing in this
 * application has a button that changes traffic.
 */
const buttonVariants = cva(
  "inline-flex cursor-pointer items-center justify-center gap-1.5 rounded-lg font-medium whitespace-nowrap transition-colors outline-none focus-visible:ring-2 focus-visible:ring-[var(--ring)] disabled:pointer-events-none disabled:opacity-50 [&_svg]:size-3.5 [&_svg]:shrink-0",
  {
    variants: {
      variant: {
        default: 'bg-brand hover:bg-brand-hover text-white',
        outline:
          'border-border bg-card text-foreground hover:border-border-strong hover:bg-hover border',
        ghost: 'text-muted-foreground hover:bg-hover hover:text-foreground bg-transparent',
        success:
          'border border-[color-mix(in_oklab,var(--ok)_35%,transparent)] bg-[color-mix(in_oklab,var(--ok)_12%,transparent)] text-[var(--ok)] hover:bg-[color-mix(in_oklab,var(--ok)_20%,transparent)]',
        danger:
          'border border-[color-mix(in_oklab,var(--critical)_35%,transparent)] bg-[color-mix(in_oklab,var(--critical)_10%,transparent)] text-[var(--critical)] hover:bg-[color-mix(in_oklab,var(--critical)_18%,transparent)]',
      },
      size: {
        sm: 'h-8 px-3 text-xs',
        default: 'h-9 px-4 text-sm',
        icon: 'size-8 text-xs',
      },
    },
    defaultVariants: { variant: 'default', size: 'default' },
  },
)

export function Button({
  className,
  variant,
  size,
  asChild = false,
  ...props
}: ComponentProps<'button'> & VariantProps<typeof buttonVariants> & { asChild?: boolean }) {
  const Component = asChild ? Slot : 'button'
  return (
    <Component
      data-slot="button"
      className={cn(buttonVariants({ variant, size }), className)}
      {...props}
    />
  )
}

export { buttonVariants }
