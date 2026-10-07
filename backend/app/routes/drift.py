"""Phase 7 -- drift snapshots, the model registry, and retrain requests.

Three endpoints, and one thing none of them does: fit a model. ``POST /retrain``
writes a request row and returns 202; ``python -m training.retrain`` picks it up.
Fitting inside a request handler is the anti-pattern the brief names outright, and
an admin request is still a request -- it would put a multi-minute, multi-gigabyte
job on the event loop that is also serving the alert stream.
"""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_session
from app.drift import PSI_MODERATE, PSI_SIGNIFICANT
from app.metrics_store import MetricsStore
from app.models import DriftFeature, DriftRun, FlowSample, RetrainRun
from app.registry import read_registry
from app.schemas import (
    DriftFeatureScore,
    DriftResponse,
    DriftSeries,
    DriftSeriesPoint,
    DriftSnapshot,
    ModelRegistry,
    RegistryEntryResponse,
    RetrainRequest,
    RetrainRunResponse,
    RetrainStatusResponse,
    Stage2RefitSummary,
)

router = APIRouter(tags=["drift"])

# How many snapshots the per-feature series reaches back over. A drift screen is
# read for a trend, and a trend needs more than two points and fewer than all of
# history -- thirty nightly runs is a month.
SERIES_SNAPSHOTS = 30

# Which training distribution the observed scores are overlaid on. The validation
# day's, because that is the one `tau_anom` was cut from.
BASELINE_DISTRIBUTION = "validation_benign"

WORKER_HINT = (
    "A requested run is executed by `python -m training.retrain` (or `make retrain`). "
    "The API never fits a model: a multi-minute fit inside a request handler would "
    "run on the same event loop that serves the alert stream."
)


def _snapshot(run: DriftRun, features: list[DriftFeature]) -> DriftSnapshot:
    return DriftSnapshot(
        id=run.id,
        computed_at=run.computed_at,
        model_version=run.model_version,
        observed_from=run.observed_from,
        observed_to=run.observed_to,
        rows_observed=run.rows_observed,
        reference=run.reference,
        reference_rows=run.reference_rows,
        features_scored=run.features_scored,
        max_psi=run.max_psi,
        moderate_count=run.moderate_count,
        significant_count=run.significant_count,
        retrain_recommended=run.retrain_recommended,
        score_histogram=run.score_histogram,
        notes=run.notes,
        features=[
            DriftFeatureScore(
                feature=entry.feature,
                psi=entry.psi,
                band=entry.band,
                expected=entry.expected,
                actual=entry.actual,
            )
            # Worst first: a screen with ninety-two rows is read from the top, and
            # the top should be the features that moved.
            for entry in sorted(features, key=lambda item: -item.psi)
        ],
    )


@router.get(
    "/metrics/drift",
    response_model=DriftResponse,
    summary="PSI per feature over time, with the baseline overlay",
)
def drift_metrics(
    request: Request,
    session: Session = Depends(get_session),
    snapshots: int = Query(
        default=SERIES_SNAPSHOTS, ge=1, le=365, description="How many runs the series covers."
    ),
) -> DriftResponse:
    """Serve the stored drift snapshots.

    Computed by ``python -m training.drift_job``, never here. A PSI over
    ninety-two features is a full scan of the sample table, and a request handler
    is the wrong place for one; more to the point, drift is a property of a window
    of time rather than of a request, so computing it per request would give two
    readers two different answers minutes apart.

    An empty response is a real state and says so -- ``latest`` is null until the
    first run. A drift monitor that invented a flat line at zero would tell its
    reader that everything is fine, which is the one lie this screen must not
    tell.
    """
    total = int(session.execute(select(func.count()).select_from(DriftRun)).scalar_one())

    recent = list(
        session.execute(
            select(DriftRun)
            .order_by(DriftRun.computed_at.desc(), DriftRun.id.desc())
            .limit(snapshots)
        )
        .scalars()
        .all()
    )

    latest: DriftSnapshot | None = None
    series: list[DriftSeries] = []

    if recent:
        run_ids = [run.id for run in recent]
        rows = list(
            session.execute(select(DriftFeature).where(DriftFeature.run_id.in_(run_ids)))
            .scalars()
            .all()
        )

        by_run: dict[int, list[DriftFeature]] = {}
        for row in rows:
            by_run.setdefault(row.run_id, []).append(row)

        latest = _snapshot(recent[0], by_run.get(recent[0].id, []))

        # Oldest first inside each series, so a chart reads left to right.
        ordered = sorted(recent, key=lambda run: (run.computed_at, run.id))
        by_feature: dict[str, list[DriftSeriesPoint]] = {}
        for run in ordered:
            for row in by_run.get(run.id, []):
                by_feature.setdefault(row.feature, []).append(
                    DriftSeriesPoint(computed_at=run.computed_at, psi=row.psi, band=row.band)
                )

        for feature, points in by_feature.items():
            series.append(
                DriftSeries(
                    feature=feature,
                    worst_psi=max(point.psi for point in points),
                    latest_psi=points[-1].psi,
                    latest_band=points[-1].band,
                    points=points,
                )
            )
        series.sort(key=lambda entry: -entry.worst_psi)

    store: MetricsStore = request.app.state.metrics
    baseline = store.error_histogram(BASELINE_DISTRIBUTION)

    sampled = int(session.execute(select(func.count()).select_from(FlowSample)).scalar_one())

    return DriftResponse(
        latest=latest,
        series=series,
        snapshots=total,
        baseline=baseline,
        moderate_threshold=PSI_MODERATE,
        significant_threshold=PSI_SIGNIFICANT,
        sampled_rows=sampled,
        sample_stride=settings.drift_sample_stride,
    )


@router.get(
    "/models",
    response_model=ModelRegistry,
    summary="Model registry: versions, thresholds, and the alerts each one scored",
)
def list_models(request: Request, session: Session = Depends(get_session)) -> ModelRegistry:
    """The registry, and with it the audit trail made readable.

    "Which model version scored which alert" has been answerable since Phase 5 --
    ``alerts.model_version`` is written on every row. What was missing was a place
    to ask, and a column nobody can query is not an audit trail. The
    ``alerts_scored`` figure here is the number somebody needs after an incident,
    when the question is how many decisions a model that turned out to be wrong
    was behind.
    """
    return ModelRegistry(
        serving=request.app.state.bundle.version,
        versions=[RegistryEntryResponse(**vars(entry)) for entry in read_registry(session)],
    )


def _stage2_summary(record: RetrainRun) -> Stage2RefitSummary | None:
    """Read Stage 2's half of a run back out of its logged comparison."""
    stage2 = (record.comparison or {}).get("stage2") or {}
    if not stage2:
        return None
    pool = stage2.get("pool") or {}
    champion = stage2.get("champion") or {}
    challenger = stage2.get("challenger") or {}
    return Stage2RefitSummary(
        attempted=bool(stage2.get("attempted")),
        promoted=bool(stage2.get("promoted")),
        decision=str(stage2.get("decision") or ""),
        pool_admitted=int(pool.get("admitted") or 0),
        pool_candidates=int(pool.get("candidates") or 0),
        pool_hosts=int(pool.get("hosts") or 0),
        pool_refused_by_cap=int(pool.get("refused_by_cap") or 0),
        champion_version=stage2.get("champion_version"),
        challenger_version=stage2.get("challenger_version"),
        champion_pr_auc=champion.get("pr_auc"),
        challenger_pr_auc=challenger.get("pr_auc"),
        champion_pool_fpr=champion.get("pool_holdout_fpr"),
        challenger_pool_fpr=challenger.get("pool_holdout_fpr"),
        champion_attack_recall=champion.get("attack_recall"),
        challenger_attack_recall=challenger.get("attack_recall"),
    )


def _retrain_response(record: RetrainRun) -> RetrainRunResponse:
    return RetrainRunResponse(
        id=record.id,
        status=record.status,
        requested_by=record.requested_by,
        requested_at=record.requested_at,
        started_at=record.started_at,
        finished_at=record.finished_at,
        labels_consumed=record.labels_consumed,
        false_positives_consumed=record.false_positives_consumed,
        true_positives_consumed=record.true_positives_consumed,
        champion_version=record.champion_version,
        challenger_version=record.challenger_version,
        champion_pr_auc=record.champion_pr_auc,
        challenger_pr_auc=record.challenger_pr_auc,
        held_out_split=record.held_out_split,
        promoted=record.promoted,
        decision=record.decision,
        error=record.error,
        stage2=_stage2_summary(record),
    )


@router.post(
    "/retrain",
    response_model=RetrainRunResponse,
    status_code=202,
    summary="Request a challenger run from the analyst labels recorded so far",
    responses={
        409: {"description": "A run is already requested or running."},
        422: {"description": "No unconsumed analyst labels to retrain from."},
    },
)
def request_retrain(
    body: RetrainRequest,
    session: Session = Depends(get_session),
) -> RetrainRunResponse:
    """Record a retrain request. **202, and nothing is fitted here.**

    The response says the request has been accepted, not that a model has been
    trained -- a 200 would claim a completed job, and this one takes minutes. The
    work is done by ``python -m training.retrain``, which claims the oldest
    pending row.

    Refuses when a run is already queued or running, because two concurrent
    retrains would both consume the same labels and race to publish a champion.
    Refuses when there are no unconsumed labels, because a challenger fitted on
    the same data as the champion differs from it only by random seed, and
    promoting on that would be promoting on noise.
    """
    active = session.execute(
        select(RetrainRun)
        .where(RetrainRun.status.in_(("requested", "running")))
        .order_by(RetrainRun.requested_at)
        .limit(1)
    ).scalar_one_or_none()
    if active is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"retrain run {active.id} is already {active.status}. Two concurrent "
                "runs would consume the same labels and race to publish a champion."
            ),
        )

    # Imported here rather than at module scope: `app.feedback` pulls in the
    # verdict query helpers, and the route module is imported at startup by every
    # process including the ones that never call this.
    from app.feedback import labelled_flows

    pending_labels = labelled_flows(session)
    if pending_labels.total == 0:
        raise HTTPException(
            status_code=422,
            detail=(
                "no analyst labels have been recorded since the last retrain. A "
                "challenger fitted on the same data as the champion differs from it "
                "only by random seed, so there would be nothing to promote on."
            ),
        )

    record = RetrainRun(
        status="requested",
        requested_by=body.requested_by or "dashboard",
        requested_at=dt.datetime.now(dt.UTC),
    )
    session.add(record)
    session.commit()
    session.refresh(record)
    return _retrain_response(record)


@router.get(
    "/retrain",
    response_model=RetrainStatusResponse,
    summary="Retraining history, including the runs that were not promoted",
)
def retrain_status(
    session: Session = Depends(get_session),
    limit: int = Query(default=20, ge=1, le=200),
) -> RetrainStatusResponse:
    """Every run, newest first.

    The runs that were *not* promoted are the point of keeping this history: a
    gate that has never declined anything is a gate nobody has evidence for.
    """
    runs = list(
        session.execute(
            select(RetrainRun)
            .order_by(RetrainRun.requested_at.desc(), RetrainRun.id.desc())
            .limit(limit)
        )
        .scalars()
        .all()
    )
    pending = int(
        session.execute(
            select(func.count()).select_from(RetrainRun).where(RetrainRun.status == "requested")
        ).scalar_one()
    )

    return RetrainStatusResponse(
        runs=[_retrain_response(record) for record in runs],
        pending=pending,
        worker_hint=WORKER_HINT,
    )
