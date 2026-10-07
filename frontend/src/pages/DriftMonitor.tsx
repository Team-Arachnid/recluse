/**
 * Screen 5 — the drift monitor.
 *
 * Everything here is measured. The PSI snapshots come from the nightly job
 * (`python -m training.drift_job`), computed over a systematic sample of *scored
 * traffic* rather than over stored alerts -- an alert is a row that crossed a
 * threshold, so a PSI built from alerts would answer whether the alerts look
 * unusual, which is a different and much less useful question.
 *
 * Until the first run there is nothing to plot, and this screen says so rather
 * than drawing a flat line at zero. A drift monitor inventing a flat line is the
 * most dangerous piece of mock data a project like this could ship: it tells its
 * reader everything is fine.
 */
import { Activity, Database, Gauge, Layers, RefreshCw } from 'lucide-react'

import { useDrift, useModelRegistry } from '@/api/queries'
import type { RegistryEntry } from '@/api/types'
import { ScreenBody } from '@/components/AppShell'
import {
  BaselineOverlay,
  PsiTable,
  PsiTrend,
  RankedPsi,
  RetrainBanner,
  SnapshotSummary,
} from '@/components/drift/Panels'
import { ErrorBoundary } from '@/components/ErrorBoundary'
import { EmptyState, ErrorState, LoadingRows } from '@/components/States'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { count, dateTime, decimal, sinceNow } from '@/lib/format'

/**
 * The registry, and with it the audit trail.
 *
 * `alerts_scored` is the column somebody reads after an incident: it is how many
 * decisions a model that turned out to be wrong was behind. The version strings
 * have been on every alert since Phase 5; what this adds is somewhere to ask.
 */
function RegistryTable({ serving, versions }: { serving: string; versions: RegistryEntry[] }) {
  if (versions.length === 0) {
    return (
      <EmptyState
        title="No model versions recorded"
        hint="A version is registered when the API starts with a bundle loaded."
      />
    )
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[46rem] text-sm">
        <thead>
          <tr className="text-muted-foreground border-border border-b text-xs">
            <th className="py-2 pr-3 text-left font-medium">Version</th>
            <th className="px-2 py-2 text-left font-medium">Stage</th>
            <th className="px-2 py-2 text-left font-medium">Trained</th>
            <th className="px-2 py-2 text-right font-medium">τ Stage 1</th>
            <th className="px-2 py-2 text-right font-medium">τ Stage 2</th>
            <th className="px-2 py-2 text-right font-medium">Alerts scored</th>
            <th className="py-2 pl-2 text-right font-medium">Verdicts</th>
          </tr>
        </thead>
        <tbody className="divide-border divide-y">
          {versions.map((entry) => (
            <tr key={entry.version}>
              <td className="py-2.5 pr-3">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-mono text-xs">{entry.version}</span>
                  {entry.version === serving ? <Badge variant="ok">serving</Badge> : null}
                </div>
                {entry.trained_on ? (
                  <p className="text-muted-foreground mt-1 max-w-md text-[11px] leading-snug">
                    {entry.trained_on}
                  </p>
                ) : null}
                {entry.notes ? (
                  <p className="text-muted-foreground mt-1 max-w-md text-[11px] leading-snug">
                    {entry.notes}
                  </p>
                ) : null}
              </td>
              <td className="px-2 py-2.5">
                <Badge variant={entry.stage === 'champion' ? 'info' : 'neutral'}>
                  {entry.stage}
                </Badge>
              </td>
              <td className="text-muted-foreground px-2 py-2.5 text-xs">
                {entry.trained_at ? dateTime(entry.trained_at) : '—'}
              </td>
              <td className="tabular px-2 py-2.5 text-right font-mono text-xs">
                {entry.tau_sup === null ? '—' : decimal(entry.tau_sup, 4)}
              </td>
              <td className="tabular px-2 py-2.5 text-right font-mono text-xs">
                {entry.tau_anom === null ? '—' : decimal(entry.tau_anom, 4)}
              </td>
              <td className="tabular px-2 py-2.5 text-right font-mono">
                {count(entry.alerts_scored)}
              </td>
              <td className="tabular text-muted-foreground py-2.5 pl-2 text-right font-mono">
                {count(entry.verdicts_recorded)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <p className="text-muted-foreground mt-3 max-w-2xl text-xs leading-relaxed">
        <span className="text-foreground font-medium">Alerts scored is the audit column.</span> Every
        alert and every verdict carries the version of the model that produced it, captured at write
        time, so a label stays attributed to the model that actually made the decision even after a
        promotion. This is the number somebody needs when a model turns out to have been wrong.
      </p>
    </div>
  )
}

export function DriftMonitor() {
  const drift = useDrift()
  const registry = useModelRegistry()

  const latest = drift.data?.latest ?? null

  return (
    <ScreenBody
      title="Drift"
      lede="A model is calibrated against a baseline, and baselines move. This is where that gets caught before it shows up as a false-positive rate nobody can explain."
      actions={
        <Button
          variant="outline"
          size="sm"
          onClick={() => void drift.refetch()}
          disabled={drift.isFetching}
        >
          <RefreshCw className={drift.isFetching ? 'animate-spin' : undefined} aria-hidden="true" />
          Refresh
        </Button>
      }
    >
      <div className="space-y-6">
        {drift.isPending ? (
          <LoadingRows rows={8} />
        ) : drift.error ? (
          <ErrorState error={drift.error} label="Could not load the drift snapshots" />
        ) : !latest ? (
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Gauge className="size-4 text-[var(--medium)]" aria-hidden="true" />
                No snapshot has been computed yet
              </CardTitle>
              <CardDescription>
                Nothing is plotted, deliberately — a flat line at zero would read as
                &ldquo;everything is fine&rdquo;.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <p className="max-w-2xl text-sm leading-relaxed">
                Drift is computed by a job, not by this screen: a PSI across every feature is a full
                scan of the sample table, and it describes a window of time rather than a request —
                computing it per request would give two readers different answers minutes apart.
              </p>
              <dl className="text-muted-foreground mt-4 flex flex-wrap gap-x-8 gap-y-2 text-xs">
                <div className="flex gap-1.5">
                  <dt>Flows sampled so far</dt>
                  <dd className="tabular text-foreground font-mono">
                    {count(drift.data?.sampled_rows)}
                  </dd>
                </div>
                <div className="flex gap-1.5">
                  <dt>Sampling rate</dt>
                  <dd className="text-foreground">
                    one flow in {count(drift.data?.sample_stride)}
                  </dd>
                </div>
              </dl>
              <p className="text-muted-foreground mt-4 text-xs">
                Run <code className="font-mono">make drift</code> to compute one now, or schedule{' '}
                <code className="font-mono">python -m training.drift_job</code> nightly.
              </p>
            </CardContent>
          </Card>
        ) : (
          <>
            <ErrorBoundary label="Retrain banner">
              <RetrainBanner snapshot={latest} />
            </ErrorBoundary>

            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <Activity className="size-4 text-[var(--info)]" aria-hidden="true" />
                  The latest snapshot
                </CardTitle>
                <CardDescription>
                  PSI = Σ (actual% − expected%) × ln(actual% / expected%), over ten quantile bins of
                  the training reference.
                </CardDescription>
              </CardHeader>
              <CardContent>
                <SnapshotSummary snapshot={latest} />
              </CardContent>
            </Card>

            <ErrorBoundary label="Ranked PSI">
              <Card>
                <CardHeader>
                  <CardTitle>What has moved</CardTitle>
                  <CardDescription>
                    Worst first. One measure per feature, so one encoding — length — with the band
                    carried as a word rather than by colour alone.
                  </CardDescription>
                </CardHeader>
                <CardContent>
                  <RankedPsi snapshot={latest} />
                </CardContent>
              </Card>
            </ErrorBoundary>

            <div className="grid gap-6 xl:grid-cols-2">
              <ErrorBoundary label="PSI trend">
                <Card>
                  <CardHeader>
                    <CardTitle>Has it been moving</CardTitle>
                    <CardDescription>
                      {count(drift.data?.snapshots)} snapshot
                      {drift.data?.snapshots === 1 ? '' : 's'} stored.
                    </CardDescription>
                  </CardHeader>
                  <CardContent>
                    <PsiTrend series={drift.data?.series ?? []} />
                  </CardContent>
                </Card>
              </ErrorBoundary>

              <ErrorBoundary label="Baseline overlay">
                <Card>
                  <CardHeader>
                    <CardTitle>Has the score baseline moved</CardTitle>
                    <CardDescription>
                      The benign distribution <code className="font-mono">tau_anom</code> was cut
                      from, against this window&rsquo;s.
                    </CardDescription>
                  </CardHeader>
                  <CardContent>
                    {drift.data ? <BaselineOverlay drift={drift.data} /> : null}
                  </CardContent>
                </Card>
              </ErrorBoundary>
            </div>

            <ErrorBoundary label="PSI table">
              <Card>
                <CardHeader>
                  <CardTitle className="flex items-center gap-2">
                    <Layers className="size-4 text-[var(--muted-foreground)]" aria-hidden="true" />
                    Every feature scored
                  </CardTitle>
                  <CardDescription>
                    The table view the ranked chart owes its reader — every value, no colour
                    required.
                  </CardDescription>
                </CardHeader>
                <CardContent className="px-0 pb-0">
                  <PsiTable snapshot={latest} />
                </CardContent>
              </Card>
            </ErrorBoundary>
          </>
        )}

        <ErrorBoundary label="Model registry">
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Database className="size-4 text-[var(--info)]" aria-hidden="true" />
                Model registry
              </CardTitle>
              <CardDescription>
                Versions, thresholds, and the decisions each one is responsible for.
                {registry.data ? (
                  <>
                    {' '}
                    Serving <span className="font-mono">{registry.data.serving}</span>
                    {latest ? <> · snapshot {sinceNow(latest.computed_at)}</> : null}
                  </>
                ) : null}
              </CardDescription>
            </CardHeader>
            <CardContent>
              {registry.isPending ? (
                <LoadingRows rows={3} />
              ) : registry.error || !registry.data ? (
                <ErrorState error={registry.error} label="Could not load the registry" />
              ) : (
                <RegistryTable
                  serving={registry.data.serving}
                  versions={registry.data.versions}
                />
              )}
            </CardContent>
          </Card>
        </ErrorBoundary>
      </div>
    </ScreenBody>
  )
}
