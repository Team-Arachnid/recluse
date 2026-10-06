# Phase 5 checkpoint — the backend API, measured

The brief's checkpoint for this phase is specific: *"start a replay at 10x, watch
alerts arrive over `curl -N localhost:8000/api/v1/stream`, confirm dedup is collapsing
bursts."* This is that, run against the shipped server with the real champion loaded,
with the output pasted rather than described.

Reproduce it with:

```bash
make backend                                    # or: uvicorn app.main:app
python scripts/phase5_checkpoint.py --speed 10 --seconds 40
```

## What was running

| | |
| --- | --- |
| Model | `stage1-lgbm-202609281410` (the shipped champion, LightGBM) |
| Stage 2 | `stage2-autoencoder-202609291144` |
| Replay source | `data/processed/test.parquet` — Friday, 595,894 held-out rows |
| Speed | 10x (500 rows/batch, 1.0s between batches) |
| Database | SQLite, `data/ids.db` |

## The run

```
health: ok, model_version=stage1-lgbm-202609281410
stream before replay: 503 (expected 503)
replay started: test at 10x
replay stopped: 202, 15500 rows scored

stream: 93 alert events, 0 heartbeats in 40s
distinct alert rows: 4
rows with >1 occurrence: 2
largest burst collapsed into one row: id 1, x85 occurrences
events received / distinct rows: 93 / 4

queue (top 5 by risk, 200):
  risk 0.9935  critical dos                    172.16.0.1 -> 192.168.10.50:80   x1
  risk 0.9566  critical UNCLASSIFIED_ANOMALY   172.16.0.1 -> 192.168.10.25:443  x85
  risk 0.6950  high     UNCLASSIFIED_ANOMALY   172.16.0.1 -> 192.168.10.14:443  x8
  risk 0.5648  high     brute_force            172.16.0.1 -> 192.168.10.50:22   x1

mitre coverage: 2 of 7 techniques fired
unclassified anomalies (no technique by design): 2
```

## What each line is evidence of

**15,500 rows scored in 40 seconds** at 10x, against a nominal 1x rate of 50 flows/second
— so about 390 rows/second measured against 500 targeted. The gap is the scoring and
explanation work inside each batch, which the pacing does not subtract. It is reported
rather than tuned away: a replay that claimed exactly 10x while delivering 7.8x would be
the dishonest version of this number.

**The stream answered 503 before a replay existed.** An empty stream and a dead stream
are indistinguishable from the client's side, so they get different status codes.

**93 events for 4 distinct alert rows.** This is the dedupe claim, measured. Without it
those 93 events would be 93 queue rows in 40 seconds, and the brief's warning — "without
this your queue is unusable within thirty seconds of starting the replay" — is not
rhetorical: at this rate an unfiltered queue would be past a hundred rows before an
analyst finished reading the first one.

**One burst collapsed 85 flows into a single row.** The queue shows it as one incident
with `x85` beside it, ranked by the worst flow in the burst rather than the most recent.

**The queue is ordered by risk, not time.** The `dos` alert at 0.9935 outranks the
`brute_force` one at 0.5648 regardless of which arrived first.

**Two unclassified anomalies, with no technique attached.** This is the project's
headline case working end to end: Stage 2 fired on traffic Stage 1 could not name, and
the alert says so rather than attaching the nearest-looking ATT&CK id. One of them is
the second-highest-risk item in the queue.

**2 of 7 techniques fired, and the other five are visible zeros.** The heatmap's axis
comes from `app/mitre.py`'s table rather than from what happened to fire, so "we have
never seen this" and "we cannot see this" stay distinguishable.

## The raw stream

`curl -N` against a live 10x replay, unedited. Note the same `id` recurring with a
rising `occurrence_count` — that is a burst collapsing in real time:

```
event: alert
data: {"id": 4, "kind": "UNCLASSIFIED_ANOMALY", "family": null, "severity": "high",
       "risk_score": 0.695, "anomaly_score": 0.15348082780838013,
       "detected_at": "2026-10-06T05:25:02.190827Z", "src_ip": "172.16.0.1",
       "dst_ip": "192.168.10.14", "occurrence_count": 27,
       "model_version": "stage1-lgbm-202609281410"}

event: alert
data: {"id": 4, ..., "occurrence_count": 28, ...}

event: alert
data: {"id": 4, ..., "occurrence_count": 29, ...}
```

## One bug this checkpoint found

The first run of the raw `curl -N` capture produced `"detected_at":
"2026-10-06T05:25:02.190827"` — **with no timezone suffix.** Every event after the first
in a burst is built from a row that has been round-tripped through SQLite, which strips
`tzinfo`, so the value serialised without its `Z` and a browser would have parsed UTC as
local time.

That is the worst shape for a timezone bug: the first event of each burst looked correct
and only the repeats were wrong, so it would have read as an intermittent display glitch
rather than a systematic offset. `app/pipeline.py` now re-attaches UTC when the ORM hands
back a naive value, and the output above is the re-verified result. No test had caught it
because the pipeline's own tests assert ids and counts off events, and the surviving
`detected_at` is only observable on the wire.

## Addresses in this report are derived, not observed

Every `src_ip` and `dst_ip` above comes from `app/topology.py`, which maps an attack
family onto the hosts the published CICIDS2017 topology documents for it. The
MachineLearningCSV release ships no addresses at all — its header starts at
`Destination Port`. `dst_port` **is** observed; `src_port` and `protocol` are absent and
stay null; `detected_at` is replay wall-clock, because a replay genuinely detects at
replay time. Every alert carries that breakdown in `raw_flow._provenance`, so the caveat
travels with the data instead of living only here.
