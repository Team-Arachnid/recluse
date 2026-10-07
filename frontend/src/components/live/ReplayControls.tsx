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
import { Segmented } from '@/components/ui/segmented'
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
    <div className="bg-card border-border flex flex-wrap items-center gap-3 rounded-[var(--radius-card)] border px-4 py-3.5">
      <span className="text-foreground-strong flex items-center gap-2 text-xs font-semibold tracking-wider uppercase">
        <span aria-hidden="true" className="bg-brand h-3.5 w-1 rounded-full" />
        Replay
      </span>

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

      {/* While a replay runs, the control shows the speed the server is actually
          running at, not the last one clicked here -- a control that disagreed
          with the rate of the demo would be worse than none. */}
      <Segmented
        value={running && status?.speed ? (status.speed as ReplaySpeed) : speed}
        options={SPEEDS.map((option) => ({ value: option, label: option + '×' }))}
        onChange={(next) => void restartAt(next)}
        label="Replay speed"
        disabled={busy}
        mono
      />

      {running ? (
        <Button variant="outline" size="sm" onClick={() => stop.mutate()} disabled={busy}>
          <Square aria-hidden="true" />
          Stop replay
        </Button>
      ) : (
        <Button size="sm" onClick={() => start.mutate({ speed, dataset })} disabled={busy}>
          <Play aria-hidden="true" />
          Start replay
        </Button>
      )}

      {status ? (
        <dl className="text-subtle-foreground ml-auto flex flex-wrap items-center gap-x-5 gap-y-1 font-mono text-[11px]">
          <div className="flex gap-1.5">
            <dt>rows scored</dt>
            <dd className="tabular text-foreground-strong">{count(status.rows_scored)}</dd>
          </div>
          <div className="flex gap-1.5">
            <dt>alerts raised</dt>
            <dd className="tabular text-foreground-strong">{count(status.alerts_emitted)}</dd>
          </div>
          {status.started_at ? (
            <div className="flex gap-1.5">
              <dt>started</dt>
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
