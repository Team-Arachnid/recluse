import type { ComponentProps } from 'react'
import { useEffect, useRef } from 'react'

import { cn } from '@/lib/utils'

/**
 * A styled native checkbox, with a real indeterminate state.
 *
 * `indeterminate` is a DOM property and not an attribute, so it cannot be set
 * from JSX -- hence the ref. It matters here: the header checkbox of a queue
 * where some rows are selected has to show "partly", because rendering it
 * unchecked tells the analyst nothing is selected while the footer says four
 * things are.
 */
export function Checkbox({
  className,
  indeterminate = false,
  ...props
}: ComponentProps<'input'> & { indeterminate?: boolean }) {
  const ref = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (ref.current) ref.current.indeterminate = indeterminate
  }, [indeterminate])

  return (
    <input
      ref={ref}
      type="checkbox"
      data-slot="checkbox"
      className={cn(
        'border-border accent-[var(--brand)] size-3.5 cursor-pointer rounded-[3px] border',
        'focus-visible:ring-2 focus-visible:ring-[var(--ring)] outline-none',
        className,
      )}
      {...props}
    />
  )
}
