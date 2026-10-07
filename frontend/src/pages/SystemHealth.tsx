/**
 * System: what is running, what it was built from, and what it will never do.
 *
 * No longer the landing page. From Phase 6 the landing page is the triage queue
 * -- analysts live in the queue, so the queue is home -- and this became what it
 * always described itself as: a status view, reached from the nav.
 */
import { Ban, Calculator, CheckCircle2, CircleDashed, CircleDotDashed } from 'lucide-react'

import { useModelMetrics } from '@/api/queries'
import { ScreenBody } from '@/components/AppShell'
import { ErrorBoundary } from '@/components/ErrorBoundary'
import { HealthPanel } from '@/components/HealthPanel'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { count, decimal, smallNumber } from '@/lib/format'

/**
 * The build phases, with what each one actually produced.
 *
 * Kept because it is the fastest honest answer to "what works and what doesn't".
 */
const PHASES: ReadonlyArray<{
  id: number
  name: string
  state: 'done' | 'partial' | 'pending'
  note?: string
}> = [
  { id: 0, name: 'Scaffolding', state: 'done' },
  { id: 1, name: 'Data and features', state: 'done' },
  { id: 2, name: 'Supervised classifier', state: 'done' },
  { id: 3, name: 'Benign-only autoencoder', state: 'done' },
  { id: 4, name: 'Fusion and leave-one-attack-out', state: 'done' },
  { id: 5, name: 'Backend API', state: 'done' },
  { id: 6, name: 'Dashboard', state: 'done' },
  { id: 7, name: 'Drift and active learning', state: 'done' },
  { id: 8, name: 'Packaging', state: 'done' },
  {
    id: 9,
    name: 'Live capture',
    state: 'partial',
    note: 'Capture, the shadow burn-in and the local threshold are built. The self-run attack exercise belongs in a lab you own and has not been run.',
  },
]

const num = (value: unknown): number | null =>
  typeof value === 'number' && Number.isFinite(value) ? value : null

/**
 * The false-positive budget, as the arithmetic it is.
 *
 * A threshold is a staffing decision, and this is the one place the dashboard
 * shows the sum rather than its answer: daily volume, analyst capacity and shift
 * length in, the false-positive rate the Stage 1 threshold was cut to out.
 */
function BudgetArithmetic() {
  const { data, isPending } = useModelMetrics()
  const budget = (data?.budget ?? {}) as Record<string, unknown>

  const volume = num(budget.expected_daily_flow_volume)
  const capacity = num(budget.analyst_capacity_per_hour)
  const shift = num(budget.analyst_shift_hours)
  const perDay = num(budget.max_alerts_per_day)
  const target = num(budget.target_fpr)
  const tauSup = num(data?.tau_sup)

  const line = (term: string, value: string, note?: string) => (
    <div className="border-divider flex items-baseline justify-between gap-4 border-b py-2 last:border-b-0">
      <dt className="text-muted-foreground text-xs">
        {term}
        {note ? <span className="text-subtle-foreground ml-2 font-mono text-[10.5px]">{note}</span> : null}
      </dt>
      <dd className="text-foreground-strong font-mono text-sm">{value}</dd>
    </div>
  )

  return (
    <Card>
      <CardHeader>
        <CardTitle>The false-positive budget</CardTitle>
        <CardDescription>
          Where the Stage 1 threshold comes from. Not 0.5 — a number derived from how many alerts a
          shift can actually read.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {isPending ? (
          <p className="text-subtle-foreground text-xs">Loading the measured budget…</p>
        ) : volume === null ? (
          <p className="text-subtle-foreground text-xs">
            No trained model is loaded, so no budget has been applied yet.
          </p>
        ) : (
          <dl>
            {line('Expected flows per day', count(volume), 'V')}
            {line('Analyst capacity per hour', count(capacity), 'C')}
            {line('Shift length, hours', count(shift))}
            {line('Alerts a day the shift can read', count(perDay), 'C × shift')}
            {line('Target false-positive rate', smallNumber(target), 'alerts ÷ V')}
            {line('Stage 1 threshold τ_sup', tauSup === null ? '—' : decimal(tauSup, 4), 'smallest τ under the target')}
          </dl>
        )}
        <p className="text-subtle-foreground mt-3 flex items-start gap-2 text-[11px] leading-relaxed">
          <Calculator className="mt-px size-3.5 shrink-0" aria-hidden="true" />
          All three inputs live in <code className="font-mono">.env</code>; the service logs the
          resulting budget at startup and every retrain re-derives its threshold from it.
        </p>
      </CardContent>
    </Card>
  )
}

export function SystemHealth() {
  return (
    <ScreenBody
      title="System"
      lede={
        <>
          Two-stage network intrusion detection. Stage 1 names the attacks it was trained on; Stage
          2 flags traffic that does not look like normal, including families it has never seen. The
          system alerts, ranks and explains — it never blocks traffic.
        </>
      }
    >
      <div className="grid gap-4 lg:grid-cols-[minmax(0,26rem)_minmax(0,1fr)]">
        <div className="space-y-4">
          <ErrorBoundary label="Health panel">
            <HealthPanel />
          </ErrorBoundary>

          <Card>
            <CardHeader>
              <CardTitle>Containment</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="flex items-start gap-3">
                <div className="bg-raised border-raised-border flex size-10 shrink-0 items-center justify-center rounded-lg border">
                  <Ban className="text-muted-foreground size-[18px]" aria-hidden="true" />
                </div>
                <div>
                  <p className="text-foreground-strong font-mono text-2xl font-bold">0</p>
                  <p className="text-muted-foreground text-xs leading-relaxed">
                    flows dropped, by design. There is no endpoint, button or flag that blocks
                    traffic: <code className="font-mono">IDS_ALLOW_AUTO_BLOCK=true</code> is refused
                    at startup. At a million flows a day, a false-positive rate of one in a thousand
                    is a thousand wrong blocks.
                  </p>
                </div>
              </div>
            </CardContent>
          </Card>
        </div>

        <div className="space-y-4">
          <ErrorBoundary label="Budget arithmetic">
            <BudgetArithmetic />
          </ErrorBoundary>

          <Card>
            <CardHeader>
              <CardTitle>Build progress</CardTitle>
            </CardHeader>
            <CardContent>
              <ol className="grid gap-1.5 sm:grid-cols-2">
                {PHASES.map((phase) => (
                  <li
                    key={phase.id}
                    className="border-border bg-inset flex items-center gap-3 rounded-lg border px-3 py-2"
                    data-state={phase.state}
                  >
                    <span className="text-subtle-foreground tabular font-mono text-xs">
                      {String(phase.id).padStart(2, '0')}
                    </span>
                    <span className="text-foreground flex-1 truncate text-sm">{phase.name}</span>
                    {phase.state === 'done' ? (
                      <CheckCircle2
                        className="size-4 text-[var(--ok)]"
                        aria-label="complete"
                      />
                    ) : phase.state === 'partial' ? (
                      <span title={phase.note} className="inline-flex">
                        <CircleDotDashed
                          className="size-4 text-[var(--medium)]"
                          aria-label="partly complete"
                        />
                      </span>
                    ) : (
                      <CircleDashed className="text-subtle-foreground size-4" aria-label="pending" />
                    )}
                  </li>
                ))}
              </ol>
              <div className="mt-4 flex flex-wrap gap-2">
                <Badge variant="ok">alert-only</Badge>
                <Badge variant="neutral">temporal splits</Badge>
                <Badge variant="neutral">benign-only Stage 2</Badge>
                <Badge variant="neutral">one feature module</Badge>
                <Badge variant="novel">leave-one-attack-out</Badge>
              </div>
            </CardContent>
          </Card>
        </div>
      </div>
    </ScreenBody>
  )
}
