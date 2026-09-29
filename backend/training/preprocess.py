"""Phase 1 -- fit the preprocessing bundle and drive the phase end to end.

Cleaning and splitting each own a file. This module owns the last step, and
the one that the serving path depends on: fitting the `RobustScaler` on the
training split alone and persisting it together with the feature order, the
dropped columns, the port encoding and the schema hash.

All five travel in one pickle because no subset of them reproduces the
training-time matrix. `app/inference.py` recomputes the hash at startup and
refuses to serve on a mismatch, which turns train/serve skew from a silent
scoring bug into a refused boot.

    python -m training.preprocess              # fit from data/processed/train.parquet
    python -m training.preprocess --all        # clean, split and fit in one run
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from training.clean import CleaningReport, clean_frame, load_raw
from training.console import echo
from training.features import (
    DEFAULT_TOP_PORTS,
    PORT_ENCODING_RAW,
    PreprocessingBundle,
    fit_preprocessing,
    save_preprocessing_bundle,
)
from training.split import SplitReport, split_frames, write_splits

logger = logging.getLogger(__name__)

BUNDLE_FILENAME = "preprocessing.pkl"


@dataclass
class PipelineResult:
    """Everything the phase checkpoint needs to report."""

    processed_dir: Path
    artifacts_dir: Path
    cleaning_report: CleaningReport
    split_report: SplitReport
    bundle: PreprocessingBundle

    def render(self) -> str:
        return "\n".join(
            [
                "CLEANING",
                self.cleaning_report.render(),
                "",
                "SPLITS",
                self.split_report.render(),
                "",
                "PREPROCESSING BUNDLE",
                f"features        {len(self.bundle['feature_order'])}",
                f"dropped columns {len(self.bundle['dropped_columns'])}: "
                f"{', '.join(self.bundle['dropped_columns']) or 'none'}",
                f"port encoding   {self.bundle['port_encoding']['strategy']}",
                f"schema hash     {self.bundle['schema_hash']}",
                f"written to      {self.artifacts_dir / BUNDLE_FILENAME}",
            ]
        )


def _warn_if_it_orphans_a_champion(bundle: PreprocessingBundle, artifacts_dir: Path) -> None:
    """Say so when this refit leaves a trained model without its scaler.

    ``preprocessing.pkl`` has two authors: this module writes it from the
    training split under the encoding it was asked for, and Phase 2 overwrites
    it with whichever bundle the champion was actually fitted against. Running
    this command after a model exists therefore desynchronises the canonical
    pair, and the API refuses to start on the mismatch -- correctly, but a long
    way from the command that caused it.

    The next training run repairs it (``train_supervised.restore_champion``).
    Saying it here means nobody has to work that out from a schema hash.

    The card is read inline rather than through ``train_supervised`` so that
    Phase 1 keeps no import on Phase 2.
    """
    card_path = artifacts_dir / "model_card.json"
    if not card_path.exists():
        return
    try:
        card = json.loads(card_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return

    champion_hash = card.get("schema_hash")
    if not champion_hash or champion_hash == bundle["schema_hash"]:
        return

    logger.warning(
        "this bundle (%s, %s) does not match champion %s (%s), so %s and "
        "supervised_model.pkl are now a mismatched pair and the API will refuse "
        "to start. Re-run the Phase 2 training to repair it (make train).",
        bundle["port_encoding"].get("strategy"),
        bundle["schema_hash"],
        card.get("version"),
        champion_hash,
        BUNDLE_FILENAME,
    )


def fit_and_save(
    train: pd.DataFrame,
    artifacts_dir: Path,
    port_encoding: str = PORT_ENCODING_RAW,
    top_n: int = DEFAULT_TOP_PORTS,
) -> tuple[PreprocessingBundle, Path]:
    """Fit on the training split only and persist the bundle."""
    if train.empty:
        raise ValueError("cannot fit preprocessing on an empty training split")

    bundle = fit_preprocessing(train, port_encoding=port_encoding, top_n=top_n)
    path = save_preprocessing_bundle(bundle, artifacts_dir / BUNDLE_FILENAME)
    logger.info(
        "bundle written to %s (%d features, %s)",
        path,
        len(bundle["feature_order"]),
        bundle["schema_hash"],
    )
    _warn_if_it_orphans_a_champion(bundle, artifacts_dir)
    return bundle, path


def run_pipeline(
    frame: pd.DataFrame,
    processed_dir: Path,
    artifacts_dir: Path,
    port_encoding: str = PORT_ENCODING_RAW,
    top_n: int = DEFAULT_TOP_PORTS,
) -> PipelineResult:
    """Clean, split, fit and write, from one raw frame."""
    cleaned, cleaning_report = clean_frame(frame)
    split = split_frames(cleaned)
    write_splits(split, processed_dir)
    bundle, _ = fit_and_save(split.train, artifacts_dir, port_encoding=port_encoding, top_n=top_n)
    return PipelineResult(
        processed_dir=processed_dir,
        artifacts_dir=artifacts_dir,
        cleaning_report=cleaning_report,
        split_report=split.report,
        bundle=bundle,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="preprocess.py",
        description="Fit and persist the Phase 1 preprocessing bundle.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Clean and split first, instead of reading data/processed/train.parquet",
    )
    parser.add_argument(
        "--port-encoding",
        default=PORT_ENCODING_RAW,
        help="raw or bucketed (the Phase 2 ablation runs both)",
    )
    parser.add_argument("--top-n", type=int, default=DEFAULT_TOP_PORTS)
    parser.add_argument("--raw-dir", type=Path, default=None)
    parser.add_argument("--processed-dir", type=Path, default=None)
    parser.add_argument("--artifacts-dir", type=Path, default=None)
    args = parser.parse_args(argv)

    from app.config import settings

    logging.basicConfig(level=logging.INFO, format="%(levelname)-8s %(message)s")

    raw_dir = args.raw_dir or settings.data_path / "raw"
    processed_dir = args.processed_dir or settings.data_path / "processed"
    artifacts_dir = args.artifacts_dir or settings.artifacts_path

    if args.all:
        result = run_pipeline(
            load_raw(raw_dir),
            processed_dir=processed_dir,
            artifacts_dir=artifacts_dir,
            port_encoding=args.port_encoding,
            top_n=args.top_n,
        )
        echo(result.render())
        return 0

    train_path = processed_dir / "train.parquet"
    if not train_path.exists():
        raise SystemExit(
            f"{train_path} not found. Run the split first, or use --all to run "
            "cleaning, splitting and fitting in one go."
        )

    bundle, path = fit_and_save(
        pd.read_parquet(train_path),
        artifacts_dir,
        port_encoding=args.port_encoding,
        top_n=args.top_n,
    )
    echo(f"features    {len(bundle['feature_order'])}")
    echo(f"encoding    {bundle['port_encoding']['strategy']}")
    echo(f"schema hash {bundle['schema_hash']}")
    echo(f"written to  {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
