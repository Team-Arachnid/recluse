/**
 * The display vocabulary of an alert: how each controlled value is named,
 * coloured and ordered on screen.
 *
 * Every map here is keyed by the generated type rather than by `string`, so
 * adding an eighth attack family to `app/models.py` becomes a compile error in
 * this file instead of an unlabelled badge in production.
 */
import type {
  AlertFamily,
  AlertKind,
  AlertStatus,
  AlertSummary,
  DetectionStage,
  Severity,
  Verdict,
} from '@/api/types'
import type { badgeVariants } from '@/components/ui/badge'
import type { VariantProps } from 'class-variance-authority'

type BadgeVariant = NonNullable<VariantProps<typeof badgeVariants>['variant']>

export const SEVERITIES: readonly Severity[] = ['critical', 'high', 'medium', 'low']

export const SEVERITY_VARIANT: Record<Severity, BadgeVariant> = {
  critical: 'critical',
  high: 'high',
  medium: 'medium',
  low: 'info',
}

export const FAMILIES: readonly AlertFamily[] = [
  'dos',
  'ddos',
  'brute_force',
  'port_scan',
  'web_attack',
  'botnet',
  'infiltration',
]

/**
 * Family names as a SOC says them, not as the database spells them.
 *
 * `ddos` is `DDoS` and not `Ddos`; `dos` is `DoS`. Mechanical title-casing gets
 * both wrong, and an acronym rendered as a word is the first thing that makes a
 * security tool read as written by someone who has not worked a queue.
 */
export const FAMILY_LABEL: Record<AlertFamily, string> = {
  dos: 'DoS',
  ddos: 'DDoS',
  brute_force: 'Brute force',
  port_scan: 'Port scan',
  web_attack: 'Web attack',
  botnet: 'Botnet',
  infiltration: 'Infiltration',
}

export const STATUSES: readonly AlertStatus[] = ['open', 'in_review', 'closed', 'dismissed']

export const STATUS_LABEL: Record<AlertStatus, string> = {
  open: 'Open',
  in_review: 'In review',
  closed: 'Closed',
  dismissed: 'Dismissed',
}

export const VERDICTS: readonly Verdict[] = ['TP', 'FP', 'UNSURE']

/**
 * Verdict wording, in the analyst's terms rather than the schema's.
 *
 * The buttons say what the analyst is asserting about the alert, and the same
 * words appear in the queue column afterwards, so the vocabulary someone learns
 * by clicking is the one they read back.
 */
export const VERDICT_LABEL: Record<Verdict, string> = {
  TP: 'True positive',
  FP: 'False positive',
  UNSURE: 'Need more info',
}

export const VERDICT_SHORT: Record<Verdict, string> = {
  TP: 'TP',
  FP: 'FP',
  UNSURE: '?',
}

export const VERDICT_VARIANT: Record<Verdict, BadgeVariant> = {
  TP: 'ok',
  FP: 'high',
  UNSURE: 'neutral',
}

export const STAGE_LABEL: Record<DetectionStage, string> = {
  stage1_supervised: 'Stage 1 · classifier',
  stage2_anomaly: 'Stage 2 · anomaly',
}

export const CRITICALITY_ORDER: readonly string[] = ['critical', 'high', 'medium', 'low']

/**
 * True for the detections the project exists to make.
 *
 * Stage 2 fired because Stage 1 could not name the traffic, so there is no
 * family and no technique. Everything that renders an anomaly differently --
 * the badge, the row marker, the two honest panels in the drawer -- branches
 * here rather than testing `family === null` independently in six places.
 */
export const isNovel = (alert: { kind: AlertKind }): boolean =>
  alert.kind === 'UNCLASSIFIED_ANOMALY'

/** What the row calls itself: a family name, or the honest label. */
export function classLabel(alert: Pick<AlertSummary, 'kind' | 'family'>): string {
  if (isNovel(alert)) return 'Unclassified anomaly'
  return alert.family ? FAMILY_LABEL[alert.family] : 'Unnamed'
}

export function classVariant(alert: Pick<AlertSummary, 'kind'>): BadgeVariant {
  return isNovel(alert) ? 'novel' : 'neutral'
}
