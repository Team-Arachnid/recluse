"""Phase 7 -- turning analyst verdicts into training data, safely.

Two things come out of the feedback loop, and the second one is a security
control rather than a feature.

**The labelled set for Stage 1.** Verdicts are read with their alert, so a
retrain learns from flows a human actually judged. The asymmetry is worth
stating plainly: a *false positive* changes the model, because it is a flow the
classifier called an attack and a human called benign -- a hard negative, which
is precisely the kind of example that moves a decision boundary. A *true
positive* mostly confirms what the model already did; it raises the measured
true-positive rate and it is worth recording, but on its own it teaches the
classifier little. And a true positive on an unclassified anomaly, which is the
most interesting label a SOC can produce, cannot enter Stage 1's vocabulary at
all without a human naming the family: the model is multiclass by family and
"an attack of some kind" is not one of its classes. That gap is real and is
reported rather than papered over.

**The benign refit pool for Stage 2, with two guards.** The autoencoder's
baseline has to be refit on recent normal traffic or it falls behind the network
it is watching. That is also the obvious way to attack it: generate enough
traffic, get it waved through, and the baseline learns that your traffic is
normal. So:

1. No row enters the pool without an analyst's explicit false-positive
   confirmation. Not "no verdict", not "dismissed" -- dismissing a noisy row is
   a triage action and says nothing about whether the traffic was benign, which
   is exactly why ``PATCH /alerts/status`` writes no verdict.
2. No single source host may contribute more than ``IDS_BENIGN_REFIT_HOST_CAP``
   of the pool. One host that can get a hundred alerts dismissed as false
   positives must not become the baseline.

Both guards report what they excluded. A guard that silently drops rows is a
guard nobody can audit, and a pool that quietly shrank to nothing would look
identical to a quiet week.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Alert, AnalystVerdict

logger = logging.getLogger(__name__)


def latest_verdicts_subquery():
    """One row per judged alert: its id and its most recent verdict.

    A window function, matching ``app/routes/alerts.py``: both resolve "the"
    verdict of an alert as the greatest ``(created_at, id)``. Two definitions of
    the latest verdict would mean the queue could show FP on a row this pool
    had declined to admit.
    """
    ranked = select(
        AnalystVerdict.alert_id.label("alert_id"),
        AnalystVerdict.verdict.label("verdict"),
        AnalystVerdict.consumed_at.label("consumed_at"),
        func.row_number()
        .over(
            partition_by=AnalystVerdict.alert_id,
            order_by=(AnalystVerdict.created_at.desc(), AnalystVerdict.id.desc()),
        )
        .label("rank"),
    ).subquery()
    return (
        select(ranked.c.alert_id, ranked.c.verdict, ranked.c.consumed_at)
        .where(ranked.c.rank == 1)
        .subquery()
    )


@dataclass
class LabelledFlows:
    """Flows an analyst judged, split by what they said.

    ``unnamed_attacks`` is the honest hole in the loop: confirmed attacks that
    carry no family label because Stage 2 found them and Stage 1 could not name
    them. They are counted and reported, and they do not enter Stage 1's training
    set, because inventing a family for them would be fabricating the one thing
    the analyst did not supply.
    """

    benign: list[dict[str, Any]] = field(default_factory=list)
    attacks: list[tuple[dict[str, Any], str]] = field(default_factory=list)
    unnamed_attacks: int = 0
    unsure: int = 0

    @property
    def total(self) -> int:
        return len(self.benign) + len(self.attacks) + self.unnamed_attacks + self.unsure


def labelled_flows(session: Session, *, only_unconsumed: bool = True) -> LabelledFlows:
    """Read every analyst-judged alert as a labelled flow record.

    ``only_unconsumed`` restricts this to labels no retrain has taken yet, which
    is the default because that is what a retrain wants. Passing False gives the
    whole history, which is what an audit wants.
    """
    latest = latest_verdicts_subquery()

    statement = select(Alert, latest.c.verdict).join(latest, latest.c.alert_id == Alert.id)
    if only_unconsumed:
        statement = statement.where(latest.c.consumed_at.is_(None))

    result = LabelledFlows()
    for alert, verdict in session.execute(statement).all():
        flow = dict(alert.raw_flow or {})
        # `_provenance` is a caveat the pipeline attached for the drawer to
        # render, not a feature. Leaving it in would put a dict into a column
        # the feature builder expects to be numeric.
        flow.pop("_provenance", None)
        if not flow:
            continue

        if verdict == "FP":
            result.benign.append(flow)
        elif verdict == "TP":
            if alert.family:
                result.attacks.append((flow, alert.family))
            else:
                result.unnamed_attacks += 1
        else:
            result.unsure += 1

    return result


@dataclass
class BenignPool:
    """The result of building the autoencoder's refit pool, guards included."""

    flows: list[dict[str, Any]] = field(default_factory=list)
    # src_ip -> how many of that host's confirmed-benign rows were admitted.
    admitted_by_host: dict[str, int] = field(default_factory=dict)
    # src_ip -> how many were refused because the host had hit its cap.
    capped_by_host: dict[str, int] = field(default_factory=dict)
    candidates: int = 0
    host_cap: float = 0.0
    # The solved per-host allowance: how many rows any one host was permitted.
    # Reported because "why was my host capped" has to have an answer.
    per_host_allowance: int = 0
    usable: bool = False
    reason: str = ""

    @property
    def admitted(self) -> int:
        return len(self.flows)

    @property
    def capped(self) -> int:
        return sum(self.capped_by_host.values())

    def render(self) -> str:
        lines = [
            f"candidates        {self.candidates:,}  (analyst-confirmed false positives)",
            f"admitted          {self.admitted:,}",
            f"refused by cap    {self.capped:,}  (no host above {self.host_cap:.0%} of the "
            f"pool, which solved to {self.per_host_allowance:,} row(s) per host)",
            f"usable            {self.usable}  {self.reason}",
        ]
        if self.capped_by_host:
            lines.append("")
            lines.append("HOSTS AT THE CAP")
            for host, refused in sorted(self.capped_by_host.items(), key=lambda p: -p[1]):
                admitted = self.admitted_by_host.get(host, 0)
                lines.append(f"  {host:<20} {admitted:>6,} admitted, {refused:>6,} refused")
        return "\n".join(lines)


def _per_host_allowance(hosts: list[str], *, cap: float) -> int:
    """How many rows one host may contribute, solved rather than guessed.

    The cap is a share of the final pool, so the allowance and the pool size
    depend on each other. This iterates to a fixed point: assume no limit, see how
    big the pool would be, derive the allowance that share implies, and repeat.
    Each pass can only tighten the allowance, so it terminates -- the loop bound
    is there to make that a guarantee rather than an argument.

    Floored at one. A zero allowance would empty the pool whenever the cap times
    the pool size rounds below one, which is every small pool -- and an empty pool
    is indistinguishable from a quiet week, which is the state this module exists
    to avoid producing silently.
    """
    if not hosts:
        return 0

    counts: dict[str, int] = {}
    for host in hosts:
        counts[host] = counts.get(host, 0) + 1

    allowance = max(counts.values())
    for _ in range(32):
        size = sum(min(count, allowance) for count in counts.values())
        tightened = max(1, int(cap * size))
        if tightened >= allowance:
            return allowance
        allowance = tightened
    return allowance


def build_benign_pool(
    session: Session,
    *,
    host_cap: float | None = None,
    minimum_rows: int | None = None,
    only_unconsumed: bool = True,
) -> BenignPool:
    """Assemble the confirmed-benign refit pool, applying both guards.

    The cap is a share of the *final* pool, which makes it circular: how many rows
    a host may contribute depends on how large the pool is, and the pool's size
    depends on how many rows each host contributed. Resolving it greedily -- admit
    a row if the host's share of the pool *so far* stays under the cap -- looks
    right and is wrong. The first row makes the pool 100% one host, so every
    subsequent row from anybody is at 50%, and for any cap below a half the pool
    deadlocks at exactly one row. A test caught that; the arithmetic is the reason.

    So the per-host allowance is solved for instead, by a short fixed-point
    iteration: start from "no limit", compute the pool that allowance produces,
    recompute the allowance from that pool's size, repeat. It converges in a few
    passes because each one can only tighten.

    With a 20% cap and five contributing hosts, each ends up with a fifth. With a
    20% cap and one host, the allowance floors at a single row -- the pool is then
    one row, which the row floor below correctly refuses to refit on. That is the
    honest outcome: a baseline cannot be rebuilt from one host's traffic, and the
    guard says so rather than quietly producing a pool that one host dominates.

    Rows are considered in a fixed order (by alert id), so the same verdicts
    produce the same pool. A random order would make a refused row a matter of
    luck, and "why was my host capped" unanswerable.
    """
    cap = settings.benign_refit_host_cap if host_cap is None else host_cap
    floor = settings.benign_refit_min_rows if minimum_rows is None else minimum_rows

    latest = latest_verdicts_subquery()
    statement = (
        select(Alert)
        .join(latest, latest.c.alert_id == Alert.id)
        .where(latest.c.verdict == "FP")
        .order_by(Alert.id)
    )
    if only_unconsumed:
        statement = statement.where(latest.c.consumed_at.is_(None))

    candidates = list(session.execute(statement).scalars().all())

    pool = BenignPool(candidates=len(candidates), host_cap=cap)

    usable: list[tuple[str, dict[str, Any]]] = []
    for alert in candidates:
        flow = dict(alert.raw_flow or {})
        # `_provenance` is a caveat for the drawer, not a feature. Leaving it in
        # would put a dict into a column the feature builder expects to be
        # numeric.
        flow.pop("_provenance", None)
        if flow:
            usable.append((alert.src_ip, flow))

    allowance = _per_host_allowance([host for host, _ in usable], cap=cap)
    pool.per_host_allowance = allowance

    for host, flow in usable:
        admitted_here = pool.admitted_by_host.get(host, 0)
        if admitted_here >= allowance:
            pool.capped_by_host[host] = pool.capped_by_host.get(host, 0) + 1
            continue

        pool.flows.append(flow)
        pool.admitted_by_host[host] = admitted_here + 1

    if pool.admitted < floor:
        pool.usable = False
        pool.reason = (
            f"-- {pool.admitted:,} rows is under the {floor:,}-row floor, so the "
            "baseline is left alone. A baseline moved by a handful of rows is a "
            "baseline moved by whoever supplied them."
        )
    else:
        pool.usable = True
        pool.reason = f"-- {pool.admitted:,} rows from {len(pool.admitted_by_host)} host(s)"

    logger.info(
        "benign refit pool: %s candidates, %s admitted, %s refused by the %.0f%% host cap",
        pool.candidates,
        pool.admitted,
        pool.capped,
        cap * 100,
    )
    return pool


def mark_consumed(session: Session, *, at: Any) -> int:
    """Stamp every unconsumed verdict as taken by a retrain. Returns the count.

    This is what makes "new labels since the last retrain" answerable from
    ``analyst_verdicts`` alone. It is called once, after a run has finished,
    whether or not the challenger was promoted -- a label that was evaluated has
    been consumed even if the model it produced lost, and leaving it unconsumed
    would make the next run train on it again and report it as new.
    """
    rows = list(
        session.execute(select(AnalystVerdict).where(AnalystVerdict.consumed_at.is_(None)))
        .scalars()
        .all()
    )
    for verdict in rows:
        verdict.consumed_at = at
    return len(rows)
