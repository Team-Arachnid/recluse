import { QueryClientProvider } from '@tanstack/react-query'
import { useState } from 'react'

import { createQueryClient } from '@/api/queryClient'
import { ErrorBoundary } from '@/components/ErrorBoundary'
import { SystemHealth } from '@/pages/SystemHealth'

export function App() {
  // Created in state so the client survives re-renders but is still per-app,
  // which keeps tests isolated from one another.
  const [queryClient] = useState(createQueryClient)

  return (
    <QueryClientProvider client={queryClient}>
      <ErrorBoundary label="Application">
        <SystemHealth />
      </ErrorBoundary>
    </QueryClientProvider>
  )
}
