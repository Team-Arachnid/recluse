/**
 * The live alert feed: one `EventSource` for the whole application.
 *
 * A subscription rather than a query, which is why it does not live in
 * `queries.ts`. It is a provider rather than a hook so there is exactly one
 * connection no matter how many screens are reading from it -- two components
 * each calling `new EventSource` would open two streams, and the backend would
 * fan every alert out twice.
 *
 * The connection is opened only while a traffic source is actually running.
 * `GET /stream` answers 503 when none is, and `EventSource` responds to a 503
 * by reconnecting forever on a short timer: a dashboard left open with no
 * replay would quietly hammer the endpoint for the rest of the day. Reading
 * `GET /replay/status` first costs one poll and avoids that entirely.
 */
import { useQueryClient } from '@tanstack/react-query'
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react'

import { env } from '@/lib/env'

import { queryKeys } from './queries'
import type { AlertEvent, AlertPage } from './types'

/** One second of the rate series. */
export interface RateSample {
  /** Seconds since the feed was first observed, so the axis is a duration. */
  second: number
  alerts: number
  flows: number
}

interface StreamState {
  /** Most recent first. Bounded by `env.tickerRows`: a 100x replay would
   *  otherwise grow this list until the tab dies. */
  events: AlertEvent[]
  /** Alerts seen on this connection, which is not the same as alerts in the
   *  database -- the stream starts when it connects. */
  received: number
  connected: boolean
  /** Null until a frame has arrived. A heartbeat counts: it is the proof the
   *  connection is alive rather than stalled. */
  lastFrameAt: number | null
  rates: RateSample[]
  /** Whether the queue follows the feed. Paused is not "disconnected": events
   *  keep arriving and the ticker keeps moving, the table just holds still. */
  following: boolean
  setFollowing: (following: boolean) => void
}

const StreamContext = createContext<StreamState | null>(null)

/** How many seconds of rate history the sparklines keep. */
const RATE_WINDOW = 60

/**
 * How often arrivals are allowed to refresh the queue.
 *
 * Not per event: at 100x the stream can deliver bursts, and one invalidation
 * per alert would put the table into a refetch loop. Not merged blindly into
 * the cache either -- an `alert` frame is deliberately narrower than the row
 * the queue renders (no status, no confidence, no asset criticality), so
 * building a row from one would mean inventing the missing half. The honest
 * version is to update in place what the frame genuinely carries and let a
 * coalesced refetch bring in rows that are new.
 */
const QUEUE_REFRESH_MS = 1_500

export function StreamProvider({ children }: { children: ReactNode }) {
  const client = useQueryClient()
  const [events, setEvents] = useState<AlertEvent[]>([])
  const [received, setReceived] = useState(0)
  const [connected, setConnected] = useState(false)
  const [lastFrameAt, setLastFrameAt] = useState<number | null>(null)
  const [rates, setRates] = useState<RateSample[]>([])
  const [following, setFollowing] = useState(true)

  // Counters the one-second timer drains, rather than state the handlers set:
  // a burst of thirty frames inside one tick would otherwise be thirty
  // renders, and the sparkline only needs the total.
  const sinceTick = useRef({ alerts: 0 })
  const dirty = useRef(false)
  const followingRef = useRef(following)
  followingRef.current = following

  /**
   * Fold one frame into the cached queue.
   *
   * Only rows the queue already holds are touched, and only with fields the
   * frame actually carries. The common case this exists for is dedupe: a
   * compromised host emitting five thousand flows is one row whose
   * `occurrence_count` climbs, and watching that number rise without the table
   * reloading is the clearest evidence on screen that dedupe is working.
   */
  const mergeIntoQueue = useCallback(
    (event: AlertEvent) => {
      const matches = client.getQueriesData<{ pages: AlertPage[]; pageParams: unknown[] }>({
        queryKey: [...queryKeys.alerts, 'list'],
      })

      for (const [key, data] of matches) {
        if (!data) continue
        let touched = false
        const pages = data.pages.map((page) => ({
          ...page,
          items: page.items.map((item) => {
            if (item.id !== event.id) return item
            touched = true
            return {
              ...item,
              occurrence_count: event.occurrence_count,
              risk_score: event.risk_score,
              anomaly_score: event.anomaly_score,
              severity: event.severity,
            }
          }),
        }))
        if (touched) client.setQueryData(key, { ...data, pages })
      }
    },
    [client],
  )

  useEffect(() => {
    let source: EventSource | null = null
    let cancelled = false

    /**
     * Connect only once a traffic source is live, then stay connected.
     *
     * The probe is a plain fetch rather than the `useReplayStatus` query so
     * this effect does not re-run on every poll -- re-running it would tear the
     * connection down and build it up again twice a second, and each rebuild
     * loses the ticker's place.
     */
    async function connectWhenSourceIsLive(): Promise<void> {
      while (!cancelled) {
        try {
          // A replay or a live capture: either one feeds the stream.
          const replay = await fetch(env.apiBaseUrl + '/replay/status')
          if (replay.ok && (await replay.json()).running) break
          const capture = await fetch(env.apiBaseUrl + '/ingest/status')
          if (capture.ok && (await capture.json()).running) break
        } catch {
          // Backend down. The health panel is what reports that; retrying
          // quietly here is the right behaviour for a feed.
        }
        await new Promise((resolve) => setTimeout(resolve, env.replayPollMs))
      }
      if (cancelled) return

      source = new EventSource(env.apiBaseUrl + '/stream')

      source.addEventListener('open', () => setConnected(true))

      source.addEventListener('alert', (frame) => {
        const event = JSON.parse((frame as MessageEvent<string>).data) as AlertEvent
        setConnected(true)
        setLastFrameAt(Date.now())
        setReceived((total) => total + 1)
        setEvents((current) => [event, ...current].slice(0, env.tickerRows))
        sinceTick.current.alerts += 1
        mergeIntoQueue(event)
        if (followingRef.current) dirty.current = true
      })

      source.addEventListener('heartbeat', () => {
        setConnected(true)
        setLastFrameAt(Date.now())
      })

      source.addEventListener('error', () => {
        // EventSource reconnects on its own; this only reports the gap so the
        // screen can say "reconnecting" instead of looking frozen.
        setConnected(false)
      })
    }

    void connectWhenSourceIsLive()

    return () => {
      cancelled = true
      source?.close()
      setConnected(false)
    }
  }, [mergeIntoQueue])

  // One timer drives both the rate series and the coalesced queue refresh.
  useEffect(() => {
    const started = Date.now()

    const timer = setInterval(() => {
      const alerts = sinceTick.current.alerts
      sinceTick.current.alerts = 0

      setRates((current) => {
        const second = Math.round((Date.now() - started) / 1000)
        // `flows` is filled in by the replay poll, which is the only thing that
        // knows how many rows were scored; the stream carries alerts alone.
        const next = [...current, { second, alerts, flows: 0 }]
        return next.slice(-RATE_WINDOW)
      })
    }, 1_000)

    const refresh = setInterval(() => {
      if (!dirty.current) return
      dirty.current = false
      void client.invalidateQueries({ queryKey: [...queryKeys.alerts, 'list'] })
      void client.invalidateQueries({ queryKey: queryKeys.queueStats })
    }, QUEUE_REFRESH_MS)

    return () => {
      clearInterval(timer)
      clearInterval(refresh)
    }
  }, [client])

  const value = useMemo<StreamState>(
    () => ({ events, received, connected, lastFrameAt, rates, following, setFollowing }),
    [events, received, connected, lastFrameAt, rates, following],
  )

  return <StreamContext.Provider value={value}>{children}</StreamContext.Provider>
}

export function useAlertStream(): StreamState {
  const value = useContext(StreamContext)
  if (!value) throw new Error('useAlertStream must be used inside <StreamProvider>')
  return value
}
