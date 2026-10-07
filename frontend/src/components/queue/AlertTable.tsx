/**
 * The triage queue table.
 *
 * Virtualised, because it holds tens of thousands of rows during a 100x replay
 * and rendering them all freezes the tab. Sorted on the server by `risk_score`,
 * never by time: sorting a triage queue chronologically ranks alerts by when a
 * packet happened rather than by what needs attention first.
 *
 * `UNCLASSIFIED_ANOMALY` rows carry three marks, not one -- their own badge
 * colour, a biohazard glyph, and an accent down the left edge of the row.
 * Colour alone would fail a colourblind analyst, and these are the rows the
 * whole project exists to produce.
 */
import { useVirtualizer } from '@tanstack/react-virtual'
import {
  createColumnHelper,
  flexRender,
  rowSelectionFeature,
  tableFeatures,
  useTable,
  type RowSelectionState,
} from '@tanstack/react-table'
import { useEffect, useMemo, useRef } from 'react'

import type { AlertSummary } from '@/api/types'
import { ClassBadge, SeverityMark, StatusPill } from '@/components/alert/AlertGlyphs'
import { Badge } from '@/components/ui/badge'
import { Checkbox } from '@/components/ui/checkbox'
import { isNovel, SEVERITY_COLOR, VERDICT_SHORT, VERDICT_VARIANT } from '@/lib/alerts'
import { clockTime, count, decimal, endpoint } from '@/lib/format'
import { cn } from '@/lib/utils'

const features = tableFeatures({ rowSelectionFeature })
const column = createColumnHelper<typeof features, AlertSummary>()

/**
 * Column widths, as a grid rather than a `<table>` layout.
 *
 * A grid is what lets the header and the virtualised rows stay aligned: the
 * rows are absolutely positioned inside a scroll container, so they are not
 * siblings of the header and cannot share a table's automatic column sizing.
 */
const LAYOUT: Record<string, { width: string; align?: 'right' | 'center' }> = {
  select: { width: '2.5rem', align: 'center' },
  time: { width: '5.25rem' },
  flow: { width: 'minmax(14rem, 1fr)' },
  class: { width: '11.5rem' },
  severity: { width: '6rem' },
  risk: { width: '6.5rem', align: 'right' },
  verdict: { width: '4.25rem', align: 'center' },
  confidence: { width: '5.5rem', align: 'right' },
  criticality: { width: '5.5rem' },
  status: { width: '6.5rem' },
  count: { width: '4rem', align: 'right' },
}

const TEMPLATE = Object.values(LAYOUT)
  .map((entry) => entry.width)
  .join(' ')

const ROW_HEIGHT = 44

const columns = column.columns([
  column.display({
    id: 'select',
    header: ({ table }) => (
      <Checkbox
        checked={table.getIsAllRowsSelected()}
        indeterminate={table.getIsSomeRowsSelected()}
        onChange={table.getToggleAllRowsSelectedHandler()}
        aria-label="Select every loaded alert"
      />
    ),
    cell: ({ row }) => (
      <Checkbox
        checked={row.getIsSelected()}
        onChange={row.getToggleSelectedHandler()}
        aria-label={'Select alert ' + row.original.id}
        // The row is a click target that opens the drawer; the checkbox is not.
        onClick={(event) => event.stopPropagation()}
      />
    ),
  }),

  column.accessor('detected_at', {
    id: 'time',
    header: 'Time',
    cell: (info) => (
      <span className="tabular text-muted-foreground font-mono text-[11.5px]">
        {clockTime(info.getValue())}
      </span>
    ),
  }),

  column.display({
    id: 'flow',
    header: 'Source → destination',
    cell: ({ row }) => {
      const alert = row.original
      return (
        <span className="tabular flex items-center gap-1.5 truncate font-mono text-[11.5px]">
          <span className="text-foreground truncate">{endpoint(alert.src_ip, alert.src_port)}</span>
          <span className="text-subtle-foreground shrink-0" aria-hidden="true">
            →
          </span>
          <span className="text-muted-foreground truncate">
            {endpoint(alert.dst_ip, alert.dst_port)}
          </span>
        </span>
      )
    },
  }),

  column.display({
    id: 'class',
    header: 'Class',
    cell: ({ row }) => <ClassBadge alert={row.original} />,
  }),

  column.accessor('severity', {
    id: 'severity',
    header: 'Severity',
    cell: (info) => <SeverityMark severity={info.getValue()} />,
  }),

  /**
   * The sort key, shown rather than implied.
   *
   * Not in the column list the spec fixes, and added deliberately: a queue
   * ordered by a number the analyst cannot see is a queue whose order looks
   * arbitrary. Reading this column downward is how anyone checks the ordering
   * claim for themselves.
   */
  column.accessor('risk_score', {
    id: 'risk',
    header: 'Risk',
    cell: (info) => (
      <span className="flex items-center gap-2">
        <span
          aria-hidden="true"
          className="bg-raised border-raised-border h-1.5 w-10 overflow-hidden rounded-full border"
        >
          <span
            className="block h-full rounded-full"
            style={{
              width: Math.max(4, info.getValue() * 100) + '%',
              backgroundColor: SEVERITY_COLOR[info.row.original.severity],
            }}
          />
        </span>
        <span className="tabular text-foreground font-mono text-[11.5px]">
          {decimal(info.getValue())}
        </span>
      </span>
    ),
  }),

  column.accessor('latest_verdict', {
    id: 'verdict',
    header: 'Verdict',
    cell: (info) => {
      const verdict = info.getValue()
      if (!verdict) return <span className="text-subtle-foreground">—</span>
      return <Badge variant={VERDICT_VARIANT[verdict]}>{VERDICT_SHORT[verdict]}</Badge>
    },
  }),

  column.accessor('confidence', {
    id: 'confidence',
    header: 'Confidence',
    cell: (info) => {
      const value = info.getValue()
      // Null for a pure anomaly alert: Stage 1 produced no class probability,
      // and a zero here would read as "certainly benign".
      return value === null ? (
        <span className="text-subtle-foreground">—</span>
      ) : (
        <span className="tabular text-muted-foreground font-mono text-[11.5px]">
          {decimal(value)}
        </span>
      )
    },
  }),

  column.accessor('asset_criticality', {
    id: 'criticality',
    header: 'Asset',
    cell: (info) => (
      <span className="text-muted-foreground truncate capitalize">{info.getValue() ?? '—'}</span>
    ),
  }),

  column.accessor('status', {
    id: 'status',
    header: 'Status',
    cell: (info) => <StatusPill status={info.getValue()} />,
  }),

  column.accessor('occurrence_count', {
    id: 'count',
    header: 'Count',
    cell: (info) => {
      const value = info.getValue()
      return (
        <span
          className={cn(
            'tabular font-mono text-[11.5px]',
            value > 1 ? 'text-foreground-strong font-semibold' : 'text-subtle-foreground',
          )}
          title={value > 1 ? value + ' flows collapsed into this alert by dedupe' : undefined}
        >
          {value > 1 ? '×' + count(value) : value}
        </span>
      )
    },
  }),
])

function cellClass(id: string): string {
  const align = LAYOUT[id]?.align
  return cn(
    'flex min-w-0 items-center px-2.5 text-xs',
    align === 'right' && 'justify-end',
    align === 'center' && 'justify-center',
  )
}

export function AlertTable({
  rows,
  selected,
  onSelectedChange,
  activeId,
  onOpen,
  onReachEnd,
  isFetchingMore,
}: {
  rows: AlertSummary[]
  selected: RowSelectionState
  onSelectedChange: (next: RowSelectionState) => void
  activeId: number | null
  onOpen: (id: number) => void
  onReachEnd: () => void
  isFetchingMore: boolean
}) {
  const scroller = useRef<HTMLDivElement>(null)

  const table = useTable({
    features,
    data: rows,
    columns,
    // Keyed by alert id, not row index. Row indices shift the instant the
    // replay inserts a higher-risk alert, which would silently move the
    // analyst's selection onto different alerts between selecting and
    // dismissing.
    getRowId: (row) => String(row.id),
    state: { rowSelection: selected },
    onRowSelectionChange: (updater) =>
      onSelectedChange(typeof updater === 'function' ? updater(selected) : updater),
    enableRowSelection: true,
  })

  const model = table.getRowModel().rows

  const virtualizer = useVirtualizer({
    count: model.length,
    getScrollElement: () => scroller.current,
    estimateSize: () => ROW_HEIGHT,
    overscan: 12,
  })

  const virtualRows = virtualizer.getVirtualItems()
  const last = virtualRows.at(-1)

  // Fetch the next page as the tail comes into view. Reading the virtualiser's
  // last rendered index rather than a scroll offset means this works at any row
  // height and after a filter change shortens the list.
  useEffect(() => {
    if (!last) return
    if (last.index >= model.length - 15 && !isFetchingMore) onReachEnd()
  }, [last, model.length, isFetchingMore, onReachEnd])

  const headerGroups = useMemo(() => table.getHeaderGroups(), [table])

  return (
    <div
      ref={scroller}
      className="scrollbar-thin min-h-0 flex-1 overflow-auto"
      role="region"
      aria-label="Alert queue"
    >
      <div className="min-w-[66rem]">
        <div className="bg-inset border-border sticky top-0 z-10 border-b">
          {headerGroups.map((group) => (
            <div
              key={group.id}
              className="grid h-9 items-center border-l-2 border-l-transparent"
              style={{ gridTemplateColumns: TEMPLATE }}
            >
              {group.headers.map((header) => (
                <div
                  key={header.id}
                  data-column={header.column.id}
                  className={cn(
                    cellClass(header.column.id),
                    'text-muted-foreground text-xs font-medium',
                  )}
                >
                  {header.isPlaceholder
                    ? null
                    : flexRender(header.column.columnDef.header, header.getContext())}
                </div>
              ))}
            </div>
          ))}
        </div>

        <div style={{ height: virtualizer.getTotalSize() }} className="relative">
          {virtualRows.map((virtual) => {
            const row = model[virtual.index]
            const alert = row.original
            const novel = isNovel(alert)
            const active = alert.id === activeId

            return (
              <div
                key={row.id}
                role="button"
                tabIndex={0}
                aria-label={'Open alert ' + alert.id}
                onClick={() => onOpen(alert.id)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault()
                    onOpen(alert.id)
                  }
                }}
                className={cn(
                  'border-divider absolute inset-x-0 grid cursor-pointer items-center border-b',
                  // Every row reserves the accent's width, so a novel row's
                  // columns line up with everyone else's.
                  'border-l-2 border-l-transparent',
                  'hover:bg-hover focus-visible:bg-hover outline-none transition-colors',
                  active && 'bg-hover',
                  // The third mark on a novel row, after the badge colour and
                  // the glyph: an accent the eye finds while scanning.
                  novel && 'border-l-2 border-l-[var(--novel)] pl-0',
                  row.getIsSelected() &&
                    'bg-[color-mix(in_oklab,var(--brand)_9%,transparent)]',
                )}
                style={{
                  height: virtual.size,
                  transform: 'translateY(' + virtual.start + 'px)',
                  gridTemplateColumns: TEMPLATE,
                }}
              >
                {row.getAllCells().map((cell) => (
                  <div
                    key={cell.id}
                    data-column={cell.column.id}
                    className={cellClass(cell.column.id)}
                  >
                    {flexRender(cell.column.columnDef.cell, cell.getContext())}
                  </div>
                ))}
              </div>
            )
          })}
        </div>

        {isFetchingMore ? (
          <p className="text-subtle-foreground py-3 text-center font-mono text-[11px]">
            Loading more…
          </p>
        ) : null}
      </div>
    </div>
  )
}
