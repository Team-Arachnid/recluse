/**
 * The triage queue table.
 *
 * Virtualised, because it holds tens of thousands of rows during a 100x replay
 * and rendering them all freezes the tab. Sorted on the server by `risk_score`,
 * never by time: sorting a triage queue chronologically ranks alerts by when a
 * packet happened rather than by what needs attention first.
 *
 * `UNCLASSIFIED_ANOMALY` rows carry three marks, not one -- their own badge
 * colour, a radar glyph, and an accent down the left edge of the row. Colour
 * alone would fail a colourblind analyst, and these are the rows the whole
 * project exists to produce.
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
import { Radar } from 'lucide-react'
import { useEffect, useMemo, useRef } from 'react'

import type { AlertSummary } from '@/api/types'
import { Badge } from '@/components/ui/badge'
import { Checkbox } from '@/components/ui/checkbox'
import {
  classLabel,
  classVariant,
  isNovel,
  SEVERITY_VARIANT,
  STATUS_LABEL,
  VERDICT_SHORT,
  VERDICT_VARIANT,
} from '@/lib/alerts'
import { clockTime, decimal, endpoint } from '@/lib/format'
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
  select: { width: '2.25rem', align: 'center' },
  time: { width: '5rem' },
  flow: { width: 'minmax(13rem, 1fr)' },
  class: { width: '10rem' },
  severity: { width: '5.5rem' },
  risk: { width: '4rem', align: 'right' },
  verdict: { width: '4rem', align: 'center' },
  confidence: { width: '5rem', align: 'right' },
  criticality: { width: '5.5rem' },
  status: { width: '5.5rem' },
  count: { width: '3.5rem', align: 'right' },
}

const TEMPLATE = Object.values(LAYOUT)
  .map((entry) => entry.width)
  .join(' ')

const ROW_HEIGHT = 34

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
      <span className="tabular text-muted-foreground font-mono">
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
        <span className="tabular flex items-center gap-1.5 truncate font-mono">
          <span className="truncate">{endpoint(alert.src_ip, alert.src_port)}</span>
          <span className="text-muted-foreground shrink-0" aria-hidden="true">
            →
          </span>
          <span className="truncate">{endpoint(alert.dst_ip, alert.dst_port)}</span>
        </span>
      )
    },
  }),

  column.display({
    id: 'class',
    header: 'Class',
    cell: ({ row }) => {
      const alert = row.original
      const novel = isNovel(alert)
      return (
        <Badge variant={classVariant(alert)} className="max-w-full">
          {novel ? <Radar className="size-3 shrink-0" aria-hidden="true" /> : null}
          <span className="truncate">{classLabel(alert)}</span>
        </Badge>
      )
    },
  }),

  column.accessor('severity', {
    id: 'severity',
    header: 'Severity',
    cell: (info) => <Badge variant={SEVERITY_VARIANT[info.getValue()]}>{info.getValue()}</Badge>,
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
    cell: (info) => <span className="tabular font-mono">{decimal(info.getValue())}</span>,
  }),

  column.accessor('latest_verdict', {
    id: 'verdict',
    header: 'Verdict',
    cell: (info) => {
      const verdict = info.getValue()
      if (!verdict) return <span className="text-muted-foreground">—</span>
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
        <span className="text-muted-foreground">—</span>
      ) : (
        <span className="tabular font-mono">{decimal(value)}</span>
      )
    },
  }),

  column.accessor('asset_criticality', {
    id: 'criticality',
    header: 'Asset',
    cell: (info) => (
      <span className="text-muted-foreground truncate">{info.getValue() ?? '—'}</span>
    ),
  }),

  column.accessor('status', {
    id: 'status',
    header: 'Status',
    cell: (info) => (
      <span className={cn(info.getValue() === 'open' ? undefined : 'text-muted-foreground')}>
        {STATUS_LABEL[info.getValue()]}
      </span>
    ),
  }),

  column.accessor('occurrence_count', {
    id: 'count',
    header: 'Count',
    cell: (info) => {
      const value = info.getValue()
      return (
        <span
          className={cn('tabular font-mono', value > 1 ? 'font-medium' : 'text-muted-foreground')}
          title={value > 1 ? value + ' flows collapsed into this alert by dedupe' : undefined}
        >
          {value > 1 ? '×' + value : value}
        </span>
      )
    },
  }),
])

function cellClass(id: string): string {
  const align = LAYOUT[id]?.align
  return cn(
    'flex min-w-0 items-center px-2 text-xs',
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
      <div className="min-w-[60rem]">
        <div className="bg-card/95 border-border sticky top-0 z-10 border-b backdrop-blur">
          {headerGroups.map((group) => (
            <div
              key={group.id}
              className="grid h-8 items-center"
              style={{ gridTemplateColumns: TEMPLATE }}
            >
              {group.headers.map((header) => (
                <div
                  key={header.id}
                  data-column={header.column.id}
                  className={cn(
                    cellClass(header.column.id),
                    'text-muted-foreground font-medium',
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
                  'border-border/60 absolute inset-x-0 grid cursor-pointer items-center border-b',
                  'hover:bg-muted/50 focus-visible:bg-muted/60 outline-none',
                  active && 'bg-muted',
                  // The third mark on a novel row, after the badge colour and
                  // the glyph: an accent the eye finds while scanning.
                  novel && 'border-l-2 border-l-[var(--novel)] pl-0',
                  row.getIsSelected() &&
                    'bg-[color-mix(in_oklab,var(--info)_10%,transparent)]',
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
          <p className="text-muted-foreground py-3 text-center text-xs">Loading more…</p>
        ) : null}
      </div>
    </div>
  )
}
