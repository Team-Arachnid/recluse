"""Phase 2/3 -- honest evaluation.

Emit: per-class precision, recall, F1 and support; the confusion matrix; PR and
ROC curves rendered side by side; PR-AUC as the headline; FPR at the chosen
threshold; and projected alerts per analyst per hour.

Accuracy may appear in a table but never as a headline number. On traffic that
is 99% benign, a model that always answers benign scores 99%.

The PR/ROC pair is deliberate: the gap between the two curves is the
explanation for why ROC-AUC flatters an imbalanced classifier.
"""

from __future__ import annotations


def main() -> None:
    raise NotImplementedError("evaluate.py is implemented in Phase 2 (supervised classifier).")


if __name__ == "__main__":
    main()
