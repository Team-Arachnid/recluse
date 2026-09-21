"""Model bundle loading and scoring entry point.

Loading happens exactly once, in the FastAPI lifespan, and the result lives on
``app.state``. Nothing here ever calls ``.fit()`` -- training is offline batch
(``backend/training/``) and this module only consumes its artifacts.

Phase 0 ships the loader and the fail-fast schema check. The scoring path
itself arrives with the models: Stage 1 in Phase 2, Stage 2 in Phase 3, fusion
in Phase 4.
"""

from __future__ import annotations

import json
import logging
import pickle
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from training.features import compute_schema_hash

logger = logging.getLogger(__name__)

UNLOADED_VERSION = "unloaded"

# Artifact filenames, as written by backend/training/.
PREPROCESSING_FILE = "preprocessing.pkl"
SUPERVISED_FILE = "supervised_model.pkl"
AUTOENCODER_FILE = "autoencoder.pt"
MODEL_CARD_FILE = "model_card.json"


class SchemaHashMismatch(RuntimeError):
    """Raised when an artifact bundle disagrees with the feature contract.

    This is deliberately fatal at startup. Feeding columns to a model in a
    different order than it was trained on produces garbage scores without
    raising anything -- train/serve skew is silent, so the check is loud.
    """


@dataclass
class ModelBundle:
    """Everything the serving path needs, loaded from disk once at startup."""

    artifacts_dir: Path

    version: str = UNLOADED_VERSION
    schema_hash: str | None = None
    feature_order: list[str] = field(default_factory=list)
    dropped_columns: list[str] = field(default_factory=list)
    port_encoding: dict[str, Any] = field(default_factory=dict)

    scaler: Any | None = None
    supervised: Any | None = None
    # Stage 2 weights are held as a state dict; Phase 3 owns the nn.Module
    # definition and reconstructs the model from it.
    autoencoder_state: dict[str, Any] | None = None
    autoencoder: Any | None = None

    tau_sup: float | None = None
    tau_anom: float | None = None

    benign_error_histogram: dict[str, Any] | None = None
    model_card: dict[str, Any] = field(default_factory=dict)

    @property
    def is_loaded(self) -> bool:
        """True once at least one trained model is resident in memory."""
        return self.supervised is not None or self.autoencoder_state is not None

    @property
    def stage1_ready(self) -> bool:
        return self.supervised is not None and self.tau_sup is not None

    @property
    def stage2_ready(self) -> bool:
        return self.autoencoder_state is not None and self.tau_anom is not None

    @property
    def status(self) -> str:
        """Health status.

        Phase 0 has no artifacts at all, which is expected rather than broken,
        so the service reports ``ok`` with ``model_version == "unloaded"``.
        ``degraded`` means a bundle was found but could not be made usable.
        """
        if self._degraded:
            return "degraded"
        return "ok"

    _degraded: bool = False

    # -- loading ---------------------------------------------------------
    def load(self) -> ModelBundle:
        """Populate the bundle from ``artifacts_dir``.

        Absent artifacts are not an error in Phase 0/1 -- the API is expected
        to serve health and the dashboard shell before any model exists. A
        *present but inconsistent* bundle is an error, and raises.
        """
        preprocessing_path = self.artifacts_dir / PREPROCESSING_FILE
        if not preprocessing_path.exists():
            logger.info(
                "no artifact bundle in %s -- serving with model_version=%s "
                "(expected until Phase 2 trains a model)",
                self.artifacts_dir,
                UNLOADED_VERSION,
            )
            return self

        with preprocessing_path.open("rb") as handle:
            preprocessing: dict[str, Any] = pickle.load(handle)

        self.scaler = preprocessing.get("scaler")
        self.feature_order = list(preprocessing.get("feature_order") or [])
        self.dropped_columns = list(preprocessing.get("dropped_columns") or [])
        self.port_encoding = dict(preprocessing.get("port_encoding") or {})
        self.schema_hash = preprocessing.get("schema_hash")

        self._verify_schema_hash()
        self._load_model_card()
        self._load_models()
        return self

    def _verify_schema_hash(self) -> None:
        """Fail fast when the persisted hash disagrees with the feature order."""
        if not self.schema_hash:
            raise SchemaHashMismatch(
                f"{PREPROCESSING_FILE} carries no schema_hash; refusing to serve. "
                "Re-run the Phase 1 pipeline so the bundle is written with one."
            )
        recomputed = compute_schema_hash(self.feature_order)
        if recomputed != self.schema_hash:
            raise SchemaHashMismatch(
                "artifact bundle schema hash does not match its feature order: "
                f"bundle={self.schema_hash} recomputed={recomputed}. "
                "The bundle is inconsistent -- retrain rather than serve it."
            )
        logger.info(
            "schema hash verified: %s (%d features)",
            self.schema_hash,
            len(self.feature_order),
        )

    def _load_model_card(self) -> None:
        card_path = self.artifacts_dir / MODEL_CARD_FILE
        if not card_path.exists():
            return
        self.model_card = json.loads(card_path.read_text(encoding="utf-8"))
        self.version = str(self.model_card.get("version", UNLOADED_VERSION))
        thresholds = self.model_card.get("thresholds", {})
        self.tau_sup = thresholds.get("tau_sup")
        self.tau_anom = thresholds.get("tau_anom")
        card_hash = self.model_card.get("schema_hash")
        if card_hash and card_hash != self.schema_hash:
            raise SchemaHashMismatch(
                f"{MODEL_CARD_FILE} schema hash {card_hash} disagrees with "
                f"{PREPROCESSING_FILE} schema hash {self.schema_hash}."
            )

    def _load_models(self) -> None:
        """Load Stage 1 / Stage 2 weights if their files are present.

        Trust boundary: everything under ``artifacts_dir`` is produced locally
        by ``backend/training/`` and is gitignored. Nothing user-supplied or
        downloaded is ever unpickled here -- the API has no artifact-upload
        path, and Phase 9's pcap ingestion feeds ``features.py``, not this.
        Torch weights are still restricted to ``weights_only=True`` so the
        Stage 2 file is data rather than code.

        Implemented per phase: Phase 2 adds the supervised branch, Phase 3 the
        autoencoder. Until then a bundle may legitimately contain only
        preprocessing.
        """
        supervised_path = self.artifacts_dir / SUPERVISED_FILE
        if supervised_path.exists():
            # scikit-learn / LightGBM estimators have no non-pickle
            # round-trip; this is a first-party file written by
            # training/train_supervised.py.
            with supervised_path.open("rb") as handle:
                self.supervised = pickle.load(handle)  # noqa: S301 - first-party artifact
            logger.info("loaded Stage 1 model from %s", supervised_path)

        autoencoder_path = self.artifacts_dir / AUTOENCODER_FILE
        if autoencoder_path.exists():
            # Imported lazily: torch is a heavy import and Phase 0 startup
            # should not pay for it when there is nothing to load.
            import torch

            self.autoencoder_state = torch.load(
                autoencoder_path, map_location="cpu", weights_only=True
            )
            logger.info("loaded Stage 2 weights from %s", autoencoder_path)

    # -- scoring ---------------------------------------------------------
    def score_batch(self, flows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Score a batch of flows through the two-stage fusion pipeline.

        Batch-only by design: per-row ``predict()`` in the replay loop is
        roughly 50x slower and makes the live demo stutter.

        Implemented in Phase 4 (fusion), on top of Phase 2 and Phase 3.
        """
        raise NotImplementedError(
            "score_batch arrives in Phase 4 (fusion); Stage 1 lands in Phase 2 "
            "and Stage 2 in Phase 3."
        )


def load_bundle(artifacts_dir: Path) -> ModelBundle:
    """Build and load the process-wide model bundle."""
    return ModelBundle(artifacts_dir=artifacts_dir).load()
