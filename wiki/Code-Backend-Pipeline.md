# Code Reference — Alert Pipeline Modules

This page documents the seven modules under `backend/app/` that turn a model score into an alert an analyst can act on: explanation, narration, MITRE mapping, remediation lookup, deduplication, drift measurement, replay and live capture. Read it if you are implementing Phase 5, Phase 7 or Phase 9, or if you want to know exactly how much of the pipeline exists today. As of Phase 0 all of these files are present, importable and documented, but only `dedupe.dedupe_key` has a working body — every other callable raises `NotImplementedError` naming its phase. For the surrounding request layer see [API Route Modules](Code-Backend-Routes.md); for the scoring path itself see [Backend Core](Code-Backend-Core.md).

| File | Lines | Role |
| --- | --- | --- |
| `backend/app/explain.py` | 38 | Per-alert feature attribution and English narration |
| `backend/app/mitre.py` | 20 | Attack family to MITRE ATT&CK technique lookup |
| `backend/app/remediation.py` | 20 | Attack family to recommended-response playbook |
| `backend/app/dedupe.py` | 28 | Collapses alert bursts onto one incident row |
| `backend/app/drift.py` | 21 | Population Stability Index and drift snapshots |
| `backend/app/replay.py` | 27 | Accelerated replay of held-out test flows |
| `backend/app/live_capture.py` | 30 | Ingestion of real network traffic |

---

## The alert pipeline in order

BUILD_PROMPT.md Part 8 fixes the order every alert passes through. Each step is owned by exactly one module, and the order is not negotiable: dedupe has to see a classified alert, so it runs after the family is known, but it has to run before persistence, so a burst never becomes 5,000 rows.

```text
  scored flow  (stage 1 probability, stage 2 reconstruction error)
        |
        v
  [1] EXPLAIN           explain.py     TreeSHAP top-5        (Stage 1)
        |                              recon-error top-5     (Stage 2)
        v
  [2] NARRATE           explain.py     per-feature phrase map -> one English sentence
        |
        v
  [3] MAP + RECOMMEND   mitre.py       family -> technique ID + plain-English meaning
        |               remediation.py family -> recommended response playbook
        v
  [4] DEDUPE            dedupe.py      key (src_host, alert_class, floor(ts, window))
        |                              hit  -> occurrence_count += 1, last_seen = ts
        |                              miss -> continue to persist
        v
  [5] ENRICH            Phase 5        asset criticality lookup, prior alert count for host
        |
        v
  [6] PERSIST           db.py          INSERT into alerts
        |
        v
  [7] PUSH              routes/stream.py  server-sent event to every connected dashboard
```

Ownership and status today:

| Step | Owner | Status in Phase 0 |
| --- | --- | --- |
| 1 Explain | `explain.explain_supervised`, `explain.explain_anomaly` | stub — raises `NotImplementedError`, lands in Phase 5 |
| 2 Narrate | `explain.narrate` | stub — raises `NotImplementedError`, lands in Phase 5 |
| 3 Map | `mitre.technique_for` | stub — raises `NotImplementedError`, lands in Phase 5 |
| 3 Recommend | `remediation.playbook_for` | stub — raises `NotImplementedError`, lands in Phase 5 |
| 4 Dedupe | `dedupe.dedupe_key` | implemented — pure function, needs no model and no session |
| 5 Enrich | not yet a module | lands in Phase 5 |
| 6 Persist | `app/models.py`, `app/db.py` | schema and session factory implemented; no writer yet |
| 7 Push | `app/routes/stream.py` | route registered, answers 501, lands in Phase 5 |

The one implemented step is implemented for a reason. `dedupe_key` is pure, takes no model and no database session, is cheap to unit-test, and the `dedupe_key` column it feeds already exists on the `alerts` table with the composite index `ix_alerts_dedupe_key_last_seen` behind it. Nothing else in the pipeline can be built honestly before there is a model to explain. See [Database Schema](Database-Schema.md) for the columns and [Roadmap](Roadmap.md) for what each phase delivers.

---

## explain.py

**Path:** `backend/app/explain.py` — produces the top-5 feature attribution behind an alert and templates it into a sentence an analyst can read.

### What it does

Every alert leaves this module carrying a reason. The module docstring states the rule directly: an alert with a score and no reason is an alert an analyst ignores. That is why explanation is step 1 of the pipeline rather than an optional decoration on the Alert Detail screen.

Two explainers exist because the two stages are different kinds of model and one explainer would be wrong for one of them. Stage 1 is a tree ensemble, so TreeSHAP gives exact per-feature contributions cheaply. Stage 2 is an autoencoder, and it hands its explanation over for free: the features it failed hardest to reconstruct are precisely why the row looks anomalous. The docstring spells the computation out.

```python
per_feature_error = (x - x_hat) ** 2
top_contributors  = argsort(per_feature_error)[-5:]
```

KernelSHAP on a neural network was considered and rejected in the same docstring as slow, approximate, and buying nothing over the reconstruction error the model has already computed. `narrate` is the second half of the job: it converts feature names and contribution values into English through a per-feature phrase template map. The docstring fixes the target register with a worked example — "2,400 distinct destination ports contacted in 8 seconds from a single source."

### Key symbols

| Symbol | Kind | Signature | Description |
| --- | --- | --- | --- |
| `explain_supervised` | function | `explain_supervised(*_: Any, **__: Any) -> dict[str, Any]` | TreeSHAP attribution for a Stage 1 prediction, top-5 contributing features. Stub. |
| `explain_anomaly` | function | `explain_anomaly(*_: Any, **__: Any) -> dict[str, Any]` | Per-feature reconstruction error for a Stage 2 detection, top-5 contributors. Stub. |
| `narrate` | function | `narrate(*_: Any, **__: Any) -> str` | Templates an explanation into one English sentence via a per-feature phrase map. Stub. |

### Notes

- The `*_: Any, **__: Any` signatures are deliberate placeholders. The real parameter lists are settled in Phase 5, once the feature contract from Phase 1 and the artifact bundle from Phase 2 exist; naming arguments now would guess at both.
- Both explainers return `dict[str, Any]` rather than a list, so one explanation can carry feature names, contribution values and the stage that produced them in a single object.
- Top-5 is a fixed budget, not a threshold. Five ranked contributors is what the Alert Detail screen has room for and what an analyst reads.
- Status: **stub — every function raises `NotImplementedError`, lands in Phase 5 (backend API).** The messages are `"TreeSHAP explanations arrive in Phase 5 (backend API)."`, `"Reconstruction-error explanations arrive in Phase 5."` and `"Narration arrives in Phase 5 (backend API)."`

---

## mitre.py

**Path:** `backend/app/mitre.py` — maps a detected attack family onto a MITRE ATT&CK technique ID plus a one-line plain-English description of what that technique means.

### What it does

The Alert Detail screen has to answer "what is this, likely?" without the analyst opening a second tab. That is the whole job of this module: family in, technique ID and a sentence out, from a static reviewed lookup.

`UNCLASSIFIED_ANOMALY` maps to no technique, on purpose. The docstring is explicit that saying so plainly is the honest answer and that it is the entire point of Stage 2 — a detector whose value is catching families nobody named cannot then be asked to name them. Any technique invented for an unclassified anomaly would be a fabrication presented with exactly the same confidence as a real mapping.

The same lookup backs the MITRE coverage heatmap on the analytics screen, served by `GET /api/v1/analytics/mitre-coverage`.

### Key symbols

| Symbol | Kind | Signature | Description |
| --- | --- | --- | --- |
| `technique_for` | function | `technique_for(*_: Any, **__: Any) -> dict[str, Any] \| None` | Returns the technique ID and plain-English meaning for a family, or `None` for an unclassified anomaly. Stub. |

### Notes

- The `| None` in the return type is the contract for `UNCLASSIFIED_ANOMALY`. Callers must handle `None` and render "no matching technique" rather than an empty string.
- The data this module will serve is the reviewed table reproduced in the [remediation.py](#remediationpy) section below. One table backs both the technique mapping and the response playbook, so the two can never disagree about which families exist.
- Status: **stub — raises `NotImplementedError("The MITRE lookup is populated in Phase 5 (backend API).")`, lands in Phase 5.**

---

## remediation.py

**Path:** `backend/app/remediation.py` — maps a detected attack family onto a recommended response playbook.

### What it does

This module is a static, reviewed lookup table, and the docstring argues the point rather than merely stating it: a fixed playbook is something a SOC can trust, while advice improvised per alert has to be re-verified every time, which defeats the purpose of having it. There is no generative step anywhere on this path.

The unclassified case gets the honest entry — no playbook exists yet, route for manual investigation. The docstring's rule is that a wrong playbook does more damage than an honest shrug, because an analyst who follows fabricated remediation advice takes real action on a real network.

### The reviewed static lookup

Both `mitre.py` and `remediation.py` are populated from this single table in BUILD_PROMPT.md Part 8. It is reproduced here in full as the data those modules will serve. It is the source the Phase 5 implementation transcribes, not a sample of output.

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

### Key symbols

| Symbol | Kind | Signature | Description |
| --- | --- | --- | --- |
| `playbook_for` | function | `playbook_for(*_: Any, **__: Any) -> dict[str, Any]` | Returns the recommended response playbook for a family; for an unclassified anomaly returns the explicit "no playbook yet, investigate manually" entry. Stub. |

### Notes

- Unlike `technique_for`, this return type is not optional. Every family including the unclassified one has an entry, because "no playbook exists yet, route for manual investigation" is itself an answer the analyst needs on screen.
- Nothing in this module or downstream of it executes a remediation. The system alerts, ranks and explains; containment is manual and confirmed by a human. `IDS_ALLOW_AUTO_BLOCK` is rejected by a validator in `app/config.py` so that the constraint is greppable rather than merely absent — see [Configuration](Configuration.md).
- Status: **stub — raises `NotImplementedError("The remediation table is populated in Phase 5 (backend API).")`, lands in Phase 5.**

---

## dedupe.py

**Path:** `backend/app/dedupe.py` — collapses a burst of near-identical alerts onto a single incident row.

### What it does

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

### Key symbols

| Symbol | Kind | Signature | Description |
| --- | --- | --- | --- |
| `dedupe_key` | function | `dedupe_key(src_host: str, alert_class: str, timestamp: dt.datetime) -> str` | Builds the deduplication key from source host, alert class and the floored time bucket. Implemented. |

### Notes

- This is the only pipeline function with a real body in Phase 0. Its docstring says why: it is pure, cheap to test, and the schema column that stores it already exists.
- Because the bucket is computed by flooring rather than by a sliding window, two alerts 299 seconds apart land in different buckets when they straddle a boundary. That is accepted. Fixed buckets are deterministic, index-friendly, and need no read-before-write to decide which window an event belongs to.
- `timestamp.timestamp()` interprets a naive datetime as local time. Alert timestamps are stored as `DateTime(timezone=True)`, so the Phase 5 caller must pass timezone-aware values for keys to stay stable across hosts.
- Changing `IDS_DEDUPE_WINDOW_SECONDS` changes every future key. Existing rows keep the keys they were written with, so a window change partitions the history rather than corrupting it.
- Status: **implemented.**

---

## drift.py

**Path:** `backend/app/drift.py` — measures how far live traffic has moved from the distribution the models were trained on.

### What it does

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

The module also overlays the training benign score distribution on the last 24 hours of live scores. When those two curves separate, the baseline has moved regardless of what any individual feature's PSI says.

### Key symbols

| Symbol | Kind | Signature | Description |
| --- | --- | --- | --- |
| `population_stability_index` | function | `population_stability_index(*_: Any, **__: Any) -> float` | Computes PSI for one feature against its training reference distribution. Stub. |

### Notes

- PSI is defined per feature. A model-level drift verdict is an aggregation over per-feature values plus the score-distribution overlay, never a single number.
- The 0.1 and 0.25 bands are the conventional thresholds, left unchanged on purpose so a number on the drift screen means the same thing it means everywhere else.
- Results are served by `GET /api/v1/metrics/drift`, itself a Phase 7 stub — see [API Route Modules](Code-Backend-Routes.md).
- Drift detection needs a training reference distribution, so it cannot precede Phase 2, and it needs accumulated live scores, so it is scheduled for Phase 7 rather than alongside the API.
- Status: **stub — raises `NotImplementedError("PSI is implemented in Phase 7 (drift and active learning).")`, lands in Phase 7.**

---

## replay.py

**Path:** `backend/app/replay.py` — streams held-out test flows through the live scoring path at accelerated time so the dashboard has real traffic to show.

### What it does

There is no live enterprise traffic available to this project, so replay is the primary traffic source. It is an asyncio background task that reads held-out test rows, scores them in batches and pushes the resulting alerts over SSE at 1x, 10x or 100x wall-clock speed.

The docstring calls this honest, and the word carries weight: these are real flows with real ground-truth labels, so the dashboard can display predictions against truth. Ground truth is surfaced in the UI badged as demo-only, and never for live capture, where no labels exist.

Batch scoring is mandatory in this loop. Per-row `predict()` is roughly 50x slower and makes the demo stutter — the same constraint that shapes `POST /api/v1/score`. The module optionally accepts a pcap upload, runs CICFlowMeter over it, and scores the resulting flows through the same features module, so an operator-supplied capture takes exactly the path a dataset row takes.

### Key symbols

| Symbol | Kind | Signature | Description |
| --- | --- | --- | --- |
| `start_replay` | async function | `async def start_replay(*_: Any, **__: Any) -> None` | Starts the background replay task at the requested speed and dataset. Stub. |
| `stop_replay` | async function | `async def stop_replay(*_: Any, **__: Any) -> None` | Stops the active replay task. Stub. |

### Notes

- Both functions are coroutines because replay is an asyncio task inside the running FastAPI process, not a separate worker. Starting it must not block the request that started it.
- Speed is a multiplier on the inter-flow delay, not a change to batch size. The scoring work per flow is identical at 1x and at 100x.
- Driven by `POST /api/v1/replay/start` and `POST /api/v1/replay/stop`.
- Status: **stub — both functions raise `NotImplementedError("The replay engine is implemented in Phase 5 (backend API).")`, lands in Phase 5.**

---

## live_capture.py

**Path:** `backend/app/live_capture.py` — ingests real network traffic as a second source alongside replay.

### What it does

Live capture is a second traffic source, never a replacement for replay. It feeds the exact same features module, the exact same inference path and the exact same alert pipeline. The docstring gives the reason: live traffic needing its own scoring code would break the train/serve-skew defence the feature contract exists to provide. Two code paths computing "the same" features is how a system starts scoring production traffic differently from how it was trained.

Authorisation is a hard precondition, stated in the module itself. Capture runs only against a network, device or lab the operator owns or is explicitly authorised to monitor. Packet capture on a network you do not control is illegal in most places regardless of intent.

The docstring also sets the expectation for first contact with real traffic: a false-positive rate well above anything the CICIDS2017 validation numbers promised, because the 2017 lab baseline is not today's encrypted household or enterprise traffic. That is domain shift, not a bug. The prescribed handling is a shadow-mode burn-in — score everything, alert no one — then recompute `tau_anom` from the locally observed benign percentile and document both thresholds and the gap between them. Two ingest paths are supported and produce the same output shape: Zeek or Suricata flow logs, or `tcpdump` plus CICFlowMeter over the resulting pcap.

### Key symbols

| Symbol | Kind | Signature | Description |
| --- | --- | --- | --- |
| `start_ingest` | async function | `async def start_ingest(*_: Any, **__: Any) -> None` | Begins scoring a live capture from a Zeek/Suricata feed or a tcpdump plus CICFlowMeter pipeline. Stub. |

### Notes

- There is no `stop_ingest` in Phase 0. The symmetry with `replay.stop_replay` is expected to appear when the module is implemented in Phase 9.
- Nothing here blocks, drops or shapes traffic. Capture is read-only observation.
- Alerts from this source carry `source = "live"` from the `AlertSource` vocabulary in `app/schemas.py`, which is how the UI knows to suppress the ground-truth badge.
- Driven by `POST /api/v1/ingest/start`.
- Status: **stub — raises `NotImplementedError("Live capture is implemented in Phase 9 (real traffic).")`, lands in Phase 9.**

---

## Related pages

- [Architecture](Architecture.md) — where the pipeline sits in the whole system
- [API Route Modules](Code-Backend-Routes.md) — the endpoints that drive these modules
- [Backend Core](Code-Backend-Core.md) — config, database, inference, models and schemas
- [ML Models](ML-Models.md) — what Stage 1 and Stage 2 actually are
- [Database Schema](Database-Schema.md) — the `alerts` table dedupe writes to
- [Roadmap](Roadmap.md) — what each phase delivers
- [Anti-Patterns](Anti-Patterns.md) — the failures these design choices are avoiding
