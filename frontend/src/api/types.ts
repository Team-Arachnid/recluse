/**
 * Concrete aliases over the generated OpenAPI types.
 *
 * Components import from here, so the generated file stays an implementation
 * detail and a schema change surfaces as a type error at the call sites.
 */
import type { components } from '@/types/api'

type Schemas = components['schemas']

// -- system ------------------------------------------------------------------
export type HealthResponse = Schemas['HealthResponse']
export type NotImplementedResponse = Schemas['NotImplementedResponse']
export type HealthStatus = HealthResponse['status']

// -- the alert domain --------------------------------------------------------
export type AlertSummary = Schemas['AlertSummary']
export type AlertDetail = Schemas['AlertDetail']
export type AlertPage = Schemas['AlertPage']
export type AlertExplanation = Schemas['AlertExplanation']
export type Contributor = Schemas['Contributor']
export type RecommendedActions = Schemas['RecommendedActions']
export type Technique = Schemas['Technique']
export type QueueStats = Schemas['QueueStats']
export type AlertStatusUpdate = Schemas['AlertStatusUpdate']
export type AlertStatusResult = Schemas['AlertStatusResult']
export type VerdictRequest = Schemas['VerdictRequest']
export type VerdictResponse = Schemas['VerdictResponse']

/**
 * The controlled vocabularies, read off the generated schema rather than
 * retyped. Adding an eighth attack family to `app/models.py` has to become a
 * type error at every `switch` that colours one, not a silent `undefined`
 * badge.
 */
export type AlertKind = AlertSummary['kind']
export type AlertFamily = NonNullable<AlertSummary['family']>
export type Severity = AlertSummary['severity']
export type AlertStatus = AlertSummary['status']
export type DetectionStage = AlertSummary['detection_stage']
export type Verdict = NonNullable<AlertSummary['latest_verdict']>

// -- the live feed -----------------------------------------------------------
export type AlertEvent = Schemas['AlertEvent']
export type HeartbeatEvent = Schemas['HeartbeatEvent']
export type ReplayStatus = Schemas['ReplayStatus']
export type ReplaySpeed = Schemas['ReplayStartRequest']['speed']

// -- measured model performance ----------------------------------------------
export type ModelMetrics = Schemas['ModelMetrics']
export type ClassMetrics = Schemas['ClassMetrics']
export type CurvePair = Schemas['CurvePair']
export type ThresholdProjection = Schemas['ThresholdProjection']
export type AnomalyHistogram = Schemas['AnomalyHistogram']
export type ErrorDistribution = Schemas['ErrorDistribution']

// -- what this deployment has seen -------------------------------------------
export type AnalyticsSummary = Schemas['AnalyticsSummary']
export type CountedPair = Schemas['CountedPair']
export type TimeBucket = Schemas['TimeBucket']
export type ThroughputStats = Schemas['ThroughputStats']
export type MitreCoverage = Schemas['MitreCoverage']
export type MitreCoverageRow = Schemas['MitreCoverageRow']
export type FeedbackLoop = Schemas['FeedbackLoop']
export type FeedbackVersionRow = Schemas['FeedbackVersionRow']

// -- drift and active learning (Phase 7) -------------------------------------
export type DriftResponse = Schemas['DriftResponse']
export type DriftSnapshot = Schemas['DriftSnapshot']
export type DriftFeatureScore = Schemas['DriftFeatureScore']
export type DriftSeries = Schemas['DriftSeries']
export type DriftSeriesPoint = Schemas['DriftSeriesPoint']
export type DriftBand = DriftFeatureScore['band']
export type ModelRegistry = Schemas['ModelRegistry']
export type RegistryEntry = Schemas['RegistryEntryResponse']
export type ModelStage = RegistryEntry['stage']
export type RetrainStatusResponse = Schemas['RetrainStatusResponse']
export type RetrainRun = Schemas['RetrainRunResponse']
export type RetrainStatus = RetrainRun['status']

export type AnalyticsRange = '24h' | '7d' | '30d' | 'all'
