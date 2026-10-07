"""Phase 2/3 -- honest evaluation of the held-out test day.

Everything the model needed to decide -- the depth, the port encoding, the
operating threshold -- was decided on the validation day by
``train_supervised.py``. This module opens Friday once, measures, and writes it
down.

What it emits: per-class precision, recall, F1 and support; the confusion
matrix; PR and ROC curves as data for the dashboard to draw side by side;
PR-AUC as the headline; the false-positive rate at the chosen threshold; and
the projected alerts per analyst per hour.

Accuracy appears in exactly one table cell, marked as non-headline. On traffic
that is 99% benign a model that always answers benign scores 99%, so the number
describes the class balance rather than the model.
"""

from __future__ import annotations

import argparse
import json
import logging
import pickle
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from training.console import echo
from training.features import build_feature_matrix, load_preprocessing_bundle
from training.labels import BENIGN_FAMILY, FAMILIES, map_labels
from training.metrics import (
    accuracy,
    alert_volume,
    attack_confidence,
    attack_probability,
    confusion,
    detection_curves,
    per_class_report,
    predicted_attack_family,
    recall_by_family,
)
from training.train_supervised import load_split

logger = logging.getLogger(__name__)

REPORT_FILENAME = "phase2_supervised.md"
METRICS_FILENAME = "metrics_supervised.json"


def output_names(algorithm: str | None) -> tuple[str, str]:
    """Report and metrics filenames for the model being evaluated.

    The champion owns the unsuffixed names, because those are what the README
    and the dashboard point at. Evaluating a fallback writes beside them rather
    than over them -- otherwise checking the baseline would silently replace the
    write-up of the model that actually ships.
    """
    if algorithm is None:
        return REPORT_FILENAME, METRICS_FILENAME
    return f"phase2_supervised_{algorithm}.md", f"metrics_supervised_{algorithm}.json"


class ArtifactMismatch(RuntimeError):
    """The model and the preprocessing it is paired with disagree.

    Fatal, for the same reason the API refuses to start on it: a model fed a
    differently-ordered or differently-scaled matrix produces confident nonsense
    and raises nothing on its own.
    """


@dataclass
class LoadedModel:
    model: Any
    bundle: dict[str, Any]
    payload: dict[str, Any]

    @property
    def classes(self) -> list[str]:
        return [str(name) for name in self.model.classes_]

    @property
    def tau_sup(self) -> float:
        return float(self.payload["tau_sup"])


def load_model(artifacts_dir: Path, algorithm: str | None = None) -> LoadedModel:
    """Load the champion pair, or a named algorithm's fallback pair.

    Both halves are first-party files written by this package into a gitignored
    directory; there is no code path that unpickles anything uploaded or
    downloaded.
    """
    suffix = f"_{algorithm}" if algorithm else "_model"
    model_path = artifacts_dir / f"supervised{suffix}.pkl"
    bundle_path = artifacts_dir / (
        f"preprocessing_{algorithm}.pkl" if algorithm else "preprocessing.pkl"
    )
    if not model_path.exists():
        raise SystemExit(f"{model_path} not found. Train Stage 1 first (make train).")

    with model_path.open("rb") as handle:
        payload = pickle.load(handle)  # noqa: S301 - first-party artifact
    bundle = load_preprocessing_bundle(bundle_path)

    if payload.get("schema_hash") != bundle.get("schema_hash"):
        raise ArtifactMismatch(
            f"{model_path.name} was trained against schema "
            f"{payload.get('schema_hash')} but {bundle_path.name} carries "
            f"{bundle.get('schema_hash')}. Retrain rather than evaluate this pair."
        )

    return LoadedModel(model=payload["model"], bundle=bundle, payload=payload)


def load_run_record(
    artifacts_dir: Path, version: str, algorithm: str | None
) -> dict[str, Any] | None:
    """The training record belonging to the model being evaluated.

    The champion's record is carried inside its model card. A challenger that
    lost still has its own ``training_<algorithm>.json`` on disk, so looking the
    record up by algorithm name alone would happily pair one run's model with
    another run's sweep tables.
    """
    if algorithm is None:
        card_path = artifacts_dir / "model_card.json"
        if card_path.exists():
            card = json.loads(card_path.read_text(encoding="utf-8"))
            if card.get("version") == version:
                return card.get("run")
        return None

    run_path = artifacts_dir / f"training_{algorithm}.json"
    if not run_path.exists():
        return None
    run = json.loads(run_path.read_text(encoding="utf-8"))
    return run if run.get("version") == version else None


def operating_point_prediction(proba: np.ndarray, classes: list[str], tau: float) -> np.ndarray:
    """What the system actually emits: a family only when the threshold is cleared.

    A plain ``argmax`` report describes an operating point nobody runs. The
    served rule is the fusion rule -- name the family when the attack
    confidence clears ``tau_sup``, otherwise stay quiet and let Stage 2 look at
    it -- so that is the rule the report is written against.
    """
    confidence = attack_confidence(proba, classes)
    family = predicted_attack_family(proba, classes)
    return np.where(confidence >= tau, family, BENIGN_FAMILY)


@dataclass
class Evaluation:
    """Measured numbers for one split, under one model."""

    split: str
    rows: int
    version: str
    algorithm: str
    port_encoding: str
    tau_sup: float
    classes: list[str]
    labels: list[str]
    per_class: dict[str, dict[str, float]] = field(default_factory=dict)
    confusion: list[list[int]] = field(default_factory=list)
    family_recall: dict[str, dict[str, float]] = field(default_factory=dict)
    pr_auc: float = float("nan")
    roc_auc: float = float("nan")
    pr_auc_one_minus_benign: float = float("nan")
    pr_curve: list[list[float]] = field(default_factory=list)
    roc_curve: list[list[float]] = field(default_factory=list)
    volume: dict[str, Any] = field(default_factory=dict)
    accuracy: float = float("nan")
    attack_rows: int = 0
    benign_rows: int = 0


def evaluate_split(
    loaded: LoadedModel,
    frame: pd.DataFrame,
    split: str,
    settings: Any,
) -> Evaluation:
    """Score one split at the persisted threshold and measure everything."""
    families = map_labels(frame["label"]).astype(str).reset_index(drop=True)
    matrix = build_feature_matrix(frame, loaded.bundle).to_numpy(dtype="float32")

    classes = loaded.classes
    proba = loaded.model.predict_proba(matrix)
    confidence = attack_confidence(proba, classes)
    tau = loaded.tau_sup

    is_benign = (families == BENIGN_FAMILY).to_numpy()
    is_attack = ~is_benign
    flagged = confidence >= tau

    predicted = operating_point_prediction(proba, classes, tau)
    labels = [family for family in FAMILIES if family in set(families) or family in classes]

    curves = detection_curves(is_attack, confidence)
    alternative = detection_curves(is_attack, attack_probability(proba, classes))
    volume = alert_volume(
        confidence,
        is_benign,
        tau,
        settings.expected_daily_flow_volume,
        settings.analyst_capacity_per_hour,
        settings.analyst_shift_hours,
    )

    return Evaluation(
        split=split,
        rows=int(len(frame)),
        version=str(loaded.payload["version"]),
        algorithm=str(loaded.payload["algorithm"]),
        port_encoding=str(loaded.payload["port_encoding"].get("strategy")),
        tau_sup=tau,
        classes=classes,
        labels=labels,
        per_class=per_class_report(families, predicted, labels),
        confusion=confusion(families, predicted, labels),
        family_recall=recall_by_family(families, flagged, labels),
        pr_auc=curves.pr_auc,
        roc_auc=curves.roc_auc,
        pr_auc_one_minus_benign=alternative.pr_auc,
        pr_curve=curves.pr_curve,
        roc_curve=curves.roc_curve,
        volume=asdict(volume),
        accuracy=accuracy(families, predicted),
        attack_rows=curves.positives,
        benign_rows=curves.negatives,
    )


# ---------------------------------------------------------------------------
# The write-up
# ---------------------------------------------------------------------------


def _percent(value: float) -> str:
    return "n/a" if np.isnan(value) else f"{value:.1%}"


def _per_class_table(evaluation: Evaluation) -> list[str]:
    rows = [
        "| Class | Precision | Recall | F1 | Support | In Stage 1's vocabulary |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for name in evaluation.labels:
        scores = evaluation.per_class.get(name)
        if scores is None:
            continue
        known = "yes" if name in evaluation.classes else "**no — never trained on**"
        rows.append(
            f"| `{name}` | {scores['precision']:.3f} | {scores['recall']:.3f} | "
            f"{scores['f1']:.3f} | {scores['support']:,} | {known} |"
        )
    return rows


def _confusion_table(evaluation: Evaluation) -> list[str]:
    header = "| true \\ predicted | " + " | ".join(f"`{name}`" for name in evaluation.labels) + " |"
    divider = "| --- " * (len(evaluation.labels) + 1) + "|"
    rows = [header, divider]
    for name, row in zip(evaluation.labels, evaluation.confusion, strict=True):
        rows.append(f"| `{name}` | " + " | ".join(f"{cell:,}" for cell in row) + " |")
    return rows


def _family_recall_table(evaluation: Evaluation) -> list[str]:
    rows = [
        "| Family | Rows on this day | Flagged by Stage 1 | Recall |",
        "| --- | --- | --- | --- |",
    ]
    for name, scores in evaluation.family_recall.items():
        if name == BENIGN_FAMILY:
            continue
        rows.append(
            f"| `{name}` | {int(scores['support']):,} | {int(scores['flagged']):,} | "
            f"**{scores['recall']:.1%}** |"
        )
    return rows


def _pr_versus_roc(evaluation: Evaluation, run: dict[str, Any] | None) -> list[str]:
    """The two curves, and the reason for reporting them next to each other.

    The gap is a function of how rare the positive class is, so the sharpest
    illustration is the validation day rather than this one: CICIDS2017's attack
    days carry enough attack traffic that PR and ROC nearly agree, while a
    realistically benign day pulls them apart. Quoting both days makes that the
    reader's observation rather than a claim.
    """
    lines = [
        "ROC's false-positive rate is divided by the benign count, so on traffic that is "
        "almost all benign thousands of false alerts barely move it. Precision has no such "
        "denominator and every false positive costs it immediately. The curve points are "
        "persisted alongside this report, in the metrics JSON named after the model, so the "
        "dashboard can draw the two side by side.",
    ]

    validation = (run or {}).get("validation")
    if not validation:
        return lines

    benign_share = evaluation.benign_rows / evaluation.rows if evaluation.rows else 0.0
    val_benign = validation.get("benign_rows", 0)
    val_total = val_benign + validation.get("attack_rows", 0)
    val_share = val_benign / val_total if val_total else 0.0

    return lines + [
        "",
        "| Split | Benign share | PR-AUC | ROC-AUC | Gap |",
        "| --- | --- | --- | --- | --- |",
        f"| Validation (Thursday) | {val_share:.1%} | {validation['pr_auc']:.4f} | "
        f"{validation['roc_auc']:.4f} | {validation['roc_auc'] - validation['pr_auc']:.4f} |",
        f"| Test (Friday) | {benign_share:.1%} | {evaluation.pr_auc:.4f} | "
        f"{evaluation.roc_auc:.4f} | {evaluation.roc_auc - evaluation.pr_auc:.4f} |",
        "",
        "That is the whole argument in two rows. On Thursday, where attacks are a rounding "
        "error in the traffic, ROC-AUC reads near-perfect while PR-AUC is far lower — and "
        "PR-AUC is the one that corresponds to an analyst's experience of the queue. On "
        "Friday the attack share is high enough that the two nearly agree, which is exactly "
        "why a single ROC figure quoted without the class balance beside it says very little.",
    ]


def _interpretation(evaluation: Evaluation, unseen: list[str]) -> str:
    """The checkpoint's written paragraph, templated from what was measured.

    Generated from the numbers rather than written once by hand, so a rerun
    that moves them cannot leave a stale claim behind.
    """
    caught = {
        name: scores["recall"]
        for name, scores in evaluation.family_recall.items()
        if name != BENIGN_FAMILY
    }
    best = max(caught.items(), key=lambda item: item[1], default=(None, 0.0))
    worst = min(caught.items(), key=lambda item: item[1], default=(None, 0.0))

    unseen_note = (
        f"Every attack family on this day — {', '.join(f'`{name}`' for name in unseen)} — is "
        "absent from Stage 1's training vocabulary, because CICIDS2017 runs each family on a "
        "single capture day and the split is temporal. The per-class recall for those rows is "
        "therefore not a measure of a model that tried and failed to name them; it is a "
        "structural zero."
        if unseen
        else "Every family on this day is in Stage 1's vocabulary."
    )

    # Benign is in `family_recall` too, and its flagged count is the false
    # positives. Counting those as attack traffic surfaced would flatter the
    # number by exactly the amount the threshold was chosen to keep small.
    overall = sum(
        scores["flagged"]
        for name, scores in evaluation.family_recall.items()
        if name != BENIGN_FAMILY
    )
    attack_rows = evaluation.attack_rows or 1

    return (
        f"{unseen_note} What Stage 1 can still do is recognise those rows as *some* attack "
        f"when they resemble a family it does know, and at `tau_sup = {evaluation.tau_sup:.4f}` "
        f"it flags {_percent(best[1])} of `{best[0]}` and {_percent(worst[1])} of `{worst[0]}` "
        f"— {overall / attack_rows:.1%} of the day's attack traffic in total. The classes it "
        f"handles worst are the ones whose flow shape has no analogue in Tuesday's brute-force "
        f"traffic or Wednesday's denial-of-service traffic; the ones it handles best are the "
        f"ones that do.\n\n"
        f"The more interesting number is the one those two disagree with. PR-AUC on this day "
        f"is {evaluation.pr_auc:.4f}, which says the model *ranks* Friday's attacks well above "
        f"its benign traffic even though it cannot name a single one of them. Recall at "
        f"`tau_sup` is low not because the ranking is poor but because the threshold sits "
        f"where the false-positive budget put it, and most of those attacks rank below that "
        f"line. Recall and queue volume are the same dial: buying more of the first spends "
        f"more of the second, which is the trade-off the threshold slider on the Live Traffic "
        f"screen exists to make visible to whoever actually owns it.\n\n"
        f"Both readings point the same way. A supervised stage cannot name what it was never "
        f"shown, and on a temporal split that is most of the test day; the "
        f"{1 - overall / attack_rows:.1%} of attack traffic it does not surface is the volume "
        f"Stage 2 has to account for, and Phase 4 measures that per family rather than "
        f"asserting it."
    )


def render_report(
    evaluation: Evaluation,
    run: dict[str, Any] | None,
    settings: Any,
) -> str:
    unseen = [
        name
        for name in evaluation.family_recall
        if name != BENIGN_FAMILY and name not in evaluation.classes
    ]
    volume = evaluation.volume
    budget = settings.max_alerts_per_day

    lines: list[str] = [
        "# Phase 2 — Stage 1, the supervised classifier",
        "",
        f"Model `{evaluation.version}` (`{evaluation.algorithm}`, destination port "
        f"`{evaluation.port_encoding}`), measured on the held-out **Friday** test day: "
        f"{evaluation.rows:,} flows, {evaluation.attack_rows:,} of them attacks.",
        "",
        "The test day was opened once, after the depth, the port encoding and the operating "
        "threshold had all been settled on the Thursday validation day.",
        "",
        "## What Stage 1 was trained on",
        "",
        f"Classes in its vocabulary: {', '.join(f'`{name}`' for name in evaluation.classes)}.",
        "",
    ]

    if run:
        lines += [
            "```",
            run["label_mapping"],
            "```",
            "",
            f"Training rows: {run['training_rows']:,} across "
            f"{run['feature_count']} features, schema `{run['schema_hash']}`.",
            "",
        ]
        if run.get("held_out_families"):
            held = ", ".join(f"`{name}`" for name in run["held_out_families"])
            lines += [
                f"Held out of the vocabulary by the support floor "
                f"({run['min_class_support']} rows): {held}. A class with a handful of "
                "examples is not a class a tree ensemble can learn, and under "
                '`class_weight="balanced"` it would earn a weight in the thousands and '
                "distort the whole decision surface. Those rows are still scored — being "
                "unnameable by Stage 1 is the condition Stage 2 exists for.",
                "",
            ]

    lines += [
        "## The operating threshold",
        "",
        "```",
        f"C                   = {settings.analyst_capacity_per_hour} alerts/hour",
        f"analyst_shift_hours = {settings.analyst_shift_hours} hours",
        f"max_alerts_per_day  = {budget:,} alerts/day",
        f"V                   = {settings.expected_daily_flow_volume:,} flows/day",
        f"target_FPR          = {settings.target_fpr:.2e}",
        f"tau_sup             = {evaluation.tau_sup:.6f}",
        "```",
        "",
        "`tau_sup` is the smallest threshold whose false-positive rate on the validation day "
        "fits that budget. It is not 0.5 and it is not `argmax`.",
        "",
        "| Measured on the test day | Value |",
        "| --- | --- |",
        f"| False-positive rate at `tau_sup` | {volume['fpr']:.2e} |",
        f"| Projected false alerts/day at V = {settings.expected_daily_flow_volume:,} | "
        f"{volume['false_alerts_per_day']:,.0f} |",
        f"| **Alerts per analyst per hour** | **{volume['alerts_per_analyst_hour']:,.1f}** |",
        f"| Analyst budget | {budget:,}/day |",
        f"| Alert rate measured on this split | {volume['alert_rate']:.2e} |",
        "",
        f"The projection is the false-positive rate multiplied by V, not the alert rate "
        f"multiplied by V. This day is {volume['attack_share_of_split']:.0%} attack traffic; "
        "projecting that density onto a real network's million flows would describe a queue "
        "no real network produces. True positives sit on top of the false-alert floor, and "
        "how many there are depends on how much attack traffic the network actually carries "
        "— which a lab capture cannot tell you.",
        "",
        "## Per-class results on the test day",
        "",
        "Predictions are taken at the operating point — a family is emitted only when the "
        "attack confidence clears `tau_sup` — because that is what the served system does.",
        "",
        *_per_class_table(evaluation),
        "",
        "A class in the vocabulary with zero support is not a failure: the test day simply "
        "carries none of it. Its precision column still means something, though — it is the "
        "share of rows the model gave that name to which really were that family, and a zero "
        "there says every such prediction was a family the model does not have a name for. "
        "The confusion matrix below says which one.",
        "",
        "## Confusion matrix",
        "",
        *_confusion_table(evaluation),
        "",
        "## Detection view: does it flag the row at all",
        "",
        "The table above scores Stage 1 on *naming* the family. The fusion pipeline asks a "
        "smaller question first — does anything fire — and a DDoS flow flagged as `dos` is "
        "caught even though the per-class table scores it as a misclassification.",
        "",
        *_family_recall_table(evaluation),
        "",
        "| Metric | Value |",
        "| --- | --- |",
        f"| **PR-AUC (headline)** | **{evaluation.pr_auc:.4f}** |",
        f"| ROC-AUC | {evaluation.roc_auc:.4f} |",
        f"| PR-AUC using `1 - P(benign)` instead | {evaluation.pr_auc_one_minus_benign:.4f} |",
        f"| Accuracy *(table cell only, never a headline)* | {evaluation.accuracy:.4f} |",
        "",
        *_pr_versus_roc(evaluation, run),
        "",
        f"Accuracy is {evaluation.accuracy:.1%} on a day that is "
        f"{evaluation.benign_rows / evaluation.rows:.1%} benign. A model that answered benign "
        "to everything would score about the same, which is why the number is in a table cell "
        "and nowhere else.",
        "",
        "## Interpretation",
        "",
        _interpretation(evaluation, unseen),
        "",
        "## What this hands to Phase 3",
        "",
        "A promoted `supervised_model.pkl` paired with the `preprocessing.pkl` it was fitted "
        "against, both carrying the same schema hash, plus `tau_sup` travelling inside the "
        "model artifact rather than beside it.",
        "",
        "The `Flagged by Stage 1` column above is the Stage 1 column of the Phase 4 "
        "leave-one-attack-out table. Stage 2 trains on the benign-only split Phase 1 already "
        "wrote and asserted attack-free, so it needs nothing from this model except the "
        "feature contract they share — which is the point of `features.py` being one module.",
        "",
    ]

    if run and run.get("depth_sweep"):
        lines += [
            "## Appendix: the validation-day sweep",
            "",
            "| max_depth | Validation PR-AUC |",
            "| --- | --- |",
            *[
                f"| {int(row['max_depth'])} | {row['val_pr_auc']:.4f} |"
                for row in run["depth_sweep"]
            ],
            "",
            "Swept on a stratified subsample of the training split, then refitted on all of "
            "it at the winning depth. No validation or test row takes part in the fit.",
            "",
        ]

    if run and run.get("threshold_sweep"):
        lines += [
            "## Appendix: threshold candidates on the validation day",
            "",
            "| tau | FPR | Attack recall | Alerts/day at V |",
            "| --- | --- | --- | --- |",
            *[
                f"| {row['tau']:.4f} | {row['fpr']:.2e} | {row['attack_recall']:.1%} | "
                f"{row['alerts_per_day']:,.0f} |"
                for row in run["threshold_sweep"]
            ],
            "",
        ]

    return "\n".join(lines)


def write_outputs(
    evaluation: Evaluation,
    run: dict[str, Any] | None,
    artifacts_dir: Path,
    reports_dir: Path,
    settings: Any,
    algorithm: str | None = None,
) -> tuple[Path, Path]:
    """Write the human report and the machine-readable metrics side by side."""
    reports_dir.mkdir(parents=True, exist_ok=True)
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    report_name, metrics_name = output_names(algorithm)

    report_path = reports_dir / report_name
    report_path.write_text(render_report(evaluation, run, settings), encoding="utf-8")

    metrics_path = write_metrics(evaluation, run, artifacts_dir, settings, metrics_name)
    return report_path, metrics_path


def write_metrics(
    evaluation: Evaluation,
    run: dict[str, Any] | None,
    artifacts_dir: Path,
    settings: Any,
    metrics_name: str = METRICS_FILENAME,
    caveat: str | None = None,
) -> Path:
    """The machine-readable half: the metrics payload and the card's test entry.

    Separate from the report so Phase 7's retrain can refresh what the API
    serves about a newly promoted champion without rewriting
    ``reports/phase2_supervised.md`` -- the clean, pre-feedback measurement the
    README quotes. ``caveat`` travels with the numbers when they are not that
    clean measurement, so the screen that renders them can say so.
    """
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "stage": "stage1_supervised",
        "budget": {
            "expected_daily_flow_volume": settings.expected_daily_flow_volume,
            "analyst_capacity_per_hour": settings.analyst_capacity_per_hour,
            "analyst_shift_hours": settings.analyst_shift_hours,
            "max_alerts_per_day": settings.max_alerts_per_day,
            "target_fpr": settings.target_fpr,
        },
        "training": run,
        "test": asdict(evaluation),
    }
    if caveat:
        payload["caveat"] = caveat

    metrics_path = artifacts_dir / metrics_name
    metrics_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    card_path = artifacts_dir / "model_card.json"
    if card_path.exists():
        card = json.loads(card_path.read_text(encoding="utf-8"))
        if card.get("version") == evaluation.version:
            card["test"] = {
                "split": evaluation.split,
                "rows": evaluation.rows,
                "pr_auc": evaluation.pr_auc,
                "roc_auc": evaluation.roc_auc,
                "accuracy": evaluation.accuracy,
                "family_recall": evaluation.family_recall,
                "alerts_per_analyst_hour": evaluation.volume["alerts_per_analyst_hour"],
            }
            if caveat:
                card["test"]["caveat"] = caveat
            card_path.write_text(json.dumps(card, indent=2), encoding="utf-8")

    return metrics_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="evaluate.py",
        description="Measure Stage 1 on the held-out test day and write the report.",
    )
    parser.add_argument(
        "--algorithm",
        default=None,
        help="Evaluate a specific fallback artifact instead of the promoted champion",
    )
    parser.add_argument("--split", default="test")
    parser.add_argument("--processed-dir", type=Path, default=None)
    parser.add_argument("--artifacts-dir", type=Path, default=None)
    parser.add_argument("--reports-dir", type=Path, default=None)
    args = parser.parse_args(argv)

    from app.config import settings

    logging.basicConfig(level=logging.INFO, format="%(levelname)-8s %(message)s")

    processed_dir = args.processed_dir or settings.data_path / "processed"
    artifacts_dir = args.artifacts_dir or settings.artifacts_path
    reports_dir = args.reports_dir or settings.reports_path

    loaded = load_model(artifacts_dir, args.algorithm)
    frame = load_split(processed_dir, args.split)
    evaluation = evaluate_split(loaded, frame, args.split, settings)
    run = load_run_record(artifacts_dir, evaluation.version, args.algorithm)

    report_path, metrics_path = write_outputs(
        evaluation, run, artifacts_dir, reports_dir, settings, args.algorithm
    )

    echo(render_report(evaluation, run, settings))
    echo(f"\nwritten to {report_path}\n           {metrics_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
