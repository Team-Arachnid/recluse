"""Phase 7 -- the retraining pipeline: analyst labels to challenger to promotion.

Five steps, and a gate:

1. Read the analyst verdicts no retrain has consumed yet.
2. Append them to the training split as labelled rows and fit a challenger.
3. Fit a control: the same configuration on the *unaugmented* split, in this run.
4. Evaluate all three on the same held-out splits, in the same run, same code.
5. Promote only on improvement, and write the comparison down either way.

Four decisions worth defending.

**The gate is the validation day, not the test day.** The brief says "the same
held-out set", and both are held out -- but the validation day is the one that
exists for model selection, and the test day is the one the README quotes. Gating
on the test day would make every retrain a selection step on it, and the reported
test numbers would creep upward over successive runs while describing nothing.
Both models are measured on both splits and both pairs of numbers are recorded;
only the validation pair decides. ``--gate test`` exists and says what it costs.

**A false positive is the label that moves the model.** It is a flow the
classifier called an attack and a human called benign -- a hard negative, which
is the kind of example that moves a boundary. A confirmed true positive mostly
agrees with what the model already did. And a confirmed true positive on an
*unclassified anomaly* -- the most valuable label a SOC can produce -- cannot
enter Stage 1's vocabulary at all, because the model is multiclass by family and
the analyst did not supply a family. That row is counted and reported and left
out, because guessing its family would be fabricating the one thing nobody said.

**There is a control arm, and it answers a different question from the gate.**
The gate is "should the challenger replace what is serving", which is
challenger-minus-champion. The control -- the same configuration fitted on the
*unaugmented* split in the same run -- answers "did the labels teach the model
anything", which is challenger-minus-control with everything else held constant.
Those come apart the moment a labelled challenger has been promoted: the champion
then carries earlier labels, so the gate compares two labelled models and says
nothing about the labels. Both numbers are reported, and the gate turns on the
first. `metrics_loao.json` carries a control fold for the same reason -- an arm
that is not a candidate, kept because it is what makes the others interpretable.

**Nothing here runs inside a request.** ``POST /retrain`` writes a row and
returns; this module picks it up. Fitting a model in a request handler is the
anti-pattern the brief names outright, and an admin request is still a request.

    python -m training.retrain                 # take the oldest pending request
    python -m training.retrain --now           # run without one
    python -m training.retrain --dry-run       # measure, promote nothing
    python -m training.retrain --no-control    # a third faster, no label effect
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import shutil
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import select

from app.config import settings
from app.db import session_scope
from app.feedback import build_benign_pool, labelled_flows, mark_consumed
from app.models import RetrainRun
from training.console import echo
from training.evaluate import evaluate_split, load_model
from training.features import LABEL_COLUMN
from training.labels import map_labels
from training.train_supervised import (
    DEFAULT_PORT_ENCODING,
    MODEL_CARD,
    load_split,
    read_model_card,
)
from training.train_supervised import train as fit_stage1

logger = logging.getLogger(__name__)

REPORT_FILENAME = "phase7_retrain.md"

# Where a challenger's artifacts are written. Its own directory, so a fit that
# loses never touches the pair that is serving -- declining to promote has to be
# a no-op on disk, not a restore.
CHALLENGER_DIR = "challenger"

# Where the control fit lands. Separate from the challenger's directory so the
# two pairs cannot be mistaken for each other on disk -- which matters, because
# one of them is a candidate to serve and the other is a measurement.
CONTROL_DIR = "control"

# Which split decides. See the module docstring.
DEFAULT_GATE = "val"

# How much better a challenger has to be. Zero would promote on floating-point
# noise, and a promotion is a change to what scores production traffic.
MIN_IMPROVEMENT = 1e-4


class RetrainError(RuntimeError):
    """The pipeline cannot produce a comparison worth acting on."""


@dataclass
class Comparison:
    """Champion, control and challenger, on both splits, measured in one run.

    Two different questions, and conflating them is the mistake this class exists
    to prevent:

    * **Should the challenger replace what is serving?** That is
      ``challenger - champion``, and it is what the gate decides on. It is a
      deployment question.
    * **Did the analyst labels teach the model anything?** That is
      ``challenger - control``, where the **control** is the same configuration
      fitted on the *unaugmented* split in this same run. Everything except the
      labels is held constant, so the difference is the labels. It is a
      measurement question, and it has no bearing on whether to switch.

    They come apart as soon as a labelled challenger has been promoted once: the
    champion then already carries earlier labels, so ``challenger - champion``
    compares two labelled models and says nothing about the labels at all. The
    first version of this class had only the first comparison and read the
    champion-to-control gap as a run-to-run noise floor. It is not one -- the
    control reproduced the original no-label fit to four decimal places, which is
    what a deterministic fit does. That gap was the previous run's label effect
    wearing the wrong name, and a gate justified by it would have been justified
    by a quantity it does not measure.

    So the noise claim is gone and the label effect is reported in its own right.
    ``metrics_loao.json`` carries a control fold for the same reason: an arm that
    is not a candidate, kept because it is the only thing that makes the other arms
    interpretable.
    """

    gate: str
    champion_version: str
    challenger_version: str
    champion: dict[str, dict[str, float]] = field(default_factory=dict)
    challenger: dict[str, dict[str, float]] = field(default_factory=dict)
    control: dict[str, dict[str, float]] = field(default_factory=dict)
    control_version: str | None = None
    improvement: float = 0.0
    promoted: bool = False
    decision: str = ""

    def _score(self, arm: dict[str, dict[str, float]]) -> float:
        return float(arm.get(self.gate, {}).get("pr_auc", float("nan")))

    def gate_scores(self) -> tuple[float, float]:
        return self._score(self.champion), self._score(self.challenger)

    @property
    def label_effect(self) -> float | None:
        """What the analyst labels were worth, on the gate split.

        Challenger minus control, with the data, the seed, the code and the run all
        held constant -- so the only difference between the two fits is the labels.
        None without a control, in which case the labels' contribution was not
        measured and the decision text says so rather than implying a number.
        """
        if not self.control:
            return None
        return self._score(self.challenger) - self._score(self.control)

    @property
    def control_matches_champion(self) -> bool | None:
        """Whether the control landed where the champion did.

        True means the champion was fitted on the same data the control just
        reproduced -- so no labels have been promoted yet, and ``improvement`` and
        ``label_effect`` are the same quantity. False means a labelled challenger
        has been promoted before, and the two questions have come apart.
        """
        if not self.control:
            return None
        return abs(self._score(self.control) - self._score(self.champion)) < MIN_IMPROVEMENT

    def render(self) -> str:
        arms = [
            ("champion", self.champion),
            ("control", self.control),
            ("challenger", self.challenger),
        ]
        present = [(name, arm) for name, arm in arms if arm]

        lines = [
            f"gate              {self.gate} (PR-AUC)",
            f"champion          {self.champion_version}",
            f"challenger        {self.challenger_version}",
        ]
        if self.control_version:
            lines.append(f"control           {self.control_version}  (same config, no labels)")
        lines += [
            "",
            "split    " + "".join(f"{name:>13}" for name, _ in present) + f"{'delta':>11}",
        ]

        for split in sorted(set(self.champion) | set(self.challenger)):
            cells = "".join(
                f"{arm.get(split, {}).get('pr_auc', float('nan')):>13.4f}" for _, arm in present
            )
            delta = self.challenger.get(split, {}).get("pr_auc", float("nan")) - self.champion.get(
                split, {}
            ).get("pr_auc", float("nan"))
            marker = "  <- gate" if split == self.gate else ""
            lines.append(f"{split:<8}{cells}{delta:>+11.4f}{marker}")

        effect = self.label_effect
        if effect is not None:
            lines += [
                "",
                f"vs champion       {self.improvement:+.4f}  (should the challenger "
                "replace what is serving -- the gate)",
                f"vs control        {effect:+.4f}  (what the analyst labels were worth, "
                "everything else held constant)",
            ]
            if self.control_matches_champion:
                lines.append(
                    "                  the control landed on the champion, so no labels "
                    "have been promoted yet and the two are the same quantity"
                )
            else:
                lines.append(
                    "                  the control did not land on the champion: a "
                    "labelled challenger has been promoted before, so the gate compares "
                    "two labelled models"
                )
        lines += ["", f"decision          {self.decision}"]
        return "\n".join(lines)


def _labels_as_frame(labelled: Any, feature_columns: list[str]) -> pd.DataFrame:
    """The analyst-labelled flows as a frame shaped like a training split.

    Reindexed onto the split's own columns so the concatenation cannot introduce
    a column the preprocessing has never seen. A raw flow is the record the
    pipeline scored, which is already the cleaned shape -- but it travelled
    through JSON, so a column the flow happened not to carry comes back absent
    rather than as a zero.
    """
    rows: list[dict[str, Any]] = []
    for flow in labelled.benign:
        rows.append({**flow, LABEL_COLUMN: "benign"})
    for flow, family in labelled.attacks:
        rows.append({**flow, LABEL_COLUMN: family})

    if not rows:
        return pd.DataFrame(columns=[*feature_columns, LABEL_COLUMN])

    frame = pd.DataFrame(rows)
    keep = [column for column in feature_columns if column != LABEL_COLUMN]
    ordered = frame.reindex(columns=[*keep, LABEL_COLUMN])
    # Absent features fill with zero, which is what `build_feature_matrix` does
    # for a missing column anyway -- doing it here keeps the two paths agreeing
    # and leaves the label column alone.
    ordered[keep] = ordered[keep].fillna(0.0)
    return ordered


def measure_model(model: Any, splits: dict[str, pd.DataFrame]) -> dict[str, dict[str, float]]:
    """Score one loaded model on every split, through the Phase 2 evaluator.

    The same function the model-performance screen's numbers come from, so a
    retrain comparison and the dashboard cannot disagree about what PR-AUC means.
    """
    scores: dict[str, dict[str, float]] = {}
    for name, frame in splits.items():
        evaluation = evaluate_split(model, frame, name, settings)
        scores[name] = {
            "pr_auc": float(evaluation.pr_auc),
            "roc_auc": float(evaluation.roc_auc),
            "attack_recall_at_tau": float(
                evaluation.volume.get("attack_recall_at_tau", float("nan"))
            ),
            "fpr": float(evaluation.volume.get("fpr", float("nan"))),
            "alerts_per_analyst_hour": float(
                evaluation.volume.get("alerts_per_analyst_hour", float("nan"))
            ),
            "rows": int(evaluation.rows),
        }
    return scores


def evaluate_arms(
    champion_dir: Path,
    challenger_dir: Path,
    *,
    splits: dict[str, pd.DataFrame],
    challenger_algorithm: str,
    control_dir: Path | None = None,
) -> dict[str, tuple[dict[str, dict[str, float]], str]]:
    """Measure every arm on every split, in this run, with one code path.

    Reading the champion's score off its stored model card instead would compare
    two evaluations rather than two models: the card's number was measured
    against the data as it was that day, and the card is also the thing a
    promotion rewrites.
    """
    arms: dict[str, tuple[dict[str, dict[str, float]], str]] = {}

    champion = load_model(champion_dir)
    arms["champion"] = (measure_model(champion, splits), str(champion.payload["version"]))

    challenger = load_model(challenger_dir, algorithm=challenger_algorithm)
    arms["challenger"] = (measure_model(challenger, splits), str(challenger.payload["version"]))

    if control_dir is not None:
        control = load_model(control_dir, algorithm=challenger_algorithm)
        arms["control"] = (measure_model(control, splits), str(control.payload["version"]))

    return arms


def publish_challenger(
    *,
    challenger_dir: Path,
    artifacts_dir: Path,
    algorithm: str,
    comparison: Comparison,
    run_record: dict[str, Any],
) -> None:
    """Make the challenger the served champion.

    A file copy plus a new model card, which is what `promote` does in Phase 2 --
    except the source is the challenger directory, so the previous champion's
    pair stays exactly where it was. A regression is a copy back, not a retrain.
    """
    shutil.copy2(
        challenger_dir / f"supervised_{algorithm}.pkl", artifacts_dir / "supervised_model.pkl"
    )
    shutil.copy2(
        challenger_dir / f"preprocessing_{algorithm}.pkl", artifacts_dir / "preprocessing.pkl"
    )

    previous = read_model_card(artifacts_dir) or {}
    card = {
        "version": comparison.challenger_version,
        "algorithm": algorithm,
        "stage": "stage1_supervised",
        "trained_at": run_record.get("trained_at"),
        "trained_on": run_record.get("trained_on"),
        "schema_hash": run_record.get("schema_hash"),
        "classes": run_record.get("classes"),
        "held_out_families": run_record.get("held_out_families"),
        "port_encoding": run_record.get("port_encoding"),
        "hyperparameters": run_record.get("hyperparameters"),
        "thresholds": {
            "tau_sup": (run_record.get("threshold") or {}).get("tau"),
            # Stage 2's threshold belongs to the autoencoder and is untouched by a
            # Stage 1 retrain. Carried over rather than recomputed, so a promotion
            # cannot silently move the anomaly threshold the dashboard draws.
            "tau_anom": (previous.get("thresholds") or {}).get("tau_anom"),
        },
        "validation": run_record.get("validation"),
        "previous_champion": comparison.champion_version,
        "promoted_by": "phase7_retrain",
        "promotion": asdict(comparison),
        "run": run_record,
    }
    (artifacts_dir / MODEL_CARD).write_text(json.dumps(card, indent=2), encoding="utf-8")


def render_report(
    comparison: Comparison,
    *,
    labelled: Any,
    pool: Any,
    gate: str,
) -> str:
    """The write-up. The brief asks for the comparison to be logged."""
    champion_score, challenger_score = comparison.gate_scores()
    lines = [
        "# Phase 7 -- retraining from analyst verdicts",
        "",
        f"Measured {dt.datetime.now(dt.UTC).isoformat(timespec='seconds')}.",
        "",
        "## What the analysts supplied",
        "",
        "```",
        f"false positives   {len(labelled.benign):,}  -> benign training rows",
        f"true positives    {len(labelled.attacks):,}  -> attack rows, labelled by family",
        f"unnamed attacks   {labelled.unnamed_attacks:,}  "
        "-> confirmed attacks with no family; left out",
        f"undecided         {labelled.unsure:,}  -> recorded, not trained on",
        "```",
        "",
        "A false positive is the label that moves the model: a flow the classifier",
        "called an attack and a human called benign is a hard negative, which is the",
        "kind of example that moves a boundary. A confirmed true positive mostly",
        "agrees with what the model already did.",
        "",
        "The unnamed attacks are the honest hole in the loop. They are the most",
        "valuable labels a SOC produces -- traffic Stage 2 caught that Stage 1 could",
        "not name -- and they cannot enter a classifier that is multiclass by family",
        "until a human says which family. Guessing would fabricate the one thing",
        "nobody said.",
        "",
        "## The benign refit pool, and its guards",
        "",
        "```",
        pool.render(),
        "```",
        "",
        "Nothing enters that pool without an explicit false-positive confirmation,",
        "and no single source host may exceed its cap. Without both, anyone who can",
        "generate enough traffic and get it waved through can teach the baseline",
        "that their traffic is normal.",
        "",
        "## Champion against challenger",
        "",
        "```",
        comparison.render(),
        "```",
        "",
        f"**The gate is the {gate} split.** Both splits are held out, but this is the",
        "one that exists for model selection. Gating on the test day would make every",
        "retrain a selection step on it, and the test numbers this project quotes",
        "would creep upward over successive runs while describing less and less.",
        "",
        "## A caveat this demo cannot design away",
        "",
        "In a real deployment the analyst labels come from production traffic, and the",
        "held-out splits stay untouched. Here they come from a *replay of a held-out",
        "split*, because that is the only traffic this project has. So:",
        "",
        "- Labels drawn from a replay of the **test** day leave the validation gate",
        "  clean, which is why the promotion decision above is still sound.",
        "- But the **test** numbers reported after such a retrain are no longer an",
        "  unbiased estimate: some of those rows are now in the training set, with",
        "  labels a human derived from the same ground truth the evaluation scores",
        "  against.",
        "",
        "Read the gate column as the decision and the test column as contaminated",
        "once a retrain has consumed labels from the test day. The clean comparison",
        "is the one in `reports/phase2_supervised.md`, measured before any feedback",
        "existed.",
        "",
        f"The challenger {'improved' if comparison.promoted else 'did not improve'} on it: "
        f"{champion_score:.4f} to {challenger_score:.4f} "
        f"({challenger_score - champion_score:+.4f}).",
        "",
        "## The control arm, and the two questions it separates",
        "",
        "A third model is fitted in every run: the same configuration on the same",
        "unaugmented data, by this code, now. It is not a candidate to serve. It is",
        "there because there are two different questions here and only one of them",
        "is the gate's:",
        "",
        "- **Should the challenger replace what is serving?** challenger - champion.",
        "  A deployment question, and the one promotion turns on.",
        "- **Did the analyst labels teach the model anything?** challenger - control,",
        "  with the data, the seed and the code held constant, so the only difference",
        "  between the two fits is the labels.",
        "",
        (
            f"On {gate}: **{comparison.improvement:+.4f}** against the champion, "
            f"**{comparison.label_effect:+.4f}** against the control."
            if comparison.label_effect is not None
            else "No control was fitted in this run, so the labels' contribution was not measured."
        ),
        "",
        (
            "The control landed on the champion, so no labelled challenger has been "
            "promoted yet and the two numbers above are the same quantity."
            if comparison.control_matches_champion
            else "The control did not land on the champion, because a labelled "
            "challenger has been promoted before. The gate is therefore comparing two "
            "labelled models, which is exactly why the label effect has to be measured "
            "against the control rather than read off the gate."
        ),
        "",
        "An earlier version of this pipeline read the champion-to-control gap as a",
        "run-to-run noise floor and gated on it. It is not one: the control reproduced",
        "the original no-label fit to four decimal places, which is what a",
        "deterministic fit does. That gap was the previous run's label effect wearing",
        "the wrong name, and a gate justified by it would have been justified by a",
        "quantity it does not measure.",
        "",
        "`metrics_loao.json` carries a control fold for the same reason: an arm that is",
        "not a candidate, kept because it is what makes the others interpretable.",
        "",
        comparison.decision,
        "",
    ]
    return "\n".join(lines)


def run_retrain(
    *,
    artifacts_dir: Path | None = None,
    data_dir: Path | None = None,
    algorithm: str = "lgbm",
    gate: str = DEFAULT_GATE,
    dry_run: bool = False,
    run_id: int | None = None,
    control: bool = True,
    **fit_options: Any,
) -> Comparison:
    """Fit a challenger from analyst labels and decide whether it ships.

    ``control`` adds a third fit -- the same configuration on the *unaugmented*
    split -- which is the only way to measure what the labels were worth once a
    labelled challenger has been promoted: after that, challenger-minus-champion
    compares two labelled models. On by default for that reason. Pass False to
    trade the measurement for a third of the runtime.
    """
    artifacts = artifacts_dir or settings.artifacts_path
    processed = (data_dir or settings.data_path) / "processed"
    challenger_dir = artifacts / CHALLENGER_DIR
    control_dir = artifacts / CONTROL_DIR

    if gate not in ("val", "test"):
        raise RetrainError(f"gate must be 'val' or 'test', got {gate!r}")

    with session_scope() as session:
        labelled = labelled_flows(session)
        pool = build_benign_pool(session)

        if labelled.total == 0:
            raise RetrainError(
                "no unconsumed analyst verdicts. There is nothing for a retrain to "
                "learn from, and refitting on the same data would produce a "
                "challenger that differs from the champion only by random seed."
            )

        train_frame = load_split(processed, "train")
        val_frame = load_split(processed, "val")
        test_frame = load_split(processed, "test")

        feedback = _labels_as_frame(labelled, list(train_frame.columns))
        augmented = (
            pd.concat([train_frame, feedback], ignore_index=True) if len(feedback) else train_frame
        )
        # Raises on an unmapped label before anything is fitted, so a bad label is
        # a refusal rather than a wasted twenty-minute fit.
        map_labels(augmented[LABEL_COLUMN])

        logger.info(
            "fitting a challenger on %s rows (%s from analyst verdicts)",
            len(augmented),
            len(feedback),
        )

        challenger_dir.mkdir(parents=True, exist_ok=True)
        run = fit_stage1(
            augmented,
            val_frame,
            artifacts_dir=challenger_dir,
            algorithm=algorithm,
            port_encoding=DEFAULT_PORT_ENCODING,
            settings=settings,
            **fit_options,
        )
        # `train` hardcodes `trained_on` to the split it expects. Correcting it
        # here is not cosmetic: the model card is the audit record for what a
        # served model learned from, and a card claiming this was fitted on the
        # Tuesday+Wednesday split alone would omit the analyst labels that are
        # the entire reason the run happened.
        run.trained_on = (
            "CICIDS2017 Tuesday+Wednesday (data/processed/train.parquet) plus "
            f"{len(feedback):,} analyst-labelled flow(s): {len(labelled.benign):,} "
            f"confirmed false positives as benign, {len(labelled.attacks):,} confirmed "
            "attacks labelled by family"
        )

        if control:
            # The same configuration and the same unaugmented data, fitted by
            # this code in this run. The challenger's distance from it is what the
            # labels were worth, with everything else held constant.
            logger.info("fitting the control on %s unaugmented rows", len(train_frame))
            control_dir.mkdir(parents=True, exist_ok=True)
            fit_stage1(
                train_frame,
                val_frame,
                artifacts_dir=control_dir,
                algorithm=algorithm,
                port_encoding=DEFAULT_PORT_ENCODING,
                settings=settings,
                **fit_options,
            )

        arms = evaluate_arms(
            artifacts,
            challenger_dir,
            splits={"val": val_frame, "test": test_frame},
            challenger_algorithm=algorithm,
            control_dir=control_dir if control else None,
        )

        champion_scores, champion_version = arms["champion"]
        challenger_scores, challenger_version = arms["challenger"]
        control_scores, control_version = arms.get("control", ({}, None))

        comparison = Comparison(
            gate=gate,
            champion_version=champion_version,
            challenger_version=challenger_version,
            champion=champion_scores,
            challenger=challenger_scores,
            control=control_scores,
            control_version=control_version,
        )
        before, after = comparison.gate_scores()
        comparison.improvement = after - before

        # The gate is the deployment question and nothing else: does the
        # challenger score better than what is serving, on the split reserved for
        # model selection. `MIN_IMPROVEMENT` is a floating-point guard -- promoting
        # on the twelfth decimal place would churn the served model for nothing.
        #
        # The label effect is reported beside the decision rather than folded into
        # it. The two can disagree, and when they do that disagreement is the most
        # informative line in the report: labels that helped against a champion
        # that is still ahead means the previous run's labels are doing more work
        # than this run's.
        effect = comparison.label_effect
        if effect is None:
            attribution = "No control was fitted, so what the labels contributed was not measured."
        elif effect > MIN_IMPROVEMENT:
            attribution = (
                f"The analyst labels were worth {effect:+.4f} against a control fitted "
                "without them, with everything else held constant."
            )
        elif effect < -MIN_IMPROVEMENT:
            attribution = (
                f"The analyst labels cost {effect:+.4f} against a control fitted without "
                "them -- this run's labels made the model worse, not better."
            )
        else:
            attribution = (
                "The analyst labels changed nothing measurable against a control fitted "
                "without them."
            )

        if comparison.improvement > MIN_IMPROVEMENT:
            comparison.promoted = not dry_run
            verb = "Promoted" if not dry_run else "Would promote"
            suffix = "" if not dry_run else " Dry run, nothing written."
            comparison.decision = (
                f"{verb}: {gate} PR-AUC improved by {comparison.improvement:+.4f} over the "
                f"serving champion. {attribution}{suffix}"
            )
            if not dry_run:
                publish_challenger(
                    challenger_dir=challenger_dir,
                    artifacts_dir=artifacts,
                    algorithm=algorithm,
                    comparison=comparison,
                    run_record=asdict(run),
                )
        else:
            comparison.promoted = False
            comparison.decision = (
                f"Not promoted: {gate} PR-AUC moved by {comparison.improvement:+.4f} "
                f"against the serving champion. {attribution} The champion stays, the "
                "challenger stays on disk under its own name, and this row is the "
                "evidence the gate works."
            )

        # Consumed either way. A label that was evaluated has been consumed even
        # if the model it produced lost; leaving it pending would make the next
        # run train on it again and report it as new.
        consumed = 0 if dry_run else mark_consumed(session, at=dt.datetime.now(dt.UTC))

        if not dry_run:
            record = (
                session.get(RetrainRun, run_id)
                if run_id is not None
                else RetrainRun(requested_by="cli")
            )
            if record is None:
                record = RetrainRun(requested_by="cli")
            record.status = "completed"
            record.started_at = record.started_at or dt.datetime.now(dt.UTC)
            record.finished_at = dt.datetime.now(dt.UTC)
            record.labels_consumed = consumed
            record.false_positives_consumed = len(labelled.benign)
            record.true_positives_consumed = len(labelled.attacks) + labelled.unnamed_attacks
            record.champion_version = comparison.champion_version
            record.challenger_version = comparison.challenger_version
            record.champion_pr_auc = before
            record.challenger_pr_auc = after
            record.held_out_split = gate
            record.promoted = comparison.promoted
            record.decision = comparison.decision
            record.comparison = asdict(comparison)
            session.add(record)

            report = render_report(comparison, labelled=labelled, pool=pool, gate=gate)
            settings.reports_path.mkdir(parents=True, exist_ok=True)
            (settings.reports_path / REPORT_FILENAME).write_text(report, encoding="utf-8")

        return comparison


def claim_pending_request(requested_by: str = "worker") -> int | None:
    """Mark the oldest pending retrain request as running, and return its id.

    The dashboard's button writes a `requested` row; this is the worker picking
    it up. Returns None when nothing is pending, which is the normal case for a
    scheduled run.
    """
    with session_scope() as session:
        pending = session.execute(
            select(RetrainRun)
            .where(RetrainRun.status == "requested")
            .order_by(RetrainRun.requested_at, RetrainRun.id)
            .limit(1)
        ).scalar_one_or_none()
        if pending is None:
            return None
        pending.status = "running"
        pending.started_at = dt.datetime.now(dt.UTC)
        pending.requested_by = pending.requested_by or requested_by
        session.flush()
        return int(pending.id)


def fail_request(run_id: int, error: str) -> None:
    """Record that a claimed request could not be completed."""
    with session_scope() as session:
        record = session.get(RetrainRun, run_id)
        if record is None:
            return
        record.status = "failed"
        record.finished_at = dt.datetime.now(dt.UTC)
        record.error = error


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--algorithm", default="lgbm", choices=("rf", "lgbm"))
    parser.add_argument(
        "--gate",
        default=DEFAULT_GATE,
        choices=("val", "test"),
        help="Which split decides promotion. 'test' turns the test day into a "
        "selection set; see the module docstring.",
    )
    parser.add_argument(
        "--now",
        action="store_true",
        help="Run without a pending request from the dashboard.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Measure, promote nothing.")
    parser.add_argument(
        "--no-control",
        action="store_true",
        help="Skip the control fit. A third faster, and the run can no longer say "
        "what the labels were worth -- only whether the challenger beat the champion.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)-8s %(message)s")

    run_id = None if args.now or args.dry_run else claim_pending_request()
    if run_id is None and not (args.now or args.dry_run):
        print(
            "no retrain has been requested. The Feedback screen's button writes the "
            "request; pass --now to run without one."
        )
        return 0

    try:
        comparison = run_retrain(
            algorithm=args.algorithm,
            gate=args.gate,
            dry_run=args.dry_run,
            run_id=run_id,
            control=not args.no_control,
        )
    except RetrainError as error:
        if run_id is not None:
            fail_request(run_id, str(error))
        print(f"retrain declined: {error}")
        return 2
    except Exception as error:  # noqa: BLE001 - the request must not be left running
        if run_id is not None:
            fail_request(run_id, repr(error))
        raise

    echo(comparison.render())
    if not args.dry_run:
        print("")
        print(f"written to {settings.reports_path / REPORT_FILENAME}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
