/**
 * The shared chart furniture: frame, tooltip, legend, scale key.
 *
 * Built once so every chart in the application reads as one system. The rules
 * encoded here, rather than repeated per chart:
 *
 * - The frame sizes itself to include the axis band. A container whose fixed
 *   height fits only the plot gives the card a tiny nested scrollbar and crops
 *   the tick labels.
 * - Grid and axis are solid hairlines one shade off the surface. Dashing is
 *   reserved: this application draws a real threshold line, and a dashed grid
 *   would compete with it.
 * - Values and labels wear text tokens, never the series colour. A colour chip
 *   beside a label carries identity; the text stays legible ink.
 * - A legend is present whenever there are two or more series, so identity is
 *   never carried by colour alone.
 */
import type { ReactNode } from 'react'

import { cn } from '@/lib/utils'

export interface SeriesKey {
  label: string
  /** A CSS colour, normally one of the `--series-*` tokens. */
  color: string
  /** Rendered as a line swatch rather than a block, for line and area series. */
  line?: boolean
}

/**
 * A titled chart block.
 *
 * `caption` sits under the plot rather than over it: a caption explains what
 * was just seen, and the one on the PR/ROC pair is load-bearing rather than
 * decorative.
 */
export function ChartFrame({
  title,
  description,
  series,
  action,
  caption,
  height = 240,
  children,
  className,
}: {
  title?: ReactNode
  description?: ReactNode
  series?: SeriesKey[]
  action?: ReactNode
  caption?: ReactNode
  height?: number
  children: ReactNode
  className?: string
}) {
  return (
    <figure className={cn('min-w-0', className)}>
      {title || series || action ? (
        <div className="mb-3 flex flex-wrap items-baseline justify-between gap-x-4 gap-y-2">
          <div className="min-w-0">
            {title ? <h3 className="text-sm font-semibold tracking-tight">{title}</h3> : null}
            {description ? (
              <p className="text-muted-foreground mt-1 text-xs leading-relaxed">{description}</p>
            ) : null}
          </div>
          <div className="flex items-center gap-3">
            {series && series.length > 1 ? <ChartLegend series={series} /> : null}
            {action}
          </div>
        </div>
      ) : null}

      <div style={{ height }} className="min-w-0">
        {children}
      </div>

      {caption ? (
        <figcaption className="text-muted-foreground mt-3 text-xs leading-relaxed">
          {caption}
        </figcaption>
      ) : null}
    </figure>
  )
}

export function ChartLegend({ series }: { series: SeriesKey[] }) {
  return (
    <ul className="flex flex-wrap items-center gap-x-4 gap-y-1.5">
      {series.map((entry) => (
        <li key={entry.label} className="text-muted-foreground flex items-center gap-1.5 text-xs">
          <span
            aria-hidden="true"
            className={cn('shrink-0 rounded-full', entry.line ? 'h-0.5 w-3.5' : 'size-2.5')}
            style={{ backgroundColor: entry.color }}
          />
          {entry.label}
        </li>
      ))}
    </ul>
  )
}

/** One row inside a tooltip: a swatch, a name, a value. */
export function TooltipRow({
  color,
  label,
  value,
}: {
  color?: string
  label: ReactNode
  value: ReactNode
}) {
  return (
    <div className="flex items-baseline justify-between gap-6">
      <span className="text-muted-foreground flex items-center gap-1.5">
        {color ? (
          <span
            aria-hidden="true"
            className="size-2 shrink-0 rounded-full"
            style={{ backgroundColor: color }}
          />
        ) : null}
        {label}
      </span>
      <span className="tabular font-mono text-foreground">{value}</span>
    </div>
  )
}

/**
 * The tooltip surface.
 *
 * A tooltip enhances and never gates: every value it shows is also reachable
 * from an axis, a direct label or the table beside the chart, because a value
 * that exists only on hover cannot be read with a keyboard or printed.
 */
export function TooltipCard({ title, children }: { title?: ReactNode; children: ReactNode }) {
  return (
    <div className="bg-card border-border rounded-md border px-3 py-2 text-xs shadow-lg">
      {title ? <p className="text-foreground mb-1.5 font-medium">{title}</p> : null}
      <div className="space-y-1">{children}</div>
    </div>
  )
}

/**
 * The key for a sequential scale.
 *
 * Required beside any continuous colour encoding: a heatmap cell's shade means
 * nothing without the range it sits in. One hue, light to dark -- never a
 * rainbow.
 */
export function ScaleKey({
  max,
  from = '0',
  color = 'var(--series-heat)',
  label,
}: {
  max: ReactNode
  from?: ReactNode
  color?: string
  label?: string
}) {
  return (
    <div className="text-muted-foreground flex items-center gap-2 text-xs">
      {label ? <span>{label}</span> : null}
      <span className="tabular font-mono">{from}</span>
      <span
        aria-hidden="true"
        className="border-border h-2.5 w-20 rounded-full border"
        style={{
          background:
            'linear-gradient(to right, color-mix(in oklab, ' +
            color +
            ' 8%, var(--color-card)), ' +
            color +
            ')',
        }}
      />
      <span className="tabular font-mono">{max}</span>
    </div>
  )
}

/**
 * Mix a series hue toward the card surface by a cell's share of the maximum.
 *
 * Sequential encoding done as one hue fading into the surface, so "near zero"
 * recedes rather than becoming a second colour. The floor keeps a non-zero cell
 * visibly non-zero: a count of one must not be indistinguishable from a count
 * of none, because telling those apart is the whole point of a coverage grid.
 */
export function heatFill(share: number, color = 'var(--series-heat)'): string {
  if (!Number.isFinite(share) || share <= 0) return 'transparent'
  const percent = Math.round(10 + Math.min(1, share) * 90)
  return 'color-mix(in oklab, ' + color + ' ' + percent + '%, var(--color-card))'
}

/** Recharts' shared cartesian defaults, so no chart sets its own grid style. */
export const GRID_PROPS = {
  stroke: 'var(--color-grid)',
  strokeDasharray: undefined,
  vertical: false,
} as const

export const AXIS_PROPS = {
  stroke: 'var(--color-axis)',
  tickLine: false,
  // The fill is set explicitly: Recharts otherwise inherits the tick colour
  // from the axis stroke, which is a hairline one shade off the surface and
  // makes the labels nearly invisible.
  tick: { fontSize: 11, fill: 'var(--color-muted-foreground)' },
} as const
