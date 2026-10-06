/**
 * Shape-accurate API fixtures for the screen tests.
 *
 * Small rather than captured wholesale: the real `/metrics/model` response
 * carries two 512-point curves and a seven-fold LOAO table, and a test asserting
 * that a caption is on screen does not become more truthful for dragging 40kB of
 * JSON along. Every field here exists on the generated type, so a schema change
 * breaks these at compile time rather than leaving them quietly describing a
 * response the server no longer sends.
 *
 * Two details are copied from the live server on purpose, because they are the
 * ones that bite:
 *
 * - timestamps have no `Z`. SQLite stores `DateTime(timezone=True)` naively, so
 *   FastAPI serialises a value written as UTC without its offset. A fixture that
 *   helpfully added the `Z` would hide the one bug this makes possible.
 * - `confidence` is present but minuscule on an anomaly alert rather than null,
 *   which is what the fusion path actually emits.
 */
import type {
  AlertDetail,
  AlertPage,
  AlertSummary,
  AnalyticsSummary,
  AnomalyHistogram,
  FeedbackLoop,
  HealthResponse,
  MitreCoverage,
  ModelMetrics,
  QueueStats,
  ReplayStatus,
  ThresholdProjection,
} from '@/api/types'

export const health: HealthResponse = {
  status: 'ok',
  model_version: 'stage1-lgbm-202609281410',
  uptime_s: 412.5,
}

const base = {
  src_port: null,
  protocol: null,
  model_version: 'stage1-lgbm-202609281410',
  source: 'replay' as const,
  latest_verdict: null,
}

/** The highest-risk row: a named DoS family, caught by Stage 1. */
export const knownAlert: AlertSummary = {
  ...base,
  id: 3,
  kind: 'KNOWN',
  family: 'dos',
  severity: 'critical',
  risk_score: 0.9935,
  confidence: 0.9941,
  anomaly_score: null,
  detected_at: '2026-10-06T10:24:11.147772',
  src_ip: '172.16.0.1',
  dst_ip: '192.168.10.50',
  dst_port: 80,
  asset_criticality: 'critical',
  status: 'open',
  occurrence_count: 1,
  first_seen: '2026-10-06T10:24:11.147772',
  last_seen: '2026-10-06T10:24:11.147772',
  mitre_technique: 'T1499',
  detection_stage: 'stage1_supervised',
}

/** The row the whole project exists to produce: Stage 2 fired, Stage 1 had no
 *  name for it, and dedupe has collapsed 99 flows into it. */
export const novelAlert: AlertSummary = {
  ...base,
  id: 1,
  kind: 'UNCLASSIFIED_ANOMALY',
  family: null,
  severity: 'critical',
  risk_score: 0.9566,
  confidence: 1.7097341754560261e-6,
  anomaly_score: 0.1134578213095665,
  detected_at: '2026-10-06T10:24:04.498311',
  src_ip: '172.16.0.1',
  dst_ip: '192.168.10.25',
  dst_port: 443,
  asset_criticality: 'low',
  status: 'open',
  occurrence_count: 99,
  first_seen: '2026-10-06T10:24:04.498311',
  last_seen: '2026-10-06T10:24:13.144851',
  mitre_technique: null,
  detection_stage: 'stage2_anomaly',
}

export const midAlert: AlertSummary = {
  ...base,
  id: 2,
  kind: 'KNOWN',
  family: 'brute_force',
  severity: 'high',
  risk_score: 0.5648,
  confidence: 0.6111,
  anomaly_score: null,
  detected_at: '2026-10-06T10:24:05.336462',
  src_ip: '172.16.0.1',
  dst_ip: '192.168.10.50',
  dst_port: 22,
  asset_criticality: 'critical',
  status: 'open',
  occurrence_count: 1,
  first_seen: '2026-10-06T10:24:05.336462',
  last_seen: '2026-10-06T10:24:05.336462',
  mitre_technique: 'T1110',
  detection_stage: 'stage1_supervised',
}

/** Descending by risk, which is the ordering the server guarantees. */
export const alertPage: AlertPage = {
  items: [knownAlert, novelAlert, midAlert],
  next_cursor: null,
  limit: 100,
}

export const novelOnlyPage: AlertPage = {
  items: [novelAlert],
  next_cursor: null,
  limit: 100,
}

export const knownDetail: AlertDetail = {
  ...knownAlert,
  explanation: {
    explainer: 'treeshap',
    base_value: 0.33,
    contributors: [
      { feature: 'flow_packets_s', share: 0.41, value: 2.84, contribution: 1.92, error: null },
      { feature: 'flow_duration', share: 0.22, value: -0.51, contribution: -1.03, error: null },
      { feature: 'fwd_packet_length_mean', share: 0.18, value: 1.1, contribution: 0.84, error: null },
    ],
  },
  narrative: '18,400 packets per second sustained to a single destination port.',
  recommended_actions: {
    technique: {
      technique_id: 'T1499',
      name: 'Endpoint Denial of Service',
      url: 'https://attack.mitre.org/techniques/T1499/',
      means: 'A single source is flooding one service with more requests than it can answer.',
    },
    has_playbook: true,
    summary: 'Rate-limit the source and confirm the service recovered.',
    actions: [
      'Confirm the destination service is still answering.',
      'Rate-limit or null-route the source at the edge.',
      'Check whether the source is an owned host that has been compromised.',
    ],
  },
  raw_flow: {
    destination_port: 80,
    flow_duration: 1_842,
    flow_packets_s: 18_400.5,
    _provenance: {
      src_ip: 'derived',
      dst_port: 'observed',
      note: 'src_ip and dst_ip are derived from the published CICIDS2017 lab topology, not observed.',
    },
  },
  ground_truth_label: 'DoS Hulk',
  host_prior_alert_count: 2,
}

/** The honest case: no technique, no playbook, and both panels say so. */
export const novelDetail: AlertDetail = {
  ...novelAlert,
  explanation: {
    explainer: 'reconstruction_error',
    base_value: null,
    contributors: [
      { feature: 'init_win_bytes_forward', share: 0.31, value: null, contribution: null, error: 1.056 },
      { feature: 'psh_flag_count', share: 0.14, value: null, contribution: null, error: 0.455 },
    ],
  },
  narrative: 'Reconstruction error 0.113, above the 99.5th percentile of normal traffic.',
  recommended_actions: {
    technique: null,
    has_playbook: false,
    summary: 'No reviewed playbook covers traffic the classifier could not name.',
    actions: [],
  },
  raw_flow: { destination_port: 443, flow_duration: 94_221 },
  ground_truth_label: 'Infiltration',
  host_prior_alert_count: 2,
}

export const relatedAlerts: AlertSummary[] = [knownAlert, midAlert]

export const queueStats: QueueStats = {
  generated_at: '2026-10-06T10:24:13.675208',
  open_alerts: 3,
  alerts_last_hour: 3,
  observed_alerts_per_hour: 180,
  observed_window_hours: 0.0018470725,
  hosts_affected: 2,
  sources_seen: 1,
  unclassified_open: 1,
  unjudged_open: 3,
}

export const anomalyHistogram: AnomalyHistogram = {
  edges: [0.0001, 0.001, 0.01, 0.1, 1],
  spacing: 'log',
  tau_anom: 0.10981125503778419,
  budget_tau: 0.4244283771419525,
  distributions: [
    {
      name: 'validation_benign',
      rows: 1000,
      counts: [120, 500, 340, 40],
      percentiles: { p50: 0.0053, max: 1.53 },
    },
    {
      name: 'test_benign',
      rows: 1000,
      counts: [100, 480, 360, 60],
      percentiles: { p50: 0.0052, max: 0.74 },
    },
    {
      name: 'test_attack',
      rows: 500,
      counts: [5, 60, 185, 250],
      percentiles: { p50: 0.052, max: 1.83 },
    },
  ],
}

export const thresholdProjection: ThresholdProjection = {
  t: 0.10981125503778419,
  fpr: 0.059594177561974,
  recall: 0.3102476252628526,
  benign_rows: 375_238,
  benign_above: 22_362,
  attack_rows: 220_656,
  attack_above: 68_458,
  false_alerts_per_day: 59_594.177561974,
  alerts_per_analyst_hour: 7449.27219524675,
  budget_per_day: 320,
  target_fpr: 0.00032,
  within_budget: false,
  covers_distribution: true,
}

export const modelMetrics: ModelMetrics = {
  per_class: {
    benign: { precision: 0.999, recall: 0.999, f1: 0.999, support: 375_238 },
    dos: { precision: 0.874, recall: 0.912, f1: 0.893, support: 193_745 },
  },
  labels: ['benign', 'dos'],
  confusion_matrix: [
    [375_177, 61],
    [17_050, 176_695],
  ],
  curves: {
    pr: [
      [1, 0.37],
      [0.88, 0.74],
      [0.5, 0.93],
    ],
    roc: [
      [0, 0],
      [0.0004, 0.56],
      [0.06, 0.93],
    ],
  },
  pr_auc: 0.8816,
  roc_auc: 0.9965,
  accuracy: 0.9713,
  tau_sup: 0.38790842847095985,
  fpr_at_threshold: 0.00031791849175430453,
  alerts_per_analyst_hour: 39.7398,
  budget: { max_alerts_per_day: 320, target_fpr: 0.00032 },
  stage1_family_recall: { ddos: { support: 128_014, flagged: 48_600, recall: 0.3796 } },
  stage2_family_recall: { ddos: { support: 128_014, flagged: 68_222, recall: 0.5329 } },
  stage2_pr_auc: 0.7728,
  loao: {
    measured_at: '2026-10-03T07:53:08+00:00',
    champion: {
      version: 'stage1-lgbm-202609281410',
      tau_sup: 0.38790842847095985,
      tau_anom: 0.10981125503778419,
    },
    stage2_alone: {
      benign_fpr: 0.059594177561974,
      benign_fpr_at_budget: 0.005641752700952462,
      families: {
        dos: { recall: 0.7553433636997083, recall_at_budget: 0.045977960721567006 },
      },
    },
    folds: [
      {
        held_out: 'dos',
        refitted: true,
        reuse_reason: null,
        classes: ['benign', 'brute_force'],
        headline: {
          family: 'dos',
          support: 193_745,
          stage1_caught: 64,
          stage1_named: 0,
          stage1_named_rate: 0,
          stage1_recall: 0.0003303311053188469,
          stage2_caught: 146_302,
          stage2_recall: 0.7551265839118428,
          total_recall: 0.7554569150171617,
          missed: 47_379,
          miss_rate: 0.24454308498283828,
          stage2_pr_auc: 0.8607339106049231,
        },
        benign: { fpr: 0.0596, alerts_per_analyst_hour: 7450.94 },
      },
      {
        held_out: 'botnet',
        refitted: false,
        reuse_reason: 'no rows on the training days at all',
        classes: ['benign', 'dos', 'brute_force'],
        headline: {
          family: 'botnet',
          support: 1948,
          stage1_caught: 0,
          stage1_named: 0,
          stage1_named_rate: 0,
          stage1_recall: 0,
          stage2_caught: 42,
          stage2_recall: 0.0216,
          total_recall: 0.0216,
          missed: 1906,
          miss_rate: 0.9784,
          stage2_pr_auc: 0.31,
        },
        benign: { fpr: 0.0596, alerts_per_analyst_hour: 7450.94 },
      },
    ],
  },
}

export const analyticsSummary: AnalyticsSummary = {
  range: '24h',
  generated_at: '2026-10-06T10:30:00.000000',
  total_alerts: 3,
  unclassified_alerts: 1,
  unclassified_rate: 1 / 3,
  series: [{ bucket: '2026-10-06T10:00:00.000000', known: 2, unclassified: 1 }],
  families: [
    { value: 'dos', count: 1 },
    { value: 'brute_force', count: 1 },
  ],
  top_destination_hosts: [{ value: '192.168.10.50', count: 2 }],
  top_destination_ports: [{ value: '80', count: 1 }],
  top_source_hosts: [{ value: '172.16.0.1', count: 3 }],
  throughput: {
    opened: 3,
    resolved: 0,
    verdicts: 1,
    true_positives: 1,
    false_positives: 0,
    unsure: 0,
    true_positive_rate: 1,
    mean_seconds_to_verdict: 42.5,
  },
}

export const mitreCoverage: MitreCoverage = {
  techniques: [
    {
      family: 'dos',
      technique_id: 'T1499',
      name: 'Endpoint Denial of Service',
      url: 'https://attack.mitre.org/techniques/T1499/',
      means: 'A single source is flooding one service.',
      count: 1,
    },
    {
      family: 'botnet',
      technique_id: 'T1071',
      name: 'Application Layer Protocol',
      url: 'https://attack.mitre.org/techniques/T1071/',
      means: 'A host is talking to a controller over an ordinary-looking protocol.',
      count: 0,
    },
  ],
  unclassified_anomalies: 1,
}

export const feedbackLoop: FeedbackLoop = {
  generated_at: '2026-10-06T10:30:00.000000',
  serving_model_version: 'stage1-lgbm-202609281410',
  total_alerts: 3,
  judged_alerts: 1,
  judged_share: 1 / 3,
  labels_total: 3,
  labels_pending_retrain: 2,
  labels_consumed: 1,
  true_positives: 2,
  false_positives: 1,
  unsure: 0,
  disagreement_rate: 1 / 3,
  mean_seconds_to_verdict: 42.5,
  by_model_version: [
    {
      model_version: 'stage1-lgbm-202609281410',
      verdicts: 3,
      true_positives: 2,
      false_positives: 1,
      unsure: 0,
    },
  ],
  retrain_available: false,
  retrain_phase: 'Phase 7 (drift and active learning)',
}

export const replayStopped: ReplayStatus = {
  running: false,
  speed: null,
  dataset: null,
  started_at: null,
  rows_scored: 0,
  alerts_emitted: 0,
}

export const notImplemented = {
  detail: 'Not implemented yet. Arrives in Phase 7 (drift and active learning).',
  phase: 'Phase 7 (drift and active learning)',
  endpoint: 'GET /metrics/drift',
}
