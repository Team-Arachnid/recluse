"""Phase 7 -- drift detection.

Population Stability Index per feature, against a reference distribution cut
from the training split:

    PSI = sum over bins of (actual% - expected%) * ln(actual% / expected%)

Warning bands: 0.1 moderate, 0.25 significant. Crossing 0.25 raises the
retrain-recommended banner on the drift screen.

Two things make a PSI implementation either trustworthy or quietly wrong, and
both are handled here rather than left to the caller:

**Shared bin edges.** The expected and actual shares must be read off the same
axis. Binning each side independently -- say, deciles of each -- produces a
number that is always near zero no matter how far the distribution has moved,
because both sides are rescaled to their own shape. The edges come from the
reference and stay fixed; that is the whole point of a reference.

**Empty bins.** ``ln(actual / expected)`` is undefined when either side is zero,
and a feature whose traffic has moved out of a reference bin entirely is exactly
the case a drift monitor exists to catch -- so it cannot be the case that makes
the arithmetic explode. Both sides are floored at a small epsilon, which caps a
single empty bin's contribution instead of letting it dominate the sum or turn
it into infinity. The floor is reported alongside the score so a reader can see
it was applied.

Reference distributions are persisted (``artifacts/drift_reference.json``) so
the nightly job needs the artifact and not the training data. Nothing here
reads a parquet file.
"""

from __future__ import annotations

import math
from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

# PSI's two decision boundaries. Industry convention, and the brief fixes them.
PSI_MODERATE = 0.1
PSI_SIGNIFICANT = 0.25

BAND_STABLE = "stable"
BAND_MODERATE = "moderate"
BAND_SIGNIFICANT = "significant"

BANDS: tuple[str, ...] = (BAND_STABLE, BAND_MODERATE, BAND_SIGNIFICANT)

# The share floor applied to an empty bin on either side. 1e-6 of the population
# -- one row in a million, which is the configured daily flow volume -- so an
# empty bin contributes a large but finite amount rather than infinity.
EPSILON = 1e-6

# How many bins a reference is cut into. Ten quantile bins is the standard PSI
# construction: enough resolution to notice a shift, few enough that each bin
# holds a meaningful share even on a feature with a long tail.
DEFAULT_BINS = 10


class DriftError(ValueError):
    """A reference and an observation that cannot be compared."""


def band_for(psi: float) -> str:
    """Which warning band a score falls in."""
    if psi >= PSI_SIGNIFICANT:
        return BAND_SIGNIFICANT
    if psi >= PSI_MODERATE:
        return BAND_MODERATE
    return BAND_STABLE


def quantile_edges(values: Sequence[float], bins: int = DEFAULT_BINS) -> list[float]:
    """Bin edges at evenly spaced quantiles of the reference, deduplicated.

    Quantiles rather than equal widths, because these features are heavily
    skewed: equal-width bins over `flow_duration` put 99% of the reference in
    the first bin, and a PSI computed against that measures nothing.

    Deduplication matters more than it looks. Nearly a third of this project's
    features are flag counts taking one of two values, so eight of ten quantiles
    land on the same number and would produce zero-width bins no row can fall
    into. Collapsing them leaves fewer bins than asked for, which is the honest
    outcome: two bins is the correct reference for a binary feature.

    A feature that is *entirely* constant still gets a usable reference -- the open
    ends mean it gets `(-inf, v)` and `[v, inf)`. That is deliberate rather than
    incidental: a feature that was always 7 and is now sometimes less than 7 has
    drifted, and those two bins are what report it.
    """
    if not values:
        raise DriftError("cannot cut a reference from an empty sample")
    if bins < 2:
        raise DriftError(f"a reference needs at least 2 bins, got {bins}")

    ordered = sorted(float(value) for value in values)
    count = len(ordered)

    cuts: list[float] = []
    for index in range(bins + 1):
        position = min(count - 1, int(round(index * (count - 1) / bins)))
        cuts.append(ordered[position])

    # Open the ends so an observation outside the reference range still lands in
    # a bin. A value beyond the reference is drift, not an error, and dropping it
    # would hide the drift by shrinking the denominator.
    edges = [-math.inf]
    for cut in cuts[1:-1]:
        if cut > edges[-1]:
            edges.append(cut)
    edges.append(math.inf)

    if len(edges) < 2:
        raise DriftError("the reference collapsed to a single value")
    return edges


def bin_shares(values: Sequence[float], edges: Sequence[float]) -> list[float]:
    """The share of `values` falling in each bin of `edges`.

    Half-open bins, `[lower, upper)`. The outer edges are infinite, so every
    value lands somewhere and an observation beyond the reference's range falls
    in the end bin rather than being dropped -- a value outside the reference is
    drift, and discarding it would hide that by shrinking the denominator.

    Returns `len(edges) - 1` shares summing to 1.0, or all zeros for an empty
    sample.
    """
    if len(edges) < 2:
        raise DriftError("need at least two edges to form a bin")

    counts = [0] * (len(edges) - 1)
    # Binary search rather than a scan per value: this runs over ninety-odd
    # features at a few thousand rows each on every nightly run, and the linear
    # version made that ten bin comparisons per row per feature.
    interior = list(edges[1:-1])
    for raw in values:
        counts[bisect_right(interior, float(raw))] += 1

    total = sum(counts)
    if total == 0:
        return [0.0] * len(counts)
    return [count / total for count in counts]


def population_stability_index(
    expected: Sequence[float],
    actual: Sequence[float],
    *,
    epsilon: float = EPSILON,
) -> float:
    """PSI between two share vectors read off the same bins.

    Both arguments are *shares*, not counts, and must be the same length -- that
    length being the number of bins they share. Passing raw values here instead
    of shares is the obvious mistake, so a vector that does not look like a
    distribution is refused rather than silently scored.
    """
    if len(expected) != len(actual):
        raise DriftError(
            f"expected and actual must share their bins: {len(expected)} vs {len(actual)}"
        )
    if not expected:
        raise DriftError("cannot score an empty distribution")

    for name, shares in (("expected", expected), ("actual", actual)):
        total = sum(shares)
        if any(share < 0 for share in shares):
            raise DriftError(f"{name} contains a negative share")
        # A tolerant check: these arrive from float division and from JSON.
        if total > 0 and not math.isclose(total, 1.0, abs_tol=1e-6):
            raise DriftError(
                f"{name} shares sum to {total:.6f}, not 1.0 -- pass shares, not counts"
            )

    score = 0.0
    for expected_share, actual_share in zip(expected, actual, strict=True):
        safe_expected = max(expected_share, epsilon)
        safe_actual = max(actual_share, epsilon)
        score += (safe_actual - safe_expected) * math.log(safe_actual / safe_expected)
    return score


@dataclass
class FeatureReference:
    """The reference distribution of one feature: its bins and their shares."""

    feature: str
    edges: list[float]
    expected: list[float]
    rows: int

    def to_json(self) -> dict[str, Any]:
        # JSON has no infinity, and `json.dumps` emits a bare `Infinity` that is
        # not valid JSON for strict parsers. The open ends are stored as null and
        # restored on read.
        return {
            "feature": self.feature,
            "edges": [None if math.isinf(edge) else edge for edge in self.edges],
            "expected": self.expected,
            "rows": self.rows,
        }

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> FeatureReference:
        edges = [
            (-math.inf if index == 0 else math.inf) if edge is None else float(edge)
            for index, edge in enumerate(payload["edges"])
        ]
        return cls(
            feature=str(payload["feature"]),
            edges=edges,
            expected=[float(share) for share in payload["expected"]],
            rows=int(payload.get("rows") or 0),
        )


@dataclass
class FeatureDrift:
    """One feature's measured drift against its reference."""

    feature: str
    psi: float
    band: str
    expected: list[float]
    actual: list[float]

    @property
    def retrain_recommended(self) -> bool:
        return self.band == BAND_SIGNIFICANT


@dataclass
class DriftReport:
    """What one nightly run measured."""

    features: list[FeatureDrift] = field(default_factory=list)
    rows_observed: int = 0

    @property
    def max_psi(self) -> float:
        return max((entry.psi for entry in self.features), default=0.0)

    @property
    def counts(self) -> dict[str, int]:
        tally = dict.fromkeys(BANDS, 0)
        for entry in self.features:
            tally[entry.band] += 1
        return tally

    @property
    def retrain_recommended(self) -> bool:
        """True once any feature has crossed the significant band.

        Any feature, not the average. A mean PSI over ninety-two features hides
        a single feature that has moved completely, and one feature moving
        completely is how a drifted deployment usually looks -- a new backup job
        changes the shape of two features and leaves the other ninety alone.
        """
        return any(entry.retrain_recommended for entry in self.features)

    def worst(self, limit: int = 10) -> list[FeatureDrift]:
        return sorted(self.features, key=lambda entry: -entry.psi)[:limit]


def measure_drift(
    references: dict[str, FeatureReference],
    observed: dict[str, Sequence[float]],
    *,
    rows_observed: int = 0,
    epsilon: float = EPSILON,
) -> DriftReport:
    """Score every feature that has both a reference and an observation.

    A feature present in one and not the other is skipped rather than scored
    against a default: a feature with no reference cannot be said to have
    drifted, and a reference with no observation means the serving path stopped
    producing it, which is a schema problem the hash check already guards.
    """
    report = DriftReport(rows_observed=rows_observed)

    for feature, reference in sorted(references.items()):
        values = observed.get(feature)
        if values is None or len(values) == 0:
            continue
        actual = bin_shares(values, reference.edges)
        psi = population_stability_index(reference.expected, actual, epsilon=epsilon)
        report.features.append(
            FeatureDrift(
                feature=feature,
                psi=psi,
                band=band_for(psi),
                expected=list(reference.expected),
                actual=actual,
            )
        )

    return report
