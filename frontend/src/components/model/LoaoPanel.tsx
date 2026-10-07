/**
 * The leave-one-attack-out table — the strongest evidence in the project.
 *
 * An entire attack family is removed from supervised training, Stage 1 is
 * refitted without it, the autoencoder is left untouched because it never saw
 * attacks anyway, and the full fusion pipeline runs on a test set containing
 * that family. What is left is a measurement of the claim this project makes:
 * detection of attack traffic it was never trained on.
 *
 * The Missed column is not an embarrassment to be trimmed. A table with honest
 * misses reads as engineering; a table of 99s reads as a bug.
 *
 * On the shape of the data: `GET /metrics/model` types `loao` as a free-form
 * object, because the artifact's layout belongs to `training/loao.py` rather
 * than to the wire contract. So this is the one file in the frontend that
 * describes a shape the generated types do not pin, and it reads defensively --
 * every field is checked rather than asserted, and a fold missing its headline
 * is skipped instead of rendering `undefined`.
 */
import { useState } from 'react'

import type { ModelMetrics } from '@/api/types'
import { heatFill, ScaleKey } from '@/components/charts/Chart'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { FAMILY_LABEL, type FAMILIES } from '@/lib/alerts'
import { count, dateTime, decimal, percent } from '@/lib/format'
import { cn } from '@/lib/utils'

interface Headline {
  family: string
  support: number
  stage1_caught: number
  stage1_named: number
  stage1_named_rate: number
  stage1_recall: number
  stage2_caught: number
  stage2_recall: number
  total_recall: number
  missed: number
  miss_rate: number
  stage2_pr_auc: number | null
}

interface Fold {
  held_out: string | null
  refitted: boolean
  reuse_reason: string | null
  classes: string[]
  headline: Headline | null
  benign: { fpr?: number; alerts_per_analyst_hour?: number } | null
}

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value)

const num = (source: Record<string, unknown>, key: string): number =>
  typeof source[key] === 'number' ? (source[key] as number) : 0

function readHeadline(value: unknown): Headline | null {
  if (!isRecord(value) || typeof value.family !== 'string') return null
  return {
    family: value.family,
    support: num(value, 'support'),
    stage1_caught: num(value, 'stage1_caught'),
    stage1_named: num(value, 'stage1_named'),
    stage1_named_rate: num(value, 'stage1_named_rate'),
    stage1_recall: num(value, 'stage1_recall'),
    stage2_caught: num(value, 'stage2_caught'),
    stage2_recall: num(value, 'stage2_recall'),
    total_recall: num(value, 'total_recall'),
    missed: num(value, 'missed'),
    miss_rate: num(value, 'miss_rate'),
    stage2_pr_auc: typeof value.stage2_pr_auc === 'number' ? value.stage2_pr_auc : null,
  }
}

function readFolds(loao: ModelMetrics['loao']): Fold[] {
  const raw = isRecord(loao) ? loao.folds : null
  if (!Array.isArray(raw)) return []

  return raw.filter(isRecord).map((fold) => ({
    held_out: typeof fold.held_out === 'string' ? fold.held_out : null,
    refitted: fold.refitted === true,
    reuse_reason: typeof fold.reuse_reason === 'string' ? fold.reuse_reason : null,
    classes: Array.isArray(fold.classes) ? fold.classes.filter((c): c is string => typeof c === 'string') : [],
    headline: readHeadline(fold.headline),
    benign: isRecord(fold.benign) ? (fold.benign as Fold['benign']) : null,
  }))
}

const familyLabel = (name: string): string =>
  FAMILY_LABEL[name as (typeof FAMILIES)[number]] ?? name

/** A recall cell: the number, with a bar behind it so a column scans as a shape. */
function RecallCell({
  value,
  tone,
  caught,
}: {
  value: number
  tone: string
  caught?: number
}) {
  return (
    <td className="px-2 py-2 text-right align-middle">
      <div className="flex items-center justify-end gap-2">
        {caught !== undefined ? (
          <span className="text-muted-foreground tabular font-mono text-[11px]">
            {count(caught)}
          </span>
        ) : null}
        <span className="tabular w-12 font-mono">{percent(value, 1)}</span>
        <span
          aria-hidden="true"
          className="h-1.5 w-12 shrink-0 overflow-hidden rounded-full bg-[var(--color-muted)]"
        >
          <span
            className="block h-full rounded-full"
            style={{ width: Math.max(2, value * 100) + '%', backgroundColor: tone }}
          />
        </span>
      </div>
    </td>
  )
}

export function LoaoPanel({ metrics }: { metrics: ModelMetrics }) {
  const [showStage2Alone, setShowStage2Alone] = useState(false)

  const folds = readFolds(metrics.loao)
  const loao = isRecord(metrics.loao) ? metrics.loao : {}
  const champion = isRecord(loao.champion) ? loao.champion : {}
  const stage2Alone = isRecord(loao.stage2_alone) ? loao.stage2_alone : {}
  const measuredAt = typeof loao.measured_at === 'string' ? loao.measured_at : null

  const rows = folds.filter((fold) => fold.headline !== null)

  if (rows.length === 0) {
    return (
      <p className="text-muted-foreground text-sm">
        No leave-one-attack-out results are loaded. Run the Phase 4 measurement to produce them —
        an empty table here would read as a model that caught nothing rather than one that has not
        been measured this way.
      </p>
    )
  }

  const stage2Families = isRecord(stage2Alone.families) ? stage2Alone.families : {}

  // The row that carries the claim: a family Stage 1 was refitted without and
  // then caught none of, ranked by what Stage 2 surfaced anyway. Read off the
  // measured folds -- if no fold qualifies, there is no hero, rather than a
  // flattering one picked by hand.
  const hero = rows
    .map((fold) => fold.headline as Headline)
    .filter((headline) => headline.stage1_recall < 0.01 && headline.support >= 100)
    .sort((a, b) => b.stage2_recall - a.stage2_recall)[0]

  return (
    <div>
      {hero ? (
        <div className="mb-6 grid gap-3 rounded-lg border border-[color-mix(in_oklab,var(--novel)_30%,transparent)] bg-[color-mix(in_oklab,var(--novel)_6%,transparent)] p-4 sm:grid-cols-[minmax(0,1.6fr)_repeat(3,minmax(0,1fr))]">
          <div className="min-w-0">
            <p className="text-[10px] font-semibold tracking-wider text-[var(--novel)] uppercase">
              The headline
            </p>
            <p className="text-foreground-strong mt-1 text-sm leading-relaxed">
              Stage 1 was refitted with every {familyLabel(hero.family)} row removed, and named none
              of them. The benign-only autoencoder surfaced{' '}
              <span className="font-semibold text-[var(--novel)]">
                {percent(hero.stage2_recall, 1)}
              </span>{' '}
              of the family anyway.
            </p>
          </div>
          {[
            { label: 'Rows held out', value: count(hero.support), tone: 'var(--foreground-strong)' },
            {
              label: 'Stage 2 surfaced',
              value: percent(hero.stage2_recall, 1),
              tone: 'var(--novel)',
            },
            { label: 'Missed', value: percent(hero.miss_rate, 1), tone: 'var(--critical)' },
          ].map((figure) => (
            <div key={figure.label} className="bg-card border-border rounded-lg border px-3.5 py-3">
              <p className="text-subtle-foreground text-[10px] tracking-wider uppercase">
                {figure.label}
              </p>
              <p className="mt-1 font-mono text-xl font-bold" style={{ color: figure.tone }}>
                {figure.value}
              </p>
            </div>
          ))}
        </div>
      ) : null}

      <div className="mb-5 max-w-3xl space-y-3 text-sm leading-relaxed">
        <p>
          For each row below, that attack family was removed from Stage 1&rsquo;s training set and
          the classifier was refitted without it. The autoencoder was left exactly as it is,
          because it never saw attack traffic in the first place. Then the whole fusion pipeline ran
          on a split containing the held-out family.
        </p>
        <p className="text-muted-foreground">
          <span className="text-foreground font-medium">Named correctly</span> is the share of
          Stage 1&rsquo;s catches that carried the right family label. Under hold-out it is zero by
          construction, and it is here so a recall figure cannot be mistaken for classification:
          Stage 1 can score a flow as suspicious without having any name for it.
        </p>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full min-w-[46rem] text-sm">
          <thead>
            <tr className="text-muted-foreground border-border bg-inset border-b">
              <th className="py-2.5 pr-3 pl-3 text-left font-medium">Held-out family</th>
              <th className="px-2 py-2 text-right font-medium">Rows</th>
              <th className="px-2 py-2 text-right font-medium">Stage 1</th>
              <th className="px-2 py-2 text-right font-medium">Stage 2</th>
              <th className="px-2 py-2 text-right font-medium">Total caught</th>
              <th className="px-2 py-2 text-right font-medium">Missed</th>
              <th className="px-2 py-2 text-right font-medium">Named correctly</th>
            </tr>
          </thead>
          <tbody className="divide-divider divide-y">
            {rows.map((fold) => {
              const headline = fold.headline as Headline
              return (
                <tr key={fold.held_out ?? headline.family} className="hover:bg-hover">
                  <td className="py-2.5 pr-3 pl-3">
                    <div className="flex items-center gap-2">
                      <span className="font-medium">{familyLabel(headline.family)}</span>
                      {!fold.refitted ? (
                        <Badge
                          variant="neutral"
                          title={
                            fold.reuse_reason ??
                            'The temporal split already holds this family out, so no refit was needed.'
                          }
                        >
                          already held out
                        </Badge>
                      ) : null}
                    </div>
                  </td>
                  <td className="tabular text-muted-foreground px-2 py-2 text-right font-mono">
                    {count(headline.support)}
                  </td>
                  <RecallCell
                    value={headline.stage1_recall}
                    tone="var(--series-known)"
                    caught={headline.stage1_caught}
                  />
                  <RecallCell
                    value={headline.stage2_recall}
                    tone="var(--series-novel)"
                    caught={headline.stage2_caught}
                  />
                  <RecallCell value={headline.total_recall} tone="var(--series-benign)" />
                  <td
                    className="tabular px-2 py-2 text-right font-mono"
                    style={{ backgroundColor: heatFill(headline.miss_rate, 'var(--series-attack)') }}
                    title={percent(headline.miss_rate, 1) + ' of this family went undetected'}
                  >
                    {count(headline.missed)}
                  </td>
                  <td
                    className={cn(
                      'tabular px-2 py-2 text-right font-mono',
                      headline.stage1_named_rate === 0 && 'text-muted-foreground',
                    )}
                  >
                    {percent(headline.stage1_named_rate, 0)}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
        <ScaleKey
          label="Missed"
          from="none"
          max="all"
          color="var(--series-attack)"
        />
        <Button variant="ghost" size="sm" onClick={() => setShowStage2Alone(!showStage2Alone)}>
          {showStage2Alone ? 'Hide' : 'Show'} Stage 2 measured on its own
        </Button>
      </div>

      {/*
       * Stage 2 alone, at both thresholds. Worth having behind a toggle rather
       * than on the main table: the fused figure is what the dashboard serves,
       * and the standalone figure at the budget threshold is the sobering
       * version -- the same detector, cut to a volume a SOC can actually read,
       * catches far less. Hiding that would be the dishonest choice; leading
       * with it would be a different chart.
       */}
      {showStage2Alone ? (
        <div className="border-border mt-4 overflow-x-auto rounded-md border">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-muted-foreground border-border border-b">
                <th className="px-3 py-2 text-left font-medium">Family</th>
                <th className="px-3 py-2 text-right font-medium">
                  Recall at the shipped threshold
                </th>
                <th className="px-3 py-2 text-right font-medium">
                  Recall cut to the alert budget
                </th>
              </tr>
            </thead>
            <tbody className="divide-border divide-y">
              {Object.entries(stage2Families)
                .filter((entry): entry is [string, Record<string, unknown>] => isRecord(entry[1]))
                .map(([family, values]) => (
                  <tr key={family}>
                    <td className="px-3 py-1.5">{familyLabel(family)}</td>
                    <td className="tabular px-3 py-1.5 text-right font-mono">
                      {percent(num(values, 'recall'), 1)}
                    </td>
                    <td className="tabular text-muted-foreground px-3 py-1.5 text-right font-mono">
                      {percent(num(values, 'recall_at_budget'), 1)}
                    </td>
                  </tr>
                ))}
            </tbody>
          </table>
          <p className="text-muted-foreground px-3 py-2 text-[11px] leading-relaxed">
            Benign false-positive rate: {percent(num(stage2Alone, 'benign_fpr'), 2)} at the shipped
            threshold, {percent(num(stage2Alone, 'benign_fpr_at_budget'), 2)} cut to the budget.
            That is the trade the threshold control on the Live screen moves.
          </p>
        </div>
      ) : null}

      <dl className="text-muted-foreground mt-5 flex flex-wrap gap-x-6 gap-y-1 text-xs">
        {typeof champion.version === 'string' ? (
          <div className="flex gap-1.5">
            <dt>Champion</dt>
            <dd className="text-foreground font-mono">{champion.version}</dd>
          </div>
        ) : null}
        {typeof champion.tau_sup === 'number' ? (
          <div className="flex gap-1.5">
            <dt>Stage 1 threshold</dt>
            <dd className="tabular text-foreground font-mono">
              {decimal(champion.tau_sup, 4)}
            </dd>
          </div>
        ) : null}
        {typeof champion.tau_anom === 'number' ? (
          <div className="flex gap-1.5">
            <dt>Stage 2 threshold</dt>
            <dd className="tabular text-foreground font-mono">
              {decimal(champion.tau_anom, 4)}
            </dd>
          </div>
        ) : null}
        {measuredAt ? (
          <div className="flex gap-1.5">
            <dt>Measured</dt>
            <dd className="text-foreground">{dateTime(measuredAt)}</dd>
          </div>
        ) : null}
      </dl>

      <p className="text-muted-foreground mt-4 max-w-3xl text-xs leading-relaxed">
        What this does and does not prove: it measures generalisation to held-out <em>known</em>{' '}
        families, which is a proxy for genuinely novel attacks rather than proof of them. The
        families here existed in 2017 lab traffic and were labelled by somebody. That is still a
        far stronger claim than an unmeasured assertion, and the misses above are part of it.
      </p>
    </div>
  )
}
