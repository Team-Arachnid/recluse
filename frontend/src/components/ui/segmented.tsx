import { cn } from '@/lib/utils'

/**
 * A segmented control: a sunken track, the selected segment raised out of it.
 *
 * Buttons with `aria-pressed` inside a labelled group, rather than a radio
 * group, because each segment is an action (fetch this range, restart at this
 * speed) as much as a state -- and a pressed button is what both a screen
 * reader and the tests already understand as "this one is on".
 */
export function Segmented<T extends string | number>({
  value,
  options,
  onChange,
  label,
  disabled = false,
  mono = false,
}: {
  value: T
  options: readonly { value: T; label: string }[]
  onChange: (next: T) => void
  label: string
  disabled?: boolean
  mono?: boolean
}) {
  return (
    <div
      className="bg-inset border-border inline-flex items-center rounded-lg border p-0.5"
      role="group"
      aria-label={label}
    >
      {options.map((option) => {
        const selected = option.value === value
        return (
          <button
            key={String(option.value)}
            type="button"
            onClick={() => onChange(option.value)}
            disabled={disabled}
            aria-pressed={selected}
            className={cn(
              'cursor-pointer rounded-md border px-3 py-1 text-xs font-medium transition-colors disabled:opacity-50',
              mono && 'font-mono',
              selected
                ? 'bg-hover border-border-strong text-foreground-strong'
                : 'text-muted-foreground hover:text-foreground border-transparent',
            )}
          >
            {option.label}
          </button>
        )
      })}
    </div>
  )
}
