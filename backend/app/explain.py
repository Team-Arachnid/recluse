"""Phase 5 -- per-alert explanation.

Two explainers, one per stage, chosen deliberately:

* Stage 1 (tree model): TreeSHAP, top-5 contributing features.
* Stage 2 (autoencoder): per-feature reconstruction error, top-5. The model
  hands this over for free -- the features it failed hardest to reconstruct
  are precisely why the row looks anomalous:

      per_feature_error = (x - x_hat) ** 2
      top_contributors  = argsort(per_feature_error)[-5:]

  KernelSHAP on a neural net is slow, approximate, and buys nothing here.

Every alert carries an explanation. An alert with a score and no reason is an
alert an analyst ignores.
"""

from __future__ import annotations

from typing import Any


def explain_supervised(*_: Any, **__: Any) -> dict[str, Any]:
    raise NotImplementedError("TreeSHAP explanations arrive in Phase 5 (backend API).")


def explain_anomaly(*_: Any, **__: Any) -> dict[str, Any]:
    raise NotImplementedError("Reconstruction-error explanations arrive in Phase 5.")


def narrate(*_: Any, **__: Any) -> str:
    """Template an explanation into English via a per-feature phrase map.

    Target register: "2,400 distinct destination ports contacted in 8 seconds
    from a single source."
    """
    raise NotImplementedError("Narration arrives in Phase 5 (backend API).")
