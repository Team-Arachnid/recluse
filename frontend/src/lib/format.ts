/**
 * Display formatting. One place, so a timestamp or a rate cannot be rendered
 * two different ways on two screens.
 */

/**
 * Parse an API timestamp, treating an offset-less one as UTC.
 *
 * This is not defensive padding. SQLite stores `DateTime(timezone=True)`
 * naively, so a value that was written as tz-aware UTC comes back without its
 * offset and FastAPI serialises it as `2026-10-06T09:52:30.198500` with no `Z`.
 * `new Date()` reads an offset-less ISO string as *local* time, which would
 * shift every alert in the queue by the viewer's UTC offset -- an hour or
 * thirteen, silently, and only on the SQLite deployment. Appending the `Z`
 * restores what the backend meant rather than guessing at it.
 */
export function parseTimestamp(value: string): Date {
  const hasZone = /(?:Z|[+-]\d{2}:?\d{2})$/.test(value)
  return new Date(hasZone ? value : `${value}Z`)
}

const TIME = new Intl.DateTimeFormat(undefined, {
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hour12: false,
})

const DATE_TIME = new Intl.DateTimeFormat(undefined, {
  day: 'numeric',
  month: 'short',
  hour: '2-digit',
  minute: '2-digit',
  hour12: false,
})

const DAY = new Intl.DateTimeFormat(undefined, { day: 'numeric', month: 'short' })

/** `14:22:07` -- the queue's time column, which is read at a glance. */
export const clockTime = (value: string): string => TIME.format(parseTimestamp(value))

/** `6 Oct 14:22` -- where the day matters, such as a 30-day chart. */
export const dateTime = (value: string): string => DATE_TIME.format(parseTimestamp(value))

export const day = (value: string): string => DAY.format(parseTimestamp(value))

/** `4m ago`, `just now`, `in 2h` -- coarse on purpose; precision lives in the
 *  absolute timestamp beside it. */
export function sinceNow(value: string, now: number = Date.now()): string {
  const seconds = Math.round((now - parseTimestamp(value).getTime()) / 1000)
  const ahead = seconds < 0
  const magnitude = Math.abs(seconds)

  let text: string
  if (magnitude < 45) text = 'just now'
  else if (magnitude < 90) text = 'a minute'
  else if (magnitude < 3600) text = `${Math.round(magnitude / 60)}m`
  else if (magnitude < 86_400) text = `${Math.round(magnitude / 3600)}h`
  else text = `${Math.round(magnitude / 86_400)}d`

  if (text === 'just now') return text
  return ahead ? `in ${text}` : `${text} ago`
}

/** `45s`, `4m 12s`, `1h 07m` -- a span, not a moment. */
export function duration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return '—'
  if (seconds < 60) return `${seconds < 10 ? seconds.toFixed(1) : Math.round(seconds)}s`
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes}m ${String(Math.floor(seconds % 60)).padStart(2, '0')}s`
  const hours = Math.floor(minutes / 60)
  return `${hours}h ${String(minutes % 60).padStart(2, '0')}m`
}

const COMPACT = new Intl.NumberFormat(undefined, { notation: 'compact', maximumFractionDigits: 1 })
const PLAIN = new Intl.NumberFormat()

/**
 * Exact below a million, compact above it.
 *
 * The threshold is high on purpose. Every count in this application is evidence
 * -- rows of support, flows missed, alerts raised -- and 47,379 missed is a
 * different statement from "47K missed" when the next row says 1,906. Compacting
 * only starts where a number has stopped being something anyone reads digit by
 * digit and become a shape.
 */
export function count(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—'
  return Math.abs(value) < 1_000_000 ? PLAIN.format(value) : COMPACT.format(value)
}

export function decimal(value: number | null | undefined, places = 2): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—'
  return value.toFixed(places)
}

/** `6.0%`. Takes a share in [0, 1], not an already-multiplied percentage. */
export function percent(share: number | null | undefined, places = 1): string {
  if (share === null || share === undefined || !Number.isFinite(share)) return '—'
  return `${(share * 100).toFixed(places)}%`
}

/**
 * A rate or probability small enough that a fixed number of decimals would
 * render it as `0.00`.
 *
 * The configured false-positive budget is 3.2e-4, and a screen that showed it
 * as 0.0003 would make the two orders of magnitude between a passing and a
 * failing threshold invisible.
 */
export function smallNumber(value: number | null | undefined, places = 3): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—'
  if (value === 0) return '0'
  return Math.abs(value) < 0.001 ? value.toExponential(2) : value.toFixed(places)
}

/** `10.0.0.5:443`, or the bare address when there is no port. */
export function endpoint(address: string, port: number | null | undefined): string {
  return port === null || port === undefined ? address : `${address}:${port}`
}

/** `dst_port_nunique` -> `dst port nunique`, for prose. Feature names stay
 *  verbatim and monospaced wherever they are the evidence rather than a label. */
export const humanise = (name: string): string => name.replace(/_/g, ' ')

/** `brute_force` -> `Brute force`. */
export const sentenceCase = (name: string): string => {
  const words = humanise(name)
  return words.charAt(0).toUpperCase() + words.slice(1)
}
