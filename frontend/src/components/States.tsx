/**
 * The states every panel has to be able to be in besides "loaded".
 *
 * A monitoring tool that fails silently is worse than one that fails loudly, so
 * none of these is a blank page: loading says it is loading, empty says what is
 * absent and what would fill it, and an error says what broke. (A fourth, for a
 * route a later phase would implement, retired with Phase 9: no route answers
 * 501 any more.)
 */
import { CircleSlash, ServerCrash } from 'lucide-react'
import type { ReactNode } from 'react'

import { Skeleton } from '@/components/ui/skeleton'
import { cn } from '@/lib/utils'

/** Stacked bars standing in for rows. Sized to the content they replace, so
 *  the layout does not jump when the data lands. */
export function LoadingRows({ rows = 5, className }: { rows?: number; className?: string }) {
  return (
    <div className={cn('space-y-2', className)} role="status" aria-label="Loading">
      {Array.from({ length: rows }, (_, index) => (
        <Skeleton key={index} className="h-7 w-full" style={{ opacity: 1 - index * 0.12 }} />
      ))}
    </div>
  )
}

/**
 * Nothing to show, and why.
 *
 * An empty screen is an invitation to act, so the copy names the action rather
 * than apologising: "No open alerts" plus how one would arrive.
 */
export function EmptyState({
  title,
  hint,
  icon,
  className,
}: {
  title: string
  hint?: string
  icon?: ReactNode
  className?: string
}) {
  return (
    <div
      className={cn(
        'text-muted-foreground flex flex-col items-center justify-center gap-2 px-6 py-14 text-center',
        className,
      )}
    >
      <span aria-hidden="true" className="opacity-60">
        {icon ?? <CircleSlash className="size-5" />}
      </span>
      <p className="text-foreground text-sm font-medium">{title}</p>
      {hint ? <p className="max-w-sm text-xs leading-relaxed">{hint}</p> : null}
    </div>
  )
}

/** A request that failed. The message is the server's where there is one,
 *  because "something went wrong" is not actionable. */
export function ErrorState({ error, label }: { error: unknown; label?: string }) {
  const message = error instanceof Error ? error.message : 'Unknown error'
  return (
    <div className="flex items-start gap-3 px-5 py-8">
      <ServerCrash
        className="mt-0.5 size-4 shrink-0 text-[var(--critical)]"
        aria-hidden="true"
      />
      <div className="min-w-0">
        <p className="text-sm font-medium">{label ?? 'Could not load this panel'}</p>
        <p className="text-muted-foreground mt-1 font-mono text-xs break-words">{message}</p>
      </div>
    </div>
  )
}
