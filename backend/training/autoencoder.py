"""Model B's architecture, and the scoring that reads out of it.

Separate from ``train_autoencoder.py`` because two processes need the
architecture and only one of them is a training run. ``autoencoder.pt`` is a
bare state dict -- weights and nothing else, which is what lets the API read it
with ``weights_only=True`` and treat the file as data rather than as code -- so
whoever loads those weights has to already know the shape to pour them into.
Importing that shape out of a training CLI would make the API process pay for
argparse, pandas and the rest of the training stack to rebuild a network of
seventeen thousand parameters.

The shape is not written down twice, though. ``geometry`` reads the widths back
out of the tensors themselves, so what the model card records about the
architecture is a *description* of the artifact rather than a second source of
truth that could quietly disagree with it.

    input(d) -> 64 -> 32 -> 16 -> 32 -> 64 -> output(d)

The bottleneck is the mechanism, not a detail of it. Sixteen numbers is all the
network may pass through the middle, so it has to spend them on the structure
benign traffic actually has. A flow that does not share that structure cannot
be squeezed through and comes back distorted, and the size of the distortion is
the score.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import numpy as np
import torch
from torch import nn

# Encoder widths. The decoder mirrors all but the last, which is the bottleneck.
ENCODER_WIDTHS: tuple[int, ...] = (64, 32, 16)

# Dropout sits in the encoder only, and not on the bottleneck itself: zeroing a
# tenth of sixteen code units is a far heavier perturbation than a tenth of
# sixty-four, and the bottleneck is already the regulariser this network is
# built around.
ENCODER_DROPOUT = 0.1

# Rows per forward pass when scoring. Reconstruction is cheap; materialising a
# million-row tensor is not.
SCORE_BATCH_ROWS = 16_384

# The bound `prepare_input` clips the compressed matrix at, in log units. Six
# means "beyond about four hundred interquartile ranges from the median, stop
# distinguishing". Chosen on the validation day over three seeds, because the
# spread within one bound is comparable to the gaps between bounds and a single
# seed is not enough to separate them -- see `train_autoencoder.py
# --input-ablation` and reports/input_ablation.md.
INPUT_CLIP = 6.0


def prepare_input(matrix: np.ndarray, clip: float = INPUT_CLIP) -> np.ndarray:
    """Compress the shared feature matrix into a range MSE can reason about.

    This is the one transform that belongs to Stage 2 rather than to the shared
    preprocessing bundle, and it exists because of a measured pathology rather
    than a preference.

    ``RobustScaler`` divides each column by its interquartile range, and when a
    column's IQR is zero scikit-learn leaves the divisor at 1.0 -- the column
    passes through essentially unscaled. CICIDS2017 has such columns: over
    three quarters of benign flows report ``idle_std`` of exactly zero, so its
    IQR is zero, while the flows that do idle report values up to 7.6e7
    microseconds. Squared, one column then accounts for **93.9%** of the total
    magnitude the loss sees, with ``active_std`` taking another 4.0%. An MSE
    objective under those conditions is not a reconstruction objective at all:
    the gradient is one column's, eighty-nine features are invisible, and the
    resulting score ranks attack traffic *below* benign traffic. That is a
    measurement rather than a worry -- the first run of this phase scored
    ROC-AUC 0.2337 on the validation-day arena, where the three classical
    baselines scored 0.71 to 0.86 on the same rows, and 0.4676 on the test day.

    ``sign(x) * log1p(|x|)`` fixes the magnitudes without discarding the
    ordering -- a flow ten thousand IQRs out still scores above one a hundred
    IQRs out -- and the clip bounds what is left of the tail. Both halves are
    monotonic in ``|x|``, so nothing about "further from normal is more
    anomalous" is lost.

    The transform is **not idempotent**, so it is applied in exactly two places:
    here, on the way into a forward pass, and once at the top of
    ``fit_autoencoder``. Nothing else holds a prepared matrix -- ``BenignData``
    carries shared-space rows -- and a test asserts the two paths agree.
    """
    compressed = np.sign(matrix) * np.log1p(np.abs(matrix))
    return np.clip(compressed, -clip, clip).astype("float32")


class Autoencoder(nn.Module):
    """Benign-only reconstruction network: ``d -> 64 -> 32 -> 16 -> 32 -> 64 -> d``.

    Batch norm after every hidden linear layer, because at this depth it is
    what makes the run converge in tens of epochs rather than hundreds. ReLU
    throughout the hidden layers.

    The output layer is a bare ``Linear`` with no activation, and that is a
    decision rather than an omission. The features arrive signed -- scaled by a
    ``RobustScaler`` and then compressed by ``prepare_input`` -- so squashing
    the output through ReLU would make every negative target unreachable and
    put a floor under the reconstruction error of every row, benign ones
    included. The score has to be able to reach zero for traffic the model
    knows well.

    The network takes ``prepare_input`` output, never a raw scaled matrix. The
    scoring functions below apply it themselves, so a caller only ever hands
    over the shared feature matrix.
    """

    def __init__(
        self,
        input_dim: int,
        widths: tuple[int, ...] = ENCODER_WIDTHS,
        dropout: float = ENCODER_DROPOUT,
    ) -> None:
        super().__init__()
        if input_dim <= 0:
            raise ValueError(f"input_dim must be positive, got {input_dim}")
        if len(widths) < 2:
            raise ValueError(f"need at least one hidden layer and a bottleneck, got {widths}")

        self.input_dim = int(input_dim)
        self.widths = tuple(int(width) for width in widths)
        self.dropout = float(dropout)

        encoder: list[nn.Module] = []
        previous = self.input_dim
        for index, width in enumerate(self.widths):
            encoder += [nn.Linear(previous, width), nn.BatchNorm1d(width), nn.ReLU()]
            if self.dropout and index < len(self.widths) - 1:
                encoder.append(nn.Dropout(self.dropout))
            previous = width
        self.encoder = nn.Sequential(*encoder)

        decoder: list[nn.Module] = []
        for width in reversed(self.widths[:-1]):
            decoder += [nn.Linear(previous, width), nn.BatchNorm1d(width), nn.ReLU()]
            previous = width
        decoder.append(nn.Linear(previous, self.input_dim))
        self.decoder = nn.Sequential(*decoder)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.decoder(self.encoder(x))

    @property
    def bottleneck(self) -> int:
        return self.widths[-1]

    def architecture(self) -> str:
        """``input(92) -> 64 -> 32 -> 16 -> 32 -> 64 -> output(92)``, for the record."""
        mirrored = list(self.widths) + list(reversed(self.widths[:-1]))
        return " -> ".join(
            [
                f"input({self.input_dim})",
                *(str(width) for width in mirrored),
                f"output({self.input_dim})",
            ]
        )

    # -- artifact I/O ----------------------------------------------------
    @staticmethod
    def geometry(state: dict[str, Any]) -> tuple[int, tuple[int, ...]]:
        """Recover ``(input_dim, widths)`` from a state dict's own tensors.

        The encoder's linear weights are the only 2-D tensors under the
        ``encoder.`` prefix, and a ``Linear``'s weight is ``(out, in)``, so the
        first one carries the feature count and each carries its own width.
        Reading the geometry rather than being told it is what keeps the
        architecture in the model card honest: it can only ever be a readout of
        the file that shipped.
        """
        widths = [
            (str(name), tuple(int(dim) for dim in tensor.shape))
            for name, tensor in state.items()
            if name.startswith("encoder.")
            and name.endswith(".weight")
            and getattr(tensor, "ndim", 0) == 2
        ]
        if not widths:
            raise ValueError(
                "state dict carries no 2-D encoder weights, so it was not written "
                f"by this class. Keys: {sorted(state)[:8]}"
            )
        return widths[0][1][1], tuple(shape[0] for _, shape in widths)

    @classmethod
    def from_state_dict(cls, state: dict[str, Any]) -> Autoencoder:
        """Rebuild a trained network from ``autoencoder.pt``, ready to score.

        Returned in ``eval()`` mode. That is not tidiness: in training mode
        batch norm normalises against the batch and dropout is live, so the
        same flow scored in two different batches would get two different
        answers.
        """
        input_dim, widths = cls.geometry(state)
        model = cls(input_dim=input_dim, widths=widths)
        model.load_state_dict(state)
        model.eval()
        return model


# ---------------------------------------------------------------------------
# Scoring
#
# The score is the loss. Nothing here converts a reconstruction into a
# probability, because there is nothing to calibrate it against -- Stage 2
# never sees a label, and `tau_anom` is a percentile of this quantity measured
# on benign traffic alone.
# ---------------------------------------------------------------------------


def _batches(
    model: Autoencoder, matrix: np.ndarray, batch_size: int, clip: float = INPUT_CLIP
) -> Iterator[tuple[torch.Tensor, torch.Tensor]]:
    """Yield ``(input, reconstruction)`` pairs, in eval mode and without grad.

    ``matrix`` is the shared feature matrix. ``prepare_input`` is applied here,
    per batch, so that every scoring path in the project gets it and no caller
    has to remember to.

    ``clip`` exists so the ablation that chose ``INPUT_CLIP`` can vary the bound
    without a second copy of the scoring path. Nothing under ``app/`` passes it;
    the default is the shipped bound, and the value a model was actually fitted
    under travels in its run record.
    """
    was_training = model.training
    model.eval()
    try:
        with torch.no_grad():
            for start in range(0, len(matrix), batch_size):
                chunk = torch.from_numpy(
                    np.ascontiguousarray(
                        prepare_input(matrix[start : start + batch_size], clip=clip)
                    )
                )
                yield chunk, model(chunk)
    finally:
        model.train(was_training)


def reconstruction_error(
    model: Autoencoder,
    matrix: np.ndarray,
    batch_size: int = SCORE_BATCH_ROWS,
    clip: float = INPUT_CLIP,
) -> np.ndarray:
    """Per-row mean squared reconstruction error -- the Stage 2 score itself."""
    if len(matrix) == 0:
        return np.empty(0, dtype="float64")
    parts = [
        ((out - inp) ** 2).mean(dim=1).numpy()
        for inp, out in _batches(model, matrix, batch_size, clip)
    ]
    return np.concatenate(parts).astype("float64")


def per_feature_error(
    model: Autoencoder, matrix: np.ndarray, batch_size: int = SCORE_BATCH_ROWS
) -> np.ndarray:
    """``(x - x_hat) ** 2`` per row per feature -- the Stage 2 explanation.

    This is the whole reason Stage 2 needs no SHAP. The quantity is already
    computed as part of the score, and the features the network failed hardest
    to reconstruct are precisely why the row looks unlike normal traffic. An
    approximated attribution would be slower and less faithful at once.

    Returns an ``(n, d)`` array, so it is for an alert or a sample rather than
    for a whole capture day.
    """
    if len(matrix) == 0:
        return np.empty((0, model.input_dim), dtype="float64")
    parts = [((out - inp) ** 2).numpy() for inp, out in _batches(model, matrix, batch_size)]
    return np.vstack(parts).astype("float64")


def top_contributors(
    errors: np.ndarray, feature_order: list[str], k: int = 5
) -> list[dict[str, float | str]]:
    """The ``k`` worst-reconstructed features of one row (or of a row average).

    Share is of that row's total squared error, which is what makes the list
    readable: "this one feature is 60% of why the row scored" says more than a
    raw magnitude nobody has a reference for.
    """
    errors = np.asarray(errors, dtype="float64").ravel()
    if errors.size != len(feature_order):
        raise ValueError(
            f"{errors.size} errors against {len(feature_order)} feature names; "
            "the pair must come from the same bundle"
        )
    total = float(errors.sum()) or 1.0
    order = np.argsort(errors)[::-1][:k]
    return [
        {
            "feature": feature_order[index],
            "error": float(errors[index]),
            "share": float(errors[index] / total),
        }
        for index in order
    ]
