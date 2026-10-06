/**
 * Model Performance panels: the per-class table, the confusion matrix, and the
 * PR/ROC pair.
 *
 * Every number here comes from the offline evaluation artifacts the backend
 * loads once at startup. None of it is recomputed from the database, and none of
 * it moves when a replay runs -- these describe the held-out day the model was
 * measured on, which is a different claim from "what this deployment has seen".
 */
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

import type { ClassMetrics, CurvePair } from '@/api/types'
import {
  AXIS_PROPS,
  ChartFrame,
  GRID_PROPS,
  heatFill,
  ScaleKey,
  TooltipCard,
  TooltipRow,
} from '@/components/charts/Chart'
import { count, decimal, percent, sentenceCase } from '@/lib/format'
import { cn } from '@/lib/utils'

export function PerClassTable({
  perClass,
  accuracy,
}: {
  perClass: Record<string, ClassMetrics>
  accuracy: number | null
}) {
  const rows = Object.entries(perClass)

  if (rows.length === 0) {
    return <p className="text-muted-foreground text-sm">No per-class metrics were recorded.</p>
  }

  return (
    <div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-muted-foreground border-border border-b text-xs">
              <th className="py-2 pr-3 text-left font-medium">Class</th>
              <th className="py-2 pr-3 text-right font-medium">Precision</th>
              <th className="py-2 pr-3 text-right font-medium">Recall</th>
              <th className="py-2 pr-3 text-right font-medium">F1</th>
              <th className="py-2 text-right font-medium">Support</th>
            </tr>
          </thead>
          <tbody className="divide-border divide-y">
            {rows.map(([label, metrics]) => (
              <tr key={label}>
                <td className="py-2 pr-3">{sentenceCase(label)}</td>
                <td className="tabular py-2 pr-3 text-right font-mono">
                  {decimal(metrics.precision, 3)}
                </td>
                <td className="tabular py-2 pr-3 text-right font-mono">
                  {decimal(metrics.recall, 3)}
                </td>
                <td className="tabular py-2 pr-3 text-right font-mono">
                  {decimal(metrics.f1, 3)}
                </td>
                <td className="tabular text-muted-foreground py-2 text-right font-mono">
                  {count(metrics.support)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/*
       * Accuracy appears once, as a footnote, and never as a headline. It is
       * reported rather than omitted because leaving it out invites the
       * question of what it was; it is a footnote because on 99% benign traffic
       * a model that always answers "benign" scores 99%.
       */}
      {accuracy !== null ? (
        <p className="text-muted-foreground mt-3 text-xs leading-relaxed">
          Overall accuracy on this split is {percent(accuracy, 1)}. It is a footnote on purpose:
          the split is overwhelmingly benign, so always answering benign would score close to the
          same number while catching nothing. PR-AUC is the figure to read.
        </p>
      ) : null}
    </div>
  )
}

/**
 * The confusion matrix.
 *
 * Shaded by row share rather than by raw count, and labelled with the count.
 * Shading by count would make every cell outside the benign row invisible --
 * the benign class outnumbers the attack classes by three orders of magnitude,
 * so one cell would be the only coloured square on the grid. Row share answers
 * the question the matrix is read for: of the rows that truly were this class,
 * where did they go?
 */
export function ConfusionMatrix({
  labels,
  matrix,
}: {
  labels: string[]
  matrix: number[][]
}) {
  if (labels.length === 0 || matrix.length === 0) {
    return <p className="text-muted-foreground text-sm">No confusion matrix was recorded.</p>
  }

  const rowTotals = matrix.map((row) => row.reduce((total, value) => total + value, 0))

  return (
    <div>
      <div className="overflow-x-auto">
        <table className="text-xs">
          <thead>
            <tr>
              <th className="text-muted-foreground px-2 py-1 text-left font-medium">
                true \ predicted
              </th>
              {labels.map((label) => (
                <th
                  key={label}
                  className="text-muted-foreground px-2 py-1 text-center font-medium"
                >
                  {sentenceCase(label)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {matrix.map((row, rowIndex) => (
              <tr key={labels[rowIndex] ?? rowIndex}>
                <th className="text-muted-foreground px-2 py-1 text-right font-medium whitespace-nowrap">
                  {sentenceCase(labels[rowIndex] ?? String(rowIndex))}
                </th>
                {row.map((value, columnIndex) => {
                  const share = rowTotals[rowIndex] ? value / rowTotals[rowIndex] : 0
                  const diagonal = rowIndex === columnIndex
                  return (
                    <td
                      key={columnIndex}
                      className={cn(
                        'border-border tabular border px-2 py-1.5 text-center font-mono',
                        diagonal && 'font-semibold',
                      )}
                      style={{ backgroundColor: heatFill(share) }}
                      title={
                        percent(share, 1) +
                        ' of true ' +
                        (labels[rowIndex] ?? '') +
                        ' predicted as ' +
                        (labels[columnIndex] ?? '')
                      }
                    >
                      {count(value)}
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
        <ScaleKey label="Share of the true class" from="0%" max="100%" />
        <p className="text-muted-foreground text-xs">
          Cells carry counts; shade is the row share.
        </p>
      </div>
    </div>
  )
}

/**
 * PR and ROC, side by side, with the caption the pairing exists for.
 *
 * Two charts rather than one with two scales, and deliberately adjacent: the
 * gap between them is the argument, and an argument you can see beats one the
 * README asserts.
 */
export function CurvePanels({
  curves,
  prAuc,
  rocAuc,
}: {
  curves: CurvePair
  prAuc: number | null
  rocAuc: number | null
}) {
  const pr = curves.pr.map(([recall, precision]) => ({ x: recall, y: precision }))
  const roc = curves.roc.map(([fpr, tpr]) => ({ x: fpr, y: tpr }))

  // The precision a classifier gets for free by answering "attack" to
  // everything: the attack share of the split. Without it, a PR curve has no
  // baseline and 0.77 looks mediocre rather than six times chance.
  const chance = pr.length ? pr[0].y : null

  return (
    <div>
      <div className="grid gap-8 lg:grid-cols-2">
        <ChartFrame
          title="Precision–recall"
          description={prAuc === null ? undefined : 'PR-AUC ' + decimal(prAuc, 3) + ' — the headline metric'}
          height={250}
        >
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={pr} margin={{ top: 4, right: 12, bottom: 18, left: 0 }}>
              <CartesianGrid {...GRID_PROPS} />
              <XAxis
                dataKey="x"
                type="number"
                domain={[0, 1]}
                {...AXIS_PROPS}
                tickFormatter={(value: number) => decimal(value, 1)}
                label={{ value: 'Recall', position: 'insideBottom', offset: -12, fontSize: 11 }}
              />
              <YAxis
                type="number"
                domain={[0, 1]}
                {...AXIS_PROPS}
                width={34}
                tickFormatter={(value: number) => decimal(value, 1)}
              />
              {chance !== null ? (
                <ReferenceLine
                  y={chance}
                  stroke="var(--color-axis)"
                  label={{
                    value: 'chance',
                    position: 'insideTopRight',
                    fontSize: 10,
                    fill: 'var(--color-muted-foreground)',
                  }}
                />
              ) : null}
              <Tooltip
                content={({ active, payload }) => {
                  if (!active || !payload?.length) return null
                  const point = payload[0].payload as { x: number; y: number }
                  return (
                    <TooltipCard>
                      <TooltipRow label="Recall" value={decimal(point.x, 3)} />
                      <TooltipRow label="Precision" value={decimal(point.y, 3)} />
                    </TooltipCard>
                  )
                }}
              />
              <Line
                dataKey="y"
                type="monotone"
                stroke="var(--series-attack)"
                strokeWidth={2}
                dot={false}
                isAnimationActive={false}
              />
            </LineChart>
          </ResponsiveContainer>
        </ChartFrame>

        <ChartFrame
          title="ROC"
          description={rocAuc === null ? undefined : 'ROC-AUC ' + decimal(rocAuc, 3) + ' — flattering, and that is the point'}
          height={250}
        >
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={roc} margin={{ top: 4, right: 12, bottom: 18, left: 0 }}>
              <CartesianGrid {...GRID_PROPS} />
              <XAxis
                dataKey="x"
                type="number"
                domain={[0, 1]}
                {...AXIS_PROPS}
                tickFormatter={(value: number) => decimal(value, 1)}
                label={{
                  value: 'False-positive rate',
                  position: 'insideBottom',
                  offset: -12,
                  fontSize: 11,
                }}
              />
              <YAxis
                type="number"
                domain={[0, 1]}
                {...AXIS_PROPS}
                width={34}
                tickFormatter={(value: number) => decimal(value, 1)}
              />
              <Tooltip
                content={({ active, payload }) => {
                  if (!active || !payload?.length) return null
                  const point = payload[0].payload as { x: number; y: number }
                  return (
                    <TooltipCard>
                      <TooltipRow label="False-positive rate" value={decimal(point.x, 4)} />
                      <TooltipRow label="True-positive rate" value={decimal(point.y, 3)} />
                    </TooltipCard>
                  )
                }}
              />
              <Line
                dataKey="y"
                type="monotone"
                stroke="var(--series-benign)"
                strokeWidth={2}
                dot={false}
                isAnimationActive={false}
              />
            </LineChart>
          </ResponsiveContainer>
        </ChartFrame>
      </div>

      <p className="text-muted-foreground mt-6 max-w-3xl text-sm leading-relaxed">
        <span className="text-foreground font-medium">
          Why these two are drawn next to each other.
        </span>{' '}
        ROC plots the true-positive rate against the false-positive rate, and the false-positive
        rate has the count of benign rows in its denominator. On traffic that is 99% benign that
        denominator is enormous, so thousands of false alerts barely move the curve and the
        classifier looks excellent. Precision has the count of <em>predicted positives</em> in its
        denominator, so the same thousands of false alerts collapse it immediately. The PR curve is
        the one that reflects what the analyst&rsquo;s queue actually looks like, which is why
        PR-AUC is the headline here and ROC-AUC is reported beside it rather than instead of it.
      </p>
    </div>
  )
}
