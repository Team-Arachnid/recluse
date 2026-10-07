/**
 * Alert detail: why / what-it-is / how-to-fix, in that order.
 *
 * The order is the point. An analyst has about four seconds of attention before
 * deciding what to do, and the first thing they need is not a label -- it is a
 * reason. An alert with a score and no reason is an alert an analyst learns to
 * ignore.
 *
 * Two panels refuse to guess. For an `UNCLASSIFIED_ANOMALY` there is no
 * technique and no playbook, and the drawer says so: Stage 2 fired *because*
 * Stage 1 could not name the traffic. Inventing either would be fabrication
 * dressed as helpfulness, and a wrong playbook does more damage than an honest
 * shrug -- the analyst follows it, loses the shift, and stops trusting the
 * playbooks that are right.
 *
 * There is no block button, and there is no endpoint behind one.
 */
import {
  ArrowRight,
  Biohazard,
  Check,
  CircleHelp,
  Cpu,
  Download,
  ExternalLink,
  Info,
} from 'lucide-react'
import { useState, type ReactNode } from 'react'

import {
  useAlert,
  useAnomalyHistogram,
  useModelMetrics,
  useRelatedAlerts,
  useSubmitVerdict,
} from '@/api/queries'
import type { AlertDetail, AlertSummary, AnomalyHistogram, RecommendedActions } from '@/api/types'
import { ClassBadge, SeverityMark, StatusPill } from '@/components/alert/AlertGlyphs'
import { ExplanationChart } from '@/components/alert/ExplanationChart'
import { ErrorState, LoadingRows } from '@/components/States'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Drawer } from '@/components/ui/drawer'
import {
  classLabel,
  isNovel,
  STAGE_LABEL,
  VERDICT_LABEL,
  VERDICT_VARIANT,
} from '@/lib/alerts'
import { clockTime, count, decimal, endpoint, percent, sinceNow } from '@/lib/format'
import { cn } from '@/lib/utils'

/** A numbered section. The numbers are real here: this is a fixed sequence of
 *  three questions, asked in one order, and the order carries the argument. */
function Section({
  step,
  heading,
  children,
}: {
  step: number
  heading: string
  children: ReactNode
}) {
  return (
    <section className="border-divider border-b px-5 py-5 last:border-b-0 sm:px-6">
      <div className="mb-3.5 flex items-center gap-2.5">
        <span
          aria-hidden="true"
          className="bg-raised border-raised-border text-muted-foreground flex size-5 items-center justify-center rounded border font-mono text-[10px] font-semibold"
        >
          {step}
        </span>
        <h3 className="text-foreground-strong text-xs font-semibold tracking-wider uppercase">
          {heading}
        </h3>
      </div>
      {children}
    </section>
  )
}

/** One cell of the flow summary well at the top of the drawer. */
function Fact({ term, children }: { term: string; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-subtle-foreground text-[10px] tracking-wider uppercase">{term}</dt>
      <dd className="text-foreground-strong mt-0.5 truncate font-mono text-xs font-semibold">
        {children}
      </dd>
    </div>
  )
}

/**
 * Where a reconstruction error sits in the benign validation distribution.
 *
 * Read off the persisted histogram bins rather than raw rows, which is what the
 * histogram exists for. A bin straddling the score counts as below it, so this
 * is a floor -- "at least the 99.7th percentile" -- which is the honest
 * direction to round for a number an analyst reads as "how unusual".
 */
function benignPercentile(score: number, histogram: AnomalyHistogram | undefined): number | null {
  const benign = histogram?.distributions.find((entry) => entry.name === 'validation_benign')
  if (!histogram || !benign || benign.rows === 0) return null
  let below = 0
  benign.counts.forEach((binCount, index) => {
    if (histogram.edges[index + 1] <= score) below += binCount
  })
  return below / benign.rows
}

/**
 * The two models, side by side: what each one said about this flow, against the
 * threshold it is held to.
 *
 * Shown for every alert, including the stage that did not fire -- "Stage 1 was
 * not confident enough to name this" is the reason an anomaly exists, and
 * "Stage 2 was never asked" is why a known attack carries no error.
 */
function ModelCards({ alert }: { alert: AlertDetail }) {
  const { data: metrics } = useModelMetrics()
  const { data: histogram } = useAnomalyHistogram()
  const novel = isNovel(alert)

  const tauSup = typeof metrics?.tau_sup === 'number' ? metrics.tau_sup : null
  const tauAnom = typeof histogram?.tau_anom === 'number' ? histogram.tau_anom : null
  const percentile =
    alert.anomaly_score !== null ? benignPercentile(alert.anomaly_score, histogram) : null

  const row = (label: string, value: ReactNode) => (
    <div className="border-divider flex items-baseline justify-between gap-3 border-b py-1.5 last:border-b-0">
      <span className="text-muted-foreground">{label}</span>
      <span className="text-foreground text-right font-mono">{value}</span>
    </div>
  )

  return (
    <div className="grid gap-3 sm:grid-cols-2">
      <div className="bg-inset border-border rounded-lg border p-3.5">
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <span className="flex size-7 items-center justify-center rounded-md border border-[color-mix(in_oklab,var(--ok)_35%,transparent)] bg-[color-mix(in_oklab,var(--ok)_12%,transparent)]">
              <Cpu className="size-3.5 text-[var(--ok)]" aria-hidden="true" />
            </span>
            <div>
              <p className="text-foreground-strong text-[11px] font-bold tracking-wider uppercase">
                Stage 1 · supervised
              </p>
              <p className="text-subtle-foreground text-[10px]">Names known families</p>
            </div>
          </div>
          <div className="text-right">
            <p className="font-mono text-sm font-bold text-[var(--ok)]">
              {alert.confidence === null ? '—' : percent(alert.confidence, 1)}
            </p>
            <p className="text-subtle-foreground text-[9px] tracking-wider uppercase">confidence</p>
          </div>
        </div>
        <div className="mt-2.5 text-xs">
          {row('Threshold τ_sup', tauSup === null ? '—' : decimal(tauSup, 4))}
          {row(
            'Verdict',
            novel ? (
              <span className="text-muted-foreground">below τ — passed on</span>
            ) : alert.family ? (
              classLabel(alert)
            ) : (
              '—'
            ),
          )}
        </div>
      </div>

      <div className="bg-inset border-border rounded-lg border p-3.5">
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <span className="flex size-7 items-center justify-center rounded-md border border-[color-mix(in_oklab,var(--novel)_35%,transparent)] bg-[color-mix(in_oklab,var(--novel)_12%,transparent)]">
              <Biohazard className="size-3.5 text-[var(--novel)]" aria-hidden="true" />
            </span>
            <div>
              <p className="text-foreground-strong text-[11px] font-bold tracking-wider uppercase">
                Stage 2 · autoencoder
              </p>
              <p className="text-subtle-foreground text-[10px]">Benign-only baseline</p>
            </div>
          </div>
          <div className="text-right">
            <p className="font-mono text-sm font-bold text-[var(--novel)]">
              {alert.anomaly_score === null ? '—' : decimal(alert.anomaly_score, 4)}
            </p>
            <p className="text-subtle-foreground text-[9px] tracking-wider uppercase">
              reconstruction error
            </p>
          </div>
        </div>
        <div className="mt-2.5 text-xs">
          {row('Threshold τ_anom', tauAnom === null ? '—' : decimal(tauAnom, 4))}
          {row(
            'Benign percentile',
            alert.anomaly_score === null ? (
              <span className="text-muted-foreground">not asked — Stage 1 named it</span>
            ) : percentile === null ? (
              '—'
            ) : (
              <span className="text-[var(--novel)]">≥ {percent(percentile, 2)}</span>
            ),
          )}
        </div>
      </div>
    </div>
  )
}

/**
 * The flow as it arrived, for the analyst who wants to check the model's work.
 *
 * `_provenance` is lifted out of the table and rendered as the caveat it is.
 * Two of the addresses above are derived from the published lab topology rather
 * than observed -- the release strips them before publication -- and the backend
 * ships the sentence saying so, so this component does not keep its own copy of
 * a fact that could drift.
 */
function RawFlow({ flow }: { flow: Record<string, unknown> }) {
  const [expanded, setExpanded] = useState(false)

  const provenance = flow._provenance as Record<string, string> | undefined
  const entries = Object.entries(flow)
    .filter(([key]) => key !== '_provenance')
    .sort(([a], [b]) => a.localeCompare(b))

  const shown = expanded ? entries : entries.slice(0, 12)

  return (
    <div className="mt-5">
      <div className="mb-2 flex items-baseline justify-between gap-3">
        <h4 className="text-muted-foreground text-[11px] font-semibold tracking-wider uppercase">
          Raw flow record
        </h4>
        {entries.length > 12 ? (
          <Button variant="ghost" size="sm" onClick={() => setExpanded(!expanded)}>
            {expanded ? 'Show fewer' : 'Show all ' + entries.length + ' features'}
          </Button>
        ) : null}
      </div>

      <dl className="border-border bg-inset grid grid-cols-1 gap-x-6 gap-y-1 rounded-lg border p-3 font-mono text-[11px] sm:grid-cols-2">
        {shown.map(([key, value]) => (
          <div key={key} className="flex items-baseline justify-between gap-3">
            <dt className="text-subtle-foreground truncate">{key}</dt>
            <dd className="tabular text-foreground shrink-0">
              {typeof value === 'number' ? decimal(value, 4) : String(value)}
            </dd>
          </div>
        ))}
      </dl>

      {provenance?.note ? (
        <p className="text-subtle-foreground mt-2 flex items-start gap-1.5 text-[11px] leading-relaxed">
          <Info className="mt-px size-3 shrink-0" aria-hidden="true" />
          {provenance.note}
        </p>
      ) : null}
    </div>
  )
}

/** Section 2 for a named family: the technique plus the sentence that saves a tab. */
function TechniquePanel({ actions }: { actions: RecommendedActions }) {
  const technique = actions.technique
  if (!technique) {
    return (
      <div className="flex items-start gap-3 rounded-lg border border-[color-mix(in_oklab,var(--novel)_30%,transparent)] bg-[color-mix(in_oklab,var(--novel)_8%,transparent)] p-3.5">
        <Biohazard className="mt-0.5 size-4 shrink-0 text-[var(--novel)]" aria-hidden="true" />
        <div>
          <p className="text-foreground-strong text-sm">
            Doesn&rsquo;t match a known technique — this is exactly what Stage 2 exists to catch.
          </p>
          <p className="text-muted-foreground mt-1.5 text-xs leading-relaxed">
            The classifier produced no family label for this flow, so there is no technique to
            name. The anomaly detector flagged it because it does not look like normal traffic.
          </p>
        </div>
      </div>
    )
  }

  return (
    <div className="bg-inset border-border rounded-lg border p-3.5">
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant="critical">{technique.technique_id}</Badge>
        <span className="text-foreground-strong text-sm font-medium">{technique.name}</span>
        <a
          href={technique.url}
          target="_blank"
          rel="noreferrer"
          className="text-subtle-foreground hover:text-foreground ml-auto inline-flex items-center gap-1 font-mono text-[11px]"
        >
          attack.mitre.org
          <ExternalLink className="size-3" aria-hidden="true" />
        </a>
      </div>
      {/* The plain-English line, so this is readable without opening a second
          tab mid-shift. */}
      <p className="text-foreground mt-2 text-sm leading-relaxed">{technique.means}</p>
    </div>
  )
}

/** Section 3: a checklist that can be scanned in three seconds, or the honest
 *  admission that there is nothing to scan. */
function PlaybookPanel({ actions }: { actions: RecommendedActions }) {
  if (!actions.has_playbook) {
    return (
      <div className="bg-inset border-border flex items-start gap-3 rounded-lg border p-3.5">
        <CircleHelp className="mt-0.5 size-4 shrink-0 text-[var(--medium)]" aria-hidden="true" />
        <div>
          <p className="text-foreground-strong text-sm">
            No playbook yet — escalate for manual investigation.
          </p>
          <p className="text-muted-foreground mt-1.5 text-xs leading-relaxed">{actions.summary}</p>
        </div>
      </div>
    )
  }

  return (
    <div>
      <p className="text-foreground text-sm leading-relaxed">{actions.summary}</p>
      <ul className="mt-3 space-y-1.5">
        {actions.actions.map((action) => (
          <li
            key={action}
            className="bg-inset border-border flex items-start gap-2.5 rounded-lg border px-3 py-2 text-sm"
          >
            <span className="mt-0.5 flex size-4 shrink-0 items-center justify-center rounded border border-[color-mix(in_oklab,var(--ok)_40%,transparent)] bg-[color-mix(in_oklab,var(--ok)_12%,transparent)]">
              <Check className="size-3 text-[var(--ok)]" aria-hidden="true" />
            </span>
            <span className="text-foreground leading-snug">{action}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}

/** Other alerts from the same source, so a scan-then-exploit sequence reads as
 *  one story instead of three disconnected rows. */
function HostContext({
  alert,
  related,
  onOpen,
}: {
  alert: AlertDetail
  related: AlertSummary[] | undefined
  onOpen: (id: number) => void
}) {
  if (!related) return null

  if (related.length === 0) {
    return (
      <p className="text-subtle-foreground mt-4 text-xs">
        No other alerts from <span className="text-foreground font-mono">{alert.src_ip}</span>{' '}
        inside 24 hours of this one.
      </p>
    )
  }

  return (
    <div className="mt-4">
      <h4 className="text-muted-foreground mb-2 text-[11px] font-semibold tracking-wider uppercase">
        {related.length} other alert{related.length === 1 ? '' : 's'} from{' '}
        <span className="text-foreground font-mono normal-case">{alert.src_ip}</span> within 24h
      </h4>
      <ul className="border-border divide-divider bg-inset divide-y overflow-hidden rounded-lg border">
        {related.slice(0, 6).map((row) => (
          <li key={row.id}>
            <button
              type="button"
              onClick={() => onOpen(row.id)}
              className="hover:bg-hover flex w-full cursor-pointer items-center gap-2.5 px-3 py-2 text-left text-xs"
            >
              <span className="tabular text-subtle-foreground shrink-0 font-mono">
                {clockTime(row.detected_at)}
              </span>
              <ClassBadge alert={row} className="shrink-0" />
              <span className="tabular text-muted-foreground min-w-0 flex-1 truncate font-mono">
                → {endpoint(row.dst_ip, row.dst_port)}
              </span>
              <ArrowRight className="size-3 shrink-0 opacity-40" aria-hidden="true" />
            </button>
          </li>
        ))}
      </ul>
    </div>
  )
}

/** Save the alert as the evidence an analyst attaches to a ticket: the flow,
 *  the explanation and the decision, exactly as the API returned them. */
function exportAlert(alert: AlertDetail) {
  const blob = new Blob([JSON.stringify(alert, null, 2)], { type: 'application/json' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = 'recluse-alert-' + alert.id + '.json'
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
}

/**
 * The three judgement buttons.
 *
 * Writing here is the only input active learning has. On success the mutation
 * invalidates the alert queries, so the queue behind the drawer refreshes itself
 * rather than going stale behind a decision that has already been recorded.
 */
function VerdictFooter({ alert }: { alert: AlertDetail }) {
  const submit = useSubmitVerdict(alert.id)
  const [note, setNote] = useState('')

  const recorded = submit.data?.verdict ?? alert.latest_verdict

  return (
    <div className="space-y-3">
      {alert.ground_truth_label ? (
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <Badge variant="medium">demo only</Badge>
          <span className="text-muted-foreground">
            Replay ground truth:{' '}
            <span className="text-foreground-strong font-mono">{alert.ground_truth_label}</span>
          </span>
          <span className="text-subtle-foreground">
            — a dataset label, never a model output. Always absent on live capture.
          </span>
        </div>
      ) : null}

      <input
        value={note}
        onChange={(event) => setNote(event.target.value)}
        placeholder="Triage note (optional)"
        aria-label="Verdict note"
        className="border-border bg-inset text-foreground placeholder:text-subtle-foreground focus:border-border-strong h-9 w-full rounded-lg border px-3 text-xs outline-none"
      />

      <div className="flex flex-wrap items-center gap-2">
        <Button variant="outline" size="sm" onClick={() => exportAlert(alert)}>
          <Download aria-hidden="true" />
          Export JSON
        </Button>

        {recorded ? (
          <span className="text-muted-foreground flex items-center gap-1.5 text-xs">
            Recorded
            <Badge variant={VERDICT_VARIANT[recorded]}>{VERDICT_LABEL[recorded]}</Badge>
          </span>
        ) : null}

        <div className="ml-auto flex flex-wrap items-center gap-2">
          {(['UNSURE', 'FP', 'TP'] as const).map((verdict) => (
            <Button
              key={verdict}
              variant={verdict === 'TP' ? 'default' : verdict === 'FP' ? 'success' : 'outline'}
              size="sm"
              disabled={submit.isPending}
              aria-pressed={recorded === verdict}
              className={cn(recorded === verdict && 'ring-foreground/40 ring-2')}
              onClick={() => submit.mutate({ verdict, note: note || null, analyst: null })}
            >
              {VERDICT_LABEL[verdict]}
            </Button>
          ))}
        </div>
      </div>

      {submit.isError ? (
        <p className="text-xs text-[var(--critical)]">
          {submit.error instanceof Error ? submit.error.message : 'Could not record the verdict'}
        </p>
      ) : null}

      {/* Stated rather than merely absent, because "where is the block button"
          is the first question anyone asks of an IDS dashboard. */}
      <p className="text-subtle-foreground text-[11px] leading-relaxed">
        Recluse alerts, ranks and explains. It never blocks traffic — at a million flows a day a
        0.1% false-positive rate is a thousand false alerts, and automatic containment on that
        rate takes production down.
      </p>
    </div>
  )
}

export function AlertDetailDrawer({
  alertId,
  onClose,
  onOpen,
}: {
  alertId: number | null
  onClose: () => void
  onOpen: (id: number) => void
}) {
  const { data: alert, error, isPending } = useAlert(alertId)
  const { data: related } = useRelatedAlerts(alertId)

  return (
    <Drawer
      open={alertId !== null}
      onClose={onClose}
      title={alert ? 'Alert ' + alert.id : 'Alert ' + (alertId ?? '')}
      eyebrow={
        alert
          ? 'Recluse two-stage triage · ' + clockTime(alert.detected_at) + ' · ' + sinceNow(alert.detected_at)
          : undefined
      }
      subtitle={
        alert ? (
          <div className="flex flex-wrap items-center gap-2.5">
            <ClassBadge alert={alert} />
            <SeverityMark severity={alert.severity} />
            <StatusPill status={alert.status} />
            <span className="text-subtle-foreground font-mono text-[11px]">
              risk <span className="text-foreground-strong">{decimal(alert.risk_score)}</span>
            </span>
            {alert.occurrence_count > 1 ? (
              <span className="text-subtle-foreground font-mono text-[11px]">
                <span className="text-foreground-strong">×{count(alert.occurrence_count)}</span> flows
                deduplicated
              </span>
            ) : null}
          </div>
        ) : null
      }
      footer={alert ? <VerdictFooter alert={alert} /> : null}
    >
      {isPending ? (
        <div className="px-6 py-5">
          <LoadingRows rows={8} />
        </div>
      ) : error ? (
        <ErrorState error={error} label="Could not load this alert" />
      ) : alert ? (
        <>
          <dl className="border-divider bg-inset grid grid-cols-2 gap-x-4 gap-y-3 border-b px-5 py-4 sm:grid-cols-4 sm:px-6">
            <Fact term="Source">{endpoint(alert.src_ip, alert.src_port)}</Fact>
            <Fact term="Destination">{endpoint(alert.dst_ip, alert.dst_port)}</Fact>
            <Fact term="Asset">{alert.asset_criticality ?? 'not inventoried'}</Fact>
            <Fact term="Host history">
              {count(alert.host_prior_alert_count)} prior alert
              {alert.host_prior_alert_count === 1 ? '' : 's'}
            </Fact>
          </dl>

          <Section step={1} heading="Why was this flagged">
            {/*
             * The sentence sits above everything else, not under a chart. A
             * chart needs interpreting; a sentence does not, and four seconds
             * is the budget.
             */}
            {alert.narrative ? (
              <p className="text-foreground-strong mb-4 text-[15px] leading-relaxed">
                {alert.narrative}
              </p>
            ) : null}

            <ModelCards alert={alert} />

            <div className="mt-5">
              <ExplanationChart explanation={alert.explanation} />
            </div>
            <RawFlow flow={alert.raw_flow} />

            <dl className="text-subtle-foreground mt-4 flex flex-wrap gap-x-6 gap-y-1 text-[11px]">
              <div className="flex gap-1.5">
                <dt>Detected by</dt>
                <dd className="text-foreground">{STAGE_LABEL[alert.detection_stage]}</dd>
              </div>
              <div className="flex min-w-0 gap-1.5">
                <dt>Model</dt>
                <dd className="text-foreground truncate font-mono">{alert.model_version}</dd>
              </div>
            </dl>
          </Section>

          <Section step={2} heading="What this likely is">
            <TechniquePanel actions={alert.recommended_actions} />
            <HostContext alert={alert} related={related} onOpen={onOpen} />
          </Section>

          <Section step={3} heading="How to fix it">
            <PlaybookPanel actions={alert.recommended_actions} />
          </Section>
        </>
      ) : null}
    </Drawer>
  )
}
