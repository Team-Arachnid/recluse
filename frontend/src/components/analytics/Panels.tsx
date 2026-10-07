/**
 * Analytics panels.
 *
 * Unlike the Model screen, everything here is computed from the database: it
 * describes what this deployment has actually seen. The two are deliberately not
 * mixed on one screen, because "the model scores 0.77 PR-AUC on the held-out
 * day" and "we have seen 1,284 alerts this week" are different kinds of claim
 * and a reader who confuses them draws the wrong conclusion from both.
 */
import { Radar } from 'lucide-react'
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

import type {
  AlertFamily,
  AnalyticsRange,
  CountedPair,
  MitreCoverage,
  TimeBucket,
} from '@/api/types'
import { FAMILY_ICON } from '@/components/alert/AlertGlyphs'
import {
  AXIS_PROPS,
  ChartFrame,
  GRID_PROPS,
  heatFill,
  ScaleKey,
  TooltipCard,
  TooltipRow,
} from '@/components/charts/Chart'
import { EmptyState } from '@/components/States'
import { Badge } from '@/components/ui/badge'
import { FAMILY_LABEL, type FAMILIES } from '@/lib/alerts'
import { clockTime, count, dateTime, percent, sentenceCase } from '@/lib/format'

const familyLabel = (name: string): string =>
  FAMILY_LABEL[name as (typeof FAMILIES)[number]] ?? sentenceCase(name)

/**
 * Alerts over time, stacked: known families against unclassified anomalies.
 *
 * Two series and an emphasis pairing rather than two equal hues. The
 * unclassified line is the novel-detection headline and the one a reviewer will
 * ask about if it spikes, so it keeps the chroma while the known-family baseline
 * recedes toward the surface. That also happens to be the only pairing of these
 * two that a colourblind reader can separate.
 */
export function AlertsOverTime({
  series,
  range,
}: {
  series: TimeBucket[]
  range: AnalyticsRange
}) {
  if (series.length === 0) {
    return (
      <EmptyState
        title="No alerts in this range"
        hint="Widen the range, or start a replay from the Live screen."
      />
    )
  }

  const hourly = range === '24h'
  const points = series.map((bucket) => ({
    bucket: bucket.bucket,
    label: hourly ? clockTime(bucket.bucket) : dateTime(bucket.bucket),
    known: bucket.known,
    unclassified: bucket.unclassified,
  }))

  return (
    <ChartFrame
      height={250}
      series={[
        { label: 'Known families', color: 'var(--series-known)' },
        { label: 'Unclassified anomalies', color: 'var(--series-novel)' },
      ]}
      caption="Stacked rather than totalled. Folding the unclassified count into one line would hide exactly the claim this project makes — and empty buckets are drawn as zeros, so an outage does not read as steady traffic."
    >
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={points} margin={{ top: 4, right: 8, bottom: 4, left: 0 }}>
          <CartesianGrid {...GRID_PROPS} />
          <XAxis
            dataKey="label"
            {...AXIS_PROPS}
            interval="preserveStartEnd"
            minTickGap={36}
          />
          <YAxis {...AXIS_PROPS} width={40} tickFormatter={(value: number) => count(value)} />
          <Tooltip
            content={({ active, payload }) => {
              if (!active || !payload?.length) return null
              const point = payload[0].payload as (typeof points)[number]
              return (
                <TooltipCard title={dateTime(point.bucket)}>
                  <TooltipRow
                    color="var(--series-novel)"
                    label="Unclassified"
                    value={count(point.unclassified)}
                  />
                  <TooltipRow
                    color="var(--series-known)"
                    label="Known families"
                    value={count(point.known)}
                  />
                  <TooltipRow label="Total" value={count(point.known + point.unclassified)} />
                </TooltipCard>
              )
            }}
          />
          <Area
            dataKey="known"
            stackId="alerts"
            type="stepAfter"
            stroke="var(--series-known)"
            strokeWidth={2}
            fill="var(--series-known)"
            fillOpacity={0.3}
            isAnimationActive={false}
          />
          <Area
            dataKey="unclassified"
            stackId="alerts"
            type="stepAfter"
            stroke="var(--series-novel)"
            strokeWidth={2}
            fill="var(--series-novel)"
            fillOpacity={0.45}
            isAnimationActive={false}
          />
        </AreaChart>
      </ResponsiveContainer>
    </ChartFrame>
  )
}

/**
 * What has actually been seen, by family.
 *
 * One series, so one colour for every bar. Shading each bar darker-where-bigger
 * would double-encode the length the chart already shows, and these categories
 * have no natural order to justify a ramp.
 */
export function FamilyBreakdown({ families }: { families: CountedPair[] }) {
  if (families.length === 0) {
    return (
      <EmptyState
        title="No named families yet"
        hint="Either nothing has fired, or everything that has was an unclassified anomaly — which is itself the finding."
      />
    )
  }

  // The reference console's "threat activity" rows: a glyph, the family, its
  // share, and a bar -- ranked, because the first question is which one.
  const total = families.reduce((sum, pair) => sum + pair.count, 0) || 1
  const peak = Math.max(1, ...families.map((pair) => pair.count))
  const ranked = [...families].sort((a, b) => b.count - a.count)

  return (
    <ul className="space-y-3.5">
      {ranked.map((pair) => {
        const family = pair.value as AlertFamily
        const Icon = FAMILY_ICON[family] ?? Radar
        const share = pair.count / total
        return (
          <li key={pair.value}>
            <div className="mb-1.5 flex items-center justify-between gap-3 text-sm">
              <span className="text-foreground flex min-w-0 items-center gap-2.5 font-medium">
                <Icon className="text-subtle-foreground size-4 shrink-0" aria-hidden="true" />
                <span className="truncate">{familyLabel(pair.value)}</span>
              </span>
              <span className="text-muted-foreground shrink-0 font-mono text-[11.5px]">
                {count(pair.count)} alert{pair.count === 1 ? '' : 's'} · {percent(share, 0)}
              </span>
            </div>
            <div
              aria-hidden="true"
              className="bg-inset border-border h-2 w-full overflow-hidden rounded-full border"
            >
              <div
                className="h-full rounded-full"
                style={{
                  width: Math.max(2, (pair.count / peak) * 100) + '%',
                  background:
                    'linear-gradient(90deg, color-mix(in oklab, var(--brand) 70%, transparent), var(--brand-bright))',
                }}
              />
            </div>
          </li>
        )
      })}
    </ul>
  )
}

/** A small ranked table. Not a chart: ten labelled values with one measure read
 *  faster as rows, and the first question anyone asks of them is "which one". */
export function RankedTable({
  title,
  unit,
  rows,
  mono = true,
}: {
  title: string
  unit: string
  rows: CountedPair[]
  mono?: boolean
}) {
  const peak = Math.max(1, ...rows.map((row) => row.count))

  return (
    <div>
      <h3 className="mb-3 text-sm font-semibold tracking-tight">{title}</h3>
      {rows.length === 0 ? (
        <p className="text-muted-foreground text-xs">Nothing in this range.</p>
      ) : (
        <ul className="space-y-1.5">
          {rows.map((row) => (
            <li key={row.value} className="flex items-center gap-3 text-xs">
              <span className={'w-32 shrink-0 truncate' + (mono ? ' font-mono' : '')}>
                {row.value}
              </span>
              <span
                aria-hidden="true"
                className="h-1.5 min-w-0 flex-1 overflow-hidden rounded-full bg-[var(--color-muted)]"
              >
                <span
                  className="block h-full rounded-full"
                  style={{
                    width: Math.max(2, (row.count / peak) * 100) + '%',
                    backgroundColor: 'var(--series-known)',
                  }}
                />
              </span>
              <span className="tabular w-12 shrink-0 text-right font-mono">
                {count(row.count)}
              </span>
            </li>
          ))}
        </ul>
      )}
      <p className="text-muted-foreground mt-2 text-[11px]">{unit}</p>
    </div>
  )
}

/**
 * The MITRE coverage grid.
 *
 * The axis comes from the technique vocabulary, not from the alerts that have
 * fired, so a technique with no hits is a visible zero rather than a missing
 * cell. "We have never seen this" and "we cannot see this" look identical when
 * the axis is built from the data, and telling them apart is the entire point of
 * a coverage grid.
 */
export function MitreHeatmap({ coverage }: { coverage: MitreCoverage }) {
  const peak = Math.max(1, ...coverage.techniques.map((row) => row.count))

  return (
    <div>
      <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
        {coverage.techniques.map((row) => (
          <a
            key={row.technique_id}
            href={row.url}
            target="_blank"
            rel="noreferrer"
            className="border-border hover:border-[var(--ring)] block rounded-md border p-2.5 transition-colors"
            style={{ backgroundColor: heatFill(row.count / peak) }}
            title={row.means}
          >
            <div className="flex items-baseline justify-between gap-2">
              <span className="font-mono text-xs font-medium">{row.technique_id}</span>
              <span className="tabular font-mono text-sm font-semibold">{count(row.count)}</span>
            </div>
            <p className="mt-0.5 truncate text-xs">{row.name}</p>
            <p className="text-muted-foreground mt-0.5 truncate text-[11px]">
              {familyLabel(row.family)}
            </p>
          </a>
        ))}
      </div>

      <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
        <ScaleKey label="Times fired" from="0" max={count(peak)} />
        <div className="flex items-center gap-2 text-xs">
          <Badge variant="novel">{count(coverage.unclassified_anomalies)}</Badge>
          <span className="text-muted-foreground">
            unclassified anomalies, which map to no technique at all
          </span>
        </div>
      </div>

      <p className="text-muted-foreground mt-3 max-w-2xl text-xs leading-relaxed">
        Stage 2 detections sit beside this grid rather than inside it. Giving them a technique id
        would be a fabrication; leaving them out entirely would understate the detections this
        project is proudest of.
      </p>
    </div>
  )
}

/**
 * SOC throughput — the panel that argues for the project's existence.
 *
 * Opened against resolved, how long a verdict takes, and the true-positive rate.
 * A flat line here is a finding rather than a blank chart.
 */
export function ThroughputPanel({
  throughput,
  unclassifiedRate,
}: {
  throughput: {
    opened: number
    resolved: number
    verdicts: number
    true_positives: number
    false_positives: number
    unsure: number
    true_positive_rate: number | null
    mean_seconds_to_verdict: number | null
  }
  unclassifiedRate: number
}) {
  const backlog = throughput.opened - throughput.resolved

  return (
    <dl className="grid gap-x-8 gap-y-5 sm:grid-cols-2 lg:grid-cols-4">
      <div>
        <dd className="text-2xl leading-tight font-semibold">{count(throughput.opened)}</dd>
        <dt className="text-muted-foreground text-xs">
          alerts opened in this range
          {backlog > 0 ? ' · ' + count(backlog) + ' still outstanding' : ''}
        </dt>
      </div>
      <div>
        <dd className="text-2xl leading-tight font-semibold">{count(throughput.resolved)}</dd>
        <dt className="text-muted-foreground text-xs">
          closed or dismissed — {percent(throughput.opened ? throughput.resolved / throughput.opened : 0, 0)}{' '}
          of what arrived
        </dt>
      </div>
      <div>
        <dd className="text-2xl leading-tight font-semibold">
          {throughput.true_positive_rate === null
            ? '—'
            : percent(throughput.true_positive_rate, 1)}
        </dd>
        <dt className="text-muted-foreground text-xs">
          of decided alerts were real, from {count(throughput.verdicts)} verdict
          {throughput.verdicts === 1 ? '' : 's'}
        </dt>
      </div>
      <div>
        {/* The candidate hero number for this screen, and not an accuracy
            percentage: this one tells a reader something they can act on. */}
        <dd className="text-2xl leading-tight font-semibold">
          {percent(unclassifiedRate, 1)}
        </dd>
        <dt className="text-muted-foreground text-xs">
          of alerts were unclassified anomalies — traffic no signature and no trained family
          explains
        </dt>
      </div>
    </dl>
  )
}
