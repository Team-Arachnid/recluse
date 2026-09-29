# Stage 2 input-transform ablation

Stage 2 reads the shared feature matrix through `sign(x) * log1p(|x|)`, clipped. This is where the clip bound comes from.

## Why there is a transform at all

`RobustScaler` divides each column by its interquartile range, and when a column's IQR is zero scikit-learn leaves the divisor at 1.0 — the column passes through essentially unscaled. CICIDS2017 has such columns. Over three quarters of benign flows report `idle_std` of exactly zero, so its IQR is zero, while the flows that do idle report values up to 7.6 × 10⁷ microseconds.

Squared, that one column accounted for **93.9%** of the total magnitude an MSE loss could see, with `active_std` taking another 4.0% and the top three together 98.7%. Under those conditions MSE is not a reconstruction objective: the gradient belongs to one column, eighty-nine features are invisible to it, and the score that comes out is a proxy for *does this flow have a large idle gap* — which benign traffic has more of than attack traffic does. The first run of Phase 3 measured **ROC-AUC 0.2337** on the shared validation-day arena — worse than a coin — where the three classical baselines scored 0.71 to 0.86 on the same rows, and 0.4676 on the test day. Those are historical figures: the current code has no path that reproduces them, which is why the table below starts from `log1p` rather than from the raw matrix.

`log1p` compresses the magnitudes without discarding the ordering — a flow ten thousand IQRs out still scores above one a hundred IQRs out — and the clip bounds what is left of the tail. Both halves are monotonic in `|x|`, so nothing about *further from normal is more anomalous* is lost.

The transform belongs to Stage 2 rather than to the preprocessing bundle. Changing the scaler would change the schema hash and force Stage 1 to be retrained for the benefit of a model that is not scale-sensitive at all — a tree ensemble does not care what a column's units are.

## The bound

Each row is 3 fits of the shipped architecture on the same 179,838 benign rows for 12 epochs, scored on the Thursday validation day. Seeds 7, 13, 29.

| Clip (log units) | Benign val loss | Validation ROC-AUC | Validation PR-AUC |
| --- | --- | --- | --- |
| none | 0.02547 | 0.7848 ± 0.0830 | 0.0163 ± 0.0053 |
| ±4 | 0.01509 | 0.7061 ± 0.0678 | 0.0112 ± 0.0020 |
| **±6** | 0.01932 | **0.8996** ± 0.0167 | 0.0325 ± 0.0071 |
| ±8 | 0.02187 | 0.8748 ± 0.0464 | 0.0312 ± 0.0139 |
| ±12 | 0.02577 | 0.8600 ± 0.0246 | 0.0216 ± 0.0039 |

**`INPUT_CLIP = 6.0`.** Both metrics put the candidates in the same order, so the choice does not rest on which one is quoted.

Several seeds rather than one, because the standard deviations in that table are comparable to the gaps between the means: one run cannot separate these candidates. What makes ±6 a result rather than a draw is the strong form: its *worst* of the 3 runs (0.8805) still beats every other candidate's *mean*.

| Clip | seed 7 | seed 13 | seed 29 |
| --- | --- | --- | --- |
| none | 0.8720 | 0.6731 | 0.8093 |
| ±4 | 0.6635 | 0.6528 | 0.8018 |
| ±6 | 0.8805 | 0.8970 | 0.9212 |
| ±8 | 0.8132 | 0.8858 | 0.9253 |
| ±12 | 0.8725 | 0.8817 | 0.8256 |

Validation ROC-AUC per seed. PR-AUC on this day runs in the hundredths for every candidate, because Thursday is 99.5% benign and its two attack families — web attacks and infiltration — are the hardest in the capture. That is why the selection is read off ROC-AUC here: every candidate is scored on identical rows, so prevalence-invariance isolates the ranking quality from the class balance. PR-AUC remains the headline where it belongs, on the test day, in `reports/phase3_anomaly.md`.
