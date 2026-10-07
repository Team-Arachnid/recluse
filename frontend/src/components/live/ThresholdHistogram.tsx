/**
 * The anomaly-score distribution with a draggable threshold.
 *
 * One interaction that communicates the precision/recall tradeoff better than
 * any table could: drag the line, watch the projected alert volume move. At one
 * setting the SOC gets forty alerts an hour; at another, four hundred. It also
 * makes the point that the threshold is a staffing decision owned by a SOC lead,
 * not a constant baked into a model.
 *
 * Hand-drawn SVG rather than a chart library, for two reasons that are specific
 * to this chart. The bins are log-spaced across four orders of magnitude, so the
 * pointer-to-threshold mapping is logarithmic and has to be exact in both
 * directions -- a reader who drags to a visible point on the curve must get the
 * number belonging to that point. And the line is a real control: it needs
 * pointer capture, keyboard operation and an `aria-valuenow`, none of which a
 * chart library's reference line has.
 *
 * The y axis is each distribution's *share* of its own rows, not raw counts.
 * There are 375,238 benign rows against 220,656 attack rows, so plotting counts
 * would make the benign curve taller for a reason that has nothing to do with
 * the shape being compared.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { useAnomalyHistogram, useThresholdProjection } from '@/api/queries'
import type { AnomalyHistogram } from '@/api/types'
import { ChartLegend, type SeriesKey } from '@/components/charts/Chart'
import { ErrorState, LoadingRows } from '@/components/States'
import { Badge } from '@/components/ui/badge'
import { CardTitle } from '@/components/ui/card'
import { env } from '@/lib/env'
import { count, decimal, percent, smallNumber } from '@/lib/format'
import { useDebounced, useElementWidth } from '@/lib/hooks'
import { cn } from '@/lib/utils'

const HEIGHT = 230
const PADDING = { top: 12, right: 12, bottom: 30, left: 44 }

/** `GET /metrics/threshold` validates `t` as `>= 0.0` and `<= 1.0`, so the
 *  slider cannot express more than that however far the bins run. */
const T_MIN = 0
const T_MAX = 1

const SERIES: SeriesKey[] = [
  { label: 'Benign (held-out day)', color: 'var(--series-benign)' },
  { label: 'Attack (held-out day)', color: 'var(--series-attack)' },
]

interface Scales {
  /** Threshold value to pixel x, on the bins' own log axis. */
  toX: (value: number) => number
  /** Pixel x back to a threshold value. The inverse has to be exact or the
   *  number disagrees with the place the analyst dragged to. */
  toValue: (x: number) => number
  toY: (share: number) => number
  plotWidth: number
  plotHeight: number
}

function buildScales(edges: number[], maxShare: number, width: number): Scales {
  const plotWidth = Math.max(10, width - PADDING.left - PADDING.right)
  const plotHeight = HEIGHT - PADDING.top - PADDING.bottom

  const lo = Math.log(edges[0])
  const hi = Math.log(edges[edges.length - 1])
  const span = hi - lo || 1

  return {
    plotWidth,
    plotHeight,
    toX: (value) => {
      const clamped = Math.max(edges[0], Math.min(edges[edges.length - 1], value))
      return PADDING.left + ((Math.log(clamped) - lo) / span) * plotWidth
    },
    toValue: (x) => {
      const ratio = Math.max(0, Math.min(1, (x - PADDING.left) / plotWidth))
      return Math.exp(lo + ratio * span)
    },
    toY: (share) => PADDING.top + plotHeight - (share / (maxShare || 1)) * plotHeight,
  }
}

/**
 * One distribution as a stepped outline over a translucent fill.
 *
 * A step rather than a smooth line, because the data is binned: a curve through
 * bin midpoints would claim a continuity the histogram does not have. Two
 * translucent fills with solid outlines is what keeps both readable where they
 * overlap, which is the part of the chart that matters -- the attack tail
 * reaching past the benign bulk is the whole claim.
 */
function Distribution({
  edges,
  shares,
  color,
  scales,
}: {
  edges: number[]
  shares: number[]
  color: string
  scales: Scales
}) {
  const steps: string[] = []
  shares.forEach((share, index) => {
    const x0 = scales.toX(edges[index])
    const x1 = scales.toX(edges[index + 1])
    const y = scales.toY(share)
    steps.push((index === 0 ? 'M' : 'L') + x0 + ' ' + y, 'L' + x1 + ' ' + y)
  })

  const outline = steps.join(' ')
  const baseline = scales.toY(0)
  const area =
    outline +
    ' L' +
    scales.toX(edges[edges.length - 1]) +
    ' ' +
    baseline +
    ' L' +
    scales.toX(edges[0]) +
    ' ' +
    baseline +
    ' Z'

  return (
    <g>
      <path d={area} fill={color} opacity={0.22} />
      <path d={outline} fill="none" stroke={color} strokeWidth={2} strokeLinejoin="round" />
    </g>
  )
}

function distributionShares(
  histogram: AnomalyHistogram,
  name: string,
): { shares: number[]; rows: number } | null {
  const found = histogram.distributions.find((entry) => entry.name === name)
  if (!found || found.rows === 0) return null
  return { shares: found.counts.map((value) => value / found.rows), rows: found.rows }
}

/** The figures the drag moves. Held at reduced opacity while a request is in
 *  flight rather than replaced by a skeleton, so nothing jumps mid-drag. */
function Projection({ t, stale }: { t: number; stale: boolean }) {
  const { data, error } = useThresholdProjection(t)

  if (error) return <ErrorState error={error} label="Could not project this threshold" />
  if (!data) return <LoadingRows rows={2} className="max-w-xs" />

  return (
    <div className={cn('transition-opacity', stale && 'opacity-60')}>
      <div className="flex flex-wrap items-baseline gap-x-8 gap-y-3">
        <div>
          <p className="text-foreground-strong font-mono text-3xl leading-tight font-bold">
            {count(Math.round(data.alerts_per_analyst_hour))}
          </p>
          <p className="text-muted-foreground text-xs">Alerts per analyst hour</p>
        </div>
        <div>
          <p className="text-foreground-strong font-mono text-lg leading-tight font-semibold">
            {count(Math.round(data.false_alerts_per_day))}
          </p>
          <p className="text-muted-foreground text-xs">
            False alerts per day, against a budget of {count(data.budget_per_day)}
          </p>
        </div>
        <div>
          <p className="text-foreground-strong font-mono text-lg leading-tight font-semibold">{smallNumber(data.fpr)}</p>
          <p className="text-muted-foreground text-xs">
            False-positive rate, target {smallNumber(data.target_fpr)}
          </p>
        </div>
        <div>
          <p className="text-foreground-strong font-mono text-lg leading-tight font-semibold">
            {data.recall === null ? '—' : percent(data.recall)}
          </p>
          <p className="text-muted-foreground text-xs">
            Attack rows caught {data.attack_rows ? 'of ' + count(data.attack_rows) : ''}
          </p>
        </div>
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <Badge variant={data.within_budget ? 'ok' : 'high'}>
          {data.within_budget ? 'Inside the budget' : 'Over the budget'}
        </Badge>
        {!data.covers_distribution ? (
          // The declared range is [0, 1] but Stage 2's errors reach about 1.83,
          // so the top of the attack distribution is outside what this control
          // can express. Saying so beats quietly reporting a recall for a
          // threshold the axis cannot reach.
          <span className="text-muted-foreground text-xs">
            Past the top of the measured range — recall above here is not meaningful.
          </span>
        ) : null}
      </div>
    </div>
  )
}

export function ThresholdHistogram() {
  const { data: histogram, error, isPending } = useAnomalyHistogram()
  const [container, width] = useElementWidth<HTMLDivElement>()
  const svg = useRef<SVGSVGElement>(null)

  const [threshold, setThreshold] = useState<number | null>(null)
  const [dragging, setDragging] = useState(false)

  // The operating threshold is where the line starts: the control opens at what
  // the model is actually doing, so the first thing it shows is the truth rather
  // than an arbitrary midpoint.
  useEffect(() => {
    if (threshold === null && histogram?.tau_anom != null) setThreshold(histogram.tau_anom)
  }, [histogram, threshold])

  const settled = useDebounced(threshold, env.thresholdDebounceMs)

  const benign = useMemo(
    () => (histogram ? distributionShares(histogram, 'test_benign') : null),
    [histogram],
  )
  const attack = useMemo(
    () => (histogram ? distributionShares(histogram, 'test_attack') : null),
    [histogram],
  )

  const maxShare = useMemo(
    () => Math.max(...(benign?.shares ?? [0]), ...(attack?.shares ?? [0])),
    [benign, attack],
  )

  const scales = useMemo(
    () => (histogram ? buildScales(histogram.edges, maxShare, width) : null),
    [histogram, maxShare, width],
  )

  const moveTo = useCallback(
    (clientX: number) => {
      if (!scales || !svg.current) return
      const box = svg.current.getBoundingClientRect()
      const value = scales.toValue(clientX - box.left)
      setThreshold(Math.max(T_MIN, Math.min(T_MAX, value)))
    },
    [scales],
  )

  useEffect(() => {
    if (!dragging) return
    const onMove = (event: PointerEvent) => moveTo(event.clientX)
    const onUp = () => setDragging(false)
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
    return () => {
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
    }
  }, [dragging, moveTo])

  if (isPending) return <LoadingRows rows={6} />
  if (error) return <ErrorState error={error} label="Could not load the score distribution" />
  if (!histogram || !scales || threshold === null) return null

  const lineX = scales.toX(threshold)
  const budgetX = histogram.budget_tau != null ? scales.toX(histogram.budget_tau) : null
  const operatingX = histogram.tau_anom != null ? scales.toX(histogram.tau_anom) : null

  // Decade ticks, which is the only honest labelling for a log axis: evenly
  // spaced labels would imply an even scale.
  const ticks: number[] = []
  for (let exponent = -4; exponent <= 1; exponent += 1) {
    const value = Math.pow(10, exponent)
    if (value >= histogram.edges[0] && value <= histogram.edges[histogram.edges.length - 1]) {
      ticks.push(value)
    }
  }

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-start justify-between gap-4">
        <div>
          <CardTitle>Where the threshold sits, and what it costs</CardTitle>
          <p className="text-muted-foreground mt-1 max-w-xl text-xs leading-relaxed">
            Drag the line. Everything to its right becomes an alert. This is the precision/recall
            tradeoff the SOC lead actually controls — and the budget behind it is configuration,
            not a constant in the model.
          </p>
        </div>
        <div className="text-right">
          <p className="tabular font-mono text-2xl font-bold text-[var(--novel)]">
            {decimal(threshold, 4)}
          </p>
          <p className="text-muted-foreground text-xs">
            Threshold{' '}
            {histogram.tau_anom != null && Math.abs(threshold - histogram.tau_anom) < 1e-9
              ? '(shipped)'
              : ''}
          </p>
        </div>
      </div>

      <ChartLegend series={SERIES} />

      <div ref={container} className="mt-2 w-full">
        <svg
          ref={svg}
          width={Math.max(width, 10)}
          height={HEIGHT}
          className="touch-none select-none"
          onPointerDown={(event) => {
            setDragging(true)
            moveTo(event.clientX)
          }}
        >
          {/* Horizontal gridlines only, solid hairlines one shade off the
              surface. Vertical ones would compete with the threshold line. */}
          {[0.25, 0.5, 0.75, 1].map((fraction) => (
            <line
              key={fraction}
              x1={PADDING.left}
              x2={PADDING.left + scales.plotWidth}
              y1={scales.toY(maxShare * fraction)}
              y2={scales.toY(maxShare * fraction)}
              stroke="var(--color-grid)"
            />
          ))}

          {[0, 0.5, 1].map((fraction) => (
            <text
              key={fraction}
              x={PADDING.left - 6}
              y={scales.toY(maxShare * fraction) + 3}
              textAnchor="end"
              className="fill-[var(--color-muted-foreground)] text-[10px]"
            >
              {percent(maxShare * fraction, 0)}
            </text>
          ))}

          {benign ? (
            <Distribution
              edges={histogram.edges}
              shares={benign.shares}
              color="var(--series-benign)"
              scales={scales}
            />
          ) : null}
          {attack ? (
            <Distribution
              edges={histogram.edges}
              shares={attack.shares}
              color="var(--series-attack)"
              scales={scales}
            />
          ) : null}

          {/* Everything above the threshold becomes an alert, so the alerting
              region is shaded rather than left to inference. */}
          <rect
            x={lineX}
            y={PADDING.top}
            width={Math.max(0, PADDING.left + scales.plotWidth - lineX)}
            height={scales.plotHeight}
            fill="var(--color-foreground)"
            opacity={0.05}
          />

          <line
            x1={PADDING.left}
            x2={PADDING.left + scales.plotWidth}
            y1={PADDING.top + scales.plotHeight}
            y2={PADDING.top + scales.plotHeight}
            stroke="var(--color-axis)"
          />

          {ticks.map((tick) => (
            <text
              key={tick}
              x={scales.toX(tick)}
              y={HEIGHT - 14}
              textAnchor="middle"
              className="fill-[var(--color-muted-foreground)] text-[10px]"
            >
              {tick >= 1 ? tick : tick.toExponential(0)}
            </text>
          ))}

          {/* The two reference thresholds, as short ticks under the axis rather
              than as full lines: a second full-height line would read as another
              control, and only one of these is draggable. */}
          {operatingX !== null ? (
            <g>
              <line
                x1={operatingX}
                x2={operatingX}
                y1={PADDING.top + scales.plotHeight}
                y2={PADDING.top + scales.plotHeight + 6}
                stroke="var(--color-series-neutral)"
                strokeWidth={2}
              />
            </g>
          ) : null}
          {budgetX !== null ? (
            <g>
              <line
                x1={budgetX}
                x2={budgetX}
                y1={PADDING.top + scales.plotHeight}
                y2={PADDING.top + scales.plotHeight + 6}
                stroke="var(--color-medium)"
                strokeWidth={2}
              />
            </g>
          ) : null}

          <g
            role="slider"
            tabIndex={0}
            aria-label="Anomaly score threshold"
            aria-valuemin={T_MIN}
            aria-valuemax={T_MAX}
            aria-valuenow={threshold}
            aria-valuetext={'threshold ' + decimal(threshold, 4)}
            onKeyDown={(event) => {
              // A log axis needs multiplicative steps: adding a constant moves
              // the line a hair at the top of the range and across a decade at
              // the bottom.
              const coarse = event.shiftKey ? 1.5 : 1.08
              if (event.key === 'ArrowRight' || event.key === 'ArrowUp') {
                event.preventDefault()
                setThreshold((current) => Math.min(T_MAX, (current ?? 0) * coarse))
              }
              if (event.key === 'ArrowLeft' || event.key === 'ArrowDown') {
                event.preventDefault()
                setThreshold((current) => Math.max(histogram.edges[0], (current ?? 0) / coarse))
              }
              if (event.key === 'Home' && histogram.tau_anom != null) {
                event.preventDefault()
                setThreshold(histogram.tau_anom)
              }
            }}
            className="cursor-ew-resize outline-none focus-visible:[&>line]:stroke-[var(--color-ring)]"
          >
            <line
              x1={lineX}
              x2={lineX}
              y1={PADDING.top - 4}
              y2={PADDING.top + scales.plotHeight + 4}
              stroke="var(--color-foreground)"
              strokeWidth={2}
            />
            {/* A wide invisible band so the line is grabbable without landing on
                two pixels dead centre. */}
            <rect
              x={lineX - 12}
              y={PADDING.top - 4}
              width={24}
              height={scales.plotHeight + 8}
              fill="transparent"
            />
            <circle cx={lineX} cy={PADDING.top - 4} r={5} fill="var(--color-foreground)" />
          </g>
        </svg>
      </div>

      <div className="text-muted-foreground mt-1 flex flex-wrap items-center gap-x-5 gap-y-1 text-[11px]">
        <span className="flex items-center gap-1.5">
          <span
            aria-hidden="true"
            className="h-0.5 w-3 bg-[var(--series-neutral)]"
          />
          shipped threshold {histogram.tau_anom != null ? decimal(histogram.tau_anom, 4) : '—'}
        </span>
        <span className="flex items-center gap-1.5">
          <span aria-hidden="true" className="h-0.5 w-3 bg-[var(--medium)]" />
          cut to the false-positive budget instead{' '}
          {histogram.budget_tau != null ? decimal(histogram.budget_tau, 4) : '—'}
        </span>
        <span>log axis, {histogram.edges.length - 1} bins</span>
      </div>

      <div className="border-border mt-5 border-t pt-5">
        <Projection t={settled ?? threshold} stale={settled !== threshold} />
      </div>

      <p className="text-muted-foreground mt-4 text-xs leading-relaxed">
        The two marks under the axis are the same model measured against two different questions.
        The shipped threshold is the 99.5th percentile of benign validation error — a statistical
        choice. The budget threshold is where the false-positive rate would have to sit to keep the
        queue inside what the configured analyst capacity can read. The gap between them is the
        cost of that statistical choice, in alerts somebody has to work.
      </p>
    </div>
  )
}
