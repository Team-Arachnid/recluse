/**
 * The marks an alert wears, in one place, so the queue, the drawer, the ticker
 * and the host-context list cannot drift into four dialects.
 *
 * `UNCLASSIFIED_ANOMALY` always carries three marks rather than one: the ochre
 * channel, the biohazard glyph, and its own words. Colour alone would fail a
 * colourblind analyst, and these are the alerts the whole project exists to
 * produce.
 */
import {
  Biohazard,
  Bot,
  DoorOpen,
  Globe,
  KeyRound,
  Radar,
  Waves,
  Zap,
  type LucideIcon,
} from 'lucide-react'

import type { AlertFamily, AlertKind, AlertStatus, Severity } from '@/api/types'
import { Badge } from '@/components/ui/badge'
import {
  classLabel,
  isNovel,
  SEVERITY_COLOR,
  STATUS_LABEL,
  STATUS_VARIANT,
} from '@/lib/alerts'
import { cn } from '@/lib/utils'

/** One glyph per family -- what the traffic does, not how bad it is. */
export const FAMILY_ICON: Record<AlertFamily, LucideIcon> = {
  dos: Zap,
  ddos: Waves,
  brute_force: KeyRound,
  port_scan: Radar,
  web_attack: Globe,
  botnet: Bot,
  infiltration: DoorOpen,
}

export function ClassBadge({
  alert,
  className,
}: {
  alert: { kind: AlertKind; family?: AlertFamily | null }
  className?: string
}) {
  const novel = isNovel(alert)
  const Icon = novel ? Biohazard : alert.family ? FAMILY_ICON[alert.family] : Radar
  return (
    <Badge
      variant={novel ? 'novel' : 'neutral'}
      className={cn('max-w-full font-sans text-[11.5px]', !novel && 'text-foreground', className)}
    >
      <Icon
        className={cn('size-3 shrink-0', !novel && 'text-subtle-foreground')}
        aria-hidden="true"
      />
      <span className="truncate">{classLabel({ kind: alert.kind, family: alert.family ?? null })}</span>
    </Badge>
  )
}

/** The sample's severity mark: a dot and the word, monospaced, no pill. */
export function SeverityMark({ severity, className }: { severity: Severity; className?: string }) {
  return (
    <span
      className={cn('inline-flex items-center gap-1.5 text-[12.5px] font-medium capitalize', className)}
      style={{ color: SEVERITY_COLOR[severity] }}
    >
      <span
        aria-hidden="true"
        className="size-1.5 shrink-0 rounded-full"
        style={{ backgroundColor: SEVERITY_COLOR[severity] }}
      />
      {severity}
    </span>
  )
}

export function StatusPill({ status, className }: { status: AlertStatus; className?: string }) {
  return (
    <Badge
      variant={STATUS_VARIANT[status]}
      className={cn('rounded-full px-2.5 font-sans text-[11.5px]', className)}
    >
      {STATUS_LABEL[status]}
    </Badge>
  )
}
