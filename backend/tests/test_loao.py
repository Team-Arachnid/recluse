"""Phase 4 -- the hold-out loop, which is the project's headline evaluation.

The real run refits a million-row model three times and scores eight hundred
thousand flows. These run on a few hundred rows, and what they pin is not the
recall a toy model achieves but the properties the claim rests on:

* a family that *is* in the training split is genuinely removed and the model
  genuinely refitted, so "it had never seen this" is a fact about the fit;
* a family the temporal split already holds out is reported as such rather
  than dressed up as a retrain that did nothing;
* the two stage columns and the Missed column account for every row of the
  family, because a table whose columns do not add up is a table with a bug;
* a family with no rows anywhere is reported as unmeasurable, not as 0%.

The last one matters more than it looks. A 0% cell reads as a detector that
failed; an absent family is a dataset that never carried it, and conflating
the two is the kind of quiet dishonesty this whole phase exists to avoid.
"""

from __future__ import annotations

import json
import math
from dataclasses import replace

import numpy as np
import pytest

from training.labels import ATTACK_FAMILIES
from training.loao import render_report, run_loao, write_outputs


@pytest.fixture
def result(two_stage_artifacts, phase2_train, phase2_val, phase2_test, budget):
    return run_loao(
        phase2_train,
        phase2_val,
        phase2_test,
        artifacts_dir=two_stage_artifacts,
        settings=budget,
    )


def fold(result, family: str):
    return next(entry for entry in result.folds if entry.held_out == family)


# ---------------------------------------------------------------------------
# What the loop holds out
# ---------------------------------------------------------------------------


def test_every_family_with_rows_anywhere_gets_a_fold(result) -> None:
    measured = {entry.held_out for entry in result.folds}

    assert measured == {"dos", "brute_force", "web_attack", "infiltration", "ddos", "port_scan"}


def test_a_family_with_no_rows_anywhere_is_unmeasurable_not_zero(result) -> None:
    """`botnet` is absent from all three synthetic splits.

    Printing it as a 0% row would read as a detector that missed every botnet
    flow, when what happened is that there were none to miss.
    """
    assert "botnet" not in {entry.held_out for entry in result.folds}
    assert "botnet" in result.arena.unmeasurable
    assert "no rows" in result.arena.unmeasurable["botnet"]


def test_a_family_in_the_training_split_is_removed_and_the_model_refitted(result) -> None:
    dos = fold(result, "dos")

    assert dos.rows_in_fit == 200
    assert dos.refitted is True
    assert "dos" not in dos.classes
    assert dos.training_rows == result.control.training_rows - 200


def test_a_family_the_temporal_split_already_held_out_is_not_refitted(result) -> None:
    """Removing zero rows and calling the result a retrain would be theatre.

    The honest statement is stronger anyway: the split put this family on a day
    the classifier never trained on, so no fold had to be constructed.
    """
    ddos = fold(result, "ddos")

    assert ddos.rows_in_split == 0
    assert ddos.refitted is False
    assert "temporal split" in (ddos.reuse_reason or "")


def test_a_family_below_the_support_floor_is_not_refitted_either(result) -> None:
    """`web_attack` has five rows on the training days and a vocabulary that
    excludes it. They are in the split and were never in the fit, so the fold
    records both numbers rather than implying the removal changed something."""
    web = fold(result, "web_attack")

    assert web.rows_in_split == 5
    assert web.rows_in_fit == 0
    assert web.refitted is False
    assert "support floor" in (web.reuse_reason or "")


def test_the_control_keeps_every_family_in_training(result) -> None:
    assert result.control.held_out is None
    assert result.control.refitted is True
    assert "dos" in result.control.classes


# ---------------------------------------------------------------------------
# What the table says
# ---------------------------------------------------------------------------


def test_the_stage_columns_and_the_missed_column_account_for_every_row(result) -> None:
    for entry in result.folds:
        outcome = entry.families[entry.held_out]
        assert outcome.stage1_caught + outcome.stage2_caught + outcome.missed == outcome.support
        assert outcome.support > 0


def test_the_recalls_sum_to_one(result) -> None:
    """The cascade is why: Stage 2 only ever sees what Stage 1 did not name, so
    the three fractions partition the family rather than overlapping."""
    for entry in result.folds:
        outcome = entry.families[entry.held_out]
        total = outcome.stage1_recall + outcome.stage2_recall + outcome.miss_rate
        assert total == pytest.approx(1.0)


def test_a_held_out_family_can_still_be_caught_but_never_named(result) -> None:
    """Caught is not the same as named, and the table has to say which it is.

    Stage 1's score is the largest single attack-class probability, so a family
    the fold has no column for can still clear `tau_sup` under another family's
    label -- a DoS flood alerting as `brute_force` here, DDoS alerting as `dos`
    on the real capture. That is a true positive an analyst can work, and it is
    counted as caught for the same reason Phase 2 counts it. What it is not is
    classification, and the naming rate is what keeps a recall figure from
    implying otherwise.
    """
    control = result.control.families["dos"]
    held_out = fold(result, "dos").families["dos"]

    assert control.stage1_named > 0
    assert held_out.stage1_named == 0
    assert held_out.stage1_named_rate == 0.0


def test_the_benign_reference_is_a_split_no_stage_was_fitted_on(result, phase2_test) -> None:
    """Stage 1 was fitted on the training days, Stage 2 on their benign rows,
    and both thresholds were cut on the validation day. The test day's benign
    traffic is the only negative class left that none of that touched."""
    benign = (phase2_test["label"] == "BENIGN").sum()

    assert result.arena.benign.name == "benign"
    assert result.arena.benign.splits == ["test"]
    assert result.control.benign.rows == benign


def test_every_fold_reports_what_its_recall_cost_in_false_positives(result) -> None:
    """A recall figure with no false-positive rate beside it is not a result."""
    for entry in [result.control, *result.folds]:
        assert 0.0 <= entry.benign.fpr <= 1.0
        assert entry.benign.alerts_per_analyst_hour >= 0.0


def test_stage2_scores_the_arena_through_the_unchanged_autoencoder(
    result, two_stage_artifacts
) -> None:
    """The one thing LOAO does not vary.

    Recomputed here straight from the artifact the API loads, against the array
    the folds were scored with: if the loop had quietly refitted or rescaled
    Stage 2 per fold, these would differ.
    """
    from app.inference import load_bundle
    from training.autoencoder import reconstruction_error

    served = load_bundle(two_stage_artifacts)
    slice_ = result.arena.attacks["ddos"]

    assert np.allclose(
        slice_.anomaly_score, reconstruction_error(served.autoencoder, slice_.matrix)
    )
    assert {entry.tau_anom for entry in result.folds} == {served.tau_anom}


# ---------------------------------------------------------------------------
# The deliverable
# ---------------------------------------------------------------------------


def test_the_report_carries_the_table_the_checkpoint_asks_for(result, budget) -> None:
    rendered = render_report(result, budget)

    for heading in (
        "# Leave-one-attack-out",
        "## The table",
        "## What each fold held out",
        "## What the recall cost",
        "## With the family in training, and without",
        "## Reading the misses",
        "## Method",
    ):
        assert heading in rendered

    assert (
        "| Held-out family | Rows | Caught by Stage 1 | Caught by Stage 2 | Total recall | Missed |"
        in rendered
    )


def test_the_report_names_every_measured_family_and_the_unmeasurable_one(result, budget) -> None:
    rendered = render_report(result, budget)

    for family in ATTACK_FAMILIES:
        assert family in rendered


def test_the_report_states_the_proxy_limit_of_the_whole_evaluation(result, budget) -> None:
    """Held-out *known* attacks are a proxy for novel ones, not proof of them.
    A table that does not say so is overclaiming."""
    rendered = render_report(result, budget)

    assert "proxy" in rendered


def test_write_outputs_produces_the_report_and_a_machine_readable_record(
    tmp_path, result, budget
) -> None:
    report_path, metrics_path = write_outputs(
        result, tmp_path / "artifacts", tmp_path / "reports", budget
    )

    assert report_path.name == "loao.md"
    assert metrics_path.name == "metrics_loao.json"

    record = json.loads(metrics_path.read_text(encoding="utf-8"))
    rows = {entry["held_out"]: entry for entry in record["folds"]}
    assert set(rows) == {entry.held_out for entry in result.folds}
    assert set(rows["dos"]["headline"]) >= {
        "support",
        "stage1_recall",
        "stage2_recall",
        "total_recall",
        "miss_rate",
    }


def test_the_record_contains_no_nan(tmp_path, result, budget) -> None:
    """`NaN` is not valid JSON. `json.dumps` emits it anyway and every strict
    parser downstream -- including the browser's -- then rejects the file."""
    _, metrics_path = write_outputs(result, tmp_path / "artifacts", tmp_path / "reports", budget)
    text = metrics_path.read_text(encoding="utf-8")

    assert "NaN" not in text
    assert "Infinity" not in text
    json.loads(text, parse_constant=lambda name: pytest.fail(f"non-JSON constant {name}"))


def test_the_record_carries_no_feature_matrices(tmp_path, result, budget) -> None:
    """The arena is eight hundred thousand flows on the real run. Its
    provenance belongs in the record; a copy of the traffic does not."""
    _, metrics_path = write_outputs(result, tmp_path / "artifacts", tmp_path / "reports", budget)

    assert metrics_path.stat().st_size < 200_000


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------


def test_the_loop_refuses_to_run_without_stage_2(
    tmp_path, phase2_train, phase2_val, phase2_test, budget
) -> None:
    """Without the autoencoder the Stage 2 column is structurally empty, and a
    table reporting 0% novel recall would be describing a missing file rather
    than a model that failed."""
    from training.features import PORT_ENCODING_BUCKETED
    from training.loao import MissingStage
    from training.train_supervised import promote
    from training.train_supervised import train as train_supervised

    artifacts = tmp_path / "stage1-only"
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

    with pytest.raises(MissingStage, match="Stage 2"):
        run_loao(phase2_train, phase2_val, phase2_test, artifacts_dir=artifacts, settings=budget)


def test_the_loop_refuses_to_run_without_any_artifacts(
    tmp_path, phase2_train, phase2_val, phase2_test, budget
) -> None:
    from training.loao import MissingStage

    with pytest.raises(MissingStage):
        run_loao(phase2_train, phase2_val, phase2_test, artifacts_dir=tmp_path, settings=budget)


def test_a_fold_threshold_is_cut_from_the_budget_not_from_a_default(result, budget) -> None:
    for entry in [result.control, *result.folds]:
        assert entry.tau_sup != 0.5
        assert math.isfinite(entry.tau_sup)
    assert result.target_fpr == budget.target_fpr


def test_the_champions_card_gains_a_compact_hold_out_summary(
    result, two_stage_artifacts, budget, tmp_path
) -> None:
    """`GET /metrics/model` serves the LOAO panel, and `app/inference.py` already
    reads the card at startup. Putting the handful of numbers the panel draws
    there saves the serving path a second file; the full record, arena
    provenance and per-fold thresholds included, stays in `metrics_loao.json`.
    """
    write_outputs(result, two_stage_artifacts, tmp_path / "reports", budget)

    card = json.loads((two_stage_artifacts / "model_card.json").read_text(encoding="utf-8"))

    assert card["loao"]["champion"] == result.version
    assert {entry["held_out"] for entry in card["loao"]["folds"]} == {
        entry.held_out for entry in result.folds
    }
    assert "botnet" in card["loao"]["unmeasurable"]
    # Stage 1's and Stage 2's own entries are an addition's neighbours, not its
    # casualties: the card is one record of one served pair.
    assert card["thresholds"]["tau_anom"] == result.tau_anom
    assert card["stage2"]["algorithm"] == "autoencoder"


def test_recording_against_a_card_from_another_schema_is_refused(
    result, two_stage_artifacts
) -> None:
    """A card and a run that disagree on the feature contract mean something
    rewrote the canonical pair mid-run. Writing the table onto it anyway would
    attribute these numbers to a model that did not produce them."""
    from training.loao import MissingStage, update_model_card

    card_path = two_stage_artifacts / "model_card.json"
    card = json.loads(card_path.read_text(encoding="utf-8"))
    card["schema_hash"] = "sha256:measured-against-something-else"
    card_path.write_text(json.dumps(card), encoding="utf-8")

    with pytest.raises(MissingStage, match="schema"):
        update_model_card(result, two_stage_artifacts)


# ---------------------------------------------------------------------------
# Honesty of the generated prose
#
# The report's sentences are generated from the measured numbers, which is what
# stops a rerun leaving stale prose behind. It also means a wrong branch
# produces a sentence that contradicts the table directly above it.
# ---------------------------------------------------------------------------


def test_the_verdict_credits_the_stage_that_actually_caught_the_family() -> None:
    """A family Stage 1 generalised onto must not be narrated as a Stage 2 win.

    This is the real shape of the `web_attack` row: HTTP brute force looks like
    the FTP and SSH brute force Stage 1 *was* trained on, so Stage 1 takes 88.6%
    and Stage 2 adds 4.6%. Total recall is high, and crediting that total to
    Stage 2 would be a generated sentence disagreeing with its own table.
    """
    from training.loao import FamilyOutcome, _verdict

    stage1_carried = FamilyOutcome(
        family="web_attack",
        splits=["val"],
        support=2154,
        stage1_caught=1909,
        stage1_named=0,
        stage2_caught=99,
        stage2_standalone_caught=120,
        stage2_budget_caught=10,
        stage1_pr_auc=0.9,
        stage2_pr_auc=0.03,
    )
    stage2_carried = replace(stage1_carried, stage1_caught=0, stage2_caught=1626)

    assert "Stage 1" in _verdict(stage1_carried)
    assert "never been shown" in _verdict(stage2_carried)
    assert "Stage 2 surfaced" not in _verdict(stage1_carried)


def test_a_family_with_few_rows_gets_an_interval_not_a_decimal_point(result, budget) -> None:
    """`infiltration` is 36 rows on the real capture.

    "44.4%" to one decimal place on sixteen caught flows implies a precision the
    sample cannot support, and the Wilson interval is the one that stays honest
    at small n and at proportions near zero or one.
    """
    rendered = render_report(result, budget)

    assert "95% interval" in rendered


def test_stage2_is_measured_on_its_own_as_well_as_in_the_cascade(result) -> None:
    """Two different questions, and the cascade only answers one of them.

    Stage 2's column in the headline table is its *marginal* contribution:
    what it adds on rows Stage 1 passed through. That is lower than what Stage 2
    can do alone, because both stages respond to the same extreme flows. Without
    the standalone figure this report and the Phase 3 one appear to disagree.
    """
    alone = result.stage2_alone

    for family, outcome in result.control.families.items():
        standalone = alone.families[family]
        assert standalone["caught"] >= outcome.stage2_caught
        assert 0.0 <= standalone["recall"] <= 1.0


def test_stage2_is_also_measured_at_the_threshold_that_fits_the_queue(result, budget) -> None:
    """`tau_anom` is a benign percentile; the budget is a staffing number, and
    Phase 3 measured them as an order of magnitude apart. Reporting the recall
    the shipped threshold buys without the recall the affordable one buys leaves
    the alerts-per-hour column looking like a broken system instead of a choice.
    """
    alone = result.stage2_alone
    rendered = render_report(result, budget)

    assert alone.budget_tau is not None
    assert alone.budget_tau > alone.tau_anom
    # The budget threshold is the tighter of the two, so it cannot alert on more
    # -- of benign traffic or of any family. Monotonic rather than strictly
    # smaller: a split whose benign rows all score below both thresholds alerts
    # zero times at each, and that is the correct answer rather than a tie.
    assert alone.benign_alerts_at_budget <= alone.benign_alerts
    for stats in alone.families.values():
        assert stats["recall_at_budget"] <= stats["recall"]
    assert "## Stage 2 on its own" in rendered


def test_the_report_states_the_cost_of_the_affordable_threshold(result, budget) -> None:
    """The budget threshold is not a free fix, and the table alone does not say so.

    On the real capture Stage 2's recall on DoS falls from 75.5% at the shipped
    percentile to 4.6% at the threshold that fits the queue, and four families
    go to zero. Every other number in this report has a generated sentence
    attached; leaving the one that decides whether the system is deployable as a
    column the reader has to interpret would be the odd one out.
    """
    rendered = render_report(result, budget)

    assert "## Stage 2 on its own" in rendered
    assert "dedup" in rendered
    assert "falls" in rendered
