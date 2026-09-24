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
from typing import Any, TypedDict

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

LABEL_COLUMN = "label"
TIMESTAMP_COLUMN = "timestamp"

# Dropped after splitting, never used as a model input.
SPLIT_ONLY_COLUMNS: tuple[str, ...] = (TIMESTAMP_COLUMN,)

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
#
# Everything below runs identically in training and in serving. That is the
# point: a second implementation inside the API is the train/serve skew
# failure mode, and it is silent.
# ---------------------------------------------------------------------------
PORT_ENCODING_RAW = "raw"
PORT_ENCODING_BUCKETED = "bucketed"
PORT_ENCODING_STRATEGIES: tuple[str, ...] = (PORT_ENCODING_RAW, PORT_ENCODING_BUCKETED)

DEFAULT_TOP_PORTS = 20

# IANA ranges. Bucketing asks "what kind of service is this?" instead of
# "which port did this lab happen to use?", which is the memorisation trap.
SERVICE_GROUPS: tuple[str, ...] = ("well_known", "registered", "ephemeral")
_WELL_KNOWN_MAX = 1023
_REGISTERED_MAX = 49151


def service_group(port: int) -> str:
    """Bucket a destination port into its IANA range.

    >>> service_group(80), service_group(8080), service_group(53000)
    ('well_known', 'registered', 'ephemeral')
    """
    if port <= _WELL_KNOWN_MAX:
        return "well_known"
    if port <= _REGISTERED_MAX:
        return "registered"
    return "ephemeral"


def drop_leakage_columns(frame: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Remove identity and splitting columns, returning what went.

    `destination_port` is deliberately not removed here: it is decided by the
    port ablation, not by the deny-list.
    """
    unwanted = [
        column
        for column in frame.columns
        if column in LEAKAGE_COLUMNS or column in SPLIT_ONLY_COLUMNS
    ]
    return frame.drop(columns=unwanted), unwanted


def fit_port_encoding(
    frame: pd.DataFrame,
    strategy: str = PORT_ENCODING_RAW,
    top_n: int = DEFAULT_TOP_PORTS,
) -> dict[str, Any]:
    """Decide the destination-port representation, on the training split only.

    The returned dict is persisted in the bundle, so the columns it produces
    are fixed once. A port first seen at serve time must not add a column --
    that would change the matrix width under a trained model.
    """
    if strategy not in PORT_ENCODING_STRATEGIES:
        raise ValueError(
            f"unknown port encoding strategy {strategy!r}; "
            f"expected one of {PORT_ENCODING_STRATEGIES}"
        )

    encoding: dict[str, Any] = {"strategy": strategy, "top_ports": [], "top_n": top_n}
    if strategy == PORT_ENCODING_RAW or PORT_COLUMN not in frame.columns:
        return encoding

    counts = frame[PORT_COLUMN].value_counts().head(top_n)
    encoding["top_ports"] = [int(port) for port in counts.index]
    return encoding


def apply_port_encoding(frame: pd.DataFrame, encoding: dict[str, Any]) -> pd.DataFrame:
    """Apply a fitted port encoding. Column set depends only on the encoding."""
    if encoding.get("strategy") != PORT_ENCODING_BUCKETED:
        return frame
    if PORT_COLUMN not in frame.columns:
        return frame

    frame = frame.copy()
    ports = pd.to_numeric(frame[PORT_COLUMN], errors="coerce").fillna(-1).astype(int)

    groups = ports.map(service_group)
    for group in SERVICE_GROUPS:
        frame[f"port_group_{group}"] = (groups == group).astype("int8")

    for port in encoding.get("top_ports", []):
        frame[f"port_is_{port}"] = (ports == port).astype("int8")

    return frame.drop(columns=[PORT_COLUMN])


def _feature_frame(frame: pd.DataFrame, encoding: dict[str, Any] | None) -> pd.DataFrame:
    """Shared prelude: drop leakage and the label, encode the port, keep numerics."""
    reduced, _ = drop_leakage_columns(frame)
    if LABEL_COLUMN in reduced.columns:
        reduced = reduced.drop(columns=[LABEL_COLUMN])
    if encoding is not None:
        reduced = apply_port_encoding(reduced, encoding)
    return reduced.select_dtypes(include=["number", "bool"]).astype("float64")


def fit_preprocessing(
    frame: pd.DataFrame,
    port_encoding: str = PORT_ENCODING_RAW,
    top_n: int = DEFAULT_TOP_PORTS,
) -> PreprocessingBundle:
    """Fit the scaler and freeze the feature contract, on the training split.

    RobustScaler, not StandardScaler: flow features are heavy-tailed enough
    that a handful of enormous flows would flatten every other value.
    """
    from sklearn.preprocessing import RobustScaler

    encoding = fit_port_encoding(frame, strategy=port_encoding, top_n=top_n)
    features = _feature_frame(frame, encoding)

    _, dropped = drop_leakage_columns(frame)

    scaler = RobustScaler()
    scaler.fit(features)

    return build_preprocessing_bundle(
        scaler=scaler,
        feature_order=list(features.columns),
        dropped_columns=dropped,
        port_encoding=encoding,
    )


def build_feature_matrix(
    frame: pd.DataFrame, bundle: PreprocessingBundle | None = None
) -> pd.DataFrame:
    """Turn cleaned flow records into the model's input matrix.

    Called by training (to fit the scaler) and by serving (to apply it), which
    is what keeps the two paths identical.

    Without a bundle this returns the unscaled feature frame, which is what
    `fit_preprocessing` needs before a scaler exists. With one it reproduces
    the training-time matrix exactly: the same columns, in `feature_order`,
    scaled by the fitted `RobustScaler`. Columns absent from the input are
    filled with zero and unexpected ones are ignored, so the matrix width a
    trained model sees never changes.
    """
    if bundle is None:
        return _feature_frame(frame, None)

    features = _feature_frame(frame, bundle.get("port_encoding"))
    ordered = features.reindex(columns=bundle["feature_order"], fill_value=0.0)
    ordered = ordered.fillna(0.0)

    scaler = bundle.get("scaler")
    if scaler is None:
        return ordered

    scaled = scaler.transform(ordered)
    return pd.DataFrame(scaled, columns=bundle["feature_order"], index=ordered.index)
