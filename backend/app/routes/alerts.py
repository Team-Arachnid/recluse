"""Alert queue, detail, verdicts and host correlation.

The queue is the landing page of the whole application -- analysts live in it,
so it is home -- and it is sorted by `risk_score`, never by timestamp. Sorting
a triage queue chronologically ranks alerts by when a packet happened rather
than by what needs attention first. `ix_alerts_status_risk_score` exists to
serve exactly this query.

Pagination is keyset, not offset, and the cursor is `(risk_score, id)` rather
than a row number. An offset page over a table the replay is actively writing
to silently skips and repeats rows: a new higher-risk alert inserted between
two requests shifts everything down by one, so page 2 re-shows the last row of
page 1 and drops one that was never seen. Keying on the sort key plus a tie
breaker makes a page mean "the next rows after this exact position", which is
stable under concurrent inserts.
"""

from __future__ import annotations

import base64
import binascii
import datetime as dt
import json

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Alert, AnalystVerdict
from app.schemas import (
    AlertDetail,
    AlertFamily,
    AlertKind,
    AlertPage,
    AlertStatus,
    AlertSummary,
    Severity,
    VerdictRequest,
    VerdictResponse,
)

router = APIRouter(prefix="/alerts", tags=["alerts"])

DEFAULT_LIMIT = 50
MAX_LIMIT = 200

# The default correlation window from docs/API-Reference.md.
DEFAULT_WINDOW_HOURS = 24


def _encode_cursor(risk_score: float, alert_id: int) -> str:
    """Pack a keyset position into one opaque string.

    Opaque on purpose -- base64 of a tiny JSON object. The client's only
    contract is "hand back what you were given", which leaves the server free
    to change the sort key later without breaking a client that had learned to
    parse it.
    """
    raw = json.dumps({"r": risk_score, "i": alert_id}, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def _decode_cursor(cursor: str) -> tuple[float, int]:
    """Unpack a cursor, or 422 if it is not one this server issued.

    A malformed cursor is a client error and gets a named one. Silently
    treating it as "start from the beginning" would make a paging bug look
    like a queue that keeps resetting.
    """
    try:
        payload = json.loads(base64.urlsafe_b64decode(cursor.encode("ascii")))
        return float(payload["r"]), int(payload["i"])
    except (ValueError, KeyError, TypeError, binascii.Error) as exc:
        raise HTTPException(
            status_code=422,
            detail=(
                f"{cursor!r} is not a cursor this server issued. Pass back the "
                "`next_cursor` from the previous page unchanged, or omit it to "
                "start from the top of the queue."
            ),
        ) from exc


def _latest_verdicts(session: Session, alert_ids: list[int]) -> dict[int, str]:
    """The most recent verdict per alert, in one query rather than N.

    `analyst_verdicts` is one-to-many and the queue shows a verdict column, so
    without this a fifty-row page would issue fifty extra queries. Ordered by
    `created_at` then `id`, so two verdicts written inside the same timestamp
    resolution still have a defined winner.
    """
    if not alert_ids:
        return {}
    rows = session.execute(
        select(AnalystVerdict.alert_id, AnalystVerdict.verdict)
        .where(AnalystVerdict.alert_id.in_(alert_ids))
        .order_by(AnalystVerdict.created_at, AnalystVerdict.id)
    ).all()
    # Later rows overwrite earlier ones, so the last write per alert wins.
    return {alert_id: verdict for alert_id, verdict in rows}


def _summary(alert: Alert, latest_verdict: str | None) -> AlertSummary:
    return AlertSummary(
        **{
            column: getattr(alert, column)
            for column in AlertSummary.model_fields
            if column != "latest_verdict"
        },
        latest_verdict=latest_verdict,
    )


def _apply_filters(
    statement: Select,
    *,
    severity: Severity | None,
    kind: AlertKind | None,
    family: AlertFamily | None,
    status: AlertStatus | None,
    since: dt.datetime | None,
    until: dt.datetime | None,
) -> Select:
    """Narrow the queue query. Every filter is optional and they compose."""
    if severity is not None:
        statement = statement.where(Alert.severity == severity)
    if kind is not None:
        statement = statement.where(Alert.kind == kind)
    if family is not None:
        statement = statement.where(Alert.family == family)
    if status is not None:
        statement = statement.where(Alert.status == status)
    if since is not None:
        statement = statement.where(Alert.detected_at >= since)
    if until is not None:
        statement = statement.where(Alert.detected_at <= until)
    return statement


@router.get(
    "",
    response_model=AlertPage,
    summary="Filter, sort and cursor-paginate the alert queue",
    responses={422: {"description": "An unrecognised filter value or a malformed cursor."}},
)
def list_alerts(
    session: Session = Depends(get_session),
    severity: Severity | None = Query(default=None, description="Severity filter."),
    kind: AlertKind | None = Query(
        default=None, description="Backs the one-click UNCLASSIFIED_ANOMALY filter chip."
    ),
    family: AlertFamily | None = Query(default=None, description="Attack family filter."),
    status: AlertStatus | None = Query(default=None, description="Triage state filter."),
    since: dt.datetime | None = Query(default=None, description="ISO 8601 lower bound."),
    until: dt.datetime | None = Query(default=None, description="ISO 8601 upper bound."),
    cursor: str | None = Query(default=None, description="Opaque cursor from a previous page."),
    limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT, description="Page size."),
) -> AlertPage:
    """The triage queue: highest risk first, keyset-paginated.

    Ordered by `risk_score` descending with `id` descending as the tie
    breaker. The tie breaker is not cosmetic: `risk_score` is rounded to four
    places, so ties are common rather than rare, and without a second key the
    database is free to return tied rows in a different order on each request
    -- which would make a cursor built on `risk_score` alone skip rows.
    """
    statement = _apply_filters(
        select(Alert),
        severity=severity,
        kind=kind,
        family=family,
        status=status,
        since=since,
        until=until,
    )

    if cursor is not None:
        last_risk, last_id = _decode_cursor(cursor)
        # Strictly after the cursor position in (risk_score desc, id desc).
        statement = statement.where(
            (Alert.risk_score < last_risk)
            | ((Alert.risk_score == last_risk) & (Alert.id < last_id))
        )

    # One row more than asked for, to learn whether a next page exists without
    # a second count query -- the count is what makes a queue slow.
    rows = list(
        session.execute(
            statement.order_by(Alert.risk_score.desc(), Alert.id.desc()).limit(limit + 1)
        )
        .scalars()
        .all()
    )

    has_more = len(rows) > limit
    page = rows[:limit]
    verdicts = _latest_verdicts(session, [alert.id for alert in page])

    return AlertPage(
        items=[_summary(alert, verdicts.get(alert.id)) for alert in page],
        next_cursor=_encode_cursor(page[-1].risk_score, page[-1].id) if has_more and page else None,
        limit=limit,
    )


def _get_or_404(session: Session, alert_id: int) -> Alert:
    alert = session.get(Alert, alert_id)
    if alert is None:
        raise HTTPException(status_code=404, detail=f"no alert with id {alert_id}.")
    return alert


@router.get(
    "/{alert_id}",
    response_model=AlertDetail,
    summary="Alert detail: explanation, narrative, remediation, raw flow",
    responses={
        404: {"description": "No alert with that id."},
        422: {"description": "A non-integer id."},
    },
)
def get_alert(alert_id: int, session: Session = Depends(get_session)) -> AlertDetail:
    """Everything the Alert Detail drawer needs, in one request.

    The drawer answers three questions in a fixed order -- why was this
    flagged, what is it likely to be, how do I fix it -- so the response
    carries the explanation, the narrated sentence, the technique and the
    playbook together rather than making the client assemble them from
    several calls. `ground_truth_label` is populated only for replayed rows
    and the frontend badges it demo-only.
    """
    alert = _get_or_404(session, alert_id)
    verdicts = _latest_verdicts(session, [alert.id])
    return AlertDetail(
        **{
            column: getattr(alert, column)
            for column in AlertDetail.model_fields
            if column != "latest_verdict"
        },
        latest_verdict=verdicts.get(alert.id),
    )


@router.post(
    "/{alert_id}/verdict",
    response_model=VerdictResponse,
    status_code=201,
    summary="Record an analyst verdict (TP / FP / UNSURE) with an optional note",
    responses={
        404: {"description": "No alert with that id."},
        422: {"description": "An invalid verdict value or a non-integer id."},
    },
)
def submit_verdict(
    alert_id: int,
    body: VerdictRequest,
    session: Session = Depends(get_session),
) -> VerdictResponse:
    """Write one `analyst_verdicts` row. This is the input to active learning.

    `model_version` is captured from the alert being judged rather than from
    whatever is currently loaded, so the label stays auditable against the
    model that actually produced the alert even after a later retrain replaces
    the champion. A label attributed to the wrong model version is worse than
    no label: it would train the next model on a correction to a decision it
    never made.

    The alert's own `status` is deliberately not changed here. Recording a
    judgement and moving an alert through triage are different actions, the
    spec gives this endpoint only the verdict, and a verdict that silently
    closed an alert would take it out of a colleague's queue mid-review.
    """
    alert = _get_or_404(session, alert_id)

    verdict = AnalystVerdict(
        alert_id=alert.id,
        verdict=body.verdict,
        note=body.note,
        analyst=body.analyst,
        model_version=alert.model_version,
    )
    session.add(verdict)
    session.commit()
    session.refresh(verdict)

    return VerdictResponse(
        id=verdict.id,
        alert_id=verdict.alert_id,
        verdict=verdict.verdict,
        note=verdict.note,
        analyst=verdict.analyst,
        model_version=verdict.model_version,
        created_at=verdict.created_at,
    )


@router.get(
    "/{alert_id}/related",
    response_model=list[AlertSummary],
    summary="Other alerts from the same source host in a 24h window",
    responses={404: {"description": "No alert with that id."}},
)
def related_alerts(
    alert_id: int,
    session: Session = Depends(get_session),
    window_hours: int = Query(
        default=DEFAULT_WINDOW_HOURS, ge=1, le=24 * 30, description="Lookback window."
    ),
) -> list[AlertSummary]:
    """Other alerts from this source host, so a sequence reads as one story.

    A scan followed by an exploit attempt from the same host is one incident
    told in two alerts; without this the analyst sees two unrelated rows and
    has to notice the shared address themselves. Backed by
    `ix_alerts_src_ip_detected_at`, which the initial migration creates for
    exactly this query.

    The window is measured from the anchor alert's own `detected_at`, not from
    now, and it reaches in both directions. An alert opened a week after the
    fact would otherwise show no context at all, and the activity worth seeing
    is what surrounded the anchor rather than what happened recently.
    """
    anchor = _get_or_404(session, alert_id)

    window = dt.timedelta(hours=window_hours)
    lower = anchor.detected_at - window
    upper = anchor.detected_at + window

    rows = list(
        session.execute(
            select(Alert)
            .where(
                Alert.src_ip == anchor.src_ip,
                Alert.id != anchor.id,
                Alert.detected_at >= lower,
                Alert.detected_at <= upper,
            )
            .order_by(Alert.detected_at.desc(), Alert.id.desc())
        )
        .scalars()
        .all()
    )

    verdicts = _latest_verdicts(session, [alert.id for alert in rows])
    return [_summary(alert, verdicts.get(alert.id)) for alert in rows]
