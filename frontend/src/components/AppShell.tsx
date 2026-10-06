/**
 * The console shell: one top bar, then a full-height work surface.
 *
 * A console rather than a dashboard. The bar is a single row -- mark, screens,
 * the state of the traffic source, the theme -- and everything below it belongs
 * to the screen. There is no second-level chrome, no breadcrumb and no page
 * title repeated under the nav, because every pixel of vertical space here is
 * a queue row an analyst can see without scrolling.
 */
import { Moon, ShieldAlert, Sun } from 'lucide-react'
import { useState, type ReactNode } from 'react'
import { NavLink } from 'react-router-dom'

import { useHealth, useReplayStatus } from '@/api/queries'
import { useAlertStream } from '@/api/stream'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { applyTheme, storedTheme, type Theme } from '@/lib/theme'
import { cn } from '@/lib/utils'

/**
 * The screens, in the order the design argues for.
 *
 * The queue is first because it is the landing page: analysts live in the
 * queue, so the queue is home. Analytics is last and reached from here rather
 * than built as the landing page, because it answers a different question
 * ("what happened this week") for a different reader.
 */
const SCREENS = [
  { to: '/', label: 'Queue', end: true },
  { to: '/live', label: 'Live' },
  { to: '/model', label: 'Model' },
  { to: '/drift', label: 'Drift' },
  { to: '/feedback', label: 'Feedback' },
  { to: '/analytics', label: 'Analytics' },
  { to: '/system', label: 'System' },
] as const

function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(storedTheme)

  const flip = () => {
    const next: Theme = theme === 'dark' ? 'light' : 'dark'
    setTheme(next)
    applyTheme(next)
  }

  return (
    <Button
      variant="ghost"
      size="sm"
      onClick={flip}
      aria-label={theme === 'dark' ? 'Switch to the light theme' : 'Switch to the dark theme'}
    >
      {theme === 'dark' ? <Moon aria-hidden="true" /> : <Sun aria-hidden="true" />}
    </Button>
  )
}

/**
 * What the traffic source is doing, in the one place it is always visible.
 *
 * Deliberately in the shell and not only on the Live screen: every number on
 * every screen is downstream of whether flows are arriving, and an analyst
 * reading an empty queue needs to know whether that means "quiet" or "nothing
 * is running".
 */
function SourceIndicator() {
  const { data: replay } = useReplayStatus()
  const { connected, received } = useAlertStream()

  if (!replay?.running) {
    return (
      <span className="text-muted-foreground hidden text-xs sm:inline">
        No traffic source running
      </span>
    )
  }

  return (
    <span className="flex items-center gap-2 text-xs">
      <span
        aria-hidden="true"
        className={cn(
          'size-1.5 rounded-full',
          connected
            ? 'bg-[var(--ok)] motion-safe:animate-pulse'
            : 'bg-[var(--medium)]',
        )}
      />
      <span className="text-muted-foreground">
        {replay.dataset} at {replay.speed}×
      </span>
      <span className="tabular text-muted-foreground hidden font-mono md:inline">
        {received} streamed
      </span>
    </span>
  )
}

export function AppShell({ children }: { children: ReactNode }) {
  const { data: health } = useHealth()

  return (
    <div className="flex min-h-screen flex-col">
      <header className="border-border bg-card/60 sticky top-0 z-30 border-b backdrop-blur">
        <div className="flex h-12 items-center gap-4 px-3 sm:px-4">
          <NavLink to="/" className="flex shrink-0 items-center gap-2" aria-label="Recluse, home">
            <ShieldAlert className="size-4 text-[var(--novel)]" aria-hidden="true" />
            <span className="text-sm font-semibold tracking-tight">Recluse</span>
          </NavLink>

          <nav aria-label="Screens" className="scrollbar-thin -mx-1 flex min-w-0 flex-1 overflow-x-auto">
            <ul className="flex items-center gap-0.5">
              {SCREENS.map((screen) => (
                <li key={screen.to}>
                  <NavLink
                    to={screen.to}
                    end={'end' in screen ? screen.end : false}
                    className={({ isActive }) =>
                      cn(
                        'block rounded-md px-2.5 py-1.5 text-sm whitespace-nowrap transition-colors',
                        isActive
                          ? 'bg-muted text-foreground font-medium'
                          : 'text-muted-foreground hover:text-foreground hover:bg-muted/60',
                      )
                    }
                  >
                    {screen.label}
                  </NavLink>
                </li>
              ))}
            </ul>
          </nav>

          <div className="flex shrink-0 items-center gap-3">
            <SourceIndicator />
            {health ? (
              <Badge
                variant={health.status === 'ok' ? 'ok' : 'high'}
                className="hidden lg:inline-flex"
                title={'Model version ' + health.model_version}
              >
                {health.model_version === 'unloaded' ? 'no model' : health.model_version}
              </Badge>
            ) : null}
            <ThemeToggle />
          </div>
        </div>
      </header>

      <main className="flex min-h-0 flex-1 flex-col">{children}</main>
    </div>
  )
}

/**
 * The wrapper for the six screens that are read rather than worked.
 *
 * The queue does not use it: a table wants the full width of the window, and
 * capping it would waste the space the source and destination columns need.
 * Everything else is prose and charts, which have a comfortable line length.
 */
export function ScreenBody({
  title,
  lede,
  actions,
  children,
}: {
  title: string
  lede?: ReactNode
  actions?: ReactNode
  children: ReactNode
}) {
  return (
    <div className="mx-auto w-full max-w-6xl px-4 py-7 sm:px-6">
      <header className="mb-7 flex flex-wrap items-start justify-between gap-4">
        <div className="max-w-2xl">
          <h1 className="text-xl font-semibold tracking-tight">{title}</h1>
          {lede ? (
            <p className="text-muted-foreground mt-2 text-sm leading-relaxed">{lede}</p>
          ) : null}
        </div>
        {actions ? <div className="flex items-center gap-2">{actions}</div> : null}
      </header>
      {children}
    </div>
  )
}
