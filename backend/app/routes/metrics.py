"""Model metrics, threshold what-ifs, drift, and the model registry.

Headline metric is PR-AUC. Accuracy may appear in a table but never as a
headline number: on 99% benign traffic, always answering benign scores 99%.
The response here carries `accuracy` in the per-class block and the Model
Performance screen is required to caption it, rather than this endpoint
omitting it and leaving the question of what it was open.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from app.config import settings
from app.metrics_store import MetricsStore
from app.routes import not_implemented
from app.schemas import (
    AnomalyHistogram,
    CurvePair,
    ErrorDistribution,
    ModelMetrics,
    NotImplementedResponse,
    ThresholdProjection,
)
from training.metrics import ErrorHistogram

router = APIRouter(tags=["metrics"])

STUB = {501: {"model": NotImplementedResponse}}


@router.get(
    "/metrics/model",
    response_model=ModelMetrics,
    summary="Per-class metrics, PR and ROC curves, and the LOAO table",
    responses={503: {"description": "No evaluation artifacts are loaded."}},
)
def model_metrics(request: Request) -> ModelMetrics:
    """Serve the measured evaluation, exactly as the offline run wrote it.

    Nothing here is recomputed from the database. These are the numbers from
    the held-out day, and a figure recomputed from whatever alerts happen to
    be stored would be a different claim wearing the same label -- it would
    move every time a replay ran.
    """
    store: MetricsStore = request.app.state.metrics

    if not store.is_loaded:
        raise HTTPException(
            status_code=503,
            detail=(
                "no evaluation artifacts are loaded, so there are no measured "
                "metrics to serve. Run the training and evaluation pipeline "
                "first -- an empty table here would read as a model that scored "
                "zero rather than one that has not been measured."
            ),
        )

    stage1 = store.stage1_test
    stage2 = store.stage2_test
    volume = stage1.get("volume") or {}

    return ModelMetrics(
        per_class=stage1.get("per_class") or {},
        labels=stage1.get("labels") or [],
        confusion_matrix=stage1.get("confusion") or [],
        curves=CurvePair(
            pr=stage1.get("pr_curve") or [],
            roc=stage1.get("roc_curve") or [],
        ),
        pr_auc=stage1.get("pr_auc"),
        roc_auc=stage1.get("roc_auc"),
        accuracy=stage1.get("accuracy"),
        tau_sup=stage1.get("tau_sup"),
        fpr_at_threshold=volume.get("fpr"),
        alerts_per_analyst_hour=volume.get("alerts_per_analyst_hour"),
        budget=store.budget,
        stage1_family_recall=stage1.get("family_recall") or {},
        stage2_family_recall=stage2.get("family_recall") or {},
        stage2_pr_auc=stage2.get("pr_auc"),
        loao=store.loao or {},
    )


@router.get(
    "/metrics/threshold",
    response_model=ThresholdProjection,
    summary="Recompute projected alert volume at a candidate threshold",
    responses={503: {"description": "No Stage 2 error distributions are loaded."}},
)
def threshold_what_if(
    request: Request,
    t: float = Query(..., ge=0.0, le=1.0, description="Candidate threshold"),
) -> ThresholdProjection:
    """Project alert volume, false-positive rate and recall at threshold `t`.

    **`t` is Stage 2's anomaly threshold**, not a Stage 1 probability.
    docs/Frontend-Screens.md draws this slider across the anomaly-score
    histogram and names the arithmetic it does -- `ErrorHistogram.above(tau)`
    over the persisted benign error distribution -- and a Stage 1 answer
    cannot be computed at an arbitrary `t` from what is stored, because the
    PR and ROC curves are `(recall, precision)` and `(fpr, tpr)` pairs with no
    threshold column to look `t` up in.

    Both figures are read off histograms that share their bin edges, so the
    false-positive rate and the recall describe the same point on the same
    axis. The result is approximate by construction -- a bin straddling `t`
    contributes all of itself -- which is exactly the arithmetic a slider
    does when it projects a count from bins instead of rescoring a day of
    traffic on every drag.

    `[0.0, 1.0]` is the declared range and it stays that way, but Stage 2's
    errors reach about 1.83 on the shipped card, so the top of the attack
    distribution is outside what this slider can express. The response says
    so in `covers_distribution` rather than quietly reporting a recall for a
    threshold the axis cannot reach.
    """
    store: MetricsStore = request.app.state.metrics

    benign_bins = store.error_histogram("test_benign")
    attack_bins = store.error_histogram("test_attack")
    if benign_bins is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "no Stage 2 benign error distribution is loaded, so a projection "
                "at this threshold would be a guess. Run the Phase 3 training and "
                "evaluation first."
            ),
        )

    benign = ErrorHistogram(**benign_bins)
    benign_above = benign.above(t)
    fpr = benign_above / benign.rows if benign.rows else 0.0

    recall: float | None = None
    attack_above: int | None = None
    if attack_bins is not None:
        attack = ErrorHistogram(**attack_bins)
        attack_above = attack.above(t)
        recall = attack_above / attack.rows if attack.rows else 0.0

    daily_volume = settings.expected_daily_flow_volume
    alerts_per_day = fpr * daily_volume

    return ThresholdProjection(
        t=t,
        fpr=fpr,
        recall=recall,
        benign_rows=benign.rows,
        benign_above=benign_above,
        attack_rows=ErrorHistogram(**attack_bins).rows if attack_bins is not None else None,
        attack_above=attack_above,
        false_alerts_per_day=alerts_per_day,
        # Per *analyst* hour, so the divisor is the shift length and not 24.
        # This is the same arithmetic training/metrics.py uses -- the shipped
        # card's 162.56 false alerts/day reads as 20.32/hour over an 8-hour
        # shift -- and the two must agree or the slider and the model card
        # would quote different volumes for the same operating point.
        alerts_per_analyst_hour=alerts_per_day / settings.analyst_shift_hours,
        budget_per_day=settings.max_alerts_per_day,
        target_fpr=settings.target_fpr,
        within_budget=alerts_per_day <= settings.max_alerts_per_day,
        covers_distribution=t <= float(benign.edges[-1]),
    )


@router.get(
    "/metrics/anomaly-histogram",
    response_model=AnomalyHistogram,
    summary="The Stage 2 reconstruction-error bins the threshold line is drawn across",
    responses={503: {"description": "No Stage 2 error distributions are loaded."}},
)
def anomaly_histogram(request: Request) -> AnomalyHistogram:
    """Serve the persisted error bins, so the slider has an axis to live on.

    `GET /metrics/threshold` answers "what happens at t". It cannot answer
    "what does the distribution look like", and the Live Traffic Monitor draws
    the threshold as a line *across a histogram* -- so it needs the bins
    themselves. Rebuilding the shape by sampling the projection endpoint sixty
    times would be sixty requests to draw one chart, and the result would still
    be a cumulative curve rather than the distribution.

    Phase 3 persisted these as sixty log-spaced bins with counts and reference
    percentiles rather than as raw rows, which is what lets the drag project an
    alert count by arithmetic instead of rescoring a day of traffic on every
    pointer move.

    Nothing here is recomputed from the database. These are the distributions
    the model was calibrated against; an overlay of what live traffic looks
    like now is a drift question and lands with `GET /metrics/drift` in
    Phase 7.
    """
    store: MetricsStore = request.app.state.metrics

    edges = store.error_histogram_edges
    names = store.error_histogram_names()
    if not edges or not names:
        raise HTTPException(
            status_code=503,
            detail=(
                "no Stage 2 error distributions are loaded, so there is no axis to "
                "draw a threshold on. Run the Phase 3 training first -- an empty "
                "histogram would read as traffic with no reconstruction error at "
                "all, which is a different claim from one that has not been measured."
            ),
        )

    distributions: list[ErrorDistribution] = []
    for name in names:
        bins = store.error_histogram(name) or {}
        distributions.append(
            ErrorDistribution(
                name=name,
                rows=int(bins.get("rows") or 0),
                counts=[int(count) for count in bins.get("counts") or []],
                percentiles={
                    key: float(value)
                    for key, value in (bins.get("percentiles") or {}).items()
                    if isinstance(value, (int, float))
                },
            )
        )

    thresholds = store.anomaly_thresholds
    histograms = store.anomaly_training.get("histograms") or {}

    # `spacing` is recorded both beside the shared edges and on each
    # distribution. Reading the shared key first and falling back to a
    # distribution's own means the chart gets the right axis scale whether or
    # not the writer duplicated it -- and log bins plotted on a linear axis
    # would pile sixty bars into the left-hand tenth of the chart.
    spacing = histograms.get("spacing")
    if not spacing:
        for name in names:
            spacing = (store.error_histogram(name) or {}).get("spacing")
            if spacing:
                break

    return AnomalyHistogram(
        edges=edges,
        spacing=str(spacing or "linear"),
        tau_anom=thresholds.get("tau"),
        budget_tau=thresholds.get("budget_tau"),
        distributions=distributions,
    )


@router.get("/metrics/drift", summary="PSI per feature over time", responses=STUB)
def drift_metrics():
    return not_implemented("GET /metrics/drift", "Phase 7 (drift and active learning)")


@router.get(
    "/models",
    summary="Model registry with champion and challenger versions",
    responses=STUB,
)
def list_models():
    return not_implemented("GET /models", "Phase 7 (drift and active learning)")
