"""Phase 7 -- re-fit Stage 2's benign baseline on analyst-confirmed traffic.

The autoencoder's idea of "normal" is fixed at the moment it was trained, and the
network it watches is not. A new backup job, a new SaaS dependency, a new
monitoring agent -- each one is ordinary traffic the baseline has never seen, so
each one fires Stage 2 until somebody teaches the baseline otherwise. Analysts
are already doing the teaching: every alert they mark a false positive is a row
a human has looked at and called benign. This module turns those rows into a
challenger autoencoder and only lets it serve if it is measurably better.

It is also the most attackable thing in the project, and that shapes every
decision below. An adversary who can generate enough traffic and get it waved
through can teach the baseline that their traffic is normal, after which Stage 2
stops seeing it -- silently, because nothing raises when a detector learns to
ignore something. So the refit never reads traffic directly. It reads the pool
``app.feedback.build_benign_pool`` assembles, and that pool has two guards
already applied: no row without an analyst's explicit false-positive
confirmation, and no single source host above ``IDS_BENIGN_REFIT_HOST_CAP`` of
the pool. Below ``IDS_BENIGN_REFIT_MIN_ROWS`` it is refused outright and the
champion is left alone, and this module says so rather than refitting on thin
evidence.

Four decisions worth defending.

**A refit, not a retrain.** The challenger starts from the champion's weights
and takes a few epochs at a tenth of the original learning rate. Retraining from
scratch on 1.2 million Phase 3 rows plus a few hundred confirmed ones would
weight the new evidence at a few parts in ten thousand -- a refit that changes
nothing, at the cost of an hour. Starting from the champion keeps everything it
already knows and moves only what the new rows argue for.

**Rehearsal, so the baseline is moved rather than replaced.** Fine-tuning on the
pool alone would teach the network the pool and let it forget everything else,
including what made it good at flagging attacks. So every refit epoch mixes the
pool with a fixed sample of the original benign training set, and early
stopping watches a held-out slice of *that* set -- the stopping signal is "has
the old baseline started to degrade", which is the failure a refit risks.
The pool is repeated up to a fixed share of each epoch; at its natural weight a
few hundred rows among sixty thousand are half a percent of the gradient, and
the refit would be a ceremony.

**The gate is a held-out set that contains both questions.** A refit can go
wrong in two directions: it can fail to learn the new normal, or it can learn it
by forgetting what an attack looks like. So the champion and the challenger are
scored on one set that asks both: the validation day (its benign rows and its
attacks, the same rows ``tau_anom`` was cut from), plus a slice of the pool
withheld from the fit and labelled benign, because an analyst said so. PR-AUC on
that set rises only if the challenger ranks the confirmed-benign rows lower
*without* ranking the attacks lower with them. Each model is scored at its own
threshold, cut by the same Phase 3 rule -- the 99.5th percentile of validation
benign error -- so the comparison is between two deployments, not between a
model and a number.

**Promotion is reversible, and visible.** The champion's weights and metrics are
archived before anything is overwritten, the model card records the refit and
the version it replaced, and the served version string changes -- see
``app.inference.compose_version`` -- so every alert afterwards names the baseline
that produced it.

    python -m training.retrain --now          # runs this after Stage 1
    python -m training.refit_autoencoder      # this alone, from the current pool
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from training.autoencoder import Autoencoder, reconstruction_error
from training.metrics import (
    ANOMALY_PERCENTILE,
    detection_curves,
    select_anomaly_threshold,
)
from training.train_autoencoder import (
    AUTOENCODER_ARTIFACT,
    METRICS_ARTIFACT,
    RUN_ARTIFACT,
    BenignData,
    assert_attack_free,
    fit_autoencoder,
    load_artifacts,
    measure,
    prepare_benign,
    score_split,
    update_model_card,
    write_artifacts,
)
from training.train_supervised import load_split

logger = logging.getLogger(__name__)

# Where a replaced champion's weights and metrics go. A refit that turns out to
# be wrong is a file copy back, not a retrain.
ARCHIVE_DIR = "stage2_archive"

# Rows of the original benign training set mixed into every refit epoch.
# Enough to hold the old baseline in place, few enough that a refit takes a
# minute rather than the Phase 3 hour.
REHEARSAL_ROWS = 60_000

# The share of each refit epoch the confirmed-benign pool makes up, by
# repetition. See the module docstring: at its natural weight the pool barely
# moves the gradient at all.
POOL_SHARE = 0.25

# The share of the pool withheld from the fit and used only by the gate. Rows
# the challenger was fitted on cannot be evidence that it learned them.
POOL_HOLDOUT_FRACTION = 0.25

# A refit, not a retrain: a few epochs, a tenth of the Phase 3 learning rate,
# stopping as soon as the old baseline starts to degrade.
REFIT_MAX_EPOCHS = 12
REFIT_PATIENCE = 3
REFIT_LEARNING_RATE = 1e-4

# How much better on the gate a challenger has to be. Zero would promote on
# floating-point noise, and a promotion changes what scores production traffic.
MIN_IMPROVEMENT = 1e-4

SEED = 7

GATE_DESCRIPTION = (
    "PR-AUC on the validation day plus the held-out slice of the confirmed-benign "
    "pool, each model at its own tau_anom (99.5th percentile of validation benign error)"
)


@dataclass
class Stage2Arm:
    """One model, measured on the gate set at its own threshold."""

    version: str
    tau_anom: float
    pr_auc: float
    roc_auc: float
    # On the validation day, at this model's own threshold.
    attack_recall: float
    benign_fpr: float
    # The rows the refit exists for: confirmed benign, never fitted on.
    pool_holdout_fpr: float
    pool_holdout_flagged: int


@dataclass
class Stage2Refit:
    """What a refit attempt did, whichever way it went.

    ``attempted`` is False when the pool was refused -- which is the normal case
    on replayed CICIDS2017 traffic, where every unclassified anomaly is
    attributed to one documented attacker address and the host cap leaves a
    single row. That row is recorded here rather than dropped, because "the
    baseline was left alone, and here is why" is the guard working.
    """

    attempted: bool
    promoted: bool
    decision: str
    gate: str = GATE_DESCRIPTION
    pool: dict[str, Any] = field(default_factory=dict)
    champion_version: str | None = None
    challenger_version: str | None = None
    rehearsal_rows: int = 0
    pool_fit_rows: int = 0
    pool_holdout_rows: int = 0
    pool_repeats: int = 0
    epochs_run: int = 0
    best_epoch: int = 0
    champion: dict[str, Any] | None = None
    challenger: dict[str, Any] | None = None
    improvement: float | None = None
    archived_to: str | None = None

    def render(self) -> str:
        lines = [f"stage 2           {'refit attempted' if self.attempted else 'left alone'}"]
        if self.attempted and self.champion and self.challenger:
            lines += [
                f"gate              {self.gate}",
                f"rehearsal rows    {self.rehearsal_rows:,} of the Phase 3 benign set",
                f"pool rows         {self.pool_fit_rows:,} fitted (x{self.pool_repeats}), "
                f"{self.pool_holdout_rows:,} held out for the gate",
                f"epochs            {self.epochs_run} run, best at {self.best_epoch}",
                "",
                f"{'':18}{'champion':>14}{'challenger':>14}",
            ]
            for key, label, fmt in (
                ("version", "version", "{}"),
                ("tau_anom", "tau_anom", "{:.4e}"),
                ("pr_auc", "gate PR-AUC", "{:.4f}"),
                ("roc_auc", "gate ROC-AUC", "{:.4f}"),
                ("attack_recall", "val attack recall", "{:.1%}"),
                ("benign_fpr", "val benign FPR", "{:.2%}"),
                ("pool_holdout_fpr", "held-out pool FPR", "{:.1%}"),
            ):
                champion = fmt.format(self.champion[key])
                challenger = fmt.format(self.challenger[key])
                if key == "version":
                    lines.append(f"{label:<18}{champion}")
                    lines.append(f"{'':<18}-> {challenger}")
                else:
                    lines.append(f"{label:<18}{champion:>14}{challenger:>14}")
        lines += ["", f"decision          {self.decision}"]
        return "\n".join(lines)


def pool_summary(pool: Any) -> dict[str, Any]:
    """The guard's own account of the pool, for the record and the report."""
    return {
        "candidates": int(pool.candidates),
        "admitted": int(pool.admitted),
        "refused_by_cap": int(pool.capped),
        "hosts": len(pool.admitted_by_host),
        "host_cap": float(pool.host_cap),
        "per_host_allowance": int(pool.per_host_allowance),
        "usable": bool(pool.usable),
        "reason": str(pool.reason).lstrip("- ").strip().rstrip("."),
    }


def split_pool(
    flows: list[dict[str, Any]], holdout_fraction: float = POOL_HOLDOUT_FRACTION, seed: int = SEED
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Deterministically withhold part of the pool from the fit.

    Seeded, so the same verdicts produce the same split and a refit can be
    re-derived. At least one row on each side whenever there are two to split:
    a gate with no confirmed-benign rows in it could not see the thing the refit
    was for, and a fit with none would not be a refit.
    """
    if len(flows) < 2:
        return list(flows), []
    order = np.random.default_rng(seed).permutation(len(flows))
    held = min(len(flows) - 1, max(1, int(round(len(flows) * holdout_fraction))))
    holdout = [flows[index] for index in sorted(order[:held])]
    fit = [flows[index] for index in sorted(order[held:])]
    return fit, holdout


def _matrix(flows: list[dict[str, Any]], bundle: dict[str, Any]) -> np.ndarray:
    from training.features import build_feature_matrix

    frame = pd.DataFrame([{k: v for k, v in flow.items() if k != "_provenance"} for flow in flows])
    return build_feature_matrix(frame, bundle).to_numpy(dtype="float32")


def measure_arm(
    model: Autoencoder,
    version: str,
    validation: Any,
    pool_holdout: np.ndarray,
    percentile: float = ANOMALY_PERCENTILE,
) -> Stage2Arm:
    """Score one model on the gate set, at a threshold it cut for itself."""
    threshold = select_anomaly_threshold(validation.benign_errors, percentile=percentile)
    holdout_errors = reconstruction_error(model, pool_holdout) if len(pool_holdout) else np.empty(0)

    truth = np.concatenate([validation.is_attack, np.zeros(len(holdout_errors), dtype=bool)])
    scores = np.concatenate([validation.errors, holdout_errors])
    curves = detection_curves(truth, scores)

    attack = validation.attack_errors
    flagged = int((holdout_errors >= threshold.tau).sum())
    return Stage2Arm(
        version=version,
        tau_anom=float(threshold.tau),
        pr_auc=float(curves.pr_auc),
        roc_auc=float(curves.roc_auc),
        attack_recall=float((attack >= threshold.tau).mean()) if attack.size else 0.0,
        benign_fpr=float(threshold.fpr),
        pool_holdout_fpr=(flagged / len(holdout_errors)) if len(holdout_errors) else 0.0,
        pool_holdout_flagged=flagged,
    )


def _refit_version(now: datetime) -> str:
    # Short on purpose: it is joined to Stage 1's version into a column that is
    # sixty-four characters wide (see `app.inference.compose_version`).
    return f"stage2-ae-refit-{now:%Y%m%d%H%M}"


def archive_champion(artifacts_dir: Path, version: str) -> Path:
    """Copy the serving Stage 2 aside before a promotion overwrites it."""
    archive = artifacts_dir / ARCHIVE_DIR / version
    archive.mkdir(parents=True, exist_ok=True)
    for name in (AUTOENCODER_ARTIFACT, METRICS_ARTIFACT, RUN_ARTIFACT):
        source = artifacts_dir / name
        if source.exists():
            shutil.copy2(source, archive / name)
    return archive


def refit_stage2(
    pool: Any,
    *,
    artifacts_dir: Path,
    processed_dir: Path,
    settings: Any,
    dry_run: bool = False,
    seed: int = SEED,
    rehearsal_rows: int = REHEARSAL_ROWS,
    max_epochs: int = REFIT_MAX_EPOCHS,
    patience: int = REFIT_PATIENCE,
    learning_rate: float = REFIT_LEARNING_RATE,
    frames: dict[str, pd.DataFrame] | None = None,
) -> Stage2Refit:
    """Refit the autoencoder on the guarded pool, gate it, and promote on improvement.

    ``frames`` lets a caller that already holds the processed splits pass them
    in (keys ``benign_train``, ``val``, ``test``) instead of reading half a
    gigabyte of Parquet a second time.
    """
    summary = pool_summary(pool)
    if not pool.usable:
        return Stage2Refit(
            attempted=False,
            promoted=False,
            pool=summary,
            decision=(
                "Stage 2 left alone: the confirmed-benign pool was refused -- "
                f"{summary['reason']}. The champion keeps serving unchanged."
            ),
        )

    import torch

    bundle, card = load_artifacts(artifacts_dir)
    weights_path = artifacts_dir / AUTOENCODER_ARTIFACT
    if not weights_path.exists():
        return Stage2Refit(
            attempted=False,
            promoted=False,
            pool=summary,
            decision=(
                f"Stage 2 left alone: there is no {AUTOENCODER_ARTIFACT} to refit. "
                "Train Stage 2 first (make train-anomaly)."
            ),
        )

    champion_state = torch.load(weights_path, map_location="cpu", weights_only=True)
    champion = Autoencoder.from_state_dict(champion_state)
    champion_version = str((card.get("stage2") or {}).get("version") or "stage2-unversioned")

    frames = frames or {}
    benign_train = frames.get("benign_train")
    if benign_train is None:
        benign_train = load_split(processed_dir, "benign_train")
    val_frame = frames.get("val")
    if val_frame is None:
        val_frame = load_split(processed_dir, "val")

    # The rehearsal set comes from the Phase 3 training set, and it is asserted
    # attack-free here for the same reason Phase 3 asserts it: the pool is
    # benign by an analyst's word, the rehearsal set by the split's.
    assert_attack_free(benign_train)
    sample = benign_train.sample(
        n=min(len(benign_train), int(rehearsal_rows * 1.15)), random_state=seed
    )
    rehearsal = prepare_benign(sample, bundle, seed=seed)

    fit_flows, holdout_flows = split_pool(pool.flows, seed=seed)
    pool_fit = _matrix(fit_flows, bundle)
    pool_holdout = _matrix(holdout_flows, bundle) if holdout_flows else np.empty((0, 0))

    repeats = max(
        1,
        int(np.ceil(POOL_SHARE / (1 - POOL_SHARE) * len(rehearsal.x_fit) / max(len(pool_fit), 1))),
    )
    data = BenignData(
        x_fit=np.vstack([rehearsal.x_fit, np.repeat(pool_fit, repeats, axis=0)]),
        x_early_stop=rehearsal.x_early_stop,
        source_rows=rehearsal.source_rows + len(fit_flows),
        duplicate_rows=rehearsal.duplicate_rows,
    )
    logger.info(
        "refitting Stage 2 from %s: %d rehearsal rows + %d pool rows x%d, %d held out",
        champion_version,
        len(rehearsal.x_fit),
        len(pool_fit),
        repeats,
        len(holdout_flows),
    )

    challenger, hyperparameters, history = fit_autoencoder(
        data,
        max_epochs=max_epochs,
        patience=patience,
        learning_rate=learning_rate,
        seed=seed,
        initial_state=champion_state,
    )

    champion_val = score_split(champion, val_frame, bundle, "val")
    challenger_val = score_split(challenger, val_frame, bundle, "val")

    now = datetime.now(UTC)
    challenger_version = _refit_version(now)
    champion_arm = measure_arm(champion, champion_version, champion_val, pool_holdout)
    challenger_arm = measure_arm(challenger, challenger_version, challenger_val, pool_holdout)
    improvement = challenger_arm.pr_auc - champion_arm.pr_auc

    result = Stage2Refit(
        attempted=True,
        promoted=False,
        decision="",
        pool=summary,
        champion_version=champion_version,
        challenger_version=challenger_version,
        rehearsal_rows=int(len(rehearsal.x_fit)),
        pool_fit_rows=len(fit_flows),
        pool_holdout_rows=len(holdout_flows),
        pool_repeats=repeats,
        epochs_run=int(hyperparameters.get("epochs_run", 0)),
        best_epoch=int(hyperparameters.get("best_epoch", 0)),
        champion=asdict(champion_arm),
        challenger=asdict(challenger_arm),
        improvement=float(improvement),
    )

    effect = (
        f"held-out confirmed-benign rows flagged {champion_arm.pool_holdout_fpr:.1%} -> "
        f"{challenger_arm.pool_holdout_fpr:.1%}, validation attack recall "
        f"{champion_arm.attack_recall:.1%} -> {challenger_arm.attack_recall:.1%}"
    )
    if improvement <= MIN_IMPROVEMENT:
        result.decision = (
            f"Stage 2 not promoted: gate PR-AUC moved by {improvement:+.4f} "
            f"({effect}). The champion keeps serving; this row is the evidence the "
            "gate works."
        )
        return result
    if dry_run:
        result.decision = (
            f"Stage 2 would be promoted: gate PR-AUC improved by {improvement:+.4f} "
            f"({effect}). Dry run, nothing written."
        )
        return result

    # Promotion. Archive first, so nothing below can leave the champion
    # unrecoverable, then write the challenger through Phase 3's own writers --
    # one code path for what a Stage 2 artifact contains.
    archive = archive_champion(artifacts_dir, champion_version)
    test_frame = frames.get("test")
    if test_frame is None:
        test_frame = load_split(processed_dir, "test")
    test_scores = score_split(challenger, test_frame, bundle, "test")

    previous_training = _previous_training(artifacts_dir)
    run = measure(
        challenger,
        data,
        challenger_val,
        test_scores,
        bundle,
        test_frame,
        settings,
        hyperparameters,
        history,
        card=card,
        baselines=[],
        arena={},
        seed=seed,
    )
    run.version = challenger_version
    run.trained_at = now.isoformat(timespec="seconds")
    run.trained_on = (
        f"refit of {champion_version}: {len(rehearsal.x_fit):,} rows of the Phase 3 "
        f"benign set as rehearsal, plus {len(fit_flows):,} analyst-confirmed false "
        f"positives from {summary['hosts']} host(s) (host cap {summary['host_cap']:.0%})"
    )
    # The PyOD comparison was measured against the champion this replaces. It
    # is carried over, labelled, rather than dropped: the screen that shows it
    # should say what it is rather than go quiet.
    run.baselines = previous_training.get("baselines") or []
    run.arena = {
        **(previous_training.get("arena") or {}),
        "measured_against": champion_version,
    }

    write_artifacts(challenger, run, artifacts_dir, settings)
    update_model_card(run, artifacts_dir)
    _record_refit_on_card(artifacts_dir, result, champion_version)

    result.promoted = True
    result.archived_to = str(archive)
    result.decision = (
        f"Stage 2 promoted: gate PR-AUC improved by {improvement:+.4f} ({effect}). "
        f"{champion_version} archived to {archive.name}/ and replaced by "
        f"{challenger_version}; restart the API to serve it."
    )
    return result


def _previous_training(artifacts_dir: Path) -> dict[str, Any]:
    path = artifacts_dir / METRICS_ARTIFACT
    if not path.exists():
        return {}
    return (json.loads(path.read_text(encoding="utf-8")).get("training")) or {}


def _record_refit_on_card(artifacts_dir: Path, result: Stage2Refit, replaced: str) -> None:
    """Note on the card that this Stage 2 is a refit, and of what."""
    card_path = artifacts_dir / "model_card.json"
    card = json.loads(card_path.read_text(encoding="utf-8"))
    stage2 = card.setdefault("stage2", {})
    stage2["previous_version"] = replaced
    stage2["refit"] = {
        "gate": result.gate,
        "pool": result.pool,
        "rehearsal_rows": result.rehearsal_rows,
        "pool_fit_rows": result.pool_fit_rows,
        "pool_holdout_rows": result.pool_holdout_rows,
        "champion": result.champion,
        "challenger": result.challenger,
        "improvement": result.improvement,
    }
    card_path.write_text(json.dumps(card, indent=2), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Measure, promote nothing.")
    args = parser.parse_args(argv)

    from app.config import settings
    from app.db import session_scope
    from app.feedback import build_benign_pool
    from training.console import echo

    logging.basicConfig(level=logging.INFO, format="%(levelname)-8s %(message)s")

    with session_scope() as session:
        pool = build_benign_pool(session)

    result = refit_stage2(
        pool,
        artifacts_dir=settings.artifacts_path,
        processed_dir=settings.data_path / "processed",
        settings=settings,
        dry_run=args.dry_run,
    )
    echo(pool.render())
    echo("")
    echo(result.render())
    return 0


if __name__ == "__main__":
    sys.exit(main())
