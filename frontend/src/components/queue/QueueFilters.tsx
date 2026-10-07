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
import { Biohazard, Pause, Play, X } from 'lucide-react'

import type { AlertFilters } from '@/api/queries'
import { useQueueStats } from '@/api/queries'
import { useAlertStream } from '@/api/stream'
import { Button } from '@/components/ui/button'
import { Select } from '@/components/ui/select'
import { FAMILIES, FAMILY_LABEL, SEVERITIES, STATUS_LABEL, STATUSES } from '@/lib/alerts'
import { count } from '@/lib/format'
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

/**
 * Pausing stops the table following the feed; it does not disconnect.
 *
 * The queue is sorted by risk, so a new high-risk alert jumps to the top and
 * pushes everything down -- which at 100x means the row an analyst is about to
 * click moves out from under the cursor. Holding the table still is the fix,
 * and making it explicit is better than a tool that silently freezes when it
 * thinks you are busy.
 */
function FollowToggle() {
  const { following, setFollowing, connected } = useAlertStream()
  return (
    <div className="flex items-center gap-2.5">
      <span className="text-subtle-foreground hidden text-[11px] 2xl:inline">
        {following
          ? connected
            ? 'New alerts appear as they arrive'
            : 'Waiting for a traffic source'
          : 'Table held still'}
      </span>
      <Button
        variant="outline"
        size="sm"
        onClick={() => setFollowing(!following)}
        aria-pressed={following}
      >
        {following ? <Pause aria-hidden="true" /> : <Play aria-hidden="true" />}
        {following ? 'Following' : 'Paused'}
      </Button>
    </div>
  )
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
  const { data: stats } = useQueueStats()
  const novelOpen = typeof stats?.unclassified_open === 'number' ? stats.unclassified_open : null

  const set = (patch: Partial<QueueFilterState>) => onChange({ ...state, ...patch })

  return (
    <div className="border-border flex flex-wrap items-center gap-2 border-b px-3 py-2.5 sm:px-4">
      {/*
       * First, and visually its own thing: the one filter a demo needs, and the
       * one that answers what this catches that a signature IDS does not.
       */}
      <button
        type="button"
        onClick={() =>
          set({ kind: novelOnly ? '' : 'UNCLASSIFIED_ANOMALY', family: novelOnly ? state.family : '' })
        }
        aria-pressed={novelOnly}
        className={cn(
          'inline-flex h-8 cursor-pointer items-center gap-1.5 rounded-lg border px-3 text-xs font-medium transition-colors',
          'focus-visible:ring-2 focus-visible:ring-[var(--ring)] outline-none',
          novelOnly
            ? 'border-[var(--novel)] bg-[color-mix(in_oklab,var(--novel)_16%,transparent)] text-[var(--novel)]'
            : 'border-[color-mix(in_oklab,var(--novel)_35%,var(--border))] text-[var(--novel)] hover:bg-[color-mix(in_oklab,var(--novel)_10%,transparent)]',
        )}
      >
        <Biohazard className="size-3.5" aria-hidden="true" />
        Unclassified anomalies
        {novelOpen !== null ? (
          <span className="rounded bg-[color-mix(in_oklab,var(--novel)_18%,transparent)] px-1.5 font-mono text-[10px] tabular-nums">
            {count(novelOpen)}
          </span>
        ) : null}
      </button>

      <span aria-hidden="true" className="bg-border mx-1 hidden h-5 w-px sm:block" />

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

      {active ? (
        <Button variant="ghost" size="sm" onClick={() => onChange({})}>
          <X aria-hidden="true" />
          Clear filters
        </Button>
      ) : null}

      <div className="ml-auto">
        <FollowToggle />
      </div>
    </div>
  )
}
