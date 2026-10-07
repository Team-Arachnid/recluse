/**
 * Why the model scored this flow the way it did.
 *
 * Two explainers, one chart, because the drawer asks one question either way --
 * and because the analyst should not have to learn which model fired to read
 * the answer.
 *
 * - Stage 1 is TreeSHAP, whose contributions are *signed*: a feature can push
 *   toward the attack class or away from it. That is a polarity encoding, so it
 *   gets the diverging pair -- warm toward attack, cool toward benign -- with a
 *   real zero baseline between them.
 * - Stage 2 is per-feature reconstruction error, which is a squared quantity and
 *   therefore never negative. That is a magnitude encoding, so it gets one hue.
 *   Colouring it as though it were signed would invent a direction the number
 *   does not have.
 */
import { Bar, BarChart, Cell, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'

import type { AlertExplanation } from '@/api/types'
import { AXIS_PROPS, ChartFrame, TooltipCard, TooltipRow } from '@/components/charts/Chart'
import { decimal, percent } from '@/lib/format'

const BAR_HEIGHT = 26
const RADIUS = 4

interface Point {
  feature: string
  /** What the bar is drawn from: a signed contribution, or an error magnitude. */
  magnitude: number
  share: number
  value: number | null
}

/**
 * A bar rounded only at its data end.
 *
 * Recharts applies one `radius` to every bar in a series, which would round the
 * baseline end too and leave each mark floating off its own axis. A custom shape
 * is the only way to anchor the square end to the zero line, which is what makes
 * a diverging chart readable: the eye finds the baseline because nothing else
 * touches it.
 */
function EndRoundedBar(props: {
  x?: number
  y?: number
  width?: number
  height?: number
  fill?: string
  payload?: Point
}) {
  const { x = 0, y = 0, width = 0, height = 0, fill, payload } = props
  const negative = (payload?.magnitude ?? 0) < 0
  const radius = Math.min(RADIUS, Math.abs(width) / 2, height / 2)

  // A path rather than a rect, so two corners can be round and two square.
  const left = Math.min(x, x + width)
  const right = Math.max(x, x + width)
  const top = y
  const bottom = y + height

  const d = negative
    ? `M ${right} ${top} L ${left + radius} ${top} Q ${left} ${top} ${left} ${top + radius}` +
      ` L ${left} ${bottom - radius} Q ${left} ${bottom} ${left + radius} ${bottom} L ${right} ${bottom} Z`
    : `M ${left} ${top} L ${right - radius} ${top} Q ${right} ${top} ${right} ${top + radius}` +
      ` L ${right} ${bottom - radius} Q ${right} ${bottom} ${right - radius} ${bottom} L ${left} ${bottom} Z`

  return <path d={d} fill={fill} />
}

export function ExplanationChart({ explanation }: { explanation: AlertExplanation }) {
  const signed = explanation.explainer === 'treeshap'

  const points: Point[] = explanation.contributors.map((contributor) => ({
    feature: contributor.feature,
    magnitude: signed ? (contributor.contribution ?? 0) : (contributor.error ?? 0),
    share: contributor.share,
    value: contributor.value ?? null,
  }))

  if (points.length === 0) {
    return (
      <p className="text-muted-foreground text-xs">
        No per-feature attribution was stored for this alert.
      </p>
    )
  }

  const widest = Math.max(...points.map((point) => Math.abs(point.magnitude))) || 1

  return (
    <ChartFrame
      height={points.length * BAR_HEIGHT + 32}
      series={
        signed
          ? [
              { label: 'Toward the attack class', color: 'var(--series-attack)' },
              { label: 'Toward benign', color: 'var(--series-benign)' },
            ]
          : undefined
      }
      caption={
        signed
          ? 'Signed TreeSHAP contributions for the predicted class. Length is how much the feature moved the score; side is which way.'
          : 'Per-feature reconstruction error: how badly the autoencoder failed to rebuild each value. Squared, so it has a size but no direction.'
      }
    >
      <ResponsiveContainer width="100%" height="100%">
        <BarChart
          data={points}
          layout="vertical"
          margin={{ top: 0, right: 8, bottom: 4, left: 0 }}
          barCategoryGap={4}
        >
          <XAxis
            type="number"
            domain={signed ? [-widest * 1.1, widest * 1.1] : [0, widest * 1.1]}
            {...AXIS_PROPS}
            axisLine={false}
            tickFormatter={(value: number) => decimal(value, 2)}
            height={18}
          />
          <YAxis
            type="category"
            dataKey="feature"
            width={148}
            {...AXIS_PROPS}
            axisLine={false}
            tick={{ fontSize: 11, fontFamily: 'var(--font-mono)', fill: 'var(--color-muted-foreground)' }}
          />
          {signed ? <ReferenceLine x={0} stroke="var(--color-axis)" /> : null}
          <Tooltip
            cursor={{ fill: 'var(--color-muted)', opacity: 0.4 }}
            content={({ active, payload }) => {
              if (!active || !payload?.length) return null
              const point = payload[0].payload as Point
              return (
                <TooltipCard title={<span className="font-mono">{point.feature}</span>}>
                  <TooltipRow
                    label={signed ? 'Contribution' : 'Reconstruction error'}
                    value={decimal(point.magnitude, 4)}
                  />
                  <TooltipRow label="Share of total" value={percent(point.share)} />
                  {point.value !== null ? (
                    <TooltipRow label="Scaled value" value={decimal(point.value, 4)} />
                  ) : null}
                </TooltipCard>
              )
            }}
          />
          <Bar dataKey="magnitude" shape={<EndRoundedBar />} isAnimationActive={false}>
            {points.map((point) => (
              <Cell
                key={point.feature}
                fill={
                  // Stage 2's error is drawn in Stage 2's own channel, so a
                  // glance at the chart says which model is explaining.
                  !signed
                    ? 'var(--series-novel)'
                    : point.magnitude < 0
                      ? 'var(--series-benign)'
                      : 'var(--series-attack)'
                }
              />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </ChartFrame>
  )
}
