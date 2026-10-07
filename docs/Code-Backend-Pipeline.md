# Code Reference — Alert Pipeline Modules

This page documents the modules under `backend/app/` that turn a model score into an alert an analyst can act on: explanation, narration, MITRE mapping, remediation lookup, risk scoring, derived addressing, deduplication, the pipeline that sequences them, the SSE broker, replay, drift measurement and live capture.

As of **Phase 9** every module on this page is implemented: the alerting path since Phase 5, `drift.py` since Phase 7, and live capture with its flow meter since Phase 9. Phase 5 added four modules this page did not originally list — `pipeline.py`, `events.py`, `risk.py` and `topology.py`. The Phase 7 helpers `sampling.py` (drift samples), `feedback.py` (labels and the guarded refit pool) and `registry.py`, and Phase 8's `release.py` and `seed.py`, sit beside these modules and are documented in their own docstrings.

| File | Lines | Role |
| --- | --- | --- |
| `backend/app/flowmeter.py` | 609 | Packets to CICIDS2017 flow features, CICFlowMeter-V3's quirks included (Phase 9) |
| `backend/app/live_capture.py` | 473 | Live capture: an authorised interface or pcap, in shadow or alert mode (Phase 9) |
| `backend/app/explain.py` | 470 | Per-alert feature attribution and English narration |
| `backend/app/replay.py` | 408 | Accelerated replay of held-out test flows |
| `backend/app/pipeline.py` | 404 | `ingest_batch` — sequences the six stages |
| `backend/app/risk.py` | 387 | `risk_score` and `severity` — the queue's ordering key |
| `backend/app/topology.py` | 324 | Derived addresses, asset criticality, provenance |
| `backend/app/drift.py` | 300 | Population Stability Index and drift reports (Phase 7) |
| `backend/app/remediation.py` | 196 | Attack family to recommended-response playbook |
| `backend/app/events.py` | 186 | The SSE broker |
| `backend/app/dedupe.py` | 154 | Collapses alert bursts onto one incident row |
| `backend/app/mitre.py` | 136 | Attack family to MITRE ATT&CK technique lookup |

---

## The alert pipeline in order

The order every alert passes through is fixed. Each step is owned by exactly one module, and the order is not negotiable: dedupe has to see a classified alert, so it runs after the family is known, but it has to run before persistence, so a burst never becomes 5,000 rows.

```text
 scored flow (stage 1 probability, stage 2 reconstruction error)
 |
 v
 [1] EXPLAIN explain.py TreeSHAP top-5 (Stage 1)
 | recon-error top-5 (Stage 2)
 v
 [2] NARRATE explain.py per-feature phrase map -> one English sentence
 |
 v
 [3] MAP + RECOMMEND mitre.py family -> technique ID + plain-English meaning
 | remediation.py family -> recommended response playbook
 v
 [4] ENRICH topology.py asset criticality for the destination host
 | pipeline.py prior alert count for the source host
 | risk.py -> risk_score and severity
 v
 [5] DEDUPE dedupe.py key (src_host, alert_class, floor(ts, window))
 | hit -> occurrence_count += 1, last_seen = ts,
 |         risk_score = max(existing, new)
 | miss -> insert
 v
 [6] PERSIST db.py INSERT into alerts
    |
        v
  [7] PUSH              routes/stream.py  server-sent event to every connected dashboard
```

Ownership and status today:

| Step | Owner | Status |
| --- | --- | --- |
| 1 Explain | `explain.explain_supervised`, `explain.explain_anomaly` | implemented — batched, one explainer call per stage per batch |
| 2 Narrate | `explain.narrate` | implemented — a static per-feature phrase map over all 92 feature names |
| 3 Map | `mitre.technique_for` | implemented — `None` for an unclassified anomaly, by design |
| 3 Recommend | `remediation.advice_for` | implemented — playbook plus technique in one call, so the two panels cannot disagree |
| 4 Enrich | `topology.criticality_for`, a per-host alert count, then `risk.risk_score` / `risk.severity` | implemented |
| 5 Dedupe | `dedupe.dedupe_key`, `dedupe.upsert_alert` | implemented — insert, or increment and keep the higher risk |
| 6 Persist | `app/models.py`, `app/db.py` | implemented — one commit per batch, owned by the caller |
| 7 Push | `events.EventBroker`, `app/routes/stream.py` | implemented — published after persistence, never before |

`pipeline.ingest_batch` is what sequences all of it.

**Enrich and Dedupe are swapped relative to the project brief's own list**, which puts Dedupe fourth. `risk_score` takes asset criticality and the host's prior alert count as two of its four inputs, and the dedupe upsert needs the *final* `risk_score` so a repeat hit can keep the highest risk in a burst. Deduping first would mean either persisting a score the enrichment is about to change, or reopening the row to patch it in.

Two things turned up in implementation that the design had not anticipated, and both are the kind that fail quietly:

- `SessionLocal` is built with `autoflush=False`, so `upsert_alert` has to flush a new row explicitly. Without it, a second alert in the same uncommitted batch sharing the same key would run its lookup against a database that does not contain the first insert yet, and would wrongly insert a duplicate instead of merging into it.
- SQLite strips `tzinfo` on the round trip, so a row re-read in a later batch comes back naive even though only tz-aware UTC was ever written. Comparing it with `max()` raises `TypeError` and would take down a whole batch over a timestamp that is naive for storage reasons rather than because anyone meant local time. `dedupe.ensure_aware` repairs it, and `pipeline` uses the same helper before putting `detected_at` on the wire — see the bug note in `reports/phase5_api.md`.

See [Database Schema](Database-Schema.md) for the columns and [Roadmap](Roadmap.md) for what each phase delivers.

---

## explain.py

`backend/app/explain.py` — produces the top-5 feature attribution behind an alert and templates it into a sentence an analyst can read.

Every alert leaves this module carrying a reason. The module docstring states the rule directly: an alert with a score and no reason is an alert an analyst ignores. That is why explanation is step 1 of the pipeline rather than an optional decoration on the Alert Detail screen.

Two explainers exist because the two stages are different kinds of model and one explainer would be wrong for one of them. Stage 1 is a tree ensemble, so TreeSHAP gives exact per-feature contributions cheaply. Stage 2 is an autoencoder, and it hands its explanation over for free: the features it failed hardest to reconstruct are precisely why the row looks anomalous. The docstring spells the computation out.

```python
per_feature_error = (x - x_hat) ** 2
top_contributors  = argsort(per_feature_error)[-5:]
```

KernelSHAP on a neural network was considered and rejected in the same docstring as slow, approximate, and buying nothing over the reconstruction error the model has already computed. `narrate` is the second half of the job: it converts feature names and contribution values into English through a per-feature phrase template map. The docstring fixes the target register with a worked example — "2,400 distinct destination ports contacted in 8 seconds from a single source."

| Symbol | Kind | Signature | Description |
| --- | --- | --- | --- |
| `explain_supervised` | function | `(model, matrix, feature_order, classes, families, k=5) -> list[dict]` | TreeSHAP attribution for the Stage 1 rows that alerted, top-5 contributors each. |
| `explain_anomaly` | function | `(model, matrix, feature_order, k=5) -> list[dict]` | Per-feature reconstruction error for the Stage 2 rows that alerted, top-5 each. |
| `narrate` | function | `(kind, family, contributors, flow=None) -> str` | Templates an explanation into one English sentence via `FEATURE_PHRASES`. |
| `FEATURE_PHRASES` | dict | `dict[str, str]` | A noun phrase per feature, covering all 92 persisted feature names. |

- **Both explainers take a matrix, not a row**, and the caller masks the alerting rows before calling. That is the batching contract: one `TreeExplainer` and one `shap_values` call per batch, never per row.
- `families` is a parameter rather than something recomputed, because each row must be attributed against the column of the family Stage 1 **named**. The row's own SHAP argmax can disagree, and attributing against the wrong column produces a fluent explanation of a different alert that nothing downstream can detect. The normalised SHAP shape is asserted against the bundle's dimensions for the same reason.
- `narrate` must be given the **raw flow** dict, whose destination port is keyed `destination_port` (`training.features.PORT_COLUMN`) — not the `dst_port`-keyed column values. Handing it the latter makes the observed-port clause silently never fire, with no error, while `explain.py`'s own tests still pass because they build the dict themselves.
- Top-5 is a fixed budget, not a threshold. Five ranked contributors is what the Alert Detail screen has room for and what an analyst reads.
- Status: **implemented (Phase 5).** `explain_supervised` builds one `shap.TreeExplainer` per call and runs it over the whole batch of alerting rows, attributing each row against the column of the family Stage 1 **named** rather than the row's own argmax — the two can disagree, and attributing against the wrong column produces a fluent, confident explanation of a different alert that nothing downstream can detect. It normalises whatever shape `shap_values` returns into `(rows, features, classes)` and raises if that does not match the bundle's own dimensions. `explain_anomaly` delegates to `training.autoencoder.per_feature_error` and `top_contributors` rather than re-deriving `(x - x_hat) ** 2`, because a second copy of the formula is a second thing that can drift from the score it explains. `narrate` templates either explanation into one sentence from a static 92-entry phrase map. Both explainers import lazily — `shap` pulls numba, `training.autoencoder` pulls torch — so a process that never explains that stage does not pay for it.

---

## mitre.py

`backend/app/mitre.py` — maps a detected attack family onto a MITRE ATT&CK technique ID plus a one-line plain-English description of what that technique means.

The Alert Detail screen has to answer "what is this, likely?" without the analyst opening a second tab. That is the whole job of this module: family in, technique ID and a sentence out, from a static reviewed lookup.

`UNCLASSIFIED_ANOMALY` maps to no technique, on purpose. The docstring is explicit that saying so plainly is the honest answer and that it is the entire point of Stage 2 — a detector whose value is catching families nobody named cannot then be asked to name them. Any technique invented for an unclassified anomaly would be a fabrication presented with exactly the same confidence as a real mapping.

The same lookup backs the MITRE coverage heatmap on the analytics screen, served by `GET /api/v1/analytics/mitre-coverage`.

| Symbol | Kind | Signature | Description |
| --- | --- | --- | --- |
| `technique_for` | function | `technique_for(family: str \| None) -> Technique \| None` | The technique id, name, URL and plain-English meaning for a family; `None` for an unclassified anomaly; **raises** for a family outside the vocabulary. |
| `coverage_vocabulary` | function | `() -> list[dict]` | Every technique the table knows, for the heatmap's axis — so a zero-hit technique is a visible zero. |

- The `| None` in the return type is the contract for `UNCLASSIFIED_ANOMALY`. Callers must handle `None` and render "no matching technique" rather than an empty string.
- The data this module serves is the reviewed table reproduced in the [remediation.py](#remediationpy) section below. One table backs both the technique mapping and the response playbook, so the two can never disagree about which families exist.
- Status: **implemented (Phase 5).** `technique_for` returns the entry for a family, `None` for an unclassified anomaly, and **raises** for a family outside `app.models.ALERT_FAMILIES` — the model can only emit that vocabulary, so anything else is a bug upstream and returning a plausible technique would hide it. `coverage_vocabulary()` builds the heatmap's axis from the table rather than from the alerts that have fired, so a technique with zero hits is a visible zero.

---

## remediation.py

`backend/app/remediation.py` — maps a detected attack family onto a recommended response playbook.

This module is a static, reviewed lookup table, and the docstring argues the point rather than merely stating it: a fixed playbook is something a SOC can trust, while advice improvised per alert has to be re-verified every time, which defeats the purpose of having it. There is no generative step anywhere on this path.

The unclassified case gets the honest entry — no playbook exists yet, route for manual investigation. The docstring's rule is that a wrong playbook does more damage than an honest shrug, because an analyst who follows fabricated remediation advice takes real action on a real network.

### The reviewed static lookup

Both `mitre.py` and `remediation.py` are populated from this single table. It is reproduced here in full as the data those modules serve. It is the source the Phase 5 implementation transcribed, not a sample of output.

| Family | Technique | What it usually means | Recommended response |
| --- | --- | --- | --- |
| Brute force (FTP/SSH/web) | T1110 | Repeated login attempts against one service, guessing credentials | Lock or rotate the targeted account, enforce MFA, rate-limit or geo-fence the service |
| DoS / DDoS | T1498, T1499 | Traffic volume aimed at exhausting a service's capacity | Enable upstream rate-limiting or scrubbing, fail over the target, block the source range at the edge |
| Port scan | T1046 | A host enumerating open ports on another host, usually reconnaissance | Review firewall rules on the scanned host, confirm no unintended exposure, watch the source for follow-up activity |
| SQL injection / web attack | T1190 | Malformed input aimed at a public-facing application | Patch or WAF-rule the endpoint, rotate credentials the app uses, review recent writes to the database |
| Botnet C2 | T1071 | A host checking in with an external command-and-control server | Isolate the host, image it before wiping, rotate any credentials that lived on it |
| Infiltration | T1204 | A host behaving as if it has been used as an entry point | Isolate the host, check for lateral movement from it, review what it could reach |
| Unclassified anomaly | (none) | Does not match a known technique — that is the entire point of Stage 2 | No playbook exists yet. Say so explicitly rather than guessing, and route it for manual investigation |

Why the table is static rather than generated:

- **Reviewability.** Seven rows can be read, argued over and signed off by the people who will act on them. Advice produced per alert cannot be reviewed before it is acted on.
- **Repeatability.** The same family produces the same guidance every time, so an analyst builds working knowledge of what the system tells them. Guidance that varies per alert has to be re-verified per alert.
- **Blast radius.** These recommendations end in actions like isolating a host or rotating credentials. Being confidently wrong here is expensive, and nothing downstream would catch a plausible-sounding fabrication before someone followed it.
- **Honest gaps.** A lookup can return "no entry". A generator always produces something, which is exactly the failure mode the unclassified row is written to prevent.

The family names in the first column correspond to the `AlertFamily` literal in `app/schemas.py` — `dos`, `ddos`, `brute_force`, `port_scan`, `web_attack`, `botnet`, `infiltration` — and the last row corresponds to the `AlertKind` value `UNCLASSIFIED_ANOMALY`.

| Symbol | Kind | Signature | Description |
| --- | --- | --- | --- |
| `playbook_for` | function | `playbook_for(family) -> Playbook` | The response playbook for a family; for an unclassified anomaly the populated `NO_PLAYBOOK` entry rather than an empty one, because an empty panel reads as a bug rather than as an admission. Raises for a family outside the vocabulary. |
| `advice_for` | function | `advice_for(family) -> dict` | Playbook **and** technique in one call, so the "what this is" and "how to fix it" panels cannot disagree about whether a technique exists. |

- Unlike `technique_for`, this return type is not optional. Every family including the unclassified one has an entry, because "no playbook exists yet, route for manual investigation" is itself an answer the analyst needs on screen.
- Nothing in this module or downstream of it executes a remediation. The system alerts, ranks and explains; containment is manual and confirmed by a human. `IDS_ALLOW_AUTO_BLOCK` is rejected by a validator in `app/config.py` so that the constraint is greppable rather than merely absent — see [Configuration](Configuration.md).
- Status: **implemented (Phase 5).** `playbook_for` returns a family's checklist, and for an unclassified anomaly returns `NO_PLAYBOOK` — a *populated* honest entry rather than an empty one, because an empty response renders as a missing panel and reads as a bug. `advice_for` returns the playbook and the technique together, in one call, so the "what this is" and "how to fix it" panels can never disagree about whether a technique exists. A test asserts no action reads as something the software does by itself.

---

## dedupe.py

`backend/app/dedupe.py` — collapses a burst of near-identical alerts onto a single incident row.

One compromised host emitting 5,000 flows is one incident, not 5,000 alerts. Without this step the triage queue is unusable within thirty seconds of starting a replay, which is the module docstring's own justification for existing.

The rule: on a key hit, increment `occurrence_count` and update `last_seen` on the existing row instead of inserting a new one. The `alerts` table carries `dedupe_key`, `occurrence_count` — with a `CheckConstraint` named `occurrence_count_positive` requiring `occurrence_count >= 1` — and `last_seen`, plus the composite index `ix_alerts_dedupe_key_last_seen` so the lookup on each incoming alert is cheap.

The dedupe key is exactly `(src_host, alert_class, floor(ts, dedupe_window_seconds))`, serialised as a pipe-delimited string:

```python
window = settings.dedupe_window_seconds
epoch = int(timestamp.timestamp())
bucket = epoch - (epoch % window)
return f"{src_host}|{alert_class}|{bucket}"
```

The third component is a floored epoch-second bucket, not the raw timestamp. `bucket` is the start of the window the event falls in, so every alert inside one window from the same host with the same class produces a byte-identical key. Window length comes from `IDS_DEDUPE_WINDOW_SECONDS`, default 300 seconds, read through `settings.dedupe_window_seconds`.

| Symbol | Kind | Signature | Description |
| --- | --- | --- | --- |
| `dedupe_key` | function | `dedupe_key(src_host: str, alert_class: str, timestamp: dt.datetime) -> str` | Builds the deduplication key from source host, alert class and the floored time bucket. Implemented. |

- This is the only pipeline function with a real body in Phase 0. Its docstring says why: it is pure, cheap to test, and the schema column that stores it already exists.
- Because the bucket is computed by flooring rather than by a sliding window, two alerts 299 seconds apart land in different buckets when they straddle a boundary. That is accepted. Fixed buckets are deterministic, index-friendly, and need no read-before-write to decide which window an event belongs to.
- `timestamp.timestamp()` interprets a naive datetime as local time. Alert timestamps are stored as `DateTime(timezone=True)`, so the Phase 5 caller must pass timezone-aware values for keys to stay stable across hosts.
- Changing `IDS_DEDUPE_WINDOW_SECONDS` changes every future key. Existing rows keep the keys they were written with, so a window change partitions the history rather than corrupting it.

---

## drift.py

`backend/app/drift.py` — measures how far live traffic has moved from the distribution the models were trained on.

A nightly job computes the Population Stability Index for each feature against the training reference distribution and stores a snapshot. PSI answers one question: has the shape of this feature changed enough that the model's calibration can no longer be trusted?

The formula, as given in the module docstring:

```text
PSI = sum over bins of (actual_pct - expected_pct) * ln(actual_pct / expected_pct)
```

`expected_pct` is the proportion of the training reference data falling in a bin; `actual_pct` is the proportion of recent live data in the same bin. The warning bands are fixed at 0.1 and 0.25.

| PSI | Reading | Consequence |
| --- | --- | --- |
| below 0.1 | stable | no action |
| 0.1 to 0.25 | moderate shift | worth watching on the drift screen |
| above 0.25 | significant shift | raises the retrain-recommended banner |

The drift job also overlays the training benign score distribution on the window's scored traffic (24 hours by default). When those two curves separate, the baseline has moved regardless of what any individual feature's PSI says.

| Symbol | Kind | Signature | Description |
| --- | --- | --- | --- |
| `population_stability_index` | function | `population_stability_index(expected, actual, *, epsilon=EPSILON) -> float` | PSI between two share vectors read off the same bins. Counts or mismatched lengths are refused with `DriftError` rather than silently scored |
| `quantile_edges` | function | `quantile_edges(values, bins=DEFAULT_BINS) -> list[float]` | Bin edges at evenly spaced quantiles of the reference, deduplicated: a binary feature gets two bins, not ten with eight that no row can fall into |
| `bin_shares` | function | `bin_shares(values, edges) -> list[float]` | The share of values in each bin of fixed edges |
| `band_for` | function | `band_for(psi) -> str` | `stable`, `moderate` or `significant`, at 0.1 and 0.25 |
| `measure_drift` | function | `measure_drift(references, observed, *, rows_observed=0, epsilon=EPSILON) -> DriftReport` | Scores every feature with both a reference and an observation, and skips the rest rather than scoring against a default |
| `FeatureReference`, `FeatureDrift`, `DriftReport` | dataclasses | — | A feature's persisted reference (edges and expected shares), one feature's result, and the run's |

- PSI is defined per feature. A model-level drift verdict is an aggregation over per-feature values plus the score-distribution overlay, never a single number.
- The 0.1 and 0.25 bands are the conventional thresholds, left unchanged on purpose so a number on the drift screen means the same thing it means everywhere else.
- **Shared bin edges.** The edges come from the reference and stay fixed. Binning each side independently — deciles of each — produces a number near zero no matter how far the distribution has moved, because both sides are rescaled to their own shape.
- **Empty bins.** Both sides are floored at a small epsilon, so a bin the traffic left entirely — exactly what a drift monitor exists to catch — caps its contribution instead of turning the sum infinite.
- The reference is persisted (`artifacts/drift_reference.json`, cut by `python -m training.drift_reference` at `IDS_DRIFT_BINS` bins), so the nightly job needs the artifact and not the training data. Nothing in this module reads a parquet file, and it is pure Python because the API imports it.
- Computed by `python -m training.drift_job` (`make drift`) over the `flow_samples` window, stored in `drift_runs` and `drift_features`, and served by `GET /api/v1/metrics/drift` — see [API Reference](API-Reference.md#get-apiv1metricsdrift).
- Status: **implemented (Phase 7).**

---

## replay.py

`backend/app/replay.py` — streams held-out test flows through the live scoring path at accelerated time so the dashboard has real traffic to show.

There is no live enterprise traffic available to this project, so replay is the primary traffic source. It is an asyncio background task that reads held-out test rows, scores them in batches and pushes the resulting alerts over SSE at 1x, 10x or 100x wall-clock speed.

The docstring calls this honest, and the word carries weight: these are real flows with real ground-truth labels, so the dashboard can display predictions against truth. Ground truth is surfaced in the UI badged as demo-only, and never for live capture, where no labels exist.

Batch scoring is mandatory in this loop. Per-row `predict()` is roughly 50x slower and makes the demo stutter — the same constraint that shapes `POST /api/v1/score`. The module optionally accepts a pcap upload, runs CICFlowMeter over it, and scores the resulting flows through the same features module, so an operator-supplied capture takes exactly the path a dataset row takes.

| Symbol | Kind | Signature | Description |
| --- | --- | --- | --- |
| `start_replay` | async function | `(*, bundle, broker, state, speed, dataset) -> dict` | Validates the dataset, arms `ReplayState`, marks the broker's source active, and creates the background task. Returns the status payload. |
| `stop_replay` | async function | `(*, state, broker) -> dict` | Cancels the task and **awaits** it, so a following start cannot race a run that is still writing. Returns the run's final counters. |
| `ReplayState` | dataclass | — | `running`, `speed`, `dataset`, `started_at`, `rows_scored`, `alerts_emitted`, plus the task handle, which `as_status()` omits. |
| `load_replay_rows` | function | `(dataset) -> (flows, labels)` | Reads a split into flow dicts plus their published labels, kept separate so the label never reaches the feature matrix. |

- Both functions are coroutines because replay is an asyncio task inside the running FastAPI process, not a separate worker. Starting it must not block the request that started it.
- Speed is a multiplier on the inter-flow delay, not a change to batch size. The scoring work per flow is identical at 1x and at 100x.
- Driven by `POST /api/v1/replay/start` and `POST /api/v1/replay/stop`.
- Status: **implemented (Phase 5).** `start_replay` validates the dataset before arming any state, so a rejected start leaves the service idle rather than wedged; `stop_replay` awaits the cancelled task so a following start cannot race a run that is still writing. The engine streams a held-out split in fixed 500-row batches and paces with `BATCH_ROWS / (BASE_FLOWS_PER_SECOND * speed)`, so **speed moves the gap between batches and never the batch size** — growing the batch would make 100x a different computation rather than the same one delivered faster. `train` and `benign_train` are refused: replaying the rows a model was fitted on demonstrates memorisation, not detection.

---

## live_capture.py

`backend/app/live_capture.py` — the second traffic source beside replay: packets from an authorised interface or a recorded pcap, metered into flows by [flowmeter.py](#flowmeterpy), scored, and in alert mode alerted on, through the code a replay uses.

Live capture is a second traffic source, never a replacement for replay. It feeds the exact same feature contract, the exact same inference path (`ModelBundle.score_batch`) and the exact same alert pipeline (`pipeline.ingest_batch`). Live traffic needing its own scoring code would break the train/serve-skew defence the feature contract exists to provide.

**Authorisation is a hard precondition, and it is configuration.** Capture runs only on interfaces listed in `IDS_LIVE_INTERFACES`, empty by default, and reads pcaps only from `IDS_LIVE_PCAP_DIR`. Nothing a request sends can widen either. Packet capture on a network you do not own or are not authorised to monitor is illegal in most places regardless of intent.

**Shadow first.** First contact with real traffic over-fires: on this project's own burn-in, the CICIDS2017 threshold would have flagged 11.8% of perfectly ordinary flows. So a capture runs in shadow mode — score everything, alert no one, write `shadow_scores` — until `python -m training.calibrate_live` has cut a local `tau_anom` from the network's own reconstruction errors, and alert mode is refused until a calibration exists for the serving Stage 2 model.

| Symbol | Kind | Signature | Description |
| --- | --- | --- | --- |
| `start_ingest` | async function | `start_ingest(*, bundle, broker, state, replay_running, source, interface, pcap, mode) -> dict` | Validates everything — mode and source, the allow-list or the pcap path, a calibration for alert mode, no replay running — before arming any state, then starts the capture task. A refused start leaves nothing armed |
| `stop_ingest` | async function | `stop_ingest(*, state) -> dict` | Signals the capture to stop and waits up to 30 seconds for the flows still open to be finished and scored |
| `score_flows` | function | `score_flows(*, bundle, broker, state, flows, calibration) -> None` | One batch. Shadow: `score_batch` at the dataset thresholds, into `shadow_scores` plus drift samples. Alert: `score_batch` at the local threshold, into `ingest_batch` with the observed endpoints, the provenance note, and the local threshold and burn-in error histogram to rank against |
| `check_interface` | function | `check_interface(name) -> None` | Raises `CaptureNotAuthorised` (the API's 403) unless the name is in `IDS_LIVE_INTERFACES` |
| `resolve_pcap` | function | `resolve_pcap(name) -> Path` | The file inside `IDS_LIVE_PCAP_DIR`, or `UnknownPcap` (404) — a name that resolves anywhere else included |
| `available_pcaps` | function | `available_pcaps() -> list[str]` | The recorded captures `/ingest/status` offers |
| `usable_local_threshold` | function | `usable_local_threshold(bundle) -> dict \| None` | The calibration in `artifacts/local_threshold.json`, returned only if it was cut against the serving Stage 2 model: a percentile of one model's errors means nothing against another's |
| `observed_provenance` | function | `observed_provenance(tau_anom, calibration) -> dict` | The `_provenance` a live alert carries: every endpoint observed, `detected_at` on the capture clock, and the threshold and calibration it was decided at |
| `IngestState` | dataclass | — | Mode, source, session and counters, as `/ingest/status` reports them |

- The capture runs on a thread — `_capture_interface` over an `AF_PACKET` raw socket, or `_capture_pcap` — feeding finished flows onto a queue. The asyncio task drains it every `IDS_LIVE_BATCH_INTERVAL_S` seconds in batches of up to 500 and scores each through `asyncio.to_thread`, so neither packet reading nor scoring blocks the event loop that serves the alert stream.
- The socket is never put in promiscuous mode. On loopback the kernel delivers each frame twice, as outgoing and as incoming; the outgoing copy is dropped so no packet is counted twice.
- Replay and capture never run at once: one writer keeps the dedupe upsert's single-writer invariant, and each start refuses with 409 while the other source is running.
- Nothing here blocks, drops or shapes traffic. Capture is read-only observation.
- Alerts from this source carry `source = "live"` from the `AlertSource` vocabulary, and never a ground-truth label: nobody labelled this traffic.
- Driven by `GET /api/v1/ingest/status`, `POST /api/v1/ingest/start` and `POST /api/v1/ingest/stop` — see [API Reference](API-Reference.md#post-apiv1ingeststart).
- Status: **implemented (Phase 9)** and run on this project's own host (`reports/phase9_live.md`, the README's "Real traffic" section). The self-run attack exercise, which belongs in a lab the operator owns, has not been run.

---

## flowmeter.py

`backend/app/flowmeter.py` — turns packets into the 70 CICIDS2017 flow columns the way CICFlowMeter-V3 made them.

The models were trained on rows CICFlowMeter-V3 produced from the 2017 captures, so a live flow is only scoreable if it is *the same quantity*: the same columns, computed the same way, quirks included. A meter that computed "correct" features would hand both models a distribution they never saw, and the train/serve-skew defence would be lost one layer earlier than `training/features.py` can see. Each rule below was checked against the training rows themselves, and `backend/tests/test_flowmeter.py` pins them:

1. Bidirectional flows on the 5-tuple; the first packet's direction is forward, and `destination_port` is its destination.
2. A flow ends at its first FIN, in either direction, or when a packet arrives more than 120 s after the flow began. The rest of a TCP teardown becomes its own short flow — the dataset's documented "TCP appendix".
3. Lengths are payload bytes, and the all-packet length statistics count the first packet twice: `average_packet_size == packet_length_mean × (n + 1) / n` on every training row.
4. Standard deviations are sample (n − 1), and 0 below two values.
5. The eight flag columns hold the first packet's flags, permuted: CICFlowMeter wrote them by iterating a Java `HashMap` under a fixed CSV header (`FLAG_COLUMNS`). `fwd_psh_flags` and `fwd_urg_flags` likewise see only the first packet.
6. `act_data_pkt_fwd` skips the first packet; `init_win_bytes_backward` is the last backward packet's window; `init_win_bytes_*` is −1 where there is no TCP window.
7. A UDP packet's header length is the last TCP header length decoded — no UDP flow in the dataset carries UDP's real 8-byte header. Reproduced, not fixed.
8. Subflow columns equal the flow totals.
9. Active and idle periods come only from gaps over 5 s within the flow.
10. Zero-duration flows are counted and not scored: their rates are infinite, and no training row had one. That includes single-packet probes — a blind spot stated rather than hidden.

One deliberate departure: a flow silent for `IDS_LIVE_IDLE_FLUSH_S` seconds is emitted. CICFlowMeter worked on finished pcaps and could wait for the end of the file; a live meter that did would never score a long-lived quiet connection.

| Symbol | Kind | Description |
| --- | --- | --- |
| `FlowMeter` | class | `feed(frame, ts_us, linktype)` returns the flows that packet finished; `flush_idle(now_us)` and `flush_all()` finish the rest; `counts` is a `MeterCounts` of packets, undecoded frames, flows and unscoreable flows |
| `MeteredFlow` | dataclass | A finished flow: `features` (the 70 columns), the observed `src_ip`, `src_port`, `dst_ip`, `dst_port` and `protocol`, and its start and end; `as_record()` gives the endpoints the pipeline stores |
| `Decoder` | class | Ethernet, Linux cooked (SLL) and raw-IP frames; IPv4 and IPv6; TCP and UDP. Anything else is counted as undecoded |
| `read_pcap` | function | Classic pcap records, microsecond or nanosecond, as `tcpdump -w` writes; pcapng is refused with a message rather than misread |
| `meter_pcap` | function | Every flow in a pcap, in the order they finished |
| `FEATURE_COLUMNS`, `FLAG_COLUMNS` | constants | The 70 output columns, and which first-packet flag each flag column really holds |

- Only TCP and UDP over IPv4 and IPv6 are metered, as CICFlowMeter did.
- Standard library only; no libpcap.
- Status: **implemented (Phase 9).**
