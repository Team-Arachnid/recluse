/**
 * Screen 6 — the feedback loop.
 *
 * A small screen with a large narrative payoff: it is where analyst judgement
 * stops being a column in a table and becomes next week's training data. It is
 * also the clearest signal that this is a system rather than a script.
 *
 * The counts come from `GET /analytics/feedback` and the history from
 * `GET /retrain`. The button queues a run rather than performing one: the endpoint
 * writes a row and returns 202, and `python -m training.retrain` claims it.
 * Fitting a model inside a request handler would put a multi-minute job on the
 * event loop that also serves the alert stream.
 *
 * The declined runs in the history are the point of keeping it. A gate that has
 * never turned anything down is a gate nobody has evidence for.
 */
import { Biohazard, GitCompare, Hourglass, ThumbsDown, ThumbsUp } from 'lucide-react'

import { useFeedbackLoop, useRequestRetrain, useRetrainRuns } from '@/api/queries'
import type { FeedbackLoop as FeedbackLoopData, RetrainRun, Stage2RefitSummary } from '@/api/types'
import { ScreenBody } from '@/components/AppShell'
import { ErrorBoundary } from '@/components/ErrorBoundary'
import { EmptyState, ErrorState, LoadingRows } from '@/components/States'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { count, dateTime, decimal, duration, percent, sinceNow } from '@/lib/format'

type BadgeVariant = 'ok' | 'info' | 'medium' | 'critical' | 'neutral'

const RETRAIN_VARIANT: Record<RetrainRun['status'], BadgeVariant> = {
  requested: 'medium',
  running: 'info',
  completed: 'ok',
  failed: 'critical',
  cancelled: 'neutral',
}

/**
 * What the run did to the autoencoder's benign baseline.
 *
 * "Left alone" is the guard working, and it is shown as such rather than hidden:
 * on a replay of CICIDS2017 every confirmed false positive is attributed to the
 * one documented attacker address, so the per-host cap admits a single row and
 * the floor refuses the pool. The same guard on live capture is what stops one
 * host that can get its traffic waved through from becoming the baseline.
 */
function Stage2Outcome({ stage2 }: { stage2: Stage2RefitSummary }) {
  const attempted = stage2.attempted
  return (
    <div className="mt-3 rounded-lg border border-[color-mix(in_oklab,var(--novel)_25%,transparent)] bg-[color-mix(in_oklab,var(--novel)_5%,transparent)] px-3 py-2.5">
      <div className="flex flex-wrap items-center gap-2">
        <Biohazard className="size-3.5 text-[var(--novel)]" aria-hidden="true" />
        <span className="text-foreground-strong text-[11px] font-semibold tracking-wider uppercase">
          Stage 2 · benign baseline
        </span>
        <Badge variant={!attempted ? 'neutral' : stage2.promoted ? 'ok' : 'novel'}>
          {!attempted ? 'left alone' : stage2.promoted ? 'refit promoted' : 'refit declined'}
        </Badge>
        <span className="text-subtle-foreground font-mono text-[11px]">
          pool {count(stage2.pool_admitted)} of {count(stage2.pool_candidates)} admitted ·{' '}
          {count(stage2.pool_hosts)} host{stage2.pool_hosts === 1 ? '' : 's'} ·{' '}
          {count(stage2.pool_refused_by_cap)} refused by the cap
        </span>
      </div>
      {attempted ? (
        <dl className="text-muted-foreground mt-2 flex flex-wrap gap-x-6 gap-y-1 font-mono text-[11px]">
          <div className="flex gap-1.5">
            <dt>gate PR-AUC</dt>
            <dd className="text-foreground">
              {decimal(stage2.champion_pr_auc, 4)} → {decimal(stage2.challenger_pr_auc, 4)}
            </dd>
          </div>
          <div className="flex gap-1.5">
            <dt>confirmed-benign still flagged</dt>
            <dd className="text-foreground">
              {percent(stage2.champion_pool_fpr, 1)} → {percent(stage2.challenger_pool_fpr, 1)}
            </dd>
          </div>
          <div className="flex gap-1.5">
            <dt>attack recall</dt>
            <dd className="text-foreground">
              {percent(stage2.champion_attack_recall, 1)} →{' '}
              {percent(stage2.challenger_attack_recall, 1)}
            </dd>
          </div>
        </dl>
      ) : null}
      <p className="text-muted-foreground mt-1.5 max-w-3xl text-xs leading-relaxed">
        {stage2.decision}
      </p>
    </div>
  )
}

/**
 * The retrain history, including the runs that were declined.
 *
 * Those are the point of keeping it. A gate that has never turned anything down
 * is a gate nobody has evidence for, and a declined run carries the two numbers
 * that show why: the champion's score and the challenger's, both measured on the
 * same held-out split inside the same run.
 */
function RetrainHistory() {
  const { data, error, isPending } = useRetrainRuns()

  if (isPending) return <LoadingRows rows={3} />
  if (error || !data) {
    return <ErrorState error={error} label="Could not load the retrain history" />
  }

  if (data.runs.length === 0) {
    return (
      <EmptyState
        title="No retrain has been requested"
        hint={data.worker_hint}
        icon={<GitCompare className="size-5" />}
      />
    )
  }

  return (
    <div>
      <ul className="divide-border divide-y">
        {data.runs.map((run) => {
          const delta =
            run.challenger_pr_auc !== null && run.champion_pr_auc !== null
              ? run.challenger_pr_auc - run.champion_pr_auc
              : null
          return (
            <li key={run.id} className="py-3 first:pt-0 last:pb-0">
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant={RETRAIN_VARIANT[run.status]}>{run.status}</Badge>
                {run.status === 'completed' ? (
                  <Badge variant={run.promoted ? 'ok' : 'neutral'}>
                    {run.promoted ? 'promoted' : 'champion kept'}
                  </Badge>
                ) : null}
                <span className="text-muted-foreground text-xs">
                  {count(run.labels_consumed)} label
                  {run.labels_consumed === 1 ? '' : 's'} consumed,{' '}
                  {count(run.false_positives_consumed)} of them false positives
                </span>
                <span className="text-muted-foreground ml-auto text-xs">
                  requested {sinceNow(run.requested_at)}
                  {run.finished_at ? ', finished ' + dateTime(run.finished_at) : ''}
                </span>
              </div>

              {delta !== null ? (
                <dl className="text-muted-foreground mt-2 flex flex-wrap gap-x-6 gap-y-1 text-xs">
                  <div className="flex gap-1.5">
                    <dt>Gate</dt>
                    <dd className="text-foreground">{run.held_out_split} PR-AUC</dd>
                  </div>
                  <div className="flex gap-1.5">
                    <dt>Champion</dt>
                    <dd className="tabular text-foreground font-mono">
                      {decimal(run.champion_pr_auc, 4)}
                    </dd>
                  </div>
                  <div className="flex gap-1.5">
                    <dt>Challenger</dt>
                    <dd className="tabular text-foreground font-mono">
                      {decimal(run.challenger_pr_auc, 4)}
                    </dd>
                  </div>
                  <div className="flex gap-1.5">
                    <dt>Delta</dt>
                    <dd
                      className="tabular font-mono"
                      style={{
                        color: delta > 0 ? 'var(--series-benign)' : 'var(--series-attack)',
                      }}
                    >
                      {delta >= 0 ? '+' : ''}
                      {decimal(delta, 4)}
                    </dd>
                  </div>
                </dl>
              ) : null}

              {run.decision ? (
                <p className="text-muted-foreground mt-2 max-w-3xl text-xs leading-relaxed">
                  {run.decision}
                </p>
              ) : null}
              {run.stage2 ? <Stage2Outcome stage2={run.stage2} /> : null}
              {run.error ? (
                <p className="mt-2 font-mono text-xs break-words text-[var(--critical)]">
                  {run.error}
                </p>
              ) : null}
            </li>
          )
        })}
      </ul>

      <p className="text-muted-foreground mt-4 max-w-3xl text-xs leading-relaxed">
        {data.worker_hint}
      </p>
    </div>
  )
}

/**
 * The TP/FP split as one divided bar.
 *
 * The diverging pair, not green against red: green and red sit 3.0 apart in
 * OKLab under simulated deuteranopia, against a floor of 8, so the two
 * halves of the most important bar on this screen would be the same colour for
 * a colourblind reader. Cool against warm separates at 21.8 and means the same
 * thing — the analyst confirmed the model, or overruled it. Both halves also
 * carry an icon and a word, so the colour is never the only channel.
 */
function AgreementBar({ data }: { data: FeedbackLoopData }) {
  const decided = data.true_positives + data.false_positives
  const total = decided + data.unsure

  if (total === 0) {
    return (
      <EmptyState
        title="No judgements yet"
        hint="Open an alert from the queue and record a verdict. Every one of them is a label the next model trains on."
        icon={<Hourglass className="size-5" />}
      />
    )
  }

  const segments = [
    {
      key: 'TP',
      label: 'Confirmed',
      sublabel: 'the model was right',
      value: data.true_positives,
      color: 'var(--series-benign)',
      icon: <ThumbsUp className="size-3.5" aria-hidden="true" />,
    },
    {
      key: 'FP',
      label: 'Overruled',
      sublabel: 'the model was wrong',
      value: data.false_positives,
      color: 'var(--series-attack)',
      icon: <ThumbsDown className="size-3.5" aria-hidden="true" />,
    },
    {
      key: 'UNSURE',
      label: 'Undecided',
      sublabel: 'not enough to judge',
      value: data.unsure,
      color: 'var(--series-neutral)',
      icon: <Hourglass className="size-3.5" aria-hidden="true" />,
    },
  ].filter((segment) => segment.value > 0)

  return (
    <div>
      {/* A 2px surface gap between segments rather than a border around each. */}
      <div className="flex h-7 w-full gap-[2px] overflow-hidden rounded-md">
        {segments.map((segment) => (
          <div
            key={segment.key}
            className="h-full"
            style={{
              width: (segment.value / total) * 100 + '%',
              backgroundColor: segment.color,
            }}
            title={segment.label + ': ' + segment.value}
          />
        ))}
      </div>

      <ul className="mt-4 grid gap-3 sm:grid-cols-3">
        {segments.map((segment) => (
          <li key={segment.key} className="flex items-start gap-2.5">
            <span
              className="mt-0.5 shrink-0"
              style={{ color: segment.color }}
              aria-hidden="true"
            >
              {segment.icon}
            </span>
            <div className="min-w-0">
              <p className="text-base leading-tight font-semibold">
                {count(segment.value)}{' '}
                <span className="text-muted-foreground text-xs font-normal">
                  {percent(segment.value / total, 0)}
                </span>
              </p>
              <p className="text-sm">{segment.label}</p>
              <p className="text-muted-foreground text-xs">{segment.sublabel}</p>
            </div>
          </li>
        ))}
      </ul>

      {data.disagreement_rate !== null ? (
        <p className="text-muted-foreground mt-5 max-w-2xl text-xs leading-relaxed">
          Disagreement rate {percent(data.disagreement_rate, 1)} — the share of the alerts an
          analyst actually decided where they overruled the model. Undecided verdicts are outside
          that denominator: not knowing is not the same as the model being wrong, and counting it as
          wrong would punish honesty.
        </p>
      ) : null}
    </div>
  )
}

export function FeedbackLoopScreen() {
  const { data, error, isPending } = useFeedbackLoop()
  const runs = useRetrainRuns()
  const request = useRequestRetrain()

  if (isPending) {
    return (
      <ScreenBody title="Feedback loop">
        <LoadingRows rows={6} />
      </ScreenBody>
    )
  }

  if (error || !data) {
    return (
      <ScreenBody title="Feedback loop">
        <ErrorState error={error} label="Could not load the feedback counts" />
      </ScreenBody>
    )
  }

  return (
    <ScreenBody
      title="Feedback loop"
      lede="Every verdict an analyst records is a label. This is where those labels queue up for the next model, and where somebody decides it is worth retraining."
    >
      <div className="space-y-6">
        <Card>
          <CardHeader>
            <CardTitle>Labels waiting for a retrain</CardTitle>
            <CardDescription>
              Verdicts no retraining run has consumed yet. The <code>consumed_at</code> column
              exists for this question, which is why &ldquo;since the last retrain&rdquo; needs no
              second table.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="flex flex-wrap items-end justify-between gap-6">
              <div>
                <p className="text-4xl leading-none font-semibold">
                  {count(data.labels_pending_retrain)}
                </p>
                <p className="text-muted-foreground mt-2 text-sm">
                  new label{data.labels_pending_retrain === 1 ? '' : 's'} since the last retrain
                  {data.labels_consumed > 0
                    ? ' · ' + count(data.labels_consumed) + ' already consumed'
                    : ''}
                </p>
              </div>

              <div className="flex flex-col items-start gap-2">
                <Button
                  onClick={() => request.mutate({ requested_by: 'dashboard' })}
                  disabled={
                    request.isPending ||
                    data.labels_pending_retrain === 0 ||
                    (runs.data?.pending ?? 0) > 0
                  }
                >
                  <GitCompare aria-hidden="true" />
                  Retrain with {count(data.labels_pending_retrain)} new label
                  {data.labels_pending_retrain === 1 ? '' : 's'}
                </Button>

                {/*
                 * The button requests a run; it does not perform one. Fitting a
                 * model inside a request handler would put a multi-minute job on
                 * the event loop that also serves the alert stream -- so the
                 * endpoint writes a row, returns 202, and the offline pipeline
                 * claims it. Saying that is better than a spinner implying the
                 * work is happening inside this click.
                 */}
                <p className="text-muted-foreground max-w-sm text-xs leading-relaxed">
                  {(runs.data?.pending ?? 0) > 0
                    ? 'A run is already queued. The worker takes one at a time: two concurrent retrains would consume the same labels and race to publish a champion.'
                    : data.labels_pending_retrain === 0
                      ? 'Nothing new to learn from. A challenger fitted on the same data as the champion differs from it only by random seed.'
                      : 'This queues a run. The fit happens offline — the API never trains a model inside a request.'}
                </p>

                {request.isError ? (
                  <p className="max-w-sm text-xs text-[var(--critical)]">
                    {request.error instanceof Error
                      ? request.error.message
                      : 'Could not queue the run'}
                  </p>
                ) : null}
                {request.isSuccess ? (
                  <p className="text-xs text-[var(--ok)]">
                    Queued as run {request.data.id}. A worker will pick it up.
                  </p>
                ) : null}
              </div>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Where the analyst and the model disagree</CardTitle>
            <CardDescription>
              The split of recorded verdicts, and how long a judgement takes to arrive.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <AgreementBar data={data} />

            <dl className="border-border mt-6 grid gap-x-8 gap-y-4 border-t pt-5 sm:grid-cols-3">
              <div>
                <dd className="text-lg font-semibold">{count(data.judged_alerts)}</dd>
                <dt className="text-muted-foreground text-xs">
                  alerts judged, of {count(data.total_alerts)} raised —{' '}
                  {percent(data.judged_share, 1)} coverage
                </dt>
              </div>
              <div>
                <dd className="text-lg font-semibold">
                  {data.mean_seconds_to_verdict === null
                    ? '—'
                    : duration(data.mean_seconds_to_verdict)}
                </dd>
                <dt className="text-muted-foreground text-xs">
                  mean time from detection to verdict
                </dt>
              </div>
              <div>
                <dd className="text-lg font-semibold">{count(data.labels_total)}</dd>
                <dt className="text-muted-foreground text-xs">
                  verdicts recorded in total, across every model version
                </dt>
              </div>
            </dl>
          </CardContent>
        </Card>

        {data.by_model_version.length > 0 ? (
          <Card>
            <CardHeader>
              <CardTitle>Labels by the model version they judged</CardTitle>
              <CardDescription>
                A verdict is attributed to the model that produced the alert, captured at verdict
                time. A label attributed to the wrong version is worse than no label — it would
                train the next model on a correction to a decision it never made.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-muted-foreground border-border border-b text-xs">
                    <th className="py-2 pr-3 text-left font-medium">Model version</th>
                    <th className="px-2 py-2 text-right font-medium">Verdicts</th>
                    <th className="px-2 py-2 text-right font-medium">Confirmed</th>
                    <th className="px-2 py-2 text-right font-medium">Overruled</th>
                    <th className="py-2 pl-2 text-right font-medium">Undecided</th>
                  </tr>
                </thead>
                <tbody className="divide-border divide-y">
                  {data.by_model_version.map((row) => (
                    <tr key={row.model_version ?? 'unattributed'}>
                      <td className="py-2 pr-3 font-mono text-xs">
                        <span className="flex items-center gap-2">
                          {row.model_version ?? 'unattributed'}
                          {row.model_version === data.serving_model_version ? (
                            <Badge variant="ok">serving</Badge>
                          ) : null}
                        </span>
                      </td>
                      <td className="tabular px-2 py-2 text-right font-mono">
                        {count(row.verdicts)}
                      </td>
                      <td className="tabular px-2 py-2 text-right font-mono">
                        {count(row.true_positives)}
                      </td>
                      <td className="tabular px-2 py-2 text-right font-mono">
                        {count(row.false_positives)}
                      </td>
                      <td className="tabular text-muted-foreground py-2 pl-2 text-right font-mono">
                        {count(row.unsure)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </CardContent>
          </Card>
        ) : null}

        <ErrorBoundary label="Retrain history">
          <Card>
            <CardHeader>
              <CardTitle>Champion against challenger</CardTitle>
              <CardDescription>
                Every run, including the ones that were declined — those are the evidence the gate
                works. Both scores are measured on the same held-out split inside the same run.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <RetrainHistory />
            </CardContent>
          </Card>
        </ErrorBoundary>

        {/*
         * This belongs on the screen, not buried in a job: it is the reason the
         * loop is safe to close at all.
         */}
        <Card>
          <CardHeader>
            <CardTitle>Why the benign refit pool is guarded</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="max-w-3xl text-sm leading-relaxed">
              Retraining the anomaly detector on recent &ldquo;normal&rdquo; traffic is how it keeps
              up with a moving baseline, and it is also the obvious way to attack it. A row only
              enters the benign refit pool after an analyst has confirmed it as a false positive,
              and no single source host may contribute more than a capped share of the pool. Without
              both guards, anyone who can generate enough traffic can teach the baseline that their
              traffic is normal.
            </p>
            <p className="text-muted-foreground mt-3 max-w-3xl text-xs leading-relaxed">
              A pool that clears both guards refits the autoencoder: a few epochs from the serving
              weights, mixed with a sample of the original benign set so the old baseline is moved
              rather than replaced. The challenger is scored against the champion on one held-out
              set — the validation day plus a slice of the pool withheld from the fit — and only a
              better score replaces it. Each run below says which way that went, and why.
            </p>
          </CardContent>
        </Card>
      </div>
    </ScreenBody>
  )
}
