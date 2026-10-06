/**
 * Screen 6 — the feedback loop.
 *
 * A small screen with a large narrative payoff: it is where analyst judgement
 * stops being a column in a table and becomes next week's training data. It is
 * also the clearest signal that this is a system rather than a script.
 *
 * The counts are real and come from `GET /analytics/feedback`. The retrain button
 * is not live, and it says so: the challenger pipeline arrives in Phase 7 and
 * the backend reports which phase in its own response, so this screen does not
 * carry a copy of the roadmap that can go stale.
 */
import { GitCompare, Hourglass, ThumbsDown, ThumbsUp } from 'lucide-react'

import { useFeedbackLoop } from '@/api/queries'
import type { FeedbackLoop as FeedbackLoopData } from '@/api/types'
import { ScreenBody } from '@/components/AppShell'
import { EmptyState, ErrorState, LoadingRows } from '@/components/States'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { count, duration, percent } from '@/lib/format'

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

  if (isPending) {
    return (
      <ScreenBody title="Feedback">
        <LoadingRows rows={6} />
      </ScreenBody>
    )
  }

  if (error || !data) {
    return (
      <ScreenBody title="Feedback">
        <ErrorState error={error} label="Could not load the feedback counts" />
      </ScreenBody>
    )
  }

  return (
    <ScreenBody
      title="Feedback"
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
                <Button disabled={!data.retrain_available}>
                  <GitCompare aria-hidden="true" />
                  Retrain with {count(data.labels_pending_retrain)} new label
                  {data.labels_pending_retrain === 1 ? '' : 's'}
                </Button>
                {!data.retrain_available ? (
                  <p className="text-muted-foreground max-w-xs text-xs leading-relaxed">
                    {data.retrain_phase} ships the challenger pipeline. Until then this button is
                    disabled rather than wired to something that would look like it worked.
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
          </CardContent>
        </Card>
      </div>
    </ScreenBody>
  )
}
