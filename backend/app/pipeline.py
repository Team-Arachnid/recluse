"""Phase 5 -- the alert pipeline: turns one scored batch into persisted,
deduplicated, explained, pushed alerts.

This is the centre of Phase 5. `app.explain`, `app.remediation`, `app.mitre`,
`app.topology`, `app.risk`, `app.schemas` and `app.dedupe` are each a lookup
table, a pure function, or a wire contract; everything after this module is
an endpoint reading what it writes.

Six stages, in the order PART 8 of the project brief lists them, with one
swap:

    1. Explain              -- TreeSHAP top-5 (Stage 1) or top-5
                                reconstruction-error features (Stage 2).
                                `app.explain`.
    2. Narrate               -- template the explanation into one English
                                sentence. `app.explain.narrate`.
    3. MITRE map and recommend -- a technique id, what it means, and what to
                                do about it. `app.remediation.advice_for`.
    4. Enrich                -- asset criticality and the host's prior alert
                                count. `app.topology`, queried once per
                                distinct source address in the batch.
    5. Dedupe                -- key on (src_host, alert_class,
                                floor(ts, 5min)); a hit increments
                                `occurrence_count` and keeps the higher
                                `risk_score` instead of inserting a second
                                row. `app.dedupe.upsert_alert`.
    6. Persist and push      -- one commit per batch (the caller's), one
                                `AlertEvent` per alert pushed to the broker,
                                after persistence.

**Enrich before Dedupe is a controller ruling, not the brief's own order.**
The brief lists Dedupe fourth and Enrich fifth. This module runs them the
other way around because `app.risk.risk_score` takes asset criticality and
the host's prior alert count as two of its four inputs, and the dedupe
upsert needs the *final* `risk_score` so that a repeat hit in a burst keeps
the highest-risk flow's score rather than whichever flow happened to arrive
last (see `app.dedupe.upsert_alert`'s max-of-both rule). Deduping first would
leave two bad choices: persist a `risk_score` the enrichment step is about
to change, or reopen the row a second time to patch it in. Running Enrich
first means the candidate handed to the dedupe upsert already carries the
number that upsert needs to compare.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.dedupe import dedupe_key, upsert_alert
from app.events import EventBroker
from app.explain import explain_anomaly, explain_supervised, narrate
from app.inference import ModelBundle
from app.models import Alert
from app.remediation import advice_for
from app.risk import risk_score, severity
from app.schemas import AlertEvent
from app.topology import addresses_for, criticality_for, provenance
from training.features import PORT_COLUMN, build_feature_matrix


def ingest_batch(
    session: Session,
    *,
    flows: list[dict[str, Any]],
    decisions: list[dict[str, Any]],
    bundle: ModelBundle,
    detected_at: datetime,
    source: str = "replay",
    ground_truth: list[str | None] | None = None,
    broker: EventBroker | None = None,
    start_index: int = 0,
) -> list[Alert]:
    """Process one scored batch: explain, narrate, map, enrich, dedupe, persist, push.

    `flows` and `decisions` are parallel lists -- the batch exactly as it was
    handed to, and returned from, `bundle.score_batch`. A row whose
    `decisions[i]["kind"]` is `None` raised no alert and is skipped; this
    function returns one `Alert` per *alerting* row, in input order. A row
    that collapses into an already-seen dedupe bucket contributes the same
    `Alert` object a second time rather than being dropped from the return
    value, mirroring `score_batch`'s own "one record per input row"
    convention. The caller already has `len(flows)` as the scored-but-not-
    necessarily-alerted denominator; this function does not re-derive it.

    `start_index` is this batch's row offset within the overall replay run.
    It feeds `app.topology.addresses_for(family, start_index + i)` so a
    family's derived addresses spread across its documented host set across
    the whole run instead of resetting every batch.

    `detected_at` is the replay wall-clock at which this batch was scored,
    and must be tz-aware. `app.models` is explicit that SQLite stores
    timestamps naively and the application layer is responsible for always
    handing over tz-aware values, so a naive `detected_at` is rejected here
    outright rather than silently stored and silently four-hours-wrong at
    read time.

    `ground_truth[i]`, if given, populates `alerts.ground_truth_label` for
    alerting row `i`. It exists only because this is a replay of a labelled
    dataset -- live capture always passes `None` (the default) -- and this
    function never derives a label from anything the model produced.

    **Explanation is batched, never per row.** The alerting rows are masked
    into a Stage 1 group and a Stage 2 group, a feature matrix is built once
    per *non-empty* group with `training.features.build_feature_matrix` (the
    only legal way to build one -- never a second, hand-rolled transform),
    and `explain_supervised`/`explain_anomaly` are each called at most once.
    A group that is empty is not just handed a zero-row matrix: it is
    skipped before either the matrix or the explainer call, so a batch with
    no Stage 1 alerts never pays shap's `TreeExplainer` setup cost.

    **The two port key namespaces must not be mixed.** `narrate(...,
    flow=...)` is given the *raw* flow dict -- `flows[i]`, keyed by the
    bundle's feature names, where the destination port lives under
    `destination_port` (`training.features.PORT_COLUMN`), because that is
    the key `app.explain._port_clause` reads. The `alerts.dst_port` column
    and `app.topology.provenance()` both spell the same fact `dst_port`.
    Handing `narrate` a dict keyed that way instead would make its
    observed-port clause silently never fire -- every sentence would simply
    lose its port half, with no error -- so this function always passes
    `flows[i]` itself to `narrate`, never the `dst_port`-keyed value it
    separately computes for the alert row's own column.

    `mitre_technique` stores `advice_for(family)["technique"]["technique_id"]`,
    or `None` for an unclassified anomaly (which maps to no technique at
    all); `recommended_actions` stores the whole `advice_for` payload,
    including the populated no-playbook entry for that same case. That
    asymmetry is deliberate and `app.remediation` explains it; this function
    does not second-guess it.

    `host_prior_alert_count` is queried once per distinct source address in
    the batch, and entirely before this batch inserts anything, so two
    alerts from one host in the same batch do not see each other and the
    count does not depend on batch size or row order.

    Every alert -- new or a repeat hit -- is published to `broker` (if one is
    given) as an `AlertEvent`-shaped dict immediately after its own upsert:
    after persistence, never before, per docs/API-Reference.md ("alerts
    reach the stream after dedupe, not before"). An event carrying an id the
    database does not yet have is an event the client cannot fetch detail
    for; `app.dedupe.upsert_alert` flushes immediately on an insert for
    exactly this reason, so every `alert.id` read here is already real,
    whether the row is brand new or a repeat hit. A repeat hit publishes
    too, carrying its risen `occurrence_count` -- that is how the ticker
    shows a burst collapsing into one row instead of flooding.

    This function flushes nothing of its own beyond what `upsert_alert`
    already guarantees, and it never commits: the caller owns the session
    (`app.db.session_scope()` in production, the `db_session` fixture in
    tests) and commits once for the whole batch, which is what makes a
    5,000-flow burst one transaction instead of 5,000.
    """
    if detected_at.utcoffset() is None:
        raise ValueError(
            "ingest_batch() needs a tz-aware detected_at; got a naive "
            f"datetime ({detected_at!r}). app.models documents that SQLite "
            "stores timestamps naively and the application layer is "
            "responsible for always handing over tz-aware values -- a naive "
            "value here would be persisted as whatever local-time-shaped "
            "number happened to be in it, silently off by the local UTC "
            "offset for every row this batch writes."
        )
    if len(flows) != len(decisions):
        raise ValueError(
            f"{len(flows)} flow(s) against {len(decisions)} decision(s); "
            "flows and decisions must be the parallel lists bundle.score_batch "
            "produced from this same batch."
        )
    if ground_truth is not None and len(ground_truth) != len(flows):
        raise ValueError(
            f"{len(ground_truth)} ground_truth label(s) against {len(flows)} "
            "flow(s); ground_truth, when given, must cover the whole batch."
        )

    alert_indices = [i for i, decision in enumerate(decisions) if decision["kind"] is not None]
    if not alert_indices:
        return []

    stage1_indices = [i for i in alert_indices if decisions[i]["kind"] == "KNOWN"]
    stage2_indices = [i for i in alert_indices if decisions[i]["kind"] == "UNCLASSIFIED_ANOMALY"]

    explanations: dict[int, dict[str, Any]] = {}
    if stage1_indices:
        frame = pd.DataFrame([flows[i] for i in stage1_indices])
        matrix = build_feature_matrix(frame, bundle.preprocessing).to_numpy(dtype="float32")
        families = [decisions[i]["family"] for i in stage1_indices]
        records = explain_supervised(
            bundle.supervised, matrix, bundle.feature_order, bundle.supervised_classes, families
        )
        explanations.update(zip(stage1_indices, records, strict=True))

    if stage2_indices:
        frame = pd.DataFrame([flows[i] for i in stage2_indices])
        matrix = build_feature_matrix(frame, bundle.preprocessing).to_numpy(dtype="float32")
        records = explain_anomaly(bundle.autoencoder, matrix, bundle.feature_order)
        explanations.update(zip(stage2_indices, records, strict=True))

    # Addresses first: the dedupe key's source host and the host-prior-count
    # query below both need them. `addresses_for` is pure and cheap enough
    # that computing it once per alerting row needs no further caching.
    addresses = {i: addresses_for(decisions[i]["family"], start_index + i) for i in alert_indices}

    # Host prior alert counts: once per distinct source address, and entirely
    # before this batch inserts anything -- see the docstring.
    distinct_sources = {addresses[i][0] for i in alert_indices}
    prior_counts = {
        src_ip: session.execute(
            select(func.count()).select_from(Alert).where(Alert.src_ip == src_ip)
        ).scalar_one()
        for src_ip in distinct_sources
    }

    alerts: list[Alert] = []
    for i in alert_indices:
        decision = decisions[i]
        flow = flows[i]
        kind = decision["kind"]
        family = decision["family"]
        src_ip, dst_ip = addresses[i]
        asset_criticality = criticality_for(dst_ip)
        host_prior_alert_count = prior_counts[src_ip]

        explanation = explanations[i]
        # `flow`, not any renamed copy of it -- see the docstring's port
        # namespace warning.
        narrative = narrate(kind, family, explanation["contributors"], flow=flow)
        advice = advice_for(family)
        technique = advice["technique"]

        risk = risk_score(
            kind=kind,
            confidence=decision["confidence"],
            anomaly_score=decision["anomaly_score"],
            tau_sup=bundle.tau_sup,
            tau_anom=bundle.tau_anom,
            histogram=bundle.benign_error_histogram,
            asset_criticality=asset_criticality,
            host_prior_alert_count=host_prior_alert_count,
        )

        raw_port = flow.get(PORT_COLUMN)
        alert_class = family if family is not None else kind
        candidate = {
            "kind": kind,
            "family": family,
            "detection_stage": decision["detection_stage"],
            "severity": severity(risk),
            "risk_score": risk,
            "confidence": decision["confidence"],
            "anomaly_score": decision["anomaly_score"],
            "detected_at": detected_at,
            "src_ip": src_ip,
            "src_port": None,  # absent from the release -- see provenance()
            "dst_ip": dst_ip,
            "dst_port": None if raw_port is None else int(raw_port),
            "protocol": None,  # absent from the release -- see provenance()
            "asset_criticality": asset_criticality,
            "host_prior_alert_count": host_prior_alert_count,
            "mitre_technique": None if technique is None else technique["technique_id"],
            "explanation": explanation,
            "narrative": narrative,
            "recommended_actions": advice,
            "raw_flow": {**flow, "_provenance": provenance()},
            "dedupe_key": dedupe_key(src_ip, alert_class, detected_at),
            "status": "open",
            "model_version": decision["model_version"],
            "source": source,
            "ground_truth_label": None if ground_truth is None else ground_truth[i],
        }

        alert, _created = upsert_alert(session, candidate)
        alerts.append(alert)

        if broker is not None:
            event = AlertEvent(
                id=alert.id,
                kind=alert.kind,
                family=alert.family,
                severity=alert.severity,
                risk_score=alert.risk_score,
                anomaly_score=alert.anomaly_score,
                detected_at=alert.detected_at,
                src_ip=alert.src_ip,
                dst_ip=alert.dst_ip,
                occurrence_count=alert.occurrence_count,
                model_version=alert.model_version,
            )
            # `mode="json"` rather than a bare dump: the broker's payload goes
            # straight into an SSE `data:` line, so it has to be JSON-ready at
            # publish time. A `datetime` left as an object would survive every
            # test that reads ids and counts off an event and then fail in the
            # stream route, on the one field (`detected_at`) the documented
            # event shape renders as an ISO-8601 string.
            broker.publish(event.model_dump(mode="json"))

    return alerts
