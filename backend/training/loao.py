"""Phase 4 -- leave-one-attack-out, the headline result.

For each attack family F:

1. Remove all F rows from supervised training.
2. Retrain the supervised model.
3. Leave the autoencoder untouched -- it never saw any attack rows anyway.
4. Run the full fusion pipeline over a test set containing F.
5. Record what fraction of F was flagged, and by which stage.

Output table (committed as reports/loao.md):

    Held-out family | Caught by Stage 1 | Caught by Stage 2 | Total recall | Missed

The Stage 2 column is the headline number. Report the misses honestly: a table
with a real Missed column reads as credible engineering, a table of 99s reads
as a bug.
"""

from __future__ import annotations


def main() -> None:
    raise NotImplementedError("loao.py is implemented in Phase 4 (fusion and LOAO).")


if __name__ == "__main__":
    main()
