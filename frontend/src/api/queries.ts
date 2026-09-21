import { useQuery } from '@tanstack/react-query'

import { env } from '@/lib/env'

import { request } from './client'
import type { HealthResponse } from './types'

/** Query keys in one place so invalidation cannot go stale. */
export const queryKeys = {
  health: ['health'] as const,
}

/**
 * Poll the backend health endpoint.
 *
 * The interval comes from VITE_HEALTH_POLL_MS. `uptime_s` advancing between
 * polls is the visible proof that the number is coming from the live process
 * rather than a cached response.
 */
export function useHealth() {
  return useQuery({
    queryKey: queryKeys.health,
    queryFn: () => request<HealthResponse>('/health'),
    refetchInterval: env.healthPollMs,
    staleTime: 0,
  })
}
