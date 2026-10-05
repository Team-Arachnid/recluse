"""Phase 5 -- the triage queue's ordering key, and the severity badge beside it.

``alerts.risk_score`` is the sort key the triage queue uses -- never the
timestamp. The composite index ``ix_alerts_status_risk_score`` exists to serve
exactly that query, and ``docs/Frontend-Screens.md`` says why: "Sorting a
triage queue chronologically ranks alerts by when a packet happened rather
than by what needs attention first." ``alerts.severity`` is the badge beside
it.

Neither is specified anywhere else in this project -- not the brief, not
``docs/API-Reference.md``, not any other doc. This module is where the
definition lives, which means this docstring carries more weight than most:
a reader has to be able to disagree with the policy on the merits, not just
read a formula off the page.

**The shape of the policy.** One 0-1 scale, with both detection stages
contributing a comparable "base", and two enrichments -- asset criticality and
the host's prior alert count -- adjusting it afterward:

    base = stage1_base(...)   if the alert is KNOWN
         = stage2_base(...)   if it is an UNCLASSIFIED_ANOMALY
    lift = min(criticality_lift + history_lift, LIFT_CAP)
    risk = base + (1 - base) * lift

**The one departure from the pre-flight ruling, and why.** The pre-flight
ruling said Stage 1 contributes its attack confidence directly, as a
probability, and Stage 2 contributes its raw position in the benign error
distribution -- a raw percentile. This module changes that one thing: both
stages are measured relative to their own operating threshold, not as a raw
probability or a raw percentile.

The reason is arithmetic, not aesthetic. ``tau_anom`` is calibrated at the
99.5th percentile of the benign error distribution, so *every*
UNCLASSIFIED_ANOMALY alert, by construction, has a raw benign-percentile of at
least 0.995. Reporting that raw percentile as the score would pin every
Stage 2 alert into ``[0.995, 1.0]``, make almost all of them the same severity
band, and destroy the ordering this column exists to provide. The same
compression applies to Stage 1, more mildly, because every KNOWN alert's
confidence already sits above ``tau_sup``.

Measuring from the threshold instead asks a question an analyst can actually
use: how far past the bar is this, as a fraction of the headroom that was
available? An alert exactly at the threshold scores 0 -- it is the least
interesting thing that still alerted, which is true, not a bug. One at the
extreme end of the range scores 1.

One caveat belongs here rather than left for a reader to find alone: that is
exactly true for Stage 1, whose base is a linear rescale of a bounded
probability, but only approximately true for Stage 2. Percentile space has
its own ceiling, so very different degrees of "extremely anomalous" compress
toward 1 much as the raw percentile did before this rescale -- far less
severely, but not to zero. ``stage2_base`` gives the real numbers and the
reason this is judged acceptable; in short, ordering and severity banding
both survive all the way to the top of the range, and the criticality/
history lift loses its own leverage over the same shrinking headroom at the
same rate, so what is lost is the finest spacing among the most extreme
anomalies, not the ranking or the badge.

This makes ``risk_score`` a function of the thresholds that produced the
alert, so a score from one model version is not directly comparable to a
score from another unless both thresholds are known. That is already
anticipated and already solved: ``docs/Database-Schema.md`` explains that
``model_versions.tau_sup``/``tau_anom`` are stored on the registry row "as well
as" living in the artifact bundle, "so an alert written months ago can be
re-read against the exact threshold that produced it, even after the bundle on
disk has been replaced," and that "without the copy, risk_score values from
two different eras would be silently incomparable." The caller is expected to
pass the exact thresholds an alert was scored against -- see
``app/models.py::ModelVersion`` -- not whatever the currently-active model
happens to have.

**What each kind reads, and why the two are not interchangeable.**
``training/fusion.py`` documents the column semantics this module depends on:
``confidence`` is Stage 1's attack confidence for every row it scored, and
``anomaly_score`` is ``NaN`` (``None`` once serialised) wherever Stage 2 was
*not consulted* -- which is every row Stage 1 named. A KNOWN alert therefore
has a confidence and no anomaly score; an UNCLASSIFIED_ANOMALY has the
reverse. Reading the wrong one for a given kind is not a fallback, it is a
different claim about the alert, so ``risk_score`` raises rather than guesses
when the input its kind needs is missing.

**What "severity" means given the rescaling.** ``severity`` buckets
``risk_score`` into quartiles of headroom *above the operating threshold*, not
quartiles of "how bad is this in the abstract". In particular, ``low`` is the
bottom quarter of a range that is already past the bar required to alert at
all -- nothing below either stage's threshold becomes an alert in the first
place, so there is no benign band for this scale to include. A SOC reading
"low" as "ignorable" is the exact failure this definition can cause.

**Why this module has no other dependencies.** Pure functions over module
constants: no database, no settings, no ``app.state``, no disk. The caller
(the alert pipeline) passes in the asset criticality and the benign-error
histogram it already has; this module only does arithmetic over what it is
given. That is also why its tests build a histogram shape rather than loading
a trained artifact.
"""

from __future__ import annotations

from typing import Any

from app.models import ALERT_KINDS
from training.metrics import ErrorHistogram


def _clamp(value: float) -> float:
    """Pin a base or a risk value to ``[0, 1]``, the one scale everything is
    fused onto."""
    return max(0.0, min(1.0, value))


# ---------------------------------------------------------------------------
# The two bases -- each stage's contribution, rescaled onto its own [0, 1]
# ---------------------------------------------------------------------------


def stage1_base(confidence: float, tau_sup: float) -> float:
    """Stage 1's contribution: headroom past its own operating threshold.

    ``(confidence - tau_sup) / (1 - tau_sup)``, clamped to ``[0, 1]``. Every
    KNOWN alert already has ``confidence >= tau_sup`` -- that is why it
    alerted -- so this rescales the live range ``[tau_sup, 1.0]`` onto
    ``[0, 1]`` instead of reporting the raw probability, which is bunched into
    whatever sliver sits above the threshold. See the module docstring for why
    a raw probability is the wrong base.
    """
    return _clamp((confidence - tau_sup) / (1 - tau_sup))


def _benign_percentile(error: float, hist: ErrorHistogram) -> float:
    """The share of benign rows at or below ``error``.

    Delegates the bin walk to ``ErrorHistogram.above``, which documents the
    approximation it makes (a bin straddling the threshold counts as fully
    above it) and the reason that approximation is correct here: it is exactly
    the arithmetic the dashboard's threshold slider does. Re-deriving the bin
    walk here would be a second copy that can drift from the one the slider
    draws from.
    """
    return 1 - hist.above(error) / hist.rows


def stage2_base(anomaly_score: float, tau_anom: float, histogram: dict[str, Any] | None) -> float:
    """Stage 2's contribution: headroom past its own threshold, in percentile
    space rather than error space.

    Reconstruction error has no natural ceiling, so headroom cannot be a
    fraction of the raw error the way Stage 1 uses a fraction of
    ``[tau_sup, 1.0]``. Measured in the benign error distribution's own
    percentile space instead: ``p`` is the share of benign rows at or below
    ``anomaly_score``, ``p_tau`` is that same share at ``tau_anom``, and the
    base is ``(p - p_tau) / (1 - p_tau)``, clamped to ``[0, 1]`` -- the same
    threshold-relative rescale ``stage1_base`` applies, just in percentile
    space rather than probability space. See the module docstring for why a
    raw percentile is the wrong base.

    The rescale has a ceiling of its own, and it is worth stating plainly.
    Once ``anomaly_score`` pushes ``p`` close to 1.0, additional raw error
    buys less and less additional ``p`` -- percentile space has nowhere
    higher to go, no matter how much the raw error keeps climbing. Against
    the shipped model card (``tau_anom`` at the 99.5th percentile of benign
    error, the 99.9th at roughly 0.3546), errors of 0.45, 0.8 and 1.5 give
    bases of 0.9501, 0.9983 and 0.9996 (rounded to four places): correctly
    ordered, but compressed into the top half of a percent of the range
    rather than spread across it -- far less severe than the raw percentile's
    own ``[0.995, 1.0]`` sliver, but the same shape of problem recurring in
    miniature at the opposite end of the scale. Two things keep this from
    costing what it sounds like it should: ordering and severity banding
    both survive intact (all three numbers above land as ``critical``, in
    the right order), so the queue still sorts correctly and the badge is
    still right; and the criticality/history lift's leverage over whatever
    headroom remains shrinks at exactly the same rate the base approaches 1,
    because the lift spends ``1 - base`` -- at ``base = 0.9996`` even
    ``LIFT_CAP``'s full 0.40 could add only about 0.00016, so enrichment
    loses the ability to re-rank alerts at the very top of the queue at
    precisely the point this compression sets in. What is actually lost is
    fine-grained spacing among the most extreme anomalies -- an analyst
    cannot read "ten times as anomalous" off this column once two alerts are
    both this far out -- which is judged an acceptable trade for not
    reintroducing the compression the rescale exists to remove everywhere
    else.

    ``histogram`` is the dict ``ModelBundle.benign_error_histogram`` carries
    (the model card's ``stage2.benign_error_histogram``): keys ``edges``,
    ``counts``, ``rows``, ``percentiles``, ``spacing``. It is rebuilt into an
    ``ErrorHistogram`` rather than consumed as a raw dict so the bin
    arithmetic stays in the one place that owns it.

    ``histogram=None`` is a real, expected case, not an error path: a served
    bundle can carry trained Stage 2 weights with no model card loaded, since
    the card is a separate artifact from the weights. Raising here would stop
    every Stage 2 alert from being written at all, which is worse than a
    cruder ranking -- so this falls back to ``anomaly_score`` as a multiple of
    ``tau_anom`` rather than a position in a distribution. That fallback is
    cruder because it ignores the actual shape of the benign tail, but it is
    still 0 at the threshold, still monotonic, and still bounded.
    """
    if histogram is None:
        return _clamp(min((anomaly_score - tau_anom) / tau_anom, 1.0))

    hist = ErrorHistogram(**histogram)
    p = _benign_percentile(anomaly_score, hist)
    p_tau = _benign_percentile(tau_anom, hist)
    return _clamp((p - p_tau) / (1 - p_tau))


# ---------------------------------------------------------------------------
# The adjustments -- asset criticality and alert history, capped so neither
# can outrank the model itself
# ---------------------------------------------------------------------------

# Asset criticality's vocabulary happens to be the same four words as
# app.models.SEVERITIES (app/topology.py::criticality_for documents the
# reuse) -- a different axis, asset importance rather than alert severity,
# that happens to share a name with it.
#
# `None` is deliberately not a key here: an address `criticality_for` does
# not recognise gets the same 0.0 lift as `low`, via `.get(..., 0.0)` at the
# call site below. `low` means assessed and unimportant; `None` means never
# assessed. Neither is evidence the alert deserves to rank higher, so both
# get no lift -- but they are not the same claim, and a later edit must not
# quietly start treating an unassessed host as more suspicious than a `low`
# one.
CRITICALITY_LIFT: dict[str, float] = {"critical": 0.30, "high": 0.20, "medium": 0.10, "low": 0.0}

# History saturates on purpose: the fifth alert from a host is strong
# evidence something is wrong there; the five-hundredth is not five hundred
# times stronger. An unbounded term would let one noisy host own the top of
# the queue permanently.
HISTORY_LIFT_MAX: float = 0.15
HISTORY_SATURATES_AT: int = 10  # prior alerts for this host

# The two lifts sum and then clamp here, so enrichment can never outweigh the
# model -- this keeps risk_score a detector ranking, not an asset-inventory
# ranking. A host being important is a reason to look sooner among equally
# plausible alerts, not a reason to treat weak evidence as strong.
LIFT_CAP: float = 0.40


# ---------------------------------------------------------------------------
# The score
# ---------------------------------------------------------------------------


def risk_score(
    *,
    kind: str,
    confidence: float | None,
    anomaly_score: float | None,
    tau_sup: float | None,
    tau_anom: float | None,
    histogram: dict[str, Any] | None = None,
    asset_criticality: str | None = None,
    host_prior_alert_count: int = 0,
) -> float:
    """The triage queue's sort key: one 0-1 score, highest-risk-first.

    Keyword-only on purpose -- eight parameters, four of them optional floats,
    is a positional call site waiting to transpose two of them silently.

    ``kind`` selects which stage's base the score is built from (see the
    module docstring for why the two are not interchangeable):

    * ``KNOWN`` reads ``confidence`` and ``tau_sup`` and calls ``stage1_base``.
    * ``UNCLASSIFIED_ANOMALY`` reads ``anomaly_score``, ``tau_anom`` and
      ``histogram`` and calls ``stage2_base``.

    Either branch raises ``ValueError`` naming the missing field when the
    input its kind needs is ``None`` -- including the threshold, because a
    stage that was never loaded cannot have produced the alert being scored.
    A ``kind`` outside ``app.models.ALERT_KINDS`` raises the same way, naming
    the vocabulary.

    ``asset_criticality`` and ``host_prior_alert_count`` then lift the base by
    consuming headroom rather than adding to the raw score:

        risk = base + (1 - base) * lift

    An alert already at 0.95 has almost no headroom left and should not be
    pushed over 1.0 by its asset tag; a 0.40 alert on the domain controller
    genuinely should climb. A multiplicative or additive form would either
    saturate past 1.0, needing a second clamp that hides the intent, or let a
    high-criticality tag matter just as much at the top of the range as the
    bottom -- backwards, since the model's own confidence should dominate
    there.

    Returns a float in ``[0, 1]``, rounded to 4 decimal places so two
    equal-risk alerts do not sort unstably on float noise.
    """
    if kind not in ALERT_KINDS:
        raise ValueError(
            f"{kind!r} is not a known alert kind. ALERT_KINDS is {ALERT_KINDS!r}; "
            "risk_score has a different rule for each one -- KNOWN reads "
            "`confidence`, UNCLASSIFIED_ANOMALY reads `anomaly_score` -- so a kind "
            "outside that vocabulary means something upstream invented one, and "
            "guessing which input to read would hide that bug instead of surfacing it."
        )

    if kind == "KNOWN":
        if confidence is None:
            raise ValueError(
                "risk_score(kind='KNOWN') needs `confidence`, Stage 1's attack "
                "probability for this row, and it was None. `anomaly_score` is not "
                "a fallback: training/fusion.py sets it to NaN/None for every row "
                "Stage 1 named, so reading it here would be a different claim about "
                "the alert, not a substitute value."
            )
        if tau_sup is None:
            raise ValueError(
                "risk_score(kind='KNOWN') needs `tau_sup`, Stage 1's operating "
                "threshold, and it was None. A KNOWN alert cannot exist unless "
                "Stage 1 was loaded and produced it, so a missing threshold here "
                "means the caller passed the wrong model version's state."
            )
        base = stage1_base(confidence, tau_sup)
    else:
        if anomaly_score is None:
            raise ValueError(
                "risk_score(kind='UNCLASSIFIED_ANOMALY') needs `anomaly_score`, "
                "Stage 2's reconstruction error, and it was None. `confidence` is "
                "not a fallback either: an anomaly alert exists precisely because "
                "Stage 1 did not name the row, so there is no attack confidence to "
                "substitute."
            )
        if tau_anom is None:
            raise ValueError(
                "risk_score(kind='UNCLASSIFIED_ANOMALY') needs `tau_anom`, Stage "
                "2's operating threshold, and it was None. An UNCLASSIFIED_ANOMALY "
                "alert cannot exist unless Stage 2 was loaded and produced it, so a "
                "missing threshold here means the caller passed the wrong model "
                "version's state."
            )
        base = stage2_base(anomaly_score, tau_anom, histogram)

    # `None` and any unrecognised string both fall through to 0.0, the same as
    # `low` -- see the comment on CRITICALITY_LIFT above for why that is not
    # the same claim as "this host is unimportant".
    criticality_lift = CRITICALITY_LIFT.get(asset_criticality, 0.0)
    history_lift = (
        min(host_prior_alert_count, HISTORY_SATURATES_AT) / HISTORY_SATURATES_AT * HISTORY_LIFT_MAX
    )
    lift = min(criticality_lift + history_lift, LIFT_CAP)

    # The lift consumes headroom (`1 - base`), not raw score -- see the
    # docstring above for the alternatives this rejects and why.
    risk = base + (1 - base) * lift
    return round(_clamp(risk), 4)


# ---------------------------------------------------------------------------
# The severity badge
# ---------------------------------------------------------------------------

# Every label here must be one of app.models.SEVERITIES -- enforced by a test
# against the real tuple, not a copy of it, so the two vocabularies cannot
# silently drift apart.
SEVERITY_BANDS: tuple[tuple[float, str], ...] = (
    (0.75, "critical"),
    (0.50, "high"),
    (0.25, "medium"),
    (0.00, "low"),
)


def severity(risk: float) -> str:
    """The badge beside ``risk_score``: quartiles of headroom past the
    threshold, not quartiles of "how bad is this attack in the abstract".

    Bands are checked highest-first and a score keeps the top band whose
    floor it meets, so a boundary value (``0.75`` exactly) lands in the higher
    band rather than the lower one.

    ``low`` is still an alert, not "probably nothing": nothing below either
    stage's operating threshold becomes an alert in the first place, so there
    is no benign band for this scale to include. A SOC reading ``low`` as
    ignorable is the failure this docstring exists to prevent.
    """
    for floor, label in SEVERITY_BANDS:
        if risk >= floor:
            return label
    raise ValueError(
        f"severity() received {risk!r}, outside the [0, 1] range risk_score promises; "
        "there is no band below SEVERITY_BANDS' lowest floor of 0.0."
    )
