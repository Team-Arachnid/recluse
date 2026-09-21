"""The shared feature contract.

`features.py` is imported by both training and serving. These tests pin the
parts of it that Phase 0 implements, because everything downstream -- the
schema-hash startup check especially -- depends on them being stable.
"""

from __future__ import annotations

import pytest

from training.features import (
    LEAKAGE_COLUMNS,
    PORT_COLUMN,
    build_preprocessing_bundle,
    compute_schema_hash,
    load_preprocessing_bundle,
    normalise_column_name,
    normalise_columns,
    save_preprocessing_bundle,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # The published CSVs really do ship headers with leading whitespace.
        (" Flow Duration", "flow_duration"),
        ("Flow Duration ", "flow_duration"),
        ("Flow Bytes/s", "flow_bytes_s"),
        ("Flow Packets/s", "flow_packets_s"),
        ("Fwd IAT Min", "fwd_iat_min"),
        ("Destination Port", "destination_port"),
        # CICIDS2017 has a genuine duplicate header disambiguated by a suffix.
        ("Fwd Header Length.1", "fwd_header_length_1"),
        ("Bwd PSH Flags", "bwd_psh_flags"),
        ("CWE Flag Count", "cwe_flag_count"),
        ("  Label  ", "label"),
    ],
)
def test_column_names_normalise_to_snake_case(raw: str, expected: str) -> None:
    assert normalise_column_name(raw) == expected


def test_normalise_columns_preserves_order() -> None:
    assert normalise_columns([" B", "A "]) == ["b", "a"]


def test_schema_hash_is_order_sensitive() -> None:
    """Order matters: a reordered matrix is a different matrix.

    This is the whole reason the hash exists -- feeding the right columns in
    the wrong order produces garbage scores and raises nothing.
    """
    assert compute_schema_hash(["a", "b"]) != compute_schema_hash(["b", "a"])


def test_schema_hash_is_stable_and_prefixed() -> None:
    first = compute_schema_hash(["flow_duration", "total_fwd_packets"])
    second = compute_schema_hash(["flow_duration", "total_fwd_packets"])

    assert first == second
    assert first.startswith("sha256:")


def test_identity_columns_are_on_the_leakage_denylist() -> None:
    for column in ("flow_id", "source_ip", "destination_ip", "source_port"):
        assert column in LEAKAGE_COLUMNS


def test_destination_port_is_not_silently_dropped() -> None:
    """It is predictive and a memorisation trap, so Phase 1 decides explicitly.

    Dropping it by default would hide the ablation the brief asks for.
    """
    assert PORT_COLUMN == "destination_port"
    assert PORT_COLUMN not in LEAKAGE_COLUMNS


def test_bundle_round_trips_with_a_matching_hash(tmp_path) -> None:
    feature_order = ["flow_duration", "total_fwd_packets", "flow_bytes_s"]
    bundle = build_preprocessing_bundle(
        scaler=None,
        feature_order=feature_order,
        dropped_columns=["bwd_psh_flags"],
        port_encoding={"strategy": "raw"},
    )

    assert bundle["schema_hash"] == compute_schema_hash(feature_order)

    path = save_preprocessing_bundle(bundle, tmp_path / "preprocessing.pkl")
    reloaded = load_preprocessing_bundle(path)

    assert reloaded["feature_order"] == feature_order
    assert reloaded["schema_hash"] == bundle["schema_hash"]
