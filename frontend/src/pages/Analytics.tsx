/**
 * Screen 7 — analytics, for whoever is not triaging alerts.
 *
 * A team lead, a reviewer, the person answering "so what happened this week".
 * Reached from a nav link and not built as the landing page: screen 1 stays the
 * queue on purpose, because an analyst's first action on a dashboard of counters
 * is to click past it.
 *
 * Same rule as the queue — no accuracy hero tile. If this screen needs one big
 * number it is the unclassified-anomaly rate, which tells a reader something
 * about what the system is catching, or alerts per analyst hour, which tells
 * them something about what it costs.
 */
import { useState } from 'react'

import { useAnalytics, useMitreCoverage } from '@/api/queries'
import type { AnalyticsRange } from '@/api/types'
import {
  AlertsOverTime,
  FamilyBreakdown,
  MitreHeatmap,
  RankedTable,
  ThroughputPanel,
} from '@/components/analytics/Panels'
import { ScreenBody } from '@/components/AppShell'
import { ErrorBoundary } from '@/components/ErrorBoundary'
import { ErrorState, LoadingRows } from '@/components/States'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { dateTime } from '@/lib/format'
import { cn } from '@/lib/utils'

const RANGES: { value: AnalyticsRange; label: string }[] = [
  { value: '24h', label: '24 hours' },
  { value: '7d', label: '7 days' },
  { value: '30d', label: '30 days' },
  { value: 'all', label: 'All time' },
]

/** One range control above everything it scopes, so every panel re-renders
 *  against the same slice. Per-panel pickers would let two charts on one screen
 *  describe two different weeks. */
function RangePicker({
  range,
  onChange,
}: {
  range: AnalyticsRange
  onChange: (next: AnalyticsRange) => void
}) {
  return (
    <div
      className="border-border inline-flex overflow-hidden rounded-md border"
      role="group"
      aria-label="Time range"
    >
      {RANGES.map((option) => (
        <button
          key={option.value}
          type="button"
          onClick={() => onChange(option.value)}
          aria-pressed={range === option.value}
          className={cn(
            'px-3 py-1.5 text-xs transition-colors',
            range === option.value
              ? 'bg-[var(--info)] text-[var(--background)] font-medium'
              : 'text-muted-foreground hover:bg-muted',
          )}
        >
          {option.label}
        </button>
      ))}
    </div>
  )
}

export function Analytics() {
  const [range, setRange] = useState<AnalyticsRange>('24h')
  const summary = useAnalytics(range)
  const coverage = useMitreCoverage()

  return (
    <ScreenBody
      title="Analytics"
      lede="What this deployment has seen, computed from the stored alerts and verdicts. Separate from the model screen on purpose: that one reports what was measured offline, this one reports what has happened here."
      actions={<RangePicker range={range} onChange={setRange} />}
    >
      {summary.isPending ? (
        <LoadingRows rows={10} />
      ) : summary.error || !summary.data ? (
        <ErrorState error={summary.error} label="Could not load the analytics summary" />
      ) : (
        <div className="space-y-6">
          <ErrorBoundary label="SOC throughput">
            <Card>
              <CardHeader>
                <CardTitle>SOC throughput</CardTitle>
                <CardDescription>
                  The numbers that argue for the project&rsquo;s existence. These should move the
                  right way as the feedback loop does its job.
                </CardDescription>
              </CardHeader>
              <CardContent>
                <ThroughputPanel
                  throughput={summary.data.throughput}
                  unclassifiedRate={summary.data.unclassified_rate}
                />
              </CardContent>
            </Card>
          </ErrorBoundary>

          <ErrorBoundary label="Alerts over time">
            <Card>
              <CardHeader>
                <CardTitle>Alerts over time</CardTitle>
                <CardDescription>
                  Watch the unclassified series specifically — that trend is the novel-detection
                  headline.
                </CardDescription>
              </CardHeader>
              <CardContent>
                <AlertsOverTime series={summary.data.series} range={range} />
              </CardContent>
            </Card>
          </ErrorBoundary>

          <div className="grid gap-6 lg:grid-cols-2">
            <ErrorBoundary label="Family breakdown">
              <Card>
                <CardHeader>
                  <CardTitle>Attack families seen</CardTitle>
                  <CardDescription>
                    What has actually fired, not a leaderboard of severity labels.
                  </CardDescription>
                </CardHeader>
                <CardContent>
                  <FamilyBreakdown families={summary.data.families} />
                </CardContent>
              </Card>
            </ErrorBoundary>

            <ErrorBoundary label="Ranked hosts">
              <Card>
                <CardHeader>
                  <CardTitle>Who is under attack</CardTitle>
                  <CardDescription>
                    The first place anyone looks when the volume moves.
                  </CardDescription>
                </CardHeader>
                <CardContent className="space-y-6">
                  <RankedTable
                    title="Most targeted hosts"
                    unit="alerts per destination address"
                    rows={summary.data.top_destination_hosts}
                  />
                  <RankedTable
                    title="Most targeted ports"
                    unit="alerts per destination port"
                    rows={summary.data.top_destination_ports}
                  />
                  <RankedTable
                    title="Busiest sources"
                    unit="alerts per source address"
                    rows={summary.data.top_source_hosts}
                  />
                </CardContent>
              </Card>
            </ErrorBoundary>
          </div>

          <ErrorBoundary label="MITRE coverage">
            <Card>
              <CardHeader>
                <CardTitle>MITRE ATT&amp;CK coverage</CardTitle>
                <CardDescription>
                  Which techniques have fired, and how often. Techniques with no hits are visible
                  zeros, because &ldquo;never seen&rdquo; and &ldquo;cannot see&rdquo; are different
                  facts.
                </CardDescription>
              </CardHeader>
              <CardContent>
                {coverage.isPending ? (
                  <LoadingRows rows={4} />
                ) : coverage.error || !coverage.data ? (
                  <ErrorState error={coverage.error} label="Could not load technique coverage" />
                ) : (
                  <MitreHeatmap coverage={coverage.data} />
                )}
              </CardContent>
            </Card>
          </ErrorBoundary>

          <p className="text-muted-foreground text-xs">
            Generated {dateTime(summary.data.generated_at)} over the last{' '}
            {RANGES.find((option) => option.value === range)?.label.toLowerCase()}.
          </p>
        </div>
      )}
    </ScreenBody>
  )
}
