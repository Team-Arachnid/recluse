"""Phase 5 -- the two per-alert explainers and the narrator.

`explain_supervised` and `explain_anomaly` are tested against a real promoted
Stage 1 (RF) and a real fitted Stage 2, built by the `two_stage_artifacts`
fixture from the small synthetic frames -- not against a mock, so a test of
the explainer is a test of what the API actually loads. `narrate` is tested
against hand-built contributor lists, because its contract is about English
output shape and register, not about either model.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.explain import FEATURE_PHRASES, explain_anomaly, explain_supervised, narrate
from app.inference import PREPROCESSING_FILE, load_bundle
from app.models import ALERT_FAMILIES
from training.autoencoder import per_feature_error, top_contributors
from training.features import build_feature_matrix


@pytest.fixture
def bundle(two_stage_artifacts):
    """A loaded bundle over the real promoted Stage 1 and fitted Stage 2."""
    return load_bundle(two_stage_artifacts)


def _alerting_rows(bundle, frame, limit: int) -> tuple[np.ndarray, list[str]]:
    """The rows of `frame` Stage 1 would actually alert on, plus their families.

    Mirrors how Task 6's pipeline will call these functions: only the rows
    Stage 1 did not call benign, never the whole scored batch.
    """
    matrix = build_feature_matrix(frame, bundle.preprocessing).to_numpy(dtype="float32")
    predicted = bundle.supervised.predict(matrix)
    alerting = predicted != "benign"
    assert alerting.any(), "fixture must produce at least one non-benign prediction"
    return matrix[alerting][:limit], list(predicted[alerting][:limit])


# ---------------------------------------------------------------------------
# Stage 1: TreeSHAP
# ---------------------------------------------------------------------------


def test_explain_supervised_orders_by_magnitude_and_shares_the_full_attribution(
    bundle, phase2_train
) -> None:
    """Five contributors, descending by |contribution|, every feature a real name.

    The share denominator is asserted against the FULL d-length attribution
    computed independently here, not against the 5 contributors returned --
    summed over all d features (not the 5 in the record) shares are exactly 1
    by construction, which is the property this test pins rather than assumes.
    The top-5 alone are a subset of that mass, so they sum to strictly less.
    """
    import shap

    alert_matrix, families = _alerting_rows(bundle, phase2_train, limit=8)

    records = explain_supervised(
        bundle.supervised, alert_matrix, bundle.feature_order, bundle.supervised_classes, families
    )

    assert len(records) == len(families)

    unwrapped = (
        bundle.supervised.booster if hasattr(bundle.supervised, "booster") else bundle.supervised
    )
    raw = shap.TreeExplainer(unwrapped).shap_values(alert_matrix)
    raw = np.stack(raw, axis=-1) if isinstance(raw, list) else np.asarray(raw)

    for row_index, (record, family) in enumerate(zip(records, families, strict=True)):
        assert record["explainer"] == "treeshap"
        contributors = record["contributors"]
        assert len(contributors) == 5
        assert all(c["feature"] in bundle.feature_order for c in contributors)

        magnitudes = [abs(c["contribution"]) for c in contributors]
        assert magnitudes == sorted(magnitudes, reverse=True)

        class_index = bundle.supervised_classes.index(family)
        full_row = raw[row_index, :, class_index]
        total_abs = np.abs(full_row).sum()
        full_shares = np.abs(full_row) / total_abs
        # The property itself: shares over the FULL attribution sum to 1.
        assert full_shares.sum() == pytest.approx(1.0)

        for contributor in contributors:
            feature_index = bundle.feature_order.index(contributor["feature"])
            assert contributor["contribution"] == pytest.approx(full_row[feature_index])
            assert contributor["share"] == pytest.approx(full_shares[feature_index])

        # The returned top-5 is a strict subset of the full attribution's mass.
        assert sum(c["share"] for c in contributors) <= 1.0 + 1e-9


def test_explain_supervised_refuses_a_shape_it_does_not_recognise(bundle, phase2_train) -> None:
    """A `feature_order` of the wrong length is the easiest honest way to force
    a shape shap did not produce, and the resulting ValueError must say so."""
    alert_matrix, families = _alerting_rows(bundle, phase2_train, limit=2)
    wrong_feature_order = bundle.feature_order[:-1]

    with pytest.raises(ValueError, match="shape"):
        explain_supervised(
            bundle.supervised,
            alert_matrix,
            wrong_feature_order,
            bundle.supervised_classes,
            families,
        )


def test_explain_supervised_refuses_a_missing_family(bundle, phase2_train) -> None:
    """family=None means the caller handed a Stage 2 row to the Stage 1
    explainer; attributing against an arbitrary column would hide that."""
    matrix = build_feature_matrix(phase2_train, bundle.preprocessing).to_numpy(dtype="float32")[:2]

    with pytest.raises(ValueError, match="family"):
        explain_supervised(
            bundle.supervised,
            matrix,
            bundle.feature_order,
            bundle.supervised_classes,
            [None, "dos"],
        )


def test_explain_supervised_refuses_a_family_count_mismatch(bundle, phase2_train) -> None:
    """One family per row is the contract; a silent short list would explain
    fewer rows than were asked for without anything downstream noticing."""
    matrix = build_feature_matrix(phase2_train, bundle.preprocessing).to_numpy(dtype="float32")[:3]

    with pytest.raises(ValueError, match="row"):
        explain_supervised(
            bundle.supervised, matrix, bundle.feature_order, bundle.supervised_classes, ["dos"]
        )


def test_explain_supervised_on_an_empty_batch_returns_no_rows_without_raising(bundle) -> None:
    empty = np.empty((0, len(bundle.feature_order)), dtype="float32")

    assert (
        explain_supervised(
            bundle.supervised, empty, bundle.feature_order, bundle.supervised_classes, []
        )
        == []
    )


# ---------------------------------------------------------------------------
# Stage 2: per-feature reconstruction error
# ---------------------------------------------------------------------------


def test_explain_anomaly_contributors_are_the_worst_reconstructed_features(
    bundle, phase2_test
) -> None:
    """Pinned against `per_feature_error` computed directly, so this test
    checks the reuse rather than re-deriving the formula a second time."""
    matrix = build_feature_matrix(phase2_test, bundle.preprocessing).to_numpy(dtype="float32")[:6]

    records = explain_anomaly(bundle.autoencoder, matrix, bundle.feature_order)

    errors = per_feature_error(bundle.autoencoder, matrix)
    assert len(records) == len(errors)
    for row_errors, record in zip(errors, records, strict=True):
        assert record["explainer"] == "reconstruction_error"
        assert record["contributors"] == top_contributors(row_errors, bundle.feature_order)


def test_explain_anomaly_on_an_empty_batch_returns_no_rows_without_raising(bundle) -> None:
    empty = np.empty((0, len(bundle.feature_order)), dtype="float32")

    assert explain_anomaly(bundle.autoencoder, empty, bundle.feature_order) == []


# ---------------------------------------------------------------------------
# The narrator
# ---------------------------------------------------------------------------

_KNOWN_CONTRIBUTORS = [
    {"feature": "flow_duration", "value": -1.4, "contribution": 0.62, "share": 0.5},
    {"feature": "total_fwd_packets", "value": -1.1, "contribution": 0.37, "share": 0.3},
    {"feature": "port_is_445", "value": 1.0, "contribution": 0.25, "share": 0.2},
]

_ANOMALY_CONTRIBUTORS = [
    {"feature": "active_std", "error": 12.0, "share": 0.4},
    {"feature": "down_up_ratio", "error": 9.0, "share": 0.3},
    {"feature": "fwd_packet_length_mean", "error": 6.0, "share": 0.2},
]


def test_narrate_known_names_the_family_and_is_not_empty() -> None:
    sentence = narrate("KNOWN", "port_scan", _KNOWN_CONTRIBUTORS)

    assert sentence
    assert "port scan" in sentence


def test_narrate_anomaly_does_not_name_a_family_or_say_technique() -> None:
    sentence = narrate("UNCLASSIFIED_ANOMALY", None, _ANOMALY_CONTRIBUTORS)

    assert sentence
    assert "technique" not in sentence.lower()
    for family in ALERT_FAMILIES:
        assert family.replace("_", " ") not in sentence.lower()


@pytest.mark.parametrize("kind,family", [("KNOWN", "dos"), ("UNCLASSIFIED_ANOMALY", None)])
def test_narrate_handles_no_contributors_without_raising(kind, family) -> None:
    sentence = narrate(kind, family, [])

    assert sentence
    assert "no explanation" in sentence.lower()


def test_narrate_known_requires_a_family() -> None:
    with pytest.raises(ValueError, match="family"):
        narrate("KNOWN", None, _KNOWN_CONTRIBUTORS)


def test_narrate_rejects_an_unknown_kind() -> None:
    with pytest.raises(ValueError, match="kind"):
        narrate("SOMETHING_ELSE", None, _KNOWN_CONTRIBUTORS)


def test_narrate_falls_back_to_a_humanised_name_for_an_unmapped_feature() -> None:
    contributors = [
        {"feature": "totally_made_up_feature", "value": 1.0, "contribution": 0.9, "share": 1.0}
    ]

    sentence = narrate("KNOWN", "dos", contributors)

    assert "totally made up feature" in sentence


def test_narrate_mentions_the_observed_port_when_a_port_feature_contributed() -> None:
    sentence = narrate("KNOWN", "port_scan", _KNOWN_CONTRIBUTORS, flow={"destination_port": 445})

    assert "445" in sentence


def test_narrate_omits_the_port_clause_when_no_port_feature_contributed() -> None:
    contributors = [{"feature": "flow_duration", "value": -1.0, "contribution": 0.9, "share": 1.0}]

    sentence = narrate("KNOWN", "dos", contributors, flow={"destination_port": 12345})

    assert "12345" not in sentence


# ---------------------------------------------------------------------------
# The phrase map's coverage of the real feature vocabulary
# ---------------------------------------------------------------------------


def test_every_real_feature_has_a_phrase_map_entry() -> None:
    """The persisted feature_order is the ground truth for what narrate() has
    to be able to talk about. backend/artifacts/ is gitignored and absent in
    CI, so this skips rather than failing when there is no bundle to check."""
    from app.config import settings

    if not (settings.artifacts_path / PREPROCESSING_FILE).exists():
        pytest.skip("no local artifact bundle to check the phrase map against")

    bundle = load_bundle(settings.artifacts_path)
    missing = [name for name in bundle.feature_order if name not in FEATURE_PHRASES]

    assert not missing, f"no phrase-map entry for: {missing}"
