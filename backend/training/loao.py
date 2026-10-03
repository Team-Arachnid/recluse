"""Phase 4 -- leave-one-attack-out, the headline result.

For each attack family F:

1. Remove all F rows from supervised training.
2. Retrain the supervised model.
3. Leave the autoencoder untouched -- it never saw any attack rows anyway.
4. Run the full fusion pipeline over a test set containing F.
5. Record what fraction of F was flagged, and by which stage.

Output table (committed as reports/loao.md):

    Held-out family | Caught by Stage 1 | Caught by Stage 2 | Total recall | Missed

The Stage 2 column is the headline number. Report the misses honestly: a table
with a real Missed column reads as credible engineering, a table of 99s reads
as a bug.

----

Four things about how this is measured, each of which changes what the table
means and none of which a reader can infer from the numbers:

**Step 1 is already done for five of the seven families, by the calendar.**
CICIDS2017's temporal split trains on Tuesday and Wednesday, so Stage 1's
vocabulary is benign, DoS and brute force and nothing else. Web attacks and
infiltration are Thursday; botnet, port scan and DDoS are Friday. For those
families "remove all F rows from supervised training" removes nothing, because
the split removed them first. Refitting anyway and calling it a retrain would
be theatre. The honest statement is also the stronger one -- the classifier was
never shown this family on any day -- and each fold records how many rows its
removal actually took out of the fit so the difference is visible rather than
asserted. Only DoS and brute force get a genuine removal-and-refit.

**The feature contract is frozen to the champion's.** The brief requires the
autoencoder to be unchanged, and an unchanged autoencoder requires an unchanged
input transform: Stage 2's weights were fitted against one ``RobustScaler``,
and scoring it through another is not the same model. Freezing the bundle also
lets every fold index into one feature matrix instead of rebuilding it. The
residual is stated rather than hidden: the scaler's medians and interquartile
ranges were computed over the held-out family's rows too. Those are
column-level statistics, not labels, and no fold's *classifier* ever sees a
row of F.

**The hyperparameters are frozen too, and no fold has a validation set.** A
fold must differ from the control in exactly one way. Re-running the depth
sweep or the early-stopping search would make it differ in two, and -- worse --
two of the held-out families live on the validation day, so a stopping rule
measured there would let a fold's fit see the very rows it is supposed never to
have met. Each fold trains for the champion's recorded iteration count. That
count was itself chosen by the champion's early stopping against the validation
day, which is the single thread connecting any fold to that day: one integer,
informed by 2,179 attack rows out of 398,507. ``tau_sup`` is re-cut per fold
from the validation day's *benign* rows at the configured false-positive
budget, which is a benign percentile and carries no information about F.

**The benign reference is the test day.** Recall without a false-positive rate
beside it is not a result, and the negative class has to be traffic that
nothing in the pipeline was fitted or calibrated on. Stage 1 trained on the
training days, Stage 2 on their benign rows, and both thresholds were cut on
the validation day. Friday's benign traffic is what is left.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from training.console import echo
from training.features import build_feature_matrix
from training.fusion import fuse
from training.labels import ATTACK_FAMILIES, BENIGN_FAMILY, map_labels
from training.metrics import alert_volume, attack_confidence, detection_curves, select_threshold
from training.train_supervised import fit_fixed, load_split, prepare

logger = logging.getLogger(__name__)

REPORT_FILENAME = "loao.md"
METRICS_ARTIFACT = "metrics_loao.json"

# The splits the arena draws from, in the order a family's provenance is
# reported. Training days first, so a family that lives only there is visibly
# the exception.
SPLIT_NAMES: tuple[str, ...] = ("train", "val", "test")

# Where the negative class comes from. See the module docstring.
BENIGN_SPLIT = "test"

SEED = 7


class PartialRun(RuntimeError):
    """Raised when a one-family run is about to overwrite the full report.

    ``--family`` is advertised by ``--help`` as an ordinary way to run, so a
    reviewer spot-checking one row would otherwise replace ``reports/loao.md``
    with a one-row table and the model card's whole ``loao`` block with a
    single fold. The deliverable is committed; destroying it from a flag that
    reads like a filter is not a trade anyone opted into.
    """


class MissingStage(RuntimeError):
    """Raised when the hold-out loop is asked to run without both stages.

    Fatal rather than degraded. Without Stage 1 there is nothing to hold a
    family out of; without Stage 2 the headline column is structurally empty,
    and a table reporting 0% novel recall would be describing a missing file
    rather than a model that failed.
    """


# ---------------------------------------------------------------------------
# The arena
# ---------------------------------------------------------------------------


@dataclass
class ArenaSlice:
    """One family's rows, scored once by Stage 2 and reused by every fold.

    Kept as separate slices rather than one concatenated matrix for two
    reasons: the real arena is eight hundred thousand rows and concatenating it
    doubles the peak memory, and a fold only needs its own family plus the
    benign reference, so there is nothing to gain from having the rest resident
    in the same array.
    """

    name: str
    splits: list[str]
    matrix: np.ndarray
    anomaly_score: np.ndarray

    @property
    def rows(self) -> int:
        return int(len(self.matrix))

    def record(self) -> dict[str, Any]:
        """Provenance and counts. Deliberately not the rows themselves."""
        return {"name": self.name, "rows": self.rows, "splits": list(self.splits)}


@dataclass
class Arena:
    """Every row the table is measured on, and where each one came from."""

    attacks: dict[str, ArenaSlice]
    benign: ArenaSlice
    unmeasurable: dict[str, str] = field(default_factory=dict)

    def record(self) -> dict[str, Any]:
        return {
            "benign_split": self.benign.splits,
            "benign": self.benign.record(),
            "attacks": [slice_.record() for slice_ in self.attacks.values()],
            "unmeasurable": dict(self.unmeasurable),
            "rows": self.benign.rows + sum(slice_.rows for slice_ in self.attacks.values()),
        }


def build_arena(
    splits: dict[str, pd.DataFrame],
    served: Any,
    families: tuple[str, ...] = ATTACK_FAMILIES,
) -> Arena:
    """Collect every row of every attack family, plus the benign reference.

    A family's rows are taken from wherever in the dataset they exist, and the
    splits they came from are recorded. That is the complete population of F in
    the capture rather than a sample of it, which is what makes the recall
    figure a statement about the family rather than about a chosen subset.

    Stage 2 scores each slice here, once. The autoencoder is the one thing the
    loop does not vary, so a row's reconstruction error is the same number in
    every row of the table.
    """
    from training.autoencoder import reconstruction_error

    collapsed = {
        name: map_labels(frame["label"]).astype(str).reset_index(drop=True)
        for name, frame in splits.items()
    }

    def slice_for(name: str, mask_for: Any) -> ArenaSlice | None:
        parts: list[np.ndarray] = []
        provenance: list[str] = []
        for split in SPLIT_NAMES:
            frame = splits[split]
            keep = mask_for(split).to_numpy()
            if not keep.any():
                continue
            provenance.append(split)
            parts.append(
                build_feature_matrix(frame[keep], served.preprocessing).to_numpy(dtype="float32")
            )
        if not parts:
            return None
        matrix = np.vstack(parts)
        logger.info("arena slice %-13s %8d rows from %s", name, len(matrix), provenance)
        return ArenaSlice(
            name=name,
            splits=provenance,
            matrix=matrix,
            anomaly_score=reconstruction_error(served.autoencoder, matrix),
        )

    attacks: dict[str, ArenaSlice] = {}
    unmeasurable: dict[str, str] = {}
    for family in families:
        found = slice_for(family, lambda split, family=family: collapsed[split] == family)
        if found is None:
            unmeasurable[family] = (
                "no rows in any split of this capture, so there is nothing to "
                "hold out and nothing to miss. Reported as unmeasurable rather "
                "than as 0% recall."
            )
            continue
        attacks[family] = found

    benign = slice_for(
        BENIGN_FAMILY,
        lambda split: (
            (collapsed[split] == BENIGN_FAMILY)
            if split == BENIGN_SPLIT
            else pd.Series(False, index=collapsed[split].index)
        ),
    )
    if benign is None:
        raise MissingStage(
            f"the {BENIGN_SPLIT} split carries no benign rows, so there is no negative "
            "class to measure a false-positive rate against. A recall figure on its "
            "own is not a result."
        )

    return Arena(attacks=attacks, benign=benign, unmeasurable=unmeasurable)


# ---------------------------------------------------------------------------
# What one fold measures
# ---------------------------------------------------------------------------


@dataclass
class FamilyOutcome:
    """One row of the headline table.

    ``stage1_caught``, ``stage2_caught`` and ``missed`` partition the family,
    because the cascade only shows Stage 2 what Stage 1 did not name. The two
    PR-AUCs are the same question asked without a threshold, measured against
    the benign reference.

    ``stage1_named`` is the part of ``stage1_caught`` that carried *this*
    family's name, and it is here because "caught" does not mean "named".
    ``attack_confidence`` is the largest single attack-class probability, so a
    held-out family can clear ``tau_sup`` under some other family's label --
    DDoS flows alerting as ``dos`` is the obvious case, and it is a correct
    alert about a flood with the family one level off. Under hold-out the fold
    has no column for the family at all, so this is zero by construction, and
    the contrast with the control is what makes the distinction visible instead
    of letting a recall figure imply a classification.
    """

    family: str
    splits: list[str]
    support: int
    stage1_caught: int
    stage1_named: int
    stage2_caught: int
    # What Stage 2 would have flagged with nothing in front of it, and what it
    # would flag at the threshold that fits the analyst queue. Neither varies by
    # fold -- the autoencoder is the component the loop holds fixed -- but both
    # belong beside the marginal figure, because the marginal one is lower than
    # Stage 2's own recall wherever the two stages agree about a flow.
    stage2_standalone_caught: int = 0
    stage2_budget_caught: int | None = None
    stage1_pr_auc: float = float("nan")
    stage2_pr_auc: float = float("nan")

    @property
    def missed(self) -> int:
        return self.support - self.stage1_caught - self.stage2_caught

    @property
    def stage2_standalone_recall(self) -> float:
        return self.stage2_standalone_caught / self.support if self.support else 0.0

    @property
    def stage2_budget_recall(self) -> float | None:
        if self.stage2_budget_caught is None or not self.support:
            return None
        return self.stage2_budget_caught / self.support

    @property
    def carried_by(self) -> str:
        """Which stage found most of what was found. Empty when nothing was."""
        if not self.stage1_caught and not self.stage2_caught:
            return ""
        return "stage1" if self.stage1_caught >= self.stage2_caught else "stage2"

    @property
    def stage1_named_rate(self) -> float:
        """Of the rows Stage 1 alerted on, the share it also named correctly."""
        return self.stage1_named / self.stage1_caught if self.stage1_caught else 0.0

    @property
    def stage1_recall(self) -> float:
        return self.stage1_caught / self.support if self.support else 0.0

    @property
    def stage2_recall(self) -> float:
        return self.stage2_caught / self.support if self.support else 0.0

    @property
    def total_recall(self) -> float:
        return self.stage1_recall + self.stage2_recall

    @property
    def miss_rate(self) -> float:
        return self.missed / self.support if self.support else 0.0

    def record(self) -> dict[str, Any]:
        return {
            "family": self.family,
            "splits": list(self.splits),
            "support": self.support,
            "stage1_caught": self.stage1_caught,
            "stage1_named": self.stage1_named,
            "stage1_named_rate": self.stage1_named_rate,
            "stage2_caught": self.stage2_caught,
            "stage2_standalone_caught": self.stage2_standalone_caught,
            "stage2_standalone_recall": self.stage2_standalone_recall,
            "stage2_budget_caught": self.stage2_budget_caught,
            "stage2_budget_recall": self.stage2_budget_recall,
            "missed": self.missed,
            "stage1_recall": self.stage1_recall,
            "stage2_recall": self.stage2_recall,
            "total_recall": self.total_recall,
            "miss_rate": self.miss_rate,
            "stage1_pr_auc": _json_float(self.stage1_pr_auc),
            "stage2_pr_auc": _json_float(self.stage2_pr_auc),
        }


@dataclass
class BenignOutcome:
    """What a fold's recall cost, on traffic nothing in the pipeline was fitted on."""

    name: str
    splits: list[str]
    rows: int
    stage1_alerts: int
    stage2_alerts: int
    alerts_per_day: float
    alerts_per_analyst_hour: float

    @property
    def stage1_fpr(self) -> float:
        return self.stage1_alerts / self.rows if self.rows else 0.0

    @property
    def stage2_fpr(self) -> float:
        return self.stage2_alerts / self.rows if self.rows else 0.0

    @property
    def fpr(self) -> float:
        return self.stage1_fpr + self.stage2_fpr

    def record(self) -> dict[str, Any]:
        return {
            "split": list(self.splits),
            "rows": self.rows,
            "stage1_alerts": self.stage1_alerts,
            "stage2_alerts": self.stage2_alerts,
            "stage1_fpr": self.stage1_fpr,
            "stage2_fpr": self.stage2_fpr,
            "fpr": self.fpr,
            "alerts_per_day": self.alerts_per_day,
            "alerts_per_analyst_hour": self.alerts_per_analyst_hour,
        }


@dataclass
class Stage2Alone:
    """Stage 2's own detection, outside the cascade and outside the folds.

    None of this varies by fold, because the autoencoder is the one component
    the loop holds fixed, so it is measured once. It answers two questions the
    headline table deliberately does not:

    * **What can Stage 2 do on its own?** Its column in the headline table is
      its *marginal* contribution -- what it adds on rows Stage 1 passed
      through -- and that is lower than its own recall wherever the two stages
      agree about a flow, which on high-rate floods is most of the time.
    * **What would it cost at a threshold the queue can absorb?** ``tau_anom``
      is the brief's 99.5th benign percentile: a statement about what normal
      traffic looks like. ``budget_tau`` is where that same benign distribution
      sits at the analyst budget. The two are far apart, and reporting the
      recall the shipped threshold buys without the recall the affordable one
      buys leaves the alerts-per-hour column looking like a broken system
      rather than like a choice somebody has to make.
    """

    tau_anom: float
    budget_tau: float | None
    benign_rows: int
    benign_alerts: int
    benign_alerts_at_budget: int | None
    families: dict[str, dict[str, float]] = field(default_factory=dict)

    @property
    def benign_fpr(self) -> float:
        return self.benign_alerts / self.benign_rows if self.benign_rows else 0.0

    @property
    def benign_fpr_at_budget(self) -> float | None:
        if self.benign_alerts_at_budget is None or not self.benign_rows:
            return None
        return self.benign_alerts_at_budget / self.benign_rows

    def alerts_per_analyst_hour(self, settings: Any, at_budget: bool = False) -> float:
        """What this threshold puts in front of one analyst, per hour.

        The unit the budget is stated in, and the only unit in which "does this
        fit the queue" is a question with an answer. Reported for
        ``budget_tau`` as well as for ``tau_anom`` because ``budget_tau`` was
        cut on the *validation* day: it fits the budget there by construction,
        and whether it still does on the day being measured is a separate
        question that only this number settles.
        """
        rate = self.benign_fpr_at_budget if at_budget else self.benign_fpr
        if rate is None:
            return 0.0
        return rate * settings.expected_daily_flow_volume / settings.analyst_shift_hours

    def record(self) -> dict[str, Any]:
        return {
            "tau_anom": self.tau_anom,
            "budget_tau": self.budget_tau,
            "benign_rows": self.benign_rows,
            "benign_alerts": self.benign_alerts,
            "benign_fpr": self.benign_fpr,
            "benign_alerts_at_budget": self.benign_alerts_at_budget,
            "benign_fpr_at_budget": self.benign_fpr_at_budget,
            "families": self.families,
        }


def measure_stage2_alone(arena: Arena, tau_anom: float, budget_tau: float | None) -> Stage2Alone:
    """Score Stage 2 against every family with nothing in front of it."""
    benign = arena.benign.anomaly_score
    families = {
        name: {
            "support": float(slice_.rows),
            "caught": float((slice_.anomaly_score >= tau_anom).sum()),
            "recall": float((slice_.anomaly_score >= tau_anom).mean()),
            **(
                {
                    "caught_at_budget": float((slice_.anomaly_score >= budget_tau).sum()),
                    "recall_at_budget": float((slice_.anomaly_score >= budget_tau).mean()),
                }
                if budget_tau is not None
                else {}
            ),
        }
        for name, slice_ in arena.attacks.items()
    }
    return Stage2Alone(
        tau_anom=float(tau_anom),
        budget_tau=float(budget_tau) if budget_tau is not None else None,
        benign_rows=arena.benign.rows,
        benign_alerts=int((benign >= tau_anom).sum()),
        benign_alerts_at_budget=(
            int((benign >= budget_tau).sum()) if budget_tau is not None else None
        ),
        families=families,
    )


@dataclass
class Fold:
    """One trained Stage 1 and everything measured through it.

    ``held_out`` is ``None`` for the control, which is the same procedure with
    nothing removed. Without it the table has no reference: "78% recall on
    infiltration" invites the question *compared with what*, and the control is
    the answer.
    """

    held_out: str | None
    rows_in_split: int
    rows_in_fit: int
    refitted: bool
    reuse_reason: str | None
    classes: list[str]
    training_rows: int
    tau_sup: float
    tau_anom: float
    threshold: dict[str, Any]
    families: dict[str, FamilyOutcome]
    benign: BenignOutcome

    @property
    def control_in_sample(self) -> bool:
        """True when the *control* was fitted on rows of this fold's family.

        ``rows_in_fit`` is exactly the count this fold removed from the
        control's training set, so a non-zero value means the control was
        scored on rows it had itself learned. Its recall on such a family is
        memorisation as much as detection, and an unlabelled 100% beside a
        held-out 0% invites the reader to credit the whole collapse to the
        hold-out.

        Deliberately *not* "the family has rows on a training day".
        ``web_attack`` does, and every one of them is below the support floor,
        so the control never fitted on any of them and its recall on that
        family is honestly out-of-sample. A caveat attached to the wrong row is
        its own kind of inaccuracy.
        """
        return self.rows_in_fit > 0

    def record(self) -> dict[str, Any]:
        headline = self.families.get(self.held_out) if self.held_out else None
        return {
            "held_out": self.held_out,
            "rows_in_split": self.rows_in_split,
            "rows_in_fit": self.rows_in_fit,
            "refitted": self.refitted,
            "control_in_sample": self.control_in_sample,
            "reuse_reason": self.reuse_reason,
            "classes": list(self.classes),
            "training_rows": self.training_rows,
            "tau_sup": self.tau_sup,
            "tau_anom": self.tau_anom,
            "threshold": self.threshold,
            "headline": headline.record() if headline else None,
            "families": [outcome.record() for outcome in self.families.values()],
            "benign": self.benign.record(),
        }


def _json_float(value: float) -> float | None:
    """``NaN`` and infinities are not JSON. Every strict parser rejects them."""
    number = float(value)
    return number if np.isfinite(number) else None


def _pr_auc(attack_scores: np.ndarray, benign_scores: np.ndarray) -> float:
    truth = np.concatenate([np.ones(len(attack_scores), bool), np.zeros(len(benign_scores), bool)])
    scores = np.concatenate([attack_scores, benign_scores])
    return detection_curves(truth, scores).pr_auc


def score_fold(
    model: Any,
    tau_sup: float,
    tau_anom: float,
    arena: Arena,
    families: list[str],
    settings: Any,
    budget_tau: float | None = None,
) -> tuple[dict[str, FamilyOutcome], BenignOutcome]:
    """Run the real cascade over the benign reference and the named families.

    ``fuse`` is imported rather than reimplemented, which is the whole point:
    the table is a measurement of the rule the API runs, not of a second
    version of it written for the evaluation.

    The class order comes off ``model.classes_`` and never off the vocabulary.
    scikit-learn sorts its classes alphabetically, so reading the order from
    anywhere else would map one family's probabilities onto another's name.
    """
    classes = [str(name) for name in model.classes_]

    def decide(slice_: ArenaSlice):
        proba = model.predict_proba(slice_.matrix)
        return fuse(proba, classes, tau_sup, anomaly_score=slice_.anomaly_score, tau_anom=tau_anom)

    benign_decisions = decide(arena.benign)
    benign_confidence = attack_confidence(model.predict_proba(arena.benign.matrix), classes)
    volume = alert_volume(
        benign_confidence,
        np.ones(arena.benign.rows, bool),
        tau_sup,
        settings.expected_daily_flow_volume,
        settings.analyst_capacity_per_hour,
        settings.analyst_shift_hours,
    )
    benign_counts = benign_decisions.counts()
    # The fused rate, not Stage 1's: the projection has to answer "what does
    # this operating point put in the queue", and Stage 2's alerts are in the
    # queue too. `alert_volume` derives its per-day figure from a rate, so the
    # rate it is handed is the fused one.
    fused_fpr = benign_counts["alerts"] / arena.benign.rows if arena.benign.rows else 0.0
    benign = BenignOutcome(
        name=arena.benign.name,
        splits=list(arena.benign.splits),
        rows=arena.benign.rows,
        stage1_alerts=benign_counts["known"],
        stage2_alerts=benign_counts["unclassified_anomaly"],
        alerts_per_day=fused_fpr * settings.expected_daily_flow_volume,
        alerts_per_analyst_hour=(
            fused_fpr * settings.expected_daily_flow_volume / settings.analyst_shift_hours
        ),
    )
    logger.debug("benign budget check: stage-1 only would be %s", volume)

    outcomes: dict[str, FamilyOutcome] = {}
    for family in families:
        slice_ = arena.attacks[family]
        decisions = decide(slice_)
        counts = decisions.counts()
        outcomes[family] = FamilyOutcome(
            family=family,
            splits=list(slice_.splits),
            support=slice_.rows,
            stage1_caught=counts["known"],
            stage1_named=int((decisions.family == family).sum()),
            stage2_caught=counts["unclassified_anomaly"],
            stage2_standalone_caught=int((slice_.anomaly_score >= tau_anom).sum()),
            stage2_budget_caught=(
                int((slice_.anomaly_score >= budget_tau).sum()) if budget_tau is not None else None
            ),
            stage1_pr_auc=_pr_auc(decisions.confidence, benign_confidence),
            stage2_pr_auc=_pr_auc(slice_.anomaly_score, arena.benign.anomaly_score),
        )
    return outcomes, benign


# ---------------------------------------------------------------------------
# The loop
# ---------------------------------------------------------------------------


@dataclass
class LoaoResult:
    """The whole evaluation: the control, one fold per family, and the arena."""

    version: str
    algorithm: str
    schema_hash: str
    hyperparameters: dict[str, Any]
    seed: int
    measured_at: str
    target_fpr: float
    champion_tau_sup: float
    tau_anom: float
    # The false-positive rate `tau_anom` achieved on the day Phase 3 cut it.
    # Kept so the report can derive the domain-shift factor instead of quoting
    # a number typed beside it.
    calibration_fpr: float | None
    arena: Arena
    stage2_alone: Stage2Alone
    control: Fold
    folds: list[Fold]

    def record(self) -> dict[str, Any]:
        return {
            "stage": "phase4_loao",
            "champion": {
                "version": self.version,
                "algorithm": self.algorithm,
                "schema_hash": self.schema_hash,
                "hyperparameters": self.hyperparameters,
                "tau_sup": self.champion_tau_sup,
                "tau_anom": self.tau_anom,
                "calibration_fpr": self.calibration_fpr,
            },
            "measured_at": self.measured_at,
            "seed": self.seed,
            "target_fpr": self.target_fpr,
            "arena": self.arena.record(),
            "stage2_alone": self.stage2_alone.record(),
            "control": self.control.record(),
            "folds": [entry.record() for entry in self.folds],
        }

    def render(self) -> str:
        lines = [
            f"champion         {self.version} ({self.algorithm})",
            f"tau_anom         {self.tau_anom:.6e}  (unchanged in every fold)",
            f"target FPR       {self.target_fpr:.2e}",
            f"arena            {self.arena.record()['rows']:,} rows",
            "",
            f"{'held out':<14}{'rows':>9}{'stage 1':>10}{'stage 2':>10}{'total':>9}{'missed':>9}",
        ]
        for entry in self.folds:
            outcome = entry.families[entry.held_out or ""]
            lines.append(
                f"{outcome.family:<14}{outcome.support:>9,}"
                f"{outcome.stage1_recall:>9.1%}{outcome.stage2_recall:>10.1%}"
                f"{outcome.total_recall:>9.1%}{outcome.miss_rate:>9.1%}"
            )
        for family, reason in self.arena.unmeasurable.items():
            lines.append(f"{family:<14}{'--':>9}   {reason.split(',')[0]}")
        return "\n".join(lines)


def load_served(artifacts_dir: Path) -> Any:
    """The champion bundle, loaded the way the API loads it.

    Deliberately the serving loader rather than a private one: the table has to
    be a measurement of the pair that ships, and that includes its schema
    check. A bundle whose halves disagree raises here instead of producing a
    plausible-looking table from a mismatched model and scaler.
    """
    from app.inference import load_bundle

    served = load_bundle(artifacts_dir)
    if not served.stage1_ready:
        raise MissingStage(
            f"no Stage 1 model with a threshold in {artifacts_dir}. The hold-out loop "
            "removes a family from Stage 1's training set, so there has to be a "
            "Stage 1 to remove it from. Run `make train` first."
        )
    if not served.stage2_ready:
        raise MissingStage(
            f"no Stage 2 model with a threshold in {artifacts_dir}. Stage 2 is the "
            "headline column of this table -- without it the loop would report 0% "
            "novel recall, which would be a description of a missing file rather "
            "than of a model that failed. Run `make train-anomaly` first."
        )
    return served


def run_loao(
    train_frame: pd.DataFrame,
    val_frame: pd.DataFrame,
    test_frame: pd.DataFrame,
    artifacts_dir: Path,
    settings: Any = None,
    seed: int = SEED,
    families: tuple[str, ...] = ATTACK_FAMILIES,
) -> LoaoResult:
    """Hold out each attack family in turn and measure what the cascade catches."""
    if settings is None:
        from app.config import settings as default_settings

        settings = default_settings

    served = load_served(artifacts_dir)
    card = served.model_card
    algorithm = str(served.supervised_algorithm or card.get("algorithm") or "rf")
    hyperparameters = dict(card.get("hyperparameters") or {})

    arena = build_arena(
        {"train": train_frame, "val": val_frame, "test": test_frame}, served, families
    )

    # Phase 3 recorded where the same benign distribution sits at the analyst
    # budget. Absent on a card written before it did, in which case the
    # affordable-threshold column is simply not reported rather than guessed.
    stage2_threshold = (card.get("stage2") or {}).get("threshold") or {}
    budget_tau = stage2_threshold.get("budget_tau")
    stage2_alone = measure_stage2_alone(arena, float(served.tau_anom), budget_tau)
    logger.info(
        "Stage 2 alone: %.2e benign FPR at tau_anom=%.6e%s",
        stage2_alone.benign_fpr,
        stage2_alone.tau_anom,
        f", {stage2_alone.benign_fpr_at_budget:.2e} at budget_tau={budget_tau:.6e}"
        if budget_tau is not None
        else "",
    )

    # One frozen-contract preparation, shared by every fold. With the bundle
    # fixed, a fold's matrix is the control's matrix minus some rows, so this is
    # built once and indexed rather than rebuilt per family.
    full = prepare(train_frame, val_frame, bundle=served.preprocessing)
    logger.info(
        "control: %d rows x %d features, classes %s",
        len(full.y_train),
        full.x_train.shape[1],
        full.classes,
    )

    control = _fit_and_measure(
        held_out=None,
        data=full,
        keep=np.ones(len(full.y_train), bool),
        rows_in_split=0,
        algorithm=algorithm,
        hyperparameters=hyperparameters,
        seed=seed,
        arena=arena,
        measure=list(arena.attacks),
        tau_anom=float(served.tau_anom),
        settings=settings,
        reuse_reason=None,
        budget_tau=budget_tau,
    )

    in_split = map_labels(train_frame["label"]).astype(str).value_counts()
    folds: list[Fold] = []
    for family in arena.attacks:
        keep = (full.train_families != family).to_numpy()
        rows_in_fit = int((~keep).sum())
        rows_in_split = int(in_split.get(family, 0))

        if rows_in_fit == 0:
            # Nothing to remove, so nothing to refit. Say which of the two
            # reasons applies rather than leaving the zero unexplained.
            reason = (
                f"{rows_in_split} row(s) on the training days, all below the "
                f"support floor, so the family was never in Stage 1's vocabulary"
                if rows_in_split
                else "no rows on the training days at all -- the temporal split "
                "already holds this family out, so the control model is the "
                "held-out model"
            )
            folds.append(
                Fold(
                    held_out=family,
                    rows_in_split=rows_in_split,
                    rows_in_fit=0,
                    refitted=False,
                    reuse_reason=reason,
                    classes=list(control.classes),
                    training_rows=control.training_rows,
                    tau_sup=control.tau_sup,
                    tau_anom=control.tau_anom,
                    threshold=dict(control.threshold),
                    families={family: control.families[family]},
                    benign=control.benign,
                )
            )
            continue

        folds.append(
            _fit_and_measure(
                held_out=family,
                data=full,
                keep=keep,
                rows_in_split=rows_in_split,
                algorithm=algorithm,
                hyperparameters=hyperparameters,
                seed=seed,
                arena=arena,
                measure=[family],
                tau_anom=float(served.tau_anom),
                settings=settings,
                reuse_reason=None,
                budget_tau=budget_tau,
            )
        )

    return LoaoResult(
        version=str(served.version),
        algorithm=algorithm,
        schema_hash=str(served.schema_hash),
        hyperparameters=hyperparameters,
        seed=seed,
        measured_at=datetime.now(UTC).isoformat(timespec="seconds"),
        target_fpr=float(settings.target_fpr),
        champion_tau_sup=float(served.tau_sup),
        tau_anom=float(served.tau_anom),
        calibration_fpr=stage2_threshold.get("fpr"),
        arena=arena,
        stage2_alone=stage2_alone,
        control=control,
        folds=folds,
    )


def _fit_and_measure(
    held_out: str | None,
    data: Any,
    keep: np.ndarray,
    rows_in_split: int,
    algorithm: str,
    hyperparameters: dict[str, Any],
    seed: int,
    arena: Arena,
    measure: list[str],
    tau_anom: float,
    settings: Any,
    reuse_reason: str | None,
    budget_tau: float | None = None,
) -> Fold:
    """Fit one fold's Stage 1, cut its threshold, and score it over the arena."""
    from dataclasses import replace

    from training.labels import vocabulary

    families = data.train_families[keep].reset_index(drop=True)
    fold_data = replace(
        data,
        x_train=data.x_train[keep],
        y_train=data.y_train[keep].reset_index(drop=True),
        train_families=families,
        classes=vocabulary(families),
    )
    logger.info(
        "fold %-13s fitting %s on %d rows, classes %s",
        held_out or "(control)",
        algorithm,
        len(fold_data.y_train),
        fold_data.classes,
    )
    model = fit_fixed(fold_data, algorithm, hyperparameters, seed=seed)

    classes = [str(name) for name in model.classes_]
    confidence = attack_confidence(model.predict_proba(fold_data.x_val), classes)
    choice = select_threshold(
        confidence,
        (fold_data.val_families == BENIGN_FAMILY).to_numpy(),
        settings.target_fpr,
    )
    logger.info(
        "fold %-13s tau_sup=%.6f (FPR %.2e on the validation day's benign rows)",
        held_out or "(control)",
        choice.tau,
        choice.fpr,
    )

    outcomes, benign = score_fold(
        model, choice.tau, tau_anom, arena, measure, settings, budget_tau=budget_tau
    )
    return Fold(
        held_out=held_out,
        rows_in_split=rows_in_split,
        rows_in_fit=int((~keep).sum()),
        refitted=True,
        reuse_reason=reuse_reason,
        classes=classes,
        training_rows=int(len(fold_data.y_train)),
        tau_sup=float(choice.tau),
        tau_anom=float(tau_anom),
        threshold={
            "tau": float(choice.tau),
            "fpr": float(choice.fpr),
            "target_fpr": float(choice.target_fpr),
            "benign_rows": int(choice.benign_rows),
            "false_alerts": int(choice.false_alerts),
            "above_every_benign_score": bool(choice.above_every_benign_score),
        },
        families=outcomes,
        benign=benign,
    )


# ---------------------------------------------------------------------------
# The write-up
#
# Committed as reports/loao.md, which is this phase's checkpoint. The prose is
# generated from the measured numbers rather than written alongside them, so a
# rerun that moves a figure cannot leave a sentence behind that contradicts it.
# ---------------------------------------------------------------------------

# Why each family is easy or hard to see in a *single flow record*. These are
# statements about the attack's mechanics and about what flow-level features can
# and cannot represent -- they do not change when the numbers do, which is why
# they are written down rather than derived. The verdict attached to each one in
# the report *is* derived.
FAMILY_MECHANICS: dict[str, str] = {
    "dos": (
        "High-rate floods sit far outside the benign envelope on several features "
        "at once -- duration, packet rate, bytes per second -- so a reconstruction "
        "trained on ordinary traffic has nowhere to put them."
    ),
    "ddos": (
        "Behaviourally the same shape as DoS from a single flow's point of view; the "
        "distribution is in the source addresses, which are not features. A "
        "classifier that has learned DoS will often name DDoS flows `dos`, and that "
        "counts as caught here: the analyst gets a correct alert about a flood, with "
        "the family one level off."
    ),
    "brute_force": (
        "Many short, regular, near-identical sessions against one service port. Each "
        "flow on its own is a modest outlier at most -- the pattern is in the "
        "repetition, which a per-flow score cannot see."
    ),
    "port_scan": (
        "Single-packet flows with near-zero duration and almost no bytes. "
        "Statistically they are indistinguishable from the shortest ordinary flows, "
        "and the evidence for *scanning* is aggregate -- hundreds of distinct "
        "destination ports from one source inside a few seconds. This feature set is "
        "per-flow, so there is nothing in one row to find."
    ),
    "web_attack": (
        "SQL injection and XSS ride inside otherwise ordinary HTTP sessions. The flow "
        "statistics barely move, because the attack is in the payload and flow-level "
        "features cannot see payload content."
    ),
    "botnet": (
        "Command-and-control beaconing is deliberately shaped to look like ordinary "
        "traffic -- that is the design goal of the malware. This is the case where "
        "*unusual* and *malicious* come apart furthest."
    ),
    "infiltration": (
        "A dropper followed by quiet internal activity: long, low-volume sessions. "
        "Some of that shape overlaps genuinely idle benign traffic, which is why the "
        "detection is partial rather than absent."
    ),
}


# Below this many rows a recall figure is a proportion from a small sample, and
# the second decimal place is noise. The real capture has `infiltration` at 36
# rows and `botnet` at 1,948, so this is not a hypothetical.
SMALL_SUPPORT_ROWS = 2_000


def _percent(value: float) -> str:
    return f"{value * 100:.1f}%"


def _wilson(caught: int, support: int, z: float = 1.96) -> tuple[float, float]:
    """A 95% interval for a proportion, by Wilson's method.

    Wilson rather than the normal approximation because the cases that need an
    interval at all are exactly the ones the normal approximation handles
    worst: small ``support``, and proportions near zero or one. On 0 of 1,948
    rows the normal interval is [0, 0], which claims certainty from a sample
    that has none.
    """
    if support <= 0:
        return 0.0, 0.0
    phat = caught / support
    denominator = 1 + z**2 / support
    centre = (phat + z**2 / (2 * support)) / denominator
    spread = z / denominator * np.sqrt(phat * (1 - phat) / support + z**2 / (4 * support**2))
    return max(0.0, centre - spread), min(1.0, centre + spread)


def _auc(value: float) -> str:
    return "--" if not np.isfinite(value) else f"{value:.4f}"


def _join(items: list[str]) -> str:
    names = [f"`{item}`" for item in items if item]
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


def _headline_table(result: LoaoResult) -> list[str]:
    lines = [
        "| Held-out family | Rows | Caught by Stage 1 | Caught by Stage 2 "
        "| Total recall | Missed |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for entry in result.folds:
        outcome = entry.families[entry.held_out or ""]
        lines.append(
            f"| `{outcome.family}` | {outcome.support:,} | {_percent(outcome.stage1_recall)} "
            f"| **{_percent(outcome.stage2_recall)}** | {_percent(outcome.total_recall)} "
            f"| {_percent(outcome.miss_rate)} |"
        )
    for family in result.arena.unmeasurable:
        lines.append(f"| `{family}` | 0 | -- | -- | -- | -- |")
    return lines


def _fold_table(result: LoaoResult) -> list[str]:
    control_classes = ", ".join(f"`{name}`" for name in result.control.classes)
    lines = [
        "| Held-out family | Rows on the training days | Rows removed from Stage 1's fit "
        "| Stage 1 refitted | Fold vocabulary | `tau_sup` |",
        "| --- | --- | --- | --- | --- | --- |",
        f"| _none (control)_ | -- | 0 | yes | {control_classes} | {result.control.tau_sup:.6f} |",
    ]
    for entry in result.folds:
        lines.append(
            f"| `{entry.held_out}` | {entry.rows_in_split:,} | {entry.rows_in_fit:,} "
            f"| {'yes' if entry.refitted else 'no'} "
            f"| {', '.join(f'`{name}`' for name in entry.classes)} | {entry.tau_sup:.6f} |"
        )
    return lines


def _cost_table(result: LoaoResult) -> list[str]:
    lines = [
        "| Held-out family | Benign rows | Stage 1 FPR | Stage 2 FPR | Fused FPR "
        "| Alerts/analyst/hour | Stage 1 PR-AUC | Stage 2 PR-AUC |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for entry in result.folds:
        outcome = entry.families[entry.held_out or ""]
        benign = entry.benign
        lines.append(
            f"| `{entry.held_out}` | {benign.rows:,} | {benign.stage1_fpr:.2e} "
            f"| {benign.stage2_fpr:.2e} | {benign.fpr:.2e} "
            f"| {benign.alerts_per_analyst_hour:,.1f} | {_auc(outcome.stage1_pr_auc)} "
            f"| {_auc(outcome.stage2_pr_auc)} |"
        )
    return lines


def _stage2_table(result: LoaoResult, settings: Any) -> list[str]:
    alone = result.stage2_alone
    has_budget = alone.budget_tau is not None
    header = (
        "| Family | Rows | Stage 2 alone, at `tau_anom` | Stage 2 in the cascade "
        "| Stage 2 alone, at the budget threshold |"
        if has_budget
        else "| Family | Rows | Stage 2 alone, at `tau_anom` | Stage 2 in the cascade |"
    )
    rule = "| --- | --- | --- | --- | --- |" if has_budget else "| --- | --- | --- | --- |"
    lines = [header, rule]
    for entry in result.folds:
        family = entry.held_out or ""
        outcome = entry.families[family]
        row = (
            f"| `{family}` | {outcome.support:,} "
            f"| {_percent(outcome.stage2_standalone_recall)} "
            f"| {_percent(outcome.stage2_recall)} "
        )
        if has_budget:
            at_budget = outcome.stage2_budget_recall
            row += f"| {_percent(at_budget) if at_budget is not None else '--'} "
        lines.append(row + "|")

    benign_row = (
        f"| _benign (false positives)_ | {alone.benign_rows:,} | {_percent(alone.benign_fpr)} | -- "
    )
    if has_budget:
        rate = alone.benign_fpr_at_budget
        benign_row += f"| {_percent(rate) if rate is not None else '--'} "
    lines.append(benign_row + "|")

    # The rows that make "does this fit the queue" answerable. Without them the
    # table prices both thresholds in a unit nobody staffs against.
    budget = settings.analyst_capacity_per_hour
    cost_row = (
        f"| _Alerts/analyst/hour_ | -- | **{alone.alerts_per_analyst_hour(settings):,.0f}** | -- "
    )
    if has_budget:
        cost_row += f"| **{alone.alerts_per_analyst_hour(settings, at_budget=True):,.0f}** "
    lines.append(cost_row + "|")
    lines.append(
        f"| _against a budget of_ | -- | {budget} | -- " + (f"| {budget} |" if has_budget else "|")
    )
    return lines


def _budget_verdict(result: LoaoResult, settings: Any) -> list[str]:
    """What the affordable threshold costs, stated from the measured numbers.

    Every other figure in this report has a sentence attached. The one that
    decides whether the detector is deployable should not be the exception, and
    the table above does not say out loud that the cheaper threshold is bought
    with most of the recall.
    """
    alone = result.stage2_alone
    if alone.budget_tau is None:
        return []

    hourly_after = alone.alerts_per_analyst_hour(settings, at_budget=True)
    budget = settings.analyst_capacity_per_hour
    overshoot = hourly_after / budget if budget else float("nan")

    moved = [
        (name, stats["recall"], stats["recall_at_budget"])
        for name, stats in alone.families.items()
        if "recall_at_budget" in stats
    ]
    if not moved:
        return []

    worst = max(moved, key=lambda row: row[1] - row[2])
    # Read off the same formatter the table uses, so this sentence cannot name
    # three families while four cells above it render 0.0%. `port_scan` keeps a
    # handful of flows at the budget threshold and rounds to zero; comparing the
    # float instead of the rendered string is how prose and table drift apart.
    zeroed = [name for name, _, after in moved if _percent(after) == _percent(0.0)]
    benign_before = alone.benign_fpr
    benign_after = alone.benign_fpr_at_budget or 0.0
    factor = benign_before / benign_after if benign_after else float("inf")

    lines = [
        "",
        f"**The affordable threshold is bought with the recall.** Moving Stage 2 from "
        f"the shipped percentile to the budget threshold divides its benign "
        f"false-positive rate by {factor:.1f} -- {_percent(benign_before)} of ordinary "
        f"flows down to {_percent(benign_after)} -- and `{worst[0]}` falls from "
        f"{_percent(worst[1])} to {_percent(worst[2])} with it."
        + (
            f" {_join(zeroed)} fall to 0.0%: at that threshold Stage 2 finds "
            "essentially none of them."
            if zeroed
            else ""
        ),
        "",
        f"**And it still does not fit the queue.** `budget_tau` is cut from the "
        f"*validation* day's benign distribution at the analyst budget, so on that day "
        f"it fits by construction. On this one it puts {hourly_after:,.0f} alerts in "
        f"front of each analyst per hour against a budget of {budget} -- "
        f"**{overshoot:.1f}x over**. That is not a second defect; it is the same domain "
        "shift the next paragraph is about, now carrying its own number. A threshold cut "
        "on one day of one capture does not transfer to the next day of the same "
        "capture.",
        "",
        "So the honest reading of both tables together is that neither threshold is a "
        "finished answer. The shipped one detects and overwhelms; the one cut to fit the "
        "queue on its calibration day detects very little and is over budget here "
        "anyway. The three things that actually move "
        "this are not threshold choices: **dedup**, which collapses a burst from one "
        "source into a single queue row with an occurrence count rather than one row "
        "per flow -- the per-analyst-hour projection above assumes one row per flow, "
        "which is the assumption Phase 5 removes; **risk ranking**, so the queue is "
        "worked in order of consequence instead of arrival; and **recalibration "
        "against a local benign baseline**, because this threshold was cut on one "
        f"lab's Thursday and moving it to that lab's Friday multiplied its "
        f"false-positive rate by {_shift_factor(result):.1f}. A threshold slider on the "
        "Live Traffic "
        "screen is where whoever owns the queue chooses a point on this curve, and "
        "nothing is auto-blocked at any setting.",
        "",
    ]
    return lines


def _shift_factor(result: LoaoResult) -> float:
    """How far `tau_anom` drifted between the day it was cut and the day measured.

    Derived rather than quoted. Phase 3 recorded the false-positive rate
    `tau_anom` achieved on its own calibration day, and this run measured what
    the same threshold achieves on the test day. A Phase 3 rerun at a different
    percentile moves both, and a hand-typed ratio beside them would go stale
    against a table that had moved.
    """
    if not result.calibration_fpr:
        return float("nan")
    return result.stage2_alone.benign_fpr / result.calibration_fpr


def _control_table(result: LoaoResult) -> list[str]:
    lines = [
        "| Family | Stage 1, family in training | Named correctly | Stage 1, family held out "
        "| Named correctly | Stage 2, family held out | Total, family held out |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for entry in result.folds:
        family = entry.held_out or ""
        control = result.control.families[family]
        outcome = entry.families[family]
        with_it = _percent(control.stage1_recall)
        if entry.control_in_sample:
            with_it = f"{with_it} _(in-sample)_"
        elif not entry.refitted:
            with_it = f"{with_it} _(same model)_"
        lines.append(
            f"| `{family}` | {with_it} | {_percent(control.stage1_named_rate)} "
            f"| {_percent(outcome.stage1_recall)} | {_percent(outcome.stage1_named_rate)} "
            f"| **{_percent(outcome.stage2_recall)}** | {_percent(outcome.total_recall)} |"
        )
    return lines


def _verdict(outcome: FamilyOutcome) -> str:
    """One sentence about this family's numbers, driven by the numbers.

    The first branch is on *which stage* carried the row, not on the total.
    A family Stage 1 generalised onto is a different result from one Stage 2
    found, and crediting a Stage 1 row to Stage 2 would be a generated sentence
    disagreeing with the table directly above it.
    """
    if outcome.carried_by == "stage1" and outcome.stage1_recall >= 0.2:
        return (
            f"Stage 1 carried this row, not Stage 2: {_percent(outcome.stage1_recall)} "
            "of the family cleared `tau_sup` under a *related* family's label, which is "
            "generalisation inside the classifier rather than novel-attack detection. "
            f"Stage 2 added {_percent(outcome.stage2_recall)} on top and "
            f"{_percent(outcome.miss_rate)} got through. Worth having, and not the "
            "claim the Stage 2 column is making."
        )
    if outcome.total_recall >= 0.6:
        return (
            f"Stage 2 surfaced {_percent(outcome.stage2_recall)} of a family the "
            f"classifier had never been shown, and {_percent(outcome.miss_rate)} got "
            "through. That is the claim this project is making, with its cost attached."
        )
    if outcome.total_recall >= 0.2:
        return (
            f"Partial: {_percent(outcome.total_recall)} caught, "
            f"{_percent(outcome.miss_rate)} missed. Worth having and not worth "
            "overselling -- a detector that finds a fifth to a half of an unseen family "
            "shortens an investigation rather than replacing one."
        )
    if outcome.total_recall >= 0.02:
        return (
            f"Largely missed: {_percent(outcome.miss_rate)} of this family got through "
            "both stages. The number is in the table because leaving it out would make "
            "the rest of the table less believable, not more."
        )
    return (
        f"Missed almost entirely -- {_percent(outcome.miss_rate)} got through. This is a "
        "real limitation of flow-level detection rather than a tuning problem, and no "
        "threshold moves it."
    )


def _validation_families(result: LoaoResult) -> str:
    """The families whose rows the inherited iteration count was selected on.

    Read off the arena's provenance rather than named, so the sentence cannot
    outlive a change to which capture day carries which family.
    """
    names = [slice_.name for slice_ in result.arena.attacks.values() if "val" in slice_.splits]
    return _join(names) or "none"


def _misses(result: LoaoResult) -> list[str]:
    lines: list[str] = []
    for entry in result.folds:
        family = entry.held_out or ""
        outcome = entry.families[family]
        lines += [
            f"**`{family}`** -- {outcome.support:,} rows from "
            f"{_join(list(outcome.splits))}; Stage 1 {_percent(outcome.stage1_recall)}, "
            f"Stage 2 {_percent(outcome.stage2_recall)}, missed "
            f"{_percent(outcome.miss_rate)}.",
            "",
            FAMILY_MECHANICS.get(family, ""),
            "",
            _verdict(outcome),
            "",
        ]
        if outcome.support < SMALL_SUPPORT_ROWS:
            low, high = _wilson(outcome.stage1_caught + outcome.stage2_caught, outcome.support)
            lines += [
                f"Small sample: {outcome.support:,} rows. The 95% interval on that "
                f"{_percent(outcome.total_recall)} total runs from {_percent(low)} to "
                f"{_percent(high)} (Wilson), so read the figure as a range and not as "
                "a decimal place.",
                "",
            ]
    for family, reason in result.arena.unmeasurable.items():
        lines += [
            f"**`{family}`** -- not measurable on this data: {reason}",
            "",
            FAMILY_MECHANICS.get(family, ""),
            "",
        ]
    return lines


def render_report(result: LoaoResult, settings: Any) -> str:
    """The checkpoint, in the form it can be committed and reviewed in."""
    arena = result.arena.record()
    refitted = [entry.held_out or "" for entry in result.folds if entry.refitted]
    natural = [entry.held_out or "" for entry in result.folds if not entry.refitted]
    best = max(
        result.folds,
        key=lambda entry: entry.families[entry.held_out or ""].stage2_recall,
        default=None,
    )

    lines = [
        "# Leave-one-attack-out",
        "",
        "The question this answers: **how much of an attack family does the system "
        "catch when the classifier has never been shown that family?** Stage 1 is "
        "refitted with the family removed. Stage 2 is untouched, because it never saw "
        "an attack label of any kind. The Stage 2 column is the headline.",
        "",
        f"Champion `{result.version}` (`{result.algorithm}`), schema "
        f"`{result.schema_hash}`, measured {result.measured_at}. "
        f"`tau_anom` = {result.tau_anom:.6e}, identical in every fold. `tau_sup` is "
        f"re-cut per fold from the validation day's benign rows at a "
        f"{result.target_fpr:.2e} false-positive budget.",
        "",
        "## The table",
        "",
        *_headline_table(result),
        "",
    ]

    if best is not None:
        outcome = best.families[best.held_out or ""]
        # "in the cascade" rather than "alone": the Stage 2 column is marginal,
        # and the section below uses "alone" for the standalone measurement. The
        # two coincide for the strongest row on this capture, which is a
        # coincidence rather than a licence to use the words interchangeably.
        lines += [
            f"Read the strongest row out loud: the system had never seen "
            f"`{best.held_out}` traffic and Stage 2 surfaced "
            f"{_percent(outcome.stage2_recall)} of it in the cascade. "
            f"{_percent(outcome.miss_rate)} of that family still got through. Both "
            "halves of that sentence are the result.",
            "",
        ]
        if "train" in outcome.splits:
            lines += [
                f"One qualification on that row before it gets quoted: every "
                f"`{best.held_out}` flow scored here comes from "
                f"{_join(list(outcome.splits))}, and the training days are the days "
                "whose *benign* traffic fitted Stage 2. No model was trained on these "
                "attack rows -- Stage 1 had them removed from its fit and Stage 2 never "
                "saw an attack label at all -- so what is weaker here than a held-out "
                "day is the separation, not the hold-out.",
                "",
            ]

    lines += [
        "## What each fold held out",
        "",
        f"Only {len(refitted)} of the {len(result.folds)} measured families needed a "
        "refit, and that is the temporal split doing the work rather than a shortcut: "
        "the training days are Tuesday and Wednesday, so Stage 1's vocabulary is "
        "benign, DoS and brute force. Every other family lives on a day the classifier "
        "never trained on, which means it was already held out before this evaluation "
        "started. Refitting to remove zero rows and calling it a retrain would be "
        "theatre; the rows-removed column is here so the difference is visible.",
        "",
        *_fold_table(result),
        "",
        f"Genuinely refitted: {_join(refitted) or 'none'}. Already held out by the "
        f"split or the support floor: {_join(natural) or 'none'}.",
        "",
        "## What the recall cost",
        "",
        "A recall figure with no false-positive rate beside it is not a result. The "
        f"negative class is the {_join(list(result.arena.benign.splits))} day's "
        f"{result.arena.benign.rows:,} benign flows -- the only traffic in the capture "
        "that Stage 1 was not trained on, Stage 2 was not fitted on, and neither "
        "threshold was cut on. The two PR-AUCs ask the same question without a "
        "threshold: how well does each stage's raw score separate this family from that "
        "benign traffic.",
        "",
        *_cost_table(result),
        "",
        f"The budget those alerts/hour figures are measured against is "
        f"{settings.analyst_capacity_per_hour} alerts per analyst per hour over an "
        f"{settings.analyst_shift_hours}-hour shift -- "
        f"{settings.max_alerts_per_day:,} per day against "
        f"{settings.expected_daily_flow_volume:,} flows, which is where the "
        f"{result.target_fpr:.2e} false-positive budget comes from.",
        "",
        "The alerts-per-hour column is the one to read carefully, and it is "
        "overwhelmingly Stage 2's. `tau_anom` is the brief's 99.5th percentile of "
        "benign reconstruction error -- a statement about what normal traffic looks "
        "like, made without reference to any attack, which is precisely what keeps "
        "Stage 2 honest. It is not a staffing decision, and it does not pretend to "
        "be one: Phase 3 measured the same benign distribution reaching the analyst "
        "budget only at its 99.968th percentile. The next section reports what Stage "
        "2 catches at each of the two thresholds, so the gap is a trade-off somebody "
        "can decide rather than a number that looks like a defect.",
        "",
        "## Stage 2 on its own",
        "",
        "Two things the headline table deliberately does not say. First, its Stage 2 "
        "column is a *marginal* figure -- what Stage 2 adds on rows Stage 1 passed "
        "through -- and that is lower than Stage 2's own recall wherever the two "
        "stages agree about a flow, which on high-rate floods is most of the time. "
        "Second, that figure is measured at the shipped threshold; the last column is "
        "the same measurement at the threshold cut to fit the queue on its "
        "calibration day -- which, as the verdict below this table says, does not "
        "mean it fits the queue here. None of this "
        "varies by fold, because the autoencoder is the component the loop holds "
        "fixed, so it is measured once.",
        "",
        *_stage2_table(result, settings),
        *_budget_verdict(result, settings),
        "## With the family in training, and without",
        "",
        "The control is the same procedure with nothing removed, which is what makes "
        "the table a comparison rather than an assertion. For a family the temporal "
        "split already held out, the control *is* the held-out model and the row says "
        "so -- there is no before-and-after to show.",
        "",
        *_control_table(result),
        "",
        "**Caught is not the same as named, and the two naming columns are why this "
        "matters.** Stage 1's score is the largest single attack-class probability, so "
        "a held-out family can clear `tau_sup` under a different family's label -- DDoS "
        "flows alerting as `dos` is the obvious case. That is still a correct alert "
        "about a flood, with the family one level off, and it is counted as caught for "
        "the same reason Phase 2 counts it: the analyst gets a true positive to work. "
        "But a held-out fold has no column for the family at all, so its naming rate is "
        "zero by construction. Where a held-out row shows Stage 1 recall above zero, "
        "read it as *an alert was raised on this flow under some other family's name*, "
        "never as classification.",
        "",
        "## Reading the misses",
        "",
        *_misses(result),
        "## Method",
        "",
        "**The fusion rule is the shipped one.** `training/fusion.py` is imported here "
        "and by `app/inference.py`; there is no second implementation written for the "
        "evaluation. Stage 1 names what clears `tau_sup`, everything else falls through "
        "to Stage 2, and the two columns are disjoint by construction.",
        "",
        "**The feature contract is frozen to the champion's.** Stage 2's weights were "
        "fitted against one `RobustScaler`, so scoring them through a per-fold scaler "
        "would not be the same model -- and the brief requires the autoencoder to be "
        "unchanged. The residual, stated rather than hidden: the frozen scaler's "
        "medians and interquartile ranges were computed over the held-out family's rows "
        "as well. Those are column statistics, not labels, and no fold's classifier "
        "ever sees a row of the family it is holding out.",
        "",
        "**No fold has a validation set.** Each fold trains for the champion's recorded "
        "iteration count with early stopping switched off, so a fold differs from the "
        "control in exactly one way. That count was chosen by the champion's own early "
        "stopping against the validation day, and it is the single thread connecting "
        "any fold to that day: one integer. Which way that integer points is worth "
        "stating too, because *one integer* reads as family-neutral and is not: the "
        "stopping rule maximised attack PR-AUC on the validation day, and the "
        f"validation day's attack rows are {_validation_families(result)} -- families "
        "in this very table. For those rows the iteration count was selected, in part, "
        "to detect them. `tau_sup` is re-cut per fold from benign rows only, which "
        "carries no information about any held-out family.",
        "",
        "**A family's rows are all of its rows.** Each family is scored on every row of "
        "it in the capture rather than on a sample, so the recall figure is a statement "
        "about the family and not about a chosen subset. Where those rows came from is "
        "in the provenance above -- and for the families that live on the training days, "
        "they come from a day whose *benign* traffic was in training even though their "
        "attack rows were removed from the fit. That is a weaker temporal separation "
        "than a held-out day, and it applies to exactly those rows of the table.",
        "",
        "**What this does not prove.** Leave-one-attack-out measures generalisation to "
        "held-out *known* attacks. It is a proxy for genuinely novel ones, not proof of "
        "them: these families existed in 2017 and were captured by the same lab, on the "
        "same network, as the benign baseline. The honest claim is that the system "
        "detects attack behaviour it was not trained to name -- which is what the Stage "
        "2 column measures -- not that it will catch whatever arrives next.",
        "",
        f"Arena: {arena['rows']:,} flows ({arena['benign']['rows']:,} benign + "
        f"{arena['rows'] - arena['benign']['rows']:,} attack). Full record in "
        f"`backend/artifacts/{METRICS_ARTIFACT}`.",
        "",
    ]
    return "\n".join(lines)


def update_model_card(result: LoaoResult, artifacts_dir: Path) -> Path | None:
    """Add a compact hold-out summary to the champion's card.

    Compact on purpose. ``GET /metrics/model`` serves the LOAO panel and
    ``app/inference.py`` already reads the card at startup, so putting the
    handful of numbers that panel draws here saves the serving path a second
    file. The full record -- arena provenance, per-fold thresholds, the control
    fold -- stays in ``metrics_loao.json``.

    Stage 1's and Stage 2's entries are untouched. The card is one record of
    one served pair, and this is an addition to it rather than a new one.
    """
    card_path = artifacts_dir / "model_card.json"
    if not card_path.exists():
        return None
    card = json.loads(card_path.read_text(encoding="utf-8"))
    if card.get("schema_hash") != result.schema_hash:
        raise MissingStage(
            f"model_card.json carries schema {card.get('schema_hash')} but the hold-out "
            f"run measured {result.schema_hash}. Something rewrote the canonical pair "
            "mid-run, so these numbers would be attributed to a model that did not "
            "produce them; re-run rather than record this."
        )

    card["loao"] = {
        "measured_at": result.measured_at,
        "champion": result.version,
        "tau_anom": result.tau_anom,
        "target_fpr": result.target_fpr,
        "benign_reference": {
            "split": list(result.arena.benign.splits),
            "rows": result.arena.benign.rows,
        },
        "unmeasurable": dict(result.arena.unmeasurable),
        "folds": [
            {
                "held_out": entry.held_out,
                "refitted": entry.refitted,
                **entry.families[entry.held_out or ""].record(),
                "fused_fpr": entry.benign.fpr,
                "control_stage1_recall": result.control.families[
                    entry.held_out or ""
                ].stage1_recall,
                # The dashboard panel draws the control column too, so the
                # caveat has to travel with the number rather than living only
                # in the write-up.
                "control_stage1_recall_in_sample": entry.control_in_sample,
            }
            for entry in result.folds
        ],
    }
    card_path.write_text(json.dumps(card, indent=2), encoding="utf-8")
    return card_path


def write_outputs(
    result: LoaoResult,
    artifacts_dir: Path,
    reports_dir: Path,
    settings: Any,
    partial_ok: bool = False,
) -> tuple[Path, Path]:
    """Write the report, the machine-readable record, and the card summary.

    A run covering fewer families than the capture carries is refused unless
    ``partial_ok`` says the caller meant it. See ``PartialRun``.
    """
    measurable = len(result.folds) + len(result.arena.unmeasurable)
    if not partial_ok and measurable < len(ATTACK_FAMILIES):
        raise PartialRun(
            f"this run measured {len(result.folds)} of {len(ATTACK_FAMILIES)} attack "
            f"families, so writing it would replace the full table with one family's "
            f"worth of rows. Re-run without --family, or pass partial_ok=True if "
            f"replacing the committed report is what you meant."
        )
    reports_dir.mkdir(parents=True, exist_ok=True)
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    report_path = reports_dir / REPORT_FILENAME
    report_path.write_text(render_report(result, settings), encoding="utf-8")

    # `allow_nan=False` is the guard, not a preference: `json.dumps` emits a
    # bare `NaN` by default and every strict parser downstream -- including the
    # browser's -- then rejects the whole file.
    metrics_path = artifacts_dir / METRICS_ARTIFACT
    metrics_path.write_text(
        json.dumps(result.record(), indent=2, allow_nan=False), encoding="utf-8"
    )
    update_model_card(result, artifacts_dir)
    return report_path, metrics_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="loao.py",
        description="Leave-one-attack-out: hold each family out of Stage 1 and measure "
        "what the fused pipeline still catches.",
    )
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument(
        "--family",
        action="append",
        default=None,
        help="Measure only this family (repeatable). Defaults to all seven. A run "
        "narrowed this way refuses to overwrite reports/loao.md unless "
        "--overwrite-partial is also given.",
    )
    parser.add_argument(
        "--overwrite-partial",
        dest="partial_ok",
        action="store_true",
        help="Allow a narrowed run to replace the committed full report",
    )
    parser.add_argument("--processed-dir", type=Path, default=None)
    parser.add_argument("--artifacts-dir", type=Path, default=None)
    parser.add_argument("--reports-dir", type=Path, default=None)
    args = parser.parse_args(argv)

    from app.config import settings

    logging.basicConfig(level=logging.INFO, format="%(levelname)-8s %(message)s")

    processed_dir = args.processed_dir or settings.data_path / "processed"
    artifacts_dir = args.artifacts_dir or settings.artifacts_path
    reports_dir = args.reports_dir or settings.reports_path

    unknown = sorted(set(args.family or ()) - set(ATTACK_FAMILIES))
    if unknown:
        raise SystemExit(f"unknown family {unknown}; expected some of {list(ATTACK_FAMILIES)}")

    result = run_loao(
        load_split(processed_dir, "train"),
        load_split(processed_dir, "val"),
        load_split(processed_dir, "test"),
        artifacts_dir=artifacts_dir,
        settings=settings,
        seed=args.seed,
        families=tuple(args.family) if args.family else ATTACK_FAMILIES,
    )

    report_path, metrics_path = write_outputs(
        result, artifacts_dir, reports_dir, settings, partial_ok=bool(args.partial_ok)
    )
    echo(result.render())
    echo(f"\nwritten to {report_path}")
    echo(f"           {metrics_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
