"""Phase 1 end to end: raw CSV shape in, splits and a bundle out.

The individual stages have their own tests. This module pins the phase's
acceptance criteria on the pipeline as a whole, because those are properties
of the output files rather than of any one function.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from training.clean import clean_frame
from training.features import (
    PORT_ENCODING_BUCKETED,
    PORT_ENCODING_RAW,
    build_feature_matrix,
    compute_schema_hash,
    load_preprocessing_bundle,
)
from training.preprocess import fit_and_save, run_pipeline
from training.split import BENIGN_LABEL, split_frames


@pytest.fixture
def pipeline(tmp_path, raw_cicids_frame: pd.DataFrame):
    """Run the whole phase into a temporary tree."""
    return run_pipeline(
        raw_cicids_frame,
        processed_dir=tmp_path / "processed",
        artifacts_dir=tmp_path / "artifacts",
        port_encoding=PORT_ENCODING_RAW,
    )


def test_pipeline_writes_every_promised_artifact(pipeline) -> None:
    for name in ("train", "val", "test", "benign_train"):
        assert (pipeline.processed_dir / f"{name}.parquet").exists()

    assert (pipeline.artifacts_dir / "preprocessing.pkl").exists()


def test_no_nan_or_inf_survives_into_processed(pipeline) -> None:
    """An explicit acceptance criterion for the phase."""
    for name in ("train", "val", "test", "benign_train"):
        frame = pd.read_parquet(pipeline.processed_dir / f"{name}.parquet")
        if frame.empty:
            continue
        numeric = frame.select_dtypes(include=[np.number])
        assert not numeric.isna().to_numpy().any(), name
        assert np.isfinite(numeric.to_numpy()).all(), name


def test_no_rows_are_shared_between_splits(pipeline) -> None:
    """Acceptance criterion: zero duplicate rows across splits."""
    frames = {
        name: pd.read_parquet(pipeline.processed_dir / f"{name}.parquet")
        for name in ("train", "val", "test")
    }

    def keys(frame: pd.DataFrame) -> set[tuple]:
        return set(map(tuple, frame.astype(str).to_numpy()))

    train, val, test = (keys(frames[n]) for n in ("train", "val", "test"))

    assert not (train & val)
    assert not (train & test)
    assert not (val & test)


def test_benign_training_set_is_attack_free_on_disk(pipeline) -> None:
    frame = pd.read_parquet(pipeline.processed_dir / "benign_train.parquet")

    assert set(frame["label"]) == {BENIGN_LABEL}


def test_leakage_columns_are_dropped_and_recorded(pipeline) -> None:
    """Acceptance criterion: IP, port and timestamp leakage dropped and logged."""
    bundle = load_preprocessing_bundle(pipeline.artifacts_dir / "preprocessing.pkl")

    for column in ("flow_id", "source_ip", "destination_ip", "source_port"):
        assert column in bundle["dropped_columns"]

    for column in ("timestamp", "label", *bundle["dropped_columns"]):
        assert column not in bundle["feature_order"]


def test_bundle_on_disk_carries_all_five_keys(pipeline) -> None:
    bundle = load_preprocessing_bundle(pipeline.artifacts_dir / "preprocessing.pkl")

    assert set(bundle) == {
        "scaler",
        "feature_order",
        "dropped_columns",
        "port_encoding",
        "schema_hash",
    }
    assert bundle["schema_hash"] == compute_schema_hash(bundle["feature_order"])


def test_the_bundle_reproduces_its_matrix_after_a_round_trip(pipeline) -> None:
    """What the startup schema check is protecting."""
    bundle = load_preprocessing_bundle(pipeline.artifacts_dir / "preprocessing.pkl")
    train = pd.read_parquet(pipeline.processed_dir / "train.parquet")

    matrix = build_feature_matrix(train, bundle)

    assert list(matrix.columns) == bundle["feature_order"]
    assert np.isfinite(matrix.to_numpy()).all()


def test_report_counts_rows_per_split_per_class(pipeline) -> None:
    counts = pipeline.split_report.counts

    assert counts["train"]
    assert counts["test"]
    assert all(isinstance(v, int) for v in counts["train"].values())


def test_both_port_encodings_are_available_for_the_phase_2_ablation(
    tmp_path, raw_cicids_frame: pd.DataFrame
) -> None:
    """Phase 1 defines both encodings; Phase 2 runs the comparison."""
    cleaned, _ = clean_frame(raw_cicids_frame)
    result = split_frames(cleaned)

    raw_bundle, _ = fit_and_save(result.train, tmp_path / "raw", port_encoding=PORT_ENCODING_RAW)
    bucketed_bundle, _ = fit_and_save(
        result.train, tmp_path / "bucketed", port_encoding=PORT_ENCODING_BUCKETED
    )

    assert raw_bundle["port_encoding"]["strategy"] == PORT_ENCODING_RAW
    assert bucketed_bundle["port_encoding"]["strategy"] == PORT_ENCODING_BUCKETED
    assert raw_bundle["schema_hash"] != bucketed_bundle["schema_hash"]
    assert "destination_port" in raw_bundle["feature_order"]
    assert "destination_port" not in bucketed_bundle["feature_order"]


def test_the_scaler_is_fitted_on_train_only(tmp_path, raw_cicids_frame) -> None:
    """Fitting on all the data leaks test-set distribution into training."""
    cleaned, _ = clean_frame(raw_cicids_frame)
    result = split_frames(cleaned)

    from_train, _ = fit_and_save(result.train, tmp_path / "a")
    from_everything, _ = fit_and_save(
        pd.concat([result.train, result.test], ignore_index=True), tmp_path / "b"
    )

    assert not np.allclose(from_train["scaler"].center_, from_everything["scaler"].center_)


def test_pipeline_renders_a_checkpoint_report(pipeline) -> None:
    rendered = pipeline.render()

    assert "rows in" in rendered
    assert "train" in rendered
    assert "schema hash" in rendered
