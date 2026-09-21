"""Feature contract shared by training and serving.

This module is imported by both ``backend/training/`` and ``backend/app/``.
It is the single place feature transforms may live. Reimplementing any of it
inside the API is the train/serve skew failure mode, which is silent: mismatched
column order produces garbage scores without raising anything.

Phase 0 implements the parts that do not depend on the dataset -- column-name
normalisation, the leakage deny-list, the schema hash, and artifact bundle
I/O. The transforms themselves land in Phase 1, once CICIDS2017 is in hand.
"""

from __future__ import annotations

import hashlib
import pickle
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypedDict

if TYPE_CHECKING:  # pragma: no cover - typing only
    import pandas as pd

SCHEMA_HASH_PREFIX = "sha256"

# ---------------------------------------------------------------------------
# Leakage control (Phase 1)
#
# Identity columns memorise the lab's addressing scheme instead of attack
# behaviour, so they are dropped before training. Timestamp is kept only long
# enough to produce the temporal split, then dropped too.
# ---------------------------------------------------------------------------
LEAKAGE_COLUMNS: tuple[str, ...] = (
    "flow_id",
    "source_ip",
    "src_ip",
    "destination_ip",
    "dst_ip",
    "source_port",
    "src_port",
)

# Dropped after splitting, never used as a model input.
SPLIT_ONLY_COLUMNS: tuple[str, ...] = ("timestamp",)

# `destination_port` is deliberately absent from LEAKAGE_COLUMNS: it is
# genuinely predictive and also a memorisation trap, so Phase 1 trains twice
# (raw port vs. bucketed service groups) and reports both.
PORT_COLUMN = "destination_port"

_NON_ALNUM = re.compile(r"[^0-9a-z]+")


def normalise_column_name(name: str) -> str:
    """Normalise a raw CICIDS2017 header to snake_case.

    The published CSVs carry leading and trailing whitespace in their headers
    -- ``" Flow Duration"`` is not ``"Flow Duration"`` -- along with slashes
    and dots. Normalising first makes every later column reference reliable.

    >>> normalise_column_name(" Flow Duration")
    'flow_duration'
    >>> normalise_column_name("Flow Bytes/s")
    'flow_bytes_s'
    >>> normalise_column_name("Fwd Header Length.1")
    'fwd_header_length_1'
    """
    collapsed = _NON_ALNUM.sub("_", name.strip().lower())
    return collapsed.strip("_")


def normalise_columns(columns: list[str]) -> list[str]:
    """Normalise a list of headers, preserving order."""
    return [normalise_column_name(column) for column in columns]


def compute_schema_hash(feature_order: list[str] | tuple[str, ...]) -> str:
    """Hash the exact feature order a model was trained against.

    Persisted into the artifact bundle and re-checked at startup. If the API
    ever feeds columns in a different order than training did, this turns a
    silent scoring bug into a refused startup.

    >>> compute_schema_hash(["a", "b"]) == compute_schema_hash(["b", "a"])
    False
    """
    payload = "\n".join(feature_order).encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()
    return f"{SCHEMA_HASH_PREFIX}:{digest}"


class PreprocessingBundle(TypedDict):
    """Required contents of ``artifacts/preprocessing.pkl``.

    Scaler, column order and hash travel together on purpose: any one of them
    on its own is not enough to reproduce the training-time feature matrix.
    """

    scaler: Any
    feature_order: list[str]
    dropped_columns: list[str]
    port_encoding: dict[str, Any]
    schema_hash: str


def build_preprocessing_bundle(
    scaler: Any,
    feature_order: list[str],
    dropped_columns: list[str],
    port_encoding: dict[str, Any],
) -> PreprocessingBundle:
    """Assemble a bundle with a schema hash derived from ``feature_order``."""
    return PreprocessingBundle(
        scaler=scaler,
        feature_order=list(feature_order),
        dropped_columns=list(dropped_columns),
        port_encoding=dict(port_encoding),
        schema_hash=compute_schema_hash(feature_order),
    )


def save_preprocessing_bundle(bundle: PreprocessingBundle, path: Path) -> Path:
    """Persist the bundle next to the model artifacts."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        pickle.dump(bundle, handle, protocol=pickle.HIGHEST_PROTOCOL)
    return path


def load_preprocessing_bundle(path: Path) -> PreprocessingBundle:
    """Load a locally produced bundle.

    First-party artifact only -- written by ``backend/training/`` into a
    gitignored directory. There is no code path that unpickles an uploaded or
    downloaded file.
    """
    with path.open("rb") as handle:
        return pickle.load(handle)  # noqa: S301 - first-party artifact


# ---------------------------------------------------------------------------
# Phase 1 -- the transforms themselves
# ---------------------------------------------------------------------------
def build_feature_matrix(frame: pd.DataFrame, bundle: PreprocessingBundle | None = None):
    """Turn cleaned flow records into the model's input matrix.

    Called by training (to fit the scaler) and by serving (to apply it), which
    is what keeps the two paths identical.

    Implemented in Phase 1: drop leakage columns, apply the port encoding,
    reindex to ``feature_order``, then apply the fitted ``RobustScaler``
    (robust, not standard: flow features are heavy-tailed enough that a
    handful of enormous flows would flatten everything else).
    """
    raise NotImplementedError("build_feature_matrix is implemented in Phase 1 (data and features).")
