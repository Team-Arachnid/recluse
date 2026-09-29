"""Phase 3 end to end: benign rows in, a separating score and a report out.

The real run fits a million flows, so these fit a thousand. What they pin is not
the separation a toy model achieves but the properties the phase's acceptance
criteria are written in terms of: the training set is asserted attack-free and
the assertion is fatal, `tau_anom` is a benign percentile rather than a tuning,
the histogram keeps every row it was handed, the artifact the API loads is the
artifact training wrote, and the report tells the truth when a baseline wins or
when the distributions do not separate.

Stage 2 rides on Stage 1's feature contract, so most of these run against a
real promoted champion built by the Phase 2 code rather than against a
hand-assembled bundle.
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from training.autoencoder import (
    INPUT_CLIP,
    Autoencoder,
    per_feature_error,
    prepare_input,
    reconstruction_error,
    top_contributors,
)
from training.features import PORT_ENCODING_BUCKETED, load_preprocessing_bundle
from training.labels import BENIGN_FAMILY
from training.metrics import error_histogram, log_bin_edges, select_anomaly_threshold
from training.split import AttackInBenignTrainingSet
from training.train_autoencoder import (
    MissingChampion,
    assert_attack_free,
    load_artifacts,
    prepare_benign,
    render_report,
    train,
)
from training.train_supervised import promote
from training.train_supervised import train as train_supervised

# Two epochs is enough to prove the loop runs and the artifacts are consistent.
# Separation on a thousand synthetic rows is not a claim worth spending minutes
# of every test run on.
FAST = {"max_epochs": 3, "patience": 2, "batch_rows": 128, "baselines": False}


@pytest.fixture
def champion(tmp_path, phase2_train, phase2_val, budget):
    """A promoted Stage 1, which is what Stage 2's feature contract comes from."""
    artifacts = tmp_path / "artifacts"
    run = train_supervised(
        phase2_train,
        phase2_val,
        artifacts_dir=artifacts,
        algorithm="rf",
        port_encoding=PORT_ENCODING_BUCKETED,
        settings=budget,
        depth_grid=(8,),
        sweep_rows=10_000,
    )
    promote(run, artifacts)
    return artifacts


@pytest.fixture
def stage2(champion, phase3_benign, phase2_val, phase2_test, budget):
    return train(
        phase3_benign,
        phase2_val,
        phase2_test,
        artifacts_dir=champion,
        settings=budget,
        **FAST,
    )


# ---------------------------------------------------------------------------
# The one property the training set may not lose
# ---------------------------------------------------------------------------


def test_the_training_set_is_asserted_attack_free(phase3_benign) -> None:
    assert assert_attack_free(phase3_benign) == len(phase3_benign)


def test_one_attack_row_in_the_training_set_is_fatal(phase3_benign, phase2_train) -> None:
    """Not a warning. An autoencoder that has seen an attack learns to
    reconstruct it and stops flagging it, and nothing anywhere raises -- the
    claim just quietly stops being true."""
    contaminated = phase2_train[phase2_train["label"] == "DoS Hulk"].head(1)
    frame = pd.concat([phase3_benign, contaminated], ignore_index=True)

    with pytest.raises(AttackInBenignTrainingSet, match="dos"):
        assert_attack_free(frame)


def test_training_refuses_a_contaminated_set_before_it_fits_anything(
    champion, phase3_benign, phase2_train, phase2_val, phase2_test, budget
) -> None:
    frame = pd.concat(
        [phase3_benign, phase2_train[phase2_train["label"] == "FTP-Patator"].head(3)],
        ignore_index=True,
    )

    with pytest.raises(AttackInBenignTrainingSet):
        train(frame, phase2_val, phase2_test, artifacts_dir=champion, settings=budget, **FAST)

    assert not (champion / "autoencoder.pt").exists()


# ---------------------------------------------------------------------------
# The input transform
# ---------------------------------------------------------------------------


def test_the_input_transform_tames_a_column_the_scaler_left_unscaled() -> None:
    """`RobustScaler` leaves a zero-IQR column divided by 1.0, and CICIDS2017 has
    such columns: `idle_std` alone owned 93.9% of the squared magnitude the loss
    could see, which made MSE a one-column objective and inverted the score."""
    ordinary = np.random.default_rng(7).normal(size=(64, 3))
    matrix = np.hstack([ordinary, np.full((64, 1), 7.6e7)])

    before = (matrix**2).mean(axis=0)
    after = (prepare_input(matrix).astype("float64") ** 2).mean(axis=0)

    assert before[-1] / before.sum() > 0.99
    assert after[-1] / after.sum() < 0.99
    assert np.abs(prepare_input(matrix)).max() <= INPUT_CLIP


def test_the_input_transform_keeps_the_ordering_it_compresses() -> None:
    """Further from normal has to stay more anomalous, or the score means nothing."""
    values = np.array([[0.0], [1.0], [10.0], [1_000.0], [-1_000.0]])

    prepared = prepare_input(values, clip=20.0).ravel()

    assert list(prepared[:4]) == sorted(prepared[:4])
    assert prepared[-1] == pytest.approx(-prepared[3])


def test_the_transform_is_applied_once_on_both_paths(stage2, champion, phase2_test) -> None:
    """It is not idempotent, so the fit and the scoring path each apply it exactly
    once. This is the assertion that the two agree."""
    import torch

    from training.features import build_feature_matrix

    bundle = load_preprocessing_bundle(champion / "preprocessing.pkl")
    model = Autoencoder.from_state_dict(
        torch.load(champion / "autoencoder.pt", map_location="cpu", weights_only=True)
    )
    shared = build_feature_matrix(phase2_test, bundle).to_numpy(dtype="float32")[:48]

    prepared = torch.from_numpy(prepare_input(shared))
    with torch.no_grad():
        by_hand = ((model(prepared) - prepared) ** 2).mean(dim=1).numpy()

    assert np.allclose(reconstruction_error(model, shared), by_hand, atol=1e-6)
    # Double-applying is the failure this is guarding, so it has to look different.
    assert not np.allclose(
        reconstruction_error(model, shared), reconstruction_error(model, prepare_input(shared))
    )


def test_the_run_records_the_transform_it_was_fitted_under(stage2) -> None:
    assert stage2.hyperparameters["input_clip"] == INPUT_CLIP
    assert "log1p" in stage2.hyperparameters["input_transform"]


# ---------------------------------------------------------------------------
# Preparation
# ---------------------------------------------------------------------------


def test_duplicates_go_before_the_early_stopping_split(champion, phase3_benign) -> None:
    """A row on both sides of the split makes the validation loss optimistic,
    which stops training later than it should."""
    bundle = load_preprocessing_bundle(champion / "preprocessing.pkl")

    data = prepare_benign(phase3_benign, bundle)

    assert data.source_rows == len(phase3_benign)
    assert data.duplicate_rows == 40
    assert data.retained_rows == len(phase3_benign) - 40


def test_the_early_stopping_slice_is_held_out_of_the_fit(champion, phase3_benign) -> None:
    bundle = load_preprocessing_bundle(champion / "preprocessing.pkl")

    data = prepare_benign(phase3_benign, bundle)

    fitted = {row.tobytes() for row in data.x_fit}
    held = {row.tobytes() for row in data.x_early_stop}
    assert len(data.x_early_stop) == int(data.retained_rows * 0.1)
    assert not fitted & held


def test_stage_2_refuses_to_train_without_stage_1s_contract(
    tmp_path, phase3_benign, phase2_val, phase2_test, budget
) -> None:
    """Both stages read one feature matrix at serving time, so a Stage 2 fitted
    against its own scaling would score garbage in production and raise
    nothing."""
    with pytest.raises(MissingChampion, match="preprocessing.pkl"):
        train(
            phase3_benign,
            phase2_val,
            phase2_test,
            artifacts_dir=tmp_path / "empty",
            settings=budget,
            **FAST,
        )


def test_a_mismatched_canonical_pair_is_refused_rather_than_trained_on(
    champion, phase3_benign
) -> None:
    card_path = champion / "model_card.json"
    card = json.loads(card_path.read_text(encoding="utf-8"))
    card["schema_hash"] = "sha256:trained-against-something-else"
    card_path.write_text(json.dumps(card), encoding="utf-8")

    with pytest.raises(MissingChampion, match="mismatched"):
        load_artifacts(champion)


# ---------------------------------------------------------------------------
# The threshold
# ---------------------------------------------------------------------------


def test_tau_anom_is_a_benign_percentile_and_nothing_else() -> None:
    """The threshold is a statement about normal traffic. A value tuned until
    the attacks landed above it would be a supervised decision wearing an
    unsupervised model's clothes."""
    errors = np.linspace(0.0, 1.0, 10_001)

    choice = select_anomaly_threshold(errors, percentile=99.5)

    assert choice.tau == pytest.approx(0.995, abs=1e-4)
    assert choice.fpr == pytest.approx(0.005, abs=1e-3)
    assert choice.benign_rows == 10_001


def test_the_budget_equivalent_threshold_is_reported_beside_it(budget) -> None:
    """A 99.5th percentile is a far looser operating point than the analyst
    budget Stage 1 was cut to, and the gap is reported rather than discovered
    later from an alert count."""
    errors = np.linspace(0.0, 1.0, 10_001)

    choice = select_anomaly_threshold(errors, percentile=99.5, target_fpr=budget.target_fpr)

    assert choice.budget_percentile == pytest.approx(99.68, abs=0.01)
    assert choice.budget_tau > choice.tau
    assert "reported, not shipped" in choice.render()


def test_a_threshold_cannot_be_cut_without_benign_rows() -> None:
    with pytest.raises(ValueError, match="benign"):
        select_anomaly_threshold(np.array([]))


def test_the_run_cuts_tau_anom_on_the_validation_day(stage2) -> None:
    assert stage2.threshold["percentile"] == 99.5
    assert "validation day" in stage2.threshold["calibrated_on"]
    assert stage2.threshold["tau"] > 0


# ---------------------------------------------------------------------------
# The histogram
# ---------------------------------------------------------------------------


def test_the_histogram_keeps_every_row_including_both_tails() -> None:
    """A histogram that silently loses its tail is the one artifact a threshold
    slider must not be handed: the tail is where the alerts are."""
    scores = np.array([1e-9, 1e-4, 1e-2, 5.0, 900.0])
    edges = log_bin_edges(np.array([1e-4, 1.0]), bins=8)

    histogram = error_histogram(scores, edges)

    assert sum(histogram.counts) == histogram.rows == 5
    assert histogram.counts[0] >= 2  # 1e-9 clipped up into the first bin
    assert histogram.counts[-1] >= 2  # 5.0 and 900.0 clipped down into the last


def test_the_histogram_projects_an_alert_count_from_its_bins() -> None:
    edges = log_bin_edges(np.array([1e-3, 1.0]), bins=10)
    histogram = error_histogram(np.geomspace(1e-3, 1.0, 1_000), edges)

    assert histogram.above(edges[-1]) == 0
    assert histogram.above(edges[0]) == histogram.rows
    assert 0 < histogram.above(edges[5]) < histogram.rows


def test_the_persisted_histogram_is_bins_and_not_rows(stage2) -> None:
    benign = stage2.histograms["validation_benign"]

    assert len(benign.edges) == len(benign.counts) + 1
    assert benign.rows > len(benign.counts)
    assert set(benign.percentiles) >= {"p50", "p99.5", "max"}


# ---------------------------------------------------------------------------
# The explanation, which the model gives away for free
# ---------------------------------------------------------------------------


def test_top_contributors_ranks_by_error_and_reports_a_share() -> None:
    errors = np.array([0.1, 9.0, 0.2, 0.7])

    top = top_contributors(errors, ["a", "b", "c", "d"], k=2)

    assert [item["feature"] for item in top] == ["b", "d"]
    assert top[0]["share"] == pytest.approx(9.0 / 10.0)


def test_top_contributors_refuses_a_mismatched_feature_list() -> None:
    with pytest.raises(ValueError, match="same bundle"):
        top_contributors(np.zeros(4), ["a", "b"])


def test_the_report_names_what_each_family_failed_to_reconstruct(stage2) -> None:
    assert BENIGN_FAMILY in stage2.explanations
    for contributors in stage2.explanations.values():
        assert len(contributors) == 5
        assert sum(item["share"] for item in contributors) <= 1.0 + 1e-9


# ---------------------------------------------------------------------------
# The artifact, and the loader that has to read it
# ---------------------------------------------------------------------------


def test_the_state_dict_carries_its_own_geometry(stage2, champion) -> None:
    """Nothing tells `from_state_dict` the widths. It reads them off the tensors,
    so the architecture in the model card can only ever be a readout of the file
    that shipped."""
    import torch

    state = torch.load(champion / "autoencoder.pt", map_location="cpu", weights_only=True)

    rebuilt = Autoencoder.from_state_dict(state)

    assert rebuilt.input_dim == stage2.feature_count
    assert rebuilt.widths == (64, 32, 16)
    assert not rebuilt.training


def test_a_state_dict_from_something_else_is_refused() -> None:
    with pytest.raises(ValueError, match="not written by this class"):
        Autoencoder.from_state_dict({"linear.weight": np.zeros((4, 4))})


def test_the_same_row_scores_the_same_in_any_batch(stage2, champion, phase2_test) -> None:
    """Batch norm normalises against the batch in training mode and dropout is
    live, so a model left in training mode would give one flow two answers."""
    import torch

    from training.features import build_feature_matrix

    bundle = load_preprocessing_bundle(champion / "preprocessing.pkl")
    model = Autoencoder.from_state_dict(
        torch.load(champion / "autoencoder.pt", map_location="cpu", weights_only=True)
    )
    matrix = build_feature_matrix(phase2_test, bundle).to_numpy(dtype="float32")[:64]

    whole = reconstruction_error(model, matrix)
    in_pieces = reconstruction_error(model, matrix, batch_size=7)
    alone = reconstruction_error(model, matrix[:1])

    assert np.allclose(whole, in_pieces, atol=1e-6)
    assert whole[0] == pytest.approx(alone[0], abs=1e-6)


def test_the_row_score_is_the_mean_of_its_per_feature_errors(stage2, champion, phase2_test) -> None:
    import torch

    from training.features import build_feature_matrix

    bundle = load_preprocessing_bundle(champion / "preprocessing.pkl")
    model = Autoencoder.from_state_dict(
        torch.load(champion / "autoencoder.pt", map_location="cpu", weights_only=True)
    )
    matrix = build_feature_matrix(phase2_test, bundle).to_numpy(dtype="float32")[:32]

    assert np.allclose(
        reconstruction_error(model, matrix),
        per_feature_error(model, matrix).mean(axis=1),
        rtol=1e-5,
    )


def test_the_serving_loader_accepts_what_training_wrote(stage2, champion) -> None:
    """The train/serve contract for Stage 2, exercised rather than assumed."""
    from app.inference import load_bundle

    bundle = load_bundle(champion)

    assert bundle.stage1_ready
    assert bundle.stage2_ready
    assert bundle.tau_anom == stage2.threshold["tau"]
    assert bundle.autoencoder is not None
    assert bundle.autoencoder.input_dim == len(bundle.feature_order)
    assert bundle.benign_error_histogram is not None
    assert sum(bundle.benign_error_histogram["counts"]) == bundle.benign_error_histogram["rows"]


def test_the_serving_loader_refuses_a_stage_2_from_a_different_feature_contract(
    stage2, champion
) -> None:
    """A width mismatch is the one form of train/serve skew the schema hash
    cannot catch on its own, because the autoencoder artifact carries no hash."""
    import torch

    from app.inference import SchemaHashMismatch, load_bundle

    torch.save(Autoencoder(input_dim=7).state_dict(), champion / "autoencoder.pt")

    with pytest.raises(SchemaHashMismatch, match="feature contract"):
        load_bundle(champion)


def test_the_model_card_gains_stage_2_without_losing_stage_1(stage2, champion) -> None:
    card = json.loads((champion / "model_card.json").read_text(encoding="utf-8"))

    assert card["thresholds"]["tau_sup"] is not None
    assert card["thresholds"]["tau_anom"] == stage2.threshold["tau"]
    assert card["anomaly_algorithm"] == "autoencoder"
    assert card["stage2"]["version"] == stage2.version
    assert card["stage2"]["benign_error_histogram"]["rows"] > 0
    # Stage 1's own record is untouched.
    assert card["stage"] == "stage1_supervised"
    assert card["run"]["algorithm"] == "rf"


def test_the_metrics_payload_has_the_same_shape_as_stage_1s(stage2, champion) -> None:
    """Phase 5 serves both from one endpoint, and a second layout would be a
    second parser."""
    payload = json.loads((champion / "metrics_anomaly.json").read_text(encoding="utf-8"))

    assert payload["stage"] == "stage2_anomaly"
    assert set(payload) >= {"stage", "budget", "training", "test"}
    assert payload["budget"]["target_fpr"] > 0
    assert payload["test"]["split"] == "test"
    assert "test" not in payload["training"]


# ---------------------------------------------------------------------------
# The write-up, which has to stay honest when the numbers are not flattering
# ---------------------------------------------------------------------------


def test_the_report_says_so_when_a_baseline_wins(stage2, budget) -> None:
    """A project that reports "ECOD beat our autoencoder and here is where" is
    more credible than one that drops the comparison."""
    beaten = replace(
        stage2,
        arena={"split": "val", "rows": 100, "attack_rows": 20, "benign_rows": 80},
        baselines=[
            {
                "name": "Autoencoder",
                "library": "torch",
                "pr_auc": 0.41,
                "roc_auc": 0.70,
                "fit_rows": 900,
                "note": "",
            },
            {
                "name": "ECOD",
                "library": "pyod",
                "pr_auc": 0.88,
                "roc_auc": 0.95,
                "fit_rows": 900,
                "note": "",
            },
        ],
    )

    report = render_report(beaten, budget)

    assert "**`ECOD` beats the autoencoder on this arena**" in report
    assert "reported rather than hidden" in report


def test_the_report_credits_the_autoencoder_when_it_wins(stage2, budget) -> None:
    winning = replace(
        stage2,
        arena={"split": "val", "rows": 100, "attack_rows": 20, "benign_rows": 80},
        baselines=[
            {
                "name": "Autoencoder",
                "library": "torch",
                "pr_auc": 0.91,
                "roc_auc": 0.97,
                "fit_rows": 900,
                "note": "",
            },
            {
                "name": "LOF",
                "library": "scikit-learn",
                "pr_auc": 0.55,
                "roc_auc": 0.80,
                "fit_rows": 900,
                "note": "novelty=True",
            },
        ],
    )

    report = render_report(winning, budget)

    assert "earns its complexity" in report
    assert "novelty=True" in report


def test_the_report_pairs_each_family_with_stage_1s_own_number(stage2, champion, budget) -> None:
    """Whether Stage 1 also misses a family is the question fusion turns on, so it
    is read off the model card rather than asserted."""
    paired = replace(
        stage2,
        test={
            **stage2.test,
            "family_recall": {
                "ddos": {"support": 120, "flagged": 80, "recall": 0.667},
                "port_scan": {"support": 90, "flagged": 1, "recall": 0.011},
            },
        },
        stage1_family_recall={
            "ddos": {"support": 120, "flagged": 45, "recall": 0.375},
            "port_scan": {"support": 90, "flagged": 0, "recall": 0.0},
        },
    )

    report = render_report(paired, budget)

    assert "Stage 1 recall" in report
    assert "37.5%" in report
    assert "**Stage 1 misses it too**" in report
    assert "gap in the system rather than a gap in one model" in report


def test_the_report_credits_fusion_when_the_stages_miss_different_traffic(stage2, budget) -> None:
    """The other arrangement, which is the one a cascade is actually for."""
    complementary = replace(
        stage2,
        test={
            **stage2.test,
            "family_recall": {"port_scan": {"support": 90, "flagged": 1, "recall": 0.011}},
        },
        stage1_family_recall={"port_scan": {"support": 90, "flagged": 72, "recall": 0.80}},
    )

    report = render_report(complementary, budget)

    assert "Stage 1 does better on" in report
    assert "what makes a cascade worth more than either half" in report


def test_the_family_table_omits_the_stage_1_column_when_it_is_unknown(stage2, budget) -> None:
    """An unevaluated champion leaves the card without a test block. The table
    shrinks rather than printing a column of dashes or, worse, of zeros."""
    alone = replace(stage2, stage1_family_recall={})

    report = render_report(alone, budget)

    assert "Stage 1 recall" not in report
    assert "Flagged by Stage 2" in report


def test_the_report_refuses_to_pass_a_checkpoint_that_did_not_separate(stage2, budget) -> None:
    """The brief is blunt: if the distributions do not separate, the model is not
    working and no dashboard will hide it."""
    failed = replace(
        stage2,
        test={**stage2.test, "attack_recall": 0.03, "roc_auc": 0.51},
    )

    report = render_report(failed, budget)

    assert "**The distributions do not separate.**" in report
    assert "Do not advance to fusion" in report


def test_the_report_carries_the_threshold_arithmetic_and_the_budget_gap(stage2, budget) -> None:
    report = render_report(stage2, budget)

    assert "tau_anom" in report
    assert f"{budget.max_alerts_per_day:,} alerts a day" in report
    assert "budget-equivalent threshold is recorded beside it" in report


def test_every_report_table_row_has_the_columns_its_header_promises(stage2, budget) -> None:
    """A pipe splits a Markdown cell even inside backticks, and the Stage 2 input
    transform is written `log1p(|x|)`. Unescaped, the architecture table renders
    with two phantom columns — a silent failure, since the file is still valid
    Markdown and nobody reads a report by counting its delimiters."""
    report = render_report(stage2, budget)

    widths: list[int] = []
    for line in report.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            widths = []
            continue
        cells = stripped.count("|") - stripped.count("\\|")
        widths.append(cells)
        # Every row of one table must agree with the first on its column count.
        assert cells == widths[0], f"ragged table row: {stripped}"


def test_the_report_draws_the_threshold_line_across_the_histogram(stage2, budget) -> None:
    report = render_report(stage2, budget)

    assert "reconstruction error" in report
    assert "tau_anom = " in report
    assert "#" in report


# ---------------------------------------------------------------------------
# The command
# ---------------------------------------------------------------------------


def test_the_cli_runs_end_to_end_and_writes_its_report(
    tmp_path, champion, phase3_benign, phase2_val, phase2_test
) -> None:
    processed = tmp_path / "processed"
    processed.mkdir()
    phase3_benign.to_parquet(processed / "benign_train.parquet", index=False)
    phase2_val.to_parquet(processed / "val.parquet", index=False)
    phase2_test.to_parquet(processed / "test.parquet", index=False)
    reports = tmp_path / "reports"

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "training.train_autoencoder",
            "--no-baselines",
            "--max-epochs",
            "2",
            "--batch-rows",
            "128",
            "--processed-dir",
            str(processed),
            "--artifacts-dir",
            str(champion),
            "--reports-dir",
            str(reports),
        ],
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parents[1],
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert (reports / "phase3_anomaly.md").exists()
    assert (champion / "autoencoder.pt").exists()
    assert "tau_anom" in result.stdout
