/**
 * The filter row.
 *
 * One row, above everything it scopes, so every panel below re-renders against
 * the same slice. The unclassified-anomaly chip sits apart from the selects and
 * is a toggle rather than an option inside the class list, because it is the
 * answer to "what does this catch that a signature IDS doesn't" and it has to
 * be one click away during a demo -- not one click plus finding the right entry
 * in a dropdown.
 */
import { Radar, X } from 'lucide-react'

import type { AlertFilters } from '@/api/queries'
import { Button } from '@/components/ui/button'
import { Select } from '@/components/ui/select'
import { FAMILIES, FAMILY_LABEL, SEVERITIES, STATUS_LABEL, STATUSES } from '@/lib/alerts'
import { cn } from '@/lib/utils'

/** Time windows, as a lookback rather than a date picker: a shift is measured
 *  backwards from now, and nobody triaging alerts wants a calendar. */
const WINDOWS = [
  { value: '', label: 'Any time' },
  { value: '1', label: 'Last hour' },
  { value: '8', label: 'Last 8 hours' },
  { value: '24', label: 'Last 24 hours' },
  { value: '168', label: 'Last 7 days' },
] as const

const VERDICT_OPTIONS = [
  { value: '', label: 'Any verdict' },
  { value: 'none', label: 'Unjudged' },
  { value: 'TP', label: 'True positive' },
  { value: 'FP', label: 'False positive' },
  { value: 'UNSURE', label: 'Need more info' },
] as const

export interface QueueFilterState extends AlertFilters {
  /** Hours of lookback as a string, so the select round-trips its own value.
   *  Converted to the `since` the API takes when the request is built. */
  windowHours?: string
}

export function buildFilters(state: QueueFilterState): AlertFilters {
  const { windowHours, ...rest } = state
  if (!windowHours) return rest
  const since = new Date(Date.now() - Number(windowHours) * 3_600_000)
  return { ...rest, since: since.toISOString() }
}

export function QueueFilters({
  state,
  onChange,
}: {
  state: QueueFilterState
  onChange: (next: QueueFilterState) => void
}) {
  const novelOnly = state.kind === 'UNCLASSIFIED_ANOMALY'
  const active = Object.entries(state).filter(([, value]) => value).length > 0

  const set = (patch: Partial<QueueFilterState>) => onChange({ ...state, ...patch })

  return (
    <div className="border-border flex flex-wrap items-center gap-2 border-b px-4 py-2">
      <Select
        value={state.severity ?? ''}
        onChange={(event) => set({ severity: event.target.value })}
        aria-label="Filter by severity"
      >
        <option value="">Any severity</option>
        {SEVERITIES.map((severity) => (
          <option key={severity} value={severity}>
            {severity}
          </option>
        ))}
      </Select>

      <Select
        value={state.family ?? ''}
        onChange={(event) =>
          // Choosing a family means choosing a named attack, so it clears the
          // unclassified chip rather than producing the empty intersection of
          // "has this family" and "has no family at all".
          set({ family: event.target.value, kind: event.target.value ? '' : state.kind })
        }
        aria-label="Filter by attack family"
      >
        <option value="">Any class</option>
        {FAMILIES.map((family) => (
          <option key={family} value={family}>
            {FAMILY_LABEL[family]}
          </option>
        ))}
      </Select>

      <Select
        value={state.windowHours ?? ''}
        onChange={(event) => set({ windowHours: event.target.value })}
        aria-label="Filter by time window"
      >
        {WINDOWS.map((window) => (
          <option key={window.value} value={window.value}>
            {window.label}
          </option>
        ))}
      </Select>

      <Select
        value={state.verdict ?? ''}
        onChange={(event) => set({ verdict: event.target.value })}
        aria-label="Filter by analyst verdict"
      >
        {VERDICT_OPTIONS.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </Select>

      <Select
        value={state.status ?? ''}
        onChange={(event) => set({ status: event.target.value })}
        aria-label="Filter by triage status"
      >
        <option value="">Any status</option>
        {STATUSES.map((status) => (
          <option key={status} value={status}>
            {STATUS_LABEL[status]}
          </option>
        ))}
      </Select>

      <button
        type="button"
        onClick={() =>
          set({ kind: novelOnly ? '' : 'UNCLASSIFIED_ANOMALY', family: novelOnly ? state.family : '' })
        }
        aria-pressed={novelOnly}
        className={cn(
          'inline-flex h-8 items-center gap-1.5 rounded-md border px-2.5 text-xs transition-colors',
          'focus-visible:ring-2 focus-visible:ring-[var(--ring)] outline-none',
          novelOnly
            ? 'border-[var(--novel)] bg-[color-mix(in_oklab,var(--novel)_20%,transparent)] text-[var(--novel)] font-medium'
            : 'border-border text-muted-foreground hover:bg-muted',
        )}
      >
        <Radar className="size-3.5" aria-hidden="true" />
        Unclassified anomalies
      </button>

      {active ? (
        <Button variant="ghost" size="sm" onClick={() => onChange({})}>
          <X aria-hidden="true" />
          Clear filters
        </Button>
      ) : null}
    </div>
  )
}
