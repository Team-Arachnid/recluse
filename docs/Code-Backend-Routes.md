# Code Reference — API Route Modules

This page documents every module under `backend/app/routes/`: the router aggregator, the shared 501 helper, and the six route modules that declare the full v1 API surface.

As of **Phase 5**, thirteen of the sixteen endpoints have real implementations. The three that do not — `GET /metrics/drift` and `GET /models` (Phase 7) and `POST /ingest/start` (Phase 9) — still answer HTTP 501 with a machine-readable body naming the phase that fills them in. `GET /api/v1/health` lives in `app/main.py` rather than here.

| File | Lines | Role |
| --- | --- | --- |
| `backend/app/routes/__init__.py` | 48 | The `not_implemented` helper and the aggregated `api_router` |
| `backend/app/routes/alerts.py` | 346 | Alert queue, detail, verdicts, host correlation |
| `backend/app/routes/analytics.py` | 257 | Aggregate analytics and MITRE coverage |
| `backend/app/routes/metrics.py` | 177 | Model metrics, threshold what-ifs, drift, model registry |
| `backend/app/routes/stream.py` | 120 | Server-sent-events live alert feed |
| `backend/app/routes/replay.py` | 110 | Replay controls and live-capture ingest |
| `backend/app/routes/score.py` | 59 | Batch flow scoring |

The routes are thin. Most of the work lives behind them, in modules this package only calls:

| Module | Lines | What the routes get from it |
| --- | --- | --- |
| `app/pipeline.py` | 308 | `ingest_batch` — the six-stage path from a scored batch to persisted alerts |
| `app/replay.py` | 339 | The asyncio replay engine and its `ReplayState` |
| `app/events.py` | 186 | `EventBroker` — in-process pub/sub between a traffic source and `/stream` |
| `app/metrics_store.py` | 103 | The three evaluation artifacts, loaded once at startup |
| `app/explain.py` | 441 | TreeSHAP, reconstruction-error contributors, and `narrate` |
| `app/risk.py` | 387 | `risk_score` and `severity` |
| `app/topology.py` | 310 | Derived addresses, asset criticality, provenance |
| `app/dedupe.py` | 139 | `dedupe_key` and the `upsert_alert` that collapses bursts |
| `app/schemas.py` | 561 | Every wire contract, and the source of the frontend's types |

---

## How paths are assembled

No route module hardcodes `/api/v1`. Each module declares a bare router, `app/routes/__init__.py` includes all six into one `api_router`, and `create_app()` in `app/main.py` mounts that aggregate under the configured prefix:

```
create_app()
  ├─ health_router   prefix=/api/v1   tags=["system"]     →  GET /health
  └─ api_router      prefix=/api/v1   (bare APIRouter, no prefix of its own)
       ├─ alerts.router      prefix=/alerts     tags=["alerts"]     (4 routes)
       ├─ score.router       prefix=/score      tags=["scoring"]    (1 route)
       ├─ metrics.router     no prefix          tags=["metrics"]    (/metrics/*, /models)
       ├─ analytics.router   prefix=/analytics  tags=["analytics"]  (2 routes)
       ├─ replay.router      no prefix          tags=["traffic"]    (/replay/*, /ingest/start)
       └─ stream.router      prefix=/stream     tags=["stream"]     (1 route)
```

Changing `IDS_API_V1_PREFIX` moves the whole surface. `/replay/start`, `/replay/stop` and `/ingest/start` share the `traffic` tag because they are one concept — where flows come from — even though replay landed in Phase 5 and ingest lands in Phase 9.

---

## routes/__init__.py

Holds `not_implemented(endpoint, phase) -> JSONResponse`, which builds a `NotImplementedResponse` and returns it with status 501. Three routes still use it.

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

Four endpoints and the largest route module, because the queue is the landing page of the whole application.

| Route | Returns |
| --- | --- |
| `GET /alerts` | `AlertPage` — items, `next_cursor`, `limit` |
| `GET /alerts/{alert_id}` | `AlertDetail` |
| `POST /alerts/{alert_id}/verdict` | `VerdictResponse`, status 201 |
| `GET /alerts/{alert_id}/related` | `list[AlertSummary]` |

All four take `session: Session = Depends(get_session)`. `B008` is in ruff's ignore list for exactly this — FastAPI's `Depends()` in a default argument is the documented idiom.

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

Two implemented endpoints plus the Phase 9 stub.

`replay_start` returns **202, not 200**: the work it starts outlives the request, so the response says the replay has been accepted and is running, not that it finished. It checks `bundle.stage1_ready or bundle.stage2_ready` first and answers 503 otherwise — a replay that emitted no alerts would read as "no attacks in this split", which is a different claim.

Error mapping:

| Raised by `app.replay` | Becomes |
| --- | --- |
| `ReplayAlreadyRunning` | 409 |
| `UnknownDataset`, `ValueError` | 422 |

`replay_stop` returns the run's final counters, which is the useful thing to return from a stop — "15,500 rows scored, 93 alerts" rather than an empty acknowledgement — or 409 if nothing is running.

Both read `app.state.replay`, a `ReplayState` created once in the lifespan so the routes report a status object rather than probing for an attribute.

---

## routes/metrics.py

Two implemented endpoints and two Phase 7 stubs.

`model_metrics` serves `app.state.metrics` — the three evaluation artifacts, loaded once at startup — and recomputes nothing. A figure recomputed from whatever alerts happen to be stored would be a different claim wearing the same label, and it would move every time a replay ran. It answers 503 when no artifact is loaded, because an empty table would read as a model that scored zero rather than one that has not been measured.

`threshold_what_if` takes `t`, constrained to `[0.0, 1.0]` at the validation layer, and projects volume, false-positive rate and recall from the persisted error histograms. `t` is **Stage 2's anomaly threshold**, not a Stage 1 probability — see [API Reference](API-Reference.md#t-is-stage-2s-anomaly-threshold) for why a Stage 1 answer is not computable from what is persisted.

Both figures come from `ErrorHistogram.above(t)` over histograms that share their bin edges, so the FPR and the recall describe the same point on the same axis. `alerts_per_analyst_hour` divides by `IDS_ANALYST_SHIFT_HOURS`, not by 24, matching `training/metrics.py` — a slider and a model card quoting different volumes for one operating point is the kind of disagreement nobody notices until a SOC lead sets a threshold from the wrong one.

---

## routes/analytics.py

Two endpoints. Unlike `/metrics/*`, everything here is computed from the `alerts` and `analyst_verdicts` tables: it describes what this deployment has actually seen.

`_floor` buckets timestamps in **Python rather than SQL**, because the date functions differ between SQLite and Postgres and this project's persistence story is that pointing `IDS_DATABASE_URL` at Postgres is a configuration change and nothing more. A `strftime` here would quietly break that.

`analytics_summary` splits its time series into `known` and `unclassified` rather than reporting a total — the unclassified line is the novel-detection headline, and folding it into a total would hide the claim this project is making. Empty buckets are emitted as zeros, because a chart that skips empty hours draws a continuous line through the gap and makes an outage look like steady traffic.

`_throughput` excludes `UNSURE` from `true_positive_rate`'s denominator. It is not a judgement that the alert was wrong, and folding it in would drag the rate down every time an analyst was honest about not knowing. With nothing decided the rate is `null`, not `0.0` — zero would claim every alert was a false positive.

`mitre_coverage` builds its rows from `app.mitre.coverage_vocabulary()`, **not** from the alerts that have fired, so a technique with no hits is a visible zero. "We have never seen this" and "we cannot see this" look identical when the axis comes from the data. `UNCLASSIFIED_ANOMALY` has no row — it maps to no technique by design — but its count sits beside the table, because omitting it would understate exactly the detections this project is proudest of.

---

## The full v1 surface at a glance

| Method | Path | Status | Phase |
| --- | --- | --- | --- |
| GET | `/api/v1/health` | Implemented | 0 |
| POST | `/api/v1/score` | Implemented | 5 |
| GET | `/api/v1/alerts` | Implemented | 5 |
| GET | `/api/v1/alerts/{alert_id}` | Implemented | 5 |
| POST | `/api/v1/alerts/{alert_id}/verdict` | Implemented | 5 |
| GET | `/api/v1/alerts/{alert_id}/related` | Implemented | 5 |
| GET | `/api/v1/stream` | Implemented | 5 |
| GET | `/api/v1/metrics/model` | Implemented | 5 |
| GET | `/api/v1/metrics/threshold` | Implemented | 5 |
| GET | `/api/v1/metrics/drift` | Registered, 501 | 7 |
| GET | `/api/v1/analytics/summary` | Implemented | 5 |
| GET | `/api/v1/analytics/mitre-coverage` | Implemented | 5 |
| POST | `/api/v1/replay/start` | Implemented | 5 |
| POST | `/api/v1/replay/stop` | Implemented | 5 |
| POST | `/api/v1/ingest/start` | Registered, 501 | 9 |
| GET | `/api/v1/models` | Registered, 501 | 7 |

`backend/tests/test_api_surface.py` machine-checks this table from both sides: `DEFERRED_ROUTES` must answer 501 with its exact phase string, `IMPLEMENTED_ROUTES` must not answer 501 at all, and a third test asserts the two lists are disjoint and together cover all sixteen pairs — so a route cannot leave one list without joining the other and silently stop being checked.

**There is no block, drop, quarantine or deny endpoint, and there will not be one.** `test_no_route_mentions_blocking` walks every path in the served schema and asserts it. See [the reasoning](API-Reference.md#explicitly-absent).
