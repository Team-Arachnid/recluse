import { ShieldAlert } from 'lucide-react'

import { ErrorBoundary } from '@/components/ErrorBoundary'
import { HealthPanel } from '@/components/HealthPanel'
import { Badge } from '@/components/ui/badge'

const PHASES = [
  { id: 0, name: 'Scaffolding', state: 'current' },
  { id: 1, name: 'Data + features', state: 'pending' },
  { id: 2, name: 'Supervised model', state: 'pending' },
  { id: 3, name: 'Anomaly model', state: 'pending' },
  { id: 4, name: 'Fusion + LOAO', state: 'pending' },
  { id: 5, name: 'Backend API', state: 'pending' },
  { id: 6, name: 'Frontend', state: 'pending' },
  { id: 7, name: 'Drift + feedback', state: 'pending' },
  { id: 8, name: 'Packaging', state: 'pending' },
  { id: 9, name: 'Real traffic', state: 'pending' },
] as const

/**
 * The Phase 0 landing page.
 *
 * Temporary by design. From Phase 6 the landing page is the triage queue --
 * analysts live in the queue, so the queue is home, and this shell is replaced
 * rather than promoted to an overview dashboard.
 */
export function SystemHealth() {
  return (
    <main className="mx-auto w-full max-w-5xl px-4 py-10 sm:px-6">
      <header className="mb-8">
        <div className="flex items-center gap-2">
          <ShieldAlert
            className="size-5 text-[var(--novel)]"
            aria-hidden="true"
          />
          <h1 className="text-lg font-semibold tracking-tight">Recluse</h1>
          <Badge variant="novel">Phase 0</Badge>
        </div>
        <p className="text-muted-foreground mt-2 max-w-2xl text-sm">
          Two-stage network intrusion detection. Stage 1 names the attacks it
          was trained on; Stage 2 flags traffic that does not look like normal,
          including families it has never seen. The system alerts, ranks and
          explains — it never blocks traffic.
        </p>
      </header>

      <div className="grid gap-6 md:grid-cols-[minmax(0,24rem)_minmax(0,1fr)]">
        <ErrorBoundary label="Health panel">
          <HealthPanel />
        </ErrorBoundary>

        <section aria-labelledby="build-progress">
          <h2
            id="build-progress"
            className="text-muted-foreground mb-3 text-xs tracking-wide uppercase"
          >
            Build progress
          </h2>
          <ol className="grid gap-1.5 sm:grid-cols-2">
            {PHASES.map((phase) => (
              <li
                key={phase.id}
                className="border-border flex items-center gap-3 rounded-md border px-3 py-2"
                data-state={phase.state}
              >
                <span className="text-muted-foreground tabular font-mono text-xs">
                  {String(phase.id).padStart(2, '0')}
                </span>
                <span className="flex-1 truncate text-sm">{phase.name}</span>
                {phase.state === 'current' ? (
                  <Badge variant="info">current</Badge>
                ) : null}
              </li>
            ))}
          </ol>
        </section>
      </div>
    </main>
  )
}
