# Database Schema

Every table, column, index and constraint Recluse persists, why the types were chosen the way they were, and what writes each table. Sourced from `backend/app/models.py`, `backend/app/db.py` and the four Alembic revisions under `backend/alembic/versions/` as they stand at Phase 9.

**Status: eight tables, every one with a writer.** The alert lifecycle -- `alerts`, `analyst_verdicts`, `model_versions` -- has existed since Phase 0 and been written since Phase 5 (the registry since Phase 7). Phase 7 added the drift monitor's `flow_samples`, `drift_runs` and `drift_features` and the retrain queue's `retrain_runs`; Phase 8 added `alerts.ground_truth_counts`; Phase 9 added `shadow_scores`, the live-capture burn-in.

---

## Entity relationships

```
 ┌──────────────────────────────┐
 │ model_versions │
 │──────────────────────────────│
 │ id PK │
 │ version UNIQUE │
 │ stage champion / │
 │ challenger / │
 │ archived │
 │ schema_hash │
 │ tau_sup, tau_anom │
 │ metrics JSON │
 └──────────────────────────────┘
 ▲
 │ by version string only.
 │ NOT a foreign key — see
 │ "Audit trail" below.
 │
┌──────────────────────────────────┴───┐
│ alerts │
│──────────────────────────────────────│
│ id PK │
│ kind KNOWN | │
│ UNCLASSIFIED_ANOMALY│
│ family null iff kind is │
│ UNCLASSIFIED_ANOMALY│
│ detection_stage stage1 | stage2 │
│ risk_score queue ordering key │
│ dedupe_key src|class|bucket │
│ occurrence_count >= 1 │
│ model_version ── audit ──────────┘
│ ... │
└───────────────┬──────────────────────┘
 │ 1
 │
 │ N ON DELETE CASCADE
 ┌───────▼──────────────────────┐
 │ analyst_verdicts │
 │──────────────────────────────│
 │ id PK │
 │ alert_id FK → alerts.id │
 │ verdict       TP | FP | UNSURE│
        │ note, analyst                │
        │ model_version  ── audit      │
        │ consumed_at   set by retrain │
        └──────────────────────────────┘
```

One alert has many verdicts. `model_versions` is referenced by version string from both other tables rather than by foreign key, which is deliberate and explained under [Audit trail](#audit-trail).

The five later tables sit outside that diagram. `drift_features` references `drift_runs` with `ON DELETE CASCADE`; the rest stand alone, and record the model versions they concern as strings, the way `alerts` does and for the same reason.

---

## alerts

One **deduplicated** detection. A burst of 5,000 flows from one compromised host collapses into a single row with `occurrence_count` incremented, not 5,000 rows.

| Column | Type | Null | Default | Index | Meaning |
| --- | --- | --- | --- | --- | --- |
| `id` | `BigInteger` (`Integer` on SQLite) | no | autoincrement | PK `pk_alerts` | Primary key |
| `kind` | `String(32)` | no | — | `ix_alerts_kind_detected_at` (1st) | `KNOWN` or `UNCLASSIFIED_ANOMALY` |
| `family` | `String(32)` | yes | — | — | Attack family; null for anomalies |
| `detection_stage` | `String(32)` | no | — | — | `stage1_supervised` or `stage2_anomaly` |
| `severity` | `String(16)` | no | — | — | `low` / `medium` / `high` / `critical` |
| `risk_score` | `Float` | no | — | `ix_alerts_status_risk_score` (2nd) | Queue ordering key — never the timestamp |
| `confidence` | `Float` | yes | — | — | Stage 1 max attack-class probability |
| `anomaly_score` | `Float` | yes | — | — | Stage 2 mean reconstruction error |
| `detected_at` | `DateTime(timezone=True)` | no | — | `ix_alerts_detected_at` | When the flow was observed |
| `src_ip` | `String(45)` | no | — | `ix_alerts_src_ip_detected_at` (1st) | Source address; 45 chars is IPv6 text length |
| `src_port` | `Integer` | yes | — | — | Source port |
| `dst_ip` | `String(45)` | no | — | — | Destination address |
| `dst_port` | `Integer` | yes | — | — | Destination port |
| `protocol` | `String(16)` | yes | — | — | Transport protocol |
| `asset_criticality` | `String(16)` | yes | — | — | Enrichment: how important the target is |
| `host_prior_alert_count` | `Integer` | no | `0` (application-side) | — | Enrichment: prior alerts from this host |
| `mitre_technique` | `String(64)` | yes | — | — | ATT&CK technique ID; null for anomalies |
| `explanation` | `JSON` | yes | — | — | Top-5 SHAP contributors or top-5 reconstruction errors |
| `narrative` | `Text` | yes | — | — | The templated English sentence |
| `recommended_actions` | `JSON` | yes | — | — | The static playbook for the family |
| `raw_flow` | `JSON` | yes | — | — | The flow record as received |
| `dedupe_key` | `String(255)` | no | — | `ix_alerts_dedupe_key_last_seen` (1st) | `src_host\|alert_class\|time_bucket` |
| `occurrence_count` | `Integer` | no | `1` (application-side) | — | How many flows collapsed into this alert |
| `first_seen` | `DateTime(timezone=True)` | no | — | — | First flow in the dedupe window |
| `last_seen` | `DateTime(timezone=True)` | no | — | `ix_alerts_dedupe_key_last_seen` (2nd) | Most recent flow in the window |
| `status` | `String(16)` | no | `open` (application-side) | `ix_alerts_status_risk_score` (1st) | Triage state |
| `model_version` | `String(64)` | no | — | — | Which model version scored this alert |
| `source` | `String(16)` | no | `replay` (application-side) | — | `replay`, `live` or `api` |
| `ground_truth_label` | `String(64)` | yes | — | — | Dataset label of the bucket's first flow; replay only, badged demo-only in the UI |
| `ground_truth_counts` | `JSON` | yes | — | — | Every dataset label the dedupe bucket absorbed, counted (Phase 8). One label is not enough for a bucket: on a replay every unclassified anomaly is attributed to the same derived host, so one five-minute bucket can hold Stage 2's benign false positives and a real catch together. Replay only; null for live capture and for rows written before Phase 8 |
| `created_at` | `DateTime(timezone=True)` | no | `CURRENT_TIMESTAMP` (server) | — | Row insert time |
| `updated_at` | `DateTime(timezone=True)` | no | `CURRENT_TIMESTAMP` (server), `onupdate` | — | Row update time |

A note on the Default column: only `created_at` and `updated_at` carry a real `server_default` in the migration. `host_prior_alert_count`, `occurrence_count`, `status` and `source` have SQLAlchemy `default=` values, which are applied by the ORM at insert time — a raw `INSERT` that omits them fails the `NOT NULL` constraint rather than silently defaulting.

### Indexes

| Index | Columns | What it serves |
| --- | --- | --- |
| `pk_alerts` | `id` | Primary key |
| `ix_alerts_detected_at` | `detected_at` | Time-window filters and the analytics range picker |
| `ix_alerts_dedupe_key_last_seen` | `dedupe_key`, `last_seen` | The dedupe lookup: one row per key inside the active window |
| `ix_alerts_status_risk_score` | `status`, `risk_score` | The triage queue: open alerts, highest risk first |
| `ix_alerts_src_ip_detected_at` | `src_ip`, `detected_at` | "Other alerts from this source in the last 24h" |
| `ix_alerts_kind_detected_at` | `kind`, `detected_at` | The one-click `UNCLASSIFIED_ANOMALY` filter chip |

Each of these exists because a specific screen or pipeline step needs it, not speculatively. The queue index is composite on `(status, risk_score)` because the queue is always filtered to open alerts *and* sorted by risk.

### Constraints

| Constraint | Rule |
| --- | --- |
| `ck_alerts_kind_valid` | `kind IN ('KNOWN', 'UNCLASSIFIED_ANOMALY')` |
| `ck_alerts_severity_valid` | `severity IN ('low', 'medium', 'high', 'critical')` |
| `ck_alerts_status_valid` | `status IN ('open', 'in_review', 'closed', 'dismissed')` |
| `ck_alerts_source_valid` | `source IN ('replay', 'live', 'api')` |
| `ck_alerts_detection_stage_valid` | `detection_stage IN ('stage1_supervised', 'stage2_anomaly')` |
| `ck_alerts_occurrence_count_positive` | `occurrence_count >= 1` |
| `ck_alerts_family_matches_kind` | `(kind = 'UNCLASSIFIED_ANOMALY' AND family IS NULL) OR (kind = 'KNOWN' AND family IS NOT NULL)` |

The last one is the thesis of the project written as a database constraint. An `UNCLASSIFIED_ANOMALY` has, by definition, no family label — Stage 2 found it precisely because Stage 1 could not name it. Making that a check constraint means no code path anywhere can produce an anomaly carrying a fabricated family, and no `KNOWN` alert can arrive without one.

Note that `family` itself has no `IN (...)` check. The allowed values live in `ALERT_FAMILIES` in `backend/app/models.py` and in the `AlertFamily` literal in `backend/app/schemas.py`; collapsing rare sub-families is a Phase 2 decision, and pinning the list in the database before that decision is made would force a migration for a mapping change.

### Relationships

`Alert.verdicts` → `list[AnalystVerdict]`, with `cascade="all, delete-orphan"` and `passive_deletes=True` so the database's `ON DELETE CASCADE` does the work rather than the ORM issuing per-row deletes.

---

## analyst_verdicts

An analyst's judgement on an alert. This is the input to active learning and the closing half of the feedback loop.

| Column | Type | Null | Default | Index | Meaning |
| --- | --- | --- | --- | --- | --- |
| `id` | `BigInteger` (`Integer` on SQLite) | no | autoincrement | PK `pk_analyst_verdicts` | Primary key |
| `alert_id` | `BigInteger` (`Integer` on SQLite) | no | — | `ix_analyst_verdicts_alert_id` | FK → `alerts.id`, `ON DELETE CASCADE` |
| `verdict` | `String(16)` | no | — | `ix_analyst_verdicts_created_at_verdict` (2nd) | `TP`, `FP` or `UNSURE` |
| `note` | `Text` | yes | — | — | Free-text analyst note |
| `analyst` | `String(128)` | yes | — | — | Who judged it |
| `model_version` | `String(64)` | yes | — | — | The model version being judged, captured at verdict time |
| `consumed_at` | `DateTime(timezone=True)` | yes | — | — | Set once a retrain has consumed this label |
| `created_at` | `DateTime(timezone=True)` | no | `CURRENT_TIMESTAMP` (server) | `ix_analyst_verdicts_created_at_verdict` (1st) | When the verdict was recorded |

### Indexes

| Index | Columns | What it serves |
| --- | --- | --- |
| `pk_analyst_verdicts` | `id` | Primary key |
| `ix_analyst_verdicts_alert_id` | `alert_id` | Verdicts for one alert; the FK lookup |
| `ix_analyst_verdicts_created_at_verdict` | `created_at`, `verdict` | The feedback screen's TP/FP breakdown over time |

### Constraints

| Constraint | Rule |
| --- | --- |
| `ck_analyst_verdicts_verdict_valid` | `verdict IN ('TP', 'FP', 'UNSURE')` |
| `fk_analyst_verdicts_alert_id_alerts` | `alert_id` → `alerts.id`, `ON DELETE CASCADE` |

`UNSURE` is a first-class value, not a missing one. An analyst who cannot decide is information — forcing a binary choice would poison the retraining pool with guesses.

`consumed_at` is what lets the feedback screen answer "how many new labels since the last retrain" without a second table: unconsumed rows are the ones with `consumed_at IS NULL`.

Foreign key enforcement on SQLite is not automatic. `backend/app/db.py` registers a `connect` event listener issuing `PRAGMA foreign_keys=ON` on every SQLite connection, so the cascade behaves the same way it would on Postgres.

---

## model_versions

The model registry: what was trained, when, on what, and with which thresholds. Backs `GET /api/v1/models` and champion/challenger promotion.

| Column | Type | Null | Default | Index | Meaning |
| --- | --- | --- | --- | --- | --- |
| `id` | `BigInteger` (`Integer` on SQLite) | no | autoincrement | PK `pk_model_versions` | Primary key |
| `version` | `String(64)` | no | — | `uq_model_versions_version` (unique) | Version string, matches `alerts.model_version` |
| `stage` | `String(16)` | no | `challenger` (application-side) | — | `champion`, `challenger` or `archived` |
| `supervised_algorithm` | `String(64)` | yes | — | — | For example `RandomForestClassifier` or `LightGBM` |
| `anomaly_algorithm` | `String(64)` | yes | — | — | For example `autoencoder` |
| `trained_at` | `DateTime(timezone=True)` | yes | — | — | When training ran |
| `trained_on` | `String(255)` | yes | — | — | Which data the run consumed |
| `schema_hash` | `String(80)` | yes | — | — | Feature-contract hash; guards train/serve skew |
| `tau_sup` | `Float` | yes | — | — | Stage 1 operating threshold from the FP budget |
| `tau_anom` | `Float` | yes | — | — | Stage 2 threshold from the benign validation percentile |
| `metrics` | `JSON` | yes | — | — | Per-class metrics, curves, LOAO results |
| `is_active` | `Boolean` | no | `false` (application-side) | — | Whether this version is serving |
| `notes` | `Text` | yes | — | — | Free text |
| `created_at` | `DateTime(timezone=True)` | no | `CURRENT_TIMESTAMP` (server) | — | Row insert time |
| `updated_at` | `DateTime(timezone=True)` | no | `CURRENT_TIMESTAMP` (server), `onupdate` | — | Row update time |

### Constraints

| Constraint | Rule |
| --- | --- |
| `uq_model_versions_version` | `version` is unique |
| `ck_model_versions_stage_valid` | `stage IN ('champion', 'challenger', 'archived')` |

Thresholds are stored here **as well as** in the artifact bundle on disk. That duplication is on purpose: an alert written months ago can be re-read against the exact threshold that produced it, even after the bundle on disk has been replaced. Without the copy, `risk_score` values from two different eras would be silently incomparable.

`schema_hash` is the same value `ModelBundle._verify_schema_hash()` checks at startup. Recording it per version means a registry row is enough to tell whether two model versions were even trained on the same feature contract.

---

## flow_samples

A systematic sample of scored flows, kept for drift measurement (Phase 7). **Not alerts:** drift is measured over the traffic, and a PSI computed from stored alerts would answer "do the alerts look unusual", a different and less useful question. `app/sampling.py::sample_scored_flows` keeps every `IDS_DRIFT_SAMPLE_STRIDE`-th flow (default 500) of each batch the replay or the live capture scores, alerting or not, systematically rather than at random so a replay produces the same sample twice.

| Column | Type | Null | Default | Index | Meaning |
| --- | --- | --- | --- | --- | --- |
| `id` | `BigInteger` (`Integer` on SQLite) | no | autoincrement | PK `pk_flow_samples` | Primary key |
| `scored_at` | `DateTime(timezone=True)` | no | — | `ix_flow_samples_scored_at`; `ix_flow_samples_scored_at_id` (1st) | When the flow was scored |
| `model_version` | `String(64)` | no | — | — | The bundle that scored it, so a PSI can be read against the model that was serving |
| `source` | `String(16)` | no | `replay` (application-side) | — | `replay`, `live` or `api`; `drift_job --source` filters on it |
| `anomaly_score` | `Float` | yes | — | — | Stage 2's error, present for every scored row, which is what lets the score overlay cover all traffic rather than its alerting tail |
| `alerted` | `Boolean` | no | `false` (application-side) | — | Whether the flow raised an alert |
| `raw_flow` | `JSON` | no | — | — | The flow as it arrived. The drift job rebuilds the feature matrix from it through `training.features.build_feature_matrix`, the function training used |
| `created_at`, `updated_at` | `DateTime(timezone=True)` | no | `CURRENT_TIMESTAMP` (server) | — | Row timestamps |

`ck_flow_samples_flow_sample_source_valid` holds `source` to the alert-source vocabulary. The table is bounded: the drift job deletes all but the newest `IDS_DRIFT_SAMPLE_KEEP` rows (default 50,000), ordered by `(scored_at, id)` so the cut is deterministic -- a drift table that grows forever becomes the largest table in the database and the slowest query in it. The prune runs in the job rather than on insert, which would put a delete in front of every batch of a 100x replay.

---

## drift_runs

One PSI computation over the sampled window, written by `python -m training.drift_job` (`make drift`). The per-feature scores live in `drift_features`; this row carries what the run concluded.

| Column | Type | Null | Default | Index | Meaning |
| --- | --- | --- | --- | --- | --- |
| `id` | `BigInteger` (`Integer` on SQLite) | no | autoincrement | PK `pk_drift_runs` | Primary key |
| `computed_at` | `DateTime(timezone=True)` | no | `CURRENT_TIMESTAMP` (server) | `ix_drift_runs_computed_at` | When the job ran |
| `model_version` | `String(64)` | no | — | — | The model serving when the window was scored |
| `observed_from`, `observed_to` | `DateTime(timezone=True)` | no | — | — | The sampled window |
| `rows_observed` | `Integer` | no | `0` | — | Samples in the window |
| `reference`, `reference_rows` | `String(255)`, `Integer` | yes | — | — | What the reference distribution was cut from, so a snapshot stays interpretable after the training data is replaced |
| `features_scored` | `Integer` | no | `0` | — | Features with a PSI |
| `max_psi` | `Float` | no | `0.0` | — | The worst feature's PSI |
| `moderate_count`, `significant_count` | `Integer` | no | `0` | — | Features in the 0.1–0.25 band, and above 0.25 |
| `retrain_recommended` | `Boolean` | no | `false` | — | True once any single feature crosses 0.25 -- any feature, not the mean, because a mean over ninety-two features hides the one that has moved entirely |
| `score_histogram` | `JSON` | yes | — | — | The observed reconstruction errors on the training baseline's bin edges, for the drift screen's overlay |
| `notes` | `Text` | yes | — | — | What the run says about itself, including why it scored nothing when the window was too thin |

The integer and boolean defaults are application-side, as on `alerts`. A window with fewer than `IDS_DRIFT_MIN_ROWS` samples still records its run, with nothing scored: PSI over a handful of rows is noise, and a retrain banner raised by noise is worse than none.

---

## drift_features

One feature's PSI inside one run. A row per feature rather than a JSON blob on the run, because the drift screen plots PSI per feature *over time* -- a query by feature across runs, which a blob would turn into a full scan and a parse.

| Column | Type | Null | Default | Index | Meaning |
| --- | --- | --- | --- | --- | --- |
| `id` | `BigInteger` (`Integer` on SQLite) | no | autoincrement | PK `pk_drift_features` | Primary key |
| `run_id` | `BigInteger` (`Integer` on SQLite) | no | — | `ix_drift_features_run_id`; `ix_drift_features_feature_run` (2nd) | FK → `drift_runs.id`, `ON DELETE CASCADE` |
| `feature` | `String(128)` | no | — | `ix_drift_features_feature_run` (1st) | Feature name from the bundle's `feature_order` |
| `psi` | `Float` | no | — | — | The Population Stability Index |
| `band` | `String(16)` | no | — | — | `stable`, `moderate` or `significant` |
| `expected`, `actual` | `JSON` | yes | — | — | The two share vectors behind the score. A PSI with no bins behind it is a number an engineer cannot act on |

`ck_drift_features_drift_band_valid` holds `band` to the three bands.

---

## retrain_runs

One champion-versus-challenger comparison, requested or completed. `POST /api/v1/retrain` inserts the row with `status = 'requested'` and returns 202; `python -m training.retrain` (`make retrain`) claims the oldest pending row, fits the challenger, evaluates both models on the same held-out split, and records the outcome whichever way it went. A challenger that lost is the more interesting row of the two: it is the evidence that the gate works.

| Column | Type | Null | Default | Index | Meaning |
| --- | --- | --- | --- | --- | --- |
| `id` | `BigInteger` (`Integer` on SQLite) | no | autoincrement | PK `pk_retrain_runs` | Primary key |
| `status` | `String(16)` | no | `requested` (application-side) | `ix_retrain_runs_status_requested_at` (1st) | `requested`, `running`, `completed`, `failed` or `cancelled` |
| `requested_by` | `String(128)` | yes | — | — | Who asked |
| `requested_at` | `DateTime(timezone=True)` | no | `CURRENT_TIMESTAMP` (server) | `ix_retrain_runs_status_requested_at` (2nd) | When it was asked for |
| `started_at`, `finished_at` | `DateTime(timezone=True)` | yes | — | — | When the worker ran it |
| `labels_consumed`, `false_positives_consumed`, `true_positives_consumed` | `Integer` | no | `0` | — | The analyst labels this run consumed; each is stamped `analyst_verdicts.consumed_at` |
| `champion_version`, `challenger_version` | `String(64)` | yes | — | — | The two models compared |
| `champion_pr_auc`, `challenger_pr_auc` | `Float` | yes | — | — | Both measured on the same held-out set inside the same run, never against a number read off an older model card |
| `held_out_split` | `String(64)` | yes | — | — | Which split that was |
| `promoted` | `Boolean` | no | `false` | — | Whether the challenger replaced the champion |
| `decision` | `Text` | yes | — | — | Why, in a sentence: the brief asks for the comparison to be logged, and a boolean is not a log |
| `comparison` | `JSON` | yes | — | — | The full logged comparison, including the guarded Stage 2 re-fit's half |
| `error` | `Text` | yes | — | — | Why a failed run failed |
| `created_at`, `updated_at` | `DateTime(timezone=True)` | no | `CURRENT_TIMESTAMP` (server) | — | Row timestamps |

`ck_retrain_runs_retrain_status_valid` holds `status` to the five values. The API refuses a second request while one is `requested` or `running`, because two concurrent runs would consume the same labels and race to publish a champion.

---

## shadow_scores

One live flow scored in shadow mode (Phase 9): measured, never alerted on. `app/live_capture.py` writes a row per flow during a burn-in, and `python -m training.calibrate_live` (`make calibrate`) cuts the local `tau_anom` from them before any live alert may reach the queue.

| Column | Type | Null | Default | Index | Meaning |
| --- | --- | --- | --- | --- | --- |
| `id` | `BigInteger` (`Integer` on SQLite) | no | autoincrement | PK `pk_shadow_scores` | Primary key |
| `session_id` | `String(32)` | no | — | `ix_shadow_scores_session_captured_at` (1st) | One burn-in run; a calibration can be cut from one run or several |
| `captured_at` | `DateTime(timezone=True)` | no | — | `ix_shadow_scores_session_captured_at` (2nd) | When the flow finished |
| `capture_source` | `String(128)` | no | — | — | `interface:<name>` or `pcap:<file>` |
| `model_version` | `String(64)` | no | — | — | The serving version; a calibration reads only the rows its Stage 2 half scored |
| `src_ip`, `dst_ip` | `String(64)` | no | — | — | Observed endpoints, kept so a burn-in can be audited -- which hosts made the traffic the threshold was cut from -- never to train on |
| `src_port`, `dst_port` | `Integer` | no | — | — | Observed ports |
| `protocol` | `String(8)` | no | — | — | `tcp` or `udp` |
| `stage1_confidence` | `Float` | yes | — | — | Stage 1's attack confidence |
| `stage2_error` | `Float` | no | — | — | The autoencoder's error on *every* flow, including the ones Stage 1 would have named: the fused decision consults Stage 2 only where Stage 1 did not alert, but a percentile of the local distribution has to be cut from all of it |
| `would_alert` | `String(32)` | yes | — | — | What the dataset thresholds would have raised: `KNOWN`, `UNCLASSIFIED_ANOMALY` or null. This is how a burn-in prices the domain shift |

`ck_shadow_scores_shadow_would_alert_valid` allows null or an alert kind. There is no `created_at`/`updated_at`: `captured_at` is the time that matters, and a shadow row is never updated.

---

## Why these types

The rule from Phase 0 is **Postgres-compatible types only, no SQLite-specific columns**, so swapping backends is a configuration change and nothing else. Four decisions follow from it.

| Decision | What was chosen | What the SQLite-native alternative would have cost |
| --- | --- | --- |
| Autoincrement keys | `BigInteger().with_variant(Integer, "sqlite")` | SQLite only auto-assigns rowids for a literal `INTEGER PRIMARY KEY`, so a plain `BigInteger` PK would not autoincrement there. A plain `Integer` PK would cap at 2^31 on Postgres — reachable for an alert table fed by a replay. The variant gets both. |
| Enumerations | `String` + `CheckConstraint` | A native `ENUM` type exists on Postgres but not on SQLite, so the same model would emit different DDL per backend. Worse, altering a Postgres enum is its own migration dialect. With a check constraint, changing the allowed set stays an ordinary migration that runs identically on both. |
| Timestamps | `DateTime(timezone=True)` | SQLite has no timestamp type at all and stores these naively, dropping the offset. A SQLite-native `TEXT` or `INTEGER` timestamp would not port to Postgres' `timestamptz`. The chosen type maps cleanly to `timestamptz` and puts the burden on the application layer, which must always hand over timezone-aware values. |
| Structured payloads | generic `JSON` | SQLite's JSON1 functions and Postgres' `jsonb` operators have different syntax. The generic type maps to `json` on Postgres and `TEXT` on SQLite, and all querying of `explanation`, `metrics` and `raw_flow` happens in Python rather than in backend-specific SQL. |

### Naming convention

`backend/app/db.py` attaches a `NAMING_CONVENTION` to the shared `MetaData`:

```python
{
  "ix": "ix_%(table_name)s_%(column_0_N_name)s",
  "uq": "uq_%(table_name)s_%(column_0_N_name)s",
  "ck": "ck_%(table_name)s_%(constraint_name)s",
  "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
  "pk": "pk_%(table_name)s",
}
```

Without it, Alembic autogenerate emits unnamed constraints, which SQLite cannot later `ALTER` and Postgres names differently — and migrations then diverge per backend. Every constraint name in the tables above comes from this convention.

### Swapping the backend

One line. `IDS_DATABASE_URL` in `.env`:

```bash
# development default
IDS_DATABASE_URL=sqlite+pysqlite:///data/ids.db

# Postgres
IDS_DATABASE_URL=postgresql+psycopg://ids:ids@localhost:5432/ids
```

`Settings.sqlalchemy_url` anchors relative SQLite paths to the repo root before handing the URL to the engine, because a bare `sqlite:///data/ids.db` resolves against the working directory and would otherwise produce a different database depending on where uvicorn was launched. For a Postgres URL the value passes through unchanged.

`_build_engine()` also applies two SQLite-only runtime settings that have no Postgres equivalent because Postgres already behaves that way: `PRAGMA foreign_keys=ON`, and `PRAGMA journal_mode=WAL` so the replay writer does not block dashboard readers. `check_same_thread=False` is passed for SQLite only, because one file is touched by request handlers and by the replay background task on different threads.

See [Configuration](Configuration.md) for the full settings list.

---

## Lifecycle

### Where rows come from

| Table | Written by | Since |
| --- | --- | --- |
| `alerts` | `app/pipeline.py::ingest_batch`, from a replay (`app/replay.py`, and `make seed`) or a live capture in alert mode (`app/live_capture.py`). `PATCH /api/v1/alerts/status` moves `status` | Phase 5 |
| `analyst_verdicts` | `POST /api/v1/alerts/{id}/verdict` from the Alert Detail footer; the retrain job stamps `consumed_at` | Phase 5 |
| `model_versions` | `app/registry.py::register_champion` at every API start, for the bundle actually loaded; a new version archives the previous champion | Phase 7 |
| `flow_samples` | `app/sampling.py::sample_scored_flows`, from replay and live capture alike; pruned by the drift job | Phase 7 |
| `drift_runs`, `drift_features` | `python -m training.drift_job` (`make drift`) | Phase 7 |
| `retrain_runs` | `POST /api/v1/retrain` inserts; `python -m training.retrain` (`make retrain`) runs and completes it | Phase 7 |
| `shadow_scores` | `app/live_capture.py` in shadow mode | Phase 9 |

A fresh deployment starts empty. `make seed`, and the container's first start, fill `alerts` and `flow_samples` from a replay of the committed demo flows and run one drift snapshot, so the dashboard opens on real data rather than on nothing.

The alert path in order, with the modules that own each step. All are implemented as of Phase 5, and `app/pipeline.py::ingest_batch` is what sequences them — with one swap from the order below: **Enrich runs before Dedupe**, because `risk_score` reads the enrichment and the dedupe upsert needs the final score to keep the higher of a burst:

```
scored flow
    │
    ├─ explain.py       top-5 SHAP (Stage 1) / top-5 reconstruction errors (Stage 2)
    │                   → alerts.explanation
    ├─ explain.narrate  templated English sentence
    │                   → alerts.narrative
    ├─ mitre.py         technique ID + plain-English description
    │                   → alerts.mitre_technique
    ├─ remediation.py   static playbook for the family
    │                   → alerts.recommended_actions
    ├─ dedupe.py        dedupe_key(src_host, alert_class, ts)   [implemented]
    │                   → hit:  UPDATE occurrence_count, last_seen
    │                     miss: INSERT a new row
    ├─ enrichment       asset criticality, prior alert count
    │                   → alerts.asset_criticality, alerts.host_prior_alert_count
    └─ persist + push over SSE
```

`dedupe_key()` is implemented in Phase 0 because it is pure, cheap to test, and the column that stores its output already exists. It floors the timestamp into a bucket of `IDS_DEDUPE_WINDOW_SECONDS` seconds (default 300) and returns `"<src_host>|<alert_class>|<bucket>"`.

### Drift snapshots

`python -m training.drift_job` reads the `flow_samples` window, rebuilds each sample's feature vector through `training.features.build_feature_matrix`, scores PSI per feature against the persisted training reference, and writes one `drift_runs` row with a `drift_features` row per feature. It refuses rather than guesses when the reference is missing or was cut against a different schema hash. `GET /api/v1/metrics/drift` serves what it wrote; nothing computes drift per request.

### Retention

Only `flow_samples` is bounded today (above). The rest is worth deciding before a long-running deployment rather than after. The considerations:

| Table | Growth driver | Consideration |
| --- | --- | --- |
| `alerts` | Flow volume, moderated heavily by dedupe | Dedupe is what makes retention tractable — one incident is one row regardless of flow count. `raw_flow` and `explanation` are the bulk of the row size; a policy that ages out those JSON payloads while keeping the alert metadata preserves the analytics history at a fraction of the size. |
| `analyst_verdicts` | Analyst throughput, so bounded by human effort | These are training labels. They should outlive the alerts they describe, which is an argument for archiving rather than cascading them away. Note the current FK cascades: deleting an alert deletes its verdicts. |
| `model_versions` | One row per version that has served | Negligible. Never delete — an archived version is the only record of what scored an old alert. |
| `drift_runs`, `drift_features` | One run per job, ninety-two feature rows each | Small per night; a year of nightly runs is about 34,000 feature rows. |
| `retrain_runs` | One row per request | Negligible, and the declined runs are evidence the gate works. Keep them. |
| `shadow_scores` | One row per live flow while a burn-in runs | The one to watch: a long burn-in on a busy interface writes a row per flow. Rows for a Stage 2 model that no longer serves are dead weight once its calibration is superseded. |

Alerts from `source = 'replay'` are demo data and are the obvious first candidate for truncation; `source = 'live'` rows are the ones with real-world value. The column exists specifically so that distinction is queryable.

---

## Migrations

Alembic, configured by `backend/alembic.ini` with `backend/alembic/env.py` reading the URL from settings rather than from the ini file. Four revisions, in order:

| Revision | Message | Adds |
| --- | --- | --- |
| `9a6857dcba76` | initial schema | `alerts`, `analyst_verdicts`, `model_versions` |
| `b2452313f8f5` | Phase 7: drift snapshots, flow samples and retrain runs | `flow_samples`, `drift_runs`, `drift_features`, `retrain_runs` |
| `23464adcb561` | Phase 8: ground truth counts per alert bucket | `alerts.ground_truth_counts` |
| `930ac6f6f5c7` | Phase 9: shadow scores, the live burn-in | `shadow_scores` |

### Apply

```bash
cd backend
uv run alembic upgrade head
```

### Create a new one

```bash
cd backend
uv run alembic revision --autogenerate -m "add drift snapshots"
```

Autogenerate is a starting point, not a finished migration. The initial revision carries the comment "Autogenerated from app/models.py, then reviewed" on both `upgrade()` and `downgrade()` — read the diff before committing it. In particular, autogenerate does not detect check-constraint changes reliably, and it will happily drop a column it cannot match.

### Inspect and roll back

```bash
uv run alembic current      # which revision the database is at
uv run alembic history      # the revision graph
uv run alembic downgrade -1 # back one revision
```

### Batch mode for SQLite

The initial migration wraps every index operation in `with op.batch_alter_table(...)`. SQLite cannot `ALTER TABLE` in most of the ways Postgres can, so Alembic emulates the change by recreating the table. Batch mode is what makes the same migration file run unmodified on both backends — keep using it for anything that alters an existing table.

Full detail on the migration environment is on [Code: Backend Migrations](Code-Backend-Migrations.md).

---

## Audit trail

**Every alert records which model version scored it.** The column is `alerts.model_version`, `String(64)`, `NOT NULL`. There is no code path that inserts an alert without one.

This is non-negotiable for anything security-adjacent. Without it, a question as ordinary as "did this alert come from the model we rolled back last week?" has no answer, and a model promotion silently rewrites the meaning of every historical score.

The audit chain runs through three places:

| Where | Column / field | What it pins down |
| --- | --- | --- |
| `alerts` | `model_version` | Which version produced this detection |
| `analyst_verdicts` | `model_version` | Which version the analyst was judging, captured at verdict time rather than looked up later |
| `model_versions` | `version`, `schema_hash`, `tau_sup`, `tau_anom`, `trained_at`, `trained_on`, `metrics` | What that version actually was |

`analyst_verdicts.model_version` is nullable while `alerts.model_version` is not, because a verdict can in principle be recorded against an alert whose scoring model is no longer the active one and the value is a snapshot rather than a requirement.

Note what is deliberately *not* a foreign key: `alerts.model_version` does not reference `model_versions.version`. An alert must retain the version string that scored it even if that registry row is later pruned or the registry is rebuilt — the audit record is the string itself, and a constraint that could make an old alert un-insertable or force a cascade would defeat the purpose. The link is by value, and joining on it is an ordinary equality join.

The serving side of the same guarantee is the schema hash. `ModelBundle._verify_schema_hash()` raises `SchemaHashMismatch` at startup when a bundle's stored hash disagrees with the recomputed hash of its `feature_order`, and the process does not come up. Storing that hash per registry row means the database can answer whether two versions shared a feature contract, not just whether they had different names.
