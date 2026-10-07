/**
 * The four figures above the queue.
 *
 * Exactly the four a shift lead reads before opening the first row: alerts in
 * the last hour, the projected alerts per analyst hour, the threshold that
 * produces them, and how many hosts are involved.
 *
 * What is deliberately absent: an accuracy percentage. On traffic that is 99%
 * benign, always answering benign scores 99%, and a stat strip is exactly where
 * a number gets read without its caveat. The headline figure here is alerts per
 * analyst hour, which is a staffing fact somebody can act on.
 */
import { Biohazard, Network, ShieldAlert, Users } from 'lucide-react'

import {
  useAnalytics,
  useAnomalyHistogram,
  useQueueStats,
  useThresholdProjection,
} from '@/api/queries'
import { StatTile } from '@/components/StatTile'
import { count, decimal, smallNumber } from '@/lib/format'

export function QueueStatStrip() {
  const stats = useQueueStats()
  const histogram = useAnomalyHistogram()
  const trend = useAnalytics('24h')

  // The operating threshold the shipped model actually runs at, so the
  // projection beside it describes this deployment rather than a default.
  const tau = histogram.data?.tau_anom ?? null
  const projection = useThresholdProjection(tau)

  const series = Array.isArray(trend.data?.series)
    ? trend.data.series.map((bucket) => bucket.known + bucket.unclassified)
    : undefined

  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
      <StatTile
        icon={ShieldAlert}
        label="Alerts in the last hour"
        pending={stats.isPending}
        value={count(stats.data?.alerts_last_hour)}
        caption={
          stats.data
            ? count(stats.data.open_alerts) + ' open · ' + count(stats.data.unjudged_open) + ' unjudged'
            : undefined
        }
        spark={series}
        sparkLabel="Alerts per hour over the last 24 hours"
      />
      <StatTile
        icon={Users}
        label="Alerts per analyst hour"
        tone="info"
        pending={projection.isPending && tau !== null}
        value={projection.data ? count(Math.round(projection.data.alerts_per_analyst_hour)) : '—'}
        captionTone={projection.data ? (projection.data.within_budget ? 'ok' : 'brand') : 'muted'}
        caption={
          projection.data
            ? projection.data.within_budget
              ? 'inside the budget'
              : 'over the ' + count(projection.data.budget_per_day) + '/day budget'
            : 'projected from the benign distribution'
        }
      />
      <StatTile
        icon={Biohazard}
        label="Stage 2 threshold"
        tone="novel"
        pending={histogram.isPending}
        value={tau === null ? '—' : decimal(tau, 3)}
        captionTone="muted"
        caption={projection.data ? 'FPR ' + smallNumber(projection.data.fpr) : undefined}
      />
      <StatTile
        icon={Network}
        label="Hosts affected"
        tone="muted"
        pending={stats.isPending}
        value={count(stats.data?.hosts_affected)}
        caption={stats.data ? count(stats.data.sources_seen) + ' distinct sources' : undefined}
      />
    </div>
  )
}
