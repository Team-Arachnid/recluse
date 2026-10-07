/**
 * A metric tile: an icon well, a label in small caps, one figure, one caption,
 * and -- where there is a real series behind the figure -- a sparkline.
 *
 * The sparkline is drawn only from data the API returned. A decorative squiggle
 * beside a number reads as a trend, and a trend nobody measured is the kind of
 * number this project exists not to show.
 */
import type { LucideIcon } from 'lucide-react'
import type { ReactNode } from 'react'

import { Skeleton } from '@/components/ui/skeleton'
import { cn } from '@/lib/utils'

export type Tone = 'brand' | 'novel' | 'ok' | 'high' | 'medium' | 'info' | 'muted'

const TONE: Record<Tone, string> = {
  brand: 'var(--brand-bright)',
  novel: 'var(--novel)',
  ok: 'var(--ok)',
  high: 'var(--high)',
  medium: 'var(--medium)',
  info: 'var(--info)',
  muted: 'var(--muted-foreground)',
}

/** The stroke for a tone's sparkline: the deeper channel colour, not the text one. */
const STROKE: Record<Tone, string> = {
  brand: 'var(--brand)',
  novel: 'var(--series-novel)',
  ok: 'var(--ok)',
  high: 'var(--high)',
  medium: 'var(--medium)',
  info: 'var(--series-benign)',
  muted: 'var(--series-known)',
}

/** The left edge of the tile: the channel at full strength, except for the
 *  neutral tile, whose edge is only a stronger hairline. */
const EDGE: Record<Tone, string> = {
  brand: 'var(--brand-bright)',
  novel: 'var(--novel)',
  ok: 'var(--ok)',
  high: 'var(--high)',
  medium: 'var(--medium)',
  info: 'var(--info)',
  muted: 'var(--border-strong)',
}

export const toneColor = (tone: Tone): string => TONE[tone]

/**
 * A sparkline as one smoothed path over the series' own range.
 *
 * No axes, because it is read for shape. At least two points, or nothing: a
 * line through one point is a dot pretending to be a trend.
 */
export function MiniSparkline({
  values,
  tone = 'brand',
  width = 64,
  height = 26,
  label,
}: {
  values: number[]
  tone?: Tone
  width?: number
  height?: number
  label?: string
}) {
  if (values.length < 2) return null
  const max = Math.max(...values)
  const min = Math.min(...values)
  const span = max - min || 1
  const pad = 2

  const points = values.map((value, index) => ({
    x: pad + (index / (values.length - 1)) * (width - pad * 2),
    y: pad + (1 - (value - min) / span) * (height - pad * 2),
  }))

  let path = `M ${points[0].x} ${points[0].y}`
  for (let index = 1; index < points.length; index += 1) {
    const previous = points[index - 1]
    const current = points[index]
    const mid = (previous.x + current.x) / 2
    path += ` C ${mid} ${previous.y}, ${mid} ${current.y}, ${current.x} ${current.y}`
  }
  const area = `${path} L ${points.at(-1)!.x} ${height} L ${points[0].x} ${height} Z`
  const stroke = STROKE[tone]

  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      className="shrink-0 opacity-90"
      role={label ? 'img' : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
    >
      <path d={area} fill={stroke} opacity={0.1} />
      <path d={path} fill="none" stroke={stroke} strokeWidth={1.6} strokeLinecap="round" />
    </svg>
  )
}

export function StatTile({
  icon: Icon,
  label,
  value,
  caption,
  tone = 'brand',
  captionTone,
  spark,
  sparkLabel,
  pending = false,
  onClick,
  className,
  children,
}: {
  icon: LucideIcon
  label: string
  value: ReactNode
  caption?: ReactNode
  tone?: Tone
  /** Defaults to `tone`; set it when the caption says something the icon does
   *  not -- "over the budget" in a neutral tile, say. */
  captionTone?: Tone
  spark?: number[]
  sparkLabel?: string
  pending?: boolean
  onClick?: () => void
  className?: string
  children?: ReactNode
}) {
  const Wrapper = onClick ? 'button' : 'div'
  return (
    <Wrapper
      type={onClick ? 'button' : undefined}
      onClick={onClick}
      className={cn(
        'bg-card border-border flex min-w-0 items-center justify-between gap-3 rounded-[var(--radius-card)] border border-l-[3px] p-3.5 text-left sm:p-4',
        onClick && 'hover:border-border-strong cursor-pointer transition-colors',
        className,
      )}
      style={{ borderLeftColor: EDGE[tone] }}
    >
      <div className="flex min-w-0 items-center gap-3.5">
        <div
          className="flex size-11 shrink-0 items-center justify-center rounded-full sm:size-12"
          style={{ backgroundColor: `color-mix(in oklab, ${EDGE[tone]} 14%, transparent)` }}
        >
          <Icon className="size-5" style={{ color: TONE[tone] }} aria-hidden="true" />
        </div>
        <div className="min-w-0">
          <p className="text-muted-foreground text-[13px] leading-tight font-medium">{label}</p>
          {pending ? (
            <Skeleton className="mt-1 h-7 w-20" />
          ) : (
            <div className="mt-0.5 flex min-w-0 flex-wrap items-baseline gap-x-2">
              <span className="text-foreground-strong font-mono text-xl font-bold tracking-tight sm:text-2xl">
                {value}
              </span>
              {caption ? (
                <span
                  className="truncate font-mono text-[10.5px] font-medium sm:text-[11px]"
                  style={{ color: TONE[captionTone ?? tone] }}
                >
                  {caption}
                </span>
              ) : null}
            </div>
          )}
          {children}
        </div>
      </div>
      {spark && !pending ? (
        <MiniSparkline values={spark} tone={tone} label={sparkLabel} />
      ) : null}
    </Wrapper>
  )
}
