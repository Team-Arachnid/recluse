/**
 * Drift panels.
 *
 * The obvious chart here is "PSI per feature over time, one line per feature",
 * and it is not buildable: there are ninety-two features and no categorical
 * palette separates more than eight series. Generating hues past that produces
 * colours a colourblind reader cannot tell apart and a full-colour reader cannot
 * either.
 *
 * So the question is split into the three charts it actually contains:
 *
 * 1. **What has moved, now** -- a ranked bar per feature, one measure, coloured by
 *    warning band. Band is a status (stable / moderate / significant), so it gets
 *    status colours and every bar carries its band as a word.
 * 2. **Whether it has been moving** -- a line chart of the worst four features
 *    only, direct-labelled. Four is inside every palette gate and is as many
 *    trends as anyone reads at once.
 * 3. **Whether the score baseline has moved** -- the training benign
 *    reconstruction-error distribution against the observed one, on shared edges.
 */
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  ReferenceArea,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

import type { DriftBand, DriftResponse, DriftSeries, DriftSnapshot } from '@/api/types'
import {
  AXIS_PROPS,
  ChartFrame,
  GRID_PROPS,
  TooltipCard,
  TooltipRow,
} from '@/components/charts/Chart'
import { EmptyState } from '@/components/States'
import { Badge } from '@/components/ui/badge'
import { clockTime, count, dateTime, decimal, duration, percent } from '@/lib/format'
import { cn } from '@/lib/utils'

/**
 * Band colours are the status palette, not series colours.
 *
 * A PSI band means stable / worth looking at / act, which is a state rather than
 * an identity, so it wears status tokens -- and it is never carried by colour
 * alone: every band is written out beside its mark.
 */
export const BAND_COLOR: Record<DriftBand, string> = {
  stable: 'var(--ok)',
  moderate: 'var(--medium)',
  significant: 'var(--critical)',
}

export const BAND_LABEL: Record<DriftBand, string> = {
  stable: 'Stable',
  moderate: 'Moderate shift',
  significant: 'Significant shift',
}

export const BAND_VARIANT: Record<DriftBand, 'ok' | 'medium' | 'critical'> = {
  stable: 'ok',
  moderate: 'medium',
  significant: 'critical',
}

/** How many features the ranked chart shows before it stops being readable. */
const RANKED_LIMIT = 20

/** How many features the trend chart shows. Four is inside every palette gate. */
const TREND_LIMIT = 4

const TREND_COLORS = [
  'var(--series-attack)',
  'var(--series-novel)',
  'var(--series-benign)',
  'var(--series-known)',
]

export function RetrainBanner({ snapshot }: { snapshot: DriftSnapshot }) {
  if (!snapshot.retrain_recommended) {
    return (
      <div className="border-border flex items-start gap-3 rounded-[var(--radius-card)] border px-4 py-3">
        <span
          aria-hidden="true"
          className="mt-1.5 size-2 shrink-0 rounded-full bg-[var(--ok)]"
        />
        <div>
          <p className="text-sm font-medium">No feature has crossed the significant band</p>
          <p className="text-muted-foreground mt-1 text-xs leading-relaxed">
            Worst PSI {decimal(snapshot.max_psi, 4)} across {snapshot.features_scored} features.
            The baseline still describes the traffic.
          </p>
        </div>
      </div>
    )
  }

  return (
    <div
      className="flex items-start gap-3 rounded-[var(--radius-card)] border px-4 py-3"
      style={{
        borderColor: 'color-mix(in oklab, var(--critical) 45%, transparent)',
        backgroundColor: 'color-mix(in oklab, var(--critical) 8%, transparent)',
      }}
      role="status"
    >
      <span
        aria-hidden="true"
        className="mt-1.5 size-2 shrink-0 rounded-full bg-[var(--critical)]"
      />
      <div className="min-w-0">
        <p className="text-sm font-medium">
          Retrain recommended — {count(snapshot.significant_count)} feature
          {snapshot.significant_count === 1 ? '' : 's'} past 0.25
        </p>
        <p className="text-muted-foreground mt-1 text-xs leading-relaxed">
          Worst PSI {decimal(snapshot.max_psi, 4)}. Raised on <em>any</em> single feature
          crossing, not on the average: a mean over {snapshot.features_scored} features hides one
          feature that has moved entirely, which is what a drifted deployment usually looks like.
        </p>
        {snapshot.notes ? (
          <p className="text-muted-foreground mt-2 text-xs leading-relaxed">{snapshot.notes}</p>
        ) : null}
      </div>
    </div>
  )
}

/** What the run observed, and what it was compared against. */
export function SnapshotSummary({ snapshot }: { snapshot: DriftSnapshot }) {
  const windowHours =
    (new Date(snapshot.observed_to).getTime() - new Date(snapshot.observed_from).getTime()) / 1000

  return (
    <dl className="grid gap-x-8 gap-y-4 sm:grid-cols-2 lg:grid-cols-4">
      <div>
        <dd className="text-xl leading-tight font-semibold">{count(snapshot.rows_observed)}</dd>
        <dt className="text-muted-foreground text-xs">
          sampled flows scored, over {duration(windowHours)}
        </dt>
      </div>
      <div>
        <dd className="tabular text-xl leading-tight font-semibold">
          {decimal(snapshot.max_psi, 3)}
        </dd>
        <dt className="text-muted-foreground text-xs">worst feature PSI</dt>
      </div>
      <div>
        <dd className="text-xl leading-tight font-semibold">
          {count(snapshot.significant_count)}
          <span className="text-muted-foreground text-sm font-normal">
            {' / '}
            {count(snapshot.features_scored)}
          </span>
        </dd>
        <dt className="text-muted-foreground text-xs">features past 0.25</dt>
      </div>
      <div>
        <dd className="text-sm leading-snug font-medium">{dateTime(snapshot.computed_at)}</dd>
        <dt className="text-muted-foreground text-xs">
          computed, against {snapshot.reference || 'an unnamed reference'}
        </dt>
      </div>
    </dl>
  )
}

/**
 * What has moved, ranked.
 *
 * One measure per feature, so one encoding: length. The band thresholds are drawn
 * as solid reference lines because they are real decision boundaries -- the thing
 * a dashed line is usually misused to suggest, and here it actually is.
 */
export function RankedPsi({ snapshot }: { snapshot: DriftSnapshot }) {
  const ranked = snapshot.features.slice(0, RANKED_LIMIT)
  if (ranked.length === 0) {
    return (
      <EmptyState
        title="Nothing was scored in this run"
        hint={snapshot.notes ?? 'The window held fewer rows than the floor the job requires.'}
      />
    )
  }

  const hidden = snapshot.features.length - ranked.length
  const widest = Math.max(...ranked.map((entry) => entry.psi), 0.3)

  return (
    <div>
      <ChartFrame
        height={ranked.length * 22 + 36}
        series={(['significant', 'moderate', 'stable'] as DriftBand[]).map((band) => ({
          label: BAND_LABEL[band],
          color: BAND_COLOR[band],
        }))}
      >
        <ResponsiveContainer width="100%" height="100%">
          <BarChart
            data={ranked}
            layout="vertical"
            margin={{ top: 0, right: 48, bottom: 18, left: 0 }}
            barCategoryGap={3}
          >
            <XAxis
              type="number"
              domain={[0, widest * 1.08]}
              {...AXIS_PROPS}
              tickFormatter={(value: number) => decimal(value, 2)}
              label={{
                value: 'Population stability index',
                position: 'insideBottom',
                offset: -12,
                fontSize: 11,
              }}
            />
            <YAxis
              type="category"
              dataKey="feature"
              width={178}
              {...AXIS_PROPS}
              axisLine={false}
              tick={{ fontSize: 10, fontFamily: 'var(--font-mono)', fill: 'var(--color-muted-foreground)' }}
            />
            <ReferenceLine x={0.1} stroke="var(--medium)" />
            <ReferenceLine x={0.25} stroke="var(--critical)" />
            <Tooltip
              cursor={{ fill: 'var(--color-muted)', opacity: 0.4 }}
              content={({ active, payload }) => {
                if (!active || !payload?.length) return null
                const point = payload[0].payload as (typeof ranked)[number]
                return (
                  <TooltipCard title={<span className="font-mono">{point.feature}</span>}>
                    <TooltipRow
                      color={BAND_COLOR[point.band]}
                      label={BAND_LABEL[point.band]}
                      value={decimal(point.psi, 4)}
                    />
                  </TooltipCard>
                )
              }}
            />
            <Bar
              dataKey="psi"
              radius={[0, 4, 4, 0]}
              barSize={13}
              isAnimationActive={false}
              label={{
                position: 'right',
                fontSize: 10,
                fill: 'var(--color-muted-foreground)',
                formatter: (value: unknown) => decimal(Number(value), 2),
              }}
            >
              {ranked.map((entry) => (
                <Cell key={entry.feature} fill={BAND_COLOR[entry.band]} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </ChartFrame>

      <p className="text-muted-foreground mt-2 text-xs leading-relaxed">
        The two lines are the decision boundaries: 0.1 moderate, 0.25 significant.
        {hidden > 0
          ? ` ${count(hidden)} further feature${hidden === 1 ? '' : 's'} scored below these — the full set is in the table view below.`
          : ''}
      </p>
    </div>
  )
}

/** Has it been moving, or did it start last night? */
export function PsiTrend({ series }: { series: DriftSeries[] }) {
  const tracked = series.slice(0, TREND_LIMIT)
  if (tracked.length === 0 || tracked[0].points.length < 2) {
    return (
      <EmptyState
        title="One snapshot so far"
        hint="A trend needs a second run. The nightly job writes one per night; `make drift` writes one now."
      />
    )
  }

  // One row per timestamp, one column per tracked feature -- the shape Recharts
  // reads for a multi-series line chart.
  const stamps = Array.from(
    new Set(tracked.flatMap((entry) => entry.points.map((point) => point.computed_at))),
  ).sort()

  const rows = stamps.map((stamp) => {
    const row: Record<string, string | number> = { stamp, label: clockTime(stamp) }
    for (const entry of tracked) {
      const point = entry.points.find((candidate) => candidate.computed_at === stamp)
      if (point) row[entry.feature] = point.psi
    }
    return row
  })

  return (
    <ChartFrame
      height={240}
      series={tracked.map((entry, index) => ({
        label: entry.feature,
        color: TREND_COLORS[index % TREND_COLORS.length],
        line: true,
      }))}
      caption="The four features with the worst PSI on any run. Four rather than ninety-two: no palette separates more than eight series, and a chart nobody can read apart is not a measurement."
    >
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={rows} margin={{ top: 4, right: 12, bottom: 4, left: 0 }}>
          <CartesianGrid {...GRID_PROPS} />
          {/* The bands as zones rather than lines here: with several series
              crossing them, a filled region reads as background and a line
              competes with the data. */}
          <ReferenceArea y1={0.1} y2={0.25} fill="var(--medium)" fillOpacity={0.07} />
          <ReferenceArea y1={0.25} y2={10_000} fill="var(--critical)" fillOpacity={0.07} />
          <XAxis dataKey="label" {...AXIS_PROPS} minTickGap={32} />
          <YAxis {...AXIS_PROPS} width={42} tickFormatter={(value: number) => decimal(value, 2)} />
          <Tooltip
            content={({ active, payload, label }) => {
              if (!active || !payload?.length) return null
              return (
                <TooltipCard title={String(label)}>
                  {payload.map((entry) => (
                    <TooltipRow
                      key={String(entry.name)}
                      color={String(entry.color)}
                      label={<span className="font-mono">{String(entry.name)}</span>}
                      value={decimal(Number(entry.value), 4)}
                    />
                  ))}
                </TooltipCard>
              )
            }}
          />
          {tracked.map((entry, index) => (
            <Line
              key={entry.feature}
              dataKey={entry.feature}
              type="monotone"
              stroke={TREND_COLORS[index % TREND_COLORS.length]}
              strokeWidth={2}
              dot={{ r: 2 }}
              isAnimationActive={false}
              connectNulls
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </ChartFrame>
  )
}

/**
 * The training benign score distribution against the observed one.
 *
 * Shared bin edges, which is the whole point: when these two separate, the
 * baseline has moved. Two curves binned independently would separate for reasons
 * that have nothing to do with drift.
 */
export function BaselineOverlay({ drift }: { drift: DriftResponse }) {
  const baseline = drift.baseline as
    | { edges?: number[]; counts?: number[]; rows?: number }
    | null
    | undefined
  const observed = drift.latest?.score_histogram as
    | { edges?: number[]; shares?: number[]; rows?: number }
    | null
    | undefined

  if (!baseline?.edges || !baseline.counts) {
    return (
      <EmptyState
        title="No training baseline is loaded"
        hint="The Stage 2 error distributions arrive with the Phase 3 training artifacts."
      />
    )
  }

  const baselineRows = baseline.rows || 1
  const points = baseline.counts.map((value, index) => ({
    edge: baseline.edges?.[index] ?? 0,
    expected: value / baselineRows,
    observed: observed?.shares?.[index] ?? null,
  }))

  return (
    <div>
      <ChartFrame
        height={220}
        series={[
          { label: 'Training baseline', color: 'var(--series-benign)', line: true },
          { label: 'Observed in this window', color: 'var(--series-attack)', line: true },
        ]}
        caption="Both binned on the same edges. Where they separate, the traffic the model is scoring no longer looks like the traffic its threshold was cut from."
      >
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={points} margin={{ top: 4, right: 8, bottom: 16, left: 0 }}>
            <CartesianGrid {...GRID_PROPS} />
            <XAxis
              dataKey="edge"
              scale="log"
              type="number"
              domain={['dataMin', 'dataMax']}
              {...AXIS_PROPS}
              tickFormatter={(value: number) =>
                value >= 0.1 ? decimal(value, 2) : value.toExponential(0)
              }
              label={{
                value: 'Reconstruction error',
                position: 'insideBottom',
                offset: -10,
                fontSize: 11,
              }}
            />
            <YAxis {...AXIS_PROPS} width={42} tickFormatter={(value: number) => percent(value, 0)} />
            <Tooltip
              content={({ active, payload }) => {
                if (!active || !payload?.length) return null
                const point = payload[0].payload as (typeof points)[number]
                return (
                  <TooltipCard title={'error ≈ ' + decimal(point.edge, 5)}>
                    <TooltipRow
                      color="var(--series-benign)"
                      label="Training baseline"
                      value={percent(point.expected, 2)}
                    />
                    {point.observed !== null ? (
                      <TooltipRow
                        color="var(--series-attack)"
                        label="Observed"
                        value={percent(point.observed, 2)}
                      />
                    ) : null}
                  </TooltipCard>
                )
              }}
            />
            <Area
              dataKey="expected"
              type="stepAfter"
              stroke="var(--series-benign)"
              strokeWidth={2}
              fill="var(--series-benign)"
              fillOpacity={0.18}
              isAnimationActive={false}
              dot={false}
            />
            <Area
              dataKey="observed"
              type="stepAfter"
              stroke="var(--series-attack)"
              strokeWidth={2}
              fill="var(--series-attack)"
              fillOpacity={0.14}
              isAnimationActive={false}
              dot={false}
              connectNulls
            />
          </AreaChart>
        </ResponsiveContainer>
      </ChartFrame>

      <p className="text-muted-foreground mt-1 text-xs">
        Baseline: {count(baseline.rows)} benign validation rows.{' '}
        {observed?.rows
          ? `Observed: ${count(observed.rows)} sampled flows that carried a Stage 2 score.`
          : 'No observed scores in this window yet.'}
      </p>
    </div>
  )
}

/** The whole set, as the table view every colour-encoded chart owes its reader. */
export function PsiTable({ snapshot }: { snapshot: DriftSnapshot }) {
  if (snapshot.features.length === 0) return null

  return (
    <div className="scrollbar-thin max-h-96 overflow-y-auto">
      <table className="w-full text-xs">
        <thead className="bg-card sticky top-0">
          <tr className="text-muted-foreground border-border border-b">
            <th className="py-2 pr-3 text-left font-medium">Feature</th>
            <th className="px-2 py-2 text-right font-medium">PSI</th>
            <th className="py-2 pl-2 text-left font-medium">Band</th>
          </tr>
        </thead>
        <tbody className="divide-border divide-y">
          {snapshot.features.map((entry) => (
            <tr key={entry.feature}>
              <td className="py-1.5 pr-3 font-mono">{entry.feature}</td>
              <td
                className={cn(
                  'tabular px-2 py-1.5 text-right font-mono',
                  entry.band === 'stable' && 'text-muted-foreground',
                )}
              >
                {decimal(entry.psi, 4)}
              </td>
              <td className="py-1.5 pl-2">
                <Badge variant={BAND_VARIANT[entry.band]}>{BAND_LABEL[entry.band]}</Badge>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
