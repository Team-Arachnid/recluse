/**
 * Every server call the dashboard makes, as TanStack Query hooks.
 *
 * All server state goes through here. No `useEffect` fetch chains: they produce
 * race conditions, double fetches under StrictMode, and a cache-less refetch on
 * every mount. The one place that touches the network outside this file is
 * `stream.ts`, because an `EventSource` is a subscription rather than a query --
 * and even that writes its events into this cache rather than into component
 * state.
 */
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { env } from '@/lib/env'

import { request } from './client'
import type {
  AlertDetail,
  AlertPage,
  AlertStatusResult,
  AlertStatusUpdate,
  AlertSummary,
  AnalyticsRange,
  AnalyticsSummary,
  AnomalyHistogram,
  FeedbackLoop,
  HealthResponse,
  MitreCoverage,
  ModelMetrics,
  QueueStats,
  ReplaySpeed,
  ReplayStatus,
  ThresholdProjection,
  VerdictRequest,
  VerdictResponse,
} from './types'

/** The filters the queue sends up. Every one is optional and they compose. */
export interface AlertFilters {
  severity?: string
  kind?: string
  family?: string
  status?: string
  /** A verdict, or 'none' for alerts nobody has judged yet. */
  verdict?: string
  since?: string
}

/**
 * Query keys in one object, so an invalidation cannot go stale.
 *
 * `alerts` is the prefix every alert-shaped query shares, which is what lets a
 * verdict invalidate the queue, the open drawer and the stat strip in one call
 * instead of three that can drift apart.
 */
export const queryKeys = {
  health: ['health'] as const,
  alerts: ['alerts'] as const,
  alertList: (filters: AlertFilters) => ['alerts', 'list', filters] as const,
  alert: (id: number) => ['alerts', 'detail', id] as const,
  related: (id: number) => ['alerts', 'related', id] as const,
  queueStats: ['alerts', 'stats'] as const,
  modelMetrics: ['metrics', 'model'] as const,
  threshold: (t: number) => ['metrics', 'threshold', t] as const,
  anomalyHistogram: ['metrics', 'anomaly-histogram'] as const,
  drift: ['metrics', 'drift'] as const,
  registry: ['models'] as const,
  analytics: ['analytics'] as const,
  analyticsSummary: (range: AnalyticsRange) => ['analytics', 'summary', range] as const,
  mitre: ['analytics', 'mitre-coverage'] as const,
  feedback: ['analytics', 'feedback'] as const,
  replay: ['replay', 'status'] as const,
} as const

function search(params: Record<string, string | number | undefined>): string {
  const query = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== '') query.set(key, String(value))
  }
  const rendered = query.toString()
  return rendered ? '?' + rendered : ''
}

// ---------------------------------------------------------------------------
// System
// ---------------------------------------------------------------------------

/** Poll backend health. `uptime_s` advancing between polls is the visible proof
 *  the number comes from the live process rather than a cached response. */
export function useHealth() {
  return useQuery({
    queryKey: queryKeys.health,
    queryFn: () => request<HealthResponse>('/health'),
    refetchInterval: env.healthPollMs,
    staleTime: 0,
  })
}

// ---------------------------------------------------------------------------
// The queue
// ---------------------------------------------------------------------------

/**
 * The triage queue, cursor-paginated and ordered by risk on the server.
 *
 * `useInfiniteQuery` because the server pages on a keyset cursor rather than an
 * offset: a page means "the next rows after this exact position", which is what
 * keeps paging stable while a replay inserts higher-risk alerts between two
 * requests. An offset pager would re-show and skip rows under exactly the load
 * this screen is built for.
 */
export function useAlerts(filters: AlertFilters, limit = env.queuePageSize) {
  return useInfiniteQuery({
    queryKey: queryKeys.alertList(filters),
    queryFn: ({ pageParam }) =>
      request<AlertPage>('/alerts' + search({ ...filters, limit, cursor: pageParam })),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (page) => page.next_cursor ?? undefined,
    // The queue is read while it is being written to, so a long stale window
    // would show a shift that has moved on.
    staleTime: 1_000,
  })
}

export function useQueueStats() {
  return useQuery({
    queryKey: queryKeys.queueStats,
    queryFn: () => request<QueueStats>('/alerts/stats'),
    refetchInterval: env.statsPollMs,
  })
}

export function useAlert(id: number | null) {
  return useQuery({
    queryKey: queryKeys.alert(id ?? -1),
    queryFn: () => request<AlertDetail>('/alerts/' + id),
    enabled: id !== null,
  })
}

/** Other alerts from the same source host, so a scan-then-exploit sequence
 *  reads as one story instead of three disconnected rows. */
export function useRelatedAlerts(id: number | null, windowHours = 24) {
  return useQuery({
    queryKey: queryKeys.related(id ?? -1),
    queryFn: () =>
      request<AlertSummary[]>(
        '/alerts/' + id + '/related' + search({ window_hours: windowHours }),
      ),
    enabled: id !== null,
  })
}

/**
 * Record an analyst verdict.
 *
 * The invalidation is the point of the hook: the queue behind the drawer
 * updates itself rather than showing a row the analyst just judged, and the
 * strip's unjudged count moves with it. Feedback and analytics go too, because
 * this click is the only thing that changes either of them.
 */
export function useSubmitVerdict(alertId: number) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (body: VerdictRequest) =>
      request<VerdictResponse>('/alerts/' + alertId + '/verdict', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: queryKeys.alerts })
      void client.invalidateQueries({ queryKey: queryKeys.analytics })
    },
  })
}

/** The bulk triage action behind "Dismiss selected". */
export function useUpdateAlertStatus() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (body: AlertStatusUpdate) =>
      request<AlertStatusResult>('/alerts/status', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: queryKeys.alerts })
      void client.invalidateQueries({ queryKey: queryKeys.analytics })
    },
  })
}

// ---------------------------------------------------------------------------
// Measured model performance. Nothing here moves when a replay runs.
// ---------------------------------------------------------------------------

export function useModelMetrics() {
  return useQuery({
    queryKey: queryKeys.modelMetrics,
    queryFn: () => request<ModelMetrics>('/metrics/model'),
    // Read from artifacts the backend loads once at startup, so refetching
    // cannot produce a different answer until the process restarts.
    staleTime: Infinity,
  })
}

export function useAnomalyHistogram() {
  return useQuery({
    queryKey: queryKeys.anomalyHistogram,
    queryFn: () => request<AnomalyHistogram>('/metrics/anomaly-histogram'),
    staleTime: Infinity,
  })
}

/**
 * Project alert volume at a candidate threshold.
 *
 * `enabled` plus a debounced `t` is what makes the drag feel immediate: the
 * line follows the pointer from local state and this query is only issued for
 * the settled value. `placeholderData` holds the previous projection while the
 * next is in flight, so the figure dims rather than collapsing to a skeleton
 * halfway through a drag.
 */
export function useThresholdProjection(t: number | null) {
  return useQuery({
    queryKey: queryKeys.threshold(t ?? -1),
    queryFn: () => request<ThresholdProjection>('/metrics/threshold' + search({ t: t as number })),
    enabled: t !== null,
    staleTime: Infinity,
    placeholderData: (previous) => previous,
  })
}

/** Phase 7's two endpoints. Both answer 501 today, and the screens report that
 *  from the response body rather than from a hardcoded sentence. */
export function useDrift() {
  return useQuery({
    queryKey: queryKeys.drift,
    queryFn: () => request<unknown>('/metrics/drift'),
    staleTime: Infinity,
  })
}

export function useModelRegistry() {
  return useQuery({
    queryKey: queryKeys.registry,
    queryFn: () => request<unknown>('/models'),
    staleTime: Infinity,
  })
}

// ---------------------------------------------------------------------------
// What this deployment has seen
// ---------------------------------------------------------------------------

export function useAnalytics(range: AnalyticsRange) {
  return useQuery({
    queryKey: queryKeys.analyticsSummary(range),
    queryFn: () => request<AnalyticsSummary>('/analytics/summary' + search({ range })),
    refetchInterval: env.statsPollMs,
  })
}

export function useMitreCoverage() {
  return useQuery({
    queryKey: queryKeys.mitre,
    queryFn: () => request<MitreCoverage>('/analytics/mitre-coverage'),
    refetchInterval: env.statsPollMs,
  })
}

export function useFeedbackLoop() {
  return useQuery({
    queryKey: queryKeys.feedback,
    queryFn: () => request<FeedbackLoop>('/analytics/feedback'),
    refetchInterval: env.statsPollMs,
  })
}

// ---------------------------------------------------------------------------
// The traffic source
// ---------------------------------------------------------------------------

export function useReplayStatus() {
  return useQuery({
    queryKey: queryKeys.replay,
    queryFn: () => request<ReplayStatus>('/replay/status'),
    refetchInterval: env.replayPollMs,
  })
}

/**
 * Start and stop the replay.
 *
 * There is no "change speed" call, and the control does not pretend there is:
 * `replay.py` fixes the inter-batch gap from `speed` when the run starts, so
 * changing it means stopping and starting again. A slider that silently failed
 * to take effect would leave the control and the engine disagreeing about what
 * the demo is doing.
 */
export function useReplayControl() {
  const client = useQueryClient()

  const settle = () => {
    void client.invalidateQueries({ queryKey: queryKeys.replay })
    void client.invalidateQueries({ queryKey: queryKeys.alerts })
  }

  const start = useMutation({
    mutationFn: (body: { speed: ReplaySpeed; dataset: string }) =>
      request<ReplayStatus>('/replay/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      }),
    onSettled: settle,
  })

  const stop = useMutation({
    mutationFn: () => request<ReplayStatus>('/replay/stop', { method: 'POST' }),
    onSettled: settle,
  })

  return { start, stop }
}
