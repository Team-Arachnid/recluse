/**
 * The live feed: alerts scrolling past as the replay scores them, and the two
 * rate series beside it.
 *
 * Flows per second and alerts per second are two charts, not one with two y
 * axes. At 10x the replay scores hundreds of flows a second and raises a
 * handful of alerts; putting both on one scale would flatten the alert series
 * into the axis, and putting them on two scales would invent a correlation by
 * choosing how to align them.
 *
 * The flow rate comes from `rows_scored` on the status poll, because the
 * stream carries alerts and only alerts -- every flow is scored, but only an
 * alert is published. Saying where each number comes from matters here: one is
 * measured off a two-second poll and the other is counted frame by frame.
 */
import { Activity, Bell } from 'lucide-react'
import { Area, AreaChart, ResponsiveContainer, Tooltip, YAxis } from 'recharts'
import { useEffect, useRef, useState } from 'react'

import { useReplayStatus } from '@/api/queries'
import { useAlertStream } from '@/api/stream'
import { ClassBadge, SeverityMark } from '@/components/alert/AlertGlyphs'
import { TooltipCard, TooltipRow } from '@/components/charts/Chart'
import { EmptyState } from '@/components/States'
import { clockTime, count, decimal } from '@/lib/format'
import type { LucideIcon } from 'lucide-react'

/** How many points of flow-rate history the sparkline keeps. One per poll. */
const FLOW_HISTORY = 45

interface Point {
  /** Seconds since the screen started watching. */
  second: number
  value: number
}

/** A sparkline: one series, no axes, the current value read out beside it. */
function RateSparkline({
  icon: Icon,
  label,
  points,
  color,
  current,
  note,
}: {
  icon: LucideIcon
  label: string
  points: Point[]
  color: string
  current: string
  note: string
}) {
  const peak = Math.max(1, ...points.map((point) => point.value))

  return (
    <div className="bg-card border-border rounded-[var(--radius-card)] border p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-3">
          <div className="bg-raised border-raised-border flex size-10 items-center justify-center rounded-lg border">
            <Icon className="size-[18px]" style={{ color }} aria-hidden="true" />
          </div>
          <div>
            <p className="text-muted-foreground text-[11px] font-medium tracking-wider uppercase">
              {label}
            </p>
            <p className="text-foreground-strong font-mono text-2xl leading-tight font-bold">
              {current}
            </p>
          </div>
        </div>
        <p className="text-subtle-foreground text-right font-mono text-[10.5px] leading-snug">
          peak {count(Math.round(peak))}
          <br />
          {note}
        </p>
      </div>

      <div className="mt-3 h-16">
        {points.length < 2 ? (
          <p className="text-subtle-foreground pt-6 text-[11px]">Waiting for a second sample…</p>
        ) : (
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={points} margin={{ top: 2, right: 2, bottom: 0, left: 0 }}>
              <defs>
                <linearGradient id={'fill-' + label.replace(/\W/g, '')} x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor={color} stopOpacity={0.28} />
                  <stop offset="100%" stopColor={color} stopOpacity={0} />
                </linearGradient>
              </defs>
              <YAxis hide domain={[0, peak * 1.15]} />
              <Tooltip
                content={({ active, payload }) => {
                  if (!active || !payload?.length) return null
                  const point = payload[0].payload as Point
                  return (
                    <TooltipCard title={point.second + 's into the feed'}>
                      <TooltipRow color={color} label={label} value={decimal(point.value, 0)} />
                    </TooltipCard>
                  )
                }}
              />
              <Area
                type="monotone"
                dataKey="value"
                stroke={color}
                strokeWidth={1.8}
                fill={'url(#fill-' + label.replace(/\W/g, '') + ')'}
                isAnimationActive={false}
                dot={false}
              />
            </AreaChart>
          </ResponsiveContainer>
        )}
      </div>
    </div>
  )
}

export function RatePanels() {
  const { rates } = useAlertStream()
  const { data: status } = useReplayStatus()

  // `rows_scored` is cumulative, so the rate is the delta between two polls --
  // and the series is the history of those deltas. An earlier version stamped
  // the latest rate onto every point, which drew a flat line whatever the
  // traffic did.
  const started = useRef(Date.now())
  const previous = useRef<{ rows: number; at: number } | null>(null)
  const [flows, setFlows] = useState<Point[]>([])

  useEffect(() => {
    if (!status) return
    const now = Date.now()
    const last = previous.current
    previous.current = { rows: status.rows_scored, at: now }
    if (!last || now === last.at || status.rows_scored < last.rows) return
    const perSecond = ((status.rows_scored - last.rows) / (now - last.at)) * 1000
    setFlows((current) =>
      [
        ...current,
        { second: Math.round((now - started.current) / 1000), value: Math.max(0, perSecond) },
      ].slice(-FLOW_HISTORY),
    )
  }, [status])

  const alertPoints = rates.map((sample) => ({ second: sample.second, value: sample.alerts }))
  const latestFlows = flows.at(-1)?.value ?? 0
  const latestAlerts = rates.at(-1)?.alerts ?? 0

  return (
    <div className="grid gap-4 sm:grid-cols-2">
      <RateSparkline
        icon={Activity}
        label="Flows scored / second"
        points={flows}
        color="var(--series-benign)"
        current={count(Math.round(latestFlows))}
        note="from rows_scored"
      />
      <RateSparkline
        icon={Bell}
        label="Alerts / second"
        points={alertPoints}
        color="var(--series-attack)"
        current={count(latestAlerts)}
        note="counted on the stream"
      />
    </div>
  )
}

export function FlowTicker({ onOpen }: { onOpen: (id: number) => void }) {
  const { events, connected } = useAlertStream()

  if (events.length === 0) {
    return (
      <EmptyState
        title={connected ? 'Connected, nothing yet' : 'No traffic source running'}
        hint={
          connected
            ? 'The stream is open and sending heartbeats. Alerts appear here as the source raises them.'
            : 'Start a replay above. The feed connects itself once a source is live.'
        }
      />
    )
  }

  return (
    <ul
      className="divide-divider scrollbar-thin divide-y overflow-y-auto"
      style={{ maxHeight: 440 }}
    >
      {events.map((event, index) => (
        <li key={String(event.id) + '-' + index}>
          <button
            type="button"
            onClick={() => onOpen(event.id)}
            className={
              'hover:bg-hover flex w-full cursor-pointer items-center gap-3 px-4 py-2 text-left text-xs motion-safe:animate-[row-in_1.2s_ease-out] ' +
              (event.kind === 'UNCLASSIFIED_ANOMALY' ? 'border-l-2 border-l-[var(--novel)]' : '')
            }
          >
            <span className="tabular text-subtle-foreground w-16 shrink-0 font-mono text-[11px]">
              {clockTime(event.detected_at)}
            </span>
            <ClassBadge alert={event} className="w-36 shrink-0" />
            <SeverityMark severity={event.severity} className="hidden w-16 shrink-0 sm:inline-flex" />
            <span className="tabular min-w-0 flex-1 truncate font-mono text-[11px]">
              <span className="text-foreground">{event.src_ip}</span>{' '}
              <span className="text-subtle-foreground">→</span>{' '}
              <span className="text-muted-foreground">{event.dst_ip}</span>
            </span>
            <span className="tabular text-muted-foreground shrink-0 font-mono text-[11px]">
              risk <span className="text-foreground">{decimal(event.risk_score)}</span>
            </span>
            {event.occurrence_count > 1 ? (
              <span
                className="tabular text-foreground-strong w-12 shrink-0 text-right font-mono text-[11px] font-semibold"
                title={event.occurrence_count + ' flows collapsed by dedupe'}
              >
                ×{count(event.occurrence_count)}
              </span>
            ) : (
              <span className="w-12 shrink-0" />
            )}
          </button>
        </li>
      ))}
    </ul>
  )
}
