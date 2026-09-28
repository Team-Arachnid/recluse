"""Estimator wrappers that have to survive a pickle round trip.

This module exists for one reason, and it is a reason worth stating in full
because the failure it prevents is confusing when you meet it cold.

``pickle`` does not store a class; it stores the class's ``__module__`` and
``__qualname__`` and looks the pair up again at load time. A class defined in a
module that was started with ``python -m training.train_supervised`` has
``__module__ == "__main__"``, because that is genuinely what the module was
called while it ran. The API process, and ``evaluate.py``, and anything else
that later loads the artifact, have a different ``__main__`` entirely -- so the
lookup lands in the wrong module and raises ``AttributeError: Can't get
attribute ... on <module '...'>``. Training succeeds, the file is written, and
nothing that reads it can open it.

Anything pickled into an artifact therefore lives here, in a module that is
imported by name and never run as a script.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np


@dataclass
class LightGBMClassifier:
    """A scikit-learn estimator face over a raw LightGBM ``Booster``.

    Serving, TreeSHAP and the evaluation all speak the estimator protocol --
    ``classes_`` and ``predict_proba`` -- so wrapping the Booster here means
    nothing downstream has to branch on which algorithm produced the champion.
    The class order travels with it, because that order *is* the column order
    of ``predict_proba`` and a column read as the wrong family is a silently
    mislabelled alert.

    The raw Booster is used rather than ``LGBMClassifier`` because training
    needs a custom evaluation function over a validation set whose labels are
    outside the training vocabulary; see ``train_supervised.fit_lightgbm``.
    """

    booster: Any
    classes: list[str]

    @property
    def classes_(self) -> np.ndarray:
        return np.array(self.classes, dtype=object)

    def predict_proba(self, x: np.ndarray) -> np.ndarray:
        return np.asarray(self.booster.predict(x, num_iteration=self.booster.best_iteration))

    def predict(self, x: np.ndarray) -> np.ndarray:
        return self.classes_[self.predict_proba(x).argmax(axis=1)]
