/**
 * The thin strip above the queue.
 *
 * Four figures, hairline-divided -- not four cards. A card implies a thing you
 * can act on, and these are a readout. Cards here would also cost about sixty
 * pixels of vertical space, which is two queue rows an analyst would have to
 * scroll for.
 *
 * What is deliberately absent: an accuracy percentage. On traffic that is 99%
 * benign, always answering benign scores 99%, and a strip is exactly where a
 * number gets read without its caveat. The headline figure here is alerts per
 * analyst hour, which is a staffing fact somebody can act on.
 */
import { Pause, Play } from 'lucide-react'
import type { ReactNode } from 'react'

import { useAnomalyHistogram, useQueueStats, useThresholdProjection } from '@/api/queries'
import { useAlertStream } from '@/api/stream'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { count, decimal, smallNumber } from '@/lib/format'
import { cn } from '@/lib/utils'

function Figure({
  label,
  value,
  note,
  pending,
  className,
}: {
  label: string
  value: ReactNode
  note?: ReactNode
  pending?: boolean
  className?: string
}) {
  return (
    <div className={cn('min-w-0 px-4 py-2.5 first:pl-4', className)}>
      {pending ? (
        <Skeleton className="h-6 w-16" />
      ) : (
        // Proportional figures, not tabular: these do not align vertically with
        // anything, and equal-width digits make a three-digit number look loose
        // at this size.
        <p className="truncate text-xl leading-tight font-semibold">{value}</p>
      )}
      <p className="text-muted-foreground mt-0.5 truncate text-xs">{label}</p>
      {note ? (
        <p className="text-muted-foreground/80 mt-0.5 truncate text-[11px]">{note}</p>
      ) : null}
    </div>
  )
}

export function QueueStatStrip() {
  const stats = useQueueStats()
  const histogram = useAnomalyHistogram()

  // The operating threshold the shipped model actually runs at, so the
  // projection beside it describes this deployment rather than a default.
  const tau = histogram.data?.tau_anom ?? null
  const projection = useThresholdProjection(tau)

  const { following, setFollowing, connected } = useAlertStream()

  return (
    <div className="border-border bg-card/40 flex flex-wrap items-stretch border-b">
      <Figure
        label="Alerts in the last hour"
        pending={stats.isPending}
        value={count(stats.data?.alerts_last_hour)}
        note={
          stats.data
            ? count(stats.data.open_alerts) + ' open · ' + count(stats.data.unjudged_open) + ' unjudged'
            : undefined
        }
      />

      <div className="border-border border-l" />
      <Figure
        label="Alerts per analyst hour"
        pending={projection.isPending && tau !== null}
        value={
          projection.data ? count(Math.round(projection.data.alerts_per_analyst_hour)) : '—'
        }
        note={
          projection.data
            ? projection.data.within_budget
              ? 'inside the configured budget'
              : 'over the ' + count(projection.data.budget_per_day) + '/day budget'
            : 'projected from the measured benign distribution'
        }
      />

      <div className="border-border border-l" />
      <Figure
        label="Stage 2 threshold"
        pending={histogram.isPending}
        value={tau === null ? '—' : decimal(tau, 3)}
        note={
          projection.data
            ? 'false-positive rate ' + smallNumber(projection.data.fpr)
            : undefined
        }
      />

      <div className="border-border border-l" />
      <Figure
        label="Hosts affected"
        pending={stats.isPending}
        value={count(stats.data?.hosts_affected)}
        note={
          stats.data ? count(stats.data.sources_seen) + ' distinct sources' : undefined
        }
      />

      <div className="border-border ml-auto flex items-center gap-3 border-l px-4">
        {/*
         * Pausing stops the table following the feed; it does not disconnect.
         * The queue is sorted by risk, so a new high-risk alert jumps to the top
         * and pushes everything down -- which at 100x means the row an analyst
         * is about to click moves out from under the cursor. Holding the table
         * still is the fix, and making it explicit is better than a tool that
         * silently freezes when it thinks you are busy.
         */}
        <Button
          variant="outline"
          size="sm"
          onClick={() => setFollowing(!following)}
          aria-pressed={following}
        >
          {following ? <Pause aria-hidden="true" /> : <Play aria-hidden="true" />}
          {following ? 'Following' : 'Paused'}
        </Button>
        <span className="text-muted-foreground hidden text-xs xl:inline">
          {following
            ? connected
              ? 'New alerts appear as they arrive'
              : 'Waiting for a traffic source'
            : 'Table held still'}
        </span>
      </div>
    </div>
  )
}
