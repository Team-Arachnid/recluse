"""Phase 4 -- the rule that sequences the two stages.

The cascade is five lines of the brief, and every property pinned below is a
way of getting those five lines subtly wrong that no dashboard would ever show
you: an exclusive comparison where the threshold arithmetic used an inclusive
one, a Stage 2 score reported for a row Stage 2 was never asked about, a KNOWN
alert whose family was read off the benign column, a row counted twice because
both stages claimed it.

The tests work on probability matrices directly rather than through a trained
model. That is deliberate: the cascade has no opinion about where `proba` came
from, and a test that trains a forest to produce one number would be measuring
the forest.
"""

from __future__ import annotations

import numpy as np
import pytest

from training.fusion import (
    KIND_KNOWN,
    KIND_UNCLASSIFIED_ANOMALY,
    STAGE1,
    STAGE2,
    fuse,
)

# Column order of every `proba` below. Benign first, exactly as
# `training.labels.vocabulary` orders it.
CLASSES = ["benign", "dos", "brute_force"]

TAU_SUP = 0.40
TAU_ANOM = 0.10


def rows(*triples: tuple[float, float, float]) -> np.ndarray:
    return np.array(triples, dtype="float64")


# ---------------------------------------------------------------------------
# Stage 1: the named branch
# ---------------------------------------------------------------------------


def test_a_confident_stage1_row_becomes_a_named_known_alert() -> None:
    decisions = fuse(
        rows((0.1, 0.85, 0.05)), CLASSES, TAU_SUP, anomaly_score=np.array([0.0]), tau_anom=TAU_ANOM
    )

    assert decisions.kind[0] == KIND_KNOWN
    assert decisions.family[0] == "dos"
    assert decisions.stage[0] == STAGE1
    assert decisions.confidence[0] == pytest.approx(0.85)


def test_the_family_is_the_best_attack_class_and_never_benign() -> None:
    """Benign is a column of `predict_proba` like any other.

    A row that is 60% benign and 30% DoS is still a DoS flow if it clears the
    threshold, and reading the family off `argmax` over all three columns would
    label it `benign` -- a KNOWN alert naming benign as the attack.
    """
    decisions = fuse(rows((0.60, 0.30, 0.10)), CLASSES, tau_sup=0.25)

    assert decisions.kind[0] == KIND_KNOWN
    assert decisions.family[0] == "dos"


def test_confidence_is_the_largest_single_attack_class_not_their_sum() -> None:
    """A flow split evenly across two families is one Stage 1 cannot *name*.

    `1 - P(benign)` would score this row 0.5 and emit a confident alert for
    whichever family won a coin toss. The brief's quantity scores it 0.25, and
    the right destination for a row Stage 1 cannot name is Stage 2.
    """
    decisions = fuse(
        rows((0.50, 0.25, 0.25)),
        CLASSES,
        TAU_SUP,
        anomaly_score=np.array([0.5]),
        tau_anom=TAU_ANOM,
    )

    assert decisions.confidence[0] == pytest.approx(0.25)
    assert decisions.kind[0] == KIND_UNCLASSIFIED_ANOMALY


# ---------------------------------------------------------------------------
# Stage 2: the branch the project is named for
# ---------------------------------------------------------------------------


def test_a_row_stage1_cannot_name_falls_through_to_stage2() -> None:
    decisions = fuse(
        rows((0.80, 0.15, 0.05)),
        CLASSES,
        TAU_SUP,
        anomaly_score=np.array([0.9]),
        tau_anom=TAU_ANOM,
    )

    assert decisions.kind[0] == KIND_UNCLASSIFIED_ANOMALY
    assert decisions.stage[0] == STAGE2
    assert decisions.anomaly_score[0] == pytest.approx(0.9)


def test_an_unclassified_anomaly_carries_no_family() -> None:
    """Mirrors the `family_matches_kind` constraint on the alerts table.

    Stage 2 has no class vocabulary -- it answers "how unlike normal is this",
    not "which attack is this" -- so inventing a family for its alerts would be
    the one dishonesty the whole stage exists to avoid.
    """
    decisions = fuse(
        rows((0.80, 0.15, 0.05)),
        CLASSES,
        TAU_SUP,
        anomaly_score=np.array([0.9]),
        tau_anom=TAU_ANOM,
    )

    assert decisions.family[0] is None


def test_a_row_below_both_thresholds_produces_no_alert() -> None:
    decisions = fuse(
        rows((0.95, 0.03, 0.02)),
        CLASSES,
        TAU_SUP,
        anomaly_score=np.array([0.01]),
        tau_anom=TAU_ANOM,
    )

    assert decisions.kind[0] is None
    assert decisions.family[0] is None
    assert decisions.stage[0] is None
    assert not decisions.alerted.any()


# ---------------------------------------------------------------------------
# The seams
# ---------------------------------------------------------------------------


def test_both_thresholds_are_inclusive() -> None:
    """`select_threshold` measures its false-positive rate with `benign >= tau`
    and `select_anomaly_threshold` takes a percentile the same way. An exclusive
    comparison here would make the served operating point differ from the
    measured one by exactly the rows sitting on the threshold -- a discrepancy
    nothing in the system would report.
    """
    on_tau_sup = fuse(rows((0.6, TAU_SUP, 0.0)), CLASSES, TAU_SUP)
    on_tau_anom = fuse(
        rows((1.0, 0.0, 0.0)),
        CLASSES,
        TAU_SUP,
        anomaly_score=np.array([TAU_ANOM]),
        tau_anom=TAU_ANOM,
    )

    assert on_tau_sup.kind[0] == KIND_KNOWN
    assert on_tau_anom.kind[0] == KIND_UNCLASSIFIED_ANOMALY


def test_stage2_is_not_consulted_for_a_row_stage1_already_named() -> None:
    """The cascade is not an optimisation, it is the semantics.

    A row Stage 1 named is a KNOWN alert whatever its reconstruction error, and
    recording that error against the alert would invite someone to re-rank
    KNOWN alerts by a number the decision never used. `NaN` says *not asked*;
    `0.0` would say *reconstructs perfectly*.
    """
    decisions = fuse(
        rows((0.05, 0.90, 0.05)),
        CLASSES,
        TAU_SUP,
        anomaly_score=np.array([99.0]),
        tau_anom=TAU_ANOM,
    )

    assert decisions.kind[0] == KIND_KNOWN
    assert np.isnan(decisions.anomaly_score[0])


def test_the_two_stages_never_claim_the_same_row() -> None:
    """What makes the hold-out table's two columns add up rather than overlap."""
    proba = rows(
        (0.05, 0.90, 0.05),  # stage 1
        (0.80, 0.15, 0.05),  # stage 2
        (0.99, 0.01, 0.00),  # neither
    )
    decisions = fuse(
        proba, CLASSES, TAU_SUP, anomaly_score=np.array([5.0, 5.0, 0.001]), tau_anom=TAU_ANOM
    )

    assert not (decisions.known & decisions.anomalous).any()
    assert decisions.counts() == {
        "rows": 3,
        "known": 1,
        "unclassified_anomaly": 1,
        "alerts": 2,
        "clear": 1,
    }


def test_a_model_with_no_attack_class_cannot_raise_a_known_alert() -> None:
    """A degenerate vocabulary must not produce a KNOWN alert with a null family.

    `attack_confidence` returns zeros when there is no attack column, so a
    threshold of zero would otherwise mark every row KNOWN and hand the
    database a row its `family_matches_kind` constraint rejects.
    """
    decisions = fuse(np.array([[1.0]]), ["benign"], tau_sup=0.0)

    assert decisions.kind[0] is None


# ---------------------------------------------------------------------------
# Degradation: the phases land in order and a bundle may hold only one stage
# ---------------------------------------------------------------------------


def test_fusion_runs_on_stage1_alone() -> None:
    decisions = fuse(rows((0.80, 0.15, 0.05), (0.05, 0.90, 0.05)), CLASSES, TAU_SUP)

    assert list(decisions.kind) == [None, KIND_KNOWN]
    assert np.isnan(decisions.anomaly_score).all()


def test_every_row_reaches_stage2_when_stage1_is_absent() -> None:
    """A bundle carrying only `autoencoder.pt` still detects, and says so.

    Confidence is `NaN` rather than zero: nothing measured it, and zero would
    read as "Stage 1 was certain this was benign".
    """
    decisions = fuse(None, None, None, anomaly_score=np.array([0.5, 0.001]), tau_anom=TAU_ANOM)

    assert list(decisions.kind) == [KIND_UNCLASSIFIED_ANOMALY, None]
    assert np.isnan(decisions.confidence).all()


def test_fusion_with_neither_stage_loaded_is_refused() -> None:
    with pytest.raises(ValueError, match="no stage"):
        fuse(None, None, None)


def test_a_proba_matrix_that_disagrees_with_its_class_list_is_refused() -> None:
    """The column order of `predict_proba` *is* the class order. A width
    mismatch means a column is being read as the wrong family, which produces
    confidently mislabelled alerts and raises nothing."""
    with pytest.raises(ValueError, match="columns"):
        fuse(rows((0.5, 0.5, 0.0)), ["benign", "dos"], TAU_SUP)


def test_an_anomaly_score_of_a_different_length_is_refused() -> None:
    with pytest.raises(ValueError, match="scoring the same batch"):
        fuse(
            rows((0.5, 0.5, 0.0)),
            CLASSES,
            TAU_SUP,
            anomaly_score=np.array([1.0, 2.0]),
            tau_anom=TAU_ANOM,
        )


# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------


def test_records_are_aligned_with_the_input_and_json_safe() -> None:
    """One record per flow scored, in the order they arrived, with `NaN`
    rendered as null -- `NaN` is not valid JSON and a serialiser that emits it
    produces a response no browser will parse."""
    proba = rows((0.05, 0.90, 0.05), (0.80, 0.15, 0.05), (0.99, 0.01, 0.00))
    decisions = fuse(
        proba, CLASSES, TAU_SUP, anomaly_score=np.array([5.0, 5.0, 0.001]), tau_anom=TAU_ANOM
    )

    records = decisions.as_records()

    assert [record["kind"] for record in records] == [
        KIND_KNOWN,
        KIND_UNCLASSIFIED_ANOMALY,
        None,
    ]
    assert records[0]["anomaly_score"] is None
    assert records[1]["family"] is None
    assert records[1]["detection_stage"] == STAGE2
    assert records[2]["confidence"] == pytest.approx(0.01)


def test_the_kinds_and_stages_match_the_wire_contract() -> None:
    """`training/` stays free of SQLAlchemy, so the vocabulary is written twice.

    The database rejects any other spelling at insert time, which would turn a
    typo here into a Phase 5 integrity error on the first real alert.
    """
    from app.models import ALERT_KINDS, DETECTION_STAGES

    assert (KIND_KNOWN, KIND_UNCLASSIFIED_ANOMALY) == ALERT_KINDS
    assert (STAGE1, STAGE2) == DETECTION_STAGES


# ---------------------------------------------------------------------------
# The serving path
#
# `ModelBundle.score_batch` is the only place a flow record becomes a decision
# in production, and it is the only place train/serve skew can enter. These run
# against artifacts written by the real Phase 2 and Phase 3 code.
# ---------------------------------------------------------------------------


@pytest.fixture
def loaded(two_stage_artifacts):
    from app.inference import load_bundle

    bundle = load_bundle(two_stage_artifacts)
    assert bundle.stage1_ready and bundle.stage2_ready
    return bundle


def test_score_batch_returns_one_record_per_flow_in_order(loaded, phase2_test) -> None:
    flows = phase2_test.head(50).to_dict("records")

    records = loaded.score_batch(flows)

    assert len(records) == 50
    assert all(set(record) >= {"kind", "family", "detection_stage"} for record in records)


def test_score_batch_stamps_every_record_with_the_model_version(loaded, phase2_test) -> None:
    """Which model version scored which alert is non-negotiable for anything
    security-adjacent, and the only place that provenance can be attached
    without guessing is the call that produced the score."""
    records = loaded.score_batch(phase2_test.head(5).to_dict("records"))

    assert {record["model_version"] for record in records} == {loaded.version}


def test_the_serving_path_and_the_training_path_produce_the_same_scores(
    loaded, phase2_test
) -> None:
    """The feature-parity assertion, made against real artifacts.

    A second implementation of the transforms inside the API is the train/serve
    skew failure mode, and it is silent: the model still returns a probability,
    it is just a probability about a different flow. This scores the same rows
    twice -- once as loose dicts through `score_batch`, once as a frame through
    `build_feature_matrix` the way training does -- and requires the numbers to
    agree.
    """
    from training.features import build_feature_matrix
    from training.metrics import attack_confidence

    frame = phase2_test.head(200)
    matrix = build_feature_matrix(frame, loaded.preprocessing).to_numpy(dtype="float32")
    expected = attack_confidence(loaded.supervised.predict_proba(matrix), loaded.supervised_classes)

    served = loaded.score_batch(frame.to_dict("records"))

    assert [record["confidence"] for record in served] == pytest.approx(list(expected))


def test_a_flow_whose_keys_arrive_shuffled_scores_identically(loaded, phase2_test) -> None:
    """JSON objects have no column order, so the matrix builder must impose one.

    Reordered keys reaching a model as reordered columns is the exact shape of
    train/serve skew that produces confident nonsense and raises nothing.
    """
    flow = phase2_test.iloc[[7]].to_dict("records")[0]
    shuffled = dict(reversed(list(flow.items())))

    assert loaded.score_batch([flow]) == loaded.score_batch([shuffled])


def test_an_obvious_attack_flow_alerts_and_an_ordinary_one_does_not(loaded, phase2_train) -> None:
    attack = phase2_train[phase2_train["label"] == "DoS Hulk"].head(20)

    records = loaded.score_batch(attack.to_dict("records"))

    assert sum(record["kind"] is not None for record in records) >= 15


def test_an_empty_batch_scores_to_an_empty_list(loaded) -> None:
    """The replay loop hands over whatever a tick produced, including nothing."""
    assert loaded.score_batch([]) == []


def test_scoring_without_a_model_is_refused_rather_than_answered(tmp_path) -> None:
    """A bundle with no artifacts serves health and the dashboard shell -- that
    is Phase 0's design. What it must not do is return a batch of nulls that
    reads like 'no attacks found'."""
    from app.inference import load_bundle

    empty = load_bundle(tmp_path)

    with pytest.raises(RuntimeError, match="no complete stage"):
        empty.score_batch([{"destination_port": 443}])


# ---------------------------------------------------------------------------
# Review findings on the serving boundary
# ---------------------------------------------------------------------------


def test_a_flow_that_supplies_no_recognised_feature_is_refused(loaded) -> None:
    """`build_feature_matrix` fills absent columns with zero by design, so an
    empty dict scores as a complete flow of zeros. That is a scored decision
    about traffic nobody described, and it is indistinguishable in the output
    from a real flow that happened to be all zeros."""
    with pytest.raises(ValueError, match="no recognised feature"):
        loaded.score_batch([{}, {}])


def test_a_feature_arriving_as_a_string_is_refused_rather_than_zeroed(loaded, phase2_test) -> None:
    """The quiet half of this project's own threat model.

    `_feature_frame` keeps numeric and boolean columns and drops the rest, and
    the `reindex` behind it then fills the dropped column with 0.0. So a feature
    the caller *did* send, as the JSON string "7.0" rather than the number 7.0,
    silently becomes zero and the model scores a different flow than the one
    that arrived. The schema hash cannot catch this: the column order is intact.
    """
    flow = phase2_test.iloc[[3]].to_dict("records")[0]
    column = next(name for name in loaded.feature_order if name in flow)
    flow[column] = str(flow[column])

    with pytest.raises(ValueError, match=column):
        loaded.score_batch([flow])


def test_a_flow_missing_some_features_still_scores(loaded, phase2_test) -> None:
    """Absent is not the same as malformed, and the zero-fill is deliberate.

    A local extractor that does not produce every CICIDS2017 column must still
    be scorable -- `build_feature_matrix` documents filling absent columns so
    the matrix width a trained model sees never changes. Only a column the
    caller supplied and got wrong is an error.
    """
    flow = phase2_test.iloc[[3]].to_dict("records")[0]
    reduced = {key: value for key, value in list(flow.items())[:3]}

    records = loaded.score_batch([reduced])

    assert len(records) == 1
    assert records[0]["confidence"] is not None


def test_scoring_a_half_loaded_bundle_is_refused_as_such(loaded) -> None:
    """`is_loaded` asks whether a model is resident; the cascade needs a
    *complete* stage. A supervised model whose threshold never arrived passes
    the first question and fails the second, and it should fail it as the same
    refusal rather than as a ValueError from inside the fusion rule."""
    loaded.tau_sup = None
    loaded.autoencoder_state = None
    loaded.autoencoder = None
    loaded.tau_anom = None

    assert loaded.is_loaded
    with pytest.raises(RuntimeError, match="no complete stage"):
        loaded.score_batch([{"destination_port": 443}])
