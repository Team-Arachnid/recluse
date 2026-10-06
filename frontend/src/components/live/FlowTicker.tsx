/**
 * The live feed: alerts scrolling past as the replay scores them, and the two
 * rate series beside it.
 *
 * Flows per second and alerts per second are two charts, not one with two y
 * axes. At 10x the replay scores roughly five hundred flows a second and raises
 * a handful of alerts; putting both on one scale would flatten the alert series
 * into the axis, and putting them on two scales would invent a correlation by
 * choosing how to align them.
 *
 * The flow rate comes from `rows_scored` on the replay status poll, because the
 * stream carries alerts and only alerts -- every flow is scored, but only an
 * alert is published. Saying where each number comes from matters here: one is
 * measured off a two-second poll and the other is counted frame by frame.
 */
import { Area, AreaChart, ResponsiveContainer, Tooltip, YAxis } from 'recharts'
import { useEffect, useRef, useState } from 'react'

import { useReplayStatus } from '@/api/queries'
import { useAlertStream, type RateSample } from '@/api/stream'
import { TooltipCard, TooltipRow } from '@/components/charts/Chart'
import { EmptyState } from '@/components/States'
import { Badge } from '@/components/ui/badge'
import { classLabel, classVariant, isNovel, SEVERITY_VARIANT } from '@/lib/alerts'
import { clockTime, count, decimal, endpoint } from '@/lib/format'

/** A sparkline: one series, no axes, the current value read out beside it. */
function RateSparkline({
  label,
  samples,
  pick,
  color,
  current,
  note,
}: {
  label: string
  samples: RateSample[]
  pick: (sample: RateSample) => number
  color: string
  current: string
  note: string
}) {
  const data = samples.map((sample) => ({ second: sample.second, value: pick(sample) }))
  const peak = Math.max(1, ...data.map((point) => point.value))

  return (
    <div className="border-border rounded-[var(--radius-card)] border px-4 py-3">
      <div className="flex items-baseline justify-between gap-3">
        <div>
          <p className="text-lg leading-tight font-semibold">{current}</p>
          <p className="text-muted-foreground text-xs">{label}</p>
        </div>
        <p className="text-muted-foreground text-right text-[11px]">
          peak {count(peak)}
          <br />
          {note}
        </p>
      </div>

      <div className="mt-2 h-14">
        {data.length < 2 ? (
          <p className="text-muted-foreground/60 pt-5 text-[11px]">
            Waiting for a second sample…
          </p>
        ) : (
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={data} margin={{ top: 2, right: 2, bottom: 0, left: 0 }}>
              <YAxis hide domain={[0, peak * 1.15]} />
              <Tooltip
                content={({ active, payload }) => {
                  if (!active || !payload?.length) return null
                  const point = payload[0].payload as { second: number; value: number }
                  return (
                    <TooltipCard title={point.second + 's into the feed'}>
                      <TooltipRow color={color} label={label} value={decimal(point.value, 0)} />
                    </TooltipCard>
                  )
                }}
              />
              <Area
                type="stepAfter"
                dataKey="value"
                stroke={color}
                strokeWidth={2}
                fill={color}
                fillOpacity={0.18}
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

  // Rows scored is cumulative, so the rate is the delta between two polls.
  const previous = useRef<{ rows: number; at: number } | null>(null)
  const [flowsPerSecond, setFlowsPerSecond] = useState(0)

  useEffect(() => {
    if (!status) return
    const now = Date.now()
    const last = previous.current
    previous.current = { rows: status.rows_scored, at: now }
    if (!last || now === last.at) return
    const delta = status.rows_scored - last.rows
    setFlowsPerSecond(Math.max(0, (delta / (now - last.at)) * 1000))
  }, [status])

  const latestAlerts = rates.at(-1)?.alerts ?? 0

  return (
    <div className="grid gap-4 sm:grid-cols-2">
      <RateSparkline
        label="Flows scored per second"
        samples={rates.map((sample) => ({ ...sample, flows: flowsPerSecond }))}
        pick={(sample) => sample.flows}
        color="var(--series-benign)"
        current={count(Math.round(flowsPerSecond))}
        note="from rows_scored"
      />
      <RateSparkline
        label="Alerts per second"
        samples={rates}
        pick={(sample) => sample.alerts}
        color="var(--series-novel)"
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
            ? 'The stream is open and sending heartbeats. Alerts appear here as the replay raises them.'
            : 'Start a replay above. The feed connects itself once a source is live.'
        }
      />
    )
  }

  return (
    <ul className="divide-border scrollbar-thin divide-y overflow-y-auto" style={{ maxHeight: 420 }}>
      {events.map((event, index) => (
        <li key={String(event.id) + '-' + index}>
          <button
            type="button"
            onClick={() => onOpen(event.id)}
            className="hover:bg-muted/60 flex w-full items-center gap-3 px-3 py-1.5 text-left text-xs motion-safe:animate-[row-in_1.2s_ease-out]"
          >
            <span className="tabular text-muted-foreground shrink-0 font-mono">
              {clockTime(event.detected_at)}
            </span>
            <Badge variant={classVariant(event)} className="shrink-0">
              {isNovel(event) ? '◆ ' : ''}
              {classLabel(event)}
            </Badge>
            <Badge variant={SEVERITY_VARIANT[event.severity]} className="shrink-0">
              {event.severity}
            </Badge>
            <span className="tabular min-w-0 flex-1 truncate font-mono">
              {event.src_ip} <span className="text-muted-foreground">→</span>{' '}
              {endpoint(event.dst_ip, null)}
            </span>
            <span className="tabular text-muted-foreground shrink-0 font-mono">
              risk {decimal(event.risk_score)}
            </span>
            {event.occurrence_count > 1 ? (
              <span
                className="tabular shrink-0 font-mono font-medium"
                title={event.occurrence_count + ' flows collapsed by dedupe'}
              >
                ×{event.occurrence_count}
              </span>
            ) : null}
          </button>
        </li>
      ))}
    </ul>
  )
}
