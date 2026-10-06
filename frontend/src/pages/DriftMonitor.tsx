/**
 * Screen 5 — the drift monitor.
 *
 * Two of this screen's three panels are served by endpoints that answer 501
 * today: `GET /metrics/drift` and `GET /models` both arrive in Phase 7. This
 * screen reports that from the response body rather than rendering a plausible
 * PSI chart, which would be the single most dangerous piece of mock data in the
 * application — a drift monitor that invents a flat line tells its reader
 * everything is fine.
 *
 * What *is* real is the reference distribution: the benign reconstruction-error
 * baseline the model was calibrated against, which is the "expected" half of
 * every PSI the nightly job will compute. Showing it now makes the empty half
 * legible: the chart is a baseline waiting for an observation, and the shape of
 * the thing being compared is already on screen.
 */
import { Activity, Database, Layers } from 'lucide-react'

import { useAnomalyHistogram, useDrift, useHealth, useModelRegistry } from '@/api/queries'
import { ScreenBody } from '@/components/AppShell'
import {
  AXIS_PROPS,
  ChartFrame,
  GRID_PROPS,
  TooltipCard,
  TooltipRow,
} from '@/components/charts/Chart'
import { ErrorBoundary } from '@/components/ErrorBoundary'
import { ErrorState, LoadingRows, NotBuiltYet } from '@/components/States'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { count, decimal, percent } from '@/lib/format'
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

/** The two bands the brief fixes, and what each one means for a decision. */
const PSI_BANDS = [
  { ceiling: 0.1, label: 'Stable', meaning: 'No action. Normal variation between days.' },
  {
    ceiling: 0.25,
    label: 'Moderate shift',
    meaning: 'Worth investigating. Something about the traffic mix has changed.',
  },
  {
    ceiling: Infinity,
    label: 'Significant shift',
    meaning: 'Retrain recommended. The baseline the model learned no longer describes the traffic.',
  },
] as const

function ReferenceBaseline() {
  const { data, error, isPending } = useAnomalyHistogram()

  if (isPending) return <LoadingRows rows={5} />
  if (error) return <ErrorState error={error} label="Could not load the reference baseline" />
  if (!data) return null

  const validation = data.distributions.find((entry) => entry.name === 'validation_benign')
  const test = data.distributions.find((entry) => entry.name === 'test_benign')
  if (!validation) return null

  const points = validation.counts.map((value, index) => ({
    edge: data.edges[index],
    expected: validation.rows ? value / validation.rows : 0,
    observed: test && test.rows ? (test.counts[index] ?? 0) / test.rows : null,
  }))

  return (
    <ChartFrame
      height={200}
      series={[
        { label: 'Calibration day (expected)', color: 'var(--series-benign)', line: true },
        { label: 'Held-out day (observed)', color: 'var(--series-attack)', line: true },
      ]}
      caption="Two benign days from the same dataset, one week apart, binned on the same axis. Where the curves separate, the baseline has already moved — and this is traffic from the same lab capture. A real network drifts faster."
    >
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={points} margin={{ top: 4, right: 8, bottom: 16, left: 0 }}>
          <defs>
            <linearGradient id="drift-expected" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="var(--series-benign)" stopOpacity={0.3} />
              <stop offset="100%" stopColor="var(--series-benign)" stopOpacity={0.02} />
            </linearGradient>
          </defs>
          <CartesianGrid {...GRID_PROPS} />
          <XAxis
            dataKey="edge"
            scale="log"
            type="number"
            domain={['dataMin', 'dataMax']}
            {...AXIS_PROPS}
            tickFormatter={(value: number) => (value >= 0.1 ? decimal(value, 2) : value.toExponential(0))}
            label={{
              value: 'Reconstruction error',
              position: 'insideBottom',
              offset: -10,
              fontSize: 11,
            }}
          />
          <YAxis
            {...AXIS_PROPS}
            width={40}
            tickFormatter={(value: number) => percent(value, 0)}
          />
          <Tooltip
            content={({ active, payload }) => {
              if (!active || !payload?.length) return null
              const point = payload[0].payload as {
                edge: number
                expected: number
                observed: number | null
              }
              return (
                <TooltipCard title={'error ≈ ' + decimal(point.edge, 4)}>
                  <TooltipRow
                    color="var(--series-benign)"
                    label="Calibration day"
                    value={percent(point.expected, 2)}
                  />
                  {point.observed !== null ? (
                    <TooltipRow
                      color="var(--series-attack)"
                      label="Held-out day"
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
            fill="url(#drift-expected)"
            isAnimationActive={false}
            dot={false}
          />
          <Area
            dataKey="observed"
            type="stepAfter"
            stroke="var(--series-attack)"
            strokeWidth={2}
            fill="var(--series-attack)"
            fillOpacity={0.1}
            isAnimationActive={false}
            dot={false}
            connectNulls
          />
        </AreaChart>
      </ResponsiveContainer>
    </ChartFrame>
  )
}

export function DriftMonitor() {
  const drift = useDrift()
  const registry = useModelRegistry()
  const { data: health } = useHealth()

  return (
    <ScreenBody
      title="Drift"
      lede="A model is calibrated against a baseline, and baselines move. This screen is where that gets caught before it shows up as a false-positive rate nobody can explain."
    >
      <div className="space-y-6">
        <ErrorBoundary label="Reference baseline">
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Activity className="size-4 text-[var(--info)]" aria-hidden="true" />
                The baseline PSI is measured against
              </CardTitle>
              <CardDescription>
                The benign error distribution the autoencoder&rsquo;s threshold was cut from. This
                is the &ldquo;expected&rdquo; half of every PSI the nightly job will compute.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <ReferenceBaseline />
            </CardContent>
          </Card>
        </ErrorBoundary>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Layers className="size-4 text-[var(--medium)]" aria-hidden="true" />
              Population stability index, per feature
            </CardTitle>
            <CardDescription>
              PSI = Σ (actual% − expected%) × ln(actual% / expected%), over the bins of one feature.
            </CardDescription>
          </CardHeader>
          <CardContent className="px-0 pb-0">
            {drift.isPending ? (
              <div className="px-5 pb-5">
                <LoadingRows rows={4} />
              </div>
            ) : (
              <NotBuiltYet error={drift.error} what="Per-feature drift">
                <div className="space-y-2">
                  {PSI_BANDS.map((band) => (
                    <div key={band.label} className="flex items-baseline gap-3 text-xs">
                      <span
                        aria-hidden="true"
                        className="mt-1 size-2 shrink-0 rounded-full"
                        style={{
                          backgroundColor:
                            band.ceiling === 0.1
                              ? 'var(--ok)'
                              : band.ceiling === 0.25
                                ? 'var(--medium)'
                                : 'var(--critical)',
                        }}
                      />
                      <span className="tabular w-16 shrink-0 font-mono">
                        {band.ceiling === Infinity ? '> 0.25' : '≤ ' + band.ceiling}
                      </span>
                      <span className="text-foreground w-32 shrink-0 font-medium">
                        {band.label}
                      </span>
                      <span className="text-muted-foreground">{band.meaning}</span>
                    </div>
                  ))}
                </div>
                <p className="text-muted-foreground mt-3 text-xs leading-relaxed">
                  There is no drift-snapshot table in the database yet, so there is nothing to plot
                  — not a flat line at zero, which is what a drift monitor must never show when it
                  has no measurements. The nightly job that fills it lands with the retraining
                  pipeline.
                </p>
              </NotBuiltYet>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Database className="size-4 text-[var(--info)]" aria-hidden="true" />
              Model registry
            </CardTitle>
            <CardDescription>
              Versions, what each was trained on, and which is champion.
            </CardDescription>
          </CardHeader>
          <CardContent className="px-0 pb-0">
            {registry.isPending ? (
              <div className="px-5 pb-5">
                <LoadingRows rows={3} />
              </div>
            ) : (
              <NotBuiltYet error={registry.error} what="The model registry">
                {/* One row of it is knowable now, from the loaded bundle, and
                    saying so is better than an empty table: the audit question
                    "what is scoring right now" has an answer today. */}
                {health ? (
                  <div className="border-border rounded-md border px-3 py-2">
                    <div className="flex flex-wrap items-center gap-2 text-xs">
                      <Badge variant="ok">serving</Badge>
                      <span className="font-mono">{health.model_version}</span>
                      <span className="text-muted-foreground">
                        loaded at startup, {count(Math.round(health.uptime_s))}s ago
                      </span>
                    </div>
                  </div>
                ) : null}
              </NotBuiltYet>
            )}
          </CardContent>
        </Card>
      </div>
    </ScreenBody>
  )
}
