"""Phase 7 -- cut the drift reference from the training split.

A PSI is a comparison against a reference, and the reference has to be fixed:
the bin edges come from the training distribution and stay there. Re-cutting
them from each night's observation would produce a number near zero no matter
how far the traffic had moved, because both sides would be rescaled to their own
shape.

So this runs once per model, writing ``artifacts/drift_reference.json``: for each
feature, the bin edges and the share of the training rows in each. The nightly
job then needs the artifact and not the training data -- which matters in a
container that ships the model but not the 500MB dataset it was fitted on.

The reference is built from the *scaled* matrix, through
``features.build_feature_matrix``, the same function training and serving both
use. Two reasons. The scaling is a monotone per-feature transform, so quantile
bins are identical either way and nothing is lost. And it means the reference and
the observation are produced by one code path, so a drift number cannot end up
measuring the difference between two feature implementations -- which is the
train/serve skew failure mode wearing a different hat.

    python -m training.drift_reference
    python -m training.drift_reference --split train --rows 100000
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from app.config import settings
from app.drift import DEFAULT_BINS, DriftError, FeatureReference, bin_shares, quantile_edges
from training.features import build_feature_matrix, load_preprocessing_bundle

logger = logging.getLogger(__name__)

REFERENCE_FILE = "drift_reference.json"
PREPROCESSING_FILE = "preprocessing.pkl"

# How many training rows the reference is cut from.
#
# A distribution summarised into ten bins does not need a million rows to be
# described precisely; a hundred thousand puts roughly ten thousand rows in each
# bin, which pins every share to three decimal places. The cap exists because
# `bin_shares` is pure Python by design -- `app/drift.py` is imported by the API
# and stays free of numpy -- and a million rows across ninety-two features is
# ninety-two million bisections for precision nobody reads.
DEFAULT_REFERENCE_ROWS = 100_000

# Fixed, so two runs of this script produce the same reference. A drift figure
# whose baseline moves between builds is not a baseline.
SEED = 7


def build_reference(
    frame: pd.DataFrame,
    bundle: Any,
    *,
    bins: int = DEFAULT_BINS,
    rows: int = DEFAULT_REFERENCE_ROWS,
    seed: int = SEED,
) -> tuple[dict[str, FeatureReference], int]:
    """Cut per-feature bins and shares from a training frame.

    Returns the references and the number of rows actually used.

    Every feature gets a reference, including the ones that barely vary. 28 of the
    shipped bundle's 92 collapse to two bins because they are flag counts taking
    one of two values, and two bins is the correct reference for a binary feature
    -- PSI reads normally against it. A feature that is entirely constant gets two
    bins as well, `(-inf, v)` and `[v, inf)`, and that is the point rather than a
    consolation: a feature that was always 7 and is now sometimes less than 7 has
    drifted, and those bins report it.

    The only skip is a sample that cannot be cut at all, which `quantile_edges`
    raises on and which is reported rather than swallowed.
    """
    matrix = build_feature_matrix(frame, bundle)

    sampled = matrix if len(matrix) <= rows else matrix.sample(n=rows, random_state=seed)
    used = len(sampled)

    references: dict[str, FeatureReference] = {}
    uncuttable: list[str] = []

    for feature in bundle["feature_order"]:
        if feature not in sampled.columns:
            continue
        values = [float(value) for value in sampled[feature].to_numpy()]
        try:
            edges = quantile_edges(values, bins=bins)
        except DriftError:
            # Only reachable for an empty sample. Reported rather than swallowed:
            # a feature silently missing from the reference is a feature that can
            # never report drift, and nothing on the screen would say why.
            uncuttable.append(feature)
            continue

        references[feature] = FeatureReference(
            feature=feature,
            edges=edges,
            expected=bin_shares(values, edges),
            rows=used,
        )

    if uncuttable:
        logger.warning(
            "%s feature(s) could not be cut into bins and have no reference: %s",
            len(uncuttable),
            ", ".join(uncuttable[:8]) + ("..." if len(uncuttable) > 8 else ""),
        )

    bin_counts = sorted(len(entry.edges) - 1 for entry in references.values())
    if bin_counts:
        logger.info(
            "%s feature(s) referenced, %s to %s bins each (%s asked for)",
            len(references),
            bin_counts[0],
            bin_counts[-1],
            bins,
        )
    return references, used


def write_reference(
    references: dict[str, FeatureReference],
    *,
    artifacts_dir: Path,
    source: str,
    rows: int,
    schema_hash: str,
    bins: int,
) -> Path:
    """Persist the reference beside the model it describes."""
    payload = {
        "built_at": datetime.now(UTC).isoformat(),
        "source": source,
        "rows": rows,
        "bins": bins,
        # The hash the reference was cut against. The nightly job refuses a
        # reference whose hash disagrees with the loaded bundle's, because bins
        # cut from a different feature order describe different features under
        # the same names -- which would produce a confident, meaningless PSI.
        "schema_hash": schema_hash,
        "features": [reference.to_json() for reference in references.values()],
    }
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    path = artifacts_dir / REFERENCE_FILE
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def load_reference(artifacts_dir: Path) -> dict[str, Any] | None:
    """Read the reference, or None when it has not been built.

    Absence is not an error: the phases land in order and the API serves before
    Phase 7 has run. The drift endpoint says so rather than inventing bins.
    """
    path = artifacts_dir / REFERENCE_FILE
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["references"] = {
        entry["feature"]: FeatureReference.from_json(entry)
        for entry in payload.get("features") or []
    }
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--split",
        default="train",
        help="Which processed split to cut the reference from (default: train).",
    )
    parser.add_argument("--bins", type=int, default=DEFAULT_BINS)
    parser.add_argument("--rows", type=int, default=DEFAULT_REFERENCE_ROWS)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)-8s %(message)s")

    artifacts = settings.artifacts_path
    bundle_path = artifacts / PREPROCESSING_FILE
    if not bundle_path.exists():
        print(
            f"no preprocessing bundle at {bundle_path}. Run the Phase 1 pipeline "
            "first -- a reference cut without the scaler and the feature order "
            "would describe a matrix the model never sees."
        )
        return 2

    split_path = settings.data_path / "processed" / f"{args.split}.parquet"
    if not split_path.exists():
        print(f"no split at {split_path}. Run `make data` first.")
        return 2

    bundle = load_preprocessing_bundle(bundle_path)
    frame = pd.read_parquet(split_path)

    references, rows = build_reference(frame, bundle, bins=args.bins, rows=args.rows)
    if not references:
        print("every feature was constant across the reference; nothing to write.")
        return 1

    path = write_reference(
        references,
        artifacts_dir=artifacts,
        source=f"{args.split}.parquet ({len(frame):,} rows, sampled to {rows:,})",
        rows=rows,
        schema_hash=bundle["schema_hash"],
        bins=args.bins,
    )

    print(f"wrote {path}")
    print(f"  {len(references)} feature(s) with {args.bins} bins, cut from {rows:,} rows")
    print(f"  schema hash {bundle['schema_hash']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
