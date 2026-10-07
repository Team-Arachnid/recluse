/**
 * The live-capture control (Phase 9): a shadow burn-in first, alerting after.
 *
 * It offers only what the server lists -- the interfaces an operator put in
 * IDS_LIVE_INTERFACES and the pcaps in IDS_LIVE_PCAP_DIR -- because capture is
 * lawful only on a network you own or are authorised to monitor, and that is a
 * decision for whoever configures the deployment, not for a dropdown.
 *
 * The two thresholds sit side by side because the gap between them is the
 * finding: the share of this network's ordinary traffic the CICIDS2017
 * threshold would have flagged is domain shift with a number on it. Alerting is
 * offered only once a burn-in has produced the local one.
 */
import { Radio, ShieldCheck, Square } from 'lucide-react'
import { useState } from 'react'

import { useIngestControl, useIngestStatus } from '@/api/queries'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Select } from '@/components/ui/select'
import { count, decimal, percent, sinceNow } from '@/lib/format'

type Mode = 'shadow' | 'alert'

export function CaptureControls() {
  const { data: status } = useIngestStatus()
  const { start, stop } = useIngestControl()
  const [chosen, setChosen] = useState<string | null>(null)

  if (!status) return null

  const options = [
    ...status.allowed_interfaces.map((name) => ({ value: 'interface:' + name, label: 'Interface ' + name })),
    ...status.pcaps.map((name) => ({ value: 'pcap:' + name, label: 'Recorded capture ' + name })),
  ]
  const selected = chosen ?? options[0]?.value ?? null
  const running = status.running
  const calibration = status.calibration
  const busy = start.isPending || stop.isPending
  const failure = start.error ?? stop.error

  const begin = (mode: Mode) => {
    if (!selected) return
    const split = selected.indexOf(':')
    const kind = selected.slice(0, split)
    const name = selected.slice(split + 1)
    start.mutate(
      kind === 'interface'
        ? { source: 'interface', interface: name, mode }
        : { source: 'pcap', pcap: name, mode },
    )
  }

  return (
    <div className="bg-card border-border space-y-4 rounded-[var(--radius-card)] border px-4 py-3.5">
      <div className="flex flex-wrap items-center gap-3">
        <span className="text-foreground-strong flex items-center gap-2 text-xs font-semibold tracking-wider uppercase">
          <span aria-hidden="true" className="h-3.5 w-1 rounded-full bg-[var(--ok)]" />
          Live capture
        </span>
        {running ? (
          <Badge variant={status.mode === 'alert' ? 'high' : 'neutral'}>
            {status.mode === 'alert' ? 'Alerting' : 'Shadow — scoring, alerting no one'} · {status.source}
          </Badge>
        ) : (
          <span className="text-subtle-foreground text-xs">Idle</span>
        )}

        {options.length === 0 ? null : running ? (
          <Button variant="outline" size="sm" onClick={() => stop.mutate()} disabled={busy}>
            <Square aria-hidden="true" />
            Stop capture
          </Button>
        ) : (
          <>
            <Select
              value={selected ?? ''}
              onChange={(event) => setChosen(event.target.value)}
              aria-label="What to capture from"
            >
              {options.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </Select>
            <Button size="sm" variant="outline" onClick={() => begin('shadow')} disabled={busy || !selected}>
              <Radio aria-hidden="true" />
              Start shadow burn-in
            </Button>
            <Button
              size="sm"
              onClick={() => begin('alert')}
              disabled={busy || !selected || !calibration}
              title={
                calibration
                  ? 'Alert at the local threshold'
                  : 'Needs a local threshold: run a shadow burn-in, then make calibrate'
              }
            >
              <ShieldCheck aria-hidden="true" />
              Start alerting
            </Button>
          </>
        )}

        {status.source ? (
          <dl className="text-subtle-foreground ml-auto flex flex-wrap items-center gap-x-5 gap-y-1 font-mono text-[11px]">
            <div className="flex gap-1.5">
              <dt>packets</dt>
              <dd className="tabular text-foreground-strong">{count(status.packets)}</dd>
            </div>
            <div className="flex gap-1.5">
              <dt>flows scored</dt>
              <dd className="tabular text-foreground-strong">{count(status.scored)}</dd>
            </div>
            <div className="flex gap-1.5" title="Zero-duration flows: their rates are infinite and no training row had one">
              <dt>unscoreable</dt>
              <dd className="tabular text-foreground-strong">{count(status.unscoreable)}</dd>
            </div>
            <div className="flex gap-1.5">
              <dt>{status.mode === 'alert' ? 'alerting flows' : 'shadow rows'}</dt>
              <dd className="tabular text-foreground-strong">
                {count(status.mode === 'alert' ? status.alerts : status.shadow_rows)}
              </dd>
            </div>
            {status.started_at ? (
              <div className="flex gap-1.5">
                <dt>started</dt>
                <dd className="text-foreground">{sinceNow(status.started_at)}</dd>
              </div>
            ) : null}
          </dl>
        ) : null}
      </div>

      {options.length === 0 ? (
        <p className="text-muted-foreground max-w-3xl text-xs leading-relaxed">
          Live capture is off on this deployment. It runs only on the interfaces listed in{' '}
          <code className="font-mono">IDS_LIVE_INTERFACES</code> — a network you own or are
          authorised to monitor — or on recorded captures in{' '}
          <code className="font-mono">IDS_LIVE_PCAP_DIR</code>. Nothing on this screen can widen
          either.
        </p>
      ) : null}

      <div className="grid gap-4 sm:grid-cols-2">
        <div>
          <p className="text-muted-foreground text-xs">Stage 2 threshold, CICIDS2017</p>
          <p className="text-foreground-strong font-mono text-xl font-semibold">
            {decimal(status.tau_anom_dataset, 4)}
          </p>
          <p className="text-subtle-foreground text-[11px]">
            99.5th percentile of a 2017 lab&apos;s benign traffic — what replay decides at.
          </p>
        </div>
        <div>
          <p className="text-muted-foreground text-xs">Stage 2 threshold, this network</p>
          {calibration ? (
            <>
              <p className="font-mono text-xl font-semibold text-[var(--ok)]">
                {decimal(calibration.tau_anom_local, 4)}
              </p>
              <p className="text-subtle-foreground text-[11px]">
                Cut from {count(calibration.flows)} flows of a shadow burn-in. At the CICIDS2017
                threshold, {percent(calibration.dataset_threshold_alert_rate, 1)} of them would
                have been alerts.
              </p>
            </>
          ) : (
            <>
              <p className="text-subtle-foreground font-mono text-xl font-semibold">not calibrated</p>
              <p className="text-subtle-foreground text-[11px]">
                Run a shadow burn-in on traffic you consider normal, then{' '}
                <code className="font-mono">make calibrate</code>. Alerting stays off until then.
              </p>
            </>
          )}
        </div>
      </div>

      {status.error ? (
        <p className="text-xs text-[var(--critical)]">The last capture stopped on an error: {status.error}</p>
      ) : null}
      {failure ? (
        <p className="text-xs text-[var(--critical)]">
          {failure instanceof Error ? failure.message : 'The capture control failed'}
        </p>
      ) : null}
    </div>
  )
}
