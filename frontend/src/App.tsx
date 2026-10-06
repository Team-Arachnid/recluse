import { QueryClientProvider } from '@tanstack/react-query'
import { useState } from 'react'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'

import { createQueryClient } from '@/api/queryClient'
import { StreamProvider } from '@/api/stream'
import { AppShell } from '@/components/AppShell'
import { ErrorBoundary } from '@/components/ErrorBoundary'
import { Analytics } from '@/pages/Analytics'
import { DriftMonitor } from '@/pages/DriftMonitor'
import { FeedbackLoopScreen } from '@/pages/FeedbackLoop'
import { LiveMonitor } from '@/pages/LiveMonitor'
import { ModelPerformance } from '@/pages/ModelPerformance'
import { SystemHealth } from '@/pages/SystemHealth'
import { TriageQueue } from '@/pages/TriageQueue'

/**
 * Each screen gets its own boundary, inside the shell rather than around it.
 *
 * A render error on the drift screen must not take the navigation with it --
 * being able to click away from a broken panel is the difference between a bug
 * and an outage.
 */
function Screen({ label, children }: { label: string; children: React.ReactNode }) {
  return <ErrorBoundary label={label}>{children}</ErrorBoundary>
}

export function App() {
  // Created in state so the client survives re-renders but is still per-app,
  // which keeps tests isolated from one another.
  const [queryClient] = useState(createQueryClient)

  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <StreamProvider>
          <AppShell>
            <Routes>
              {/* The queue is the landing page. Analysts live in the queue, so
                  the queue is home -- not an overview dashboard they click
                  past a few hundred times a shift. */}
              <Route
                path="/"
                element={
                  <Screen label="Triage queue">
                    <TriageQueue />
                  </Screen>
                }
              />
              <Route
                path="/live"
                element={
                  <Screen label="Live traffic monitor">
                    <LiveMonitor />
                  </Screen>
                }
              />
              <Route
                path="/model"
                element={
                  <Screen label="Model performance">
                    <ModelPerformance />
                  </Screen>
                }
              />
              <Route
                path="/drift"
                element={
                  <Screen label="Drift monitor">
                    <DriftMonitor />
                  </Screen>
                }
              />
              <Route
                path="/feedback"
                element={
                  <Screen label="Feedback loop">
                    <FeedbackLoopScreen />
                  </Screen>
                }
              />
              <Route
                path="/analytics"
                element={
                  <Screen label="Analytics">
                    <Analytics />
                  </Screen>
                }
              />
              <Route
                path="/system"
                element={
                  <Screen label="System health">
                    <SystemHealth />
                  </Screen>
                }
              />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          </AppShell>
        </StreamProvider>
      </BrowserRouter>
    </QueryClientProvider>
  )
}
