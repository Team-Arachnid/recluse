# Phase 3 — Stage 2, the anomaly detector

Model `stage2-autoencoder-202610070106`: a PyTorch autoencoder, `input(92) -> 64 -> 32 -> 16 -> 32 -> 64 -> output(92)`, 17,612 parameters, fitted on 1,191,239 benign flows and nothing else.

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
| Epochs | 60 run, best at 60 (loss 0.008996) |

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
tau_anom         1.031578e-01
percentile       99.5th of benign reconstruction error
calibrated on    the Thursday validation day's benign rows (396,328 benign rows)
achieved FPR     5.00e-03  (1,982 of 396,328 benign rows)
analyst budget   3.20e-04 FPR, which this benign split reaches at the 99.968th percentile
budget tau       4.015586e-01  (reported, not shipped)
```

`tau_anom` is a statement about normal traffic, not a value tuned until the attacks landed above it. It is read off the benign rows of the Thursday validation day — benign traffic from a day the network never trained on, and the same day `tau_sup` was cut from.

The budget row is the uncomfortable one and it is reported on purpose. Phase 2 derived `tau_sup` from an analyst queue: 320 alerts a day at 1,000,000 flows, a false-positive rate of 3.20e-04. A 99.5th percentile is a false-positive rate of 5 × 10⁻³, which is 16x looser — around 5,000 false alerts a day against a budget of 320. The brief specifies the percentile, so the percentile is what ships and what Phase 4 fuses on; the budget-equivalent threshold is recorded beside it so the gap is a measured quantity rather than a surprise, and the Live Traffic screen's threshold slider is where whoever owns the queue moves between them.

## The checkpoint: benign against attack

Reconstruction error on the held-out **Friday** test day, 375,238 benign flows against 220,656 attack flows, binned on a log axis because the error runs over orders of magnitude. Each column is scaled to its own tallest bin — the two differ tenfold in row count — and the percentage beside each bar is the share of that column.

```
  reconstruction error    benign (375,238)                   attack (220,656)
  9.60e-05 - 1.13e-04                                  0.00%                              0.00%
  1.13e-04 - 1.33e-04     .                            0.02%                              0.00%
  1.33e-04 - 1.56e-04     .                            0.03%                              0.00%
  1.56e-04 - 1.84e-04     #                            0.11%                              0.00%
  1.84e-04 - 2.16e-04     ##                           0.28%                              0.00%
  2.16e-04 - 2.54e-04     ##                           0.40%                              0.00%
  2.54e-04 - 2.98e-04     ####                         0.70%                              0.00%
  2.98e-04 - 3.51e-04     #######                      1.19%                              0.00%
  3.51e-04 - 4.12e-04     ###########                  1.70%                              0.00%
  4.12e-04 - 4.85e-04     #############                2.14%                              0.00%
  4.85e-04 - 5.70e-04     ################             2.51%                              0.00%
  5.70e-04 - 6.71e-04     ##################           2.84%                              0.00%
  6.71e-04 - 7.89e-04     ####################         3.12%                              0.00%
  7.89e-04 - 9.27e-04     #####################        3.29%                              0.00%
  9.27e-04 - 1.09e-03     #####################        3.35%                              0.00%
  1.09e-03 - 1.28e-03     ###################          2.98%                              0.00%
  1.28e-03 - 1.51e-03     ###################          3.08% .                            0.07%
  1.51e-03 - 1.77e-03     #######################      3.61% .                            0.09%
  1.77e-03 - 2.08e-03     #################            2.72% .                            0.01%
  2.08e-03 - 2.45e-03     #################            2.76% .                            0.00%
  2.45e-03 - 2.88e-03     ##################           2.81% .                            0.00%
  2.88e-03 - 3.39e-03     #################            2.65% .                            0.00%
  3.39e-03 - 3.98e-03     ######################       3.45% .                            0.00%
  3.98e-03 - 4.68e-03     ####################         3.12% .                            0.00%
  4.68e-03 - 5.51e-03     ####################         3.12% .                            0.03%
  5.51e-03 - 6.48e-03     #####################        3.41% .                            0.03%
  6.48e-03 - 7.62e-03     #####################        3.29% .                            0.02%
  7.62e-03 - 8.95e-03     ##########################   4.15% .                            0.00%
  8.95e-03 - 1.05e-02     ##########################   4.12% .                            0.01%
  1.05e-02 - 1.24e-02     #########################    4.02% .                            0.03%
  1.24e-02 - 1.46e-02     #########################    3.94% #                            0.45%
  1.46e-02 - 1.71e-02     ######################       3.50% #                            0.77%
  1.71e-02 - 2.01e-02     ##################           2.87% ##                           1.25%
  2.01e-02 - 2.37e-02     ###############              2.47% #############                7.09%
  2.37e-02 - 2.78e-02     ############                 1.96% #########################   13.43%
  2.78e-02 - 3.27e-02     ##########                   1.55% #######                      3.61%
  3.27e-02 - 3.85e-02     ########                     1.34% #####                        2.82%
  3.85e-02 - 4.52e-02     #######                      1.06% ##                           1.22%
  4.52e-02 - 5.32e-02     ######                       0.91% ################             8.58%
  5.32e-02 - 6.26e-02     #####                        0.78% ##########################  14.15%
  6.26e-02 - 7.36e-02     ###########                  1.68% ##########                   5.33%
  7.36e-02 - 8.65e-02     ######                       0.96% #########                    5.17%
  8.65e-02 - 1.02e-01     ####                         0.58% #######                      4.06%
  ---------------------- tau_anom = 1.032e-01 ----------------------
  1.02e-01 - 1.20e-01     ###                          0.40% #############                6.85%
  1.20e-01 - 1.41e-01     ##                           0.32% #########                    5.16%
  1.41e-01 - 1.65e-01     ######                       1.00% #########                    5.12%
  1.65e-01 - 1.94e-01     ######                       1.03% #######                      3.94%
  1.94e-01 - 2.29e-01     ###                          0.43% ######                       3.51%
  2.29e-01 - 2.69e-01     #                            0.10% ##                           1.28%
  2.69e-01 - 3.16e-01     ##                           0.32% #                            0.39%
  3.16e-01 - 3.72e-01     ####                         0.65% .                            0.04%
  3.72e-01 - 4.37e-01     ###                          0.56% .                            0.01%
  4.37e-01 - 5.14e-01     ####                         0.58% .                            0.01%
  5.14e-01 - 6.04e-01     .                            0.04% .                            0.04%
  6.04e-01 - 7.10e-01     .                            0.00% .                            0.19%
  7.10e-01 - 8.35e-01     .                            0.01% #                            0.39%
  8.35e-01 - 9.82e-01                                  0.00% #                            0.77%
  9.82e-01 - 1.15e+00                                  0.00% #####                        2.53%
  1.15e+00 - 1.36e+00                                  0.00% ##                           1.36%
  1.36e+00 - 1.60e+00                                  0.00% .                            0.15%
```

**The distributions separate partially.** The median attack flow reconstructs 11.7x worse than the median benign one. ROC-AUC is 0.9093, so the ranking carries real signal, but at `tau_anom` only 31.3% of the test day's attack traffic clears the line. The per-family table below is where that average comes apart, and it is the honest reading of this checkpoint rather than a pass.

| Measured on the test day | Value |
| --- | --- |
| **PR-AUC (headline)** | **0.7695** |
| ROC-AUC | 0.9093 |
| Attack recall at `tau_anom` | **31.3%** |
| False-positive rate | 5.39e-02 |
| Projected false alerts/day at V = 1,000,000 | 53,915 |
| Alerts per analyst per hour | 6,739.4 |
| Median benign reconstruction error | 5.000e-03 |
| Median attack reconstruction error | 5.867e-02 |

`tau_anom` was cut to alert on 0.50% of Thursday's benign flows. On Friday's benign traffic the same threshold fires on 5.39% of them — 10.8x more often, which is 53,915 false alerts a day against a budget of 320. Nothing about the model changed between those two numbers; the benign traffic did. This is the dataset-internal version of the domain shift Phase 9 has to handle on live capture, measured across two days of one lab network rather than across five years and a different one — and it is the argument for the shadow-mode burn-in and the locally recomputed threshold that phase prescribes, made as evidence rather than as a worry.

## Per family

Every attack family on the Friday test day is one Stage 1 has no name for, and one the autoencoder has never seen an example of. This table is what Stage 2 does with them on its own, before any fusion. Stage 1's column is lifted from the champion's own test-day figures on the model card — the same day, the two models that shipped. The two are read at thresholds cut by different rules, so the recall figures are *not* a like-for-like comparison of the models; what the pairing shows is which families each stage misses.

| Family on the test day | Rows | Flagged by Stage 2 | Stage 2 recall |
| --- | --- | --- | --- |
| Benign — these are false positives | 375,238 | 20,231 | **5.39%** |
| `ddos` | 128,014 | 68,711 | **53.7%** |
| `port_scan` | 90,694 | 261 | **0.3%** |
| `botnet` | 1,948 | 42 | **2.2%** |

The average is carried by one family. `ddos` accounts for 53.7% of its own rows, which is most of the 31.3% figure above.

The families Stage 2 does **not** close are `port_scan` (0.3%) and `botnet` (2.2%). That is the number to carry into Phase 4 rather than the average.

The mechanism is worth naming, because the explanation table below makes it look like a contradiction. The score is a *mean* over every feature, so a flow can have a highly distinctive error signature and still score low: short, sparse flows reconstruct easily on most columns, and a large error on five of them is divided by ninety-two. Stage 2 can be responding to the right features and still rank the row below the threshold, which is a limitation of the aggregate rather than of the representation.

## Do the baselines beat it

Every detector is scored on the same 42,179-row arena — 2,179 attack flows and 40,000 benign ones, drawn from the Thursday validation day. The validation day rather than the test day, because choosing between detectors is a choice, and choices are not made on the test day. Identical rows, so the columns compare; a subsample of the benign traffic, so the absolute PR-AUC is not the same quantity as the full-split figure above.

| Detector | Library | Fitted on | PR-AUC | ROC-AUC |
| --- | --- | --- | --- | --- |
| **Autoencoder** | torch | 1,191,239 benign rows | **0.3083** | 0.9335 |
| IsolationForest | scikit-learn | 40,000 benign rows | 0.0954 | 0.7342 |
| LOF | scikit-learn | 40,000 benign rows | 0.3545 | 0.9323 |
| ECOD | pyod | 40,000 benign rows | 0.0803 | 0.7136 |

The classical detectors are fitted on a 40,000-row benign reference set rather than on all 1,191,239. LOF is a k-nearest-neighbour method: scoring against a million reference rows does not finish, and an autoencoder that needed a handicapped LOF to look good would not be worth shipping. Every one of them sees benign rows only, the same discipline the autoencoder is held to.

**`LOF` beats the autoencoder on this arena** (0.3545 against 0.3083 PR-AUC). That is reported rather than hidden, and it is not a defeat for the two-stage design: the design needs a Stage 2 that catches families nobody named, not a Stage 2 that is a neural network. The finding is that a parameter-free detector does that job at least as well on this data, and it belongs in the write-up either way.

`Autoencoder`: scored on the arena from the same weights measured on the full splits.

`LOF`: novelty=True, so the benign rows are the reference set rather than the scored set.

## What it failed to reconstruct

The Stage 2 explanation is free: `(x - x_hat) ** 2` is already computed as part of the score, and the features the network failed hardest to rebuild are precisely why the row looks unlike normal traffic. No SHAP, no perturbation sampling, and more faithful to the model than either. Averaged over a sample of each family on the test day:

| Family | The five features it fails hardest to reconstruct |
| --- | --- |
| benign (for contrast) | `active_std` (13%), `down_up_ratio` (7%), `active_max` (4%), `fwd_packet_length_min` (4%), `active_mean` (4%) |
| `ddos` | `active_std` (16%), `packet_length_variance` (10%), `bwd_iat_total` (6%), `active_max` (6%), `active_mean` (5%) |
| `port_scan` | `init_win_bytes_forward` (29%), `psh_flag_count` (13%), `ack_flag_count` (11%), `init_win_bytes_backward` (9%), `urg_flag_count` (7%) |
| `botnet` | `flow_iat_min` (15%), `port_group_registered` (14%), `port_group_well_known` (10%), `init_win_bytes_backward` (7%), `fwd_iat_min` (4%) |

## Appendix: the training curve

| Epoch | Train loss | Benign validation loss |
| --- | --- | --- |
| 1 | 0.082135 | 0.023424 |
| 2 | 0.032026 | 0.018811 |
| 3 | 0.028158 | 0.016093 |
| 4 | 0.026215 | 0.015757 |
| 5 | 0.024853 | 0.013732 |
| 6 | 0.023928 | 0.013465 |
| 7 | 0.023176 | 0.013157 |
| 8 | 0.022520 | 0.012898 |
| 9 | 0.022199 | 0.012488 |
| 10 | 0.021650 | 0.011855 |
| 11 | 0.021350 | 0.012162 |
| 12 | 0.020957 | 0.011693 |
| 13 | 0.020695 | 0.011798 |
| 14 | 0.020425 | 0.011649 |
| 15 | 0.020190 | 0.011212 |
| 16 | 0.020076 | 0.010875 |
| 17 | 0.019857 | 0.011020 |
| 18 | 0.019812 | 0.011037 |
| 19 | 0.019485 | 0.010710 |
| 20 | 0.019365 | 0.010901 |
| 21 | 0.019226 | 0.010812 |
| 22 | 0.019062 | 0.010943 |
| 23 | 0.018933 | 0.010341 |
| 24 | 0.018796 | 0.010174 |
| 25 | 0.018732 | 0.010441 |
| 26 | 0.018561 | 0.010075 |
| 27 | 0.018498 | 0.010413 |
| 28 | 0.018376 | 0.010160 |
| 29 | 0.018233 | 0.010219 |
| 30 | 0.018145 | 0.010234 |
| 31 | 0.018006 | 0.010082 |
| 32 | 0.017909 | 0.009878 |
| 33 | 0.017812 | 0.010155 |
| 34 | 0.017730 | 0.010151 |
| 35 | 0.017614 | 0.009749 |
| 36 | 0.017581 | 0.010214 |
| 37 | 0.017447 | 0.010263 |
| 38 | 0.017360 | 0.009740 |
| 39 | 0.017329 | 0.010088 |
| 40 | 0.017180 | 0.009730 |
| 41 | 0.017156 | 0.009613 |
| 42 | 0.017016 | 0.009557 |
| 43 | 0.017024 | 0.009574 |
| 44 | 0.016991 | 0.009491 |
| 45 | 0.016882 | 0.009679 |
| 46 | 0.016822 | 0.009838 |
| 47 | 0.016793 | 0.009414 |
| 48 | 0.016765 | 0.009259 |
| 49 | 0.016721 | 0.009163 |
| 50 | 0.016675 | 0.009791 |
| 51 | 0.016573 | 0.009354 |
| 52 | 0.016571 | 0.009249 |
| 53 | 0.016529 | 0.009318 |
| 54 | 0.016448 | 0.009035 |
| 55 | 0.016391 | 0.009396 |
| 56 | 0.016376 | 0.009512 |
| 57 | 0.016318 | 0.009394 |
| 58 | 0.016336 | 0.009195 |
| 59 | 0.016257 | 0.009093 |
| 60 | 0.016218 | 0.008996 |

Early stopping watches the second column and the weights of the best epoch are restored before the artifact is written. Stopping where patience ran out would ship a model several epochs past its own best loss.

## What this hands to Phase 4

`autoencoder.pt` as a bare state dict, `tau_anom` and the benign error histogram on the model card, and a Stage 2 that shares Stage 1's feature contract exactly. Fusion needs nothing else: one matrix, Stage 1 first, Stage 2 on whatever Stage 1 could not confidently name, and `UNCLASSIFIED_ANOMALY` for what clears `tau_anom` without a family.

The per-family column above is the Stage 2 column of the leave-one-attack-out table, measured here without the hold-out loop. Phase 4 runs the loop, which removes each family from Stage 1's training set in turn and asks the same question of the pair rather than of Stage 2 alone.
