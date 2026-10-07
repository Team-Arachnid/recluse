# Code Reference — API Route Modules

This page documents every module under `backend/app/routes/`: the router aggregator, the 501 helper, and the seven route modules that declare the full v1 API surface.

As of **Phase 9**, all twenty-six operations have real implementations and none answers 501: Phase 7 filled in drift and the registry, and Phase 9 live capture, the last of the original stubs. `GET /api/v1/health` lives in `app/main.py` rather than here. Every endpoint's contract is in the [API Reference](API-Reference.md); this page is about the modules.

| File | Lines | Role |
| --- | --- | --- |
| `backend/app/routes/__init__.py` | 52 | The `not_implemented` helper, kept for any future stub, and the aggregated `api_router` |
| `backend/app/routes/alerts.py` | 528 | Alert queue, stats, bulk status, detail, verdicts, host correlation |
| `backend/app/routes/analytics.py` | 370 | Aggregate analytics, the feedback loop, MITRE coverage |
| `backend/app/routes/drift.py` | 353 | Drift snapshots, the model registry, retrain requests |
| `backend/app/routes/replay.py` | 260 | Replay controls and the dataset list; live-capture ingest |
| `backend/app/routes/metrics.py` | 242 | Model metrics, threshold what-ifs, the anomaly histogram |
| `backend/app/routes/stream.py` | 139 | Server-sent-events live alert feed |
| `backend/app/routes/score.py` | 59 | Batch flow scoring |

The routes are thin. Most of the work lives behind them, in modules this package only calls:

| Module | Lines | What the routes get from it |
| --- | --- | --- |
| `app/pipeline.py` | 404 | `ingest_batch` — the six-stage path from a scored batch to persisted alerts |
| `app/replay.py` | 408 | The asyncio replay engine, its `ReplayState`, and the dataset list |
| `app/live_capture.py` | 473 | Live capture: `start_ingest`, `stop_ingest`, `IngestState`, the calibration loader |
| `app/events.py` | 186 | `EventBroker` — in-process pub/sub between a traffic source and `/stream` |
| `app/metrics_store.py` | 135 | The three evaluation artifacts, loaded once at startup |
| `app/registry.py` | 233 | `read_registry` — model versions with the alerts and verdicts each one is behind |
| `app/feedback.py` | 317 | `labelled_flows` — the analyst labels a retrain would consume |
| `app/drift.py` | 300 | The PSI bands the drift response reports |
| `app/explain.py` | 470 | TreeSHAP, reconstruction-error contributors, and `narrate` |
| `app/risk.py` | 387 | `risk_score` and `severity` |
| `app/topology.py` | 324 | Derived addresses, asset criticality, provenance |
| `app/dedupe.py` | 154 | `dedupe_key` and the `upsert_alert` that collapses bursts |
| `app/schemas.py` | 1025 | Every wire contract, and the source of the frontend's types |

---

## How paths are assembled

No route module hardcodes `/api/v1`. Each module declares a bare router, `app/routes/__init__.py` includes all seven into one `api_router`, and `create_app()` in `app/main.py` mounts that aggregate under the configured prefix:

```
create_app()
  ├─ health_router   prefix=/api/v1   tags=["system"]     →  GET /health
  └─ api_router      prefix=/api/v1   (bare APIRouter, no prefix of its own)
       ├─ alerts.router      prefix=/alerts     tags=["alerts"]     (6 routes)
       ├─ score.router       prefix=/score      tags=["scoring"]    (1 route)
       ├─ metrics.router     no prefix          tags=["metrics"]    (/metrics/model, /threshold, /anomaly-histogram)
       ├─ drift.router       no prefix          tags=["drift"]      (/metrics/drift, /models, /retrain x2)
       ├─ analytics.router   prefix=/analytics  tags=["analytics"]  (3 routes)
       ├─ replay.router      no prefix          tags=["traffic"]    (/replay/* x4, /ingest/* x3)
       └─ stream.router      prefix=/stream     tags=["stream"]     (1 route)
```

Changing `IDS_API_V1_PREFIX` moves the whole surface. `/replay/*` and `/ingest/*` share the `traffic` tag because they are one concept — where flows come from — even though replay landed in Phase 5 and ingest in Phase 9. `/metrics/drift` is tagged `drift` with the registry and retraining rather than `metrics`, because it serves what the drift job wrote, not the evaluation artifacts.

---

## routes/__init__.py

Holds `not_implemented(endpoint, phase) -> JSONResponse`, which builds a `NotImplementedResponse` and returns it with status 501. No route uses it today — Phase 9's `/ingest/start` was the last stub — and it stays so a route registered ahead of its phase can answer "not built yet" rather than invent data. `backend/tests/test_api_surface.py` keeps an empty `DEFERRED_ROUTES` list for the same reason.

The helper is defined *before* the route modules are imported, and the imports sit at the bottom of the file behind a `# noqa: E402`. That ordering is deliberate: the route modules import the helper from this package, so defining it after the imports would be a circular import.

---

## routes/score.py

One endpoint, and the thinnest module here, because it is nothing but a contract over machinery that already existed.

`score_flows(flows: list[FlowRecord], request: Request) -> ScoreResponse` reaches `request.app.state.bundle` — loaded once in the lifespan — calls `score_batch`, maps the records onto `ScoredFlow`, and counts the alerts. No handler loads, reloads, fits or trains anything.

Error mapping, with the original message passed through because both were written to be read by whoever sent the batch:

| Raised by `score_batch` | Becomes |
| --- | --- |
| `RuntimeError` (no complete stage loaded) | 503 |
| `ValueError` (from `_verify_payload`) | 422 |

**Stateless by design.** It persists no alert. The documented response carries no `id`, and an `alerts` row needs a source address — `alerts.src_ip` is `NOT NULL` — which an arbitrary API caller does not supply. The dedupe/enrich/persist/push pipeline runs where a flow's origin is known.

---

## routes/alerts.py

Six endpoints and the largest route module, because the queue is the landing page of the whole application.

| Route | Returns |
| --- | --- |
| `GET /alerts` | `AlertPage` — items, `next_cursor`, `limit` |
| `GET /alerts/stats` | `QueueStats` — the counts above the queue |
| `PATCH /alerts/status` | `AlertStatusResult` — the bulk triage action |
| `GET /alerts/{alert_id}` | `AlertDetail` |
| `POST /alerts/{alert_id}/verdict` | `VerdictResponse`, status 201 |
| `GET /alerts/{alert_id}/related` | `list[AlertSummary]` |

All six take `session: Session = Depends(get_session)`. `B008` is in ruff's ignore list for exactly this — FastAPI's `Depends()` in a default argument is the documented idiom.

### The cursor

`_encode_cursor` / `_decode_cursor` pack `(risk_score, id)` into base64 of a tiny JSON object. Opaque on purpose: the client's only contract is "hand back what you were given", which leaves the server free to change the sort key without breaking a client that learned to parse it.

Keyset rather than offset, because a replay writes to this table while an analyst pages through it. An offset page silently skips and repeats rows — one new higher-risk alert between two requests shifts everything down by one, so page 2 re-shows the last row of page 1 and drops one nobody saw. `backend/tests/test_alerts.py::test_paging_is_stable_when_a_higher_risk_alert_lands_mid_page` asserts that scenario directly.

The `id` tie breaker is load-bearing. `risk_score` is rounded to four places so ties are common; without a second key the database may order tied rows differently per request, and a cursor on `risk_score` alone would page past some of them.

A malformed cursor raises 422 with a message, not a silent reset — treating it as "start from the top" would make a paging bug look like a queue that keeps jumping back.

### `_latest_verdicts`

One query for a whole page rather than N. `analyst_verdicts` is one-to-many and the queue shows a verdict column, so without it a fifty-row page would issue fifty extra queries. Ordered by `created_at` then `id`, so two verdicts inside the same timestamp resolution still have a defined winner; later rows overwrite earlier ones in the dict, so the last write per alert wins.

### `submit_verdict`

Captures `model_version` from the alert being judged, not from whatever is currently loaded. A label attributed to the wrong model version is worse than no label: it would train the next model on a correction to a decision it never made.

It does **not** touch the alert's `status`. Recording a judgement and moving an alert through triage are different actions, and a verdict that silently closed an alert would take it out of a colleague's queue mid-review.

### `queue_stats` and `update_alert_status`

`queue_stats` counts what the deployment has actually seen, from the database: open alerts, the last hour, the observed rate over the span of stored alerts, hosts and sources among open alerts, open unclassified anomalies, and open alerts nobody has judged. The projected volume beside them on the strip comes from `/metrics/threshold` instead, because a projection is a property of the model rather than of the queue.

`update_alert_status` applies one status to up to 500 ids in one transaction — fifty single requests would be fifty chances to half-apply one decision — and reports ids that no longer exist in `missing` rather than failing the batch. It writes no verdict: dismissing a noisy row is not the claim "the model was wrong", and conflating the two would poison the labels retraining reads.

### `related_alerts`

Measures the window from the anchor's own `detected_at`, not from now, and reaches both directions. An alert opened a week after the fact would otherwise show no context at all, and the reconnaissance that preceded an exploit attempt is usually the context worth most.

---

## routes/stream.py

One endpoint, and about ten lines of actual protocol as the design promised: a `StreamingResponse` over an async generator with content type `text/event-stream`. No SSE library is installed and none is wanted.

`_frame(event_name, payload)` builds one frame. The blank line is the record separator the protocol requires, and the JSON must not contain a raw newline or the parser treats the remainder as a new field — which is why the payload is never pretty-printed. Two tests cover exactly those two failure modes, because the protocol is unforgiving about both and silent on both.

`_events(broker)` subscribes, yields an `alert` frame per event, and emits a `heartbeat` when a read times out after `HEARTBEAT_SECONDS` (15). The `finally` that unsubscribes is the load-bearing part: a closed browser tab cancels the generator, and without it the broker would keep filling a queue for a reader that no longer exists.

`stream_alerts` answers **503** when `broker.active_source is None` rather than holding open a connection that can never produce an event. An empty stream and a dead stream are indistinguishable from the client's side, and the first is a bug while the second is Tuesday.

The response sets `Cache-Control: no-cache` and `X-Accel-Buffering: no`, because without them a reverse proxy may buffer the stream into oblivion — the feed looks dead while events pile up upstream, which is the same symptom as forgetting `curl -N`.

---

## routes/replay.py

Seven endpoints: four drive the replay engine (`/replay/status`, `/replay/datasets`, `/replay/start`, `/replay/stop`) and three the live capture (`/ingest/status`, `/ingest/start`, `/ingest/stop`). They share a module and the `traffic` tag because they are one concept, and they never run at once: each start answers 409 while the other source is running, because one writer is what keeps the dedupe upsert's single-writer invariant.

`replay_start` returns **202, not 200**: the work it starts outlives the request, so the response says the replay has been accepted and is running, not that it finished. It checks `bundle.stage1_ready or bundle.stage2_ready` first and answers 503 otherwise — a replay that emitted no alerts would read as "no attacks in this split", which is a different claim.

| Raised by `app.replay` | Becomes |
| --- | --- |
| `ReplayAlreadyRunning` | 409 |
| `UnknownDataset`, `ValueError` | 422 |

`replay_stop` returns the run's final counters, which is the useful thing to return from a stop — "15,500 rows scored, 93 alerts" rather than an empty acknowledgement — or 409 if nothing is running. `replay_status` and `replay_dataset_list` always answer 200: `running: false` is an answer, and the dataset list says which names will run here, so a container without the dataset offers only the committed demo sample.

`ingest_start` is a 202 for the same reason as a replay, and answers 503 when Stage 2 is not loaded — there would be nothing to score or calibrate with.

| Raised by `app.live_capture` | Becomes |
| --- | --- |
| `CaptureNotAuthorised` — an interface outside `IDS_LIVE_INTERFACES` | 403 |
| `UnknownPcap` — no such file inside `IDS_LIVE_PCAP_DIR` | 404 |
| `IngestAlreadyRunning`, `NotCalibrated` — alert mode before a burn-in | 409 |
| `ValueError` | 422 |

`ingest_stop` waits for the flows still open to be finished and scored, then reports the final counters. All three ingest routes return `IngestStatus`, which adds what the capture may name (`allowed_interfaces`, `pcaps`) and both Stage 2 thresholds, the dataset's and the local calibration — the latter only when it was cut for the serving Stage 2 model.

The routes read `app.state.replay` and `app.state.ingest`, created once in the lifespan, so they report a status object rather than probing for an attribute.

---

## routes/metrics.py

Three endpoints, all served from the evaluation artifacts loaded at startup.

`model_metrics` serves `app.state.metrics` — the three evaluation artifacts, loaded once at startup — and recomputes nothing. A figure recomputed from whatever alerts happen to be stored would be a different claim wearing the same label, and it would move every time a replay ran. It answers 503 when no artifact is loaded, because an empty table would read as a model that scored zero rather than one that has not been measured.

`threshold_what_if` takes `t`, constrained to `[0.0, 1.0]` at the validation layer, and projects volume, false-positive rate and recall from the persisted error histograms. `t` is **Stage 2's anomaly threshold**, not a Stage 1 probability — see [API Reference](API-Reference.md#t-is-stage-2s-anomaly-threshold) for why a Stage 1 answer is not computable from what is persisted.

Both figures come from `ErrorHistogram.above(t)` over histograms that share their bin edges, so the FPR and the recall describe the same point on the same axis. `alerts_per_analyst_hour` divides by `IDS_ANALYST_SHIFT_HOURS`, not by 24, matching `training/metrics.py` — a slider and a model card quoting different volumes for one operating point is the kind of disagreement nobody notices until a SOC lead sets a threshold from the wrong one.

---

`anomaly_histogram` serves the persisted Stage 2 error bins — validation benign, test benign and test attack on shared log-spaced edges, with `tau_anom` and `budget_tau` — so the Live screen can draw its threshold line *across* the distribution. Sampling the projection endpoint sixty times would be sixty requests for one chart, and would still give a cumulative curve. It answers 503 when no distribution is loaded, because an empty histogram would read as traffic with no reconstruction error at all.

---

## routes/drift.py

Four endpoints (Phase 7), and one thing none of them does: fit a model.

`drift_metrics` serves the snapshots `python -m training.drift_job` stored — the latest run with every feature's PSI worst first, a per-feature series over the last `snapshots` runs (default 30), and the validation day's benign error histogram to overlay. Drift is a property of a window of time, not of a request; computed per request, two readers minutes apart would get two answers. An empty response is a real state: `latest` is null until the first run, rather than a flat line at zero that would tell its reader everything is fine.

`list_models` returns the registry with `serving`, and for each version the alerts and verdicts it is behind — the audit trail made queryable.

`request_retrain` writes a `retrain_runs` row and returns **202**; `python -m training.retrain` claims it. It answers 409 while a run is requested or running, because two would consume the same labels and race to publish a champion, and 422 when no unconsumed labels exist, because a challenger fitted on the champion's own data differs only by random seed. `retrain_status` lists every run, newest first, including the ones that were not promoted — a gate that has never declined anything is a gate nobody has evidence for.

---

## routes/analytics.py

Three endpoints. Unlike `/metrics/*`, everything here is computed from the `alerts` and `analyst_verdicts` tables: it describes what this deployment has actually seen.

`_floor` buckets timestamps in **Python rather than SQL**, because the date functions differ between SQLite and Postgres and this project's persistence story is that pointing `IDS_DATABASE_URL` at Postgres is a configuration change and nothing more. A `strftime` here would quietly break that.

`analytics_summary` splits its time series into `known` and `unclassified` rather than reporting a total — the unclassified line is the novel-detection headline, and folding it into a total would hide the claim this project is making. Empty buckets are emitted as zeros, because a chart that skips empty hours draws a continuous line through the gap and makes an outage look like steady traffic.

`_throughput` excludes `UNSURE` from `true_positive_rate`'s denominator. It is not a judgement that the alert was wrong, and folding it in would drag the rate down every time an analyst was honest about not knowing. With nothing decided the rate is `null`, not `0.0` — zero would claim every alert was a false positive.

`feedback_loop` serves the Feedback screen: labels since the last retrain (`analyst_verdicts.consumed_at IS NULL`), the TP/FP split, the disagreement rate FP / (TP + FP) — `UNSURE` outside the denominator again — and the same counts per model version, so a label against a since-replaced champion stays attributed to it.

`mitre_coverage` builds its rows from `app.mitre.coverage_vocabulary()`, **not** from the alerts that have fired, so a technique with no hits is a visible zero. "We have never seen this" and "we cannot see this" look identical when the axis comes from the data. `UNCLASSIFIED_ANOMALY` has no row — it maps to no technique by design — but its count sits beside the table, because omitting it would understate exactly the detections this project is proudest of.

---

## The full v1 surface at a glance

The table of all twenty-six operations, with the phase each landed in, is the [API Reference's endpoint index](API-Reference.md#endpoint-index) — kept in one place so two copies cannot disagree.

`backend/tests/test_api_surface.py` machine-checks it from both sides: every operation in `IMPLEMENTED_ROUTES` must appear in the served schema and must not answer 501, `DEFERRED_ROUTES` (empty today) would hold any route still answering 501 with its exact phase string, and a third test asserts the two lists together equal the schema's operations exactly — so a route cannot be added, or leave one list, without the suite noticing. `backend/tests/test_api_contract.py` then pins the whole schema to the committed snapshot.

**There is no block, drop, quarantine or deny endpoint, and there will not be one.** `test_no_route_mentions_blocking` walks every path in the served schema and asserts it. See [the reasoning](API-Reference.md#explicitly-absent).
