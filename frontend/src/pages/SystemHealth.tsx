import { ShieldAlert } from 'lucide-react'

import { ScreenBody } from '@/components/AppShell'
import { ErrorBoundary } from '@/components/ErrorBoundary'
import { HealthPanel } from '@/components/HealthPanel'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'

/**
 * The build phases, with what each one actually produced.
 *
 * Kept because it is the fastest honest answer to "what works and what doesn't"
 * — and because two screens genuinely depend on endpoints that do not exist yet,
 * so the state of the build is user-visible information rather than project
 * trivia.
 */
const PHASES = [
  { id: 0, name: 'Scaffolding', state: 'done' },
  { id: 1, name: 'Data and features', state: 'done' },
  { id: 2, name: 'Supervised classifier', state: 'done' },
  { id: 3, name: 'Benign-only autoencoder', state: 'done' },
  { id: 4, name: 'Fusion and leave-one-attack-out', state: 'done' },
  { id: 5, name: 'Backend API', state: 'done' },
  { id: 6, name: 'Dashboard', state: 'current' },
  { id: 7, name: 'Drift and active learning', state: 'pending' },
  { id: 8, name: 'Packaging', state: 'pending' },
  { id: 9, name: 'Live capture', state: 'pending' },
] as const

/**
 * System health.
 *
 * No longer the landing page. From Phase 6 the landing page is the triage queue
 * — analysts live in the queue, so the queue is home — and this became what it
 * always described itself as: a status view, reached from the nav.
 */
export function SystemHealth() {
  return (
    <ScreenBody
      title="System"
      lede={
        <>
          Two-stage network intrusion detection. Stage 1 names the attacks it was trained on; Stage
          2 flags traffic that does not look like normal, including families it has never seen. The
          system alerts, ranks and explains — it never blocks traffic.
        </>
      }
      actions={
        <Badge variant="novel">
          <ShieldAlert className="size-3" aria-hidden="true" />
          Phase 6
        </Badge>
      }
    >
      <div className="grid gap-6 md:grid-cols-[minmax(0,24rem)_minmax(0,1fr)]">
        <ErrorBoundary label="Health panel">
          <HealthPanel />
        </ErrorBoundary>

        <Card>
          <CardHeader>
            <CardTitle>Build progress</CardTitle>
          </CardHeader>
          <CardContent>
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
                  ) : phase.state === 'done' ? (
                    <span className="text-[var(--ok)]" aria-label="complete">
                      ✓
                    </span>
                  ) : null}
                </li>
              ))}
            </ol>

            <p className="text-muted-foreground mt-4 text-xs leading-relaxed">
              The Drift screen&rsquo;s per-feature PSI and the model registry are served by
              endpoints that answer 501 until Phase 7. Those panels say so rather than rendering a
              plausible chart — a drift monitor showing an invented flat line would tell its reader
              everything is fine.
            </p>
          </CardContent>
        </Card>
      </div>
    </ScreenBody>
  )
}
