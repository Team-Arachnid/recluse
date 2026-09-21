import { Activity, RefreshCw, ServerCrash } from 'lucide-react'

import { useHealth } from '@/api/queries'
import { Badge } from '@/components/ui/badge'
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { env } from '@/lib/env'

function Field({
  term,
  children,
}: {
  term: string
  children: React.ReactNode
}) {
  return (
    <div className="border-border flex items-baseline justify-between gap-4 border-b py-2.5 last:border-b-0">
      <dt className="text-muted-foreground text-xs tracking-wide uppercase">
        {term}
      </dt>
      <dd className="tabular font-mono text-sm">{children}</dd>
    </div>
  )
}

function formatUptime(seconds: number): string {
  if (seconds < 60) return `${seconds.toFixed(1)}s`
  const minutes = Math.floor(seconds / 60)
  const remainder = Math.floor(seconds % 60)
  if (minutes < 60) return `${minutes}m ${remainder}s`
  const hours = Math.floor(minutes / 60)
  return `${hours}h ${minutes % 60}m`
}

/**
 * Live backend health, fetched through TanStack Query.
 *
 * This is the Phase 0 checkpoint: real data from FastAPI rendered in React.
 * Note what is deliberately absent -- no accuracy tile. On traffic that is 99%
 * benign it would be meaningless, and a hero percentage is the exact failure
 * mode this project is built to avoid.
 */
export function HealthPanel() {
  const { data, error, isPending, isFetching, refetch } = useHealth()

  return (
    <Card className="w-full max-w-md">
      <CardHeader>
        <div className="flex items-center justify-between gap-3">
          <CardTitle className="flex items-center gap-2">
            <Activity className="size-4 text-[var(--info)]" aria-hidden="true" />
            Backend health
          </CardTitle>
          {data ? (
            <Badge variant={data.status === 'ok' ? 'ok' : 'high'}>
              {data.status}
            </Badge>
          ) : null}
        </div>
        <CardDescription>
          GET /api/v1/health, polled every {env.healthPollMs / 1000}s
        </CardDescription>
      </CardHeader>

      <CardContent>
        {isPending ? (
          <div className="space-y-3" role="status" aria-label="Loading health">
            <Skeleton className="h-5 w-full" />
            <Skeleton className="h-5 w-4/5" />
            <Skeleton className="h-5 w-2/3" />
          </div>
        ) : error ? (
          <div className="flex items-start gap-3">
            <ServerCrash
              className="mt-0.5 size-4 shrink-0 text-[var(--critical)]"
              aria-hidden="true"
            />
            <div className="min-w-0">
              <p className="text-sm font-medium">Backend unreachable</p>
              <p className="text-muted-foreground mt-1 font-mono text-xs break-words">
                {error instanceof Error ? error.message : 'Unknown error'}
              </p>
            </div>
          </div>
        ) : (
          <dl>
            <Field term="status">{data.status}</Field>
            <Field term="model_version">
              {data.model_version === 'unloaded' ? (
                <span className="text-muted-foreground">unloaded</span>
              ) : (
                data.model_version
              )}
            </Field>
            <Field term="uptime_s">{formatUptime(data.uptime_s)}</Field>
          </dl>
        )}
      </CardContent>

      <CardFooter className="justify-between gap-3">
        <p className="text-muted-foreground text-xs">
          {data?.model_version === 'unloaded'
            ? 'No model trained yet — Phase 2 writes the first bundle.'
            : 'Scoring with the loaded bundle.'}
        </p>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => void refetch()}
          disabled={isFetching}
          aria-label="Refresh health"
        >
          <RefreshCw
            className={isFetching ? 'animate-spin' : undefined}
            aria-hidden="true"
          />
          Refresh
        </Button>
      </CardFooter>
    </Card>
  )
}
