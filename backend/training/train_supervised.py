"""Phase 2 -- Model A, the supervised classifier.

Order of work:

1. RandomForestClassifier baseline (n_estimators=300, max_depth tuned against
   the validation day, multi-class). Commit it as a running baseline before
   touching anything else.
2. LightGBM as a swap-in upgrade once the baseline runs end to end, keeping
   the RandomForest artifact as a fallback.

Rules:

* class_weight balanced or explicit per-class weights. No SMOTE -- synthetic
  interpolation between flow records invents packets that could not exist on a
  real network. If demonstrated at all, it is an ablation that underperforms.
* Early stopping against the validation day.
* Classes: benign, dos, ddos, brute_force, port_scan, web_attack, botnet,
  infiltration. Rare sub-families collapse; log the mapping.
* tau_sup comes from the false-positive budget, never from argmax:

      max_alerts_per_day = analyst_capacity_per_hour * analyst_shift_hours
      target_fpr         = max_alerts_per_day / expected_daily_flow_volume
      tau_sup            = smallest threshold where FPR(tau) <= target_fpr

  All three inputs are configured in .env (see Settings.target_fpr) and are
  stated in the README.
* Persist tau_sup into the artifact bundle.

Artifact: artifacts/supervised_model.pkl
"""

from __future__ import annotations


def main() -> None:
    raise NotImplementedError(
        "train_supervised.py is implemented in Phase 2 (supervised classifier)."
    )


if __name__ == "__main__":
    main()
