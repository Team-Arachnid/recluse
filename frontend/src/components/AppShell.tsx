/**
 * The console shell: a sidebar of screens, and a work surface beside it.
 *
 * The sidebar carries two things besides navigation, both read off the live API
 * rather than drawn: how much work is waiting on each screen (open alerts,
 * labels pending a retrain, a drift warning), and what the two-stage pipeline
 * is actually running -- each stage's version and threshold. An analyst should
 * never have to open a second screen to learn which model produced the queue in
 * front of them.
 *
 * The queue is first because it is the landing page: analysts live in the
 * queue, so the queue is home. Analytics is reached from here rather than built
 * as the landing page, because it answers a different question ("what happened
 * this week") for a different reader.
 */
import {
  Activity,
  BarChart3,
  Cpu,
  Gauge,
  Inbox,
  Layers,
  Menu,
  Moon,
  RefreshCcw,
  ShieldCheck,
  Sun,
  Waves,
  X,
  type LucideIcon,
} from 'lucide-react'
import { useEffect, useState, type ReactNode } from 'react'
import { NavLink, useLocation } from 'react-router-dom'

import {
  useAnomalyHistogram,
  useDrift,
  useFeedbackLoop,
  useHealth,
  useModelMetrics,
  useQueueStats,
  useIngestStatus,
  useReplayStatus,
} from '@/api/queries'
import { useAlertStream } from '@/api/stream'
import { RecluseLogo } from '@/components/RecluseLogo'
import { Button } from '@/components/ui/button'
import { count, decimal } from '@/lib/format'
import { applyTheme, storedTheme, type Theme } from '@/lib/theme'
import { cn } from '@/lib/utils'

type BadgeKind = 'queue' | 'drift' | 'feedback' | 'live'

interface Screen {
  to: string
  label: string
  icon: LucideIcon
  end?: boolean
  badge?: BadgeKind
}

/** The screens, in the order the design argues for. */
const SCREENS: Screen[] = [
  { to: '/', label: 'Triage queue', icon: Inbox, end: true, badge: 'queue' },
  { to: '/live', label: 'Live traffic', icon: Activity, badge: 'live' },
  { to: '/model', label: 'Model performance', icon: Gauge },
  { to: '/drift', label: 'Drift monitor', icon: Waves, badge: 'drift' },
  { to: '/feedback', label: 'Feedback loop', icon: RefreshCcw, badge: 'feedback' },
  { to: '/analytics', label: 'Analytics', icon: BarChart3 },
  { to: '/system', label: 'System', icon: Cpu },
]

const num = (value: unknown): number | null =>
  typeof value === 'number' && Number.isFinite(value) ? value : null

/** The served version is both stages joined by `+`; the sidebar names each. */
export function splitVersion(version: string | null | undefined): {
  stage1: string | null
  stage2: string | null
} {
  if (!version || version === 'unloaded') return { stage1: null, stage2: null }
  const [stage1, stage2] = version.split('+')
  return { stage1: stage1 || null, stage2: stage2 || null }
}

function NavBadge({ kind, active }: { kind: BadgeKind; active: boolean }) {
  const stats = useQueueStats()
  const drift = useDrift()
  const feedback = useFeedbackLoop()
  const { data: replay } = useReplayStatus()

  const pill = (text: string, title: string, tone: 'brand' | 'novel' | 'plain' = 'plain') => (
    <span
      title={title}
      aria-hidden="true"
      className={cn(
        'rounded border px-1.5 py-px font-mono text-[10px] font-semibold tabular-nums',
        tone === 'brand' &&
          'border-[color-mix(in_oklab,var(--brand)_40%,transparent)] bg-[color-mix(in_oklab,var(--brand)_14%,transparent)] text-[var(--brand-foreground)]',
        tone === 'novel' &&
          'border-[color-mix(in_oklab,var(--novel)_40%,transparent)] bg-[color-mix(in_oklab,var(--novel)_14%,transparent)] text-[var(--novel)]',
        tone === 'plain' && 'border-raised-border bg-raised text-muted-foreground',
        active && tone === 'plain' && 'text-foreground',
      )}
    >
      {text}
    </span>
  )

  if (kind === 'queue') {
    const open = num(stats.data?.open_alerts)
    if (!open) return null
    return pill(count(open), count(open) + ' open alerts', 'brand')
  }
  if (kind === 'feedback') {
    const pending = num(feedback.data?.labels_pending_retrain)
    if (!pending) return null
    return pill(count(pending), count(pending) + ' labels waiting for a retrain')
  }
  if (kind === 'drift') {
    if (drift.data?.latest?.retrain_recommended !== true) return null
    return pill('PSI', 'A feature crossed the 0.25 band: retrain recommended', 'novel')
  }
  if (replay?.running === true) {
    return (
      <span
        aria-hidden="true"
        title="A traffic source is running"
        className="size-1.5 rounded-full bg-[var(--ok)] motion-safe:animate-[pulse-dot_1.6s_ease-in-out_infinite]"
      />
    )
  }
  return null
}

/** What the fused pipeline is running right now -- the sample's pipeline card,
 *  filled from the API rather than from copy. */
function PipelineCard() {
  const { data: health } = useHealth()
  const { data: metrics } = useModelMetrics()
  const { data: histogram } = useAnomalyHistogram()
  const { stage1, stage2 } = splitVersion(health?.model_version)

  const tauSup = num(metrics?.tau_sup)
  const tauAnom = num(histogram?.tau_anom)

  const row = (index: string, name: string, kind: string, tau: number | null, version: string | null) => (
    <div>
      <div className="flex items-center justify-between gap-2">
        <span className="text-muted-foreground">
          {index}. {name}
        </span>
        <span className="text-foreground font-medium">{kind}</span>
      </div>
      <div className="text-subtle-foreground mt-0.5 flex items-center justify-between gap-2 font-mono text-[10px]">
        <span className="truncate" title={version ?? undefined}>
          {version ?? 'not loaded'}
        </span>
        <span className="shrink-0">τ {tau === null ? '—' : decimal(tau, 3)}</span>
      </div>
    </div>
  )

  return (
    <div className="border-border bg-card/90 relative m-3 rounded-lg border px-3.5 py-3.5 text-[11px] backdrop-blur-sm">
      <div className="text-foreground-strong mb-2.5 flex items-center gap-2 text-[10px] font-semibold tracking-wider uppercase">
        <Layers className="text-muted-foreground size-3.5" aria-hidden="true" />
        Two-stage pipeline
      </div>
      <div className="space-y-2 leading-tight">
        {row('1', 'Classifier', 'Supervised', tauSup, stage1)}
        {row('2', 'Autoencoder', 'Benign-only', tauAnom, stage2)}
      </div>
      <div className="border-divider text-muted-foreground mt-3 flex items-center gap-1.5 border-t pt-2.5 text-[10px]">
        <ShieldCheck className="size-3 shrink-0 text-[var(--brand-foreground)]" aria-hidden="true" />
        <span className="truncate">Alerts, ranks, explains — never blocks</span>
      </div>
    </div>
  )
}

function Sidebar({ onNavigate }: { onNavigate?: () => void }) {
  return (
    <aside className="bg-sidebar border-sidebar-border relative flex h-full w-64 shrink-0 flex-col overflow-hidden border-r select-none">
      {/* The reference console's ember: a low crimson glow pooled at the foot of
          the rail. Decorative, behind everything, and never over content. */}
      <div
        aria-hidden="true"
        className="pointer-events-none absolute -bottom-48 -left-44 size-[30rem] rounded-full opacity-50 blur-2xl dark:opacity-100"
        style={{
          background:
            'radial-gradient(circle at center, color-mix(in oklab, var(--brand-bright) 55%, transparent) 0%, color-mix(in oklab, var(--brand) 18%, transparent) 38%, transparent 68%)',
        }}
      />
      <NavLink
        to="/"
        onClick={onNavigate}
        className="border-sidebar-border relative flex items-center gap-3 border-b px-5 py-5"
        aria-label="Recluse, home"
      >
        <RecluseLogo size={38} className="shrink-0 text-[var(--brand-bright)]" />
        <span className="flex flex-col">
          <span className="text-foreground-strong text-base leading-tight font-semibold tracking-tight uppercase">
            Recluse
          </span>
          <span className="text-[10px] font-semibold tracking-[0.2em] text-[#c53a3a] uppercase">
            NIDS Engine
          </span>
        </span>
      </NavLink>

      <nav aria-label="Screens" className="scrollbar-thin relative flex-1 overflow-y-auto px-3 py-4">
        <ul className="space-y-0.5">
          {SCREENS.map((screen) => (
            <li key={screen.to}>
              <NavLink
                to={screen.to}
                end={screen.end ?? false}
                onClick={onNavigate}
                aria-label={screen.label}
                className={({ isActive }) =>
                  cn(
                    'flex items-center justify-between gap-2 rounded-lg border-l-[3px] px-3 py-2.5 text-sm transition-colors',
                    isActive
                      ? 'border-l-brand-bright text-foreground-strong bg-[color-mix(in_oklab,var(--brand-bright)_16%,transparent)] font-medium'
                      : 'text-muted-foreground hover:text-foreground hover:bg-hover border-l-transparent',
                  )
                }
              >
                {({ isActive }) => (
                  <>
                    <span className="flex min-w-0 items-center gap-3">
                      <screen.icon
                        className={cn(
                          'size-[18px] shrink-0',
                          isActive ? 'text-[var(--brand-bright)]' : 'text-subtle-foreground',
                        )}
                        aria-hidden="true"
                      />
                      <span className="truncate">{screen.label}</span>
                    </span>
                    {screen.badge ? <NavBadge kind={screen.badge} active={isActive} /> : null}
                  </>
                )}
              </NavLink>
            </li>
          ))}
        </ul>
      </nav>

      <PipelineCard />
    </aside>
  )
}

function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(storedTheme)

  const flip = () => {
    const next: Theme = theme === 'dark' ? 'light' : 'dark'
    setTheme(next)
    applyTheme(next)
  }

  return (
    <Button
      variant="outline"
      size="icon"
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
 * Every number on every screen is downstream of whether flows are arriving,
 * and an analyst reading an empty queue needs to know whether that means
 * "quiet" or "nothing is running".
 */
function SourcePill() {
  const { data: replay } = useReplayStatus()
  const { data: capture } = useIngestStatus()
  const { connected, received } = useAlertStream()

  const capturing = capture?.running === true
  const running = replay?.running === true || capturing
  const live = running && connected
  const detail = capturing
    ? (capture?.mode === 'alert' ? 'capture' : 'shadow') + ' ' + (capture?.source ?? '')
    : running
      ? 'replay' + (replay?.speed ? ' ' + replay.speed + '×' : '')
      : null

  return (
    <span
      className={cn(
        'inline-flex h-8 items-center gap-2 rounded-full border px-3.5 text-xs font-medium',
        live
          ? 'border-[color-mix(in_oklab,var(--ok)_40%,transparent)] bg-[color-mix(in_oklab,var(--ok)_10%,transparent)] text-[var(--ok)]'
          : 'border-border bg-card text-muted-foreground',
      )}
      title={
        running
          ? count(received) + ' alerts streamed this session'
          : 'No traffic source is running. Start a replay from Live traffic.'
      }
    >
      <span
        aria-hidden="true"
        className={cn(
          'size-2 rounded-full',
          live
            ? 'bg-[var(--ok)] motion-safe:animate-[pulse-dot_1.6s_ease-in-out_infinite]'
            : running
              ? 'bg-[var(--medium)]'
              : 'bg-[var(--subtle-foreground)]',
        )}
      />
      {running ? (live ? 'Live' : 'Connecting') : 'Idle'}
      {detail ? (
        <span className="hidden font-mono text-[10.5px] opacity-80 sm:inline">· {detail}</span>
      ) : null}
    </span>
  )
}

/** The top of every screen: what it is, one line on why, and the source state. */
export function PageHeader({
  title,
  lede,
  actions,
}: {
  title: string
  lede?: ReactNode
  actions?: ReactNode
}) {
  return (
    <header className="border-border mb-6 flex flex-col gap-4 border-b pb-5 sm:flex-row sm:items-start sm:justify-between">
      <div className="max-w-3xl min-w-0">
        <h1 className="text-foreground-strong text-2xl font-semibold tracking-tight sm:text-[28px]">
          {title}
        </h1>
        {lede ? (
          <p className="text-muted-foreground mt-1.5 text-sm leading-relaxed">{lede}</p>
        ) : null}
        <span aria-hidden="true" className="bg-brand-bright mt-3 block h-[3px] w-12 rounded-full" />
      </div>
      <div className="flex shrink-0 flex-wrap items-center gap-2">
        {actions}
        <SourcePill />
        <ThemeToggle />
      </div>
    </header>
  )
}

export function AppShell({ children }: { children: ReactNode }) {
  const [menuOpen, setMenuOpen] = useState(false)
  const location = useLocation()

  // A route change closes the phone drawer, whichever link caused it.
  useEffect(() => setMenuOpen(false), [location.pathname])

  return (
    <div className="bg-background text-foreground flex h-dvh w-full overflow-hidden">
      <div className="hidden md:flex">
        <Sidebar />
      </div>

      {menuOpen ? (
        <div className="fixed inset-0 z-40 flex md:hidden">
          <div
            className="absolute inset-0 bg-black/70"
            onClick={() => setMenuOpen(false)}
            aria-hidden="true"
          />
          <div className="relative z-50 flex">
            <Sidebar onNavigate={() => setMenuOpen(false)} />
            <Button
              variant="outline"
              size="icon"
              className="absolute top-4 -right-11"
              onClick={() => setMenuOpen(false)}
              aria-label="Close navigation"
            >
              <X aria-hidden="true" />
            </Button>
          </div>
        </div>
      ) : null}

      <main className="flex min-h-0 min-w-0 flex-1 flex-col overflow-y-auto">
        <div className="border-border bg-sidebar flex items-center justify-between border-b px-4 py-3 md:hidden">
          <Button
            variant="outline"
            size="icon"
            onClick={() => setMenuOpen(true)}
            aria-label="Open navigation"
          >
            <Menu aria-hidden="true" />
          </Button>
          <span className="flex items-center gap-2">
            <RecluseLogo size={22} />
            <span className="text-foreground-strong text-sm font-semibold tracking-wider uppercase">
              Recluse
            </span>
          </span>
          <span className="w-8" />
        </div>
        {children}
      </main>
    </div>
  )
}

/**
 * The wrapper for the screens that are read rather than worked.
 *
 * The queue does not use it: a table wants the full height of the window.
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
    <div className="mx-auto w-full max-w-[1500px] px-4 py-6 sm:px-6 lg:px-8">
      <PageHeader title={title} lede={lede} actions={actions} />
      {children}
    </div>
  )
}
