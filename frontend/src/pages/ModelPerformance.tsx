/**
 * Screen 4 — model performance.
 *
 * The LOAO panel leads, which is a deliberate inversion of how a model page is
 * usually built. Per-class tables and a confusion matrix describe performance on
 * traffic the model was trained for; the leave-one-attack-out table is the only
 * thing here that speaks to the claim the project actually makes. Putting it
 * third, under the furniture, would bury the evidence.
 *
 * Nothing on this screen is recomputed from the database. These are the numbers
 * measured on the held-out day, and they do not move when a replay runs.
 */
import { useModelMetrics } from '@/api/queries'
import { ScreenBody } from '@/components/AppShell'
import { ErrorBoundary } from '@/components/ErrorBoundary'
import { LoaoPanel } from '@/components/model/LoaoPanel'
import { ConfusionMatrix, CurvePanels, PerClassTable } from '@/components/model/Panels'
import { ErrorState, LoadingRows } from '@/components/States'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { count, decimal, percent, smallNumber } from '@/lib/format'

export function ModelPerformance() {
  const { data, error, isPending } = useModelMetrics()

  if (isPending) {
    return (
      <ScreenBody title="Model performance">
        <LoadingRows rows={10} />
      </ScreenBody>
    )
  }

  if (error || !data) {
    return (
      <ScreenBody title="Model performance">
        <ErrorState error={error} label="Could not load the measured metrics" />
      </ScreenBody>
    )
  }

  const budget = data.budget as Record<string, unknown>
  const budgetPerDay = typeof budget.max_alerts_per_day === 'number' ? budget.max_alerts_per_day : null
  const targetFpr = typeof budget.target_fpr === 'number' ? budget.target_fpr : null

  return (
    <ScreenBody
      title="Model performance"
      lede="Measured on the held-out day by the offline evaluation, served from the artifacts beside the model. None of it is recomputed from stored alerts, so none of it moves when a replay runs."
      actions={
        <div className="flex flex-wrap items-center gap-2">
          {data.pr_auc !== null ? (
            <Badge variant="info">Stage 1 PR-AUC {decimal(data.pr_auc, 3)}</Badge>
          ) : null}
          {data.stage2_pr_auc !== null ? (
            <Badge variant="novel">Stage 2 PR-AUC {decimal(data.stage2_pr_auc, 3)}</Badge>
          ) : null}
        </div>
      }
    >
      <div className="space-y-6">
        {/* The evidence first. */}
        <ErrorBoundary label="Leave-one-attack-out panel">
          <Card className="border-[color-mix(in_oklab,var(--novel)_35%,var(--color-border))]">
            <CardHeader>
              <CardTitle className="text-base">
                Leave-one-attack-out: detection of families the classifier never saw
              </CardTitle>
              <CardDescription>
                The measurement behind this project&rsquo;s central claim, with the misses shown.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <LoaoPanel metrics={data} />
            </CardContent>
          </Card>
        </ErrorBoundary>

        <ErrorBoundary label="Curve pair">
          <Card>
            <CardHeader>
              <CardTitle>Precision–recall against ROC</CardTitle>
              <CardDescription>
                Drawn side by side because the gap between them is the argument.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <CurvePanels
                curves={data.curves}
                prAuc={data.pr_auc}
                rocAuc={data.roc_auc}
              />
            </CardContent>
          </Card>
        </ErrorBoundary>

        <div className="grid gap-6 xl:grid-cols-2">
          <ErrorBoundary label="Per-class metrics">
            <Card>
              <CardHeader>
                <CardTitle>Per-class metrics</CardTitle>
                <CardDescription>
                  Stage 1, on the classes it was trained to name.
                </CardDescription>
              </CardHeader>
              <CardContent>
                <PerClassTable perClass={data.per_class} accuracy={data.accuracy} />
              </CardContent>
            </Card>
          </ErrorBoundary>

          <ErrorBoundary label="Confusion matrix">
            <Card>
              <CardHeader>
                <CardTitle>Confusion matrix</CardTitle>
                <CardDescription>Where each true class actually went.</CardDescription>
              </CardHeader>
              <CardContent>
                <ConfusionMatrix labels={data.labels} matrix={data.confusion_matrix} />
              </CardContent>
            </Card>
          </ErrorBoundary>
        </div>

        <ErrorBoundary label="Operating point">
          <Card>
            <CardHeader>
              <CardTitle>The operating point, and the budget it was cut against</CardTitle>
              <CardDescription>
                A threshold is a staffing decision. These are the numbers it was chosen from.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <dl className="grid gap-x-8 gap-y-4 sm:grid-cols-2 lg:grid-cols-4">
                <div>
                  <dd className="text-lg font-semibold">
                    {data.tau_sup === null ? '—' : decimal(data.tau_sup, 4)}
                  </dd>
                  <dt className="text-muted-foreground text-xs">
                    Stage 1 threshold, chosen from a false-positive budget rather than left at 0.5
                  </dt>
                </div>
                <div>
                  <dd className="tabular text-lg font-semibold">
                    {smallNumber(data.fpr_at_threshold)}
                  </dd>
                  <dt className="text-muted-foreground text-xs">
                    Measured false-positive rate there
                    {targetFpr !== null ? ', against a target of ' + smallNumber(targetFpr) : ''}
                  </dt>
                </div>
                <div>
                  <dd className="text-lg font-semibold">
                    {data.alerts_per_analyst_hour === null
                      ? '—'
                      : count(Math.round(data.alerts_per_analyst_hour))}
                  </dd>
                  <dt className="text-muted-foreground text-xs">
                    Stage 1 alerts per analyst hour. The fused figure on the queue is far higher,
                    because Stage 2&rsquo;s threshold is a benign percentile rather than a staffing
                    decision.
                  </dt>
                </div>
                <div>
                  <dd className="text-lg font-semibold">
                    {budgetPerDay === null ? '—' : count(budgetPerDay)}
                  </dd>
                  <dt className="text-muted-foreground text-xs">
                    Alerts a day the configured analyst capacity can read
                  </dt>
                </div>
              </dl>

              <div className="border-border mt-6 grid gap-6 border-t pt-5 lg:grid-cols-2">
                <FamilyRecall
                  title="Stage 1 alone, per family"
                  blurb="What the classifier catches by itself, including families outside its vocabulary."
                  recall={data.stage1_family_recall}
                  tone="var(--series-known)"
                />
                <FamilyRecall
                  title="Stage 2 alone, per family"
                  blurb="What the benign-only autoencoder catches by itself, having never seen an attack."
                  recall={data.stage2_family_recall}
                  tone="var(--series-novel)"
                />
              </div>
            </CardContent>
          </Card>
        </ErrorBoundary>
      </div>
    </ScreenBody>
  )
}

/** Per-family recall for one stage, as a ranked list rather than a chart: it is
 *  a handful of labelled values, and a bar per row reads faster than axes. */
function FamilyRecall({
  title,
  blurb,
  recall,
  tone,
}: {
  title: string
  blurb: string
  recall: Record<string, unknown>
  tone: string
}) {
  const rows = Object.entries(recall)
    .map(([family, value]) => {
      const entry = typeof value === 'object' && value !== null ? (value as Record<string, unknown>) : {}
      return {
        family,
        recall: typeof entry.recall === 'number' ? entry.recall : 0,
        support: typeof entry.support === 'number' ? entry.support : 0,
      }
    })
    .sort((a, b) => b.recall - a.recall)

  if (rows.length === 0) return null

  return (
    <div>
      <h4 className="text-sm font-semibold tracking-tight">{title}</h4>
      <p className="text-muted-foreground mt-1 mb-3 text-xs leading-relaxed">{blurb}</p>
      <ul className="space-y-1.5">
        {rows.map((row) => (
          <li key={row.family} className="flex items-center gap-3 text-xs">
            <span className="w-24 shrink-0 truncate">{row.family}</span>
            <span
              aria-hidden="true"
              className="h-1.5 min-w-0 flex-1 overflow-hidden rounded-full bg-[var(--color-muted)]"
            >
              <span
                className="block h-full rounded-full"
                style={{ width: Math.max(1, row.recall * 100) + '%', backgroundColor: tone }}
              />
            </span>
            <span className="tabular w-12 shrink-0 text-right font-mono">
              {percent(row.recall, 1)}
            </span>
            <span className="tabular text-muted-foreground w-14 shrink-0 text-right font-mono">
              {count(row.support)}
            </span>
          </li>
        ))}
      </ul>
    </div>
  )
}
