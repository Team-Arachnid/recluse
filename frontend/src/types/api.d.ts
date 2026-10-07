/**
 * GENERATED FILE - do not edit.
 *
 * Regenerate with `npm run gen:types` while the backend is running.
 * Source: the FastAPI app OpenAPI schema.
 */
export interface paths {
    "/api/v1/health": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Liveness, loaded model version, and process uptime
         * @description Report what is actually loaded, not a hardcoded version string.
         *
         *     ``model_version`` is ``"unloaded"`` until Phase 2 produces an artifact
         *     bundle. That is the honest answer for a scaffold, and it is what the
         *     dashboard renders.
         */
        get: operations["health_api_v1_health_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/alerts": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Filter, sort and cursor-paginate the alert queue
         * @description The triage queue: highest risk first, keyset-paginated.
         *
         *     Ordered by `risk_score` descending with `id` descending as the tie
         *     breaker. The tie breaker is not cosmetic: `risk_score` is rounded to four
         *     places, so ties are common rather than rare, and without a second key the
         *     database is free to return tied rows in a different order on each request
         *     -- which would make a cursor built on `risk_score` alone skip rows.
         */
        get: operations["list_alerts_api_v1_alerts_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/alerts/stats": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Counts for the strip above the queue: volume, hosts, unjudged
         * @description What this deployment has actually seen, for the strip above the queue.
         *
         *     Everything here is counted from the database. The projected alert volume
         *     and the operating threshold that sit beside these on the strip come from
         *     `GET /metrics/threshold` instead, because a projection at a candidate
         *     threshold is a property of the model rather than of the queue -- and
         *     recomputing it from stored alerts would make it move every time a replay
         *     ran.
         *
         *     There is no accuracy figure here. A strip is precisely where a number gets
         *     read without its caveat, and on 99% benign traffic that one is
         *     uninformative by construction.
         */
        get: operations["queue_stats_api_v1_alerts_stats_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/alerts/status": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        /**
         * Move a batch of alerts to a triage status (the bulk dismiss action)
         * @description Apply one triage status to a batch of alerts, in one transaction.
         *
         *     The queue's action is "bulk select, then dismiss", so the endpoint takes a
         *     list. Fifty single-id requests would be fifty transactions and fifty
         *     chances to half-apply what the analyst decided once.
         *
         *     An id that no longer exists is reported in `missing` rather than failing
         *     the batch. A bulk action against a queue the replay is still writing to can
         *     legitimately name a row that has since gone, and rejecting all fifty
         *     because of one stale id would throw away forty-nine decisions that were
         *     fine.
         *
         *     This moves `alerts.status` and nothing else. It writes no verdict: a
         *     triage state and an analyst's judgement are different facts, and dismissing
         *     a noisy row is not the same claim as "the model was wrong" -- conflating
         *     them would poison the labels the retraining pipeline reads.
         */
        patch: operations["update_alert_status_api_v1_alerts_status_patch"];
        trace?: never;
    };
    "/api/v1/alerts/{alert_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Alert detail: explanation, narrative, remediation, raw flow
         * @description Everything the Alert Detail drawer needs, in one request.
         *
         *     The drawer answers three questions in a fixed order -- why was this
         *     flagged, what is it likely to be, how do I fix it -- so the response
         *     carries the explanation, the narrated sentence, the technique and the
         *     playbook together rather than making the client assemble them from
         *     several calls. `ground_truth_label` is populated only for replayed rows
         *     and the frontend badges it demo-only.
         */
        get: operations["get_alert_api_v1_alerts__alert_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/alerts/{alert_id}/verdict": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Record an analyst verdict (TP / FP / UNSURE) with an optional note
         * @description Write one `analyst_verdicts` row. This is the input to active learning.
         *
         *     `model_version` is captured from the alert being judged rather than from
         *     whatever is currently loaded, so the label stays auditable against the
         *     model that actually produced the alert even after a later retrain replaces
         *     the champion. A label attributed to the wrong model version is worse than
         *     no label: it would train the next model on a correction to a decision it
         *     never made.
         *
         *     The alert's own `status` is deliberately not changed here. Recording a
         *     judgement and moving an alert through triage are different actions, the
         *     spec gives this endpoint only the verdict, and a verdict that silently
         *     closed an alert would take it out of a colleague's queue mid-review.
         */
        post: operations["submit_verdict_api_v1_alerts__alert_id__verdict_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/alerts/{alert_id}/related": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Other alerts from the same source host in a 24h window
         * @description Other alerts from this source host, so a sequence reads as one story.
         *
         *     A scan followed by an exploit attempt from the same host is one incident
         *     told in two alerts; without this the analyst sees two unrelated rows and
         *     has to notice the shared address themselves. Backed by
         *     `ix_alerts_src_ip_detected_at`, which the initial migration creates for
         *     exactly this query.
         *
         *     The window is measured from the anchor alert's own `detected_at`, not from
         *     now, and it reaches in both directions. An alert opened a week after the
         *     fact would otherwise show no context at all, and the activity worth seeing
         *     is what surrounded the anchor rather than what happened recently.
         */
        get: operations["related_alerts_api_v1_alerts__alert_id__related_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/score": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Score a batch of flow records
         * @description Score ``flows`` through the loaded bundle and count the alerts.
         *
         *     ``ModelBundle.score_batch`` already returns one record per flow, in
         *     input order, with ``model_version`` attached -- this handler maps those
         *     records onto the wire model and counts the alerts rather than
         *     re-deriving either from scratch.
         */
        post: operations["score_flows_api_v1_score_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/metrics/model": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Per-class metrics, PR and ROC curves, and the LOAO table
         * @description Serve the measured evaluation, exactly as the offline run wrote it.
         *
         *     Nothing here is recomputed from the database. These are the numbers from
         *     the held-out day, and a figure recomputed from whatever alerts happen to
         *     be stored would be a different claim wearing the same label -- it would
         *     move every time a replay ran.
         */
        get: operations["model_metrics_api_v1_metrics_model_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/metrics/threshold": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Recompute projected alert volume at a candidate threshold
         * @description Project alert volume, false-positive rate and recall at threshold `t`.
         *
         *     **`t` is Stage 2's anomaly threshold**, not a Stage 1 probability.
         *     docs/Frontend-Screens.md draws this slider across the anomaly-score
         *     histogram and names the arithmetic it does -- `ErrorHistogram.above(tau)`
         *     over the persisted benign error distribution -- and a Stage 1 answer
         *     cannot be computed at an arbitrary `t` from what is stored, because the
         *     PR and ROC curves are `(recall, precision)` and `(fpr, tpr)` pairs with no
         *     threshold column to look `t` up in.
         *
         *     Both figures are read off histograms that share their bin edges, so the
         *     false-positive rate and the recall describe the same point on the same
         *     axis. The result is approximate by construction -- a bin straddling `t`
         *     contributes all of itself -- which is exactly the arithmetic a slider
         *     does when it projects a count from bins instead of rescoring a day of
         *     traffic on every drag.
         *
         *     `[0.0, 1.0]` is the declared range and it stays that way, but Stage 2's
         *     errors reach about 1.83 on the shipped card, so the top of the attack
         *     distribution is outside what this slider can express. The response says
         *     so in `covers_distribution` rather than quietly reporting a recall for a
         *     threshold the axis cannot reach.
         */
        get: operations["threshold_what_if_api_v1_metrics_threshold_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/metrics/anomaly-histogram": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * The Stage 2 reconstruction-error bins the threshold line is drawn across
         * @description Serve the persisted error bins, so the slider has an axis to live on.
         *
         *     `GET /metrics/threshold` answers "what happens at t". It cannot answer
         *     "what does the distribution look like", and the Live Traffic Monitor draws
         *     the threshold as a line *across a histogram* -- so it needs the bins
         *     themselves. Rebuilding the shape by sampling the projection endpoint sixty
         *     times would be sixty requests to draw one chart, and the result would still
         *     be a cumulative curve rather than the distribution.
         *
         *     Phase 3 persisted these as sixty log-spaced bins with counts and reference
         *     percentiles rather than as raw rows, which is what lets the drag project an
         *     alert count by arithmetic instead of rescoring a day of traffic on every
         *     pointer move.
         *
         *     Nothing here is recomputed from the database. These are the distributions
         *     the model was calibrated against; an overlay of what live traffic looks
         *     like now is a drift question and lands with `GET /metrics/drift` in
         *     Phase 7.
         */
        get: operations["anomaly_histogram_api_v1_metrics_anomaly_histogram_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/metrics/drift": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * PSI per feature over time, with the baseline overlay
         * @description Serve the stored drift snapshots.
         *
         *     Computed by ``python -m training.drift_job``, never here. A PSI over
         *     ninety-two features is a full scan of the sample table, and a request handler
         *     is the wrong place for one; more to the point, drift is a property of a window
         *     of time rather than of a request, so computing it per request would give two
         *     readers two different answers minutes apart.
         *
         *     An empty response is a real state and says so -- ``latest`` is null until the
         *     first run. A drift monitor that invented a flat line at zero would tell its
         *     reader that everything is fine, which is the one lie this screen must not
         *     tell.
         */
        get: operations["drift_metrics_api_v1_metrics_drift_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/models": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Model registry: versions, thresholds, and the alerts each one scored
         * @description The registry, and with it the audit trail made readable.
         *
         *     "Which model version scored which alert" has been answerable since Phase 5 --
         *     ``alerts.model_version`` is written on every row. What was missing was a place
         *     to ask, and a column nobody can query is not an audit trail. The
         *     ``alerts_scored`` figure here is the number somebody needs after an incident,
         *     when the question is how many decisions a model that turned out to be wrong
         *     was behind.
         */
        get: operations["list_models_api_v1_models_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/retrain": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Retraining history, including the runs that were not promoted
         * @description Every run, newest first.
         *
         *     The runs that were *not* promoted are the point of keeping this history: a
         *     gate that has never declined anything is a gate nobody has evidence for.
         */
        get: operations["retrain_status_api_v1_retrain_get"];
        put?: never;
        /**
         * Request a challenger run from the analyst labels recorded so far
         * @description Record a retrain request. **202, and nothing is fitted here.**
         *
         *     The response says the request has been accepted, not that a model has been
         *     trained -- a 200 would claim a completed job, and this one takes minutes. The
         *     work is done by ``python -m training.retrain``, which claims the oldest
         *     pending row.
         *
         *     Refuses when a run is already queued or running, because two concurrent
         *     retrains would both consume the same labels and race to publish a champion.
         *     Refuses when there are no unconsumed labels, because a challenger fitted on
         *     the same data as the champion differs from it only by random seed, and
         *     promoting on that would be promoting on noise.
         */
        post: operations["request_retrain_api_v1_retrain_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/analytics/summary": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Alerts over time, family mix, top hosts and sources, SOC throughput
         * @description Everything on the analytics screen except the heatmap.
         *
         *     The alerts-over-time series is stacked by known family versus
         *     `UNCLASSIFIED_ANOMALY`, because that split is the novel-detection headline
         *     -- the unclassified line is the one a reviewer asks about if it spikes, and
         *     burying it inside a total would hide exactly the claim this project is
         *     making.
         *
         *     Buckets with no alerts are emitted as zeros rather than omitted. A chart
         *     that skips empty hours draws a continuous line through a gap and makes an
         *     outage look like steady traffic.
         */
        get: operations["analytics_summary_api_v1_analytics_summary_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/analytics/feedback": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Labels since the last retrain, the TP/FP split, and the disagreement rate
         * @description The Feedback Loop screen: analyst judgement on its way back to the model.
         *
         *     `labels_pending_retrain` counts `analyst_verdicts` rows whose `consumed_at`
         *     is still null. That column exists for exactly this question, which is why
         *     "since the last retrain" needs no second table and no snapshot job.
         *
         *     `disagreement_rate` is FP / (TP + FP) -- the share of decided alerts where
         *     the analyst overruled the model. UNSURE sits outside the denominator for
         *     the same reason it does in `_throughput`: it is not a judgement that the
         *     model was wrong, and counting it as one would push the rate up every time
         *     somebody was honest about not knowing.
         *
         *     The per-version breakdown is the audit trail doing its job. Verdicts carry
         *     the version of the model that produced the alert, captured at verdict time,
         *     so a label recorded against a since-replaced champion stays attributed to
         *     it -- and a retrain can tell which of its labels are corrections to
         *     decisions it actually made.
         *
         *     `retrain_available` is false and says which phase changes that. A button
         *     that looked live and did nothing would be worse than one that explains
         *     itself.
         */
        get: operations["feedback_loop_api_v1_analytics_feedback_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/analytics/mitre-coverage": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Technique counts for the coverage heatmap
         * @description One row per technique the lookup knows, with how often it has fired.
         *
         *     **The axis comes from `app.mitre.coverage_vocabulary()`, not from the
         *     alerts that have fired**, so a technique with no hits is a visible zero
         *     rather than a missing row. "We have never seen this" and "we cannot see
         *     this" look identical when the axis is built from the data, and telling
         *     them apart is the entire point of a coverage heatmap.
         *
         *     `UNCLASSIFIED_ANOMALY` maps to no technique by design, so it is reported
         *     alongside the table as its own count rather than folded into it. Hiding it
         *     would understate exactly the detections this project is proudest of;
         *     giving it a technique id would be a fabrication.
         */
        get: operations["mitre_coverage_api_v1_analytics_mitre_coverage_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/replay/status": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Whether a replay is running, at what speed, and what it has done
         * @description Read the current traffic-source state without changing it.
         *
         *     Start and stop are both 202s whose bodies describe the state at the moment
         *     they were called, which is no help to a dashboard that was opened after the
         *     fact: a browser refresh in the middle of a replay would otherwise leave the
         *     speed control guessing, and a control that shows 1x while the engine runs
         *     at 100x is worse than one that shows nothing.
         *
         *     Always 200, including when nothing is running -- `running: false` is the
         *     answer, not an error. `GET /stream` is the endpoint that distinguishes the
         *     two with a status code, because there a dead connection and an idle one are
         *     genuinely different problems.
         */
        get: operations["replay_status_api_v1_replay_status_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/replay/start": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Start replaying held-out test flows at 1x, 10x or 100x
         * @description Begin streaming a held-out split, scoring in batches and pushing over SSE.
         *
         *     **202, not 200.** The work this starts outlives the request: the response
         *     says the replay has been accepted and is now running, not that it has
         *     finished. A 200 would claim a completed job.
         */
        post: operations["replay_start_api_v1_replay_start_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/replay/stop": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Stop the active replay
         * @description Cancel the running replay and report what it managed before stopping.
         *
         *     The counters in the response are the run's final totals, which is the
         *     useful thing to return from a stop -- "it scored 142,000 rows and raised
         *     318 alerts before you stopped it" rather than an empty acknowledgement.
         */
        post: operations["replay_stop_api_v1_replay_stop_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/ingest/start": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Begin scoring a live capture (authorised networks only) */
        post: operations["ingest_start_api_v1_ingest_start_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/stream": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Live alert feed over server-sent events
         * @description Hold a connection open and push one event per persisted alert.
         *
         *     Answers **503** when no traffic source is running rather than holding open
         *     a connection that can never produce an event. An empty stream and a stream
         *     with nothing to say are indistinguishable from the client's side, and the
         *     first is a bug while the second is Tuesday -- so the two are given
         *     different status codes. `POST /replay/start` is what makes a source
         *     active.
         */
        get: operations["stream_alerts_api_v1_stream_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
}
export type webhooks = Record<string, never>;
export interface components {
    schemas: {
        /**
         * AlertDetail
         * @description GET /api/v1/alerts/{alert_id}'s response.
         *
         *     Everything a summary has, plus why it fired, what it likely is, and how
         *     to fix it. A superset of AlertSummary rather than an independent model,
         *     because the detail response genuinely is one and two separate models
         *     would drift apart.
         */
        AlertDetail: {
            /** Id */
            id: number;
            /**
             * Kind
             * @enum {string}
             */
            kind: "KNOWN" | "UNCLASSIFIED_ANOMALY";
            /**
             * Family
             * @description Stage 1 label; always null for UNCLASSIFIED_ANOMALY.
             */
            family: ("dos" | "ddos" | "brute_force" | "port_scan" | "web_attack" | "botnet" | "infiltration") | null;
            /**
             * Severity
             * @enum {string}
             */
            severity: "low" | "medium" | "high" | "critical";
            /**
             * Risk Score
             * @description Queue ordering key; never the timestamp.
             */
            risk_score: number;
            /**
             * Confidence
             * @description Stage 1 max attack-class probability.
             */
            confidence: number | null;
            /**
             * Anomaly Score
             * @description Stage 2 mean reconstruction error.
             */
            anomaly_score: number | null;
            /**
             * Detected At
             * Format: date-time
             */
            detected_at: string;
            /** Src Ip */
            src_ip: string;
            /** Src Port */
            src_port: number | null;
            /** Dst Ip */
            dst_ip: string;
            /** Dst Port */
            dst_port: number | null;
            /** Protocol */
            protocol: string | null;
            /**
             * Asset Criticality
             * @description From enrichment; see app/topology.py.
             */
            asset_criticality: string | null;
            /**
             * Status
             * @enum {string}
             */
            status: "open" | "in_review" | "closed" | "dismissed";
            /**
             * Occurrence Count
             * @description The dedupe count; 5,000 flows can be one row.
             */
            occurrence_count: number;
            /**
             * First Seen
             * Format: date-time
             */
            first_seen: string;
            /**
             * Last Seen
             * Format: date-time
             */
            last_seen: string;
            /**
             * Mitre Technique
             * @description Null for UNCLASSIFIED_ANOMALY, which maps to no technique.
             */
            mitre_technique: string | null;
            /**
             * Detection Stage
             * @enum {string}
             */
            detection_stage: "stage1_supervised" | "stage2_anomaly";
            /** Model Version */
            model_version: string;
            /**
             * Source
             * @enum {string}
             */
            source: "replay" | "live" | "api";
            /**
             * Latest Verdict
             * @description Most recent analyst_verdicts.verdict by created_at; null until judged.
             */
            latest_verdict: ("TP" | "FP" | "UNSURE") | null;
            explanation: components["schemas"]["AlertExplanation"];
            /**
             * Narrative
             * @description The templated English sentence; app/explain.py::narrate.
             */
            narrative: string;
            recommended_actions: components["schemas"]["RecommendedActions"];
            /**
             * Raw Flow
             * @description The flow as it arrived; carries the _provenance key from app/topology.py.
             */
            raw_flow: {
                [key: string]: unknown;
            };
            /**
             * Ground Truth Label
             * @description Replay only; always null for live capture, badged demo-only on the frontend.
             */
            ground_truth_label: string | null;
            /**
             * Host Prior Alert Count
             * @description Other alerts from this source host; see GET /alerts/{id}/related.
             */
            host_prior_alert_count: number;
        };
        /**
         * AlertEvent
         * @description Payload of the ``alert`` SSE event (API-Reference.md "Planned event shape").
         *
         *     SSE responses are ``text/event-stream``, so FastAPI never attaches this
         *     as a ``response_model`` -- it exists purely so the frontend's
         *     ``EventSource`` handler has a generated type instead of a hand-written
         *     one that can silently drift from it. Deliberately narrower than
         *     AlertSummary: the ticker renders one line and the drawer fetches the
         *     rest when clicked.
         */
        AlertEvent: {
            /** Id */
            id: number;
            /**
             * Kind
             * @enum {string}
             */
            kind: "KNOWN" | "UNCLASSIFIED_ANOMALY";
            /** Family */
            family: ("dos" | "ddos" | "brute_force" | "port_scan" | "web_attack" | "botnet" | "infiltration") | null;
            /**
             * Severity
             * @enum {string}
             */
            severity: "low" | "medium" | "high" | "critical";
            /** Risk Score */
            risk_score: number;
            /** Anomaly Score */
            anomaly_score: number | null;
            /**
             * Detected At
             * Format: date-time
             */
            detected_at: string;
            /** Src Ip */
            src_ip: string;
            /** Dst Ip */
            dst_ip: string;
            /** Occurrence Count */
            occurrence_count: number;
            /** Model Version */
            model_version: string;
        };
        /**
         * AlertExplanation
         * @description ``explanation`` on AlertDetail -- app/explain.py's per-alert attribution.
         *
         *     ``base_value`` is TreeSHAP's expected value for the predicted class and is
         *     absent for the reconstruction-error explainer, which has no such baseline.
         */
        AlertExplanation: {
            /**
             * Explainer
             * @enum {string}
             */
            explainer: "treeshap" | "reconstruction_error";
            /**
             * Base Value
             * @description TreeSHAP's expected value. Absent for Stage 2.
             */
            base_value?: number | null;
            /** Contributors */
            contributors: components["schemas"]["Contributor"][];
        };
        /**
         * AlertPage
         * @description GET /api/v1/alerts' response: a page of the triage queue.
         *
         *     No ``total``, deliberately: counting the whole filtered set on every page
         *     is the query that makes a triage queue slow, and nothing in
         *     docs/Frontend-Screens.md asks for one. Do not add it reflexively.
         */
        AlertPage: {
            /** Items */
            items: components["schemas"]["AlertSummary"][];
            /**
             * Next Cursor
             * @description Opaque cursor for the next page; null if none.
             */
            next_cursor: string | null;
            /** Limit */
            limit: number;
        };
        /**
         * AlertStatusResult
         * @description What PATCH /api/v1/alerts/status returns.
         *
         *     ``missing`` is reported rather than raising: a bulk action against a queue
         *     the replay is still writing to can legitimately name a row that has since
         *     been deleted, and failing the whole batch for one stale id would throw away
         *     the forty-nine decisions that were fine. The caller is told exactly which
         *     ids did not land.
         */
        AlertStatusResult: {
            /**
             * Status
             * @enum {string}
             */
            status: "open" | "in_review" | "closed" | "dismissed";
            /** Updated */
            updated: number[];
            /** Missing */
            missing: number[];
        };
        /**
         * AlertStatusUpdate
         * @description PATCH /api/v1/alerts/status' request body -- the bulk triage action.
         *
         *     A list rather than one id per request because the queue's action is "bulk
         *     select, then dismiss": fifty single requests would be fifty transactions
         *     and fifty chances to half-apply the analyst's one decision.
         *
         *     ``status`` is the full ``AlertStatus`` vocabulary and not just
         *     ``dismissed``. Moving a row to ``in_review`` or ``closed`` is the same
         *     operation on the same column, and an endpoint that only allowed dismissal
         *     would have to be replaced the first time the queue grew a second action.
         */
        AlertStatusUpdate: {
            /** Alert Ids */
            alert_ids: number[];
            /**
             * Status
             * @enum {string}
             */
            status: "open" | "in_review" | "closed" | "dismissed";
        };
        /**
         * AlertSummary
         * @description One row of the triage queue -- GET /api/v1/alerts.
         *
         *     Columns fixed by docs/Frontend-Screens.md section 1 (Triage Queue).
         *     ``latest_verdict`` is derived, not a column: ``analyst_verdicts`` is
         *     one-to-many, and this is where "the" verdict of an alert -- the most
         *     recent one by ``created_at`` -- is defined, so every endpoint that
         *     returns a summary agrees on what it means.
         */
        AlertSummary: {
            /** Id */
            id: number;
            /**
             * Kind
             * @enum {string}
             */
            kind: "KNOWN" | "UNCLASSIFIED_ANOMALY";
            /**
             * Family
             * @description Stage 1 label; always null for UNCLASSIFIED_ANOMALY.
             */
            family: ("dos" | "ddos" | "brute_force" | "port_scan" | "web_attack" | "botnet" | "infiltration") | null;
            /**
             * Severity
             * @enum {string}
             */
            severity: "low" | "medium" | "high" | "critical";
            /**
             * Risk Score
             * @description Queue ordering key; never the timestamp.
             */
            risk_score: number;
            /**
             * Confidence
             * @description Stage 1 max attack-class probability.
             */
            confidence: number | null;
            /**
             * Anomaly Score
             * @description Stage 2 mean reconstruction error.
             */
            anomaly_score: number | null;
            /**
             * Detected At
             * Format: date-time
             */
            detected_at: string;
            /** Src Ip */
            src_ip: string;
            /** Src Port */
            src_port: number | null;
            /** Dst Ip */
            dst_ip: string;
            /** Dst Port */
            dst_port: number | null;
            /** Protocol */
            protocol: string | null;
            /**
             * Asset Criticality
             * @description From enrichment; see app/topology.py.
             */
            asset_criticality: string | null;
            /**
             * Status
             * @enum {string}
             */
            status: "open" | "in_review" | "closed" | "dismissed";
            /**
             * Occurrence Count
             * @description The dedupe count; 5,000 flows can be one row.
             */
            occurrence_count: number;
            /**
             * First Seen
             * Format: date-time
             */
            first_seen: string;
            /**
             * Last Seen
             * Format: date-time
             */
            last_seen: string;
            /**
             * Mitre Technique
             * @description Null for UNCLASSIFIED_ANOMALY, which maps to no technique.
             */
            mitre_technique: string | null;
            /**
             * Detection Stage
             * @enum {string}
             */
            detection_stage: "stage1_supervised" | "stage2_anomaly";
            /** Model Version */
            model_version: string;
            /**
             * Source
             * @enum {string}
             */
            source: "replay" | "live" | "api";
            /**
             * Latest Verdict
             * @description Most recent analyst_verdicts.verdict by created_at; null until judged.
             */
            latest_verdict: ("TP" | "FP" | "UNSURE") | null;
        };
        /**
         * AnalyticsSummary
         * @description GET /api/v1/analytics/summary. No accuracy tile, by design.
         */
        AnalyticsSummary: {
            /** Range */
            range: string;
            /**
             * Generated At
             * Format: date-time
             */
            generated_at: string;
            /** Total Alerts */
            total_alerts: number;
            /** Unclassified Alerts */
            unclassified_alerts: number;
            /**
             * Unclassified Rate
             * @description A candidate hero number; accuracy is not.
             */
            unclassified_rate: number;
            /** Series */
            series: components["schemas"]["TimeBucket"][];
            /** Families */
            families: components["schemas"]["CountedPair"][];
            /** Top Destination Hosts */
            top_destination_hosts: components["schemas"]["CountedPair"][];
            /** Top Destination Ports */
            top_destination_ports: components["schemas"]["CountedPair"][];
            /** Top Source Hosts */
            top_source_hosts: components["schemas"]["CountedPair"][];
            throughput: components["schemas"]["ThroughputStats"];
        };
        /**
         * AnomalyHistogram
         * @description GET /api/v1/metrics/anomaly-histogram -- the axis the slider is drawn on.
         *
         *     The Live Traffic Monitor draws the threshold as a draggable line *across a
         *     histogram*, so it needs the bins themselves and not only a projection at
         *     one point. ``GET /metrics/threshold`` answers "what happens at t"; it
         *     cannot answer "what does the distribution look like", and reconstructing
         *     the shape from sixty calls to it would be sixty requests to redraw one
         *     chart.
         *
         *     Every distribution here shares ``edges``, which is what makes the benign
         *     and attack curves comparable on one axis -- see
         *     ``MetricsStore.error_histogram``.
         */
        AnomalyHistogram: {
            /** Edges */
            edges: number[];
            /**
             * Spacing
             * @description 'log' or 'linear'; the axis scale the bins were cut on.
             */
            spacing: string;
            /**
             * Tau Anom
             * @description The operating threshold the line starts at.
             */
            tau_anom: number | null;
            /**
             * Budget Tau
             * @description Where the threshold would sit if cut to the false-positive budget instead.
             */
            budget_tau: number | null;
            /** Distributions */
            distributions: components["schemas"]["ErrorDistribution"][];
        };
        /**
         * ClassMetrics
         * @description Precision, recall, F1 and support for one family.
         */
        ClassMetrics: {
            /** Precision */
            precision: number;
            /** Recall */
            recall: number;
            /** F1 */
            f1: number;
            /** Support */
            support: number;
        };
        /**
         * Contributor
         * @description One ranked feature behind an alert's score.
         *
         *     ``value``/``contribution`` are TreeSHAP's (Stage 1); ``error`` is the
         *     reconstruction-error explainer's (Stage 2). Both groups are optional on
         *     one model, rather than two models the UI has to discriminate, because the
         *     Alert Detail drawer renders one signed bar chart either way (see
         *     app/explain.py).
         */
        Contributor: {
            /** Feature */
            feature: string;
            /**
             * Share
             * @description This contributor's share of the total attribution.
             */
            share: number;
            /**
             * Value
             * @description The row's scaled feature value. TreeSHAP only.
             */
            value?: number | null;
            /**
             * Contribution
             * @description Signed SHAP contribution. TreeSHAP only.
             */
            contribution?: number | null;
            /**
             * Error
             * @description Per-feature reconstruction error. Stage 2 only.
             */
            error?: number | null;
        };
        /**
         * CountedPair
         * @description One ranked row: a value and how often it appears.
         */
        CountedPair: {
            /** Value */
            value: string;
            /** Count */
            count: number;
        };
        /**
         * CurvePair
         * @description The PR and ROC point series, rendered side by side.
         *
         *     Both are lists of two-element points: PR is ``(recall, precision)`` and
         *     ROC is ``(fpr, tpr)``. Neither carries a threshold column, which is why
         *     GET /metrics/threshold cannot be answered from them and reads the error
         *     histograms instead.
         */
        CurvePair: {
            /**
             * Pr
             * @description (recall, precision) points.
             */
            pr: number[][];
            /**
             * Roc
             * @description (fpr, tpr) points.
             */
            roc: number[][];
        };
        /**
         * DriftFeatureScore
         * @description One feature's PSI inside one snapshot.
         */
        DriftFeatureScore: {
            /** Feature */
            feature: string;
            /** Psi */
            psi: number;
            /**
             * Band
             * @enum {string}
             */
            band: "stable" | "moderate" | "significant";
            /**
             * Expected
             * @description The reference share per bin. The 'expected' half of the PSI.
             */
            expected?: number[] | null;
            /**
             * Actual
             * @description The observed share per bin, on the reference's own edges.
             */
            actual?: number[] | null;
        };
        /**
         * DriftResponse
         * @description GET /api/v1/metrics/drift.
         *
         *     Carries the latest snapshot in full plus a per-feature series across
         *     snapshots, because the screen needs both: the bands say what is true now, and
         *     the series says whether it has been true for a week or started last night.
         *
         *     ``baseline`` is the training benign score distribution the observed one is
         *     overlaid on. Both are on the same bin edges -- two curves on two axes separate
         *     for reasons that have nothing to do with drift.
         */
        DriftResponse: {
            /** @description Null until the first run. */
            latest: components["schemas"]["DriftSnapshot"] | null;
            /**
             * Series
             * @description Worst feature first.
             */
            series: components["schemas"]["DriftSeries"][];
            /**
             * Snapshots
             * @description How many runs are stored.
             */
            snapshots: number;
            /**
             * Baseline
             * @description The training benign error distribution, on shared edges.
             */
            baseline: {
                [key: string]: unknown;
            } | null;
            /** Moderate Threshold */
            moderate_threshold: number;
            /** Significant Threshold */
            significant_threshold: number;
            /**
             * Sampled Rows
             * @description Rows currently in the sample table.
             */
            sampled_rows: number;
            /**
             * Sample Stride
             * @description One flow in this many is kept for drift.
             */
            sample_stride: number;
        };
        /**
         * DriftSeries
         * @description One feature's PSI across every snapshot -- what the screen plots.
         */
        DriftSeries: {
            /** Feature */
            feature: string;
            /** Worst Psi */
            worst_psi: number;
            /** Latest Psi */
            latest_psi: number;
            /**
             * Latest Band
             * @enum {string}
             */
            latest_band: "stable" | "moderate" | "significant";
            /** Points */
            points: components["schemas"]["DriftSeriesPoint"][];
        };
        /**
         * DriftSeriesPoint
         * @description One feature's PSI at one point in time.
         */
        DriftSeriesPoint: {
            /**
             * Computed At
             * Format: date-time
             */
            computed_at: string;
            /** Psi */
            psi: number;
            /**
             * Band
             * @enum {string}
             */
            band: "stable" | "moderate" | "significant";
        };
        /**
         * DriftSnapshot
         * @description One nightly run: what it observed and what it concluded.
         *
         *     ``features`` is empty on a run that declined to score -- when the window held
         *     fewer rows than ``IDS_DRIFT_MIN_ROWS``. That is reported as a run with a note
         *     rather than omitted, because "we looked and there was not enough traffic" and
         *     "we did not look" are different facts and the screen has to be able to tell
         *     them apart.
         */
        DriftSnapshot: {
            /** Id */
            id: number;
            /**
             * Computed At
             * Format: date-time
             */
            computed_at: string;
            /** Model Version */
            model_version: string;
            /**
             * Observed From
             * Format: date-time
             */
            observed_from: string;
            /**
             * Observed To
             * Format: date-time
             */
            observed_to: string;
            /** Rows Observed */
            rows_observed: number;
            /** Reference */
            reference: string | null;
            /** Reference Rows */
            reference_rows: number | null;
            /** Features Scored */
            features_scored: number;
            /** Max Psi */
            max_psi: number;
            /** Moderate Count */
            moderate_count: number;
            /** Significant Count */
            significant_count: number;
            /**
             * Retrain Recommended
             * @description True once any single feature crosses 0.25. Any feature, not the mean.
             */
            retrain_recommended: boolean;
            /**
             * Score Histogram
             * @description Observed reconstruction errors, binned on the training baseline's edges.
             */
            score_histogram: {
                [key: string]: unknown;
            } | null;
            /** Notes */
            notes: string | null;
            /** Features */
            features: components["schemas"]["DriftFeatureScore"][];
        };
        /**
         * ErrorDistribution
         * @description One of Stage 2's persisted reconstruction-error distributions.
         */
        ErrorDistribution: {
            /**
             * Name
             * @description validation_benign, test_benign or test_attack.
             */
            name: string;
            /** Rows */
            rows: number;
            /**
             * Counts
             * @description One count per bin; len(edges) - 1 of them.
             */
            counts: number[];
            /**
             * Percentiles
             * @description Reference quantiles for the axis labels.
             */
            percentiles: {
                [key: string]: number;
            };
        };
        /**
         * FeedbackLoop
         * @description GET /api/v1/analytics/feedback -- the Feedback Loop screen.
         *
         *     ``labels_pending_retrain`` counts ``analyst_verdicts`` rows whose
         *     ``consumed_at`` is still null. That column exists for exactly this
         *     question, which is why "since the last retrain" is answerable without a
         *     second table.
         *
         *     ``disagreement_rate`` is FP / (TP + FP): the share of judged alerts where
         *     the analyst overruled the model. UNSURE is outside the denominator, the
         *     same way it is in ``ThroughputStats`` -- it is not a judgement that the
         *     model was wrong, and counting it as one would drag the rate up every time
         *     someone was honest about not knowing.
         */
        FeedbackLoop: {
            /**
             * Generated At
             * Format: date-time
             */
            generated_at: string;
            /**
             * Serving Model Version
             * @description The bundle currently loaded and scoring.
             */
            serving_model_version: string;
            /** Total Alerts */
            total_alerts: number;
            /**
             * Judged Alerts
             * @description Distinct alerts carrying at least one verdict.
             */
            judged_alerts: number;
            /**
             * Judged Share
             * @description judged_alerts / total_alerts; 0.0 with no alerts.
             */
            judged_share: number;
            /** Labels Total */
            labels_total: number;
            /**
             * Labels Pending Retrain
             * @description Verdicts with consumed_at IS NULL.
             */
            labels_pending_retrain: number;
            /** Labels Consumed */
            labels_consumed: number;
            /** True Positives */
            true_positives: number;
            /** False Positives */
            false_positives: number;
            /** Unsure */
            unsure: number;
            /**
             * Disagreement Rate
             * @description FP / (TP + FP); null with no decision.
             */
            disagreement_rate: number | null;
            /** Mean Seconds To Verdict */
            mean_seconds_to_verdict: number | null;
            /** By Model Version */
            by_model_version: components["schemas"]["FeedbackVersionRow"][];
            /**
             * Retrain Available
             * @description False until Phase 7 ships the challenger pipeline; the button says so.
             */
            retrain_available: boolean;
            /**
             * Retrain Phase
             * @description The phase that makes retraining callable.
             */
            retrain_phase: string;
        };
        /**
         * FeedbackVersionRow
         * @description Verdicts recorded against one model version.
         */
        FeedbackVersionRow: {
            /** Model Version */
            model_version: string | null;
            /** Verdicts */
            verdicts: number;
            /** True Positives */
            true_positives: number;
            /** False Positives */
            false_positives: number;
            /** Unsure */
            unsure: number;
        };
        /**
         * FlowRecord
         * @description One flow to score -- POST /api/v1/score's request body is a list of these.
         *
         *     An open mapping rather than a fixed-field model: the feature list belongs
         *     to the persisted artifact bundle's ``feature_order``
         *     (training/features.py), not to this code, and is not stable across a
         *     retrain. Values are coerced to float; a string value is refused by
         *     ``FeatureValue`` above rather than silently coerced or zeroed downstream.
         */
        FlowRecord: {
            [key: string]: number;
        };
        /** HTTPValidationError */
        HTTPValidationError: {
            /** Detail */
            detail?: components["schemas"]["ValidationError"][];
        };
        /**
         * HealthResponse
         * @description ``GET /api/v1/health``.
         *
         *     The contract is exactly three fields, per Phase 0. ``model_version`` is
         *     ``"unloaded"`` until Phase 2 writes a real artifact bundle -- the endpoint
         *     reports what is actually loaded rather than a hardcoded string.
         */
        HealthResponse: {
            /**
             * Status
             * @description 'ok' once the process is serving; 'degraded' if a loaded artifact bundle is present but unusable.
             * @enum {string}
             */
            status: "ok" | "degraded";
            /**
             * Model Version
             * @description Version of the currently loaded model bundle, or 'unloaded'.
             * @example unloaded
             * @example rf-20260921-1
             */
            model_version: string;
            /**
             * Uptime S
             * @description Seconds since the FastAPI lifespan started.
             * @example 12.482
             */
            uptime_s: number;
        };
        /**
         * HeartbeatEvent
         * @description Payload of the ``heartbeat`` SSE event.
         *
         *     Sent so a client that has not seen an alert in a while can still tell the
         *     connection is alive rather than stalled.
         */
        HeartbeatEvent: {
            /**
             * Ts
             * Format: date-time
             */
            ts: string;
        };
        /**
         * MitreCoverage
         * @description GET /api/v1/analytics/mitre-coverage.
         *
         *     ``unclassified_anomalies`` sits beside the table rather than inside it:
         *     Stage 2 maps to no technique by design, so it has no row, but omitting the
         *     count would understate exactly the detections this project is proudest of.
         */
        MitreCoverage: {
            /** Techniques */
            techniques: components["schemas"]["MitreCoverageRow"][];
            /** Unclassified Anomalies */
            unclassified_anomalies: number;
        };
        /**
         * MitreCoverageRow
         * @description One technique on the coverage heatmap, with its hit count.
         */
        MitreCoverageRow: {
            /** Family */
            family: string;
            /** Technique Id */
            technique_id: string;
            /** Name */
            name: string;
            /** Url */
            url: string;
            /**
             * Means
             * @description The plain-English line an analyst reads instead of a tab.
             */
            means: string;
            /**
             * Count
             * @description Zero is a real, visible value here -- see the endpoint.
             */
            count: number;
        };
        /**
         * ModelMetrics
         * @description GET /api/v1/metrics/model.
         *
         *     ``accuracy`` is present and is deliberately not the headline: on 99%
         *     benign traffic, always answering benign scores 99%. PR-AUC is the headline
         *     and the Model Performance screen is required to caption the difference.
         */
        ModelMetrics: {
            /** Per Class */
            per_class: {
                [key: string]: components["schemas"]["ClassMetrics"];
            };
            /**
             * Labels
             * @description Row/column order of the confusion matrix.
             */
            labels: string[];
            /** Confusion Matrix */
            confusion_matrix: number[][];
            curves: components["schemas"]["CurvePair"];
            /**
             * Pr Auc
             * @description The headline metric.
             */
            pr_auc: number | null;
            /** Roc Auc */
            roc_auc: number | null;
            /**
             * Accuracy
             * @description Reported, never a headline. See the docstring.
             */
            accuracy: number | null;
            /**
             * Tau Sup
             * @description The Stage 1 operating threshold.
             */
            tau_sup: number | null;
            /**
             * Fpr At Threshold
             * @description Measured FPR at tau_sup.
             */
            fpr_at_threshold: number | null;
            /**
             * Alerts Per Analyst Hour
             * @description Projected volume at tau_sup.
             */
            alerts_per_analyst_hour: number | null;
            /**
             * Budget
             * @description The FP budget the thresholds were cut against.
             */
            budget: {
                [key: string]: unknown;
            };
            /**
             * Stage1 Family Recall
             * @description Per-family recall, Stage 1 alone.
             */
            stage1_family_recall: {
                [key: string]: unknown;
            };
            /**
             * Stage2 Family Recall
             * @description Per-family recall, Stage 2 alone.
             */
            stage2_family_recall: {
                [key: string]: unknown;
            };
            /** Stage2 Pr Auc */
            stage2_pr_auc: number | null;
            /**
             * Loao
             * @description The leave-one-attack-out table: per held-out family, what each stage caught.
             */
            loao: {
                [key: string]: unknown;
            };
        };
        /**
         * ModelRegistry
         * @description GET /api/v1/models. Champion first.
         */
        ModelRegistry: {
            /**
             * Serving
             * @description The version this process has loaded.
             */
            serving: string;
            /** Versions */
            versions: components["schemas"]["RegistryEntryResponse"][];
        };
        /**
         * NotImplementedResponse
         * @description Body returned by route stubs that a later phase fills in.
         *
         *     Explicit and machine-readable, so a caller can tell "not built yet" apart
         *     from "built and broken". No stub returns invented data.
         */
        NotImplementedResponse: {
            /** Detail */
            detail: string;
            /**
             * Phase
             * @description Build phase that implements this endpoint.
             */
            phase: string;
            /** Endpoint */
            endpoint: string;
        };
        /**
         * QueueStats
         * @description GET /api/v1/alerts/stats -- the thin strip above the triage queue.
         *
         *     Measured from the database: this is what *this* deployment has seen, not
         *     what the offline evaluation measured. The projected alert volume and the
         *     operating threshold beside it on the strip come from
         *     ``GET /metrics/threshold`` instead, because a projection at a candidate
         *     threshold is a property of the model, not of the queue.
         *
         *     There is no accuracy field and there is not going to be one. On traffic
         *     that is 99% benign, always answering benign scores 99%, and a strip is
         *     exactly where a number gets read without its caveat.
         */
        QueueStats: {
            /**
             * Generated At
             * Format: date-time
             */
            generated_at: string;
            /**
             * Open Alerts
             * @description Alerts still in 'open'; the size of the queue.
             */
            open_alerts: number;
            /**
             * Alerts Last Hour
             * @description Alerts whose detected_at is inside the last hour.
             */
            alerts_last_hour: number;
            /**
             * Observed Alerts Per Hour
             * @description Alerts divided by the hours actually observed; null before the first alert.
             */
            observed_alerts_per_hour: number | null;
            /**
             * Observed Window Hours
             * @description Hours between the first and last alert, or null when none exist.
             */
            observed_window_hours: number | null;
            /**
             * Hosts Affected
             * @description Distinct destination addresses across open alerts.
             */
            hosts_affected: number;
            /**
             * Sources Seen
             * @description Distinct source addresses across open alerts.
             */
            sources_seen: number;
            /**
             * Unclassified Open
             * @description Open UNCLASSIFIED_ANOMALY alerts -- the detections Stage 1 could not name.
             */
            unclassified_open: number;
            /**
             * Unjudged Open
             * @description Open alerts with no analyst verdict yet.
             */
            unjudged_open: number;
        };
        /**
         * RecommendedActions
         * @description ``recommended_actions`` on AlertDetail.
         *
         *     The shape app/remediation.py's ``advice_for`` returns. ``technique: None``
         *     together with ``has_playbook: False`` is the honest unclassified-anomaly
         *     panel -- there is no technique and no playbook, and that combination has
         *     to be expressible rather than optioned away.
         */
        RecommendedActions: {
            technique: components["schemas"]["Technique"] | null;
            /** Has Playbook */
            has_playbook: boolean;
            /** Summary */
            summary: string;
            /** Actions */
            actions: string[];
        };
        /**
         * RegistryEntryResponse
         * @description One model version, with the decisions it is responsible for.
         *
         *     ``alerts_scored`` is the audit number: it is what somebody needs after an
         *     incident, when the question is how many decisions a model that turned out to
         *     be wrong was behind.
         */
        RegistryEntryResponse: {
            /** Version */
            version: string;
            /**
             * Stage
             * @enum {string}
             */
            stage: "champion" | "challenger" | "archived";
            /** Is Active */
            is_active: boolean;
            /** Supervised Algorithm */
            supervised_algorithm: string | null;
            /** Anomaly Algorithm */
            anomaly_algorithm: string | null;
            /** Trained At */
            trained_at: string | null;
            /** Trained On */
            trained_on: string | null;
            /** Schema Hash */
            schema_hash: string | null;
            /** Tau Sup */
            tau_sup: number | null;
            /** Tau Anom */
            tau_anom: number | null;
            /** Metrics */
            metrics: {
                [key: string]: unknown;
            };
            /** Notes */
            notes: string | null;
            /** Alerts Scored */
            alerts_scored: number;
            /** Verdicts Recorded */
            verdicts_recorded: number;
            /** First Alert At */
            first_alert_at: string | null;
            /** Last Alert At */
            last_alert_at: string | null;
        };
        /**
         * ReplayStartRequest
         * @description POST /api/v1/replay/start's request body.
         *
         *     ``speed`` is a ``Literal`` of exactly the documented allowed values, so an
         *     unlisted speed is a 422 from the validation layer rather than something
         *     the replay engine has to police.
         */
        ReplayStartRequest: {
            /**
             * Speed
             * @description Time acceleration factor.
             * @enum {integer}
             */
            speed: 1 | 10 | 100;
            /**
             * Dataset
             * @description Held-out split name to replay.
             */
            dataset: string;
        };
        /**
         * ReplayStatus
         * @description What POST /api/v1/replay/start and /replay/stop return on 202.
         */
        ReplayStatus: {
            /** Running */
            running: boolean;
            /**
             * Speed
             * @description Null when no replay is running.
             */
            speed: number | null;
            /**
             * Dataset
             * @description Null when no replay is running.
             */
            dataset: string | null;
            /**
             * Started At
             * @description Null when no replay is running.
             */
            started_at: string | null;
            /** Rows Scored */
            rows_scored: number;
            /** Alerts Emitted */
            alerts_emitted: number;
        };
        /**
         * RetrainRequest
         * @description POST /api/v1/retrain's body.
         */
        RetrainRequest: {
            /**
             * Requested By
             * @description Who asked.
             */
            requested_by?: string | null;
        };
        /**
         * RetrainRunResponse
         * @description One retraining run, requested or finished.
         *
         *     A run that was not promoted is the more interesting row of the two: it is the
         *     evidence that the gate works. ``champion_pr_auc`` and ``challenger_pr_auc``
         *     are both measured on ``held_out_split`` inside the same run, so the comparison
         *     is between two models rather than between two evaluations.
         */
        RetrainRunResponse: {
            /** Id */
            id: number;
            /**
             * Status
             * @enum {string}
             */
            status: "requested" | "running" | "completed" | "failed" | "cancelled";
            /** Requested By */
            requested_by: string | null;
            /**
             * Requested At
             * Format: date-time
             */
            requested_at: string;
            /** Started At */
            started_at: string | null;
            /** Finished At */
            finished_at: string | null;
            /** Labels Consumed */
            labels_consumed: number;
            /** False Positives Consumed */
            false_positives_consumed: number;
            /** True Positives Consumed */
            true_positives_consumed: number;
            /** Champion Version */
            champion_version: string | null;
            /** Challenger Version */
            challenger_version: string | null;
            /** Champion Pr Auc */
            champion_pr_auc: number | null;
            /** Challenger Pr Auc */
            challenger_pr_auc: number | null;
            /** Held Out Split */
            held_out_split: string | null;
            /** Promoted */
            promoted: boolean;
            /** Decision */
            decision: string | null;
            /** Error */
            error: string | null;
        };
        /**
         * RetrainStatusResponse
         * @description GET /api/v1/retrain. What has been asked for and what came of it.
         */
        RetrainStatusResponse: {
            /** Runs */
            runs: components["schemas"]["RetrainRunResponse"][];
            /**
             * Pending
             * @description Requests no worker has claimed yet.
             */
            pending: number;
            /**
             * Worker Hint
             * @description How a pending request gets executed. The API never fits a model.
             */
            worker_hint: string;
        };
        /**
         * ScoreResponse
         * @description POST /api/v1/score's response.
         *
         *     ``scored`` and ``alerts`` are surfaced rather than left for the caller to
         *     recompute from ``results``: both are already known from the records
         *     ``ModelBundle.score_batch`` returns (the same information
         *     ``FusionDecisions.counts()`` carries), so re-deriving them client-side
         *     would just be a second place to get the denominator wrong.
         */
        ScoreResponse: {
            /** Results */
            results: components["schemas"]["ScoredFlow"][];
            /**
             * Scored
             * @description Number of flow records scored; len(results).
             */
            scored: number;
            /**
             * Alerts
             * @description Number of results whose kind is not null.
             */
            alerts: number;
        };
        /**
         * ScoredFlow
         * @description One row of POST /api/v1/score's response, in input order.
         *
         *     ``detection_stage`` is nullable even though the "Planned" table in
         *     docs/API-Reference.md does not mark it so -- a documentation gap, not a
         *     design choice. ``training/fusion.py`` sets ``kind``, ``family`` and
         *     ``stage`` all to ``None`` for a row that raised no alert, and the
         *     response is one result per input row *including* those rows, which are
         *     the denominator of every rate on the dashboard.
         */
        ScoredFlow: {
            /**
             * Kind
             * @description Fusion outcome; null for a row that raised no alert.
             */
            kind: ("KNOWN" | "UNCLASSIFIED_ANOMALY") | null;
            /**
             * Family
             * @description Stage 1 label; always null for UNCLASSIFIED_ANOMALY.
             */
            family: ("dos" | "ddos" | "brute_force" | "port_scan" | "web_attack" | "botnet" | "infiltration") | null;
            /**
             * Confidence
             * @description Stage 1 max attack-class probability.
             */
            confidence: number | null;
            /**
             * Anomaly Score
             * @description Stage 2 mean reconstruction error.
             */
            anomaly_score: number | null;
            /**
             * Detection Stage
             * @description Which stage fired; null if neither did.
             */
            detection_stage: ("stage1_supervised" | "stage2_anomaly") | null;
            /**
             * Model Version
             * @description The bundle version that produced this score.
             */
            model_version: string;
        };
        /**
         * Technique
         * @description A MITRE ATT&CK technique, as app/mitre.py's static lookup carries it.
         */
        Technique: {
            /** Technique Id */
            technique_id: string;
            /** Name */
            name: string;
            /** Url */
            url: string;
            /**
             * Means
             * @description One plain-English line true of this traffic.
             */
            means: string;
        };
        /**
         * ThresholdProjection
         * @description GET /api/v1/metrics/threshold.
         *
         *     ``t`` is Stage 2's anomaly threshold, not a Stage 1 probability -- see the
         *     endpoint docstring for why a Stage 1 answer is not computable from what is
         *     persisted.
         */
        ThresholdProjection: {
            /** T */
            t: number;
            /**
             * Fpr
             * @description Benign rows at or above t, over all benign rows.
             */
            fpr: number;
            /**
             * Recall
             * @description Attack rows at or above t; null without that split.
             */
            recall: number | null;
            /** Benign Rows */
            benign_rows: number;
            /** Benign Above */
            benign_above: number;
            /** Attack Rows */
            attack_rows: number | null;
            /** Attack Above */
            attack_above: number | null;
            /**
             * False Alerts Per Day
             * @description fpr x IDS_EXPECTED_DAILY_FLOW_VOLUME.
             */
            false_alerts_per_day: number;
            /**
             * Alerts Per Analyst Hour
             * @description Per analyst hour: divided by shift length.
             */
            alerts_per_analyst_hour: number;
            /** Budget Per Day */
            budget_per_day: number;
            /** Target Fpr */
            target_fpr: number;
            /** Within Budget */
            within_budget: boolean;
            /**
             * Covers Distribution
             * @description False when t sits above the histogram's range, so recall is not meaningful.
             */
            covers_distribution: boolean;
        };
        /**
         * ThroughputStats
         * @description SOC throughput: the panel that argues for the project's existence.
         */
        ThroughputStats: {
            /** Opened */
            opened: number;
            /** Resolved */
            resolved: number;
            /** Verdicts */
            verdicts: number;
            /** True Positives */
            true_positives: number;
            /** False Positives */
            false_positives: number;
            /** Unsure */
            unsure: number;
            /**
             * True Positive Rate
             * @description TP / (TP + FP). UNSURE is excluded from the denominator, not counted as wrong.
             */
            true_positive_rate: number | null;
            /** Mean Seconds To Verdict */
            mean_seconds_to_verdict: number | null;
        };
        /**
         * TimeBucket
         * @description One point of the alerts-over-time series.
         *
         *     Split into known versus unclassified rather than a single total, because
         *     the unclassified line is the novel-detection headline and folding it into
         *     a total would hide the claim this project is making.
         */
        TimeBucket: {
            /**
             * Bucket
             * Format: date-time
             */
            bucket: string;
            /** Known */
            known: number;
            /** Unclassified */
            unclassified: number;
        };
        /** ValidationError */
        ValidationError: {
            /** Location */
            loc: (string | number)[];
            /** Message */
            msg: string;
            /** Error Type */
            type: string;
            /** Input */
            input?: unknown;
            /** Context */
            ctx?: Record<string, never>;
        };
        /**
         * VerdictRequest
         * @description POST /api/v1/alerts/{alert_id}/verdict's request body.
         */
        VerdictRequest: {
            /**
             * Verdict
             * @enum {string}
             */
            verdict: "TP" | "FP" | "UNSURE";
            /**
             * Note
             * @description Free text.
             */
            note?: string | null;
            /**
             * Analyst
             * @description Who judged it.
             */
            analyst?: string | null;
        };
        /**
         * VerdictResponse
         * @description POST /api/v1/alerts/{alert_id}/verdict's 201 response: the created row.
         *
         *     ``model_version`` is captured at verdict time -- fixed to whatever
         *     produced the alert being judged -- so the label stays auditable against
         *     that model even after a later retrain changes what is currently serving.
         */
        VerdictResponse: {
            /** Id */
            id: number;
            /** Alert Id */
            alert_id: number;
            /**
             * Verdict
             * @enum {string}
             */
            verdict: "TP" | "FP" | "UNSURE";
            /** Note */
            note: string | null;
            /** Analyst */
            analyst: string | null;
            /** Model Version */
            model_version: string | null;
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
        };
    };
    responses: never;
    parameters: never;
    requestBodies: never;
    headers: never;
    pathItems: never;
}
export type $defs = Record<string, never>;
export interface operations {
    health_api_v1_health_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HealthResponse"];
                };
            };
        };
    };
    list_alerts_api_v1_alerts_get: {
        parameters: {
            query?: {
                /** @description Severity filter. */
                severity?: ("low" | "medium" | "high" | "critical") | null;
                /** @description Backs the one-click UNCLASSIFIED_ANOMALY filter chip. */
                kind?: ("KNOWN" | "UNCLASSIFIED_ANOMALY") | null;
                /** @description Attack family filter. */
                family?: ("dos" | "ddos" | "brute_force" | "port_scan" | "web_attack" | "botnet" | "infiltration") | null;
                /** @description Triage state filter. */
                status?: ("open" | "in_review" | "closed" | "dismissed") | null;
                /** @description Latest analyst verdict, or 'none' for alerts nobody has judged yet. */
                verdict?: ("TP" | "FP" | "UNSURE" | "none") | null;
                /** @description ISO 8601 lower bound. */
                since?: string | null;
                /** @description ISO 8601 upper bound. */
                until?: string | null;
                /** @description Opaque cursor from a previous page. */
                cursor?: string | null;
                /** @description Page size. */
                limit?: number;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["AlertPage"];
                };
            };
            /** @description An unrecognised filter value or a malformed cursor. */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
        };
    };
    queue_stats_api_v1_alerts_stats_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["QueueStats"];
                };
            };
        };
    };
    update_alert_status_api_v1_alerts_status_patch: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["AlertStatusUpdate"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["AlertStatusResult"];
                };
            };
            /** @description An empty id list, more than 500 ids, or an unknown status. */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
        };
    };
    get_alert_api_v1_alerts__alert_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                alert_id: number;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["AlertDetail"];
                };
            };
            /** @description No alert with that id. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description A non-integer id. */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
        };
    };
    submit_verdict_api_v1_alerts__alert_id__verdict_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                alert_id: number;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["VerdictRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["VerdictResponse"];
                };
            };
            /** @description No alert with that id. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description An invalid verdict value or a non-integer id. */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
        };
    };
    related_alerts_api_v1_alerts__alert_id__related_get: {
        parameters: {
            query?: {
                /** @description Lookback window. */
                window_hours?: number;
            };
            header?: never;
            path: {
                alert_id: number;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["AlertSummary"][];
                };
            };
            /** @description No alert with that id. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    score_flows_api_v1_score_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["FlowRecord"][];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ScoreResponse"];
                };
            };
            /** @description A flow record is malformed (unrecognised or non-numeric features). */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description No model bundle is loaded. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
        };
    };
    model_metrics_api_v1_metrics_model_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ModelMetrics"];
                };
            };
            /** @description No evaluation artifacts are loaded. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
        };
    };
    threshold_what_if_api_v1_metrics_threshold_get: {
        parameters: {
            query: {
                /** @description Candidate threshold */
                t: number;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ThresholdProjection"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
            /** @description No Stage 2 error distributions are loaded. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
        };
    };
    anomaly_histogram_api_v1_metrics_anomaly_histogram_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["AnomalyHistogram"];
                };
            };
            /** @description No Stage 2 error distributions are loaded. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
        };
    };
    drift_metrics_api_v1_metrics_drift_get: {
        parameters: {
            query?: {
                /** @description How many runs the series covers. */
                snapshots?: number;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["DriftResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_models_api_v1_models_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ModelRegistry"];
                };
            };
        };
    };
    retrain_status_api_v1_retrain_get: {
        parameters: {
            query?: {
                limit?: number;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RetrainStatusResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    request_retrain_api_v1_retrain_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["RetrainRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RetrainRunResponse"];
                };
            };
            /** @description A run is already requested or running. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description No unconsumed analyst labels to retrain from. */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
        };
    };
    analytics_summary_api_v1_analytics_summary_get: {
        parameters: {
            query?: {
                /** @description Time range */
                range?: string;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["AnalyticsSummary"];
                };
            };
            /** @description An unrecognised range. */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
        };
    };
    feedback_loop_api_v1_analytics_feedback_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["FeedbackLoop"];
                };
            };
        };
    };
    mitre_coverage_api_v1_analytics_mitre_coverage_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["MitreCoverage"];
                };
            };
        };
    };
    replay_status_api_v1_replay_status_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ReplayStatus"];
                };
            };
        };
    };
    replay_start_api_v1_replay_start_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ReplayStartRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ReplayStatus"];
                };
            };
            /** @description A replay is already running. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Unknown dataset, or a speed outside 1 / 10 / 100. */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description No model bundle is loaded, so there is nothing to score with. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
        };
    };
    replay_stop_api_v1_replay_stop_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ReplayStatus"];
                };
            };
            /** @description No replay is running. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
        };
    };
    ingest_start_api_v1_ingest_start_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description Not Implemented */
            501: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["NotImplementedResponse"];
                };
            };
        };
    };
    stream_alerts_api_v1_stream_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description An open `text/event-stream` of `alert` and `heartbeat` events. */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "text/event-stream": string | components["schemas"]["AlertEvent"] | components["schemas"]["HeartbeatEvent"];
                };
            };
            /** @description No traffic source is running, so there is nothing to stream. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
        };
    };
}
