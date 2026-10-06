/**
 * The traffic-source control: which split, how fast, start and stop.
 *
 * Changing speed stops and restarts, because that is what the engine does --
 * `replay.py` fixes the inter-batch gap from `speed` when a run begins, so there
 * is no live speed to change. A control that pretended otherwise would leave the
 * number on screen disagreeing with the rate of the demo.
 *
 * `train` is not offered. Replaying the rows Stage 1 was fitted on would show
 * the model recognising traffic it memorised, which is the one demo that proves
 * nothing, and the backend refuses it anyway.
 */
import { Play, Square } from 'lucide-react'
import { useState } from 'react'

import { useReplayControl, useReplayStatus } from '@/api/queries'
import type { ReplaySpeed } from '@/api/types'
import { Button } from '@/components/ui/button'
import { Select } from '@/components/ui/select'
import { count, sinceNow } from '@/lib/format'

const SPEEDS: ReplaySpeed[] = [1, 10, 100]

/** The replayable splits, matching `REPLAYABLE_SPLITS` in `app/replay.py`. */
const DATASETS = [
  { value: 'test', label: 'Held-out test day' },
  { value: 'val', label: 'Validation day' },
] as const

export function ReplayControls() {
  const { data: status } = useReplayStatus()
  const { start, stop } = useReplayControl()

  const [speed, setSpeed] = useState<ReplaySpeed>(10)
  const [dataset, setDataset] = useState<string>('test')

  const running = status?.running ?? false
  const busy = start.isPending || stop.isPending
  const failure = start.error ?? stop.error

  const restartAt = async (next: ReplaySpeed) => {
    setSpeed(next)
    if (!running) return
    await stop.mutateAsync()
    await start.mutateAsync({ speed: next, dataset })
  }

  return (
    <div className="border-border flex flex-wrap items-center gap-3 rounded-[var(--radius-card)] border px-4 py-3">
      <Select
        value={dataset}
        onChange={(event) => setDataset(event.target.value)}
        disabled={running}
        aria-label="Which split to replay"
      >
        {DATASETS.map((entry) => (
          <option key={entry.value} value={entry.value}>
            {entry.label}
          </option>
        ))}
      </Select>

      <div
        className="border-border inline-flex overflow-hidden rounded-md border"
        role="group"
        aria-label="Replay speed"
      >
        {SPEEDS.map((option) => (
          <button
            key={option}
            type="button"
            onClick={() => void restartAt(option)}
            disabled={busy}
            aria-pressed={speed === option}
            className={
              'px-2.5 py-1.5 text-xs transition-colors disabled:opacity-50 ' +
              (speed === option
                ? 'bg-[var(--info)] text-[var(--background)] font-medium'
                : 'text-muted-foreground hover:bg-muted')
            }
          >
            {option}×
          </button>
        ))}
      </div>

      {running ? (
        <Button variant="outline" size="sm" onClick={() => stop.mutate()} disabled={busy}>
          <Square aria-hidden="true" />
          Stop replay
        </Button>
      ) : (
        <Button
          size="sm"
          onClick={() => start.mutate({ speed, dataset })}
          disabled={busy}
        >
          <Play aria-hidden="true" />
          Start replay
        </Button>
      )}

      {status ? (
        <dl className="text-muted-foreground ml-auto flex flex-wrap items-center gap-x-5 gap-y-1 text-xs">
          <div className="flex gap-1.5">
            <dt>Rows scored</dt>
            <dd className="tabular text-foreground font-mono">{count(status.rows_scored)}</dd>
          </div>
          <div className="flex gap-1.5">
            <dt>Alerts raised</dt>
            <dd className="tabular text-foreground font-mono">{count(status.alerts_emitted)}</dd>
          </div>
          {status.started_at ? (
            <div className="flex gap-1.5">
              <dt>Started</dt>
              <dd className="text-foreground">{sinceNow(status.started_at)}</dd>
            </div>
          ) : null}
        </dl>
      ) : null}

      {failure ? (
        <p className="w-full text-xs text-[var(--critical)]">
          {failure instanceof Error ? failure.message : 'The replay control failed'}
        </p>
      ) : null}
    </div>
  )
}
