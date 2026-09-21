import { QueryClient } from '@tanstack/react-query'

import { ApiError } from './client'

/**
 * All server state goes through TanStack Query. No useEffect fetch chains.
 */
export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        // A 501 means a later phase has not been built yet. Retrying it is
        // pointless and only delays the message the UI wants to show.
        retry: (failureCount, error) => {
          if (error instanceof ApiError && error.isNotImplemented) return false
          if (error instanceof ApiError && error.status < 500) return false
          return failureCount < 2
        },
        refetchOnWindowFocus: true,
        staleTime: 2_000,
      },
    },
  })
}
