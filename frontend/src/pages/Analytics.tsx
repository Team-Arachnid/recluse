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
import { Biohazard, Crosshair, Gavel, ShieldAlert } from 'lucide-react'
import { useState } from 'react'

import { useAnalytics, useMitreCoverage } from '@/api/queries'
import type { AnalyticsRange, AnalyticsSummary } from '@/api/types'
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
import { StatTile } from '@/components/StatTile'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Segmented } from '@/components/ui/segmented'
import { count, dateTime, percent } from '@/lib/format'

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
  return <Segmented value={range} options={RANGES} onChange={onChange} label="Time range" />
}

/**
 * The overview row: the sample console's four tiles, filled from the same
 * summary the panels below read. Sparklines are the summary's own series --
 * nothing here is drawn that was not counted.
 */
function OverviewTiles({ summary }: { summary: AnalyticsSummary }) {
  const known = summary.series.map((bucket) => bucket.known + bucket.unclassified)
  const novel = summary.series.map((bucket) => bucket.unclassified)
  const throughput = summary.throughput
  const hosts = summary.top_destination_hosts.length

  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
      <StatTile
        icon={ShieldAlert}
        label="Alerts in range"
        value={count(summary.total_alerts)}
        caption={count(throughput.opened - throughput.resolved) + ' outstanding'}
        spark={known}
        sparkLabel="Alerts per bucket"
      />
      <StatTile
        icon={Biohazard}
        label="Unclassified anomalies"
        tone="novel"
        value={count(summary.unclassified_alerts)}
        caption={percent(summary.unclassified_rate, 1) + ' of alerts'}
        spark={novel}
        sparkLabel="Unclassified anomalies per bucket"
      />
      <StatTile
        icon={Crosshair}
        label="Hosts under attack"
        tone="high"
        value={count(hosts)}
        caption={
          summary.top_destination_hosts[0]
            ? 'most hit ' + summary.top_destination_hosts[0].value
            : 'none yet'
        }
        captionTone="muted"
      />
      <StatTile
        icon={Gavel}
        label="Verdicts recorded"
        tone="ok"
        value={count(throughput.verdicts)}
        caption={
          throughput.true_positive_rate === null
            ? 'none decided yet'
            : percent(throughput.true_positive_rate, 0) + ' confirmed real'
        }
        captionTone="muted"
      />
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
        <div className="space-y-4">
          <OverviewTiles summary={summary.data} />

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
