"""Phase 3 -- Model B, the anomaly detector.

Trains on benign rows only: Monday in full, plus benign rows from Tuesday and
Wednesday. Attack rows must never enter this training set, and that must be
asserted in code rather than merely intended -- it is what makes the
novel-attack claim real instead of a relabelled supervised model.

Architecture:

    input(d) -> 64 -> 32 -> 16 -> 32 -> 64 -> output(d)
    ReLU, MSE loss, Adam, early stopping on benign validation loss
    Dropout 0.1 in the encoder; batch norm helps convergence here

Score is the per-row mean squared reconstruction error.

tau_anom is the 99.5th percentile of reconstruction error on held-out benign
validation data. Persist the benign error distribution as histogram bins (not
raw rows) -- the dashboard threshold slider and drift detection both read it.

Baselines to run on the same split: IsolationForest, LOF and ECOD from PyOD.
They establish that the autoencoder earns its complexity, and if one of them
wins, that is a finding to report rather than hide.

Artifact: artifacts/autoencoder.pt (state dict)
"""

from __future__ import annotations


def main() -> None:
    raise NotImplementedError("train_autoencoder.py is implemented in Phase 3 (anomaly detector).")


if __name__ == "__main__":
    main()
