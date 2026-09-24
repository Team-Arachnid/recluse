"""Phase 1 feature transforms -- the shared train/serve path.

`build_feature_matrix` is the one implementation both training and serving
call. If the two ever diverge, mismatched column order produces garbage scores
and raises nothing, so the tests that matter most here are the ones pinning
that a bundle reproduces its training-time matrix exactly.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from sklearn.preprocessing import RobustScaler

from training.features import (
    PORT_ENCODING_BUCKETED,
    PORT_ENCODING_RAW,
    SERVICE_GROUPS,
    apply_port_encoding,
    build_feature_matrix,
    compute_schema_hash,
    drop_leakage_columns,
    fit_port_encoding,
    fit_preprocessing,
)


@pytest.fixture
def train_frame() -> pd.DataFrame:
    """A small cleaned-looking training frame."""
    return pd.DataFrame(
        {
            "flow_id": ["a", "b", "c", "d"],
            "source_ip": ["10.0.0.1"] * 4,
            "destination_ip": ["10.0.0.2"] * 4,
            "source_port": [40001, 40002, 40003, 40004],
            "destination_port": [80, 443, 8080, 53000],
            "flow_duration": [100, 200, 300, 400],
            "flow_bytes_s": [10.0, 20.0, 30.0, 4000.0],
            "label": ["BENIGN", "DDoS", "BENIGN", "PortScan"],
        }
    )


# --------------------------------------------------------------------------
# Leakage
# --------------------------------------------------------------------------
def test_identity_columns_are_dropped(train_frame: pd.DataFrame) -> None:
    reduced, dropped = drop_leakage_columns(train_frame)

    for column in ("flow_id", "source_ip", "destination_ip", "source_port"):
        assert column not in reduced.columns
        assert column in dropped


def test_destination_port_survives_the_leakage_drop(train_frame: pd.DataFrame) -> None:
    """It is decided by the port ablation, not by the deny-list."""
    reduced, _ = drop_leakage_columns(train_frame)

    assert "destination_port" in reduced.columns


def test_label_is_never_a_feature(train_frame: pd.DataFrame) -> None:
    bundle = fit_preprocessing(train_frame, port_encoding=PORT_ENCODING_RAW)

    assert "label" not in bundle["feature_order"]


# --------------------------------------------------------------------------
# Port encoding
# --------------------------------------------------------------------------
def test_raw_encoding_keeps_the_port_as_one_numeric_column(
    train_frame: pd.DataFrame,
) -> None:
    encoding = fit_port_encoding(train_frame, strategy=PORT_ENCODING_RAW)
    encoded = apply_port_encoding(train_frame, encoding)

    assert encoding["strategy"] == PORT_ENCODING_RAW
    assert "destination_port" in encoded.columns


def test_bucketed_encoding_replaces_the_raw_port(train_frame: pd.DataFrame) -> None:
    encoding = fit_port_encoding(train_frame, strategy=PORT_ENCODING_BUCKETED)
    encoded = apply_port_encoding(train_frame, encoding)

    assert "destination_port" not in encoded.columns
    for group in SERVICE_GROUPS:
        assert f"port_group_{group}" in encoded.columns


def test_bucketed_encoding_one_hots_the_top_ports(train_frame: pd.DataFrame) -> None:
    encoding = fit_port_encoding(train_frame, strategy=PORT_ENCODING_BUCKETED, top_n=2)
    encoded = apply_port_encoding(train_frame, encoding)

    assert len(encoding["top_ports"]) == 2
    for port in encoding["top_ports"]:
        assert f"port_is_{port}" in encoded.columns


def test_service_groups_classify_by_port_range(train_frame: pd.DataFrame) -> None:
    encoding = fit_port_encoding(train_frame, strategy=PORT_ENCODING_BUCKETED)
    encoded = apply_port_encoding(train_frame, encoding)

    # 80 is well-known, 8080 registered, 53000 ephemeral.
    assert encoded.loc[0, "port_group_well_known"] == 1
    assert encoded.loc[2, "port_group_registered"] == 1
    assert encoded.loc[3, "port_group_ephemeral"] == 1


def test_top_ports_are_fitted_on_training_data_only(train_frame: pd.DataFrame) -> None:
    """A port that only appears at serve time must not create a new column.

    Fitting the encoding on whatever frame arrives is how test-set information
    reaches training; the columns are fixed once, at fit time.
    """
    encoding = fit_port_encoding(train_frame, strategy=PORT_ENCODING_BUCKETED, top_n=2)

    serving = pd.DataFrame({"destination_port": [9999, 9999, 9999], "flow_duration": [1, 2, 3]})
    encoded = apply_port_encoding(serving, encoding)

    assert "port_is_9999" not in encoded.columns
    assert all(f"port_is_{port}" in encoded.columns for port in encoding["top_ports"])


def test_an_unknown_strategy_is_rejected(train_frame: pd.DataFrame) -> None:
    with pytest.raises(ValueError, match="strategy"):
        fit_port_encoding(train_frame, strategy="one-hot-everything")


# --------------------------------------------------------------------------
# The bundle
# --------------------------------------------------------------------------
def test_bundle_carries_all_five_keys(train_frame: pd.DataFrame) -> None:
    """Three is the common shortcut and it is not enough to rebuild the matrix."""
    bundle = fit_preprocessing(train_frame, port_encoding=PORT_ENCODING_RAW)

    for key in (
        "scaler",
        "feature_order",
        "dropped_columns",
        "port_encoding",
        "schema_hash",
    ):
        assert key in bundle


def test_bundle_hash_matches_its_feature_order(train_frame: pd.DataFrame) -> None:
    bundle = fit_preprocessing(train_frame, port_encoding=PORT_ENCODING_RAW)

    assert bundle["schema_hash"] == compute_schema_hash(bundle["feature_order"])


def test_the_scaler_is_robust_not_standard(train_frame: pd.DataFrame) -> None:
    """Flow features are heavy-tailed; StandardScaler flattens them."""
    bundle = fit_preprocessing(train_frame, port_encoding=PORT_ENCODING_RAW)

    assert isinstance(bundle["scaler"], RobustScaler)


# --------------------------------------------------------------------------
# The matrix
# --------------------------------------------------------------------------
def test_matrix_columns_are_exactly_the_feature_order(
    train_frame: pd.DataFrame,
) -> None:
    bundle = fit_preprocessing(train_frame, port_encoding=PORT_ENCODING_BUCKETED)
    matrix = build_feature_matrix(train_frame, bundle)

    assert list(matrix.columns) == bundle["feature_order"]


def test_a_missing_column_is_filled_rather_than_reordering_the_rest(
    train_frame: pd.DataFrame,
) -> None:
    """Serving may legitimately be handed a frame missing an optional column."""
    bundle = fit_preprocessing(train_frame, port_encoding=PORT_ENCODING_RAW)

    partial = train_frame.drop(columns=["flow_bytes_s"])
    matrix = build_feature_matrix(partial, bundle)

    assert list(matrix.columns) == bundle["feature_order"]
    assert not matrix.isna().to_numpy().any()


def test_an_unexpected_column_is_ignored(train_frame: pd.DataFrame) -> None:
    bundle = fit_preprocessing(train_frame, port_encoding=PORT_ENCODING_RAW)

    extra = train_frame.copy()
    extra["some_new_counter"] = 1

    matrix = build_feature_matrix(extra, bundle)

    assert list(matrix.columns) == bundle["feature_order"]


def test_serving_reproduces_the_training_matrix_exactly(
    train_frame: pd.DataFrame,
) -> None:
    """The train/serve skew guard.

    A row scored at serve time must produce the same vector it produced during
    training. If this drifts, scores go wrong silently.
    """
    bundle = fit_preprocessing(train_frame, port_encoding=PORT_ENCODING_BUCKETED)

    training_matrix = build_feature_matrix(train_frame, bundle)
    one_row = build_feature_matrix(train_frame.iloc[[2]], bundle)

    np.testing.assert_allclose(one_row.to_numpy(), training_matrix.iloc[[2]].to_numpy())


def test_column_order_is_honoured_even_when_the_input_is_shuffled(
    train_frame: pd.DataFrame,
) -> None:
    """Feeding the right columns in the wrong order is the silent failure."""
    bundle = fit_preprocessing(train_frame, port_encoding=PORT_ENCODING_RAW)

    expected = build_feature_matrix(train_frame, bundle)
    reversed_input = train_frame[list(reversed(train_frame.columns))]
    actual = build_feature_matrix(reversed_input, bundle)

    np.testing.assert_allclose(actual.to_numpy(), expected.to_numpy())


def test_matrix_is_finite(train_frame: pd.DataFrame) -> None:
    bundle = fit_preprocessing(train_frame, port_encoding=PORT_ENCODING_RAW)
    matrix = build_feature_matrix(train_frame, bundle)

    assert np.isfinite(matrix.to_numpy()).all()


def test_matrix_without_a_bundle_returns_unscaled_features(
    train_frame: pd.DataFrame,
) -> None:
    """The fit path needs the feature frame before a scaler exists."""
    matrix = build_feature_matrix(train_frame)

    assert "label" not in matrix.columns
    assert "source_ip" not in matrix.columns
