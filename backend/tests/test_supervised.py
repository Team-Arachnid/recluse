"""Phase 2 end to end: splits in, a promoted champion and a report out.

The real run takes minutes on a million rows, so these fit on a few hundred.
What they pin is not the accuracy of a toy model but the properties the phase's
acceptance criteria are written in terms of: the threshold comes from the
budget, the artifacts are a consistent pair, the fallback survives promotion,
and the report tells the truth about families the model has never seen.
"""

from __future__ import annotations

import json
import pickle
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from training.evaluate import (
    ArtifactMismatch,
    load_model,
    operating_point_prediction,
    render_report,
    write_outputs,
)
from training.evaluate import evaluate_split as evaluate
from training.features import (
    PORT_ENCODING_BUCKETED,
    PORT_ENCODING_RAW,
    fit_preprocessing,
    load_preprocessing_bundle,
    save_preprocessing_bundle,
)
from training.labels import BENIGN_FAMILY
from training.train_supervised import (
    NoTrainableClasses,
    prepare,
    promote,
    render_port_ablation,
    restore_champion,
    train,
)

# The depth sweep is the slow part and its value here is structural, not
# numerical: one candidate is enough to prove the path runs.
FAST = {"depth_grid": (8,), "sweep_rows": 10_000}


@pytest.fixture
def trained(tmp_path, phase2_train, phase2_val, budget):
    return train(
        phase2_train,
        phase2_val,
        artifacts_dir=tmp_path / "artifacts",
        algorithm="rf",
        port_encoding=PORT_ENCODING_RAW,
        settings=budget,
        **FAST,
    )


# ---------------------------------------------------------------------------
# Vocabulary and preparation
# ---------------------------------------------------------------------------


def test_the_support_floor_keeps_a_five_row_class_out_of_the_vocabulary(
    phase2_train, phase2_val
) -> None:
    """Under `class_weight="balanced"` five rows against six hundred earn a
    weight in the hundreds. The class cannot be learned and the weight distorts
    everything else, so it is held out and reported."""
    data = prepare(phase2_train, phase2_val, port_encoding=PORT_ENCODING_RAW)

    assert data.classes == ["benign", "dos", "brute_force"]
    assert data.held_out == ["web_attack"]
    assert "held out" in data.label_mapping


def test_held_out_rows_do_not_reach_the_fit(phase2_train, phase2_val) -> None:
    data = prepare(phase2_train, phase2_val, port_encoding=PORT_ENCODING_RAW)

    assert set(data.y_train) == set(data.classes)
    assert len(data.y_train) == len(phase2_train) - 5


def test_a_split_with_nothing_learnable_fails_loudly(phase2_val) -> None:
    benign_only = phase2_val[phase2_val["label"] == "BENIGN"]

    with pytest.raises(NoTrainableClasses):
        prepare(benign_only, phase2_val, port_encoding=PORT_ENCODING_RAW)


def test_the_scaler_is_fitted_without_the_validation_day(phase2_train, phase2_val) -> None:
    """The validation day sets the threshold, so it must not also shape the
    scaler that produced the scores it is set from."""
    from training.features import fit_preprocessing

    trained_only = prepare(phase2_train, phase2_val, port_encoding=PORT_ENCODING_RAW).bundle
    everything = fit_preprocessing(pd.concat([phase2_train, phase2_val], ignore_index=True))

    assert not np.allclose(trained_only["scaler"].center_, everything["scaler"].center_)


# ---------------------------------------------------------------------------
# Threshold and artifacts
# ---------------------------------------------------------------------------


def test_tau_sup_comes_from_the_budget_and_not_from_argmax(trained, budget) -> None:
    assert trained.threshold["target_fpr"] == budget.target_fpr
    assert trained.threshold["fpr"] <= budget.target_fpr
    assert trained.threshold["tau"] != 0.5


def test_the_run_records_what_it_collapsed_and_what_it_held_out(trained) -> None:
    assert trained.held_out_families == ["web_attack"]
    assert "DoS Hulk" in trained.label_mapping
    assert trained.class_support["benign"] == 600


def test_the_model_and_its_preprocessing_are_written_as_a_matching_pair(tmp_path, trained) -> None:
    """A model fed a differently-ordered matrix produces confident nonsense and
    raises nothing, so the two halves carry the same hash or neither ships."""
    artifacts = tmp_path / "artifacts"
    with (artifacts / "supervised_rf.pkl").open("rb") as handle:
        payload = pickle.load(handle)
    bundle = load_preprocessing_bundle(artifacts / "preprocessing_rf.pkl")

    assert payload["schema_hash"] == bundle["schema_hash"] == trained.schema_hash
    assert payload["feature_order"] == bundle["feature_order"]
    assert payload["tau_sup"] == trained.threshold["tau"]


def test_evaluation_refuses_a_mismatched_pair(tmp_path, trained) -> None:
    artifacts = tmp_path / "artifacts"
    bundle = load_preprocessing_bundle(artifacts / "preprocessing_rf.pkl")
    bundle["schema_hash"] = "sha256:not-the-one-it-was-trained-on"
    from training.features import save_preprocessing_bundle

    save_preprocessing_bundle(bundle, artifacts / "preprocessing_rf.pkl")

    with pytest.raises(ArtifactMismatch):
        load_model(artifacts, "rf")


def test_promotion_publishes_the_canonical_pair_and_a_model_card(tmp_path, trained) -> None:
    artifacts = tmp_path / "artifacts"

    assert promote(trained, artifacts) is True

    card = json.loads((artifacts / "model_card.json").read_text(encoding="utf-8"))
    assert card["version"] == trained.version
    assert card["thresholds"]["tau_sup"] == trained.threshold["tau"]
    assert card["thresholds"]["tau_anom"] is None
    assert (artifacts / "supervised_model.pkl").exists()
    assert (artifacts / "preprocessing.pkl").exists()


def test_a_lightgbm_champion_survives_leaving_the_process_that_trained_it(
    tmp_path, phase2_train, phase2_val, budget
) -> None:
    """Pickle stores a class by module path, not by value.

    A wrapper class defined in a module started as `python -m ...` is recorded
    as living in `__main__`, and the API process -- whose `__main__` is uvicorn
    -- then cannot find it. Training succeeds, the artifact is written, and
    nothing that reads it can open it. Unpickling in a subprocess is the only
    way to test that from inside a test runner, because the test runner's own
    `__main__` is not the trainer's either.
    """
    artifacts = tmp_path / "artifacts"
    run = train(
        phase2_train,
        phase2_val,
        artifacts_dir=artifacts,
        algorithm="lgbm",
        port_encoding=PORT_ENCODING_RAW,
        settings=budget,
    )
    promote(run, artifacts)

    probe = (
        "import pickle, sys;"
        "p = pickle.load(open(sys.argv[1], 'rb'));"
        "print(type(p['model']).__module__, len(p['classes']))"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe, str(artifacts / "supervised_model.pkl")],
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parents[1],
        check=False,
    )

    assert result.returncode == 0, result.stderr
    module, classes = result.stdout.split()
    assert module == "training.estimators"
    assert int(classes) == len(run.classes)


def test_the_serving_loader_accepts_what_training_wrote(tmp_path, trained) -> None:
    """The train/serve contract, exercised end to end rather than assumed.

    Training writes the pair, the API's loader reads it, and Stage 1 counts as
    ready only once both the model and its threshold are resident.
    """
    from app.inference import load_bundle

    artifacts = tmp_path / "artifacts"
    promote(trained, artifacts)

    bundle = load_bundle(artifacts)

    assert bundle.version == trained.version
    assert bundle.supervised_classes == trained.classes
    assert bundle.tau_sup == trained.threshold["tau"]
    assert bundle.stage1_ready
    assert not bundle.stage2_ready


def test_the_serving_loader_refuses_a_model_from_a_different_schema(tmp_path, trained) -> None:
    """Train/serve skew is silent, so it has to stop the process at boot."""
    import pickle as pickle_module

    from app.inference import SchemaHashMismatch, load_bundle

    artifacts = tmp_path / "artifacts"
    promote(trained, artifacts)

    path = artifacts / "supervised_model.pkl"
    with path.open("rb") as handle:
        payload = pickle_module.load(handle)
    payload["schema_hash"] = "sha256:trained-against-something-else"
    with path.open("wb") as handle:
        pickle_module.dump(payload, handle)

    with pytest.raises(SchemaHashMismatch):
        load_bundle(artifacts)


def test_a_losing_run_repairs_a_canonical_pair_that_make_data_desynchronised(
    tmp_path, phase2_train, phase2_val, budget
) -> None:
    """`make data` and `make train` both write `preprocessing.pkl`.

    Phase 1 writes it under its own default port encoding; Phase 2 overwrites it
    with whatever the champion was fitted against. Running `make data` after a
    model exists therefore leaves the canonical bundle disagreeing with the
    canonical model — and a later training run whose challenger *loses* used to
    walk straight past that, turning a recoverable state into a crash two
    commands later. This is that exact sequence.
    """
    artifacts = tmp_path / "artifacts"

    champion = train(
        phase2_train,
        phase2_val,
        artifacts_dir=artifacts,
        algorithm="rf",
        port_encoding=PORT_ENCODING_BUCKETED,
        settings=budget,
        **FAST,
    )
    promote(champion, artifacts)

    # `make data`: Phase 1 refits the canonical bundle under its own encoding.
    phase1 = fit_preprocessing(phase2_train, port_encoding=PORT_ENCODING_RAW)
    save_preprocessing_bundle(phase1, artifacts / "preprocessing.pkl")
    assert phase1["schema_hash"] != champion.schema_hash
    with pytest.raises(ArtifactMismatch):
        load_model(artifacts)

    # `make train`: a challenger that cannot win must still leave the pair sane.
    challenger = replace(
        champion,
        version="stage1-rf-weaker",
        validation={**champion.validation, "pr_auc": champion.val_pr_auc - 0.1},
    )

    assert promote(challenger, artifacts) is False

    loaded = load_model(artifacts)
    assert loaded.payload["schema_hash"] == champion.schema_hash
    assert loaded.bundle["schema_hash"] == champion.schema_hash


def test_restoring_is_a_no_op_when_the_canonical_pair_is_already_right(tmp_path, trained) -> None:
    """It repairs damage; it does not churn files that are already correct."""
    artifacts = tmp_path / "artifacts"
    promote(trained, artifacts)

    assert restore_champion(artifacts) is False


def test_a_weaker_challenger_does_not_displace_the_champion(tmp_path, trained) -> None:
    """Keeping the fallback is what makes a regression a file swap rather than
    a retrain."""
    artifacts = tmp_path / "artifacts"
    promote(trained, artifacts)

    weaker = replace(
        trained,
        version="stage1-rf-weaker",
        validation={**trained.validation, "pr_auc": trained.val_pr_auc - 0.1},
    )

    assert promote(weaker, artifacts) is False

    card = json.loads((artifacts / "model_card.json").read_text(encoding="utf-8"))
    assert card["version"] == trained.version


# ---------------------------------------------------------------------------
# Evaluation and the write-up
# ---------------------------------------------------------------------------


@pytest.fixture
def evaluated(tmp_path, trained, phase2_test, budget):
    artifacts = tmp_path / "artifacts"
    promote(trained, artifacts)
    return evaluate(load_model(artifacts), phase2_test, "test", budget)


def test_the_test_day_families_are_reported_as_never_trained_on(evaluated) -> None:
    """The temporal split puts every Friday family outside Stage 1's
    vocabulary. That is the structural fact the whole phase reports."""
    assert "ddos" not in evaluated.classes
    assert "port_scan" not in evaluated.classes
    assert set(evaluated.family_recall) >= {"ddos", "port_scan"}


def test_nothing_is_predicted_below_the_threshold(evaluated, phase2_test, tmp_path) -> None:
    loaded = load_model(tmp_path / "artifacts")
    from training.features import build_feature_matrix

    matrix = build_feature_matrix(phase2_test, loaded.bundle).to_numpy(dtype="float32")
    proba = loaded.model.predict_proba(matrix)

    predicted = operating_point_prediction(proba, loaded.classes, tau=1.5)

    assert set(predicted) == {BENIGN_FAMILY}


def test_the_report_carries_every_section_the_checkpoint_asks_for(evaluated, budget) -> None:
    rendered = render_report(evaluated, None, budget)

    for heading in (
        "## The operating threshold",
        "## Per-class results on the test day",
        "## Confusion matrix",
        "## Detection view",
        "## Interpretation",
    ):
        assert heading in rendered
    assert "PR-AUC (headline)" in rendered
    assert "Alerts per analyst per hour" in rendered


def test_accuracy_appears_once_and_never_as_a_headline(evaluated, budget) -> None:
    """On a day that is mostly benign, answering benign to everything scores
    well. The number is kept for comparability and demoted in place."""
    rendered = render_report(evaluated, None, budget)

    assert "never a headline" in rendered
    assert "# Accuracy" not in rendered
    assert "**Accuracy" not in rendered


def test_write_outputs_produces_the_report_and_the_curve_data(tmp_path, evaluated, budget) -> None:
    artifacts = tmp_path / "artifacts"
    report_path, metrics_path = write_outputs(
        evaluated, None, artifacts, tmp_path / "reports", budget
    )

    assert report_path.name == "phase2_supervised.md"
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    assert metrics["budget"]["target_fpr"] == budget.target_fpr
    assert metrics["test"]["pr_curve"]
    assert metrics["test"]["roc_curve"]


def test_the_model_card_gains_the_test_numbers(tmp_path, evaluated, budget) -> None:
    artifacts = tmp_path / "artifacts"
    write_outputs(evaluated, None, artifacts, tmp_path / "reports", budget)

    card = json.loads((artifacts / "model_card.json").read_text(encoding="utf-8"))
    assert card["test"]["pr_auc"] == evaluated.pr_auc


# ---------------------------------------------------------------------------
# The destination-port ablation Phase 1 deferred to here
# ---------------------------------------------------------------------------


def test_both_port_encodings_train_and_can_be_compared(
    tmp_path, phase2_train, phase2_val, budget
) -> None:
    runs = {}
    for encoding in (PORT_ENCODING_RAW, PORT_ENCODING_BUCKETED):
        runs[encoding] = train(
            phase2_train,
            phase2_val,
            artifacts_dir=tmp_path / encoding,
            algorithm="rf",
            port_encoding=encoding,
            settings=budget,
            **FAST,
        )

    assert runs[PORT_ENCODING_RAW].schema_hash != runs[PORT_ENCODING_BUCKETED].schema_hash

    rendered = render_port_ablation(runs, "rf")
    assert "Validation PR-AUC" in rendered
    assert "`raw`" in rendered and "`bucketed`" in rendered


# ---------------------------------------------------------------------------
# What Phase 4's hold-out loop needs from this module
#
# LOAO refits Stage 1 once per held-out family, and it has to change exactly
# one thing per fold. Both additions below exist to make that true: a frozen
# feature contract so the folds share one matrix, and a fit that repeats the
# champion's hyperparameters instead of searching for its own.
# ---------------------------------------------------------------------------


def test_prepare_accepts_a_frozen_bundle_instead_of_fitting_one(phase2_train, phase2_val) -> None:
    """Stage 2 is the thing LOAO does not vary, and an unchanged autoencoder
    means an unchanged input transform. Refitting the scaler per fold would
    also change two things at once."""
    champion = fit_preprocessing(phase2_train, port_encoding=PORT_ENCODING_BUCKETED)

    data = prepare(phase2_train, phase2_val, port_encoding=PORT_ENCODING_RAW, bundle=champion)

    assert data.bundle["schema_hash"] == champion["schema_hash"]
    assert data.bundle["scaler"] is champion["scaler"]


def test_prepare_can_hold_a_family_out_of_the_vocabulary_and_the_fit(
    phase2_train, phase2_val
) -> None:
    data = prepare(phase2_train, phase2_val, port_encoding=PORT_ENCODING_RAW, exclude=("dos",))

    assert data.classes == ["benign", "brute_force"]
    assert "dos" not in set(data.y_train)
    assert len(data.y_train) == 600 + 150


def test_a_frozen_bundle_makes_the_folds_share_one_feature_matrix(
    phase2_train, phase2_val
) -> None:
    """The property the hold-out loop is built on.

    With the contract frozen, the rows that survive an exclusion are
    bit-identical to the same rows in the unreduced matrix -- so eight folds
    index into one matrix instead of rebuilding it eight times.
    """
    champion = fit_preprocessing(phase2_train, port_encoding=PORT_ENCODING_BUCKETED)
    full = prepare(phase2_train, phase2_val, bundle=champion)
    reduced = prepare(phase2_train, phase2_val, bundle=champion, exclude=("dos",))

    kept = (full.train_families != "dos").to_numpy()

    assert np.array_equal(reduced.x_train, full.x_train[kept])


def test_holding_out_every_attack_family_fails_loudly(phase2_train, phase2_val) -> None:
    with pytest.raises(NoTrainableClasses):
        prepare(phase2_train, phase2_val, exclude=("dos", "brute_force"))


def test_fit_fixed_repeats_a_recorded_depth_instead_of_sweeping_for_one(
    phase2_train, phase2_val
) -> None:
    from training.train_supervised import fit_fixed

    data = prepare(phase2_train, phase2_val, port_encoding=PORT_ENCODING_RAW)

    model = fit_fixed(data, "rf", {"n_estimators": 20, "max_depth": 11}, seed=7)

    assert model.max_depth == 11
    assert model.n_estimators == 20
    # `classes_` is the column order of `predict_proba`, and scikit-learn sorts
    # it alphabetically -- which is *not* `FAMILIES` order. Every caller reads
    # the order off the model for exactly that reason; reading it off the
    # vocabulary instead would map `dos` probabilities onto `brute_force`.
    assert set(model.classes_) == set(data.classes)
    assert model.predict_proba(data.x_val).shape[1] == len(data.classes)


def test_fit_fixed_trains_lightgbm_for_exactly_the_recorded_rounds(
    phase2_train, phase2_val
) -> None:
    """No early stopping, and so no validation set.

    Two of the five families the table holds out live on the validation day. A
    stopping rule measured there would let a fold's fit see the very rows the
    fold is supposed never to have met.
    """
    from training.train_supervised import fit_fixed

    data = prepare(phase2_train, phase2_val, port_encoding=PORT_ENCODING_RAW)

    model = fit_fixed(data, "lgbm", {"best_iteration": 12, "num_leaves": 7}, seed=7)

    assert model.booster.num_trees() == 12 * len(data.classes)
    assert list(model.classes_) == data.classes


def test_fit_fixed_sizes_the_output_layer_from_the_fold_not_the_record(
    phase2_train, phase2_val
) -> None:
    """Removing a family removes a column.

    A booster told to emit three columns for a two-class problem does not
    fail -- it emits a column of noise, which `attack_confidence` then reads as
    a family's probability.
    """
    from training.train_supervised import fit_fixed

    data = prepare(phase2_train, phase2_val, exclude=("dos",))

    model = fit_fixed(data, "lgbm", {"best_iteration": 5, "num_class": 3}, seed=7)

    assert model.predict_proba(data.x_val).shape[1] == 2
