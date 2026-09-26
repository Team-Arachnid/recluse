"""Phase 1 -- temporal train/validation/test splits.

Day structure of CICIDS2017:

    Monday      benign only                                  autoencoder train
    Tuesday     FTP-Patator, SSH-Patator                     train
    Wednesday   DoS Hulk/GoldenEye/Slowloris/Slowhttptest,
                Heartbleed                                   train
    Thursday    web attacks (AM), infiltration (PM)          validation
    Friday      botnet, port scan, DDoS                      test

Temporal splits only. train_test_split(shuffle=True) leaks near-identical
duplicated flows across train and test and manufactures fake 99.9% scores.

Splitting by day also keeps whole attack families out of the test set, which
is what makes the Phase 4 leave-one-attack-out evaluation mean anything: a
family the classifier saw on Tuesday is not the evidence that it generalises.

Checkpoint for this phase: row counts per split per class, zero duplicate rows
shared across splits, and no NaN or Inf surviving.
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from training.console import echo
from training.features import DAY_COLUMN, LABEL_COLUMN, TIMESTAMP_COLUMN

logger = logging.getLogger(__name__)

BENIGN_LABEL = "BENIGN"

# Which day plays which role. Weekend days are absent from the capture.
DAY_ROLES: dict[str, str] = {
    "Monday": "benign_only",
    "Tuesday": "train",
    "Wednesday": "train",
    "Thursday": "val",
    "Friday": "test",
}

SPLIT_NAMES: tuple[str, ...] = ("train", "val", "test")


class AttackInBenignTrainingSet(RuntimeError):
    """Raised when a labelled attack reaches the Stage 2 training set.

    Stage 2's entire claim is that it never saw an attack label. A single
    contaminated row silently weakens that, so this is fatal rather than a
    warning.
    """


@dataclass
class SplitReport:
    """Row counts per split per class, plus what the split had to remove."""

    counts: dict[str, dict[str, int]] = field(default_factory=dict)
    benign_train_rows: int = 0
    cross_split_duplicates: int = 0
    unknown_days: dict[str, int] = field(default_factory=dict)

    def render(self) -> str:
        lines: list[str] = []
        for split in SPLIT_NAMES:
            rows = self.counts.get(split, {})
            total = sum(rows.values())
            lines.append(f"{split} ({total:,} rows)")
            for label, count in sorted(rows.items(), key=lambda kv: -kv[1]):
                lines.append(f"    {label:<28} {count:>10,}")
        lines.append(f"benign_train ({self.benign_train_rows:,} rows, attack-free)")
        lines.append(f"cross-split duplicates removed: {self.cross_split_duplicates:,}")
        if self.unknown_days:
            lines.append(f"rows on unexpected days: {self.unknown_days}")
        return "\n".join(lines)


@dataclass
class SplitResult:
    train: pd.DataFrame
    val: pd.DataFrame
    test: pd.DataFrame
    benign_train: pd.DataFrame
    report: SplitReport


def _counts(frame: pd.DataFrame) -> dict[str, int]:
    if frame.empty:
        return {}
    return {str(k): int(v) for k, v in frame[LABEL_COLUMN].value_counts().items()}


def _drop_cross_split_duplicates(
    ordered: dict[str, pd.DataFrame],
) -> tuple[dict[str, pd.DataFrame], int]:
    """Remove rows that already appeared in an earlier split.

    Done as one vectorised pass over the concatenated splits. The obvious
    alternative -- build a string key per row with
    `astype(str).agg(join, axis=1)` and test membership against a growing
    Python set -- takes ~24s per 200k rows and never finishes on the real
    2.5M-row dataset. `duplicated` compares in C.

    Rows are compared on their full contents, so the label participates -- two
    genuinely identical flows carrying different labels are not duplicates of
    each other and both survive. `duplicated(keep="first")` with the splits
    concatenated in train, val, test order is exactly the "earliest split
    wins" rule, and it also catches duplicates created *within* a split when
    the splitting key was dropped.
    """
    names = [name for name in SPLIT_NAMES if not ordered[name].empty]
    if not names:
        return ordered, 0

    combined = pd.concat([ordered[name] for name in names], keys=names, names=["_split", "_row"])
    duplicated = combined.duplicated(keep="first")
    if not duplicated.any():
        return ordered, 0

    kept = combined[~duplicated]
    result = dict(ordered)
    for name in names:
        result[name] = (
            kept.xs(name, level="_split")
            if name in kept.index.get_level_values("_split")
            else ordered[name].iloc[0:0]
        ).reset_index(drop=True)

    return result, int(duplicated.sum())


def split_frames(frame: pd.DataFrame) -> SplitResult:
    """Split cleaned flows temporally and build the benign-only training set."""
    has_timestamp = TIMESTAMP_COLUMN in frame.columns
    has_day = DAY_COLUMN in frame.columns
    if not has_timestamp and not has_day:
        raise ValueError(
            f"no {TIMESTAMP_COLUMN!r} and no {DAY_COLUMN!r} column: splitting is "
            "temporal and needs one of them. The MachineLearningCSV release ships "
            "no timestamp, so its capture day is recovered from the file name by "
            "clean.load_raw."
        )
    if LABEL_COLUMN not in frame.columns:
        raise ValueError(f"no {LABEL_COLUMN!r} column")

    frame = frame.copy()

    # A real timestamp is the finer truth where a file carries one; the capture
    # day is the fallback for the release that does not.
    if has_timestamp:
        frame[TIMESTAMP_COLUMN] = pd.to_datetime(frame[TIMESTAMP_COLUMN], errors="coerce")
        frame = frame.dropna(subset=[TIMESTAMP_COLUMN])
        day = frame[TIMESTAMP_COLUMN].dt.day_name()
    else:
        frame = frame.dropna(subset=[DAY_COLUMN])
        day = frame[DAY_COLUMN].astype(str)
    report = SplitReport()

    unknown = day[~day.isin(DAY_ROLES)]
    if not unknown.empty:
        report.unknown_days = {str(k): int(v) for k, v in unknown.value_counts().items()}
        logger.warning("rows on unexpected days: %s", report.unknown_days)

    role = day.map(DAY_ROLES)

    # Monday is benign-only and is reserved in full for Stage 2, so it is not
    # a supervised split. Tuesday and Wednesday contribute their benign rows
    # to Stage 2 as well, while their attack rows train Stage 1.
    benign_mask = frame[LABEL_COLUMN] == BENIGN_LABEL
    benign_train = frame[(role == "benign_only") | (benign_mask & (role == "train"))]

    contamination = benign_train[benign_train[LABEL_COLUMN] != BENIGN_LABEL]
    if not contamination.empty:
        found = sorted(set(contamination[LABEL_COLUMN]))
        raise AttackInBenignTrainingSet(
            f"{len(contamination)} attack row(s) reached the Stage 2 training set: "
            f"{found}. Monday is supposed to be benign-only; check the source files."
        )

    splits = {name: frame[role == name] for name in SPLIT_NAMES}

    # The splitting keys go now: neither is ever a model input.
    keys_to_drop = [c for c in (TIMESTAMP_COLUMN, DAY_COLUMN) if c in frame.columns]

    def finalise(part: pd.DataFrame) -> pd.DataFrame:
        return part.drop(columns=keys_to_drop).reset_index(drop=True)

    ordered = {name: finalise(splits[name]) for name in SPLIT_NAMES}
    benign_train = finalise(benign_train)

    # Dropping the splitting key can make rows from different days identical.
    # Keep the earliest split's copy and drop the rest, so nothing spans two.
    ordered, report.cross_split_duplicates = _drop_cross_split_duplicates(ordered)

    report.counts = {name: _counts(ordered[name]) for name in SPLIT_NAMES}
    report.benign_train_rows = len(benign_train)

    return SplitResult(
        train=ordered["train"],
        val=ordered["val"],
        test=ordered["test"],
        benign_train=benign_train,
        report=report,
    )


def load_interim(interim_dir: Path) -> pd.DataFrame:
    """Concatenate every cleaned Parquet file and finish the cleaning globally.

    Per-file cleaning defers the zero-variance decision, because a column that
    is constant on one capture day can vary on another -- deciding per file
    would give the files different column sets and concatenating those would
    inject NaN. The assessment happens here instead, once, across the whole
    dataset.
    """
    from training.clean import zero_variance_columns

    sources = sorted(interim_dir.glob("*.parquet"))
    if not sources:
        raise SystemExit(
            f"no Parquet files in {interim_dir}. Run clean.py first (make data-clean)."
        )
    frames = [pd.read_parquet(path) for path in sources]
    combined = pd.concat(frames, ignore_index=True)

    dropped = zero_variance_columns(combined)
    if dropped:
        logger.info("dropping %d globally zero-variance column(s): %s", len(dropped), dropped)
        combined = combined.drop(columns=dropped)

    return combined


def write_splits(result: SplitResult, processed_dir: Path) -> None:
    processed_dir.mkdir(parents=True, exist_ok=True)
    for name in SPLIT_NAMES:
        getattr(result, name).to_parquet(processed_dir / f"{name}.parquet", index=False)
    result.benign_train.to_parquet(processed_dir / "benign_train.parquet", index=False)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="split.py", description="Split cleaned flows temporally by capture day."
    )
    parser.add_argument("--interim-dir", type=Path, default=None)
    parser.add_argument("--processed-dir", type=Path, default=None)
    args = parser.parse_args(argv)

    from app.config import settings

    logging.basicConfig(level=logging.INFO, format="%(levelname)-8s %(message)s")

    interim_dir = args.interim_dir or settings.data_path / "interim"
    processed_dir = args.processed_dir or settings.data_path / "processed"

    frame = load_interim(interim_dir)
    result = split_frames(frame)
    write_splits(result, processed_dir)

    echo(result.report.render())
    return 0


if __name__ == "__main__":
    sys.exit(main())
