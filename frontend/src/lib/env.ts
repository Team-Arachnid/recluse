/**
 * Typed access to the Vite environment.
 *
 * Every value has a default here and nowhere else, so no component contains a
 * literal port, path or interval.
 */

const DEFAULTS = {
  apiBaseUrl: '/api/v1',
  healthPollMs: 5000,
  statsPollMs: 10_000,
  replayPollMs: 2000,
  thresholdDebounceMs: 150,
  tickerRows: 60,
  queuePageSize: 100,
} as const

function positiveInt(raw: string | undefined, fallback: number): number {
  const parsed = Number(raw)
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback
}

export const env = {
  /** Prefix for every API request. Relative by default so the dev proxy and
   *  the production reverse proxy both work without a rebuild. */
  apiBaseUrl: import.meta.env.VITE_API_BASE_URL ?? DEFAULTS.apiBaseUrl,

  healthPollMs: positiveInt(import.meta.env.VITE_HEALTH_POLL_MS, DEFAULTS.healthPollMs),

  /** How often the aggregate panels re-ask. Slower than health on purpose:
   *  these are counts over the whole table, and the live feed is the SSE
   *  stream rather than a poll. */
  statsPollMs: positiveInt(import.meta.env.VITE_STATS_POLL_MS, DEFAULTS.statsPollMs),

  /** The replay status poll. Fast, because it backs a control the analyst just
   *  clicked and a stale answer there reads as a control that did nothing. */
  replayPollMs: positiveInt(import.meta.env.VITE_REPLAY_POLL_MS, DEFAULTS.replayPollMs),

  /**
   * Debounce on the threshold slider's projection request.
   *
   * A drag fires pointer events at display refresh rate. Without this, a
   * two-second drag issues a hundred-odd requests, the responses arrive out of
   * order and the projected figure flickers between stale values. At 150ms the
   * line still follows the pointer because it is driven by local state, while
   * the network sees roughly six requests over the same drag.
   */
  thresholdDebounceMs: positiveInt(
    import.meta.env.VITE_THRESHOLD_DEBOUNCE_MS,
    DEFAULTS.thresholdDebounceMs,
  ),

  /** How many rows the live ticker keeps. Bounded because a 100x replay would
   *  otherwise grow the list until the tab dies. */
  tickerRows: positiveInt(import.meta.env.VITE_TICKER_ROWS, DEFAULTS.tickerRows),

  queuePageSize: positiveInt(import.meta.env.VITE_QUEUE_PAGE_SIZE, DEFAULTS.queuePageSize),
} as const
