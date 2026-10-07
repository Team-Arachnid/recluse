/**
 * Screen 1 — the triage queue. This is the landing page.
 *
 * Most security tools open on a dashboard of counters, and an analyst's first
 * action is to click past it into the list of things that need judging. Every
 * one of those clicks is overhead repeated hundreds of times a shift. Recluse
 * opens on the work. The aggregate view exists -- it is the Analytics screen --
 * and it is reached from a nav link, because it answers a different question for
 * a different reader.
 *
 * There is also a subtractive argument. An overview landing page invites a hero
 * tile, and the most tempting hero tile is an accuracy percentage: on traffic
 * that is 99% benign, a model that always answers "benign" scores 99%. Opening
 * on a work queue removes the slot that number would have filled.
 *
 * The open alert lives in the URL (`?alert=1284`) rather than in component
 * state, so a row an analyst is looking at can be linked to a colleague and
 * survives a refresh. The drawer is still a drawer: the queue behind it keeps
 * its scroll position and its selection.
 */
import type { RowSelectionState } from '@tanstack/react-table'
import { Inbox, Radar } from 'lucide-react'
import { useCallback, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'

import { useAlerts, useUpdateAlertStatus } from '@/api/queries'
import { useAlertStream } from '@/api/stream'
import { AlertDetailDrawer } from '@/components/alert/AlertDetailDrawer'
import { PageHeader } from '@/components/AppShell'
import { AlertTable } from '@/components/queue/AlertTable'
import {
  buildFilters,
  QueueFilters,
  type QueueFilterState,
} from '@/components/queue/QueueFilters'
import { QueueStatStrip } from '@/components/queue/QueueStatStrip'
import { EmptyState, ErrorState, LoadingRows } from '@/components/States'
import { Button } from '@/components/ui/button'
import { count } from '@/lib/format'

export function TriageQueue() {
  const [filterState, setFilterState] = useState<QueueFilterState>({})
  const [selected, setSelected] = useState<RowSelectionState>({})
  const [params, setParams] = useSearchParams()
  const { following } = useAlertStream()

  const filters = useMemo(() => buildFilters(filterState), [filterState])
  const query = useAlerts(filters)
  const updateStatus = useUpdateAlertStatus()

  const rows = useMemo(
    () => query.data?.pages.flatMap((page) => page.items) ?? [],
    [query.data],
  )

  const openId = params.get('alert')
  const activeId = openId ? Number(openId) : null

  const open = useCallback(
    (id: number) => {
      const next = new URLSearchParams(params)
      next.set('alert', String(id))
      // `replace`, so judging twenty alerts does not bury the queue under
      // twenty back-button steps.
      setParams(next, { replace: true })
    },
    [params, setParams],
  )

  const close = useCallback(() => {
    const next = new URLSearchParams(params)
    next.delete('alert')
    setParams(next, { replace: true })
  }, [params, setParams])

  const loadMore = useCallback(() => {
    // Paused means the table holds still, which includes not pulling the next
    // page in under the analyst's cursor.
    if (!following) return
    if (query.hasNextPage && !query.isFetchingNextPage) void query.fetchNextPage()
  }, [following, query])

  const selectedIds = useMemo(
    () => Object.keys(selected).filter((id) => selected[id]).map(Number),
    [selected],
  )

  const dismiss = () => {
    updateStatus.mutate(
      { alert_ids: selectedIds, status: 'dismissed' },
      { onSuccess: () => setSelected({}) },
    )
  }

  return (
    <div className="mx-auto flex min-h-0 w-full max-w-[1600px] flex-1 flex-col px-4 py-6 sm:px-6 lg:px-8">
      <PageHeader
        title="Triage queue"
        lede="Open alerts, highest risk first. Stage 1 names the attacks it was trained on; Stage 2 flags what nobody named — the ochre rows."
      />

      <QueueStatStrip />

      <section className="bg-card border-border mt-4 flex min-h-[26rem] flex-1 flex-col overflow-hidden rounded-[var(--radius-card)] border">
        <QueueFilters state={filterState} onChange={setFilterState} />

        {query.isPending ? (
          <div className="p-4">
            <LoadingRows rows={12} />
          </div>
        ) : query.error ? (
          <ErrorState error={query.error} label="Could not load the queue" />
        ) : rows.length === 0 ? (
          <EmptyState
            icon={
              filterState.kind === 'UNCLASSIFIED_ANOMALY' ? (
                <Radar className="size-5" />
              ) : (
                <Inbox className="size-5" />
              )
            }
            title={
              Object.values(filterState).some(Boolean)
                ? 'No alerts match these filters'
                : 'No open alerts'
            }
            hint={
              Object.values(filterState).some(Boolean)
                ? 'Clear a filter, or widen the time window.'
                : 'Start a replay from the Live traffic screen to score held-out flows and fill the queue.'
            }
          />
        ) : (
          <AlertTable
            rows={rows}
            selected={selected}
            onSelectedChange={setSelected}
            activeId={activeId}
            onOpen={open}
            onReachEnd={loadMore}
            isFetchingMore={query.isFetchingNextPage}
          />
        )}

        {/*
         * The bulk bar appears only when there is a selection. A permanently
         * visible action bar with nothing selected is a row of disabled buttons,
         * which teaches an analyst to ignore that strip of the screen.
         */}
        {selectedIds.length > 0 ? (
          <div className="border-border bg-muted flex flex-wrap items-center gap-3 border-t px-4 py-2.5">
            <span className="text-foreground-strong font-mono text-xs">
              {count(selectedIds.length)} selected
            </span>
            <Button size="sm" variant="outline" onClick={dismiss} disabled={updateStatus.isPending}>
              Dismiss selected
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setSelected({})}>
              Clear selection
            </Button>
            {/* Dismissing is a triage move, not a judgement. Saying so here is
                what stops the bulk action being used as a shortcut for "the model
                was wrong", which would poison the labels a retrain reads. */}
            <span className="text-subtle-foreground ml-auto hidden text-[11px] lg:inline">
              Dismissing moves these out of the queue. It records no verdict.
            </span>
          </div>
        ) : null}
      </section>

      <AlertDetailDrawer alertId={activeId} onClose={close} onOpen={open} />
    </div>
  )
}
