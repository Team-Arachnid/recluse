# Phase 3 — Stage 2, the anomaly detector

Model `stage2-autoencoder-202609291144`: a PyTorch autoencoder, `input(92) -> 64 -> 32 -> 16 -> 32 -> 64 -> output(92)`, 17,612 parameters, fitted on 1,191,239 benign flows and nothing else.

Stage 1 answers *which named attack is this*. Stage 2 answers *how unlike normal traffic is this*, and it answers it having never been shown an attack of any kind. That is the whole reason it can say something about a family nobody labelled.

## The training set carries no attacks

1,331,862 benign rows from `data/processed/benign_train.parquet` — Monday in full plus the benign rows of Tuesday and Wednesday. 8,264 exact duplicates were dropped, leaving 1,191,239 rows to fit on and 132,359 held back to early-stop on.

The attack-free property is asserted in code, twice: Phase 1 raises `AttackInBenignTrainingSet` when it assembles the split, and this phase re-checks the label column before the optimiser is constructed. Both are fatal rather than warnings. An autoencoder that has seen an attack learns to reconstruct it and stops flagging it, and nothing anywhere raises — the claim just quietly stops being true.

Duplicates are dropped for a reason worth naming. Phase 1 removes them within each capture file and across the supervised splits, but the benign-only set is assembled from three days after that pass, so a benign flow appearing identically on Monday and Tuesday survives twice. Left in, it lands on both sides of the early-stopping split and makes the validation loss optimistic, which stops training later than it should.

## Architecture

```
input(92) -> 64 -> 32 -> 16 -> 32 -> 64 -> output(92)
```

| Element | Choice |
| --- | --- |
| Input | `sign(x) * log1p(\|x\|), clipped to +/-6`, applied to the shared feature matrix |
| Activation | ReLU on every hidden layer; the output layer is a bare `Linear` |
| Loss | MSE — the loss and the score are the same quantity |
| Optimiser | Adam, lr 0.001 |
| Regularisation | Dropout 0.1 in the encoder's hidden layers |
| Normalisation | Batch norm after every hidden linear layer |
| Bottleneck | 16 units |
| Batch | 1,024 rows |
| Stopping | Early stopping on benign validation loss, patience 6 |
| Epochs | 60 run, best at 54 (loss 0.008977) |

Two of those are decisions rather than defaults. The output layer has no activation because the features arrive signed: squashing the output through ReLU would make every negative target unreachable and put a floor under the reconstruction error of every row, benign ones included. And dropout is absent from the bottleneck itself — zeroing a tenth of sixteen code units is a much heavier perturbation than a tenth of sixty-four, and the bottleneck is already the regulariser this network is built around.

### The input transform, and the pathology that forced it

The first run of this phase produced a detector that ranked attack traffic *below* benign traffic: ROC-AUC 0.2337 on the shared validation-day arena, where the three classical baselines scored 0.71 to 0.86 on the same rows, and 0.4676 on the test day. That is worth writing down, because the cause is a property of the shared preprocessing bundle rather than of the network.

`RobustScaler` divides each column by its interquartile range, and when a column's IQR is zero scikit-learn leaves the divisor at 1.0 — the column passes through essentially unscaled. CICIDS2017 has such columns. Over three quarters of benign flows report `idle_std` of exactly zero, so its IQR is zero, while the flows that do idle report values up to 7.6 × 10⁷ microseconds. Squared, that one column accounted for **93.9%** of the total magnitude the MSE loss could see, with `active_std` taking another 4.0% and the top three together 98.7%.

An MSE objective under those conditions is not a reconstruction objective. The gradient belongs to one column, eighty-nine features are invisible to it, and the score that comes out is a proxy for *does this flow have a large idle gap* — which benign traffic has more of than attack traffic does. Hence the inversion. The loss was also still falling monotonically at epoch 60, in the tens of billions, having never triggered early stopping.

Stage 2 therefore reads the shared matrix through `sign(x) * log1p(|x|), clipped to +/-6`. `log1p` compresses the magnitudes without discarding the ordering — a flow ten thousand IQRs out still scores above one a hundred IQRs out — and the clip bounds what is left of the tail. Both halves are monotonic in `|x|`, so nothing about *further from normal is more anomalous* is lost. The bound is chosen on the validation day over three seeds; the comparison is in [`reports/input_ablation.md`](input_ablation.md), reproducible with `make ablation-input`.

The transform belongs to Stage 2, not to the bundle. Changing the scaler would change the schema hash and force Stage 1 to be retrained for the benefit of a model that is not scale-sensitive at all — a tree ensemble does not care what a column's units are. It is applied in exactly two places, once on the way into a forward pass and once at the top of the fit, because it is not idempotent; `BenignData` carries shared-space rows so that nothing else can apply it twice, and a test asserts the two paths agree.

Every baseline below sees the same transformed input. Handing them the raw scaled matrix would flatter the autoencoder for free: LOF is a Euclidean method and IsolationForest partitions axis by axis, so both are pulled apart by the same column that broke the network.

Stage 2 is fitted against the champion's own preprocessing bundle — 92 features under schema `sha256:7672483867812ed560e289d8af7febbe3c29cf86356789e98e7a2086b6797160`. At serving time one feature matrix is built per batch and both stages read it, so a Stage 2 fitted against its own scaling would produce confident nonsense in production without raising anything. The scaler is a label-free centring and scaling statistic; what makes Stage 2's claim true is that no *label* and no attack row reached the fit.

## The threshold

```
tau_anom         1.098113e-01
percentile       99.5th of benign reconstruction error
calibrated on    the Thursday validation day's benign rows (396,328 benign rows)
achieved FPR     5.00e-03  (1,982 of 396,328 benign rows)
analyst budget   3.20e-04 FPR, which this benign split reaches at the 99.968th percentile
budget tau       4.244284e-01  (reported, not shipped)
```

`tau_anom` is a statement about normal traffic, not a value tuned until the attacks landed above it. It is read off the benign rows of the Thursday validation day — benign traffic from a day the network never trained on, and the same day `tau_sup` was cut from.

The budget row is the uncomfortable one and it is reported on purpose. Phase 2 derived `tau_sup` from an analyst queue: 320 alerts a day at 1,000,000 flows, a false-positive rate of 3.20e-04. A 99.5th percentile is a false-positive rate of 5 × 10⁻³, which is 16x looser — around 5,000 false alerts a day against a budget of 320. The brief specifies the percentile, so the percentile is what ships and what Phase 4 fuses on; the budget-equivalent threshold is recorded beside it so the gap is a measured quantity rather than a surprise, and the Live Traffic screen's threshold slider is where whoever owns the queue moves between them.

## The checkpoint: benign against attack

Reconstruction error on the held-out **Friday** test day, 375,238 benign flows against 220,656 attack flows, binned on a log axis because the error runs over orders of magnitude. Each column is scaled to its own tallest bin — the two differ tenfold in row count — and the percentage beside each bar is the share of that column.

```
  reconstruction error    benign (375,238)                   attack (220,656)
  1.09e-04 - 1.29e-04     .                            0.01%                              0.00%
  1.29e-04 - 1.51e-04     .                            0.05%                              0.00%
  1.51e-04 - 1.78e-04     #                            0.11%                              0.00%
  1.78e-04 - 2.09e-04     #                            0.19%                              0.00%
  2.09e-04 - 2.46e-04     #                            0.26%                              0.00%
  2.46e-04 - 2.89e-04     ###                          0.51%                              0.00%
  2.89e-04 - 3.40e-04     #####                        0.92%                              0.00%
  3.40e-04 - 4.00e-04     ########                     1.36%                              0.00%
  4.00e-04 - 4.70e-04     ###########                  2.00%                              0.00%
  4.70e-04 - 5.53e-04     #############                2.36%                              0.00%
  5.53e-04 - 6.50e-04     ################             2.76%                              0.00%
  6.50e-04 - 7.64e-04     #################            3.02%                              0.00%
  7.64e-04 - 8.99e-04     #################            3.04%                              0.00%
  8.99e-04 - 1.06e-03     #################            2.95%                              0.00%
  1.06e-03 - 1.24e-03     ###############              2.71%                              0.00%
  1.24e-03 - 1.46e-03     ##############               2.52%                              0.00%
  1.46e-03 - 1.72e-03     ###############              2.68%                              0.00%
  1.72e-03 - 2.02e-03     ###############              2.66% .                            0.08%
  2.02e-03 - 2.38e-03     #####################        3.59% .                            0.07%
  2.38e-03 - 2.79e-03     ###################          3.25% .                            0.01%
  2.79e-03 - 3.29e-03     #################            3.06% .                            0.01%
  3.29e-03 - 3.86e-03     ######################       3.77% .                            0.01%
  3.86e-03 - 4.54e-03     #####################        3.71% .                            0.00%
  4.54e-03 - 5.34e-03     ###################          3.31% .                            0.05%
  5.34e-03 - 6.28e-03     #######################      4.07% .                            0.03%
  6.28e-03 - 7.39e-03     #####################        3.63% .                            0.00%
  7.39e-03 - 8.69e-03     #########################    4.33% .                            0.00%
  8.69e-03 - 1.02e-02     #########################    4.31% .                            0.01%
  1.02e-02 - 1.20e-02     ##########################   4.55% .                            0.03%
  1.20e-02 - 1.41e-02     ######################       3.92% #                            1.05%
  1.41e-02 - 1.66e-02     ##################           3.12% ##                           1.71%
  1.66e-02 - 1.95e-02     ###############              2.70% ###                          2.18%
  1.95e-02 - 2.30e-02     #############                2.32% ###############             10.68%
  2.30e-02 - 2.70e-02     ############                 2.03% #################           12.21%
  2.70e-02 - 3.18e-02     #########                    1.55% ##                           1.37%
  3.18e-02 - 3.74e-02     ######                       1.13% #                            0.91%
  3.74e-02 - 4.39e-02     #####                        0.94% #                            0.69%
  4.39e-02 - 5.17e-02     #####                        0.93% ##########################  18.55%
  5.17e-02 - 6.07e-02     #####                        0.90% #######                      5.03%
  6.07e-02 - 7.14e-02     #####                        0.81% ###                          2.01%
  7.14e-02 - 8.40e-02     ####                         0.72% #####                        3.79%
  8.40e-02 - 9.88e-02     ###                          0.51% #######                      4.94%
  ---------------------- tau_anom = 1.098e-01 ----------------------
  9.88e-02 - 1.16e-01     ######                       1.01% #######                      5.31%
  1.16e-01 - 1.37e-01     #######                      1.31% ########                     5.86%
  1.37e-01 - 1.61e-01     ##########                   1.77% #######                      4.84%
  1.61e-01 - 1.89e-01     ##                           0.37% ######                       4.48%
  1.89e-01 - 2.22e-01     .                            0.05% ####                         3.18%
  2.22e-01 - 2.61e-01     .                            0.04% ####                         2.85%
  2.61e-01 - 3.07e-01     ###                          0.60% #                            1.03%
  3.07e-01 - 3.61e-01     ####                         0.62% #                            0.52%
  3.61e-01 - 4.25e-01     ##                           0.40% #                            0.51%
  4.25e-01 - 5.00e-01     ###                          0.52% #                            0.37%
  5.00e-01 - 5.87e-01     .                            0.03% .                            0.14%
  5.87e-01 - 6.91e-01     .                            0.01% .                            0.03%
  6.91e-01 - 8.12e-01     .                            0.00% .                            0.06%
  8.12e-01 - 9.55e-01                                  0.00% .                            0.33%
  9.55e-01 - 1.12e+00                                  0.00% #                            0.88%
  1.12e+00 - 1.32e+00                                  0.00% ###                          2.23%
  1.32e+00 - 1.55e+00                                  0.00% ##                           1.64%
  1.55e+00 - 1.83e+00                                  0.00% .                            0.30%
```

**The distributions separate partially.** The median attack flow reconstructs 10.1x worse than the median benign one. ROC-AUC is 0.9045, so the ranking carries real signal, but at `tau_anom` only 31.0% of the test day's attack traffic clears the line. The per-family table below is where that average comes apart, and it is the honest reading of this checkpoint rather than a pass.

| Measured on the test day | Value |
| --- | --- |
| **PR-AUC (headline)** | **0.7728** |
| ROC-AUC | 0.9045 |
| Attack recall at `tau_anom` | **31.0%** |
| False-positive rate | 5.96e-02 |
| Projected false alerts/day at V = 1,000,000 | 59,594 |
| Alerts per analyst per hour | 7,449.3 |
| Median benign reconstruction error | 5.180e-03 |
| Median attack reconstruction error | 5.223e-02 |

`tau_anom` was cut to alert on 0.50% of Thursday's benign flows. On Friday's benign traffic the same threshold fires on 5.96% of them — 11.9x more often, which is 59,594 false alerts a day against a budget of 320. Nothing about the model changed between those two numbers; the benign traffic did. This is the dataset-internal version of the domain shift Phase 9 has to handle on live capture, measured across two days of one lab network rather than across five years and a different one — and it is the argument for the shadow-mode burn-in and the locally recomputed threshold that phase prescribes, made as evidence rather than as a worry.

## Per family

Every attack family on the Friday test day is one Stage 1 has no name for, and one the autoencoder has never seen an example of. This table is what Stage 2 does with them on its own, before any fusion. Stage 1's column is lifted from the champion's own test-day figures on the model card — the same day, the two models that shipped. The two are read at thresholds cut by different rules, so the recall figures are *not* a like-for-like comparison of the models; what the pairing shows is which families each stage misses.

| Family on the test day | Rows | Flagged by Stage 2 | Stage 2 recall | Stage 1 recall |
| --- | --- | --- | --- | --- |
| Benign — these are false positives | 375,238 | 22,362 | **5.96%** | 0.02% |
| `ddos` | 128,014 | 68,222 | **53.3%** | 38.0% |
| `port_scan` | 90,694 | 194 | **0.2%** | 0.6% |
| `botnet` | 1,948 | 42 | **2.2%** | 0.0% |

The average is carried by one family. `ddos` accounts for 53.3% of its own rows, which is most of the 31.0% figure above.

The families Stage 2 does **not** close are `port_scan` (0.2%) and `botnet` (2.2%). That is the number to carry into Phase 4 rather than the average.

**Stage 1 misses them too**: `botnet` (Stage 1 0.0%, Stage 2 2.2%) and `port_scan` (Stage 1 0.6%, Stage 2 0.2%), from the champion's own test-day figures on the model card. A family neither stage surfaces is a gap in the system rather than a gap in one model, and fusing two detectors that look past the same traffic does not produce a third that does not. Fusion helps where the two miss *different* rows, so this is the row of the leave-one-attack-out table to read first.

The mechanism is worth naming, because the explanation table below makes it look like a contradiction. The score is a *mean* over every feature, so a flow can have a highly distinctive error signature and still score low: short, sparse flows reconstruct easily on most columns, and a large error on five of them is divided by ninety-two. Stage 2 can be responding to the right features and still rank the row below the threshold, which is a limitation of the aggregate rather than of the representation.

## Do the baselines beat it

Every detector is scored on the same 42,179-row arena — 2,179 attack flows and 40,000 benign ones, drawn from the Thursday validation day. The validation day rather than the test day, because choosing between detectors is a choice, and choices are not made on the test day. Identical rows, so the columns compare; a subsample of the benign traffic, so the absolute PR-AUC is not the same quantity as the full-split figure above.

| Detector | Library | Fitted on | PR-AUC | ROC-AUC |
| --- | --- | --- | --- | --- |
| **Autoencoder** | torch | 1,191,239 benign rows | **0.6232** | 0.9670 |
| IsolationForest | scikit-learn | 40,000 benign rows | 0.0954 | 0.7342 |
| LOF | scikit-learn | 40,000 benign rows | 0.3545 | 0.9323 |
| ECOD | pyod | 40,000 benign rows | 0.0803 | 0.7136 |

The classical detectors are fitted on a 40,000-row benign reference set rather than on all 1,191,239. LOF is a k-nearest-neighbour method: scoring against a million reference rows does not finish, and an autoencoder that needed a handicapped LOF to look good would not be worth shipping. Every one of them sees benign rows only, the same discipline the autoencoder is held to.

The autoencoder wins on this arena — +0.2687 PR-AUC over `LOF`, the best of the classical detectors, so it earns its complexity here.

`Autoencoder`: scored on the arena from the same weights measured on the full splits.

`LOF`: novelty=True, so the benign rows are the reference set rather than the scored set.

## What it failed to reconstruct

The Stage 2 explanation is free: `(x - x_hat) ** 2` is already computed as part of the score, and the features the network failed hardest to rebuild are precisely why the row looks unlike normal traffic. No SHAP, no perturbation sampling, and more faithful to the model than either. Averaged over a sample of each family on the test day:

| Family | The five features it fails hardest to reconstruct |
| --- | --- |
| benign (for contrast) | `active_std` (10%), `down_up_ratio` (9%), `fwd_packet_length_min` (4%), `act_data_pkt_fwd` (4%), `active_min` (3%) |
| `ddos` | `active_std` (12%), `packet_length_variance` (10%), `active_max` (7%), `bwd_iat_total` (6%), `active_mean` (6%) |
| `port_scan` | `init_win_bytes_forward` (31%), `psh_flag_count` (14%), `ack_flag_count` (13%), `urg_flag_count` (10%), `min_seg_size_forward` (7%) |
| `botnet` | `flow_iat_min` (17%), `port_group_registered` (14%), `init_win_bytes_backward` (10%), `port_group_well_known` (9%), `port_is_80` (3%) |

## Appendix: the training curve

| Epoch | Train loss | Benign validation loss |
| --- | --- | --- |
| 1 | 0.082126 | 0.023503 |
| 2 | 0.032089 | 0.018594 |
| 3 | 0.028283 | 0.016055 |
| 4 | 0.026327 | 0.016018 |
| 5 | 0.024931 | 0.013889 |
| 6 | 0.023991 | 0.013448 |
| 7 | 0.023176 | 0.013014 |
| 8 | 0.022583 | 0.012663 |
| 9 | 0.022244 | 0.012628 |
| 10 | 0.021725 | 0.012353 |
| 11 | 0.021439 | 0.012577 |
| 12 | 0.021117 | 0.011649 |
| 13 | 0.020802 | 0.011615 |
| 14 | 0.020530 | 0.011597 |
| 15 | 0.020339 | 0.011325 |
| 16 | 0.020166 | 0.010866 |
| 17 | 0.019915 | 0.010940 |
| 18 | 0.019878 | 0.010785 |
| 19 | 0.019610 | 0.010686 |
| 20 | 0.019467 | 0.010757 |
| 21 | 0.019315 | 0.010551 |
| 22 | 0.019155 | 0.010513 |
| 23 | 0.019015 | 0.010275 |
| 24 | 0.018871 | 0.010168 |
| 25 | 0.018818 | 0.010295 |
| 26 | 0.018648 | 0.009945 |
| 27 | 0.018565 | 0.010389 |
| 28 | 0.018509 | 0.010036 |
| 29 | 0.018386 | 0.010037 |
| 30 | 0.018289 | 0.009966 |
| 31 | 0.018209 | 0.009877 |
| 32 | 0.018108 | 0.009746 |
| 33 | 0.018081 | 0.010232 |
| 34 | 0.018001 | 0.009832 |
| 35 | 0.017905 | 0.009489 |
| 36 | 0.017922 | 0.009766 |
| 37 | 0.017809 | 0.009743 |
| 38 | 0.017777 | 0.009486 |
| 39 | 0.017729 | 0.009679 |
| 40 | 0.017614 | 0.009733 |
| 41 | 0.017608 | 0.009557 |
| 42 | 0.017483 | 0.009372 |
| 43 | 0.017470 | 0.009437 |
| 44 | 0.017461 | 0.009488 |
| 45 | 0.017389 | 0.009249 |
| 46 | 0.017356 | 0.009608 |
| 47 | 0.017287 | 0.009280 |
| 48 | 0.017308 | 0.009443 |
| 49 | 0.017282 | 0.009151 |
| 50 | 0.017187 | 0.009747 |
| 51 | 0.017139 | 0.009362 |
| 52 | 0.017092 | 0.009296 |
| 53 | 0.017068 | 0.009177 |
| 54 | 0.016933 | 0.008977 |
| 55 | 0.016941 | 0.009263 |
| 56 | 0.016976 | 0.009183 |
| 57 | 0.016876 | 0.008988 |
| 58 | 0.016842 | 0.009080 |
| 59 | 0.016868 | 0.009062 |
| 60 | 0.016784 | 0.009208 |

Early stopping watches the second column and the weights of the best epoch are restored before the artifact is written. Stopping where patience ran out would ship a model several epochs past its own best loss.

## What this hands to Phase 4

`autoencoder.pt` as a bare state dict, `tau_anom` and the benign error histogram on the model card, and a Stage 2 that shares Stage 1's feature contract exactly. Fusion needs nothing else: one matrix, Stage 1 first, Stage 2 on whatever Stage 1 could not confidently name, and `UNCLASSIFIED_ANOMALY` for what clears `tau_anom` without a family.

The per-family column above is the Stage 2 column of the leave-one-attack-out table, measured here without the hold-out loop. Phase 4 runs the loop, which removes each family from Stage 1's training set in turn and asks the same question of the pair rather than of Stage 2 alone.
