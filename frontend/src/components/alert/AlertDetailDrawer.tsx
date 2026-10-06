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
import { ArrowRight, Check, CircleHelp, ExternalLink, Radar } from 'lucide-react'
import { useState } from 'react'

import { useAlert, useRelatedAlerts, useSubmitVerdict } from '@/api/queries'
import type { AlertDetail, AlertSummary, RecommendedActions } from '@/api/types'
import { ExplanationChart } from '@/components/alert/ExplanationChart'
import { ErrorState, LoadingRows } from '@/components/States'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Drawer } from '@/components/ui/drawer'
import {
  classLabel,
  classVariant,
  isNovel,
  SEVERITY_VARIANT,
  STAGE_LABEL,
  STATUS_LABEL,
  VERDICT_LABEL,
  VERDICT_VARIANT,
} from '@/lib/alerts'
import { clockTime, decimal, endpoint, sinceNow } from '@/lib/format'

/** A numbered panel. The numbers are real here: this is a fixed sequence of
 *  three questions, asked in one order, and the order carries the argument. */
function Panel({
  step,
  heading,
  children,
}: {
  step: number
  heading: string
  children: React.ReactNode
}) {
  return (
    <section className="border-border border-b px-5 py-5 last:border-b-0">
      <div className="mb-3 flex items-baseline gap-2.5">
        <span
          aria-hidden="true"
          className="text-muted-foreground tabular font-mono text-xs"
        >
          {step}
        </span>
        <h3 className="text-sm font-semibold tracking-tight">{heading}</h3>
      </div>
      {children}
    </section>
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
    <div className="mt-4">
      <div className="mb-2 flex items-baseline justify-between gap-3">
        <h4 className="text-muted-foreground text-xs">Raw flow record</h4>
        {entries.length > 12 ? (
          <Button variant="ghost" size="sm" onClick={() => setExpanded(!expanded)}>
            {expanded ? 'Show fewer' : 'Show all ' + entries.length + ' features'}
          </Button>
        ) : null}
      </div>

      <dl className="border-border bg-muted/40 grid grid-cols-1 gap-x-6 gap-y-1 rounded-md border p-3 font-mono text-[11px] sm:grid-cols-2">
        {shown.map(([key, value]) => (
          <div key={key} className="flex items-baseline justify-between gap-3">
            <dt className="text-muted-foreground truncate">{key}</dt>
            <dd className="tabular shrink-0">
              {typeof value === 'number' ? decimal(value, 4) : String(value)}
            </dd>
          </div>
        ))}
      </dl>

      {provenance?.note ? (
        <p className="text-muted-foreground mt-2 text-[11px] leading-relaxed">
          {provenance.note}
        </p>
      ) : null}
    </div>
  )
}

/** Panel 2 for a named family: the technique plus the sentence that saves a tab. */
function TechniquePanel({ actions }: { actions: RecommendedActions }) {
  const technique = actions.technique
  if (!technique) {
    return (
      <div className="flex items-start gap-2.5">
        <Radar className="mt-0.5 size-4 shrink-0 text-[var(--novel)]" aria-hidden="true" />
        <div>
          <p className="text-sm">
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
    <div>
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant="info" className="font-mono">
          {technique.technique_id}
        </Badge>
        <span className="text-sm font-medium">{technique.name}</span>
        <a
          href={technique.url}
          target="_blank"
          rel="noreferrer"
          className="text-muted-foreground hover:text-foreground inline-flex items-center gap-1 text-xs"
        >
          attack.mitre.org
          <ExternalLink className="size-3" aria-hidden="true" />
        </a>
      </div>
      {/* The plain-English line, so this is readable without opening a second
          tab mid-shift. */}
      <p className="mt-2 text-sm leading-relaxed">{technique.means}</p>
    </div>
  )
}

/** Panel 3: a checklist that can be scanned in three seconds, or the honest
 *  admission that there is nothing to scan. */
function PlaybookPanel({ actions }: { actions: RecommendedActions }) {
  if (!actions.has_playbook) {
    return (
      <div className="flex items-start gap-2.5">
        <CircleHelp
          className="mt-0.5 size-4 shrink-0 text-[var(--medium)]"
          aria-hidden="true"
        />
        <div>
          <p className="text-sm">No playbook yet — escalate for manual investigation.</p>
          <p className="text-muted-foreground mt-1.5 text-xs leading-relaxed">
            {actions.summary}
          </p>
        </div>
      </div>
    )
  }

  return (
    <div>
      <p className="text-sm leading-relaxed">{actions.summary}</p>
      <ul className="mt-3 space-y-1.5">
        {actions.actions.map((action) => (
          <li key={action} className="flex items-start gap-2 text-sm">
            <Check
              className="mt-0.5 size-3.5 shrink-0 text-[var(--ok)]"
              aria-hidden="true"
            />
            <span className="leading-snug">{action}</span>
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
      <p className="text-muted-foreground mt-4 text-xs">
        No other alerts from <span className="font-mono">{alert.src_ip}</span> inside 24 hours of
        this one.
      </p>
    )
  }

  return (
    <div className="mt-4">
      <h4 className="text-muted-foreground mb-2 text-xs">
        {related.length} other alert{related.length === 1 ? '' : 's'} from{' '}
        <span className="font-mono">{alert.src_ip}</span> within 24 hours
      </h4>
      <ul className="border-border divide-border divide-y rounded-md border">
        {related.slice(0, 6).map((row) => (
          <li key={row.id}>
            <button
              type="button"
              onClick={() => onOpen(row.id)}
              className="hover:bg-muted flex w-full items-center gap-2.5 px-2.5 py-1.5 text-left text-xs"
            >
              <span className="tabular text-muted-foreground shrink-0 font-mono">
                {clockTime(row.detected_at)}
              </span>
              <Badge variant={classVariant(row)} className="shrink-0">
                {classLabel(row)}
              </Badge>
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
          <Badge variant="neutral">demo only</Badge>
          <span className="text-muted-foreground">
            Replay ground truth:{' '}
            <span className="text-foreground font-mono">{alert.ground_truth_label}</span>
          </span>
          <span className="text-muted-foreground/70">
            — a dataset label, never a model output. Always absent on live capture.
          </span>
        </div>
      ) : null}

      <input
        value={note}
        onChange={(event) => setNote(event.target.value)}
        placeholder="Note (optional)"
        aria-label="Verdict note"
        className="border-border bg-background focus-visible:ring-2 focus-visible:ring-[var(--ring)] h-8 w-full rounded-md border px-2.5 text-xs outline-none"
      />

      <div className="flex flex-wrap items-center gap-2">
        {(['TP', 'FP', 'UNSURE'] as const).map((verdict) => (
          <Button
            key={verdict}
            variant={recorded === verdict ? 'default' : 'outline'}
            size="sm"
            disabled={submit.isPending}
            onClick={() => submit.mutate({ verdict, note: note || null, analyst: null })}
          >
            {VERDICT_LABEL[verdict]}
          </Button>
        ))}

        {recorded ? (
          <span className="text-muted-foreground ml-auto flex items-center gap-1.5 text-xs">
            Recorded
            <Badge variant={VERDICT_VARIANT[recorded]}>{VERDICT_LABEL[recorded]}</Badge>
          </span>
        ) : null}
      </div>

      {submit.isError ? (
        <p className="text-xs text-[var(--critical)]">
          {submit.error instanceof Error ? submit.error.message : 'Could not record the verdict'}
        </p>
      ) : null}

      {/* Stated rather than merely absent, because "where is the block button"
          is the first question anyone asks of an IDS dashboard. */}
      <p className="text-muted-foreground/70 text-[11px] leading-relaxed">
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
      subtitle={
        alert ? (
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant={classVariant(alert)}>
              {isNovel(alert) ? <Radar className="size-3" aria-hidden="true" /> : null}
              {classLabel(alert)}
            </Badge>
            <Badge variant={SEVERITY_VARIANT[alert.severity]}>{alert.severity}</Badge>
            <span className="text-muted-foreground text-xs">
              risk <span className="tabular text-foreground font-mono">{decimal(alert.risk_score)}</span>
            </span>
            {alert.occurrence_count > 1 ? (
              <span className="text-muted-foreground text-xs">
                <span className="tabular text-foreground font-mono">
                  ×{alert.occurrence_count}
                </span>{' '}
                flows deduplicated
              </span>
            ) : null}
            <span className="text-muted-foreground text-xs">
              {STATUS_LABEL[alert.status]} · {sinceNow(alert.detected_at)}
            </span>
          </div>
        ) : null
      }
      footer={alert ? <VerdictFooter alert={alert} /> : null}
    >
      {isPending ? (
        <div className="px-5 py-5">
          <LoadingRows rows={8} />
        </div>
      ) : error ? (
        <ErrorState error={error} label="Could not load this alert" />
      ) : alert ? (
        <>
          <Panel step={1} heading="Why was this flagged">
            {/*
             * The sentence sits above the chart, not under it. A chart needs
             * interpreting; a sentence does not, and four seconds is the budget.
             */}
            {alert.narrative ? (
              <p className="mb-4 text-sm leading-relaxed">{alert.narrative}</p>
            ) : null}

            <ExplanationChart explanation={alert.explanation} />
            <RawFlow flow={alert.raw_flow} />

            <dl className="text-muted-foreground mt-4 flex flex-wrap gap-x-6 gap-y-1 text-xs">
              <div className="flex gap-1.5">
                <dt>Detected by</dt>
                <dd className="text-foreground">{STAGE_LABEL[alert.detection_stage]}</dd>
              </div>
              {alert.confidence !== null ? (
                <div className="flex gap-1.5">
                  <dt>Stage 1 confidence</dt>
                  <dd className="tabular text-foreground font-mono">
                    {decimal(alert.confidence)}
                  </dd>
                </div>
              ) : null}
              {alert.anomaly_score !== null ? (
                <div className="flex gap-1.5">
                  <dt>Stage 2 error</dt>
                  <dd className="tabular text-foreground font-mono">
                    {decimal(alert.anomaly_score, 4)}
                  </dd>
                </div>
              ) : null}
              <div className="flex gap-1.5">
                <dt>Model</dt>
                <dd className="text-foreground font-mono">{alert.model_version}</dd>
              </div>
            </dl>
          </Panel>

          <Panel step={2} heading="What this likely is">
            <TechniquePanel actions={alert.recommended_actions} />
            <HostContext alert={alert} related={related} onOpen={onOpen} />
          </Panel>

          <Panel step={3} heading="How to fix it">
            <PlaybookPanel actions={alert.recommended_actions} />
          </Panel>
        </>
      ) : null}
    </Drawer>
  )
}
